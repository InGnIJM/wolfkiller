"""Audience projection must emit the documented public payload keys."""

from app.services.audience_projector import AudienceProjector


def _event(event_type: str, payload: dict, visibility=("PUBLIC",)) -> dict:
    return {
        "event_id": "evt", "event_type": event_type,
        "payload": payload, "visibility": list(visibility),
    }


def test_pipeline_death_seat_becomes_player_seat() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("PLAYER_DIED", {"seat": 10, "cause": "wolf_kill", "round_number": 2}),
    ])

    assert projected == [{
        "event_id": "evt:audience",
        "event_type": "death",
        "schema_version": 1,
        "payload": {"player_seat": 10, "cause": "wolf_kill", "round_number": 2},
    }]


def test_death_already_using_player_seat_is_preserved() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("PLAYER_DIED", {"player_seat": 7, "cause": "exile", "round_number": 1}),
    ])

    assert projected[0]["payload"] == {
        "player_seat": 7, "cause": "exile", "round_number": 1,
    }


def test_engine_died_event_target_seat_becomes_player_seat() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("PLAYER_DIED", {"target_seat": 5, "cause": "hunter_shot", "round_number": 3}),
    ])

    assert projected[0]["payload"] == {
        "player_seat": 5, "cause": "hunter_shot", "round_number": 3,
    }


def test_non_public_death_is_not_projected() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("PLAYER_DIED", {"seat": 1, "cause": "wolf_kill", "round_number": 1}, visibility=()),
    ])

    assert projected == []


def test_technical_abstain_player_seat_becomes_voter_seat() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("TECHNICAL_ABSTAIN", {
            "player_seat": 4, "round_number": 2,
            "phase": "vote_casting", "failure_code": "provider_timeout",
        }),
    ])

    assert projected[0]["payload"] == {
        "voter_seat": 4, "round_number": 2, "failure_code": "provider_timeout",
    }


def test_death_without_any_seat_key_is_projected_unchanged() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("PLAYER_DIED", {"cause": "wolf_kill", "round_number": 1}),
    ])

    assert projected[0]["payload"] == {"cause": "wolf_kill", "round_number": 1}


def test_technical_abstain_without_any_seat_key_is_projected_unchanged() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("TECHNICAL_ABSTAIN", {"round_number": 1, "failure_code": "parse_error"}),
    ])

    assert projected[0]["payload"] == {"round_number": 1, "failure_code": "parse_error"}


def test_technical_abstain_already_using_voter_seat_is_preserved() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("TECHNICAL_ABSTAIN", {
            "voter_seat": 6, "round_number": 1, "failure_code": "parse_error",
        }),
    ])

    assert projected[0]["payload"] == {
        "voter_seat": 6, "round_number": 1, "failure_code": "parse_error",
    }
