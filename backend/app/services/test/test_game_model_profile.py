from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.llm_client import LLMClientConfig
from app.agents.providers.base import ProviderProfile
from app.agents.providers.openai_compatible import OpenAICompatibleTransport
from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.api.websocket.ws_handler import WSManager
from app.services import game_service
from app.services.game_service import GameService, resolve_model_config
from app.stores.model_config_store import JsonModelConfigStore, ModelConfig


@pytest.fixture
def stored_model(tmp_path, monkeypatch):
    model = ModelConfig.new(
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        model_id="openrouter/model",
        provider_profile="openrouter",
    )
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    store.upsert(model)
    monkeypatch.setattr(game_service, "get_model_config_store", lambda: store)
    return model


def test_resolved_config_carries_profile_and_snapshot_has_no_key(stored_model):
    client_config, snapshot = resolve_model_config(
        [{"config_id": stored_model.id, "count": 9}], 9,
    )

    assert client_config.provider_profile == "openrouter"
    assert snapshot[0]["provider_profile"] == "openrouter"
    assert "api_key" not in snapshot[0]
    assert "api_key_encrypted" not in snapshot[0]


@pytest.mark.asyncio
async def test_create_game_passes_only_llm_clients_to_game_core(
    stored_model, monkeypatch,
):
    class FakeLLMClient:
        instances = []

        def __init__(self, *, config: LLMClientConfig):
            self.config = config
            self.instances.append(self)

        def get_model(self):
            return MagicMock()

    def assert_core_has_no_provider_objects(values):
        assert not any(
            isinstance(value, (ProviderProfile, OpenAICompatibleTransport))
            for value in values
        )

    real_create_roles = game_service.builtin_registry.create_roles
    real_director = game_service.NightDirector
    real_scheduler = game_service.Scheduler

    def create_roles(*args, **kwargs):
        assert_core_has_no_provider_objects((*args, *kwargs.values()))
        assert isinstance(kwargs["llm_client_factory"](), FakeLLMClient)
        return real_create_roles(*args, **kwargs)

    def director(*args, **kwargs):
        assert_core_has_no_provider_objects((*args, *kwargs.values()))
        return real_director(*args, **kwargs)

    def scheduler(*args, **kwargs):
        assert_core_has_no_provider_objects((*args, *kwargs.values()))
        return real_scheduler(*args, **kwargs)

    monkeypatch.setattr(game_service, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(game_service.builtin_registry, "create_roles", create_roles)
    monkeypatch.setattr(game_service, "NightDirector", director)
    monkeypatch.setattr(game_service, "Scheduler", scheduler)
    monkeypatch.setattr(GameEngine, "start", AsyncMock())

    service = GameService(WSManager(), EventBus(), data_dir="data")
    await service.create_game(
        role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 8},
        model_assignments=[{"config_id": stored_model.id, "count": 9}],
    )

    assert FakeLLMClient.instances
    assert all(
        client.config.provider_profile == "openrouter"
        for client in FakeLLMClient.instances
    )
