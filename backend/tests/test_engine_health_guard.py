"""The engine's guard checks must sit above the directors' broad fallbacks."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.model_health import HealthEvent
from app.core.game_engine import GameEngine
from app.core.model_health_guard import ProviderOutagePaused
from app.models.game import GameConfig, GameState, PlayerState


def _event() -> HealthEvent:
    return HealthEvent(
        provider_key="k", rule="provider_terminal",
        failure_code="provider_auth_error", seats=(1, 2),
        contracts=("night_action",), sample_count=2, reopen_count=0,
    )


class _Guard:
    def __init__(self, *, raise_for: int | None = 1) -> None:
        self.checks: list[int] = []
        self.raise_for = raise_for

    def record_telemetry(self, _record) -> None:
        return None

    def check(self, seat: int) -> None:
        self.checks.append(seat)
        if seat == self.raise_for:
            raise ProviderOutagePaused(_event())


def _engine(guard, tmp_path) -> GameEngine:
    engine = GameEngine(
        "game", config=GameConfig(), roles={}, data_dir=str(tmp_path),
        health_guard=guard,
    )
    engine.state = GameState(game_id="game", config=engine.config)
    engine.state.players[1] = PlayerState(seat_number=1, role="wolf-killer-villager", camp="good")
    engine.roles[1] = SimpleNamespace(
        speak=_never_called, request_action=_never_called_async,
    )
    return engine


async def _never_called(*_args, **_kwargs):
    raise AssertionError("the model must not be called after the guard trips")


def _never_called_async(*_args, **_kwargs):
    raise AssertionError("the model must not be called after the guard trips")


@pytest.mark.asyncio
async def test_speak_checks_the_guard_before_the_role_talk(tmp_path) -> None:
    guard = _Guard()
    engine = _engine(guard, tmp_path)

    with pytest.raises(ProviderOutagePaused):
        await engine.speak(1, "day_speech")
    assert guard.checks == [1]


@pytest.mark.asyncio
async def test_vote_checks_the_guard_before_opening_the_window(tmp_path) -> None:
    guard = _Guard()
    engine = _engine(guard, tmp_path)

    with pytest.raises(ProviderOutagePaused):
        await engine.vote(1)
    assert guard.checks == [1]


@pytest.mark.asyncio
async def test_a_missing_guard_is_a_noop(tmp_path) -> None:
    engine = GameEngine("game", config=GameConfig(), roles={}, data_dir=str(tmp_path))
    engine.state = GameState(game_id="game", config=engine.config)
    engine._guard_turn(1)
