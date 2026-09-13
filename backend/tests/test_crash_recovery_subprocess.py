from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys

from app.core.game_engine import GameEngine
from app.core.state_machine import GameEvent
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.persistence.checkpoint_codec import CheckpointCodec
from app.persistence.repository import GameRepository
from app.roles.registry import builtin_registry
from app.services.audience_projector import AudienceProjector
from app.services.durable_step_coordinator import DurableStepCoordinator
from app.services.model_invocation_service import ModelInvocationService


_CRASH_EXIT = 86
_BACKEND = Path(__file__).resolve().parents[1]
_TRANSACTION_POINTS = (
    "before_step_commit",
    "inside_transaction_before_commit",
    "after_step_commit",
    "before_audience_send",
)
_SCENARIOS = {
    "speech": (
        GamePhase.SPEECH,
        "SPEECH_MADE",
        {"player_seat": 1, "text": "hello", "round_number": 2, "phase": "speech"},
    ),
    "vote": (
        GamePhase.VOTE_CASTING,
        "VOTE_CAST",
        {"voter_seat": 1, "target_seat": 2, "round_number": 2, "status": "accepted"},
    ),
    "night": (
        GamePhase.NIGHT,
        "NIGHT_ACTION",
        {"action_type": "guard", "target_seat": 2, "round_number": 2},
    ),
}


def _state(scenario: str) -> GameState:
    phase, _, _ = _SCENARIOS[scenario]
    digest = builtin_registry.freeze().digest
    return GameState(
        game_id="game", phase=phase, round_number=2,
        config=GameConfig(role_counts={"wolf-killer-villager": 2}),
        players={
            1: PlayerState(1, "wolf-killer-villager", "good"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        },
        registry_digest=digest, pipeline_version="v2", effect_schema_version=1,
    )


def _create_game(repository: GameRepository) -> None:
    repository.create_game(
        game_id="game", name="game", config={}, execution_status="running",
        source="native", model_snapshot=[], execution_generation=1,
    )


def _commit_values(scenario: str) -> dict[str, object]:
    _, event_type, payload = _SCENARIOS[scenario]
    return {
        "state": _state(scenario),
        "orchestration": {"execution_position": f"round:2:{scenario}"},
        "expected_storage_revision": 0,
        "execution_generation": 1,
        "step_key": f"round:2:{scenario}",
        "input_facts": {"scenario": scenario, "request_id": f"request:{scenario}"},
        "result_facts": {"scenario": scenario, "accepted": True},
        "domain_events": [{
            "event_id": f"domain:{scenario}", "event_type": event_type,
            "visibility": ["PUBLIC"], "payload": payload,
            "schema_version": 1, "timestamp": "2026-01-01T00:00:00+00:00",
        }],
    }


def _coordinator(repository: GameRepository, fault_injector=None) -> DurableStepCoordinator:
    registry = builtin_registry.freeze()
    return DurableStepCoordinator(
        repository, CheckpointCodec(registry), AudienceProjector(),
        fault_injector=fault_injector,
    )


def _semantic_view(repository: GameRepository) -> dict[str, object]:
    checkpoint = repository.load_checkpoint("game")
    commits = repository.list_commits("game")
    events = repository.get_audience_events("game", after_seq=0, limit=100)
    return {
        "storage_revision": repository.get_game("game")["storage_revision"],
        "checkpoint": None if checkpoint is None else checkpoint["checkpoint"],
        "commits": [{key: value for key, value in row.items() if key != "created_at"}
                    for row in commits],
        "snapshot": repository.get_audience_snapshot("game"),
        "events": [{key: value for key, value in row.items() if key != "timestamp"}
                   for row in events["events"]],
        "high_watermark": events["high_watermark"],
    }


def _crash_at(target: str):
    def inject(point: str) -> None:
        if point == target:
            os._exit(_CRASH_EXIT)
    return inject


def _child_transaction(data_dir: Path, point: str, scenario: str) -> None:
    repository = GameRepository(
        data_dir,
        fault_injector=_crash_at(point) if point == "inside_transaction_before_commit" else None,
    )
    _create_game(repository)
    coordinator = _coordinator(
        repository,
        _crash_at(point) if point != "inside_transaction_before_commit" else None,
    )
    coordinator.commit(**_commit_values(scenario))
    raise AssertionError(f"fault point was not reached: {point}")


def _child_model(data_dir: Path, point: str, calls_path: Path) -> None:
    repository = GameRepository(data_dir)
    _create_game(repository)

    def provider():
        with calls_path.open("a", encoding="utf-8") as stream:
            stream.write("call\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"text": "persisted"}, {
            "prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3,
        }

    ModelInvocationService(repository, fault_injector=_crash_at(point)).invoke_sync(
        game_id="game", request_id="request", actor_seat=1,
        action_position="speech:2:1", frozen_request={"prompt": "fixed"},
        provider_profile="test", model_id="fake", execution_generation=1,
        provider_call=provider,
    )
    raise AssertionError(f"fault point was not reached: {point}")


def _child_phase(data_dir: Path) -> None:
    repository = GameRepository(data_dir)
    _create_game(repository)
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    state = GameState(
        game_id="game", phase=GamePhase.WAITING,
        config=GameConfig(role_counts={"wolf-killer-villager": 1}),
        players={1: PlayerState(1, "wolf-killer-villager", "good")},
        registry_digest=registry.digest,
        pipeline_version="v2", effect_schema_version=1,
    )
    engine = GameEngine(
        "game", config=state.config, data_dir=str(data_dir / "logs"),
        fault_injector=_crash_at("after_phase_transition"),
    )
    engine.state = state
    _coordinator(repository).commit(
        state=state, orchestration=engine.export_orchestration(codec),
        expected_storage_revision=0,
        execution_generation=1, step_key="initial", input_facts={"initial": True},
        result_facts={"phase": "waiting"}, domain_events=[],
    )
    engine.sm.transition(GameEvent.START)
    asyncio.run(engine._broadcast_phase_change())
    raise AssertionError("phase fault point was not reached")


def _start_child(*arguments: object) -> subprocess.Popen[str]:
    environment = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONUTF8")
        if name in os.environ
    }
    environment["PYTHONPATH"] = str(_BACKEND)
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--child",
         *(str(value) for value in arguments)],
        cwd=_BACKEND, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _finish_child(process: subprocess.Popen[str]) -> subprocess.CompletedProcess[str]:
    try:
        stdout, stderr = process.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def _run_child(*arguments: object) -> subprocess.CompletedProcess[str]:
    return _finish_child(_start_child(*arguments))


