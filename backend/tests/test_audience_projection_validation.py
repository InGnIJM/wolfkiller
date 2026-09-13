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


def test_initialization_without_optional_players_or_config_remains_valid():
    projected = AudienceProjector().project_events("game", [{
        "event_id": "init", "event_type": "GAME_INITIALIZED", "visibility": ["PUBLIC"],
        "payload": {},
    }])
    assert projected[0]["payload"] == {}
