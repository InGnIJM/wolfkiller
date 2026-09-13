"""Persistence and public-schema coverage for per-seat model assignments."""

from __future__ import annotations

import json
import logging

import pytest
from pydantic import ValidationError

from app import config as config_module
from app.api.model_schemas import ModelAssignment, ModelSnapshotEntry
from app.config import LLMConfig
from app.services.game_manifest import (
    GameManifest,
    _has_known_model_assignment,
    _recover_model_assignment,
)
from app.services.game_summary import _summarize


_MODEL_SNAPSHOT = [
    {
        "config_id": None,
        "name": "环境默认 (.env)",
        "model_id": "env-model",
        "base_url": "https://env.example/v1",
        "provider_profile": "custom-openai",
        "count": 1,
        "seats": [2],
    },
    {
        "config_id": "model-a",
        "name": "Model A",
        "model_id": "provider/model-a",
        "base_url": "https://models.example/v1",
        "provider_profile": "openrouter",
        "count": 2,
        "seats": [1, 3],
    },
]


def test_model_assignment_requires_a_strict_positive_integer() -> None:
    assert ModelAssignment(config_id=None, count=1).count == 1
    for invalid in (True, 1.0, "1", 0, -1):
        with pytest.raises(ValidationError):
            ModelAssignment(config_id=None, count=invalid)


def test_model_snapshot_entry_exposes_v2_public_fields() -> None:
    entry = ModelSnapshotEntry.model_validate(_MODEL_SNAPSHOT[1])

    assert entry.count == 2
    assert entry.seats == [1, 3]
    assert "api_key" not in entry.model_dump()


def test_manifest_persists_snapshot_version_on_add_and_update(tmp_path) -> None:
    manifest = GameManifest(str(tmp_path))
    config = {"role_counts": {"wolf-killer-villager": 3}}
    manifest.add_game(
        "added",
        config,
        model_snapshot=_MODEL_SNAPSHOT,
        model_snapshot_version=2,
    )
    manifest.add_game("updated", config)
    manifest.update_game(
        "updated",
        model_snapshot=_MODEL_SNAPSHOT,
        model_snapshot_version=2,
    )
    for game_id in ("added", "updated"):
        (tmp_path / "games" / game_id).mkdir(parents=True, exist_ok=True)

    entries = GameManifest(str(tmp_path)).load_or_rebuild()

    assert entries["added"]["model_snapshot_version"] == 2
    assert entries["updated"]["model_snapshot_version"] == 2
    assert entries["added"]["model_snapshot"] == _MODEL_SNAPSHOT
    assert entries["updated"]["model_snapshot"] == _MODEL_SNAPSHOT


def test_manifest_rebuild_recovers_sanitized_v2_assignment_from_log(tmp_path) -> None:
    game_dir = tmp_path / "games" / "recovered"
    game_dir.mkdir(parents=True)
    logged_snapshot = [
        {**entry, "api_key": "must-never-be-recovered"}
        for entry in _MODEL_SNAPSHOT
    ]
    records = [
        {
            "timestamp": "t0",
            "round": 0,
            "phase": "waiting",
            "operation": "model_assignment",
            "data": {
                "model_snapshot_version": 2,
                "model_snapshot": logged_snapshot,
            },
        },
        {
            "timestamp": "t1",
            "round": 0,
            "phase": "role_deal",
            "operation": "role_init",
            "data": {
                "players": {
                    "1": {"role": "wolf-killer-villager"},
                    "2": {"role": "wolf-killer-villager"},
                    "3": {"role": "wolf-killer-villager"},
                },
            },
        },
    ]
    (game_dir / "game.log").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    entry = GameManifest(str(tmp_path)).load_or_rebuild()["recovered"]

    assert entry["model_snapshot_version"] == 2
    assert entry["model_snapshot"] == _MODEL_SNAPSHOT
    assert "api_key" not in json.dumps(entry, ensure_ascii=False)
    assert _has_known_model_assignment(entry) is True


def test_manifest_keeps_legacy_snapshot_without_migrating_it(tmp_path) -> None:
    game_dir = tmp_path / "games" / "legacy"
    game_dir.mkdir(parents=True)
    legacy_snapshot = [{"config_id": None, "name": "环境默认 (.env)"}]
    (tmp_path / "games" / "index.json").write_text(json.dumps([{
        "game_id": "legacy",
        "created_at": "t0",
        "phase": "game_over",
        "round_number": 1,
        "player_count": 3,
        "config": {"role_counts": {"wolf-killer-villager": 3}},
        "winner": "good",
        "finished_at": "t1",
        "model_snapshot": legacy_snapshot,
    }]), encoding="utf-8")
    (game_dir / "game.log").write_text(
        json.dumps({"operation": "game_over", "data": {"winner": "good"}})
        + "\n",
        encoding="utf-8",
    )

    entry = GameManifest(str(tmp_path)).load_or_rebuild()["legacy"]

    assert entry["model_snapshot"] == legacy_snapshot
    assert "model_snapshot_version" not in entry
    assert _has_known_model_assignment(entry) is False


