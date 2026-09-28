"""The wolves' next-day plan belongs to the god view.

A wolf turn may carry a ``day_plan``: the engine appends it to the line it keeps
in the wolf channel as ``（次日计划：…）``, and the audience mapping used to cut it
off and throw it away, so the plan was visible only when a wolf happened to
repeat it in a later line.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.models.game import GamePhase, GameState
from app.services.audience_projector import AudienceProjector
from app.services.game_service import GameService


def _engine(history: tuple[str, ...], round_number: int = 1) -> SimpleNamespace:
    state = GameState("plan", phase=GamePhase.NIGHT, round_number=round_number)
    return SimpleNamespace(
        game_id="plan", state=state,
        _pending_night_batch=SimpleNamespace(discussion_history=history),
    )


def test_a_wolf_turn_publishes_its_next_day_plan_as_its_own_field() -> None:
    engine = _engine(("4号：今晚刀预言家（次日计划：明天推3号）",))

    events = GameService._checkpoint_domain_events(engine, "00000001:wolf_discussion:1:4:1")

    assert [event["event_type"] for event in events] == ["WOLF_CHAT_MESSAGE"]
    assert events[0]["payload"] == {
        "seat": 4, "text": "今晚刀预言家", "day_plan": "明天推3号", "round_number": 1,
    }
    projected = AudienceProjector().project_events("plan", events)
    assert projected[0]["payload"] == {
        "seat": 4, "text": "今晚刀预言家", "day_plan": "明天推3号", "round_number": 1,
    }


def test_a_wolf_turn_without_a_plan_publishes_no_plan_field() -> None:
    engine = _engine(("5号：先听4号的",))

    events = GameService._checkpoint_domain_events(engine, "00000002:wolf_discussion:1:5:2")

    assert events[0]["payload"] == {"seat": 5, "text": "先听4号的", "round_number": 1}


def test_a_chat_line_that_only_mentions_the_marker_stays_whole() -> None:
    # The marker without the closing bracket is prose, not a plan: the line is
    # published as it was said instead of being cut in half.
    engine = _engine(("6号：我说的是（次日计划：要小心",))

    events = GameService._checkpoint_domain_events(engine, "00000003:wolf_discussion:1:6:3")

    assert events[0]["payload"] == {
        "seat": 6, "text": "我说的是（次日计划：要小心", "round_number": 1,
    }
