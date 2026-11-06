"""Process-level session-affinity switch and its per-seat request headers."""

from __future__ import annotations

import logging

import pytest

import app.services.game_service as game_service_module
from app.agents import session_affinity
from app.agents.llm_client import LLMClientConfig
from app.agents.session_affinity import (
    SESSION_AFFINITY_ENV,
    SESSION_HEADER_NAME,
    SessionAffinityMode,
    apply_session_affinity,
    session_affinity_mode,
    session_affinity_plan,
    session_key,
    treatment_seats,
)


def _config(**overrides) -> LLMClientConfig:
    values = {
        "base_url": "http://127.0.0.1:8045/v1",
        "api_key": "test-key",
        "model_id": "gemini-test",
        "temperature": 0.1,
        "max_tokens": 128,
        "strict_base_url": "http://127.0.0.1:8045/v1",
        "provider_profile": "custom-openai",
    }
    values.update(overrides)
    return LLMClientConfig(**values)


def _header(config: LLMClientConfig) -> str | None:
    return dict(config.headers).get(SESSION_HEADER_NAME)


# --- switch -----------------------------------------------------------------


def test_mode_defaults_to_off_when_unset(monkeypatch):
    monkeypatch.delenv(SESSION_AFFINITY_ENV, raising=False)

    assert session_affinity_mode() is SessionAffinityMode.OFF


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_mode_means_off(raw):
    assert session_affinity_mode(raw) is SessionAffinityMode.OFF


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("on", SessionAffinityMode.ON),
        ("ON", SessionAffinityMode.ON),
        (" ab ", SessionAffinityMode.AB),
        ("off", SessionAffinityMode.OFF),
    ],
)
def test_mode_accepts_the_three_names_case_insensitively(raw, expected):
    assert session_affinity_mode(raw) is expected


def test_mode_reads_the_process_environment(monkeypatch):
    monkeypatch.setenv(SESSION_AFFINITY_ENV, "AB")

    assert session_affinity_mode() is SessionAffinityMode.AB


@pytest.mark.parametrize("raw", ["yes", "1", "a/b"])
def test_invalid_mode_fails_loudly(raw):
    with pytest.raises(ValueError, match=SESSION_AFFINITY_ENV):
        session_affinity_mode(raw)


# --- deterministic key and arm ---------------------------------------------


def test_off_is_not_a_treatment_seat():
    # ``apply_session_affinity`` returns before the check when off; this keeps
    # the helper itself total and covered for every mode.
    assert session_affinity._is_treatment_seat(  # noqa: SLF001
        "g", 1, 6, SessionAffinityMode.OFF,
    ) is False


def test_session_key_is_stable_and_seat_specific():
    assert session_key("game-1", 3) == "wk-game-1-s3"
    assert session_key("game-1", 3) == session_key("game-1", 3)
    assert session_key("game-1", 3) != session_key("game-1", 4)
    assert session_key("game-1", 3) != session_key("game-2", 3)


@pytest.mark.parametrize(("players", "half"), [(12, 6), (9, 4), (1, 0)])
def test_treatment_seats_is_deterministic_and_exactly_half(players, half):
    first = treatment_seats("game-1", players)
    second = treatment_seats("game-1", players)

    assert first == second
    assert len(first) == half
    assert first <= frozenset(range(1, players + 1))


def test_treatment_seats_are_reproducible_from_the_game_id():
    # The report script relies on recomputing the same arm after the game.
    other_game = treatment_seats("game-2", 12)

    assert other_game == treatment_seats("game-2", 12)
    assert other_game <= frozenset(range(1, 13))


def test_plan_describes_the_arm_for_logs():
    assert session_affinity_plan("g", 6, SessionAffinityMode.OFF) == {
        "mode": "off", "header": SESSION_HEADER_NAME, "treatment_seats": [],
    }
    assert session_affinity_plan("g", 6, SessionAffinityMode.ON) == {
        "mode": "on", "header": SESSION_HEADER_NAME,
        "treatment_seats": [1, 2, 3, 4, 5, 6],
    }
    plan = session_affinity_plan("g", 6, SessionAffinityMode.AB)
    assert plan["mode"] == "ab"
    assert plan["header"] == SESSION_HEADER_NAME
    assert set(plan["treatment_seats"]) == treatment_seats("g", 6)


# --- config stamping --------------------------------------------------------


def test_off_returns_the_original_config():
    config = _config()

    result = apply_session_affinity(
        config, game_id="g", seat=1, total_players=6,
        mode=SessionAffinityMode.OFF, profile_id="custom-openai",
    )

    assert result is config


def test_on_stamps_every_gateway_seat():
    for seat in (1, 2, 3):
        config = _config()

        result = apply_session_affinity(
            config, game_id="g", seat=seat, total_players=6,
            mode=SessionAffinityMode.ON, profile_id="custom-openai",
        )

        assert _header(result) == session_key("g", seat)
        assert config.headers == ()


def test_ab_stamps_only_the_treatment_half():
    treatment = treatment_seats("g", 6)
    assert treatment

    for seat in range(1, 7):
        result = apply_session_affinity(
            _config(), game_id="g", seat=seat, total_players=6,
            mode=SessionAffinityMode.AB, profile_id="custom-openai",
        )
        if seat in treatment:
            assert _header(result) == session_key("g", seat)
        else:
            assert _header(result) is None


def test_non_gateway_profiles_are_never_stamped():
    config = _config(provider_profile="openai")

    result = apply_session_affinity(
        config, game_id="g", seat=1, total_players=6,
        mode=SessionAffinityMode.ON, profile_id="openai",
    )

    assert result is config


def test_an_explicit_session_header_is_respected(caplog):
    config = _config(headers=(("X-Session-Id", "operator-choice"),))

    with caplog.at_level(logging.WARNING, logger=session_affinity.__name__):
        result = apply_session_affinity(
            config, game_id="g", seat=1, total_players=6,
            mode=SessionAffinityMode.ON, profile_id="custom-openai",
        )

    assert result is config
    assert dict(result.headers)["X-Session-Id"] == "operator-choice"
    assert "already configured" in caplog.text


def test_the_header_does_not_change_model_parameters_or_digest():
    config = _config()
    stamped = apply_session_affinity(
        config, game_id="g", seat=1, total_players=6,
        mode=SessionAffinityMode.ON, profile_id="custom-openai",
    )

    assert game_service_module._model_parameters(stamped) == (
        game_service_module._model_parameters(config)
    )
    assert game_service_module._parameter_digest(
        game_service_module._model_parameters(stamped)
    ) == game_service_module._parameter_digest(
        game_service_module._model_parameters(config)
    )
