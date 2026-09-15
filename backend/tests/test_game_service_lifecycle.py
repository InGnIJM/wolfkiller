from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

import app.services.game_service as service_module
from app.api.websocket.ws_handler import WSManager
from app.agents.llm_client import env_default_client_config
from app.core.event_bus import EventBus
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.models.actions import DeathReport, SpeechRecord, VoteAction
from app.models.contracts import (
    AcceptedAction, ActionCommand, ActionContract, ActionRequest,
)
from app.persistence.checkpoint_codec import CheckpointError
from app.persistence.repository import GameRepository
from app.services.game_service import GameService
from app.core.scheduler import PointResult
from app.persistence.repository import InvalidExecutionTransition
from app.services.model_invocation_service import ModelRequestUnavailable


def test_startup_quarantines_corrupt_checkpoint_instead_of_crashing(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    repository.create_game(
        game_id="corrupt", name="corrupt", config={},
        execution_status="interrupted", source="native", model_snapshot=[],
    )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "INSERT INTO game_checkpoints VALUES (?,?,?,?,?,?)",
            ("corrupt", 0, 1, "{}", "wrong-digest", "now"),
        )

    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    try:
        record = repository.get_game("corrupt")
        assert record["execution_status"] == "recovery_blocked"
        assert record["recovery_block_code"] == "checkpoint_corrupt"
    finally:
        asyncio.run(service.aclose())
        repository.close()


