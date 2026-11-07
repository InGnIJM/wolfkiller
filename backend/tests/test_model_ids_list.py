"""Provider configs keep an editable model list; ``model_id`` stays the
default entry (``model_ids[0]``) so older consumers keep working."""

import json

import pytest

from app.stores.model_config_store import JsonModelConfigStore, ModelConfig


def _store(tmp_path, monkeypatch):
    return JsonModelConfigStore(str(tmp_path / "models.json"))


def test_new_config_defaults_model_ids_to_single_entry(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    config = ModelConfig.new(
        name="p", base_url="https://x/v1", model_id="m-a",
    )
    store.upsert(config)

    assert config.model_ids == ["m-a"]
    assert store.get(config.id).model_ids == ["m-a"]


def test_new_config_accepts_model_list_and_syncs_default(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    config = ModelConfig.new(
        name="p", base_url="https://x/v1", model_id="m-a",
        model_ids=["m-a", "m-b"],
    )
    store.upsert(config)

    loaded = store.get(config.id)
    assert loaded.model_ids == ["m-a", "m-b"]
    assert loaded.model_id == "m-a"


def test_legacy_entry_without_model_ids_migrates(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "version": 1,
        "configs": [{
            "id": "c1", "name": "p", "base_url": "https://x/v1",
            "model_id": "m-a", "api_key_encrypted": "",
        }],
    }), encoding="utf-8")
    store = JsonModelConfigStore(str(path))

    loaded = store.get("c1")
    assert loaded.model_ids == ["m-a"]
    assert loaded.model_id == "m-a"


def test_stored_list_keeps_model_id_in_sync_with_first(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "version": 1,
        "configs": [{
            "id": "c1", "name": "p", "base_url": "https://x/v1",
            "model_id": "stale", "model_ids": ["m-a", "m-b"],
            "api_key_encrypted": "",
        }],
    }), encoding="utf-8")
    store = JsonModelConfigStore(str(path))

    assert store.get("c1").model_id == "m-a"


def test_entry_without_any_model_keeps_empty_list(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "version": 1,
        "configs": [{
            "id": "c1", "name": "p", "base_url": "https://x/v1",
            "model_id": "", "api_key_encrypted": "",
        }],
    }), encoding="utf-8")
    store = JsonModelConfigStore(str(path))

    loaded = store.get("c1")
    assert loaded.model_ids == []
    assert loaded.model_id == ""
