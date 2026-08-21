import pytest

from app.agents.llm_client import LLMClientConfig, env_default_client_config
from app.services import game_service
from app.services.game_service import resolve_model_config
from app.stores.model_config_store import JsonModelConfigStore, ModelConfig
from app.stores.model_key_crypto import ModelKeyCrypto


def _store_with(tmp_path, monkeypatch, **fields):
    values = dict(
        name="DeepSeek Pro", base_url="https://cfg.test/v1", model_id="cfg-model",
    )
    values.update(fields)
    cfg = ModelConfig.new(**values)
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    store.upsert(cfg)
    monkeypatch.setattr(game_service, "get_model_config_store", lambda: store)
    return cfg, store


def test_none_assignments_returns_env_default_and_empty_snapshot():
    client_config, snapshot = resolve_model_config(None, 9)

    assert isinstance(client_config, LLMClientConfig)
    assert snapshot == []


def test_env_entry_returns_env_config_and_display_snapshot(monkeypatch):
    monkeypatch.setattr(game_service.app_config.llm, "base_url", "http://env.test/v1")
    monkeypatch.setattr(game_service.app_config.llm, "api_key", "env-key")

    client_config, snapshot = resolve_model_config([{"config_id": None, "count": 9}], 9)

    assert client_config.base_url == "http://env.test/v1"
    assert snapshot == [{
        "config_id": None, "name": "环境默认 (.env)",
        "model_id": client_config.model_id, "base_url": "http://env.test/v1",
    }]


def test_env_entry_count_mismatch_raises():
    with pytest.raises(ValueError, match="count"):
        resolve_model_config([{"config_id": None, "count": 8}], 9)


def test_multiple_entries_raise_in_stage_one():
    with pytest.raises(ValueError, match="exactly one"):
        resolve_model_config([{"config_id": None, "count": 5}] * 2, 9)


def test_non_dict_entry_raises():
    with pytest.raises(ValueError, match="object"):
        resolve_model_config(["junk"], 9)


def test_non_list_assignments_raise():
    with pytest.raises(ValueError, match="exactly one"):
        resolve_model_config({"config_id": None}, 9)


def test_unknown_config_id_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(
        game_service, "get_model_config_store",
        lambda: JsonModelConfigStore(str(tmp_path / "models.json")),
    )
    with pytest.raises(ValueError, match="unknown"):
        resolve_model_config([{"config_id": "missing", "count": 9}], 9)


def test_configured_model_uses_stored_fields_and_env_fallbacks(tmp_path, monkeypatch):
    monkeypatch.setattr(game_service.app_config.llm, "max_tokens", 999)
    monkeypatch.setattr(game_service.app_config.llm, "strict_base_url", "http://env.test/beta")
    monkeypatch.setattr(game_service.app_config.llm, "action_timeout_seconds", 91.0)
    monkeypatch.setattr(game_service.app_config.llm, "action_retry_timeout_seconds", 61.0)
    crypto = ModelKeyCrypto()
    cfg, _ = _store_with(
        tmp_path, monkeypatch,
        api_key_encrypted=crypto.encrypt("sk-cfg-key"),
        temperature=0.5, strict_base_url="http://cfg.test/strict",
    )

    client_config, snapshot = resolve_model_config(
        [{"config_id": cfg.id, "count": 9}], 9,
    )

    assert client_config.base_url == "https://cfg.test/v1"
    assert client_config.model_id == "cfg-model"
    assert client_config.api_key == "sk-cfg-key"
    assert client_config.temperature == 0.5
    assert client_config.max_tokens == 999
    assert client_config.strict_base_url == "http://cfg.test/strict"
    assert client_config.action_timeout_seconds == 91.0
    assert client_config.action_retry_timeout_seconds == 61.0
    assert snapshot == [{
        "config_id": cfg.id, "name": "DeepSeek Pro",
        "model_id": "cfg-model", "base_url": "https://cfg.test/v1",
    }]
    assert "sk-cfg-key" not in str(snapshot)


