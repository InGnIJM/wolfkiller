from __future__ import annotations

import asyncio
import copy
import json

import pytest

from app.config import PipelineMode
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.core.game_engine import (
    GameEngine, _PendingDeath, _PendingNightBatch, _PendingNightCompletion,
    _PendingWin,
)
from app.core.night_flow import WolfVote
from app.core.role_pipeline import PipelineDiff, PipelineResult
from app.core.scheduler import PointResult
from app.models.actions import SpeechRecord
from app.models.conversation import Conversation, ConversationScope
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.persistence.checkpoint_codec import CheckpointCodec, CheckpointError
from app.roles.registry import builtin_registry


class _Role:
    role_name = "wolf-killer-villager"

    def __init__(self) -> None:
        self._last_words_used = True


def _state(digest: str) -> GameState:
    return GameState(
        game_id="recover", phase=GamePhase.NIGHT, round_number=2,
        config=GameConfig(role_counts={"wolf-killer-villager": 1}),
        players={1: PlayerState(1, "wolf-killer-villager", "good")},
        registry_digest=digest, pipeline_version="v2", effect_schema_version=1,
    )


def test_engine_orchestration_round_trip_restores_future_behavior_state(tmp_path) -> None:
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    engine = GameEngine("recover", roles={1: _Role()}, data_dir=str(tmp_path))
    engine.state = _state(registry.digest)
    engine._pending_night_batch = _PendingNightBatch(2, 0, (), (), ())
    engine._last_words_given = {(1, 2)}
    engine.conversation_log.records = [
        Conversation(ConversationScope.PUBLIC, "hello", 2, speaker_seat=1, phase="speech")
    ]
    engine._rng.seed(17)
    expected_random = engine._rng.random()
    engine._rng.seed(17)

    orchestration = engine.export_orchestration(
        codec, model_assignments=[{"config_id": "model", "seats": [1]}],
    )
    orchestration = json.loads(json.dumps(orchestration))
    document = codec.encode(engine.state, orchestration=orchestration)
    restored_state, restored_orchestration = codec.decode(document)

    role = _Role()
    role._last_words_used = False
    restored = GameEngine("recover", roles={1: role}, data_dir=str(tmp_path))
    restored.load_restored_state(restored_state, restored_orchestration, codec)

    assert restored.state.phase is GamePhase.NIGHT
    assert restored.sm.get_state() is GamePhase.NIGHT
    assert restored._pending_night_batch == _PendingNightBatch(2, 0, (), (), ())
    assert restored._last_words_given == {(1, 2)}
    assert restored.conversation_log.records[0].content == "hello"
    assert role._last_words_used is True
    assert restored._rng.random() == expected_random


@pytest.mark.asyncio
async def test_restored_speech_round_does_not_repeat_checkpointed_seat(tmp_path):
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    source = GameEngine("recover", roles={1: _Role(), 2: _Role()}, data_dir=str(tmp_path))
    source.state = GameState(
        game_id="recover", phase=GamePhase.SPEECH, round_number=1,
        config=GameConfig(role_counts={"wolf-killer-villager": 2}),
        players={
            1: PlayerState(1, "wolf-killer-villager", "good"),
            2: PlayerState(2, "wolf-killer-villager", "good"),
        },
        speeches=[SpeechRecord(player_seat=1, text="already said", round_number=1)],
        registry_digest=registry.digest, pipeline_version="v2", effect_schema_version=1,
    )
    source.conversation_log.records = [
        Conversation(ConversationScope.PUBLIC, "already said", 1, speaker_seat=1, phase="speech"),
    ]
    source.sm.set_state(GamePhase.SPEECH)
    state, orchestration = codec.decode(
        codec.encode(source.state, orchestration=source.export_orchestration(codec)),
    )

    bus = EventBus()
    speeches = []

    async def record_speech(**kwargs):
        speeches.append(kwargs)

    bus.subscribe(BusEvent.SPEECH_MADE, record_speech)
    restored = GameEngine(
        "recover", roles={1: _Role(), 2: _Role()}, data_dir=str(tmp_path), event_bus=bus,
    )
    restored.load_restored_state(state, orchestration, codec)
    spoken = []

    async def capture_speak(seat, kind):
        spoken.append(seat)
        return f"speech from {seat}"

    restored.speak = capture_speak
    await restored._execute_speech_round()

    assert spoken == [2]
    assert [(record.player_seat, record.text) for record in restored.state.speeches] == [
        (1, "already said"),
        (2, "speech from 2"),
    ]
    assert [event["speech"].player_seat for event in speeches] == [2]
    public = [
        record for record in restored.conversation_log.records
        if record.speaker_seat == 1 and record.phase == "speech"
    ]
    assert len(public) == 1


