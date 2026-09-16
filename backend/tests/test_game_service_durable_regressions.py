"""Exercise durable service boundaries with isolated storage and fake providers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

import app.services.game_service as module
from app.agents.llm_client import env_default_client_config
from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.models.game import GameConfig, GamePhase, GameState
from app.persistence.repository import GameRepository, InvalidExecutionTransition
from app.services.benchmark_game_executor import _role_assignments
from app.services.benchmark_service import BenchmarkService
from app.services.game_service import GameService


@pytest_asyncio.fixture
async def durable(tmp_path):
    repository = GameRepository(tmp_path)
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository)
    yield service, repository
    await service.aclose()
    repository.close()


@pytest.mark.asyncio
async def test_is_lobby_game_hides_benchmark_owned_records(durable):
    service, repository = durable
    add_game(service, repository, "native", benchmark=False)
    add_game(service, repository, "bench", benchmark=True)
    repository.create_game(
        game_id="orphan-bench", name="orphan", config={}, execution_status="failed",
        source="benchmark", model_snapshot=[],
    )
    service._games["orphan-bench"] = GameState(game_id="orphan-bench")
    service._games["legacy"] = GameState(game_id="legacy")
    assert service.is_lobby_game("native") is True
    assert service.is_lobby_game("bench") is False
    assert service.is_lobby_game("orphan-bench") is False
    assert service.is_lobby_game("legacy") is True
    service.repository = None
    assert service.is_lobby_game("bench") is True


@pytest.mark.asyncio
async def test_delete_benchmark_owned_game_releases_and_purges(durable):
    service, repository = durable
    add_game(service, repository, "bench", benchmark=True)
    from pathlib import Path
    archive = Path(service.data_dir) / "games" / "bench"
    archive.mkdir(parents=True)
    (archive / "game.log").write_text("{}\n", encoding="utf-8")
    engine = MagicMock()
    engine.stop = AsyncMock()
    service._engines["bench"] = engine
    await service.delete_benchmark_owned_game("run", "bench")
    engine.stop.assert_awaited()
    assert service.get_game_state("bench") is None
    assert repository.get_game("bench") is None
    assert not archive.exists()
    with pytest.raises(KeyError):
        await service.delete_benchmark_owned_game("run", "missing")
    add_game(service, repository, "native", benchmark=False)
    from app.persistence.repository import GameReferencedByBenchmark
    with pytest.raises(GameReferencedByBenchmark):
        await service.delete_benchmark_owned_game("run", "native")
    service.repository = None
    with pytest.raises(RuntimeError, match="unavailable"):
        await service.delete_benchmark_owned_game("run", "native")
    add_game(service, repository, "busy-bench", benchmark=True)
    service.repository = repository
    engine = MagicMock()
    engine.stop = AsyncMock()
    service._engines["busy-bench"] = engine
    service.cancel_benchmark_game = AsyncMock(side_effect=InvalidExecutionTransition("busy"))
    await service.delete_benchmark_owned_game("run", "busy-bench")
    assert service.get_game_state("busy-bench") is None
    add_game(service, repository, "idle-bench", benchmark=True)
    await service.delete_benchmark_owned_game("run", "idle-bench")
    assert service.get_game_state("idle-bench") is None


def add_game(service, repository, game_id="game", *, status="running", benchmark=True):
    repository.create_game(
        game_id=game_id, name="Original", config={}, execution_status=status,
        source="benchmark" if benchmark else "native",
        benchmark_run_id="run" if benchmark else None, model_snapshot=[],
    )
    service._games[game_id] = GameState(game_id=game_id)
    service._durable_contexts[game_id] = {"storage_revision": 0, "execution_generation": 1}


@pytest.mark.parametrize("entry, message", [
    ({"seats": []}, "no seats"),
    ({"seats": [True]}, "invalid seat"),
    ({"seats": [1, True]}, "inconsistent"),
    ({"seats": [1, 3]}, "inconsistent"),
    ({"seats": [1, 2]}, "inconsistent"),
])
def test_model_snapshot_refuses_ambiguous_runtime_binding(entry, message):
    config = env_default_client_config()
    with pytest.raises(ValueError, match=message):
        module._freeze_model_runtime({1: config, 2: replace(config, model_id="different")}, [entry])


@pytest.mark.parametrize("value, expected", [
    (None, None),
    (SimpleNamespace(prompt_tokens=True, completion_tokens=2, total_tokens=3), None),
    (SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
     {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}),
])
def test_provider_usage_preserves_known_counts_without_inventing_tokens(value, expected):
    assert module._usage_mapping(value) == expected


def test_executor_rejects_missing_frozen_role_map():
    with pytest.raises(ValueError, match="must be an object"):
        _role_assignments({}, 1)


@pytest.mark.asyncio
async def test_report_cancellation_does_not_clear_a_newer_refresh():
    repository = MagicMock()
    repository.get_benchmark_run.return_value = {"run_id": "run"}
    repository.get_latest_benchmark_report.return_value = None
    service = BenchmarkService(repository)
    await service.request_report("run")
    previous = service._report_tasks["run"]
    replacement = asyncio.create_task(asyncio.Event().wait())
    service._report_tasks["run"] = replacement
    previous.cancel()
    await asyncio.gather(previous, return_exceptions=True)
    await asyncio.sleep(0)
    assert service._report_tasks["run"] is replacement
    assert service._report_failures["run"] == "report_generation_cancelled"
    replacement.cancel()
    await asyncio.gather(replacement, return_exceptions=True)
    service._report_tasks.clear()
    cached = {"provisional": False, "input_digest": "stable"}
    repository.get_latest_benchmark_report.return_value = {"report": cached}
    assert await service.wait_for_report("run") == cached


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "paused"])
async def test_benchmark_cancel_stops_runner_and_invalidates_late_results(durable, status):
    service, repository = durable
    add_game(service, repository, status=status)
    if status == "running":
        runner = asyncio.create_task(asyncio.Event().wait())
        service._tasks["game"] = runner
        service._engines["game"] = SimpleNamespace(stop=AsyncMock())
        client = SimpleNamespace(aclose=AsyncMock())
        service._llm_clients["game"] = {1: client}
    result = await service.cancel_benchmark_game("game")
    assert result["execution_status"] == "cancelled"
    assert repository.get_game("game")["execution_generation"] == 2
    assert await service.cancel_benchmark_game("game") == result
    if status == "running":
        assert runner.cancelled()
        client.aclose.assert_awaited_once()
    assert (await service.wait_game("game"))["execution_status"] == "cancelled"


@pytest.mark.asyncio
async def test_benchmark_controls_validate_ownership_and_runner(durable, tmp_path):
    service, repository = durable
    with pytest.raises(KeyError):
        await service.cancel_benchmark_game("missing")
    with pytest.raises(KeyError):
        await service.wait_game("missing")
    with pytest.raises(KeyError):
        service._mark_recovery_blocked("missing", "corrupt")
    add_game(service, repository, benchmark=False)
    with pytest.raises(ValueError, match="not_managed"):
        await service.cancel_benchmark_game("game")
    with pytest.raises(InvalidExecutionTransition, match="no active runner"):
        await service.pause_game("game")
    assert (await service.resume_game("game"))["execution_status"] == "running"
    repository.transition_execution("game", expected=("running",), target="failed")
    service._mark_recovery_blocked("game", "checkpoint_corrupt")
    service._mark_recovery_blocked("game", "checkpoint_corrupt")
    legacy = GameService(WSManager(), EventBus(), data_dir=str(tmp_path / "legacy"))
    with pytest.raises(ValueError, match="unavailable"):
        await legacy.cancel_benchmark_game("game")
    await legacy._wait_model_consumption_allowed("game")
    await legacy._checkpoint_hook(None, [])("noop")
    await legacy.aclose()


@pytest.mark.asyncio
async def test_paused_game_without_live_runner_uses_recovery(durable):
    service, repository = durable
    add_game(service, repository, status="paused")
    with pytest.raises(ValueError, match="checkpoint_missing"):
        await service.resume_benchmark_game("game")
    assert repository.get_game("game")["recovery_block_code"] == "checkpoint_missing"


@pytest.mark.asyncio
async def test_native_rename_and_delete_are_durable_and_stop_clock(durable):
    service, repository = durable
    add_game(service, repository, benchmark=False)
    assert service.get_display_name("game") == "Original"
    service.rename_game("game", "Renamed")
    assert repository.get_game("game")["name"] == "Renamed"
    clock = asyncio.create_task(asyncio.Event().wait())
    service._clock_tasks["game"] = clock
    await service.delete_game("game")
    await asyncio.gather(clock, return_exceptions=True)
    assert repository.get_game("game") is None
    assert clock.cancelled()
    assert service.get_display_name("missing-id") == "missing-"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["running", "paused", "missing", "legacy", "stop_error", "storage_error"])
async def test_shutdown_always_drains_runners_even_when_cleanup_fails(durable, monkeypatch, mode):
    service, repository = durable
    if mode not in {"legacy", "missing"}:
        add_game(service, repository, status="paused" if mode == "paused" else "running")
    if mode == "legacy":
        service.repository = None
    runner = asyncio.create_task(asyncio.Event().wait())
    service._tasks["game"] = runner
    if mode != "missing":
        service._engines["game"] = SimpleNamespace(stop=AsyncMock(
            side_effect=RuntimeError("stop failed") if mode == "stop_error" else None,
        ))
    if mode == "storage_error":
        monkeypatch.setattr(repository, "transition_execution", MagicMock(side_effect=OSError("disk full")))
    await service.aclose()
    assert runner.cancelled()
    if mode in {"running", "stop_error"}:
        assert repository.get_game("game")["execution_status"] == "interrupted"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "missing", "failure"])
async def test_clock_heartbeat_tracks_only_active_time_and_exits(durable, monkeypatch, mode):
    service, repository = durable
    if mode != "missing":
        add_game(service, repository)
    real_sleep = asyncio.sleep
    ticks = 0

    async def next_tick(_delay):
        nonlocal ticks
        ticks += 1
        await real_sleep(0)
        if mode == "failure":
            raise OSError("clock unavailable")
        if ticks == 2:
            repository.transition_execution("game", expected=("running",), target="paused")
        if ticks == 3:
            repository.transition_execution("game", expected=("paused",), target="failed")

    monkeypatch.setattr(module.asyncio, "sleep", next_tick)
    service._start_clock_heartbeat("game")
    first = service._clock_tasks["game"]
    service._start_clock_heartbeat("game")
    assert service._clock_tasks["game"] is first
    await first
    if mode == "normal":
        assert repository.get_runtime_clock("game")["active_elapsed_ms"] >= 0
        assert ticks == 3


@pytest.mark.asyncio
async def test_clock_heartbeat_cancels_armed_benchmark_timeout(durable, monkeypatch):
    service, repository = durable
    add_game(service, repository, benchmark=True)
    repository.save_runtime_clock(
        "game", active_elapsed_ms=0, remaining_window_ms=None, execution_generation=1,
    )
    service._clock_state["game"] = {
        "active_elapsed_ms": 1500, "remaining_window_ms": None,
        "running_since": 1.0,
    }
    service.arm_benchmark_timeout("game", 1)
    real_sleep = asyncio.sleep

    async def one_tick(_delay):
        await real_sleep(0)

    monkeypatch.setattr(module.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(module.asyncio, "sleep", one_tick)
    service._start_clock_heartbeat("game")
    await asyncio.wait_for(service._clock_tasks["game"], timeout=1.0)
    record = repository.get_game("game")
    assert record["execution_status"] == "cancelled"
    assert record["recovery_block_code"] == "benchmark_game_timeout"


@pytest.fixture
def fake_provider(monkeypatch):
    class Client:
        instances = []
        payload = {"action_type": "pass", "target_seat": None, "reasoning": "test"}

        def __init__(self, *, config):
            self.config = config
            self.model_name = "fake-model"
            self.provider_profile = SimpleNamespace(profile_id="custom-openai")
            self.calls = 0
            self.closed = False
            self.instances.append(self)

        def invoke_json(self, *_args, **_kwargs):
            self.calls += 1
            return SimpleNamespace(payload=self.payload, usage=SimpleNamespace(
                prompt_tokens=1, completion_tokens=2, total_tokens=3,
            ))

        def invoke_action(self, *_args, **_kwargs):
            self.calls += 1
            return SimpleNamespace(payload=self.payload)

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(module, "LLMClient", Client)
    monkeypatch.setattr(GameEngine, "_game_loop", AsyncMock())
    return Client


@pytest.mark.asyncio
async def test_durable_night_and_action_gateway_persist_then_reuse_results(durable, fake_provider):
    from app.models.pipeline import IssuedActionRequest

    service, repository = durable
    game_id = await service.create_game(
        role_counts={"wolf-killer-werewolf": 1, "wolf-killer-guard": 1},
        model_seat_assignments={1: None, 2: None},
    )
    await service.wait_game(game_id)
    engine = service._engines[game_id]
    invoke = engine._director._invoke
    args = ([{"content": "test prompt"}], "werewolf_kill", {"type": "object"}, 1)
    first = invoke(*args)
    assert invoke(*args) == first
    request_rows = repository.list_model_requests(game_id)
    assert len(request_rows) == 1
    assert request_rows[0]["status"] == "resolved"
    attempts = repository.list_model_attempts(game_id)
    assert attempts[0]["total_tokens"] == 3

    renderer = MagicMock()
    renderer.render.return_value = "test action prompt"
    provider = service._command_provider(
        service._registry_snapshot, renderer,
        lambda seat: fake_provider.instances[seat - 1], engine._director,
    )
    spec = service._registry_snapshot.require("wolf-killer-guard")
    contract = spec.contracts[0]
    request = IssuedActionRequest(2, spec.role_id, contract, 0, 1, "night", "window", "guard:1")
    context = SimpleNamespace(game_id=game_id, round_number=1, phase="night", facts={})
    first_command = provider(request, context, 0)
    assert provider(request, context, 0) == first_command
    assert len(repository.list_model_requests(game_id)) == 2
    assert len(repository.list_model_attempts(game_id)) == 2
    # A committed step consumes the resolved requests, rather than redispatching.
    await engine._durable_checkpoint("gateway_checked")
    assert {row["status"] for row in repository.list_model_requests(game_id)} == {"consumed"}


@pytest.mark.asyncio
async def test_durable_action_gateway_bypasses_unbound_and_collected_wolf_actions(durable):
    from app.models.pipeline import IssuedActionRequest, ActionCommand, SchedulePoint

    service, repository = durable
    client = MagicMock()
    client.invoke_action.return_value = SimpleNamespace(payload={"action_type": "pass", "reasoning": "ok"})
    director = MagicMock()
    expected = ActionCommand(action_type="kill", target_seat=2, reasoning="collected")
    director.collected_vote.return_value = expected
    provider = service._command_provider(service._registry_snapshot, MagicMock(), lambda _seat: client, director)
    wolf = service._registry_snapshot.require("wolf-killer-werewolf")
    contract = next(c for c in wolf.contracts if c.schedule_point is SchedulePoint.NIGHT_WOLF_VOTE)
    request = IssuedActionRequest(1, wolf.role_id, contract, 0, 1, "night", "w", "wolf:1")
    assert provider(request, SimpleNamespace(game_id="unbound"), 0) == expected
    guard = service._registry_snapshot.require("wolf-killer-guard")
    request = IssuedActionRequest(1, guard.role_id, guard.contracts[0], 0, 1, "night", "w", "guard:1")
    context = SimpleNamespace(game_id="unbound", round_number=2, facts={})
    assert provider(request, context, 0).action_type == "pass"
    assert repository.list_games() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("options, message", [
    ({"benchmark_item_index": 0}, "requires benchmark_run_id"),
    ({"benchmark_run_id": "run"}, "benchmark source"),
    ({"model_seat_assignments": {}}, "every seat"),
    ({"model_seat_assignments": {1: 7}}, "string or null"),
])
async def test_creation_rejects_incomplete_ownership_and_model_seats(durable, options, message):
    service, repository = durable
    with pytest.raises(ValueError, match=message):
        await service.create_game(role_counts={"wolf-killer-villager": 1}, **options)
    assert repository.list_games() == []


@pytest.mark.asyncio
async def test_durable_night_rejects_non_object_provider_response(durable, fake_provider):
    service, repository = durable
    game_id = await service.create_game(role_counts={"wolf-killer-villager": 1})
    await service.wait_game(game_id)
    fake_provider.payload = []
    with pytest.raises(ValueError, match="not a JSON object"):
        service._engines[game_id]._director._invoke([], "werewolf_kill", {}, 1)
    assert repository.list_model_attempts(game_id)[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_invalid_tool_xml_falls_back_to_json_instead_of_accepting_bad_action():
    from langchain_core.messages import AIMessage
    from app.agents.output_parser import StrictCapabilityError
    from app.roles.base import BaseRole
    from tests.test_base_role import TestBaseRoleAccept, make_state

    state = make_state(phase=GamePhase.VOTE_CASTING)
    request = TestBaseRoleAccept()._vote_request(state)
    client = MagicMock()
    client.get_model_with_action_tool.return_value.ainvoke = AsyncMock(return_value=AIMessage(content=(
        '<tool_call>{"name":"' + request.contract.resolved_tool_name + '","arguments":'
        '{"action_type":"nonexistent","target_seat":2,"reasoning":"bad"}}</tool_call>'
    )))
    role = BaseRole(1, "wolf-killer-villager", MagicMock(), client)
    with pytest.raises(StrictCapabilityError, match="no native tool call"):
        await role._invoke_strict_action([], request)


@pytest.mark.asyncio
@pytest.mark.parametrize("decode_failure", [False, True])
async def test_finished_archive_with_corrupt_checkpoint_stays_finished(durable, decode_failure):
    import hashlib
    import sqlite3

    service, repository = durable
    add_game(service, repository, status="completed")
    service._games.clear()
    raw = "{}"
    digest = hashlib.sha256(raw.encode()).hexdigest() if decode_failure else "invalid"
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("INSERT INTO game_checkpoints VALUES (?,?,?,?,?,?)", ("game", 0, 1, raw, digest, "now"))
    service._load_native_games()
    assert repository.get_game("game")["execution_status"] == "completed"
    assert service.get_game_state("game") is None


def test_night_checkpoint_without_public_batch_emits_only_step_marker():
    engine = SimpleNamespace(game_id="game", state=GameState(game_id="game"), _pending_night_batch=None)
    events = GameService._checkpoint_domain_events(engine, "1:night_point:empty")
    assert events[0]["event_type"] == "STEP_COMMITTED"
    assert events[0]["visibility"] == []


@pytest.mark.asyncio
async def test_recovery_rejects_non_numeric_frozen_runtime_parameter(durable, monkeypatch):
    from tests.test_game_service_lifecycle import _state_with_players

    service, _ = durable
    bad = replace(env_default_client_config(), max_tokens="not-an-integer")
    monkeypatch.setattr(module, "env_default_client_config", lambda: bad)
    parameters = module._model_parameters(bad)
    record = {"config": {
        "engine_recovery_version": 1, "prompt_digest": module._prompt_digest(),
        "model_runtime_version": 1, "model_runtime": [{
            "config_id": None, "seats": [1, 2], "parameters": parameters,
            "parameters_digest": module._parameter_digest(parameters),
        }],
    }}
    with pytest.raises(ValueError, match="checkpoint_corrupt"):
        service._resolve_recovery_model_configs(_state_with_players(), record)


@pytest.mark.asyncio
@pytest.mark.parametrize("close_mode", ["missing", "raises"])
async def test_partial_recovery_client_cleanup_preserves_original_failure(durable, monkeypatch, close_mode):
    from tests.test_game_service_lifecycle import _state_with_players

    service, _ = durable
    state = _state_with_players()
    state.registry_digest = service._registry_snapshot.digest
    config = env_default_client_config()
    monkeypatch.setattr(service, "_resolve_recovery_model_configs", lambda *_: {1: config, 2: config})

    class Client:
        created = 0

        def __init__(self, **_kwargs):
            type(self).created += 1
            if self.created == 2:
                raise RuntimeError("transport construction failed")
            if close_mode == "raises":
                self.close = MagicMock(side_effect=RuntimeError("close failed too"))

    monkeypatch.setattr(module, "LLMClient", Client)
    with pytest.raises(ValueError, match="model_key_unavailable"):
        service._build_recovered_engine(state, {}, {})


@pytest.mark.asyncio
async def test_recovery_role_factory_lookup_failure_is_a_registry_block(durable, fake_provider, monkeypatch):
    from tests.test_game_service_lifecycle import _state_with_players

    service, _ = durable
    state = _state_with_players()
    state.registry_digest = service._registry_snapshot.digest
    config = env_default_client_config()
    monkeypatch.setattr(service, "_resolve_recovery_model_configs", lambda *_: {1: config, 2: config})
    monkeypatch.setattr(module.builtin_registry, "require", MagicMock(side_effect=KeyError("removed role")))
    with pytest.raises(ValueError, match="registry_mismatch"):
        service._build_recovered_engine(state, {}, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed", "removed"])
async def test_new_runner_completion_races_preserve_terminal_record(durable, fake_provider, monkeypatch, outcome):
    service, repository = durable

    async def raced_loop(engine):
        repository.transition_execution(engine.game_id, expected=("running",), target="cancelled")
        if outcome == "completed":
            engine.state.phase = GamePhase.GAME_OVER
            return
        if outcome == "removed":
            service._games.pop(engine.game_id)
        raise RuntimeError("late runner failure")

    monkeypatch.setattr(GameEngine, "_game_loop", raced_loop)
    game_id = await service.create_game(role_counts={"wolf-killer-villager": 1})
    await asyncio.gather(service._tasks[game_id], return_exceptions=True)
    await asyncio.sleep(0)
    assert repository.get_game(game_id)["execution_status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed", "missing_state", "legacy"])
async def test_recovered_watcher_handles_finalization_races(durable, outcome):
    service, repository = durable
    add_game(service, repository, status="cancelled")
    if outcome == "completed":
        service._games["game"].phase = GamePhase.GAME_OVER
    if outcome == "missing_state":
        service._games.clear()
    if outcome == "legacy":
        service.repository = None

    async def finish():
        if outcome != "completed":
            raise RuntimeError("late recovered runner failure")

    task = asyncio.create_task(finish())
    service._attach_task_watcher("game", None, task)
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert repository.get_game("game")["execution_status"] == "cancelled"


@pytest.mark.asyncio
async def test_recovered_night_gateway_rejects_malformed_payloads_and_handles_early_callback(
    durable, fake_provider, monkeypatch,
):
    service, repository = durable
    game_id = await service.create_game(role_counts={"wolf-killer-villager": 1})
    await service.wait_game(game_id)
    checkpoint = repository.load_checkpoint(game_id)
    state, orchestration = service._checkpoint_codec.decode(checkpoint["checkpoint"])
    record = repository.get_game(game_id)
    recovered, clients = service._build_recovered_engine(state, orchestration, record)
    fake_provider.payload = []
    with pytest.raises(ValueError, match="not a JSON object"):
        recovered._director._invoke([], "werewolf_kill", {}, 1)
    assert repository.list_model_attempts(game_id)[0]["status"] == "failed"

    invoke_sync = service._model_invocations.invoke_sync
    monkeypatch.setattr(service._model_invocations, "invoke_sync", lambda **_kwargs: SimpleNamespace(normalized_result=[]))
    with pytest.raises(ValueError, match="not a JSON object"):
        recovered._director._invoke([], "werewolf_discussion", {}, 1)

    monkeypatch.setattr(service._model_invocations, "invoke_sync", invoke_sync)
    fake_provider.payload = {"action_type": "pass", "reasoning": "probe"}
    observed = []

    class ConstructionStopped(Exception):
        pass

    class EarlyDirector:
        def __init__(self, _snapshot, invoke):
            observed.append(json.loads(invoke([], "early_restore_probe", {}, 1)))
            raise ConstructionStopped

    monkeypatch.setattr(module, "NightDirector", EarlyDirector)
    with pytest.raises(ConstructionStopped):
        service._build_recovered_engine(state, orchestration, record)
    assert observed == [fake_provider.payload]
    await service._close_clients(clients)
