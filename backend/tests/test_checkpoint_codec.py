from __future__ import annotations

import copy
import json
from dataclasses import fields

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import CommitResult, EffectApplier, _Runtime
from app.core.point_journal import PointCheckpoint, PointKey, WorkCursor, point_journal
from app.core.scheduler import Scheduler
from app.models.actions import DeathReport, NightAction, SpeechRecord, VoteAction
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import ActionCommand, IssuedActionRequest, SchedulePoint
from app.persistence.checkpoint_codec import CHECKPOINT_VERSION, CheckpointCodec, CheckpointError
import app.persistence.checkpoint_codec as checkpoint_codec_module
from app.roles.registry import builtin_registry


def _rich_state() -> GameState:
    state = GameState(
        game_id="game-checkpoint",
        phase=GamePhase.VOTE_CASTING,
        round_number=3,
        vote_round=2,
        is_tiebreak=True,
        tiebreak_candidates={1, 2},
        supplemental_speakers={1},
        voted_seats={2},
        accepted_action_keys={"accepted-a"},
        config=GameConfig(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 1},
            reveal_on_death=True,
        ),
        players={
            1: PlayerState(
                1, "wolf-killer-werewolf", "werewolf", is_alive=True,
                is_sheriff=True, check_results=[{"seat": 2, "camp": "good"}],
                revealed_role="wolf-killer-werewolf",
            ),
            2: PlayerState(2, "wolf-killer-villager", "good", is_alive=False),
        },
        sheriff=1,
        speeches=[SpeechRecord(1, "测试发言", 3, "speech")],
        votes=[VoteAction(1, 2, "理由", "思考")],
        night_actions=[NightAction(1, "kill", 2, "理由", "思考")],
        death_history=[DeathReport(2, "wolf_kill", 3)],
        win_result=None,
        last_wolf_kill_target=2,
        sheriff_election_complete=True,
        speaking_order=[1],
        current_speaker=1,
        state_revision=7,
        pipeline_version="v2",
        registry_digest=builtin_registry.freeze().digest,
        spec_versions={"wolf-killer-werewolf": 1},
        effect_schema_version=1,
        last_consistent_checkpoint="vote:3:2",
    )
    commit = CommitResult("action-a", ("effect-a",), 7, (), "digest-a")
    state._pipeline_runtime = _Runtime(
        revision=7,
        role_resources={1: {"gun": 1}},
        private_data={1: {"known": [2]}},
        statuses={1: {"protected"}},
        relations={1: {("teammate", 2)}},
        private_facts={1: [{"namespace": "seer", "fact": {"seat": 2}}]},
        pending_damage=({"target": 2, "amount": 1, "cause": "wolf_kill"},),
        pending_protection=(),
        events=({"event_type": "TEST", "payload": {}},),
        commits={"action-a": commit},
        resource_setup_digest="setup",
        action_counts={"window": {"x": 1}, "round": {}, "game": {}},
        vote_receipts={},
    )
    registry = builtin_registry.freeze()
    contract = registry.require("wolf-killer-werewolf").contracts[0]
    request = IssuedActionRequest(
        1, "wolf-killer-werewolf", contract, 7, 3, "night",
        "window-a", "action-a",
    )
    key = PointKey("game-checkpoint", 3, "night", SchedulePoint.NIGHT_ACTION, registry.digest)
    point_journal(state).put(
        key,
        PointCheckpoint(
            (request,), (), (), (), (), (), WorkCursor("main", 0, 0),
            work_count=1,
        ),
    )
    return state


def test_encode_round_trips_frozen_events_after_guard_pass() -> None:
    registry = builtin_registry.freeze()
    state = GameState(
        game_id="guard-pass",
        phase=GamePhase.NIGHT,
        round_number=1,
        config=GameConfig(role_counts={"wolf-killer-guard": 1}, reveal_on_death=True),
        players={1: PlayerState(1, "wolf-killer-guard", "good")},
        pipeline_version="v2",
        registry_digest=registry.digest,
        spec_versions={"wolf-killer-guard": 1},
        effect_schema_version=1,
    )
    command = ActionCommand(
        action_type="pass",
        target_seat=None,
        reasoning="首夜无明确信息，且女巫首夜很可能使用解药救人；我若盲守同一目标反而可能触发双救穿透，因此选择空守过夜。",
    )
    scheduler = Scheduler(
        registry, ContextProjector(), ActionValidator(), ActionResolver(),
        EffectApplier(), lambda *_args: command,
    )
    result = scheduler.run_point(state, SchedulePoint.NIGHT_ACTION)
    assert [event["event_type"] for event in result.events] == ["GUARD_REASONING"]

    codec = CheckpointCodec(registry)
    document = codec.encode(state, orchestration={"source": "guard-pass"})
    encoded_events = [
        event["event_type"]
        for event in document["pipeline_runtime"]["events"]
    ]
    journal_events = [
        event["event_type"]
        for entry in document["point_journal"]
        for event in entry["checkpoint"]["events"]
    ]
    assert encoded_events == ["GUARD_REASONING"]
    assert journal_events == ["GUARD_REASONING"]

    restored, orchestration = codec.decode(document)
    assert orchestration == {"source": "guard-pass"}
    restored_types = [
        event["event_type"] for event in restored._pipeline_runtime.events
    ]
    journal = point_journal(restored).entries()
    assert restored_types == ["GUARD_REASONING"]
    assert [event["event_type"] for event in journal[0][1].events] == ["GUARD_REASONING"]


