"""The prompt-capture switch endpoint.

The response is a closed set on purpose: ``enabled`` + ``source`` and nothing
else. A later "helpful" addition (game id, file path, prompt excerpt) has to
fail a test rather than leak through a settings endpoint.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.routes import settings_routes
from app.api.settings_schemas import (
    PromptCaptureSettingsRequest,
    PromptCaptureSettingsResponse,
)
from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.core.prompt_capture import PROMPT_CAPTURE_ENV, resolve_capture_setting
from app.services.game_service import GameService
from app.stores import app_settings_store as settings_store

SETTINGS_ENV = "APP_SETTINGS_PATH"


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv(SETTINGS_ENV, str(tmp_path / "settings.json"))
    monkeypatch.delenv(PROMPT_CAPTURE_ENV, raising=False)
    monkeypatch.setattr(settings_store, "_store", None)
    yield
    monkeypatch.setattr(settings_store, "_store", None)


# ── GET：三来源 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_reports_the_default_source_when_nothing_is_configured():
    payload = await settings_routes.get_prompt_capture()

    assert (payload.enabled, payload.source) == (True, "default")


@pytest.mark.asyncio
async def test_get_reports_the_environment_source(monkeypatch):
    monkeypatch.setenv(PROMPT_CAPTURE_ENV, "off")

    payload = await settings_routes.get_prompt_capture()

    assert (payload.enabled, payload.source) == (False, "env")


@pytest.mark.asyncio
async def test_get_reports_the_interface_source(monkeypatch):
    monkeypatch.setenv(PROMPT_CAPTURE_ENV, "off")  # 环境变量被界面设置短路
    settings_store.get_app_settings_store().set_prompt_capture_enabled(True)

    payload = await settings_routes.get_prompt_capture()

    assert (payload.enabled, payload.source) == (True, "ui")


# ── PATCH：三态 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_writes_the_override_and_reports_the_interface_source(monkeypatch):
    monkeypatch.setenv(PROMPT_CAPTURE_ENV, "off")

    payload = await settings_routes.update_prompt_capture(
        PromptCaptureSettingsRequest(enabled=True),
    )

    assert (payload.enabled, payload.source) == (True, "ui")
    assert settings_store.get_app_settings_store().get_prompt_capture_enabled() is True


@pytest.mark.asyncio
async def test_patch_false_is_stored_as_false_not_as_absence(monkeypatch):
    monkeypatch.setenv(PROMPT_CAPTURE_ENV, "on")

    payload = await settings_routes.update_prompt_capture(
        PromptCaptureSettingsRequest(enabled=False),
    )

    assert (payload.enabled, payload.source) == (False, "ui")
    assert settings_store.get_app_settings_store().get_prompt_capture_enabled() is False


@pytest.mark.asyncio
async def test_patch_null_and_missing_both_clear_the_override(monkeypatch):
    monkeypatch.setenv(PROMPT_CAPTURE_ENV, "off")
    store = settings_store.get_app_settings_store()
    store.set_prompt_capture_enabled(True)

    cleared = await settings_routes.update_prompt_capture(
        PromptCaptureSettingsRequest(enabled=None),
    )
    assert (cleared.enabled, cleared.source) == (False, "env")
    assert store.get_prompt_capture_enabled() is None

    store.set_prompt_capture_enabled(True)
    empty = await settings_routes.update_prompt_capture(PromptCaptureSettingsRequest())
    assert (empty.enabled, empty.source) == (False, "env")
    assert store.get_prompt_capture_enabled() is None


# ── 严格布尔与闭集 ──────────────────────────────────────────────


@pytest.mark.parametrize("value", ["yes", "true", 1, 0, [], {}])
def test_request_rejects_anything_but_a_real_boolean(value):
    # 非严格模式会把 "yes" / 1 静默转成 True，等于开了一个绕过界面校验的写入口。
    with pytest.raises(ValidationError):
        PromptCaptureSettingsRequest(enabled=value)


def test_response_has_exactly_two_keys():
    payload = PromptCaptureSettingsResponse(enabled=True, source="ui")

    assert set(payload.model_dump()) == {"enabled", "source"}


def test_response_rejects_extra_fields_and_coerced_booleans():
    with pytest.raises(ValidationError):
        PromptCaptureSettingsResponse(enabled=True, source="ui", game_id="g")
    with pytest.raises(ValidationError):
        PromptCaptureSettingsResponse(enabled=True, source="ui", max_chars=2048)
    with pytest.raises(ValidationError):
        PromptCaptureSettingsResponse(enabled=1, source="ui")
    with pytest.raises(ValidationError):
        PromptCaptureSettingsResponse(enabled=True, source="elsewhere")


# ── 唯一实现：路由与服务同源（R17） ──────────────────────────────


@pytest.mark.parametrize(
    ("stored", "env", "expected"),
    [(True, "0", (True, "ui")), (False, "1", (False, "ui")), (None, "1", (True, "env"))],
)
@pytest.mark.asyncio
async def test_route_and_game_service_resolve_through_the_same_function(
    tmp_path, monkeypatch, stored, env, expected,
) -> None:
    monkeypatch.setenv(PROMPT_CAPTURE_ENV, env)
    if stored is not None:
        settings_store.get_app_settings_store().set_prompt_capture_enabled(stored)
    service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
    try:
        payload = await settings_routes.get_prompt_capture()

        assert (payload.enabled, payload.source) == expected
        # 服务在建局时读同一个存储值，走同一个裁决函数；这里比对的是「两份
        # 代码不会各写一套优先级」。
        assert resolve_capture_setting(service._prompt_capture_setting()) == expected
    finally:
        await service.aclose()
