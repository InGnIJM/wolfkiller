"""What a degraded action looks like to the audience and to the seat itself.

Three consumers read the same payload, and after the fix each one must treat it
the same way the other two do:

* the **god view** keeps the line, marked ``degraded`` so it can say "the system
  acted" instead of "the player decided";
* the **seat's own history** drops it, because a placeholder is not a memory and
  the next prompt would otherwise show the model a decision it never made;
* the **old-archive replay** still accepts rows written before the key existed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.api.routes import game_routes
from app.core.conversation_log import ConversationLog
from app.core.game_engine import GameEngine
from app.models.game import GamePhase, GameState, PlayerState
from app.models.pipeline import DEGRADED_REASONING, DEGRADED_THOUGHT
from app.services.audience_projector import AudienceProjector
from app.services.game_service import GameService


class TestTheFlagSurvivesTheDomainCommit:
    """The last hop before the database.

    A degraded payload is only useful if it is still degraded after the durable
    step that writes ``audience_events`` and after the projector's field
    whitelist — both sit between the hook and the frontend.
    """

    def _events(self) -> list[dict[str, object]]:
        pipeline_event = {
            "event_type": "GUARD_REASONING",
            "payload": {
                "seat": 8, "action_type": "pass", "target_seat": None,
                "reasoning": DEGRADED_REASONING, "thought": DEGRADED_THOUGHT,
                "degraded": True,
            },
            "visibility": ("PUBLIC",),
        }
        state = GameState("commit", phase=GamePhase.NIGHT, round_number=3)
        engine = SimpleNamespace(
            game_id="commit", state=state,
            _pending_night_batch=SimpleNamespace(
                raw_results=(SimpleNamespace(events=(pipeline_event,)),),
            ),
        )
        return GameService._checkpoint_domain_events(engine, "00000001:night_point:3")

    def test_the_domain_event_keeps_the_flag(self) -> None:
        events = self._events()

        assert events[0]["event_type"] == "GUARD_REASONING"
        assert events[0]["payload"]["degraded"] is True

    def test_the_projected_payload_keeps_the_flag(self) -> None:
        projected = AudienceProjector().project_events("commit", self._events())

        assert projected[0]["event_type"] == "night_thought"
        assert projected[0]["payload"]["degraded"] is True


class _Logger:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    def log_audience_action(self, *args) -> None:
        self.rows.append(args)


def _engine() -> tuple[SimpleNamespace, _Logger]:
    logger = _Logger()
    state = GameState(
        "degraded-audience", phase=GamePhase.NIGHT, round_number=2,
        players={8: PlayerState(8, "wolf-killer-witch", "good", is_alive=True)},
    )
    engine = SimpleNamespace(
        game_id="degraded-audience", state=state, game_logger=logger,
        conversation_log=ConversationLog(),
    )
    return engine, logger


def _pair(*, degraded: bool | None) -> tuple[str, dict[str, object]]:
    payload: dict[str, object] = {
        "seat": 8, "action_type": "pass", "target_seat": None,
        "reasoning": DEGRADED_REASONING, "thought": DEGRADED_THOUGHT,
    }
    if degraded is not None:
        payload["degraded"] = degraded
    return ("WITCH_REASONING", payload)


class TestTheSeatDoesNotRememberAPlaceholder:
    def test_a_degraded_thought_never_enters_the_private_history(self) -> None:
        engine, logger = _engine()

        GameEngine._log_audience_pairs(engine, (_pair(degraded=True),), "night")

        # The god view still sees it …
        assert len(logger.rows) == 1
        # … but the seat's own memory stays clean.
        assert engine.conversation_log.get_thoughts_for_seat(8) == []

    def test_a_real_thought_is_still_recorded(self) -> None:
        engine, logger = _engine()
        payload = {
            "seat": 8, "action_type": "save", "target_seat": 3,
            "reasoning": "救他", "thought": "决定使用解药救 3 号玩家：救他",
            "degraded": False,
        }

        GameEngine._log_audience_pairs(engine, (("WITCH_REASONING", payload),), "night")

        thoughts = engine.conversation_log.get_thoughts_for_seat(8)
        assert [thought.content for thought in thoughts] == ["决定使用解药救 3 号玩家：救他"]

    def test_a_legacy_payload_without_the_key_still_records(self) -> None:
        """Old archives and old in-flight games have no ``degraded`` key."""
        engine, _ = _engine()

        GameEngine._log_audience_pairs(engine, (_pair(degraded=None),), "night")

        assert len(engine.conversation_log.get_thoughts_for_seat(8)) == 1


class TestTheGodViewCanTellWhoActed:
    def _project(self, payload: dict[str, object]) -> dict[str, object]:
        projected = AudienceProjector().project_events("degraded-audience", [{
            "event_id": "e1", "event_type": "WITCH_REASONING",
            "visibility": ["PUBLIC"], "payload": {**payload, "round_number": 2},
        }])
        assert len(projected) == 1
        return projected[0]["payload"]

    def test_the_flag_reaches_the_projected_payload(self) -> None:
        payload = {
            "seat": 8, "action_type": "pass", "target_seat": None,
            "reasoning": DEGRADED_REASONING, "thought": DEGRADED_THOUGHT,
            "degraded": True, "round_number": 2,
        }

        assert self._project(payload)["degraded"] is True

    def test_a_legacy_row_projects_without_the_key(self) -> None:
        payload = {
            "seat": 8, "action_type": "pass", "target_seat": None,
            "reasoning": "先看", "thought": "决定今晚不使用药水：先看",
            "round_number": 2,
        }

        assert "degraded" not in self._project(payload)

    def test_a_wolf_ballot_carries_the_flag_too(self) -> None:
        projected = AudienceProjector().project_events("degraded-audience", [{
            "event_id": "e2", "event_type": "WOLF_VOTE", "visibility": ["PUBLIC"],
            "payload": {"seat": 1, "target_seat": None, "reasoning": DEGRADED_REASONING,
                        "degraded": True, "round_number": 2},
        }])

        assert projected[0]["payload"]["degraded"] is True


class TestOldArchiveReplay:
    """The replay path rebuilds events from ``game.log`` rows, not from the DB."""

    def _record(self, event_type: str, payload: dict[str, object]) -> dict[str, object]:
        return {
            "timestamp": "2026-01-01T00:00:00Z", "operation": "audience_action",
            "round": 2, "phase": "night", "data": {"event_type": event_type, "payload": payload},
        }

    def _reasoning_payload(self, *, degraded: bool | None) -> dict[str, object]:
        payload: dict[str, object] = {
            "seat": 8, "action_type": "pass", "target_seat": None,
            "reasoning": DEGRADED_REASONING, "thought": DEGRADED_THOUGHT,
        }
        if degraded is not None:
            payload["degraded"] = degraded
        return payload

    def test_a_degraded_row_replays_with_the_flag(self) -> None:
        events = game_routes._public_operation_events(
            self._record("WITCH_REASONING", self._reasoning_payload(degraded=True))
        )

        assert events[0]["event_type"] == "night_thought"
        assert events[0]["payload"]["degraded"] is True

    def test_a_row_written_before_the_key_still_replays(self) -> None:
        events = game_routes._public_operation_events(
            self._record("WITCH_REASONING", self._reasoning_payload(degraded=None))
        )

        assert events[0]["event_type"] == "night_thought"
        assert "degraded" not in events[0]["payload"]

    def test_a_wolf_ballot_row_replays_with_the_flag(self) -> None:
        events = game_routes._public_operation_events(self._record("WOLF_VOTE", {
            "seat": 1, "target_seat": None, "reasoning": DEGRADED_REASONING, "degraded": True,
        }))

        assert events[0]["event_type"] == "wolf_vote"
        assert events[0]["payload"]["degraded"] is True

    def test_a_wolf_ballot_row_without_the_key_still_replays(self) -> None:
        events = game_routes._public_operation_events(self._record("WOLF_VOTE", {
            "seat": 1, "target_seat": 3, "reasoning": "刀他",
        }))

        assert events[0]["payload"]["reasoning"] == "刀他"
        assert "degraded" not in events[0]["payload"]

    def test_a_foreign_key_is_still_dropped(self) -> None:
        """The relaxed field set must not become a free-for-all."""
        payload = self._reasoning_payload(degraded=None)
        payload["unexpected"] = "surprise"

        assert game_routes._public_operation_events(
            self._record("WITCH_REASONING", payload)
        ) == []

    def test_a_json_round_trip_keeps_the_flag(self) -> None:
        """The row as it is actually stored, not as the test built it."""
        row = self._record("WITCH_REASONING", self._reasoning_payload(degraded=True))

        events = game_routes._public_operation_events(json.loads(json.dumps(row)))

        assert events[0]["payload"]["degraded"] is True


class TestTheLiveWolfBallotCarriesTheFlag:
    """The ``wolf_vote:`` label branch, not the projector.

    The live god view is filled from the durable step labels, so this hop builds
    its own payload from ``_pending_night_batch`` — it used to drop the flag, and
    an engine-cast ballot then read as the wolf's own abstention live while the
    archive replay showed it correctly.
    """

    @staticmethod
    def _events(*, degraded: bool) -> list[dict[str, object]]:
        state = GameState("live-ballot", phase=GamePhase.NIGHT, round_number=2)
        vote = SimpleNamespace(
            seat=2, target_seat=None, reasoning=DEGRADED_REASONING, degraded=degraded,
        )
        engine = SimpleNamespace(
            game_id="live-ballot", state=state,
            _pending_night_batch=SimpleNamespace(wolf_votes=(vote,)),
        )
        return GameService._checkpoint_domain_events(engine, "00000001:wolf_vote:2:2")

    def test_a_degraded_ballot_is_marked(self) -> None:
        events = self._events(degraded=True)

        assert events[0]["event_type"] == "WOLF_VOTE"
        assert events[0]["payload"]["degraded"] is True

    def test_a_real_ballot_keeps_its_historical_shape(self) -> None:
        events = self._events(degraded=False)

        assert events[0]["event_type"] == "WOLF_VOTE"
        assert "degraded" not in events[0]["payload"]
