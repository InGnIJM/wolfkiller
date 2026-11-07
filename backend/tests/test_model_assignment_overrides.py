"""Per-assignment model_id / reasoning_effort overrides.

A saved config pins the *provider* (base_url, key, profile); the model id
and thinking effort can be re-picked per assignment group and per vote
model at game-creation time, so one OpenCode Go config can serve
``deepseek-v4.1-flash`` seats and ``mimo`` seats in the same game.
"""

import pytest

import app.services.game_service as service_module
from app.agents.llm_client import env_default_client_config
from app.services.game_service import resolve_model_assignments
from app.stores.model_config_store import JsonModelConfigStore, ModelConfig


def _store_with(tmp_path, monkeypatch, **fields):
    values = dict(
        name="Go", base_url="https://opencode.ai/zen/go/v1",
        model_id="deepseek-v4.1-flash", provider_profile="opencode-go",
    )
    values.update(fields)
    cfg = ModelConfig.new(**values)
    store = JsonModelConfigStore(str(tmp_path / "models.json"))
    store.upsert(cfg)
    monkeypatch.setattr(service_module, "get_model_config_store", lambda: store)
    return cfg


def test_same_config_twice_with_different_model_ids(tmp_path, monkeypatch):
    cfg = _store_with(tmp_path, monkeypatch)

    seat_configs, snapshot = resolve_model_assignments(
        [
            {"config_id": cfg.id, "count": 2, "model_id": "mimo-v2.6-flash"},
            {"config_id": cfg.id, "count": 1, "model_id": "glm-5.3-flash"},
        ],
        3,
        shuffle=lambda values: None,
    )

    assert [seat_configs[s].model_id for s in (1, 2, 3)] == [
        "mimo-v2.6-flash", "mimo-v2.6-flash", "glm-5.3-flash",
    ]
    # Every seat keeps the provider's own endpoint/key/profile.
    assert all(
        seat_configs[s].base_url == "https://opencode.ai/zen/go/v1"
        and seat_configs[s].provider_profile == "opencode-go"
        for s in (1, 2, 3)
    )
    assert [item["model_id"] for item in snapshot] == [
        "mimo-v2.6-flash", "glm-5.3-flash",
    ]


def test_reasoning_effort_overrides_the_stored_value(tmp_path, monkeypatch):
    cfg = _store_with(tmp_path, monkeypatch, reasoning_effort="high")

    seat_configs, _ = resolve_model_assignments(
        [{"config_id": cfg.id, "count": 1, "reasoning_effort": "none"}],
        1, shuffle=lambda values: None,
    )

    assert seat_configs[1].reasoning_effort == "none"


def test_unset_override_keeps_the_stored_effort(tmp_path, monkeypatch):
    cfg = _store_with(tmp_path, monkeypatch, reasoning_effort="low")

    seat_configs, _ = resolve_model_assignments(
        [{"config_id": cfg.id, "count": 1}], 1, shuffle=lambda values: None,
    )

    assert seat_configs[1].reasoning_effort == "low"


def test_env_entry_accepts_overrides(monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "high")
    env = env_default_client_config()

    seat_configs, snapshot = resolve_model_assignments(
        [
            {
                "config_id": None, "count": 1,
                "model_id": "override-model", "reasoning_effort": "none",
            },
        ],
        1, shuffle=lambda values: None,
    )

    assert seat_configs[1].model_id == "override-model"
    assert seat_configs[1].reasoning_effort == "none"
    assert snapshot[0]["model_id"] == "override-model"
    assert env.model_id != "override-model"


def test_identical_duplicate_entries_still_raise(tmp_path, monkeypatch):
    cfg = _store_with(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="unique"):
        resolve_model_assignments(
            [
                {"config_id": cfg.id, "count": 1, "model_id": "m-a"},
                {"config_id": cfg.id, "count": 1, "model_id": "m-a"},
            ],
            2,
        )


def test_different_effort_makes_entries_distinct(tmp_path, monkeypatch):
    cfg = _store_with(tmp_path, monkeypatch)

    seat_configs, _ = resolve_model_assignments(
        [
            {"config_id": cfg.id, "count": 1, "reasoning_effort": "none"},
            {"config_id": cfg.id, "count": 1, "reasoning_effort": "high"},
        ],
        2, shuffle=lambda values: None,
    )

    assert seat_configs[1].reasoning_effort == "none"
    assert seat_configs[2].reasoning_effort == "high"


@pytest.mark.parametrize("model_id", ["", "   ", 7, ["x"]])
def test_model_id_must_be_a_non_empty_string(tmp_path, monkeypatch, model_id):
    cfg = _store_with(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="model_id"):
        resolve_model_assignments(
            [{"config_id": cfg.id, "count": 1, "model_id": model_id}], 1,
        )


@pytest.mark.parametrize("effort", ["bogus", 3, ["low"]])
def test_reasoning_effort_must_be_a_known_value(
    tmp_path, monkeypatch, effort,
):
    cfg = _store_with(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="reasoning_effort"):
        resolve_model_assignments(
            [{"config_id": cfg.id, "count": 1, "reasoning_effort": effort}], 1,
        )


def test_assignment_schema_rejects_blank_model_id() -> None:
    from app.api.model_schemas import ModelAssignment

    with pytest.raises(Exception):
        ModelAssignment(config_id="m1", count=1, model_id="   ")


def test_assignment_schema_strips_model_id_whitespace() -> None:
    from app.api.model_schemas import ModelAssignment

    entry = ModelAssignment(config_id="m1", count=1, model_id="  deepseek-v4.1-flash  ")

    assert entry.model_id == "deepseek-v4.1-flash"