def test_config_without_key_uses_env_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(game_service.app_config.llm, "api_key", "env-key")
    cfg, _ = _store_with(tmp_path, monkeypatch, api_key_encrypted="")

    client_config, _ = resolve_model_config([{"config_id": cfg.id, "count": 9}], 9)

    assert client_config.api_key == "env-key"


def test_config_with_invalid_key_raises(tmp_path, monkeypatch):
    cfg, _ = _store_with(tmp_path, monkeypatch, api_key_encrypted="garbage")

    with pytest.raises(ValueError, match="invalid"):
        resolve_model_config([{"config_id": cfg.id, "count": 9}], 9)


def test_configured_model_derives_strict_url_for_non_deepseek_provider(tmp_path, monkeypatch):
    crypto = ModelKeyCrypto()
    cfg, _ = _store_with(
        tmp_path, monkeypatch, api_key_encrypted=crypto.encrypt("sk-cfg-key"),
    )

    client_config, _ = resolve_model_config([{"config_id": cfg.id, "count": 9}], 9)

    assert client_config.strict_base_url == "https://cfg.test/v1"


def test_configured_model_derives_beta_strict_url_for_official_deepseek(tmp_path, monkeypatch):
    crypto = ModelKeyCrypto()
    cfg, _ = _store_with(
        tmp_path, monkeypatch,
        base_url="https://api.deepseek.com/v1",
        api_key_encrypted=crypto.encrypt("sk-cfg-key"),
    )

    client_config, _ = resolve_model_config([{"config_id": cfg.id, "count": 9}], 9)

    assert client_config.strict_base_url == "https://api.deepseek.com/beta"


@pytest.mark.asyncio
async def test_create_game_route_passes_assignments_and_returns_snapshot(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.api.routes import game_routes
    from app.api.schemas import CreateGameRequest

    service = MagicMock()
    service.create_game = AsyncMock(return_value="game-123")
    service.get_game_state.return_value = MagicMock(
        players={1: object(), 2: object()},
        config=MagicMock(role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 1}),
    )
    service.get_game_model_snapshot.return_value = [{"config_id": None, "name": "环境默认 (.env)"}]
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.create_game(CreateGameRequest(
        role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 1},
        model_assignments=[{"config_id": None, "count": 2}],
    ))

    service.create_game.assert_awaited_once_with(
        role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 1},
        model_assignments=[{"config_id": None, "count": 2}],
    )
    assert response.model_snapshot == [{"config_id": None, "name": "环境默认 (.env)"}]


@pytest.mark.asyncio
async def test_create_game_route_maps_resolution_errors_to_400(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import HTTPException

    from app.api.routes import game_routes
    from app.api.schemas import CreateGameRequest

    service = MagicMock()
    service.create_game = AsyncMock(side_effect=ValueError("unknown model config"))
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    with pytest.raises(HTTPException) as exc:
        await game_routes.create_game(CreateGameRequest(
            role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 1},
            model_assignments=[{"config_id": "missing", "count": 2}],
        ))
    assert exc.value.status_code == 400


def test_manifest_update_game_persists_model_snapshot(tmp_path):
    from app.services.game_manifest import GameManifest

    manifest = GameManifest(data_dir=str(tmp_path))
    manifest.add_game(
        "g1", {"role_counts": {"wolf-killer-werewolf": 1, "wolf-killer-villager": 1}},
    )
    manifest.update_game(
        "g1", model_snapshot=[{"config_id": None, "name": "环境默认 (.env)"}],
    )
    # load_or_rebuild() drops entries whose game directory is missing,
    # so create it (matching the other manifest persistence tests).
    (tmp_path / "games" / "g1").mkdir(parents=True, exist_ok=True)

    entries = manifest.load_or_rebuild()

    assert entries["g1"]["model_snapshot"] == [
        {"config_id": None, "name": "环境默认 (.env)"},
    ]