def test_checkpoint_round_trip_preserves_all_game_state_fields_and_runtime() -> None:
    registry = builtin_registry.freeze()
    state = _rich_state()
    document = CheckpointCodec(registry).encode(
        state,
        orchestration={
            "execution_position": "vote:collect",
            "last_words_given": [[2, 3]],
            "role_state": {"1": {"last_words_used": False}},
            "conversation_records": [{"scope": "public", "content": "hello"}],
            "model_assignments": [{"config_id": None, "seats": [1, 2]}],
            "random_state": {"seed": 42},
        },
    )
    assert document["checkpoint_version"] == CHECKPOINT_VERSION
    assert set(document["state"]) == {field.name for field in fields(GameState)}

    restored, orchestration = CheckpointCodec(registry).decode(document)
    assert restored is not state
    assert restored.phase is GamePhase.VOTE_CASTING
    assert restored.tiebreak_candidates == {1, 2}
    assert restored.players[1].check_results == [{"seat": 2, "camp": "good"}]
    assert restored.speeches[0].text == "测试发言"
    assert restored.speeches[0].phase == "speech"
    assert restored.votes[0].thinking == "思考"
    assert restored.night_actions[0].target_seat == 2
    assert restored.death_history[0].cause == "wolf_kill"
    assert restored._pipeline_runtime.role_resources == {1: {"gun": 1}}
    assert restored._pipeline_runtime.statuses == {1: {"protected"}}
    assert restored._pipeline_runtime.relations == {1: {("teammate", 2)}}
    assert restored._pipeline_runtime.commits["action-a"].state_digest == "digest-a"
    entries = point_journal(restored).entries()
    assert len(entries) == 1
    restored_request = entries[0][1].issued[0]
    assert restored_request.contract is registry.require("wolf-killer-werewolf").contracts[0]
    assert orchestration["execution_position"] == "vote:collect"
    assert orchestration["random_state"] == {"seed": 42}

    restored.players[1].check_results.append({"seat": 3})
    assert state.players[1].check_results == [{"seat": 2, "camp": "good"}]


def test_checkpoint_json_is_canonical_and_round_trips() -> None:
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    raw = codec.dumps(codec.encode(_rich_state(), orchestration={}))
    assert raw == json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    restored, _ = codec.loads(raw)
    assert restored.game_id == "game-checkpoint"
    assert restored.config.enable_sheriff is False
    assert restored.sheriff_office.badge_destroyed is False


def test_checkpoint_accepts_legacy_config_without_sheriff_fields() -> None:
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    document = codec.encode(_rich_state(), orchestration={})
    document["state"]["config"].pop("enable_sheriff")
    document["state"].pop("sheriff_office")
    restored, _ = codec.decode(document)
    assert restored.config.enable_sheriff is False
    assert restored.sheriff_office.step == ""
    assert restored.sheriff_office.candidates == set()


def test_checkpoint_accepts_legacy_speech_without_phase() -> None:
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    document = codec.encode(_rich_state(), orchestration={})
    document["state"]["speeches"][0].pop("phase")
    restored, _ = codec.decode(document)
    assert restored.speeches[0].phase is None


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda doc: doc.update(checkpoint_version=999), "checkpoint version"),
        (lambda doc: doc["state"].pop("phase"), "state fields"),
        (lambda doc: doc["state"].update(phase="made_up"), "phase"),
        (lambda doc: doc["pipeline_runtime"].update(revision=True), "runtime revision"),
        (lambda doc: doc["point_journal"][0]["key"].update(registry_digest="wrong"), "registry"),
    ],
)
def test_checkpoint_rejects_unknown_or_corrupt_documents(mutate, message) -> None:
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    document = codec.encode(_rich_state(), orchestration={})
    mutate(document)
    with pytest.raises(CheckpointError, match=message):
        codec.decode(document)


def test_checkpoint_rejects_unregistered_contract_reference() -> None:
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    document = codec.encode(_rich_state(), orchestration={})
    document["point_journal"][0]["checkpoint"]["issued"][0]["contract_id"] = "missing"
    with pytest.raises(CheckpointError, match="contract"):
        codec.decode(document)