def test_restore_enters_game_loop_without_calling_new_game_reset(tmp_path) -> None:
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    source = GameEngine("recover", roles={1: _Role()}, data_dir=str(tmp_path))
    source.state = _state(registry.digest)
    orchestration = source.export_orchestration(codec)
    state, orchestration = codec.decode(codec.encode(source.state, orchestration=orchestration))
    restored = GameEngine("recover", roles={1: _Role()}, data_dir=str(tmp_path))
    observed = []

    async def game_loop():
        observed.append((restored.state.phase, restored.state.round_number, restored._running))
        restored._running = False

    restored._game_loop = game_loop
    asyncio.run(restored.restore(state, orchestration, codec))
    assert observed == [(GamePhase.NIGHT, 2, True)]


def _rich_orchestration(tmp_path):
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    engine = GameEngine("recover", roles={1: _Role()}, data_dir=str(tmp_path))
    engine.state = _state(registry.digest)
    point = PointResult(
        (), (), ({"event_type": "NIGHT_ACTION", "payload": {"target": 1}},),
        "point-digest", ({"code": "fallback"},),
    )
    engine._pending_night_batch = _PendingNightBatch(
        2, 2, ("1号：目标2",), (WolfVote(1, "kill", 2, "reason"),),
        (point,), ((1, 2),), 2,
    )
    result = PipelineResult(
        ("action",), ("effect",), "pipeline-digest",
        ({"event_type": "PUBLIC"},), PipelineMode.V2,
        PipelineDiff(False, ("effects",)),
    )
    engine._pending_night_completion = _PendingNightCompletion(
        result, (_PendingDeath(2, "wolf_kill", 2),), 1, 3,
        _PendingWin("werewolf", "elimination"), True, False,
    )
    engine._last_words_given = {(1, 2, "wolf_kill")}
    engine._active_vote_window_id = "vote-window"
    engine.conversation_log.records = [
        Conversation(ConversationScope.WEREWOLF, "secret", 2, speaker_seat=1, phase="night")
    ]
    engine._checkpoint_counter = 7
    return codec, engine, json.loads(json.dumps(engine.export_orchestration(codec)))


def test_rich_engine_orchestration_round_trip_covers_pending_work(tmp_path) -> None:
    codec, source, orchestration = _rich_orchestration(tmp_path)
    restored = GameEngine("recover", roles={1: _Role()}, data_dir=str(tmp_path / "restored"))
    restored.load_restored_state(source.state, orchestration, codec)

    assert restored._pending_night_batch == source._pending_night_batch
    assert restored._pending_night_completion == source._pending_night_completion
    assert restored._last_words_given == {(1, 2, "wolf_kill")}
    assert restored._active_vote_window_id == "vote-window"
    assert restored._checkpoint_counter == 7
    assert restored.conversation_log.records[0].scope is ConversationScope.WEREWOLF


def test_pending_completion_round_trip_allows_absent_deaths_diff_and_win(tmp_path) -> None:
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    source = GameEngine("recover", roles={1: _Role()}, data_dir=str(tmp_path))
    source.state = _state(registry.digest)
    result = PipelineResult((), (), "digest", (), PipelineMode.V2, None)
    source._pending_night_completion = _PendingNightCompletion(result)
    orchestration = source.export_orchestration(codec)
    assert orchestration["pending_night_completion"]["deaths"] is None
    assert orchestration["pending_night_completion"]["win_result"] is None
    assert orchestration["pending_night_completion"]["result"]["diff"] is None

    restored = GameEngine("recover", roles={1: _Role()}, data_dir=str(tmp_path / "restored"))
    restored.load_restored_state(source.state, orchestration, codec)
    assert restored._pending_night_completion == source._pending_night_completion


def test_engine_orchestration_encoder_rejects_invalid_live_values(tmp_path) -> None:
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    engine = GameEngine("recover", data_dir=str(tmp_path))
    engine.state = _state(registry.digest)

    engine._pending_night_batch = object()
    with pytest.raises(CheckpointError, match="pending night batch"):
        engine.export_orchestration(codec)
    engine._pending_night_batch = None
    engine._pending_night_completion = object()
    with pytest.raises(CheckpointError, match="pending night completion"):
        engine.export_orchestration(codec)
    engine._pending_night_completion = None
    with pytest.raises(CheckpointError, match="orchestration JSON"):
        engine.export_orchestration(codec, model_assignments=[{"bad": object()}])