def test_startup_quarantines_checkpoint_that_fails_schema_decode(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    repository.create_game(
        game_id="invalid", name="invalid", config={},
        execution_status="paused", source="native", model_snapshot=[],
    )
    raw = json.dumps({}, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "INSERT INTO game_checkpoints VALUES (?,?,?,?,?,?)",
            ("invalid", 0, 1, raw, hashlib.sha256(raw.encode()).hexdigest(), "now"),
        )
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    try:
        assert repository.get_game("invalid")["recovery_block_code"] == "checkpoint_corrupt"
    finally:
        asyncio.run(service.aclose())
        repository.close()


def test_night_point_checkpoint_promotes_scheduler_public_events() -> None:
    state = GameState(game_id="game", config=GameConfig(
        role_counts={"wolf-killer-villager": 1},
    ))
    raw = PointResult((), (), ({
        "event_type": "NIGHT_ACTION",
        "payload": {"action_type": "guard", "target_seat": 1, "round_number": 1},
        "visibility": ("PUBLIC",),
    },), "digest")
    engine = SimpleNamespace(
        game_id="game", state=state,
        _pending_night_batch=SimpleNamespace(raw_results=(raw,)),
    )

    events = GameService._checkpoint_domain_events(
        engine, "00000001:night_point:1:night_action",
    )

    assert events == [{
        "event_type": "NIGHT_ACTION",
        "payload": {"action_type": "guard", "target_seat": 1, "round_number": 1},
        "visibility": ["PUBLIC"],
        "event_id": events[0]["event_id"],
        "schema_version": 1,
    }]


def test_vote_result_checkpoint_emits_tally_without_replaying_private_data() -> None:
    state = GameState(game_id="game", config=GameConfig(
        role_counts={"wolf-killer-villager": 3},
    ))
    state.round_number = 2
    state.votes = [
        VoteAction(1, 3, "private one"),
        VoteAction(2, 3, "private two"),
        VoteAction(3, None, "private abstention"),
    ]
    engine = SimpleNamespace(
        game_id="game", state=state, _pending_night_batch=None,
    )

    events = GameService._checkpoint_domain_events(
        engine, "00000008:vote_result:2:1:3",
    )

    assert events == [{
        "event_id": events[0]["event_id"],
        "event_type": "VOTE_RESULT",
        "payload": {
            "round_number": 2,
            "exiled_seat": 3,
            "counts": {"3": 2},
        },
        "visibility": ["PUBLIC"],
        "schema_version": 1,
    }]


def test_game_over_phase_checkpoint_emits_winner_event() -> None:
    state = GameState(game_id="game", config=GameConfig(
        role_counts={"wolf-killer-villager": 1},
    ))
    state.phase = GamePhase.GAME_OVER
    state.win_result = {"winning_camp": "good", "reason": "all_wolves_dead"}
    engine = SimpleNamespace(
        game_id="game", state=state, _pending_night_batch=None,
    )

    events = GameService._checkpoint_domain_events(
        engine, "00000009:phase:2:game_over:vote:1",
    )

    assert events[0]["event_type"] == "GAME_OVER"
    assert events[0]["payload"] == state.win_result


@pytest.mark.asyncio
async def test_pause_and_resume_live_runner_are_idempotent(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    state = GameState(game_id="game-1", config=GameConfig(
        role_counts={"wolf-killer-villager": 1},
    ))
    repository.create_game(
        game_id="game-1", name="game-1", config={},
        execution_status="running", source="native", model_snapshot=[],
    )
    controls: list[str] = []
    engine = SimpleNamespace(
        pause=lambda: controls.append("pause"),
        resume=lambda: controls.append("resume"),
    )
    service._games["game-1"] = state
    service._engines["game-1"] = engine
    service._tasks["game-1"] = asyncio.create_task(asyncio.sleep(60))
    try:
        first = await service.pause_game("game-1")
        second = await service.pause_game("game-1")
        resumed = await service.resume_game("game-1")

        assert first["execution_status"] == "paused"
        assert second["execution_status"] == "paused"
        assert resumed["execution_status"] == "running"
        assert controls == ["pause", "resume"]
        assert repository.get_game("game-1")["execution_generation"] == 1
    finally:
        service._tasks["game-1"].cancel()
        await asyncio.gather(service._tasks["game-1"], return_exceptions=True)
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_recover_without_checkpoint_sets_stable_block_code(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    repository.create_game(
        game_id="game-1", name="game-1", config={},
        execution_status="interrupted", source="native", model_snapshot=[],
    )
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    try:
        with pytest.raises(ValueError, match="checkpoint_missing"):
            await service.recover_game("game-1")
        record = repository.get_game("game-1")
        assert record["execution_status"] == "recovery_blocked"
        assert record["recovery_block_code"] == "checkpoint_missing"
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_manual_recovery_uses_checkpoint_without_new_game_reset(
    tmp_path, monkeypatch,
) -> None:
    import app.services.game_service as service_module
    from app.core.game_engine import GameEngine

    class FakeClient:
        def __init__(self, *, config) -> None:
            self.config = config

        async def aclose(self) -> None:
            pass

    async def stop_after_initial_checkpoint(self) -> None:
        self._running = False

    monkeypatch.setattr(service_module, "LLMClient", FakeClient)
    monkeypatch.setattr(GameEngine, "_game_loop", stop_after_initial_checkpoint)
    repository = GameRepository(tmp_path)
    first = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    game_id = await first.create_game(role_counts={
        "wolf-killer-werewolf": 1,
        "wolf-killer-villager": 3,
    })
    await first._tasks[game_id]
    before = repository.load_checkpoint(game_id)
    assert before is not None
    original_roles = {
        seat: player.role for seat, player in first.get_game_state(game_id).players.items()
    }
    repository.transition_execution(
        game_id, expected=("running",), target="interrupted",
    )

    second = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    observed: list[tuple[int, dict[int, str]]] = []

    async def restored_loop(self, state, orchestration, codec) -> None:
        observed.append((state.round_number, {
            seat: player.role for seat, player in state.players.items()
        }))
        self._running = False

    monkeypatch.setattr(GameEngine, "restore", restored_loop)
    try:
        info = await second.recover_game(game_id)
        recovered_engine = second._engines[game_id]
        assert all(role._rng is recovered_engine._rng for role in recovered_engine.roles.values())
        await second._tasks[game_id]
        assert info["execution_status"] == "running"
        assert observed == [(0, original_roles)]
        assert repository.get_game(game_id)["execution_generation"] == 2
        assert repository.load_checkpoint(game_id)["storage_revision"] == before["storage_revision"]
    finally:
        await first.aclose()
        await second.aclose()
        repository.close()


def _state_with_players(game_id: str = "game") -> GameState:
    state = GameState(game_id=game_id, config=GameConfig(
        role_counts={"wolf-killer-villager": 2}, reveal_on_death=True,
    ))
    state.round_number = 2
    state.players = {
        seat: PlayerState(seat, "wolf-killer-villager", "good")
        for seat in (1, 2)
    }
    return state


def test_checkpoint_domain_event_matrix_covers_public_and_fallback_events() -> None:
    state = _state_with_players()
    engine = SimpleNamespace(
        game_id="game", state=state, _pending_night_batch=None,
    )

    initialized = GameService._checkpoint_domain_events(
        engine, "00000001:game_initialized",
    )[0]
    assert initialized["event_type"] == "GAME_INITIALIZED"
    assert initialized["payload"]["config"]["reveal_on_death"] is True

    state.phase = GamePhase.SPEECH
    phase = GameService._checkpoint_domain_events(
        engine, "00000002:phase:2:speech",
    )[0]
    assert phase["payload"] == {"phase": "speech", "round_number": 2}

    state.votes = [VoteAction(1, None)]
    vote = GameService._checkpoint_domain_events(
        engine, "00000003:vote_result:2:1:none",
    )[0]
    assert vote["payload"]["exiled_seat"] is None
    assert vote["payload"]["counts"] == {}

    assert GameService._checkpoint_domain_events(
        engine, "00000004:speech:2:1",
    )[0]["payload"] == {}
    state.speeches = [SpeechRecord(1, "hello", 2)]
    assert GameService._checkpoint_domain_events(
        engine, "00000005:last_words:2:1",
    )[0]["payload"]["text"] == "hello"

    assert GameService._checkpoint_domain_events(
        engine, "00000006:vote_received:2:2",
    )[0]["payload"] == {}
    state.votes.append(VoteAction(2, 1, "reason"))
    assert GameService._checkpoint_domain_events(
        engine, "00000007:vote_received:2:2",
    )[0]["payload"]["target_seat"] == 1

    assert GameService._checkpoint_domain_events(
        engine, "00000008:night_death:2:1",
    )[0]["payload"] == {}
    state.death_history = [DeathReport(1, "wolf_kill", 2)]
    assert GameService._checkpoint_domain_events(
        engine, "00000009:night_death:2:1",
    )[0]["payload"]["cause"] == "wolf_kill"

    fallback = GameService._checkpoint_domain_events(
        engine, "00000010:exile_reaction:2:1",
    )[0]
    assert fallback["event_type"] == "STEP_COMMITTED"
    assert fallback["visibility"] == []
    state.death_history.extend([
        DeathReport(1, "exile", 2), DeathReport(2, "hunter_shot", 2),
        DeathReport(2, "exile", 1),
    ])
    deaths = GameService._checkpoint_domain_events(
        engine, "00000011:exile_reaction:2:1",
    )
    assert [item["payload"]["cause"] for item in deaths] == ["exile", "hunter_shot"]

    state.win_result = {"winning_camp": "good", "reason": "done"}
    assert GameService._checkpoint_domain_events(
        engine, "00000012:night_complete:2",
    )[0]["event_type"] == "GAME_OVER"
    assert GameService._checkpoint_domain_events(
        engine, "00000013:unknown",
    )[0]["payload"] == {"position": "unknown"}

    engine._pending_night_batch = SimpleNamespace(raw_results=(
        SimpleNamespace(events=({
            "event_type": "PUBLIC", "payload": "opaque",
            "visibility": {"PUBLIC"},
        },)),
    ))
    promoted = GameService._checkpoint_domain_events(
        engine, "00000014:night_point:2:action",
    )[0]
    assert promoted["payload"] == "opaque"
    assert promoted["visibility"] == ["PUBLIC"]
    engine._pending_night_batch = SimpleNamespace(
        raw_results=(SimpleNamespace(events=()),),
    )
    assert GameService._checkpoint_domain_events(
        engine, "00000015:night_point:2:empty",
    )[0]["event_type"] == "STEP_COMMITTED"


def test_wolf_discussion_checkpoint_emits_chat_and_skips_silent_turns() -> None:
    from app.core.night_flow import WolfVote

    state = GameState(game_id="game", config=GameConfig(
        role_counts={"wolf-killer-werewolf": 2},
    ))
    state.round_number = 1
    engine = SimpleNamespace(
        game_id="game", state=state,
        _pending_night_batch=SimpleNamespace(
            discussion_history=("3号：我怀疑2号",),
            wolf_votes=(),
        ),
    )

    spoke = GameService._checkpoint_domain_events(
        engine, "00000002:wolf_discussion:1:3:1",
    )
    assert spoke == [{
        "event_id": spoke[0]["event_id"],
        "event_type": "WOLF_CHAT_MESSAGE",
        "payload": {"seat": 3, "text": "我怀疑2号", "round_number": 1},
        "visibility": ["PUBLIC"],
        "schema_version": 1,
    }]

    engine._pending_night_batch = SimpleNamespace(
        discussion_history=("3号：先刀预言家（次日计划：白天投4）",),
        wolf_votes=(),
    )
    planned = GameService._checkpoint_domain_events(
        engine, "00000003:wolf_discussion:1:3:1",
    )
    assert planned[0]["payload"]["text"] == "先刀预言家"

    engine._pending_night_batch = SimpleNamespace(
        discussion_history=("3号：（跳过）",),
        wolf_votes=(),
    )
    skipped = GameService._checkpoint_domain_events(
        engine, "00000004:wolf_discussion:1:3:1",
    )
    assert skipped[0]["event_type"] == "STEP_COMMITTED"
    assert skipped[0]["visibility"] == []

    engine._pending_night_batch = None
    missing = GameService._checkpoint_domain_events(
        engine, "00000005:wolf_discussion:1:3:1",
    )
    assert missing[0]["event_type"] == "STEP_COMMITTED"

    engine._pending_night_batch = SimpleNamespace(discussion_history=())
    assert GameService._checkpoint_domain_events(
        engine, "00000005:wolf_discussion:1:3:1",
    )[0]["event_type"] == "STEP_COMMITTED"

    engine._pending_night_batch = SimpleNamespace(
        discussion_history=("6号：刀预言家",),
    )
    assert GameService._checkpoint_domain_events(
        engine, "00000005:wolf_discussion:1:3:1",
    )[0]["event_type"] == "STEP_COMMITTED"

    engine._pending_night_batch = SimpleNamespace(
        wolf_votes=(WolfVote(3, "kill", 2, "像神"),),
    )
    vote = GameService._checkpoint_domain_events(
        engine, "00000006:wolf_vote:1:3",
    )
    assert vote == [{
        "event_id": vote[0]["event_id"],
        "event_type": "WOLF_VOTE",
        "payload": {
            "seat": 3, "target_seat": 2, "reasoning": "像神", "round_number": 1,
        },
        "visibility": ["PUBLIC"],
        "schema_version": 1,
    }]

    engine._pending_night_batch = SimpleNamespace(wolf_votes=())
    empty_vote = GameService._checkpoint_domain_events(
        engine, "00000007:wolf_vote:1:3",
    )
    assert empty_vote[0]["event_type"] == "STEP_COMMITTED"

    engine._pending_night_batch = None
    assert GameService._checkpoint_domain_events(
        engine, "00000007:wolf_vote:1:3",
    )[0]["event_type"] == "STEP_COMMITTED"


@pytest.mark.asyncio
async def test_execution_controls_reject_missing_managed_and_invalid_games(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    repository.create_game(
        game_id="managed", name="managed", config={}, execution_status="running",
        source="benchmark", model_snapshot=[], benchmark_run_id="run-1",
    )
    repository.create_game(
        game_id="failed", name="failed", config={}, execution_status="failed",
        source="native", model_snapshot=[],
    )
    controls: list[str] = []
    service._engines["managed"] = SimpleNamespace(
        pause=lambda: controls.append("pause"),
        resume=lambda: controls.append("resume"),
    )
    service._tasks["managed"] = asyncio.create_task(asyncio.sleep(60))
    try:
        with pytest.raises(KeyError):
            service.get_execution_info("missing")
        with pytest.raises(KeyError):
            await service.pause_game("missing")
        with pytest.raises(KeyError):
            await service.resume_game("missing")
        with pytest.raises(KeyError):
            await service.recover_game("missing")
        with pytest.raises(ValueError, match="game_managed_by_benchmark"):
            await service.pause_game("managed")
        with pytest.raises(ValueError, match="game_managed_by_benchmark"):
            await service.resume_game("managed")
        with pytest.raises(ValueError, match="game_managed_by_benchmark"):
            await service.recover_game("managed", expected_statuses=("running",))
        with pytest.raises(InvalidExecutionTransition):
            await service.pause_game("failed")
        with pytest.raises(InvalidExecutionTransition):
            await service.resume_game("failed")
        with pytest.raises(InvalidExecutionTransition):
            await service.recover_game("failed")
        assert (await service.pause_benchmark_game("managed"))["execution_status"] == "paused"
        assert (await service.resume_benchmark_game("managed"))["execution_status"] == "running"
        assert (await service.recover_benchmark_game("managed"))["execution_status"] == "running"
        assert controls == ["pause", "resume"]
    finally:
        service._tasks["managed"].cancel()
        await asyncio.gather(service._tasks["managed"], return_exceptions=True)
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_legacy_execution_controls_and_blocking_errors(tmp_path) -> None:
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    live = _state_with_players("live")
    done = _state_with_players("done")
    done.phase = GamePhase.GAME_OVER
    controls: list[str] = []
    service._games.update(live=live, done=done)
    service._engines["live"] = SimpleNamespace(
        pause=lambda: controls.append("pause"),
        resume=lambda: controls.append("resume"),
    )
    try:
        assert service.get_execution_info("live")["execution_status"] == "running"
        assert service.get_execution_info("done")["execution_status"] == "completed"
        await service.pause_game("live")
        await service.resume_game("live")
        assert controls == ["pause", "resume"]
        with pytest.raises(KeyError):
            service.get_execution_info("missing")
        with pytest.raises(KeyError):
            await service.pause_game("missing")
        with pytest.raises(KeyError):
            await service.resume_game("missing")
        with pytest.raises(ValueError, match="legacy_archive"):
            await service.recover_game("live")
        with pytest.raises(ValueError, match="blocked"):
            service._mark_recovery_blocked("live", "blocked")
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_pause_rolls_back_engine_when_persistence_fails(tmp_path, monkeypatch) -> None:
    repository = GameRepository(tmp_path)
    repository.create_game(
        game_id="game", name="game", config={}, execution_status="running",
        source="native", model_snapshot=[],
    )
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    controls: list[str] = []
    service._engines["game"] = SimpleNamespace(
        pause=lambda: controls.append("pause"),
        resume=lambda: controls.append("resume"),
    )
    monkeypatch.setattr(
        repository, "transition_execution",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk")),
    )
    try:
        with pytest.raises(RuntimeError, match="disk"):
            await service.pause_game("game")
        assert controls == ["pause", "resume"]
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_model_consumption_waits_for_resume_and_rejects_terminal_state(
    tmp_path, monkeypatch,
) -> None:
    repository = GameRepository(tmp_path)
    repository.create_game(
        game_id="game", name="game", config={}, execution_status="paused",
        source="native", model_snapshot=[],
    )
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    original_sleep = asyncio.sleep

    async def resume_on_sleep(_delay: float) -> None:
        repository.transition_execution(
            "game", expected=("paused",), target="running",
        )
        await original_sleep(0)

    try:
        monkeypatch.setattr(asyncio, "sleep", resume_on_sleep)
        await service._wait_model_consumption_allowed("game")
        repository.transition_execution(
            "game", expected=("running",), target="failed",
        )
        with pytest.raises(ModelRequestUnavailable, match="failed"):
            await service._wait_model_consumption_allowed("game")
        repository.mark_game_deleted("game")
        with pytest.raises(ModelRequestUnavailable, match="deleted"):
            await service._wait_model_consumption_allowed("game")
    finally:
        await service.aclose()
        repository.close()


def test_recovery_model_config_validation_matrix(tmp_path, monkeypatch) -> None:
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    state = _state_with_players()
    runtime_config = service_module.replace(
        env_default_client_config(), provider_profile="auto",
    )
    parameters = service_module._model_parameters(runtime_config)

    def document(entry=None):
        current = {
            "config_id": None,
            "seats": [1, 2],
            "parameters": dict(parameters),
            "parameters_digest": service_module._parameter_digest(parameters),
        } if entry is None else entry
        return {
            "engine_recovery_version": 1,
            "prompt_digest": service_module._prompt_digest(),
            "model_runtime_version": 1,
            "model_runtime": [current],
        }

    try:
        resolved = service._resolve_recovery_model_configs(
            state, {"config": document()},
        )
        assert set(resolved) == {1, 2}
        assert all(config.api_key == runtime_config.api_key for config in resolved.values())

        invalid_cases = [
            ({"config": None}, "model_config_missing"),
            ({"config": {**document(), "engine_recovery_version": 2}},
             "checkpoint_version_unsupported"),
            ({"config": {**document(), "prompt_digest": "old"}},
             "prompt_version_mismatch"),
            ({"config": {**document(), "model_runtime_version": 2}},
             "model_config_missing"),
            ({"config": {**document(), "model_runtime": {}}},
             "model_config_missing"),
            ({"config": document("bad")}, "model_config_missing"),
            ({"config": document({"config_id": 1, "seats": [1],
                                    "parameters": parameters})},
             "model_config_missing"),
            ({"config": document({"config_id": None, "seats": [],
                                    "parameters": parameters})},
             "model_config_missing"),
            ({"config": document({"config_id": None, "seats": [1, 2],
                                    "parameters": parameters,
                                    "parameters_digest": "wrong"})},
             "checkpoint_corrupt"),
            ({"config": document({"config_id": None, "seats": [1, 1],
                                    "parameters": parameters,
                                    "parameters_digest": service_module._parameter_digest(parameters)})},
             "checkpoint_corrupt"),
            ({"config": document({"config_id": None, "seats": [1],
                                    "parameters": parameters,
                                    "parameters_digest": service_module._parameter_digest(parameters)})},
             "model_config_missing"),
        ]
        for record, message in invalid_cases:
            with pytest.raises(ValueError, match=message):
                service._resolve_recovery_model_configs(state, record)

        changed = dict(parameters)
        changed["model_id"] = f"{parameters['model_id']}-changed"
        with pytest.raises(ValueError, match="model_config_changed"):
            service._resolve_recovery_model_configs(state, {"config": document({
                "config_id": None, "seats": [1, 2], "parameters": changed,
                "parameters_digest": service_module._parameter_digest(changed),
            })})

        saved = document()
        saved["model_runtime"][0]["config_id"] = "saved"
        monkeypatch.setattr(
            service_module, "_saved_client_config",
            lambda _id, _fallback: (runtime_config, {}),
        )
        assert set(service._resolve_recovery_model_configs(
            state, {"config": saved},
        )) == {1, 2}

        def unavailable(_id, _fallback):
            raise ValueError("key decryption failed")

        monkeypatch.setattr(service_module, "_saved_client_config", unavailable)
        with pytest.raises(ValueError, match="model_key_unavailable"):
            service._resolve_recovery_model_configs(state, {"config": saved})

        def unknown(_id, _fallback):
            raise ValueError("unknown model config")

        monkeypatch.setattr(service_module, "_saved_client_config", unknown)
        with pytest.raises(ValueError, match="model_config_missing"):
            service._resolve_recovery_model_configs(state, {"config": saved})
    finally:
        asyncio.run(service.aclose())


def test_sync_model_consumption_waits_and_rejects_invalid_records(
    tmp_path, monkeypatch,
) -> None:
    repository = GameRepository(tmp_path)
    repository.create_game(
        game_id="game", name="game", config={}, execution_status="paused",
        source="native", model_snapshot=[],
    )
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )

    def resume_on_sleep(_delay: float) -> None:
        repository.transition_execution(
            "game", expected=("paused",), target="running",
        )

    try:
        monkeypatch.setattr(service_module.time, "sleep", resume_on_sleep)
        service._wait_model_consumption_allowed_sync("game")
        repository.transition_execution(
            "game", expected=("running",), target="failed",
        )
        with pytest.raises(ModelRequestUnavailable, match="failed"):
            service._wait_model_consumption_allowed_sync("game")
        repository.mark_game_deleted("game")
        with pytest.raises(ModelRequestUnavailable, match="deleted"):
            service._wait_model_consumption_allowed_sync("game")

        legacy = GameService(WSManager(), EventBus(), data_dir=str(tmp_path / "legacy"))
        try:
            legacy._wait_model_consumption_allowed_sync("anything")
        finally:
            asyncio.run(legacy.aclose())
    finally:
        asyncio.run(service.aclose())
        repository.close()


@pytest.mark.asyncio
async def test_recovered_task_watcher_finalizes_success_failure_and_cancel(
    tmp_path,
) -> None:
    repository = GameRepository(tmp_path)
    for game_id in ("success", "failure", "cancelled"):
        repository.create_game(
            game_id=game_id, name=game_id, config={}, execution_status="running",
            source="native", model_snapshot=[],
        )
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    success_state = _state_with_players("success")
    success_state.phase = GamePhase.GAME_OVER
    failure_state = _state_with_players("failure")
    service._games.update(success=success_state, failure=failure_state)

    async def succeed() -> None:
        return None

    async def fail() -> None:
        raise RuntimeError("engine crash")

    success = asyncio.create_task(succeed())
    failure = asyncio.create_task(fail())
    cancelled = asyncio.create_task(asyncio.sleep(60))
    service._attach_task_watcher("success", SimpleNamespace(), success)
    service._attach_task_watcher("failure", SimpleNamespace(), failure)
    service._attach_task_watcher("cancelled", SimpleNamespace(), cancelled)
    cancelled.cancel()
    await asyncio.gather(success, failure, cancelled, return_exceptions=True)
    await asyncio.sleep(0)
    try:
        assert repository.get_game("success")["execution_status"] == "completed"
        assert repository.get_game("failure")["execution_status"] == "failed"
        assert failure_state.phase is GamePhase.ERROR
        assert repository.get_game("cancelled")["execution_status"] == "running"
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_recovery_maps_checkpoint_and_engine_validation_failures(
    tmp_path, monkeypatch,
) -> None:
    repository = GameRepository(tmp_path)
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    monkeypatch.setattr(
        repository, "load_checkpoint",
        lambda _game_id: {"checkpoint": {}, "storage_revision": 1},
    )
    cases = [
        ("unsupported checkpoint version 99", "checkpoint_version_unsupported"),
        ("registry digest mismatch", "registry_mismatch"),
        ("malformed state", "checkpoint_corrupt"),
    ]
    try:
        for index, (message, expected) in enumerate(cases):
            game_id = f"decode-{index}"
            repository.create_game(
                game_id=game_id, name=game_id, config={},
                execution_status="interrupted", source="native", model_snapshot=[],
            )
            monkeypatch.setattr(
                service._checkpoint_codec, "decode",
                lambda _doc, _message=message: (_ for _ in ()).throw(
                    CheckpointError(_message)
                ),
            )
            with pytest.raises(ValueError, match=expected):
                await service.recover_game(game_id)
            assert repository.get_game(game_id)["recovery_block_code"] == expected
            with pytest.raises(ValueError, match=expected):
                await service.recover_game(game_id)

        state = _state_with_players("build")
        monkeypatch.setattr(
            service._checkpoint_codec, "decode", lambda _doc: (state, {}),
        )
        for index, source_code in enumerate((
            "model_config_missing", "model_config_changed", "model_key_unavailable",
            "prompt_version_mismatch", "checkpoint_version_unsupported",
            "registry_mismatch", "checkpoint_corrupt", "unexpected detail",
        )):
            game_id = f"build-{index}"
            state.game_id = game_id
            repository.create_game(
                game_id=game_id, name=game_id, config={},
                execution_status="interrupted", source="native", model_snapshot=[],
            )
            monkeypatch.setattr(
                service, "_build_recovered_engine",
                lambda *_args, _code=source_code: (_ for _ in ()).throw(
                    ValueError(_code)
                ),
            )
            expected = source_code if source_code != "unexpected detail" else "checkpoint_corrupt"
            with pytest.raises(ValueError, match=expected):
                await service.recover_game(game_id)
            assert repository.get_game(game_id)["recovery_block_code"] == expected

        repository.create_game(
            game_id="already-running", name="already-running", config={},
            execution_status="running", source="native", model_snapshot=[],
        )
        assert (await service.recover_game("already-running"))["execution_status"] == "running"
    finally:
        await service.aclose()
        repository.close()


def test_runtime_clock_persists_elapsed_and_optional_window(tmp_path, monkeypatch) -> None:
    repository = GameRepository(tmp_path)
    repository.create_game(
        game_id="game", name="game", config={}, execution_status="running",
        source="native", model_snapshot=[],
    )
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    service._durable_contexts["game"] = {
        "storage_revision": 0, "execution_generation": 1,
    }
    service._clock_state["game"] = {
        "active_elapsed_ms": 100, "remaining_window_ms": 900,
        "running_since": 10.0,
    }
    monkeypatch.setattr(service_module.time, "monotonic", lambda: 10.25)
    try:
        service._persist_runtime_clock("game", running=True)
        clock = repository.get_runtime_clock("game")
        assert clock["active_elapsed_ms"] == 350
        assert clock["remaining_window_ms"] == 900
        assert clock["execution_generation"] == 1
        service._clock_state["game"]["remaining_window_ms"] = "invalid"
        service._persist_runtime_clock("game", running=False)
        assert repository.get_runtime_clock("game")["remaining_window_ms"] is None
        service._persist_runtime_clock("unknown", running=False)

        legacy = GameService(WSManager(), EventBus(), data_dir=str(tmp_path / "legacy-clock"))
        try:
            legacy._persist_runtime_clock("game", running=True)
        finally:
            asyncio.run(legacy.aclose())
    finally:
        asyncio.run(service.aclose())
        repository.close()


@pytest.mark.asyncio
async def test_role_instrumentation_durably_round_trips_speech_and_action(
    tmp_path,
) -> None:
    repository = GameRepository(tmp_path)
    repository.create_game(
        game_id="game", name="game", config={}, execution_status="running",
        source="native", model_snapshot=[],
    )
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    service._durable_contexts["game"] = {
        "storage_revision": 0, "execution_generation": 2,
    }
    observed: list[dict] = []

    class Invocations:
        malformed = False

        async def invoke(self, **kwargs):
            observed.append(kwargs)
            normalized, _usage = await kwargs["provider_call"]()
            if self.malformed:
                normalized = {"command": None}
            return SimpleNamespace(normalized_result=normalized)

    invocations = Invocations()
    service._model_invocations = invocations

    async def speak(_state, _log, _context):
        return "public words"

    contract = ActionContract(
        contract_id="vote", phase=GamePhase.VOTE_CASTING,
        action_types=("vote",), actions_requiring_target=frozenset({"vote"}),
        resolution_priority=1, fallback_action_type="vote",
    )
    request = ActionRequest(
        actor_seat=1, role_id="wolf-killer-villager", contract=contract,
        phase=GamePhase.VOTE_CASTING, round_id=2, idempotency_key="vote:2:1",
    )

    async def request_action(_state, _log, original_request):
        return AcceptedAction(
            request=original_request,
            command=ActionCommand(action_type="vote", target_seat=2, reasoning="ok"),
            technical_failure_code="retry", timeout_type="soft",
        )

    role = SimpleNamespace(
        speak=speak, request_action=request_action,
        llm_client=SimpleNamespace(
            provider_profile=SimpleNamespace(profile_id="profile"), model_name="model",
        ),
    )
    state = _state_with_players()
    state.phase = GamePhase.VOTE_CASTING
    try:
        service._instrument_roles("game", {1: role})
        assert await role.speak(state, [], "day") == "public words"
        accepted = await role.request_action(state, [], request)
        assert accepted.command.target_seat == 2
        assert accepted.technical_failure_code == "retry"
        assert accepted.timeout_type == "soft"
        assert [item["recovery"] for item in observed] == [True, True]
        assert service._client_identity(object()) == ("unknown", "unknown")

        invocations.malformed = True
        with pytest.raises(ModelRequestUnavailable, match="no command"):
            await role.request_action(state, [], request)
    finally:
        await service.aclose()
        repository.close()


def test_build_recovered_engine_maps_registry_client_and_role_failures(
    tmp_path, monkeypatch,
) -> None:
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    state = _state_with_players()
    state.registry_digest = "wrong"
    try:
        with pytest.raises(ValueError, match="registry_mismatch"):
            service._build_recovered_engine(state, {}, {"config": {}})

        state.registry_digest = service._registry_snapshot.digest
        config = env_default_client_config()
        monkeypatch.setattr(
            service, "_resolve_recovery_model_configs",
            lambda *_args: {1: config, 2: config},
        )
        closed: list[int] = []

        class PartialClient:
            count = 0

            def __init__(self, *, config):
                type(self).count += 1
                self.number = type(self).count
                if self.number == 2:
                    raise RuntimeError("transport")

            def close(self):
                closed.append(self.number)

        monkeypatch.setattr(service_module, "LLMClient", PartialClient)
        with pytest.raises(ValueError, match="model_key_unavailable"):
            service._build_recovered_engine(state, {}, {"config": {}})
        assert closed == [1]

        class Client:
            def __init__(self, *, config):
                self.config = config

        monkeypatch.setattr(service_module, "LLMClient", Client)
        monkeypatch.setattr(
            service, "_resolve_recovery_model_configs", lambda *_args: {1: config},
        )
        with pytest.raises(ValueError, match="checkpoint_corrupt"):
            service._build_recovered_engine(state, {}, {"config": {}})

        monkeypatch.setattr(
            service, "_resolve_recovery_model_configs",
            lambda *_args: {1: config, 2: config},
        )
        state.players[1].role = "unknown-role"
        with pytest.raises(ValueError, match="registry_mismatch"):
            service._build_recovered_engine(state, {}, {"config": {}})
    finally:
        asyncio.run(service.aclose())


def test_recovered_night_gateway_uses_durable_result_and_logs_telemetry(
    tmp_path, monkeypatch,
) -> None:
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    state = _state_with_players()
    state.registry_digest = service._registry_snapshot.digest
    service._durable_contexts[state.game_id] = {
        "storage_revision": 1, "execution_generation": 2,
    }
    config = env_default_client_config()
    calls: list[dict] = []
    telemetry: list[dict] = []

    class Client:
        provider_profile = SimpleNamespace(profile_id="profile")
        model_name = "model"

        def __init__(self, *, config):
            self.config = config

        def invoke_json(self, messages, *, tool_name, schema):
            calls.append({"messages": messages, "tool_name": tool_name, "schema": schema})
            return SimpleNamespace(
                payload={"action_type": "pass", "target_seat": None}, usage=None,
            )

    class Invocations:
        @staticmethod
        def request_digest(_request):
            return "abcdef0123456789"

        @staticmethod
        def invoke_sync(**kwargs):
            calls.append(kwargs)
            normalized, _usage = kwargs["provider_call"]()
            return SimpleNamespace(normalized_result=normalized)

    class Engine:
        def __init__(self, **kwargs):
            self.director = kwargs["director"]
            self._rng = service_module.random.Random(7)
            self.game_logger = SimpleNamespace(
                log_llm_call=lambda *args, **kwargs: telemetry.append(kwargs),
            )

        def load_restored_state(self, state, orchestration, codec):
            self.loaded = (state, orchestration, codec)

    service._model_invocations = Invocations()
    monkeypatch.setattr(service_module, "LLMClient", Client)
    monkeypatch.setattr(service_module, "GameEngine", Engine)
    monkeypatch.setattr(
        service, "_resolve_recovery_model_configs",
        lambda *_args: {1: config, 2: config},
    )
    try:
        engine, clients = service._build_recovered_engine(
            state, {"position": "night"}, {"config": {}},
        )
        raw = engine.director._invoke(
            [{"content": "prompt"}], "werewolf_kill", {"type": "object"}, 1,
        )
        assert json.loads(raw) == {"action_type": "pass", "target_seat": None}
        assert set(clients) == {1, 2}
        durable_call = next(item for item in calls if "request_id" in item)
        assert durable_call["request_id"].startswith("night:2:werewolf_kill:1:")
        assert durable_call["recovery"] is True
        assert telemetry[0]["contract_id"] == "werewolf_kill"
    finally:
        asyncio.run(service.aclose())
