"""Malformed audience facts must not corrupt cursors or expose private fields."""

from types import SimpleNamespace

import pytest

from app.services.audience_event_service import AudienceEventService
from app.services.audience_projector import AudienceProjector


@pytest.mark.parametrize("watermark", [True, -1, "3", None])
def test_invalid_repository_watermark_is_not_returned_to_viewers(watermark):
    repository = SimpleNamespace(
        get_game=lambda _: {"game_id": "game"},
        get_audience_events=lambda *args, **kwargs: {"high_watermark": watermark},
    )
    with pytest.raises(RuntimeError, match="invalid high_watermark"):
        AudienceEventService(repository).get_events("game", after_seq=0, limit=100)


@pytest.mark.parametrize("changes", [{"event_id": ""}, {"event_id": None}, {"payload": []}])
def test_incomplete_public_event_cannot_enter_the_audience_stream(changes):
    event = {
        "event_id": "speech", "event_type": "SPEECH_MADE", "visibility": ["PUBLIC"],
        "payload": {"player_seat": 1, "text": "hello"},
    }
    with pytest.raises(ValueError, match="event_id or payload"):
        AudienceProjector().project_events("game", [{**event, **changes}])


def test_legacy_initialization_player_list_is_sanitized_without_config():
    projected = AudienceProjector().project_events("game", [{
        "event_id": "init", "event_type": "GAME_INITIALIZED", "visibility": ["PUBLIC"],
        "payload": {"players": [
            {"seat_number": 1, "role": "villager", "private_prompt": "hidden"},
            "invalid player",
        ]},
    }])
    assert projected[0]["payload"] == {"players": [{"seat_number": 1, "role": "villager"}]}


def test_day_verdict_events_are_projected_for_viewers():
    projected = AudienceProjector().project_events("game", [
        {"event_id": "flip", "event_type": "EXILE_CANCELLED", "visibility": ["PUBLIC"],
         "payload": {"target_seat": 3, "round_number": 2, "secret": "x"}},
        {"event_id": "boom", "event_type": "SELF_EXPLODE", "visibility": ["PUBLIC"],
         "payload": {"seat": 1, "target_seat": 4, "round_number": 2}},
        {"event_id": "why", "event_type": "WEREWOLF_KING_REASONING", "visibility": ["PUBLIC"],
         "payload": {"seat": 1, "action_type": "explode", "target_seat": 4,
                     "reasoning": "r", "thought": "private", "round_number": 2}},
        {"event_id": "badge", "event_type": "SHERIFF_BADGE", "visibility": ["PUBLIC"],
         "payload": {"from_seat": 4, "to_seat": None, "round_number": 1, "secret": "x"}},
        {"event_id": "win", "event_type": "SHERIFF_ELECTED", "visibility": ["PUBLIC"],
         "payload": {"seat": 2, "round_number": 1, "reason": "auto"}},
        {"event_id": "run", "event_type": "SHERIFF_RUN", "visibility": ["PUBLIC"],
         "payload": {"seat": 2, "choice": "run", "round_number": 1, "secret": "x"}},
        {"event_id": "out", "event_type": "SHERIFF_WITHDRAW", "visibility": ["PUBLIC"],
         "payload": {"seat": 3, "choice": "withdraw", "round_number": 1}},
        {"event_id": "ballot", "event_type": "SHERIFF_VOTE", "visibility": ["PUBLIC"],
         "payload": {"voter_seat": 4, "target_seat": None, "kind": "vote", "round_number": 1}},
        {"event_id": "side", "event_type": "SHERIFF_SIDE", "visibility": ["PUBLIC"],
         "payload": {"seat": 2, "side": "death_left", "round_number": 1}},
    ])
    assert [(row["event_type"], row["payload"]) for row in projected] == [
        ("exile_cancelled", {"target_seat": 3, "round_number": 2}),
        ("self_explode", {"seat": 1, "target_seat": 4, "round_number": 2}),
        ("night_thought", {"seat": 1, "action_type": "werewolf_king_reasoning",
                           "target_seat": 4, "reasoning": "r", "round_number": 2}),
        ("sheriff_badge", {"from_seat": 4, "to_seat": None, "round_number": 1}),
        ("sheriff_elected", {"seat": 2, "round_number": 1, "reason": "auto"}),
        ("sheriff_run", {"seat": 2, "choice": "run", "round_number": 1}),
        ("sheriff_withdraw", {"seat": 3, "choice": "withdraw", "round_number": 1}),
        ("sheriff_vote", {"voter_seat": 4, "target_seat": None, "kind": "vote", "round_number": 1}),
        ("sheriff_side", {"seat": 2, "side": "death_left", "round_number": 1}),
    ]


def test_initialization_without_optional_players_or_config_remains_valid():
    projected = AudienceProjector().project_events("game", [{
        "event_id": "init", "event_type": "GAME_INITIALIZED", "visibility": ["PUBLIC"],
        "payload": {},
    }])
    assert projected[0]["payload"] == {}
