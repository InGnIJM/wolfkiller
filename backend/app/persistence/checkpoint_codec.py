"""Explicit, versioned JSON codec for resumable game checkpoints."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields
from typing import Any

from app.core.effect_applier import CommitResult, _Runtime
from app.core.point_journal import (
    PendingEvent, PointCheckpoint, PointKey, WorkCursor, point_journal,
)
from app.models.actions import DeathReport, NightAction, SpeechRecord, VoteAction
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import IssuedActionRequest, SchedulePoint
from app.roles.registry import RegistrySnapshot


CHECKPOINT_VERSION = 1


class CheckpointError(ValueError):
    """The checkpoint is corrupt or incompatible with this runtime."""


_STATE_FIELDS = {field.name for field in fields(GameState)}
_PLAYER_FIELDS = {field.name for field in fields(PlayerState)}
_RUNTIME_FIELDS = {field.name for field in fields(_Runtime)}
_COMMIT_FIELDS = {field.name for field in fields(CommitResult)}


def _plain(value: object) -> object:
    """Clone a JSON value and reject executable or ambiguous Python values."""
    try:
        return json.loads(json.dumps(
            value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise CheckpointError(f"invalid JSON value: {error}") from error


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise CheckpointError(f"{name} must be an object")
    return value


def _exact(value: object, names: set[str], label: str) -> Mapping[str, object]:
    row = _mapping(value, label)
    if set(row) != names:
        raise CheckpointError(f"{label} fields do not match schema")
    return row


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise CheckpointError(f"invalid {label}")
    return value


def _optional_integer(value: object, label: str, *, minimum: int = 0) -> int | None:
    return None if value is None else _integer(value, label, minimum=minimum)


def _string(value: object, label: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) is not str:
        raise CheckpointError(f"invalid {label}")
    return value


def _array(value: object, label: str) -> list[object]:
    if type(value) is not list:
        raise CheckpointError(f"{label} must be an array")
    return value


def _seat_map(value: object, label: str) -> dict[int, object]:
    row = _mapping(value, label)
    result: dict[int, object] = {}
    for key, item in row.items():
        try:
            seat = int(key)
        except ValueError as error:
            raise CheckpointError(f"invalid {label} seat") from error
        if str(seat) != key or seat < 1:
            raise CheckpointError(f"invalid {label} seat")
        result[seat] = item
    return result


def _encode_request(request: IssuedActionRequest) -> dict[str, object]:
    return {
        "actor_seat": request.actor_seat,
        "role_id": request.role_id,
        "contract_id": request.contract.contract_id,
        "contract_version": request.contract.schema_version,
        "contract_digest": request.contract.stable_digest(),
        "context_revision": request.context_revision,
        "round_number": request.round_number,
        "phase": request.phase,
        "window_id": request.window_id,
        "action_key": request.action_key,
        "schema_version": request.schema_version,
    }


def _encode_commit(commit: CommitResult) -> dict[str, object]:
    return {
        "action_key": commit.action_key,
        "effect_ids": list(commit.effect_ids),
        "revision": commit.revision,
        "events": _plain(commit.events),
        "state_digest": commit.state_digest,
        "schema_version": commit.schema_version,
    }


class CheckpointCodec:
    def __init__(self, registry: RegistrySnapshot) -> None:
        if type(registry) is not RegistrySnapshot:
            raise TypeError("registry must be a RegistrySnapshot")
        self._registry = registry

    def encode(
        self, state: GameState, *, orchestration: Mapping[str, object]
    ) -> dict[str, object]:
        if type(state) is not GameState:
            raise TypeError("state must be GameState")
        runtime = getattr(state, "_pipeline_runtime", _Runtime())
        if type(runtime) is not _Runtime:
            raise CheckpointError("invalid pipeline runtime")
        state_doc = {
            "game_id": state.game_id,
            "phase": state.phase.value,
            "round_number": state.round_number,
            "vote_round": state.vote_round,
            "is_tiebreak": state.is_tiebreak,
            "tiebreak_candidates": sorted(state.tiebreak_candidates),
            "supplemental_speakers": sorted(state.supplemental_speakers),
            "voted_seats": sorted(state.voted_seats),
            "accepted_action_keys": sorted(state.accepted_action_keys),
            "config": {
                "role_counts": dict(sorted(state.config.role_counts.items())),
                "reveal_on_death": state.config.reveal_on_death,
            },
            "players": {
                str(seat): {
                    "seat_number": player.seat_number, "role": player.role,
                    "camp": player.camp, "is_alive": player.is_alive,
                    "has_antidote": player.has_antidote,
                    "has_poison": player.has_poison, "has_gun": player.has_gun,
                    "is_sheriff": player.is_sheriff,
                    "check_results": _plain(player.check_results),
                    "revealed_role": player.revealed_role,
                }
                for seat, player in sorted(state.players.items())
            },
            "sheriff": state.sheriff,
            "speeches": [item.to_dict() for item in state.speeches],
            "votes": [item.to_dict() for item in state.votes],
            "night_actions": [item.to_dict() for item in state.night_actions],
            "death_history": [item.to_dict() for item in state.death_history],
            "win_result": _plain(state.win_result),
            "last_wolf_kill_target": state.last_wolf_kill_target,
            "sheriff_election_complete": state.sheriff_election_complete,
            "speaking_order": list(state.speaking_order),
            "current_speaker": state.current_speaker,
            "state_revision": state.state_revision,
            "pipeline_version": state.pipeline_version,
            "registry_digest": state.registry_digest,
            "spec_versions": dict(sorted(state.spec_versions.items())),
            "effect_schema_version": state.effect_schema_version,
            "last_consistent_checkpoint": state.last_consistent_checkpoint,
        }
        if set(state_doc) != _STATE_FIELDS:  # catches model additions at development time
            raise CheckpointError("state fields do not match codec schema")
        runtime_doc = {
            "revision": runtime.revision,
            "role_resources": {str(k): v for k, v in sorted(runtime.role_resources.items())},
            "private_data": {str(k): v for k, v in sorted(runtime.private_data.items())},
            "statuses": {str(k): sorted(v) for k, v in sorted(runtime.statuses.items())},
            "relations": {str(k): [list(item) for item in sorted(v)] for k, v in sorted(runtime.relations.items())},
            "private_facts": {str(k): v for k, v in sorted(runtime.private_facts.items())},
            "pending_damage": list(runtime.pending_damage),
            "pending_protection": list(runtime.pending_protection),
            "events": list(runtime.events),
            "commits": {key: _encode_commit(value) for key, value in sorted(runtime.commits.items())},
            "resource_setup_digest": runtime.resource_setup_digest,
            "action_counts": runtime.action_counts,
            "vote_receipts": runtime.vote_receipts,
        }
        journal_doc = []
        for key, checkpoint in point_journal(state).entries():
            journal_doc.append({
                "key": {
                    "game_id": key.game_id, "round_number": key.round_number,
                    "phase": key.phase, "point": key.point.value,
                    "registry_digest": key.registry_digest,
                },
                "checkpoint": {
                    "issued": [_encode_request(item) for item in checkpoint.issued],
                    "actual": [_encode_request(item) for item in checkpoint.actual],
                    "commits": [_encode_commit(item) for item in checkpoint.commits],
                    "events": list(checkpoint.events), "faults": list(checkpoint.faults),
                    "pending": [dict(item) for item in checkpoint.pending],
                    "cursor": {
                        "kind": checkpoint.cursor.kind, "index": checkpoint.cursor.index,
                        "subindex": checkpoint.cursor.subindex,
                    },
                    "complete_result": checkpoint.complete_result,
                    "work_count": checkpoint.work_count,
                },
            })
        return _plain({
            "checkpoint_version": CHECKPOINT_VERSION,
            "registry_digest": self._registry.digest,
            "state": state_doc,
            "pipeline_runtime": runtime_doc,
            "point_journal": journal_doc,
            "orchestration": dict(orchestration),
        })  # type: ignore[return-value]

    def dumps(self, document: Mapping[str, object]) -> str:
        value = _plain(document)
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":"))

    def encode_issued_request(self, request: IssuedActionRequest) -> dict[str, object]:
        if type(request) is not IssuedActionRequest:
            raise TypeError("request must be an IssuedActionRequest")
        return _encode_request(request)

    def decode_issued_request(self, value: object) -> IssuedActionRequest:
        return self._decode_request(value)

    def encode_commit_result(self, commit: CommitResult) -> dict[str, object]:
        if type(commit) is not CommitResult:
            raise TypeError("commit must be a CommitResult")
        return _encode_commit(commit)

    def decode_commit_result(self, value: object) -> CommitResult:
        return self._decode_commit(value)

    def loads(self, raw: str) -> tuple[GameState, dict[str, object]]:
        try:
            document = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise CheckpointError("invalid checkpoint JSON") from error
        return self.decode(document)

    def decode(self, document: Mapping[str, object]) -> tuple[GameState, dict[str, object]]:
        root = _exact(document, {
            "checkpoint_version", "registry_digest", "state",
            "pipeline_runtime", "point_journal", "orchestration",
        }, "checkpoint")
        if root["checkpoint_version"] != CHECKPOINT_VERSION:
            raise CheckpointError("unsupported checkpoint version")
        if root["registry_digest"] != self._registry.digest:
            raise CheckpointError("registry mismatch")
        state_row = _exact(root["state"], _STATE_FIELDS, "state")
        state = self._decode_state(state_row)
        runtime = self._decode_runtime(root["pipeline_runtime"])
        setattr(state, "_pipeline_runtime", runtime)
        entries = tuple(self._decode_journal_entry(item) for item in _array(root["point_journal"], "point_journal"))
        point_journal(state).restore_entries(entries)
        orchestration = _mapping(root["orchestration"], "orchestration")
        return state, _plain(orchestration)  # type: ignore[return-value]

    def _decode_state(self, row: Mapping[str, object]) -> GameState:
        try:
            phase = GamePhase(row["phase"])
        except (TypeError, ValueError) as error:
            raise CheckpointError("invalid phase") from error
        config = _exact(row["config"], {"role_counts", "reveal_on_death"}, "config")
        role_counts = _mapping(config["role_counts"], "role_counts")
        if any(type(k) is not str or type(v) is not int or v < 0 for k, v in role_counts.items()):
            raise CheckpointError("invalid role_counts")
        if type(config["reveal_on_death"]) is not bool:
            raise CheckpointError("invalid reveal_on_death")
        players: dict[int, PlayerState] = {}
        for seat, raw in _seat_map(row["players"], "players").items():
            item = _exact(raw, _PLAYER_FIELDS, "player")
            check_results = _array(item["check_results"], "check_results")
            player = PlayerState(
                seat_number=_integer(item["seat_number"], "player seat", minimum=1),
                role=_string(item["role"], "player role"),
                camp=_string(item["camp"], "player camp"),
                is_alive=self._bool(item["is_alive"], "is_alive"),
                has_antidote=self._bool(item["has_antidote"], "has_antidote"),
                has_poison=self._bool(item["has_poison"], "has_poison"),
                has_gun=self._bool(item["has_gun"], "has_gun"),
                is_sheriff=self._bool(item["is_sheriff"], "is_sheriff"),
                check_results=_plain(check_results),
                revealed_role=_string(item["revealed_role"], "revealed_role", optional=True),
            )
            if player.seat_number != seat:
                raise CheckpointError("player seat does not match map key")
            players[seat] = player
        result = GameState(
            game_id=_string(row["game_id"], "game_id"), phase=phase,
            round_number=_integer(row["round_number"], "round_number"),
            vote_round=_integer(row["vote_round"], "vote_round", minimum=1),
            is_tiebreak=self._bool(row["is_tiebreak"], "is_tiebreak"),
            tiebreak_candidates=self._int_set(row["tiebreak_candidates"], "tiebreak_candidates"),
            supplemental_speakers=self._int_set(row["supplemental_speakers"], "supplemental_speakers"),
            voted_seats=self._int_set(row["voted_seats"], "voted_seats"),
            accepted_action_keys=self._str_set(row["accepted_action_keys"], "accepted_action_keys"),
            config=GameConfig(role_counts=dict(role_counts), reveal_on_death=config["reveal_on_death"]),
            players=players,
            sheriff=_optional_integer(row["sheriff"], "sheriff", minimum=1),
            speeches=[self._speech(item) for item in _array(row["speeches"], "speeches")],
            votes=[self._vote(item) for item in _array(row["votes"], "votes")],
            night_actions=[self._night_action(item) for item in _array(row["night_actions"], "night_actions")],
            death_history=[self._death(item) for item in _array(row["death_history"], "death_history")],
            win_result=_plain(row["win_result"]),
            last_wolf_kill_target=_optional_integer(row["last_wolf_kill_target"], "last_wolf_kill_target", minimum=1),
            sheriff_election_complete=self._bool(row["sheriff_election_complete"], "sheriff_election_complete"),
            speaking_order=self._int_list(row["speaking_order"], "speaking_order"),
            current_speaker=_optional_integer(row["current_speaker"], "current_speaker", minimum=1),
            state_revision=_integer(row["state_revision"], "state_revision"),
            pipeline_version=_string(row["pipeline_version"], "pipeline_version"),
            registry_digest=_string(row["registry_digest"], "registry_digest"),
            spec_versions=self._str_int_map(row["spec_versions"], "spec_versions"),
            effect_schema_version=_integer(row["effect_schema_version"], "effect_schema_version"),
            last_consistent_checkpoint=_string(row["last_consistent_checkpoint"], "last_consistent_checkpoint", optional=True),
        )
        return result

    def _decode_runtime(self, value: object) -> _Runtime:
        row = _exact(value, _RUNTIME_FIELDS, "pipeline runtime")
        revision = _integer(row["revision"], "runtime revision")
        try:
            runtime = _Runtime(
                revision=revision,
                role_resources={k: self._str_int_map(v, "role resources") for k, v in _seat_map(row["role_resources"], "role_resources").items()},
                private_data={k: _plain(_mapping(v, "private data")) for k, v in _seat_map(row["private_data"], "private_data").items()},
                statuses={k: self._str_set(v, "statuses") for k, v in _seat_map(row["statuses"], "statuses").items()},
                relations={k: self._relations(v) for k, v in _seat_map(row["relations"], "relations").items()},
                private_facts={k: _plain(_array(v, "private facts")) for k, v in _seat_map(row["private_facts"], "private_facts").items()},
                pending_damage=tuple(_plain(_array(row["pending_damage"], "pending_damage"))),
                pending_protection=tuple(_plain(_array(row["pending_protection"], "pending_protection"))),
                events=tuple(_plain(_array(row["events"], "runtime events"))),
                commits={key: self._decode_commit(item) for key, item in _mapping(row["commits"], "commits").items()},
                resource_setup_digest=_string(row["resource_setup_digest"], "resource_setup_digest", optional=True),
                action_counts={key: self._str_int_map(item, "action counts") for key, item in _mapping(row["action_counts"], "action_counts").items()},
                vote_receipts={key: _plain(_mapping(item, "vote receipt")) for key, item in _mapping(row["vote_receipts"], "vote_receipts").items()},
            )
            return runtime.clone()
        except (TypeError, ValueError) as error:
            raise CheckpointError(f"invalid pipeline runtime: {error}") from error

    def _decode_commit(self, value: object) -> CommitResult:
        row = _exact(value, _COMMIT_FIELDS, "commit")
        try:
            return CommitResult(
                _string(row["action_key"], "action_key"),
                tuple(self._str_list(row["effect_ids"], "effect_ids")),
                _integer(row["revision"], "commit revision"),
                tuple(_plain(_array(row["events"], "commit events"))),
                _string(row["state_digest"], "state_digest"),
                _integer(row["schema_version"], "commit schema_version", minimum=1),
            )
        except (TypeError, ValueError) as error:
            raise CheckpointError(f"invalid commit: {error}") from error

    def _decode_request(self, value: object) -> IssuedActionRequest:
        names = {"actor_seat", "role_id", "contract_id", "contract_version", "contract_digest", "context_revision", "round_number", "phase", "window_id", "action_key", "schema_version"}
        row = _exact(value, names, "issued request")
        role_id = _string(row["role_id"], "role_id")
        try:
            spec = self._registry.require(role_id)
        except ValueError as error:
            raise CheckpointError("unknown request role or contract") from error
        contract_id = _string(row["contract_id"], "contract_id")
        matches = [item for item in spec.contracts if item.contract_id == contract_id]
        if len(matches) != 1:
            raise CheckpointError("unknown request contract")
        contract = matches[0]
        if (row["contract_version"] != contract.schema_version or
                row["contract_digest"] != contract.stable_digest()):
            raise CheckpointError("request contract mismatch")
        try:
            return IssuedActionRequest(
                _integer(row["actor_seat"], "actor_seat", minimum=1), role_id, contract,
                _integer(row["context_revision"], "context_revision"),
                _integer(row["round_number"], "round_number"),
                _string(row["phase"], "phase"), _string(row["window_id"], "window_id"),
                _string(row["action_key"], "action_key"),
                _integer(row["schema_version"], "request schema_version", minimum=1),
            )
        except (TypeError, ValueError) as error:
            raise CheckpointError(f"invalid issued request: {error}") from error

    def _decode_journal_entry(self, value: object) -> tuple[PointKey, PointCheckpoint]:
        row = _exact(value, {"key", "checkpoint"}, "journal entry")
        key_row = _exact(row["key"], {"game_id", "round_number", "phase", "point", "registry_digest"}, "journal key")
        if key_row["registry_digest"] != self._registry.digest:
            raise CheckpointError("journal registry mismatch")
        try:
            key = PointKey(
                _string(key_row["game_id"], "game_id"),
                _integer(key_row["round_number"], "round_number"),
                _string(key_row["phase"], "phase"), SchedulePoint(key_row["point"]),
                _string(key_row["registry_digest"], "registry_digest"),
            )
            cp = _exact(row["checkpoint"], {"issued", "actual", "commits", "events", "faults", "pending", "cursor", "complete_result", "work_count"}, "point checkpoint")
            cursor = _exact(cp["cursor"], {"kind", "index", "subindex"}, "work cursor")
            checkpoint = PointCheckpoint(
                tuple(self._decode_request(item) for item in _array(cp["issued"], "issued")),
                tuple(self._decode_request(item) for item in _array(cp["actual"], "actual")),
                tuple(self._decode_commit(item) for item in _array(cp["commits"], "commits")),
                tuple(_plain(_array(cp["events"], "events"))),
                tuple(_plain(_array(cp["faults"], "faults"))),
                tuple(PendingEvent(**dict(_mapping(item, "pending event"))) for item in _array(cp["pending"], "pending")),
                WorkCursor(_string(cursor["kind"], "cursor kind"), _integer(cursor["index"], "cursor index"), _integer(cursor["subindex"], "cursor subindex")),
                None if cp["complete_result"] is None else _plain(_mapping(cp["complete_result"], "complete_result")),
                _integer(cp["work_count"], "work_count"),
            )
            return key, checkpoint
        except (TypeError, ValueError) as error:
            if isinstance(error, CheckpointError):
                raise
            raise CheckpointError(f"invalid point checkpoint: {error}") from error

    @staticmethod
    def _bool(value: object, label: str) -> bool:
        if type(value) is not bool:
            raise CheckpointError(f"invalid {label}")
        return value

    @staticmethod
    def _int_list(value: object, label: str) -> list[int]:
        return [_integer(item, label, minimum=1) for item in _array(value, label)]

    @classmethod
    def _int_set(cls, value: object, label: str) -> set[int]:
        result = cls._int_list(value, label)
        if len(result) != len(set(result)):
            raise CheckpointError(f"duplicate {label}")
        return set(result)

    @staticmethod
    def _str_list(value: object, label: str) -> list[str]:
        return [_string(item, label) for item in _array(value, label)]

    @classmethod
    def _str_set(cls, value: object, label: str) -> set[str]:
        result = cls._str_list(value, label)
        if len(result) != len(set(result)):
            raise CheckpointError(f"duplicate {label}")
        return set(result)

    @staticmethod
    def _str_int_map(value: object, label: str) -> dict[str, int]:
        row = _mapping(value, label)
        return {key: _integer(item, label) for key, item in row.items()}

    @staticmethod
    def _relations(value: object) -> set[tuple[str, int]]:
        result: set[tuple[str, int]] = set()
        for item in _array(value, "relations"):
            pair = _array(item, "relation")
            if len(pair) != 2:
                raise CheckpointError("invalid relation")
            result.add((_string(pair[0], "relation"), _integer(pair[1], "relation seat", minimum=1)))
        return result

    @staticmethod
    def _speech(value: object) -> SpeechRecord:
        row = _exact(value, {"player_seat", "text", "round_number"}, "speech")
        return SpeechRecord(_integer(row["player_seat"], "speech seat", minimum=1), _string(row["text"], "speech text"), _integer(row["round_number"], "speech round"))

    @staticmethod
    def _vote(value: object) -> VoteAction:
        row = _exact(value, {"voter_seat", "target_seat", "reasoning", "thinking"}, "vote")
        return VoteAction(_integer(row["voter_seat"], "voter seat", minimum=1), _optional_integer(row["target_seat"], "vote target", minimum=1), _string(row["reasoning"], "vote reasoning"), _string(row["thinking"], "vote thinking"))

    @staticmethod
    def _night_action(value: object) -> NightAction:
        row = _exact(value, {"player_seat", "action_type", "target_seat", "reasoning", "thinking"}, "night action")
        return NightAction(_integer(row["player_seat"], "action seat", minimum=1), _string(row["action_type"], "action type"), _optional_integer(row["target_seat"], "action target", minimum=1), _string(row["reasoning"], "action reasoning"), _string(row["thinking"], "action thinking"))

    @staticmethod
    def _death(value: object) -> DeathReport:
        row = _exact(value, {"player_seat", "cause", "round_number"}, "death")
        return DeathReport(_integer(row["player_seat"], "death seat", minimum=1), _string(row["cause"], "death cause"), _integer(row["round_number"], "death round"))
