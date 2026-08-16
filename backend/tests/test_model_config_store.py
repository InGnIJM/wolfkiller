import json

import pytest

from app.stores.model_config_store import (
    JsonModelConfigStore, ModelConfig, get_model_config_store,
)


def _config(name="DeepSeek Pro", **overrides):
    base = dict(
        name=name,
        base_url="https://api.deepseek.com/v1",
        model_id="deepseek-v4-pro",
        api_key_encrypted="gAAAAA...",
        temperature=1.2,
        strict_base_url=None,
    )
    base.update(overrides)
    return ModelConfig.new(**base)


def test_new_generates_id_and_timestamps():
    cfg = _config()
    assert len(cfg.id) == 32
    assert cfg.created_at
    assert cfg.updated_at


def test_store_roundtrip_upsert_list_get_delete(tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    cfg = _config()

    store.upsert(cfg)

    assert [c.id for c in store.list_all()] == [cfg.id]
    loaded = store.get(cfg.id)
    assert loaded is not None
    assert loaded.name == "DeepSeek Pro"
    assert loaded.api_key_encrypted == cfg.api_key_encrypted
    assert store.delete(cfg.id) is True
    assert store.list_all() == []


def test_upsert_replaces_existing_with_same_id(tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    cfg = _config()
    store.upsert(cfg)
    renamed = _config(name="Renamed")
    renamed.id = cfg.id
    store.upsert(renamed)

    configs = store.list_all()

    assert len(configs) == 1
    assert configs[0].name == "Renamed"


def test_delete_missing_returns_false(tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    assert store.delete("nope") is False


def test_missing_file_returns_empty_list(tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    assert store.list_all() == []
    assert store.get("x") is None


def test_corrupt_file_returns_empty_list(tmp_path):
    path = tmp_path / "models.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = JsonModelConfigStore(str(path))
    assert store.list_all() == []


def test_non_dict_entries_are_skipped(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"version": 1, "configs": ["junk"]}), encoding="utf-8")
    store = JsonModelConfigStore(str(path))
    assert store.list_all() == []


def test_non_dict_root_returns_empty_list(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    store = JsonModelConfigStore(str(path))
    assert store.list_all() == []


def test_persisted_file_has_version_field_and_no_tmp_left(tmp_path):
    path = tmp_path / "models.json"
    store = JsonModelConfigStore(str(path))
    store.upsert(_config())

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert len(raw["configs"]) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_factory_returns_singleton_for_env_path(tmp_path, monkeypatch):
    import app.stores.model_config_store as mod

    monkeypatch.setattr(mod, "_store", None)
    monkeypatch.setenv("MODEL_CONFIG_PATH", str(tmp_path / "custom.json"))

    first = get_model_config_store()
    second = get_model_config_store()

    assert isinstance(first, JsonModelConfigStore)
    assert first is second
    assert first._path.name == "custom.json"