@pytest.mark.parametrize(
    "changes",
    [
        {"model_snapshot_version": None},
        {"model_snapshot_version": True},
        {"model_snapshot": []},
        {"player_count": 0},
        {"model_snapshot": ["not-an-entry"]},
        {"model_snapshot": [{**_MODEL_SNAPSHOT[1], "count": True}]},
        {"model_snapshot": [{**_MODEL_SNAPSHOT[1], "count": 0}]},
        {"model_snapshot": [{**_MODEL_SNAPSHOT[1], "config_id": ""}]},
        {"model_snapshot": [{**_MODEL_SNAPSHOT[1], "config_id": []}]},
        {"model_snapshot": [{**_MODEL_SNAPSHOT[1], "seats": "1,3"}]},
        {"model_snapshot": [{**_MODEL_SNAPSHOT[1], "seats": [3, 1]}]},
        {"model_snapshot": [{**_MODEL_SNAPSHOT[1], "seats": [1, True]}]},
        {"model_snapshot": [{**_MODEL_SNAPSHOT[1], "seats": [1, 1]}]},
        {"model_snapshot": [{**_MODEL_SNAPSHOT[1], "seats": [1]}]},
        {"model_snapshot": [{**_MODEL_SNAPSHOT[1], "seats": [1, 4]}]},
        {
            "model_snapshot": [
                _MODEL_SNAPSHOT[0],
                {**_MODEL_SNAPSHOT[1], "config_id": None},
            ],
        },
    ],
)
def test_invalid_v2_assignment_is_marked_unknown(changes) -> None:
    entry = {
        "model_snapshot_version": 2,
        "model_snapshot": _MODEL_SNAPSHOT,
        "player_count": 3,
        **changes,
    }

    assert _has_known_model_assignment(entry) is False


def test_summary_marks_valid_v2_and_legacy_model_assignments() -> None:
    current = _summarize(
        "current",
        [],
        [],
        {
            "model_snapshot_version": 2,
            "model_snapshot": _MODEL_SNAPSHOT,
            "player_count": 3,
        },
    )
    legacy_snapshot = [{"model_id": "legacy-model"}]
    legacy = _summarize(
        "legacy", [], [], {"model_snapshot": legacy_snapshot, "player_count": 3},
    )

    assert current["model_snapshot_version"] == 2
    assert current["model_snapshot"] == _MODEL_SNAPSHOT
    assert current["model_assignment_known"] is True
    assert legacy["model_snapshot_version"] is None
    assert legacy["model_snapshot"] == legacy_snapshot
    assert legacy["model_assignment_known"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"model_snapshot_version": 2, "model_snapshot": "not-a-list"},
        {"model_snapshot_version": 2, "model_snapshot": []},
        {"model_snapshot_version": 2, "model_snapshot": ["not-an-entry"]},
        {
            "model_snapshot_version": 2,
            "model_snapshot": [{**_MODEL_SNAPSHOT[1], "config_id": []}],
        },
        {
            "model_snapshot_version": 2,
            "model_snapshot": [{**_MODEL_SNAPSHOT[1], "config_id": ""}],
        },
        {
            "model_snapshot_version": 2,
            "model_snapshot": [{**_MODEL_SNAPSHOT[1], "name": None}],
        },
        {
            "model_snapshot_version": 2,
            "model_snapshot": [{**_MODEL_SNAPSHOT[1], "name": ""}],
        },
        {
            "model_snapshot_version": 2,
            "model_snapshot": [{**_MODEL_SNAPSHOT[1], "count": 0}],
        },
    ],
)
def test_model_assignment_log_recovery_rejects_malformed_payloads(payload) -> None:
    assert _recover_model_assignment(payload) is None


def test_manifest_ignores_malformed_model_assignment_log_record(tmp_path) -> None:
    game_dir = tmp_path / "games" / "malformed"
    game_dir.mkdir(parents=True)
    (game_dir / "game.log").write_text(json.dumps({
        "operation": "model_assignment",
        "data": {"model_snapshot_version": 2, "model_snapshot": []},
    }) + "\n", encoding="utf-8")

    entry = GameManifest(str(tmp_path)).load_or_rebuild()["malformed"]

    assert "model_snapshot" not in entry
    assert "model_snapshot_version" not in entry


def test_llm_models_uses_first_nonempty_value_and_warns_once(
    monkeypatch, caplog,
) -> None:
    monkeypatch.setenv("LLM_MODELS", " , alpha, , beta, gamma ")
    monkeypatch.setattr(config_module, "_llm_models_deprecation_warned", False)

    with caplog.at_level(logging.WARNING, logger="app.config"):
        assert LLMConfig().models == ["alpha"]
        assert LLMConfig().models == ["alpha"]

    records = [record for record in caplog.records if "LLM_MODELS" in record.message]
    assert len(records) == 1
    assert "first non-empty value" in records[0].message


def test_llm_models_single_or_blank_compatibility_values_do_not_warn(
    monkeypatch, caplog,
) -> None:
    monkeypatch.setattr(config_module, "_llm_models_deprecation_warned", False)
    with caplog.at_level(logging.WARNING, logger="app.config"):
        monkeypatch.setenv("LLM_MODELS", " , only-model, ")
        assert LLMConfig().models == ["only-model"]
        monkeypatch.setenv("LLM_MODELS", " , , ")
        monkeypatch.setenv("LLM_MODEL", "fallback-model")
        assert LLMConfig().models == ["fallback-model"]

    assert not [record for record in caplog.records if "LLM_MODELS" in record.message]
