"""Custom request headers must reach the client but never the public record."""

from __future__ import annotations

from dataclasses import replace

import pytest
import pytest_asyncio

import app.services.game_service as module
from app.agents.llm_client import env_default_client_config
from app.api.model_schemas import public_model_snapshot
from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.persistence.repository import GameRepository
from app.services.game_service import GameService, resolve_model_config
from app.stores.model_config_store import JsonModelConfigStore, ModelConfig

HEADERS = {"x-opencode-request": "user-1"}


@pytest_asyncio.fixture
async def durable(tmp_path):
    repository = GameRepository(tmp_path)
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    yield service, repository
    await service.aclose()
    repository.close()


@pytest.fixture
def stored_model(tmp_path, monkeypatch):
    model = ModelConfig.new(
        name="OpenCode Zen",
        base_url="https://opencode.ai/zen/v1",
        model_id="claude-sonnet-4-6",
        provider_profile="opencode",
        headers=HEADERS,
    )
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    store.upsert(model)
    monkeypatch.setattr(module, "get_model_config_store", lambda: store)
    return model


def test_resolved_config_carries_custom_headers(stored_model):
    client_config, _ = resolve_model_config(
        [{"config_id": stored_model.id, "count": 9}], 9,
    )

    assert client_config.headers == (("x-opencode-request", "user-1"),)


def test_resolved_config_resolves_the_opencode_profile(stored_model):
    client_config, snapshot = resolve_model_config(
        [{"config_id": stored_model.id, "count": 9}], 9,
    )

    assert client_config.provider_profile == "opencode"
    assert snapshot[0]["provider_profile"] == "opencode"


def test_model_snapshot_omits_custom_headers(stored_model):
    _, snapshot = resolve_model_config(
        [{"config_id": stored_model.id, "count": 9}], 9,
    )

    assert "headers" not in snapshot[0]


def test_public_model_snapshot_drops_custom_headers(stored_model):
    _, snapshot = resolve_model_config(
        [{"config_id": stored_model.id, "count": 9}], 9,
    )
    leaked = [{**snapshot[0], "headers": HEADERS}]

    published = public_model_snapshot(leaked)

    assert published and "headers" not in published[0]


def test_headers_do_not_change_the_parameters_digest(stored_model):
    """An existing checkpoint must still resume after headers are added."""
    with_headers, _ = resolve_model_config(
        [{"config_id": stored_model.id, "count": 9}], 9,
    )
    without_headers = replace(with_headers, headers=())

    assert module._model_parameters(with_headers) == (
        module._model_parameters(without_headers)
    )
    assert module._parameter_digest(module._model_parameters(with_headers)) == (
        module._parameter_digest(module._model_parameters(without_headers))
    )


def test_opencode_profile_is_publishable_in_a_model_snapshot(stored_model):
    """The snapshot validator must know every resolvable profile id."""
    _, snapshot = resolve_model_config(
        [{"config_id": stored_model.id, "count": 9}], 9,
    )

    assert len(public_model_snapshot(snapshot)) == 1


def _record_for(config, config_id, seats=(1, 2)):
    parameters = module._model_parameters(config)
    return {"config": {
        "engine_recovery_version": 1, "prompt_digest": module._prompt_digest(),
        "model_runtime_version": 1, "model_runtime": [{
            "config_id": config_id, "seats": list(seats), "parameters": parameters,
            "parameters_digest": module._parameter_digest(parameters),
        }],
    }}


@pytest.mark.asyncio
async def test_recovery_restores_headers_from_the_live_config(durable, stored_model):
    from tests.test_game_service_lifecycle import _state_with_players

    service, _ = durable
    env_config = replace(env_default_client_config(), provider_profile="auto")
    current, _ = module._saved_client_config(stored_model.id, env_config)
    record = _record_for(current, stored_model.id)

    restored = service._resolve_recovery_model_configs(_state_with_players(), record)

    assert restored[1].headers == (("x-opencode-request", "user-1"),)
    assert restored[2].headers == (("x-opencode-request", "user-1"),)


@pytest.mark.asyncio
async def test_recovery_without_a_stored_config_carries_no_headers(durable):
    from tests.test_game_service_lifecycle import _state_with_players

    service, _ = durable
    env_config = replace(env_default_client_config(), provider_profile="auto")
    record = _record_for(env_config, None)

    restored = service._resolve_recovery_model_configs(_state_with_players(), record)

    assert restored[1].headers == ()
