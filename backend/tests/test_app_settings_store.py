"""Application-level settings store (``data/settings.json``).

The operator switches live in their own file rather than in ``models.json``:
that file holds model credentials, and a settings entry would surface as a
selectable model in ``GET /api/models``.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.stores import app_settings_store as store_module
from app.stores.app_settings_store import (
    APP_SETTINGS_PATH_ENV,
    SETTINGS_FILENAME,
    JsonAppSettingsStore,
    get_app_settings_store,
)


def _boom(*args, **kwargs):
    raise OSError("disk is on fire")


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """Every test decides its own store path; the module singleton is global."""
    monkeypatch.setattr(store_module, "_store", None)
    yield
    monkeypatch.setattr(store_module, "_store", None)


# ── 读写 ────────────────────────────────────────────────────────


def test_missing_file_reads_as_unset_without_a_warning(tmp_path, caplog):
    store = JsonAppSettingsStore(str(tmp_path / "settings.json"))

    with caplog.at_level(logging.WARNING):
        assert store.get_prompt_capture_enabled() is None

    # 全新安装就是这个状态；在这里告警等于每局都在日志里吓人一跳。
    assert caplog.records == []


def test_unreadable_file_warns_and_still_reads_as_unset(tmp_path, caplog):
    path = tmp_path / "settings.json"
    path.mkdir()  # 目录冒充文件：OSError 但不是「还没建文件」

    with caplog.at_level(logging.WARNING):
        assert JsonAppSettingsStore(str(path)).get_prompt_capture_enabled() is None

    assert caplog.records


def test_roundtrip_true_then_false_lands_on_disk(tmp_path):
    path = tmp_path / "settings.json"
    store = JsonAppSettingsStore(str(path))

    store.set_prompt_capture_enabled(True)
    assert store.get_prompt_capture_enabled() is True
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1, "settings": {"prompt_capture_enabled": True},
    }

    store.set_prompt_capture_enabled(False)
    assert store.get_prompt_capture_enabled() is False
    assert json.loads(path.read_text(encoding="utf-8"))["settings"] == {
        "prompt_capture_enabled": False,
    }


def test_set_none_clears_the_key_but_keeps_the_file(tmp_path):
    path = tmp_path / "settings.json"
    store = JsonAppSettingsStore(str(path))
    store.set_prompt_capture_enabled(True)

    store.set_prompt_capture_enabled(None)

    assert store.get_prompt_capture_enabled() is None
    assert json.loads(path.read_text(encoding="utf-8"))["settings"] == {}


def test_set_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "nested" / "deeper" / "settings.json"

    JsonAppSettingsStore(str(path)).set_prompt_capture_enabled(True)

    assert path.exists()


def test_a_later_write_keeps_unrelated_keys(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"version": 1, "settings": {"someone_elses_key": "keep me"}}),
        encoding="utf-8",
    )
    store = JsonAppSettingsStore(str(path))

    store.set_prompt_capture_enabled(True)

    assert json.loads(path.read_text(encoding="utf-8"))["settings"] == {
        "someone_elses_key": "keep me", "prompt_capture_enabled": True,
    }


# ── 损坏文件：一律回落「未设置」，永不对调用方抛异常 ──────────────


@pytest.mark.parametrize(
    "payload",
    [
        "{not json at all",                                   # JSONDecodeError
        "[1, 2, 3]",                                          # 根不是对象
        '{"version": 1, "settings": [1, 2]}',                 # settings 不是对象
    ],
)
def test_corrupt_or_misshapen_files_fall_back_to_unset(tmp_path, caplog, payload):
    path = tmp_path / "settings.json"
    path.write_text(payload, encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert JsonAppSettingsStore(str(path)).get_prompt_capture_enabled() is None

    assert caplog.records, "shape problems must warn instead of passing silently"


@pytest.mark.parametrize("value", ["true", 1, 0, [], {}])
def test_non_boolean_values_are_treated_as_unset(tmp_path, value):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"version": 1, "settings": {"prompt_capture_enabled": value}}),
        encoding="utf-8",
    )

    assert JsonAppSettingsStore(str(path)).get_prompt_capture_enabled() is None


def test_write_failure_is_swallowed_and_reports_unset(tmp_path, monkeypatch, caplog):
    path = tmp_path / "settings.json"
    store = JsonAppSettingsStore(str(path))
    monkeypatch.setattr(store_module.os, "replace", _boom)

    with caplog.at_level(logging.WARNING):
        store.set_prompt_capture_enabled(True)  # must not raise

    assert caplog.records
    assert store.get_prompt_capture_enabled() is None


def test_directory_creation_failure_is_swallowed(tmp_path, monkeypatch, caplog):
    store = JsonAppSettingsStore(str(tmp_path / "nested" / "settings.json"))
    monkeypatch.setattr(store_module.os, "makedirs", _boom)

    with caplog.at_level(logging.WARNING):
        store.set_prompt_capture_enabled(True)  # must not raise

    assert caplog.records


# ── 单例与路径 ──────────────────────────────────────────────────


def test_singleton_is_shared_and_path_is_overridable(tmp_path, monkeypatch):
    custom = tmp_path / "custom" / "settings.json"
    monkeypatch.setenv(APP_SETTINGS_PATH_ENV, str(custom))

    first = get_app_settings_store()
    first.set_prompt_capture_enabled(False)

    assert first is get_app_settings_store()
    assert custom.exists()
    assert first.get_prompt_capture_enabled() is False


def test_default_path_follows_the_data_directory(tmp_path, monkeypatch):
    monkeypatch.delenv(APP_SETTINGS_PATH_ENV, raising=False)
    monkeypatch.setenv("WOLFKILLER_DATA_DIR", str(tmp_path))

    get_app_settings_store().set_prompt_capture_enabled(True)

    assert (tmp_path / SETTINGS_FILENAME).exists()