def _set(document, *path_and_value) -> None:
    *path, value = path_and_value
    target = document
    for name in path[:-1]:
        target = target[name]
    target[path[-1]] = value


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda doc: doc.pop("execution_position"), "fields"),
        (lambda doc: _set(doc, "pending_night_batch", {}), "pending night batch"),
        (lambda doc: _set(doc, "pending_night_batch", "stage", 99), "pending night batch"),
        (lambda doc: _set(doc, "pending_night_batch", "raw_results", 0, {}), "pending night batch"),
        (lambda doc: _set(doc, "pending_night_batch", "raw_results", 0, "events", None), "pending night batch"),
        (lambda doc: _set(doc, "pending_night_completion", {}), "pending night completion"),
        (lambda doc: _set(doc, "pending_night_completion", "result", {}), "pending night completion"),
        (lambda doc: _set(doc, "pending_night_completion", "result", "diff", {}), "pending night completion"),
        (lambda doc: _set(doc, "pending_night_completion", "result", "mode", "missing"), "pending night completion"),
        (lambda doc: _set(doc, "pending_night_completion", "deaths", [{"seat": 0, "cause": "x", "round_number": 1}]), "pending night completion"),
        (lambda doc: _set(doc, "conversation_records", [{"scope": "missing"}]), "engine orchestration"),
        (lambda doc: _set(doc, "last_words_given", [[1]]), "engine orchestration"),
        (lambda doc: _set(doc, "last_words_given", [[1, "bad"]]), "engine orchestration"),
        (lambda doc: _set(doc, "last_words_given", [[1, 2, 3]]), "engine orchestration"),
        (lambda doc: _set(doc, "active_vote_window_id", 1), "engine orchestration"),
        (lambda doc: _set(doc, "role_state", []), "engine orchestration"),
        (lambda doc: _set(doc, "random_state", []), "engine orchestration"),
        (lambda doc: _set(doc, "random_state", {"version": 3, "internal": None, "gauss_next": None}), "engine orchestration"),
        (lambda doc: _set(doc, "checkpoint_counter", -1), "checkpoint counter"),
    ],
)
def test_engine_orchestration_rejects_each_corrupt_shape(
    tmp_path, mutation, message,
) -> None:
    codec, source, orchestration = _rich_orchestration(tmp_path)
    document = copy.deepcopy(orchestration)
    mutation(document)
    restored = GameEngine("recover", roles={1: _Role()}, data_dir=str(tmp_path / "target"))
    with pytest.raises(CheckpointError, match=message):
        restored.load_restored_state(source.state, document, codec)


def test_restore_ignores_unknown_or_malformed_role_state_rows(tmp_path) -> None:
    codec, source, orchestration = _rich_orchestration(tmp_path)
    orchestration["role_state"] = {"1": {"last_words_used": "yes"}, "99": {"last_words_used": True}}
    role = _Role()
    role._last_words_used = False
    restored = GameEngine("recover", roles={1: role}, data_dir=str(tmp_path / "target"))
    restored.load_restored_state(source.state, orchestration, codec)
    assert role._last_words_used is False



def test_engine_rejects_foreign_state_and_active_night_restore(tmp_path):
    from unittest.mock import Mock

    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    engine = GameEngine("recover", data_dir=str(tmp_path))
    original = engine.state
    with pytest.raises(ValueError, match="does not belong"):
        engine.load_restored_state(GameState("other"), {}, codec)
    engine._night_task = Mock()
    engine._night_task.done.return_value = False
    with pytest.raises(ValueError, match="night execution is active"):
        engine.load_restored_state(_state(registry.digest), {}, codec)
    assert engine.state is original


@pytest.mark.asyncio
async def test_sync_checkpoint_hook_advances_once_and_phase_fault_preserves_cursor(tmp_path):
    engine = GameEngine("recover", data_dir=str(tmp_path))
    keys = []
    engine._checkpoint_hook = keys.append
    await engine._durable_checkpoint("sync")
    assert keys == ["00000000:sync"]
    engine.sm.set_state(GamePhase.NIGHT)
    def crash(boundary):
        assert boundary == "after_phase_transition"
        raise RuntimeError("simulated crash")
    engine._fault_injector = crash
    with pytest.raises(RuntimeError, match="simulated crash"):
        await engine._broadcast_phase_change()
    assert engine.state.phase is GamePhase.NIGHT
    assert engine._checkpoint_counter == 1
