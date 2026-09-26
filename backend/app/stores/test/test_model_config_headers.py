import json

from app.stores.model_config_store import JsonModelConfigStore, ModelConfig


def test_new_config_defaults_to_no_custom_headers():
    assert ModelConfig.new("n", "https://example.test/v1", "m").headers == {}


def test_headers_round_trip_through_the_store(tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    config = ModelConfig.new(
        "Zen", "https://opencode.ai/zen/v1", "model",
        headers={"x-opencode-request": "user-1"},
    )

    store.upsert(config)

    assert store.get(config.id).headers == {"x-opencode-request": "user-1"}


def test_headers_are_persisted_verbatim(tmp_path):
    path = tmp_path / "models.json"
    store = JsonModelConfigStore(str(path))
    config = ModelConfig.new(
        "Zen", "https://opencode.ai/zen/v1", "model",
        headers={"x-opencode-session": "ses_pinned"},
    )

    store.upsert(config)

    stored = json.loads(path.read_text(encoding="utf-8"))["configs"][0]
    assert stored["headers"] == {"x-opencode-session": "ses_pinned"}


def test_old_json_without_headers_loads_as_empty(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"version": 1, "configs": [{
        "id": "old", "name": "Old", "base_url": "https://openrouter.ai/api/v1",
        "model_id": "model", "created_at": "t", "updated_at": "t",
    }]}), encoding="utf-8")

    assert JsonModelConfigStore(str(path)).get("old").headers == {}


def test_old_entry_is_not_mutated_when_default_headers_are_added(tmp_path, monkeypatch):
    entry = {
        "id": "old", "name": "Old", "base_url": "https://example.com",
        "model_id": "model", "created_at": "t", "updated_at": "t",
    }
    raw = {"version": 1, "configs": [entry]}
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    monkeypatch.setattr(store, "_read", lambda: raw)

    assert store.list_all()[0].headers == {}

    assert "headers" not in raw["configs"][0]


def test_two_configs_keep_independent_headers(tmp_path):
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    first = ModelConfig.new("A", "https://a.test/v1", "m", headers={"x-a": "1"})
    second = ModelConfig.new("B", "https://b.test/v1", "m", headers={"x-b": "2"})

    store.upsert(first)
    store.upsert(second)

    assert store.get(first.id).headers == {"x-a": "1"}
    assert store.get(second.id).headers == {"x-b": "2"}
