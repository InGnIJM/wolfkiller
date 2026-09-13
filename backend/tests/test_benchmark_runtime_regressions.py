"""Regression coverage for benchmark lifecycle and frozen game inputs."""

import asyncio
import random
from unittest.mock import MagicMock

import pytest

from app.persistence.repository import GameRepository
from app.services.benchmark_service import BenchmarkService
from app.services.game_service import GameService


def _spec():
    return {
        "name": "regression", "mode": "mixed_arena", "seed": 1, "games": 1,
        "scenario": {"scenario_id": "tiny", "role_counts": {"villager": 1}},
        "models": [{"model_config_id": None}],
    }


@pytest.mark.asyncio
async def test_cancel_draft_marks_all_items_cancelled(tmp_path):
    repository = GameRepository(tmp_path)
    try:
        service = BenchmarkService(repository)
        run = service.create_run(_spec())
        result = await service.cancel(run["run_id"])
        assert result["status"] == "cancelled"
        assert {row["status"] for row in repository.list_benchmark_items(run["run_id"])} == {"cancelled"}
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_immediate_resume_after_pause_has_a_live_worker(tmp_path):
    repository = GameRepository(tmp_path)
    started = asyncio.Event()
    calls = []

    async def execute(item):
        calls.append(item["item_index"])
        started.set()
        await asyncio.Event().wait()

    service = BenchmarkService(repository, item_executor=execute)
    try:
        run_id = service.create_run(_spec())["run_id"]
        await service.start(run_id)
        await started.wait()
        old_worker = service._tasks[run_id]
        await service.pause(run_id)
        await service.resume(run_id)
        assert service._tasks[run_id] is not old_worker
        started.clear()
        await asyncio.wait_for(started.wait(), timeout=1)
        assert calls == [0, 0]
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_cancelling_game_waiter_preserves_runner():
    service = object.__new__(GameService)
    service._games = {"game": object()}
    service.get_execution_info = MagicMock(return_value={"execution_status": "running"})
    runner = asyncio.create_task(asyncio.Event().wait())
    service._tasks = {"game": runner}
    waiter = asyncio.create_task(service.wait_game("game"))
    try:
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not runner.done()
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


def test_report_refreshes_when_only_run_status_changes(tmp_path):
    repository = GameRepository(tmp_path)
    try:
        service = BenchmarkService(repository)
        run_id = service.create_run(_spec())["run_id"]
        before = service.generate_report(run_id)
        repository.transition_benchmark(run_id, expected=("draft",), target="cancelled")
        after = service.generate_report(run_id)
        assert before["provisional"] is True
        assert after["provisional"] is False
        assert after["input_digest"] != before["input_digest"]
    finally:
        repository.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes, message", [
    ({"role_by_seat": {1: "wolf-killer-villager"}}, "every seat"),
    ({"role_by_seat": {1: "unknown", 2: "wolf-killer-villager"}}, "role counts"),
    ({"role_by_seat": {True: "wolf-killer-villager", 2: "wolf-killer-villager"}}, "seat"),
    ({"runtime_seed": True}, "runtime seed"),
    ({"runtime_seed": -1}, "runtime seed"),
])
async def test_invalid_frozen_inputs_fail_before_model_resolution(monkeypatch, changes, message):
    import app.services.game_service as module

    resolve = MagicMock(side_effect=AssertionError("must validate before model resolution"))
    monkeypatch.setattr(module, "resolve_model_assignments", resolve)
    service = object.__new__(GameService)
    service.repository = None
    with pytest.raises(ValueError, match=message):
        await service.create_game(role_counts={"wolf-killer-villager": 2}, **changes)
    resolve.assert_not_called()


@pytest.mark.asyncio
async def test_real_executor_creates_game_with_frozen_plan_and_checkpoint(tmp_path, monkeypatch):
    import app.services.game_service as module
    from app.api.websocket.ws_handler import WSManager
    from app.core.event_bus import EventBus
    from app.core.game_engine import GameEngine
    from app.models.game import GamePhase
    from app.services.benchmark_game_executor import BenchmarkGameExecutor

    class FakeClient:
        def __init__(self, *, config):
            self.config = config

        async def aclose(self):
            pass

    async def finish_without_model_calls(engine):
        engine.state.phase = GamePhase.GAME_OVER
        engine._running = False

    monkeypatch.setattr(module, "LLMClient", FakeClient)
    monkeypatch.setattr(GameEngine, "_game_loop", finish_without_model_calls)
    repository = GameRepository(tmp_path)
    game_service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository)
    benchmark = BenchmarkService(repository, item_executor=BenchmarkGameExecutor(repository, game_service))
    try:
        spec = _spec()
        spec["scenario"]["role_counts"] = {"wolf-killer-villager": 1}
        run_id = benchmark.create_run(spec)["run_id"]
        await benchmark.start(run_id)
        await benchmark.wait(run_id)
        item = repository.list_benchmark_items(run_id)[0]
        assert item["status"] == "completed", item["terminal_reason"]
        assert repository.get_benchmark_run(run_id)["status"] == "completed"
        engine = game_service._engines[item["game_id"]]
        assert {str(seat): role.role_name for seat, role in engine.roles.items()} == item["assignment"]["role_by_seat"]
        assert all(role._rng is engine._rng for role in engine.roles.values())
        assert engine._rng.random() == random.Random(item["assignment"]["runtime_seed"]).random()
        assert repository.load_checkpoint(item["game_id"]) is not None
    finally:
        await benchmark.aclose()
        await game_service.aclose()
        repository.close()


def test_empty_speech_fallback_uses_game_rng():
    from app.models.game import GameConfig, GameState, PlayerState
    from app.roles.villager import Villager

    role = Villager(1, "wolf-killer-villager", MagicMock(), MagicMock())
    role._rng = random.Random(739)
    state = GameState(game_id="game", config=GameConfig(role_counts={"wolf-killer-villager": 3}))
    state.players = {
        seat: PlayerState(seat_number=seat, role="wolf-killer-villager", camp="good")
        for seat in (1, 2, 3)
    }
    before = role._rng.getstate()
    role._generate_fallback_speech(state, "day_speech")
    assert role._rng.getstate() != before


@pytest.mark.asyncio
async def test_concurrent_resume_waits_for_pause_cleanup(tmp_path):
    repository = GameRepository(tmp_path)
    pausing = asyncio.Event()
    release_pause = asyncio.Event()
    started = asyncio.Event()

    async def execute(item):
        if not item.get("game_id"):
            repository.create_game(
                game_id="game", name="game", config={}, execution_status="running",
                source="benchmark", benchmark_run_id=item["run_id"], model_snapshot=[],
            )
            repository.attach_benchmark_game(item["run_id"], item["item_index"], "game")
        started.set()
        await asyncio.Event().wait()

    async def pause_game(_game_id):
        pausing.set()
        await release_pause.wait()

    service = BenchmarkService(repository, item_executor=execute, game_pauser=pause_game)
    try:
        run_id = service.create_run(_spec())["run_id"]
        await service.start(run_id)
        await started.wait()
        old_worker = service._tasks[run_id]
        pause = asyncio.create_task(service.pause(run_id))
        await pausing.wait()
        resume = asyncio.create_task(service.resume(run_id))
        await asyncio.sleep(0)
        assert not resume.done()
        release_pause.set()
        await pause
        await resume
        assert service._tasks[run_id] is not old_worker
        assert repository.get_benchmark_run(run_id)["status"] == "running"
    finally:
        release_pause.set()
        await service.aclose()
        repository.close()