def test_subprocess_crash_recovers_to_uninterrupted_commit_without_duplicates(
    tmp_path,
) -> None:
    expected_by_scenario: dict[str, dict[str, object]] = {}
    for scenario in _SCENARIOS:
        baseline = GameRepository(tmp_path / f"baseline-{scenario}")
        try:
            _create_game(baseline)
            _coordinator(baseline).commit(**_commit_values(scenario))
            expected_by_scenario[scenario] = _semantic_view(baseline)
        finally:
            baseline.close()

    cases = [
        (point, scenario, tmp_path / f"crash-{point}-{scenario}")
        for point in _TRANSACTION_POINTS for scenario in _SCENARIOS
    ]
    processes = [
        (point, scenario, crash_dir,
         _start_child("transaction", crash_dir, point, scenario))
        for point, scenario, crash_dir in cases
    ]
    for point, scenario, crash_dir, child in processes:
        process = _finish_child(child)
        assert process.returncode == _CRASH_EXIT, (
            f"{point}/{scenario}: {process.stderr}"
        )
        recovered = GameRepository(crash_dir)
        try:
            before = recovered.list_commits("game")
            if point in {"before_step_commit", "inside_transaction_before_commit"}:
                assert before == [], f"{point}/{scenario}"
            else:
                assert len(before) == 1, f"{point}/{scenario}"

            receipt = _coordinator(recovered).commit(**_commit_values(scenario))
            assert receipt.replayed is (
                point in {"after_step_commit", "before_audience_send"}
            )
            assert _semantic_view(recovered) == expected_by_scenario[scenario]
            assert len(recovered.list_commits("game")) == 1
            events = recovered.get_audience_events("game", after_seq=0, limit=100)["events"]
            assert len(events) == 1
        finally:
            recovered.close()


