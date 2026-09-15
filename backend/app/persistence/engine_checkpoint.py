"""Explicit codec for GameEngine orchestration that is not part of GameState."""

from __future__ import annotations

import json
from collections.abc import Mapping

from app.config import PipelineMode
from app.core.effect_applier import CommitResult
from app.core.night_flow import WolfVote
from app.core.role_pipeline import PipelineDiff, PipelineResult
from app.core.scheduler import PointResult
from app.models.conversation import Conversation, ConversationScope
from app.persistence.checkpoint_codec import CheckpointCodec, CheckpointError, _json_value


def _plain(value: object) -> object:
    try:
        return json.loads(json.dumps(
            _json_value(value), ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ))
    except (TypeError, ValueError) as error:
        raise CheckpointError("invalid engine orchestration JSON") from error


def _point_result(codec: CheckpointCodec, value: PointResult) -> dict[str, object]:
    return {
        "requests": [codec.encode_issued_request(item) for item in value.requests],
        "commits": [codec.encode_commit_result(item) for item in value.commits],
        "events": list(value.events), "state_digest": value.state_digest,
        "faults": list(value.faults),
    }


def _decode_point_result(codec: CheckpointCodec, value: object) -> PointResult:
    if not isinstance(value, Mapping) or set(value) != {
        "requests", "commits", "events", "state_digest", "faults",
    }:
        raise CheckpointError("invalid point result")
    try:
        return PointResult(
            tuple(codec.decode_issued_request(item) for item in value["requests"]),
            tuple(codec.decode_commit_result(item) for item in value["commits"]),
            tuple(value["events"]), value["state_digest"], tuple(value["faults"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CheckpointError("invalid point result") from error


def _pipeline_result(value: PipelineResult) -> dict[str, object]:
    return {
        "accepted_actions": list(value.accepted_actions),
        "effects": list(value.effects), "state_digest": value.state_digest,
        "public_events": list(value.public_events), "mode": value.mode.value,
        "diff": None if value.diff is None else {
            "matched": value.diff.matched, "mismatches": list(value.diff.mismatches),
        },
    }


def _decode_pipeline_result(value: object) -> PipelineResult:
    if not isinstance(value, Mapping) or set(value) != {
        "accepted_actions", "effects", "state_digest", "public_events", "mode", "diff",
    }:
        raise CheckpointError("invalid pipeline result")
    diff = value["diff"]
    try:
        if diff is not None:
            if not isinstance(diff, Mapping) or set(diff) != {"matched", "mismatches"}:
                raise CheckpointError("invalid pipeline diff")
            diff = PipelineDiff(diff["matched"], tuple(diff["mismatches"]))
        return PipelineResult(
            tuple(value["accepted_actions"]), tuple(value["effects"]),
            value["state_digest"], tuple(value["public_events"]),
            PipelineMode(value["mode"]), diff,
        )
    except (TypeError, ValueError) as error:
        raise CheckpointError("invalid pipeline result") from error


def encode_engine_orchestration(
    engine: object, codec: CheckpointCodec, *,
    model_assignments: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    from app.core.game_engine import _PendingNightBatch, _PendingNightCompletion

    batch = getattr(engine, "_pending_night_batch", None)
    completion = getattr(engine, "_pending_night_completion", None)
    batch_doc = None
    if batch is not None:
        if type(batch) is not _PendingNightBatch:
            raise CheckpointError("invalid pending night batch")
        batch_doc = {
            "round_number": batch.round_number, "stage": batch.stage,
            "discussion_history": list(batch.discussion_history),
            "wolf_votes": [
                {"seat": item.seat, "action_type": item.action_type,
                 "target_seat": item.target_seat, "reasoning": item.reasoning}
                for item in batch.wolf_votes
            ],
            "raw_results": [_point_result(codec, item) for item in batch.raw_results],
            "discussion_leads": [list(item) for item in batch.discussion_leads],
            "wolf_random_hint": batch.wolf_random_hint,
        }
    completion_doc = None
    if completion is not None:
        if type(completion) is not _PendingNightCompletion:
            raise CheckpointError("invalid pending night completion")
        completion_doc = {
            "result": _pipeline_result(completion.result),
            "deaths": None if completion.deaths is None else [
                {"seat": item.seat, "cause": item.cause,
                 "round_number": item.round_number} for item in completion.deaths
            ],
            "event_cursor": completion.event_cursor, "stage": completion.stage,
            "win_result": None if completion.win_result is None else {
                "winning_camp": completion.win_result.winning_camp,
                "reason": completion.win_result.reason,
            },
            "win_checked": completion.win_checked,
            "win_invalid": completion.win_invalid,
        }
    random_state = getattr(engine, "_rng").getstate()
    return _plain({
        "execution_position": getattr(engine, "state").phase.value,
        "checkpoint_counter": getattr(engine, "_checkpoint_counter"),
        "pending_night_batch": batch_doc,
        "pending_night_completion": completion_doc,
        "last_words_given": [list(item) for item in sorted(getattr(engine, "_last_words_given"))],
        "active_vote_window_id": getattr(engine, "_active_vote_window_id"),
        "vote_service": getattr(engine, "_vote_service").checkpoint(),
        "conversation_records": [item.to_dict() for item in getattr(engine, "conversation_log").records],
        "role_state": {
            str(seat): {"last_words_used": bool(getattr(role, "_last_words_used", False))}
            for seat, role in sorted(getattr(engine, "roles").items())
        },
        "model_assignments": model_assignments or [],
        "random_state": {
            "version": random_state[0], "internal": list(random_state[1]),
            "gauss_next": random_state[2],
        },
    })  # type: ignore[return-value]


def restore_engine_orchestration(
    engine: object, codec: CheckpointCodec, value: Mapping[str, object],
) -> None:
    from app.core.game_engine import (
        _PendingDeath, _PendingNightBatch, _PendingNightCompletion, _PendingWin,
    )

    names = {
        "execution_position", "checkpoint_counter", "pending_night_batch", "pending_night_completion",
        "last_words_given", "active_vote_window_id", "vote_service",
        "conversation_records", "role_state", "model_assignments", "random_state",
    }
    if not isinstance(value, Mapping) or set(value) != names:
        raise CheckpointError("invalid engine orchestration fields")
    batch_doc = value["pending_night_batch"]
    batch = None
    if batch_doc is not None:
        if not isinstance(batch_doc, Mapping) or set(batch_doc) != {
            "round_number", "stage", "discussion_history", "wolf_votes",
            "raw_results", "discussion_leads", "wolf_random_hint",
        }:
            raise CheckpointError("invalid pending night batch")
        try:
            batch = _PendingNightBatch(
                batch_doc["round_number"], batch_doc["stage"],
                tuple(batch_doc["discussion_history"]),
                tuple(WolfVote(**dict(item)) for item in batch_doc["wolf_votes"]),
                tuple(_decode_point_result(codec, item) for item in batch_doc["raw_results"]),
                tuple(tuple(item) for item in batch_doc["discussion_leads"]),
                batch_doc["wolf_random_hint"],
            )
        except (TypeError, ValueError) as error:
            raise CheckpointError("invalid pending night batch") from error
    completion_doc = value["pending_night_completion"]
    completion = None
    if completion_doc is not None:
        if not isinstance(completion_doc, Mapping) or set(completion_doc) != {
            "result", "deaths", "event_cursor", "stage", "win_result",
            "win_checked", "win_invalid",
        }:
            raise CheckpointError("invalid pending night completion")
        try:
            deaths = completion_doc["deaths"]
            win = completion_doc["win_result"]
            completion = _PendingNightCompletion(
                _decode_pipeline_result(completion_doc["result"]),
                None if deaths is None else tuple(_PendingDeath(**dict(item)) for item in deaths),
                completion_doc["event_cursor"], completion_doc["stage"],
                None if win is None else _PendingWin(**dict(win)),
                completion_doc["win_checked"], completion_doc["win_invalid"],
            )
        except (TypeError, ValueError) as error:
            raise CheckpointError("invalid pending night completion") from error
    conversations = []
    try:
        for item in value["conversation_records"]:
            row = dict(item)
            row["scope"] = ConversationScope(row["scope"])
            conversations.append(Conversation(**row))
        last_words = {tuple(item) for item in value["last_words_given"]}
        if any(len(item) not in {2, 3} or any(
            type(part) is not int for part in item[:2]
        ) or len(item) == 3 and type(item[2]) is not str for item in last_words):
            raise ValueError
        active = value["active_vote_window_id"]
        if active is not None and type(active) is not str:
            raise ValueError
        role_state = value["role_state"]
        if not isinstance(role_state, Mapping):
            raise ValueError
        random_doc = value["random_state"]
        if not isinstance(random_doc, Mapping) or set(random_doc) != {"version", "internal", "gauss_next"}:
            raise ValueError
        random_state = (
            random_doc["version"], tuple(random_doc["internal"]),
            random_doc["gauss_next"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CheckpointError("invalid engine orchestration") from error
    getattr(engine, "_vote_service").restore_checkpoint(value["vote_service"])
    getattr(engine, "conversation_log").records = conversations
    for seat, role in getattr(engine, "roles").items():
        row = role_state.get(str(seat))
        if isinstance(row, Mapping) and type(row.get("last_words_used")) is bool:
            setattr(role, "_last_words_used", row["last_words_used"])
    getattr(engine, "_rng").setstate(random_state)
    setattr(engine, "_pending_night_batch", batch)
    setattr(engine, "_pending_night_completion", completion)
    setattr(engine, "_last_words_given", last_words)
    setattr(engine, "_active_vote_window_id", active)
    counter = value["checkpoint_counter"]
    if type(counter) is not int or counter < 0:
        raise CheckpointError("invalid engine checkpoint counter")
    setattr(engine, "_checkpoint_counter", counter)
