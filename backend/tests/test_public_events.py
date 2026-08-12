from app.api.websocket.public_events import PublicNightSubstep


def test_public_night_substep_emits_only_the_public_allowlist():
    event = PublicNightSubstep.from_internal(
        step="werewolf",
        round_number=3,
        highlight_seats=[1, 4],
        action_seat=1,
        action={"target": 4, "result": "killed", "role": "werewolf"},
        wolf_kill_target=4,
        future_secret={"identity": "seer"},
    )

    assert event.to_payload() == {
        "phase": "night",
        "round_number": 3,
        "substep": "werewolf",
    }


def test_public_night_substep_discards_private_input_values():
    event = PublicNightSubstep.from_internal(
        step="witch",
        round_number=8,
        highlight_seats=[2],
        action_seat=2,
        action={"target": 5, "result": "poisoned", "role": "witch"},
        wolf_kill_target=5,
        arbitrary_future_secret="never expose this",
    )

    payload = event.to_payload()

    assert set(payload) == {"phase", "round_number", "substep"}
    assert "highlight_seats" not in payload
    assert "action_seat" not in payload
    assert "action" not in payload
    assert "wolf_kill_target" not in payload
    assert "arbitrary_future_secret" not in payload


def test_public_night_substep_is_immutable():
    event = PublicNightSubstep.from_internal(step="seer", round_number=1)

    try:
        event.substep = "werewolf"
    except AttributeError:
        pass
    else:
        raise AssertionError("PublicNightSubstep must be immutable")
