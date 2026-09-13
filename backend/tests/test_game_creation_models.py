import pytest

from app.agents.llm_client import LLMClientConfig, env_default_client_config
from app.services import game_service
from app.services.game_service import resolve_model_assignments, resolve_model_config
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


def test_none_assignments_expands_env_default_to_every_seat():
    seat_configs, snapshot = resolve_model_assignments(None, 3)

    assert sorted(seat_configs) == [1, 2, 3]
    assert all(
        isinstance(client_config, LLMClientConfig)
        for client_config in seat_configs.values()
    )
    assert len({client.model_id for client in seat_configs.values()}) == 1
    assert snapshot == [{
        "config_id": None,
        "name": "环境默认 (.env)",
        "model_id": seat_configs[1].model_id,
        "base_url": seat_configs[1].base_url,
        "provider_profile": snapshot[0]["provider_profile"],
        "count": 3,
        "seats": [1, 2, 3],
    }]


@pytest.mark.parametrize("total_players", [0, -1, 1.5, True])
def test_total_players_must_be_a_positive_integer(total_players):
    with pytest.raises(ValueError, match="total players"):
        resolve_model_assignments(None, total_players)


def test_env_entry_returns_env_config_and_display_snapshot(monkeypatch):
    monkeypatch.setattr(game_service.app_config.llm, "base_url", "http://env.test/v1")
    monkeypatch.setattr(game_service.app_config.llm, "api_key", "env-key")

    seat_configs, snapshot = resolve_model_assignments(
        [{"config_id": None, "count": 3}], 3,
    )

    assert all(
        client_config.base_url == "http://env.test/v1"
        for client_config in seat_configs.values()
    )
    assert snapshot == [{
        "config_id": None, "name": "环境默认 (.env)",
        "model_id": seat_configs[1].model_id, "base_url": "http://env.test/v1",
        "provider_profile": "custom-openai",
        "count": 3,
        "seats": [1, 2, 3],
    }]


def test_env_entry_count_mismatch_raises():
    with pytest.raises(ValueError, match="count"):
        resolve_model_assignments([{"config_id": None, "count": 8}], 9)


def test_multiple_entries_are_independently_shuffled_and_grouped(
    tmp_path, monkeypatch,
):
    first, store = _store_with(tmp_path, monkeypatch, name="First", model_id="model-a")
    second = ModelConfig.new(
        name="Second", base_url="https://second.test/v1", model_id="model-b",
        provider_profile="openrouter",
    )
    store.upsert(second)

    seat_configs, snapshot = resolve_model_assignments(
        [
            {"config_id": None, "count": 1},
            {"config_id": first.id, "count": 2},
            {"config_id": second.id, "count": 1},
        ],
        4,
        shuffle=lambda values: values.reverse(),
    )

    assert [seat_configs[seat].model_id for seat in range(1, 5)] == [
        "model-b", "model-a", "model-a", env_default_client_config().model_id,
    ]
    assert snapshot == [
        {
            "config_id": None,
            "name": "环境默认 (.env)",
            "model_id": env_default_client_config().model_id,
            "base_url": env_default_client_config().base_url,
            "provider_profile": snapshot[0]["provider_profile"],
            "count": 1,
            "seats": [4],
        },
        {
            "config_id": first.id,
            "name": "First",
            "model_id": "model-a",
            "base_url": "https://cfg.test/v1",
            "provider_profile": "custom-openai",
            "count": 2,
            "seats": [2, 3],
        },
        {
            "config_id": second.id,
            "name": "Second",
            "model_id": "model-b",
            "base_url": "https://second.test/v1",
            "provider_profile": "openrouter",
            "count": 1,
            "seats": [1],
        },
    ]
    assert sorted(seat for item in snapshot for seat in item["seats"]) == [1, 2, 3, 4]
    assert "api_key" not in str(snapshot)


def test_duplicate_config_ids_raise():
    with pytest.raises(ValueError, match="unique"):
        resolve_model_assignments([
            {"config_id": None, "count": 1},
            {"config_id": None, "count": 1},
        ], 2)


def test_empty_assignments_raise():
    with pytest.raises(ValueError, match="non-empty"):
        resolve_model_assignments([], 2)


def test_non_dict_entry_raises():
    with pytest.raises(ValueError, match="object"):
        resolve_model_assignments(["junk"], 9)


def test_non_list_assignments_raise():
    with pytest.raises(ValueError, match="list"):
        resolve_model_assignments({"config_id": None}, 9)


@pytest.mark.parametrize("count", [0, -1, 1.5, True])
def test_count_must_be_a_positive_integer(count):
    with pytest.raises(ValueError, match="positive integer"):
        resolve_model_assignments([{"config_id": None, "count": count}], 1)


def test_config_id_must_be_a_string_or_null():
    with pytest.raises(ValueError, match="config_id"):
        resolve_model_assignments([{"config_id": 123, "count": 1}], 1)


def test_unknown_config_id_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(
        game_service, "get_model_config_store",
        lambda: JsonModelConfigStore(str(tmp_path / "models.json")),
    )
    with pytest.raises(ValueError, match="unknown"):
        resolve_model_assignments([{"config_id": "missing", "count": 9}], 9)


