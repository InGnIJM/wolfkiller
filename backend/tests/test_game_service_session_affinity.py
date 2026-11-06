"""Session headers must reach the seat clients on create and on recovery."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.game_service as service_module
from app.agents.llm_client import env_default_client_config
from app.agents.session_affinity import (
    SESSION_AFFINITY_ENV,
    SESSION_HEADER_NAME,
    treatment_seats,
)
from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.services.game_service import GameService
from app.stores.model_config_store import JsonModelConfigStore, ModelConfig
from tests.test_game_service_lifecycle import _state_with_players


@pytest.fixture
def gateway_model(tmp_path, monkeypatch):
    model = ModelConfig.new(
        name="Gateway",
        base_url="http://127.0.0.1:8045/v1",
        model_id="gemini-3.8-flash-high",
        provider_profile="custom-openai",
    )
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    store.upsert(model)
    monkeypatch.setattr(service_module, "get_model_config_store", lambda: store)
    return model


def _capturing_client() -> tuple[type, list]:
    captured: list = []

    class Client:
        def __init__(self, *, config, dedicated_http_clients: bool = False):
            captured.append(config)

        async def aclose(self):
            pass

        def close(self):
            pass

    return Client, captured


def _header(config) -> str | None:
    return dict(config.headers).get(SESSION_HEADER_NAME)


async def _create_game(monkeypatch, tmp_path, gateway_model):
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    service._manifest = MagicMock()
    monkeypatch.setattr(GameEngine, "start", AsyncMock())
    client, captured = _capturing_client()
    monkeypatch.setattr(service_module, "LLMClient", client)
    try:
        game_id = await service.create_game(
            role_counts={"wolf-killer-werewolf": 2, "wolf-killer-villager": 4},
            model_assignments=[{"config_id": gateway_model.id, "count": 6}],
        )
    finally:
        await service.aclose()
    return game_id, captured


@pytest.mark.asyncio
async def test_invalid_switch_fails_before_a_game_is_created(monkeypatch, tmp_path):
    monkeypatch.setenv(SESSION_AFFINITY_ENV, "bogus")
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    service._manifest = MagicMock()
    try:
        with pytest.raises(ValueError, match=SESSION_AFFINITY_ENV):
            await service.create_game(role_counts={"wolf-killer-villager": 2})
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_create_game_stamps_every_seat_when_switched_on(
    monkeypatch, tmp_path, gateway_model,
):
    monkeypatch.setenv(SESSION_AFFINITY_ENV, "on")

    game_id, captured = await _create_game(monkeypatch, tmp_path, gateway_model)

    assert len(captured) == 6
    assert {_header(config) for config in captured} == {
        f"wk-{game_id}-s{seat}" for seat in range(1, 7)
    }


@pytest.mark.asyncio
async def test_create_game_stamps_only_the_ab_treatment_half(
    monkeypatch, tmp_path, gateway_model,
):
    monkeypatch.setenv(SESSION_AFFINITY_ENV, "ab")

    game_id, captured = await _create_game(monkeypatch, tmp_path, gateway_model)

    treatment = treatment_seats(game_id, 6)
    stamped = {_header(config) for config in captured if _header(config)}
    assert stamped == {f"wk-{game_id}-s{seat}" for seat in treatment}
    assert sum(1 for config in captured if _header(config) is None) == 6 - len(treatment)


@pytest.mark.asyncio
async def test_create_game_leaves_seats_alone_when_switched_off(
    monkeypatch, tmp_path, gateway_model,
):
    monkeypatch.delenv(SESSION_AFFINITY_ENV, raising=False)

    _, captured = await _create_game(monkeypatch, tmp_path, gateway_model)

    assert len(captured) == 6
    assert all(_header(config) is None for config in captured)


@pytest.mark.asyncio
async def test_recovery_recomputes_the_same_per_seat_headers(
    monkeypatch, tmp_path, gateway_model,
):
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    state = _state_with_players()
    env_config = replace(env_default_client_config(), provider_profile="auto")
    config, _ = service_module._saved_client_config(gateway_model.id, env_config)
    monkeypatch.setattr(
        service, "_resolve_recovery_model_configs", lambda *_args: {1: config, 2: config},
    )
    monkeypatch.setenv(SESSION_AFFINITY_ENV, "ab")

    client, captured = _capturing_client()
    monkeypatch.setattr(service_module, "LLMClient", client)

    class ConstructionStopped(Exception):
        pass

    class EarlyDirector:
        def __init__(self, *_args, **_kwargs):
            raise ConstructionStopped

    monkeypatch.setattr(service_module, "NightDirector", EarlyDirector)
    try:
        with pytest.raises(ConstructionStopped):
            service._build_recovered_engine(state, {}, {})
    finally:
        await service.aclose()

    treatment = treatment_seats(state.game_id, len(state.players))
    stamped = {_header(item) for item in captured if _header(item)}
    assert stamped == {f"wk-{state.game_id}-s{seat}" for seat in treatment}
