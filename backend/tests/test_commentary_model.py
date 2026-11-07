"""Commentary/review model selection: provider config plus per-selection
``model_id``/``reasoning_effort`` overrides, frozen into the game doc."""

from types import SimpleNamespace

import pytest

import app.services.game_service as service_module
from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.core.game_engine import GameEngine
from app.persistence.repository import GameRepository
from app.services.game_service import GameService
from app.stores.model_config_store import JsonModelConfigStore, ModelConfig


def _store_with(tmp_path, monkeypatch, **fields):
    values = dict(
        name="Commentary", base_url="https://comm.test/v1", model_id="comm-model",
    )
    values.update(fields)
    cfg = ModelConfig.new(**values)
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    store.upsert(cfg)
    monkeypatch.setattr(service_module, "get_model_config_store", lambda: store)
    return cfg, store


class _FakeClient:
    def __init__(self, *, config, dedicated_http_clients: bool = False):
        self.config = config
        self.model_name = config.model_id
        self.provider_profile = SimpleNamespace(profile_id="custom-openai")
        self.closed = 0

    async def aclose(self):
        self.closed += 1


async def _noop_start(self):
    return None


@pytest.fixture
def fake_clients(monkeypatch):
    monkeypatch.setattr(service_module, "LLMClient", _FakeClient)
    monkeypatch.setattr(GameEngine, "start", _noop_start)


@pytest.mark.asyncio
async def test_create_game_freezes_commentary_overrides(
    tmp_path, monkeypatch, fake_clients,
):
    cfg, _ = _store_with(tmp_path, monkeypatch, reasoning_effort="high")
    repository = GameRepository(tmp_path / "repo")
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    try:
        game_id = await service.create_game(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
            commentary_enabled=True,
            commentary_config_id=cfg.id,
            commentary_model_id="mimo-v2.6-flash",
            commentary_reasoning_effort="none",
        )
        await service._tasks[game_id]

        doc = repository.get_game(game_id)["config"]["commentary"]
        assert doc["commentary_config_id"] == cfg.id
        assert doc["commentary_model_id"] == "mimo-v2.6-flash"
        assert doc["commentary_reasoning_effort"] == "none"
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_commentary_overrides_without_a_config_are_rejected(
    tmp_path, monkeypatch, fake_clients,
):
    repository = GameRepository(tmp_path / "repo")
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    try:
        with pytest.raises(ValueError, match="commentary_model_id"):
            await service.create_game(
                role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
                commentary_model_id="mimo-v2.6-flash",
            )
        with pytest.raises(ValueError, match="commentary_reasoning_effort"):
            await service.create_game(
                role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
                commentary_reasoning_effort="low",
            )
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_create_game_rejects_invalid_commentary_overrides(
    tmp_path, monkeypatch, fake_clients,
):
    cfg, _ = _store_with(tmp_path, monkeypatch)
    repository = GameRepository(tmp_path / "repo")
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    try:
        with pytest.raises(ValueError, match="commentary_model_id"):
            await service.create_game(
                role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
                commentary_config_id=cfg.id, commentary_model_id="  ",
            )
        with pytest.raises(ValueError, match="reasoning_effort"):
            await service.create_game(
                role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
                commentary_config_id=cfg.id, commentary_reasoning_effort="ultra",
            )
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_create_game_without_commentary_keeps_meta_clean(
    tmp_path, monkeypatch, fake_clients,
):
    repository = GameRepository(tmp_path / "repo")
    service = GameService(
        WSManager(), EventBus(), data_dir=str(tmp_path), repository=repository,
    )
    try:
        game_id = await service.create_game(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
        )
        await service._tasks[game_id]
        doc = repository.get_game(game_id)["config"]["commentary"]
        assert doc["enabled"] is False
        assert doc["commentary_model_id"] is None
        assert doc["commentary_reasoning_effort"] is None
    finally:
        await service.aclose()
        repository.close()