def test_configured_model_uses_stored_fields_and_env_fallbacks(tmp_path, monkeypatch):
    monkeypatch.setattr(game_service.app_config.llm, "max_tokens", 999)
    monkeypatch.setattr(game_service.app_config.llm, "strict_base_url", "http://env.test/beta")
    monkeypatch.setattr(game_service.app_config.llm, "action_timeout_seconds", 91.0)
    monkeypatch.setattr(game_service.app_config.llm, "action_retry_timeout_seconds", 61.0)
    monkeypatch.setattr(game_service.app_config.llm, "action_max_tokens", 2050)
    crypto = ModelKeyCrypto()
    cfg, store = _store_with(
        tmp_path, monkeypatch,
        api_key_encrypted=crypto.encrypt("sk-cfg-key"),
        temperature=0.5, strict_base_url="http://cfg.test/strict",
    )

    seat_configs, snapshot = resolve_model_assignments(
        [{"config_id": cfg.id, "count": 9}], 9,
    )
    client_config = seat_configs[1]

    assert client_config.base_url == "https://cfg.test/v1"
    assert client_config.model_id == "cfg-model"
    assert client_config.api_key == "sk-cfg-key"
    assert client_config.temperature == 0.5
    assert client_config.max_tokens == 999
    assert client_config.strict_base_url == "http://cfg.test/strict"
    assert client_config.action_timeout_seconds == 91.0
    assert client_config.action_retry_timeout_seconds == 61.0
    assert client_config.action_max_tokens == 2050
    assert snapshot == [{
        "config_id": cfg.id, "name": "DeepSeek Pro",
        "model_id": "cfg-model", "base_url": "https://cfg.test/v1",
        "provider_profile": "custom-openai",
        "count": 9,
        "seats": list(range(1, 10)),
    }]
    assert "sk-cfg-key" not in str(snapshot)

    cfg.model_id = "changed-after-start"
    store.upsert(cfg)
    store.delete(cfg.id)
    assert client_config.model_id == "cfg-model"
    assert snapshot[0]["model_id"] == "cfg-model"


def test_config_without_key_uses_env_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(game_service.app_config.llm, "api_key", "env-key")
    cfg, _ = _store_with(tmp_path, monkeypatch, api_key_encrypted="")

    seat_configs, _ = resolve_model_assignments(
        [{"config_id": cfg.id, "count": 9}], 9,
    )

    assert seat_configs[1].api_key == "env-key"


def test_config_with_invalid_key_raises(tmp_path, monkeypatch):
    cfg, _ = _store_with(tmp_path, monkeypatch, api_key_encrypted="garbage")

    with pytest.raises(ValueError, match="invalid"):
        resolve_model_assignments([{"config_id": cfg.id, "count": 9}], 9)


def test_configured_model_derives_strict_url_for_non_deepseek_provider(tmp_path, monkeypatch):
    crypto = ModelKeyCrypto()
    cfg, _ = _store_with(
        tmp_path, monkeypatch, api_key_encrypted=crypto.encrypt("sk-cfg-key"),
    )

    seat_configs, _ = resolve_model_assignments(
        [{"config_id": cfg.id, "count": 9}], 9,
    )

    assert seat_configs[1].strict_base_url == "https://cfg.test/v1"


def test_configured_model_derives_beta_strict_url_for_official_deepseek(tmp_path, monkeypatch):
    crypto = ModelKeyCrypto()
    cfg, _ = _store_with(
        tmp_path, monkeypatch,
        base_url="https://api.deepseek.com/v1",
        api_key_encrypted=crypto.encrypt("sk-cfg-key"),
    )

    seat_configs, _ = resolve_model_assignments(
        [{"config_id": cfg.id, "count": 9}], 9,
    )

    assert seat_configs[1].strict_base_url == "https://api.deepseek.com/beta"


def test_legacy_single_model_resolver_remains_compatible():
    client_config, snapshot = resolve_model_config(
        [{"config_id": None, "count": 2}], 2,
    )

    assert isinstance(client_config, LLMClientConfig)
    assert snapshot[0]["count"] == 2
    assert snapshot[0]["seats"] == [1, 2]


def test_legacy_single_model_resolver_rejects_multiple_assignments():
    with pytest.raises(ValueError, match="exactly one"):
        resolve_model_config([
            {"config_id": None, "count": 1},
            {"config_id": "another", "count": 1},
        ], 2)


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
    expected_snapshot = [{
        "config_id": None,
        "name": "环境默认 (.env)",
        "model_id": "env-model",
        "base_url": "https://env.test/v1",
        "provider_profile": "custom-openai",
        "count": 2,
        "seats": [1, 2],
    }]
    service.get_game_model_snapshot.return_value = expected_snapshot
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.create_game(CreateGameRequest(
        role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 1},
        model_assignments=[{"config_id": None, "count": 2}],
    ))

    service.create_game.assert_awaited_once_with(
        role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 1},
        reveal_on_death=False,
        model_assignments=[{"config_id": None, "count": 2}],
    )
    assert [entry.model_dump() for entry in response.model_snapshot] == expected_snapshot


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
