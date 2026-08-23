import json

from app.stores.model_config_store import JsonModelConfigStore, ModelConfig


def test_old_json_without_provider_profile_loads_as_auto(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"version": 1, "configs": [{
        "id": "old", "name": "Old", "base_url": "https://openrouter.ai/api/v1",
        "model_id": "model", "created_at": "t", "updated_at": "t",
    }]}), encoding="utf-8")

    assert JsonModelConfigStore(str(path)).get("old").provider_profile == "auto"


def test_profile_round_trip(tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    config = ModelConfig.new(
        "OpenRouter", "https://openrouter.ai/api/v1", "model",
        provider_profile="openrouter",
    )

    store.upsert(config)

    assert store.get(config.id).provider_profile == "openrouter"


def test_old_entry_is_not_mutated_when_default_profile_is_added(tmp_path, monkeypatch):
    entry = {
        "id": "old", "name": "Old", "base_url": "https://example.com",
        "model_id": "model", "created_at": "t", "updated_at": "t",
    }
    raw = {"version": 1, "configs": [entry]}
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    monkeypatch.setattr(store, "_read", lambda: raw)

    assert store.list_all()[0].provider_profile == "auto"
    assert store.get("old").provider_profile == "auto"

    assert "provider_profile" not in raw["configs"][0]