def test_codec_public_helpers_validate_types_and_round_trip_values(monkeypatch) -> None:
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    state = _rich_state()
    entry = point_journal(state).entries()[0][1]
    request = entry.issued[0]
    commit = state._pipeline_runtime.commits["action-a"]

    assert codec.decode_issued_request(codec.encode_issued_request(request)) == request
    assert codec.decode_commit_result(codec.encode_commit_result(commit)) == commit
    with pytest.raises(TypeError, match="registry"):
        CheckpointCodec(object())
    with pytest.raises(TypeError, match="state"):
        codec.encode(object(), orchestration={})
    with pytest.raises(TypeError, match="IssuedActionRequest"):
        codec.encode_issued_request(object())
    with pytest.raises(TypeError, match="commit"):
        codec.encode_commit_result(object())
    with pytest.raises(CheckpointError, match="invalid checkpoint JSON"):
        codec.loads("{")
    with pytest.raises(CheckpointError, match="invalid checkpoint JSON"):
        codec.loads(None)
    with pytest.raises(CheckpointError, match="invalid JSON value"):
        codec.dumps({"value": object()})

    state._pipeline_runtime = object()
    with pytest.raises(CheckpointError, match="pipeline runtime"):
        codec.encode(state, orchestration={})

    state = _rich_state()
    monkeypatch.setattr(
        checkpoint_codec_module, "_STATE_FIELDS",
        checkpoint_codec_module._STATE_FIELDS | {"future_field"},
    )
    with pytest.raises(CheckpointError, match="state fields"):
        codec.encode(state, orchestration={})


def _mutate_nested(document: dict, *path_and_value) -> None:
    *path, value = path_and_value
    target = document
    for name in path[:-1]:
        target = target[name]
    target[path[-1]] = value


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda doc: doc.update(registry_digest="wrong"), "registry mismatch"),
        (lambda doc: _mutate_nested(doc, "state", "config", "role_counts", {"role": -1}), "role_counts"),
        (lambda doc: _mutate_nested(doc, "state", "config", "reveal_on_death", 1), "reveal_on_death"),
        (lambda doc: _mutate_nested(doc, "state", "config", "enable_sheriff", 1), "enable_sheriff"),
        (lambda doc: _mutate_nested(doc, "state", "sheriff_office", "speech_side", 1), "speech_side"),
        (lambda doc: _mutate_nested(doc, "state", "sheriff_office", "step", 1), "sheriff step"),
        (lambda doc: _mutate_nested(doc, "state", "players", {"bad": {}}), "players seat"),
        (lambda doc: _mutate_nested(doc, "state", "players", {"01": {}}), "players seat"),
        (lambda doc: _mutate_nested(doc, "state", "players", {1: {}}), "players must be an object"),
        (lambda doc: _mutate_nested(doc, "state", "players", "1", "seat_number", 2), "seat does not match"),
        (lambda doc: _mutate_nested(doc, "state", "players", "1", "role", None), "player role"),
        (lambda doc: _mutate_nested(doc, "state", "players", "1", "check_results", {}), "check_results"),
        (lambda doc: _mutate_nested(doc, "state", "is_tiebreak", 1), "is_tiebreak"),
        (lambda doc: _mutate_nested(doc, "state", "voted_seats", [1, 1]), "duplicate voted_seats"),
        (lambda doc: _mutate_nested(doc, "state", "accepted_action_keys", ["a", "a"]), "duplicate accepted_action_keys"),
        (lambda doc: _mutate_nested(doc, "state", "speeches", {}), "speeches"),
        (lambda doc: _mutate_nested(doc, "state", "speeches", 0, "phase", 1), "speech phase"),
        (lambda doc: doc["state"]["speeches"][0].pop("text"), "speech fields"),
        (lambda doc: _mutate_nested(doc, "state", "speeches", 0, "secret", "x"), "speech fields"),
        (lambda doc: _mutate_nested(doc, "pipeline_runtime", "relations", {"1": [["bad"]]}), "invalid relation"),
        (lambda doc: _mutate_nested(doc, "pipeline_runtime", "action_counts", {"window": {}}), "pipeline runtime"),
        (lambda doc: _mutate_nested(doc, "pipeline_runtime", "commits", "action-a", "action_key", ""), "invalid commit"),
        (lambda doc: _mutate_nested(doc, "point_journal", 0, "checkpoint", "issued", 0, "role_id", "missing"), "unknown request role"),
        (lambda doc: _mutate_nested(doc, "point_journal", 0, "checkpoint", "issued", 0, "contract_digest", "wrong"), "contract mismatch"),
        (lambda doc: _mutate_nested(doc, "point_journal", 0, "checkpoint", "issued", 0, "schema_version", 2), "invalid issued request"),
        (lambda doc: _mutate_nested(doc, "point_journal", 0, "key", "point", "missing"), "invalid point checkpoint"),
        (lambda doc: _mutate_nested(doc, "point_journal", 0, "checkpoint", "issued", 0, "actor_seat", 0), "actor_seat"),
        (lambda doc: _mutate_nested(doc, "point_journal", {}), "point_journal"),
        (lambda doc: _mutate_nested(doc, "orchestration", []), "orchestration"),
    ],
)
def test_checkpoint_rejects_each_typed_corruption(mutation, message) -> None:
    registry = builtin_registry.freeze()
    codec = CheckpointCodec(registry)
    document = copy.deepcopy(codec.encode(_rich_state(), orchestration={}))
    mutation(document)
    with pytest.raises(CheckpointError, match=message):
        codec.decode(document)