def test_subprocess_model_boundaries_recover_without_recalling_persisted_result(
    tmp_path,
) -> None:
    cases = (
        ("request_prepared", "prepared", 0),
        ("attempt_dispatched", "in_flight", 0),
        ("result_persisted", "resolved", 1),
    )
    processes = []
    for point, status_after_crash, expected_calls_after_crash in cases:
        data_dir = tmp_path / f"data-{point}"
        calls_path = tmp_path / f"calls-{point}.txt"
        processes.append((
            point, status_after_crash, expected_calls_after_crash, data_dir,
            calls_path, _start_child("model", data_dir, point, calls_path),
        ))

    for (point, status_after_crash, expected_calls_after_crash, data_dir,
         calls_path, child) in processes:
        process = _finish_child(child)
        assert process.returncode == _CRASH_EXIT, f"{point}: {process.stderr}"
        repository = GameRepository(data_dir)
        calls = (0 if not calls_path.exists()
                 else len(calls_path.read_text(encoding="utf-8").splitlines()))
        try:
            assert repository.get_model_request("game", "request")["status"] == status_after_crash
            assert calls == expected_calls_after_crash
            recovery = point == "attempt_dispatched"
            generation = 1
            if recovery:
                assert repository.interrupt_running_games() == ["game"]
                game = repository.transition_execution(
                    "game", expected=("interrupted",), target="running",
                    increment_generation=True,
                )
                generation = int(game["execution_generation"])

            def provider():
                with calls_path.open("a", encoding="utf-8") as stream:
                    stream.write("call\n")
                return {"text": "persisted"}, {
                    "prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3,
                }

            result = ModelInvocationService(repository).invoke_sync(
                game_id="game", request_id="request", actor_seat=1,
                action_position="speech:2:1", frozen_request={"prompt": "fixed"},
                provider_profile="test", model_id="fake", execution_generation=generation,
                provider_call=provider, recovery=recovery,
            )
            assert result.normalized_result == {"text": "persisted"}
            assert result.reused is (point == "result_persisted")
            final_calls = len(calls_path.read_text(encoding="utf-8").splitlines())
            assert final_calls == 1
            assert repository.get_model_request("game", "request")["status"] == "resolved"
            attempts = repository.list_model_attempts("game")
            assert len(attempts) == (2 if recovery else 1)
            if recovery:
                assert [row["status"] for row in attempts] == ["unknown", "resolved"]
        finally:
            repository.close()


def test_subprocess_phase_transition_replays_from_last_durable_checkpoint(tmp_path) -> None:
    process = _run_child("phase", tmp_path / "data")
    assert process.returncode == _CRASH_EXIT, process.stderr

    repository = GameRepository(tmp_path / "data")
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    try:
        durable = repository.load_checkpoint("game")
        assert durable is not None
        assert durable["checkpoint"]["state"]["phase"] == "waiting"
        assert len(repository.list_commits("game")) == 1
        state, orchestration = codec.decode(durable["checkpoint"])
        context = {"storage_revision": 1}

        async def checkpoint(step_key: str) -> None:
            receipt = _coordinator(repository).commit(
                state=engine.state, orchestration=orchestration,
                expected_storage_revision=context["storage_revision"],
                execution_generation=1, step_key=step_key,
                input_facts={"event": "start"},
                result_facts={"phase": engine.state.phase.value},
                domain_events=[{
                    "event_id": "phase:role_deal", "event_type": "PHASE_CHANGED",
                    "visibility": ["PUBLIC"],
                    "payload": {"phase": "role_deal", "round_number": 0},
                    "timestamp": "2026-01-01T00:00:00+00:00",
                }],
            )
            context["storage_revision"] = receipt.storage_revision

        engine = GameEngine(
            "game", config=state.config, data_dir=str(tmp_path / "logs"),
            checkpoint_hook=checkpoint,
        )
        engine.load_restored_state(state, orchestration, codec)
        engine.sm.transition(GameEvent.START)
        asyncio.run(engine._broadcast_phase_change())

        assert repository.load_checkpoint("game")["checkpoint"]["state"]["phase"] == "role_deal"
        assert len(repository.list_commits("game")) == 2
        events = repository.get_audience_events("game", after_seq=0, limit=100)["events"]
        assert [event["event_type"] for event in events] == ["phase"]
    finally:
        repository.close()


if __name__ == "__main__":
    if len(sys.argv) < 4 or sys.argv[1] != "--child":
        raise SystemExit(2)
    mode = sys.argv[2]
    if mode == "transaction" and len(sys.argv) == 6:
        _child_transaction(Path(sys.argv[3]), sys.argv[4], sys.argv[5])
    elif mode == "model" and len(sys.argv) == 6:
        _child_model(Path(sys.argv[3]), sys.argv[4], Path(sys.argv[5]))
    elif mode == "phase" and len(sys.argv) == 4:
        _child_phase(Path(sys.argv[3]))
    else:
        raise SystemExit(2)
