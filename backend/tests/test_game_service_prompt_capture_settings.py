"""The operator switch is resolved once per game — at creation, never on resume.

``llm_prompts.jsonl``'s existence is the durable half of that latch. The tests
below pin both halves, including the failure mode the plan marks P0: a game that
was interrupted *without* a corpus must not start capturing just because the
switch was flipped on while it was stopped.
"""

from __future__ import annotations

import asyncio

import pytest

from app.api.websocket.ws_handler import WSManager
from app.core.event_bus import EventBus
from app.core.prompt_capture import PROMPT_CAPTURE_FILENAME
from app.services.game_service import GameService
from app.stores import app_settings_store as settings_store

CAPTURE_ENV = "PROMPT_CAPTURE_ENABLED"
SETTINGS_ENV = "APP_SETTINGS_PATH"


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Point the settings singleton at a per-test file and clear the env switch."""
    monkeypatch.setenv(SETTINGS_ENV, str(tmp_path / "settings.json"))
    monkeypatch.delenv(CAPTURE_ENV, raising=False)
    monkeypatch.setattr(settings_store, "_store", None)
    yield
    monkeypatch.setattr(settings_store, "_store", None)


def _service(tmp_path, monkeypatch, *, stored=None, env=None) -> GameService:
    if stored is not None:
        settings_store.get_app_settings_store().set_prompt_capture_enabled(stored)
    if env is not None:
        monkeypatch.setenv(CAPTURE_ENV, env)
    return GameService(WSManager(), EventBus(), data_dir=str(tmp_path))


def _capture_file(tmp_path, game_id: str = "game"):
    return tmp_path / "games" / game_id / PROMPT_CAPTURE_FILENAME


# ── 建局时解析一次：UI > env > 默认 ─────────────────────────────


@pytest.mark.parametrize(
    ("stored", "env", "expected"),
    [
        (True, "0", True),    # 界面设置短路环境变量
        (False, "1", False),  # 反向同理
        (None, "1", True),
        (None, "0", False),
        (None, None, True),   # 内建默认：开
    ],
)
def test_a_new_game_resolves_ui_env_then_default(
    tmp_path, monkeypatch, stored, env, expected,
) -> None:
    service = _service(tmp_path, monkeypatch, stored=stored, env=env)
    try:
        service._latch_capture_request("game")

        capture = service._prompt_capture("game")

        assert (capture is not None) is expected
        assert _capture_file(tmp_path).exists() is expected
    finally:
        asyncio.run(service.aclose())


def test_flipping_the_switch_mid_game_cannot_split_the_corpus(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, stored=True)
    try:
        service._latch_capture_request("game")
        capture = service._prompt_capture("game")
        assert capture is not None

        settings_store.get_app_settings_store().set_prompt_capture_enabled(False)

        assert service._prompt_capture("game") is capture
    finally:
        asyncio.run(service.aclose())


# ── 恢复路径：不读开关，只认 marker（P0 回归网） ─────────────────


def test_a_resumed_game_never_starts_capturing_just_because_the_switch_is_on(
    tmp_path, monkeypatch,
) -> None:
    # 恢复路径不会调 _latch_capture_request —— 这条就是 R16 的回归网：
    # 存储说「开」、env 也说「开」，但这一局从来没有采集过，恢复后依然不采。
    service = _service(tmp_path, monkeypatch, stored=True, env="1")
    try:
        assert service._prompt_capture("game") is None
        assert not _capture_file(tmp_path).exists()
    finally:
        asyncio.run(service.aclose())


def test_a_resumed_game_keeps_capturing_on_the_strength_of_its_marker(
    tmp_path, monkeypatch,
) -> None:
    # 反过来也要成立：开关已经关掉，但这一局已经写过语料，恢复后继续追加。
    path = _capture_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"existing": true}\n', encoding="utf-8")
    service = _service(tmp_path, monkeypatch, stored=False)
    try:
        capture = service._prompt_capture("game")

        assert capture is not None
        capture.flush({"second": True})
        assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    finally:
        asyncio.run(service.aclose())


# ── 闩锁的生命周期 ──────────────────────────────────────────────


def test_latching_reads_the_store_at_call_time(tmp_path, monkeypatch):
    # 界面改完不需要重启后端：下一次建局现读。
    service = _service(tmp_path, monkeypatch)
    try:
        settings_store.get_app_settings_store().set_prompt_capture_enabled(True)
        service._latch_capture_request("game")
        assert service._prompt_capture("game") is not None
    finally:
        asyncio.run(service.aclose())


def test_the_latched_decision_does_not_outlive_the_handle(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, stored=True)
    try:
        service._latch_capture_request("game")
        assert service._prompt_capture("game") is not None

        service._close_prompt_capture("game")

        # 句柄与闩锁一起释放，长进程不会为跑过的每一局留一个字典项；此后
        # 唯一还能开采集的信号是磁盘上的 marker。
        assert service._capture_requests == {}
        service._close_prompt_capture("game")  # 幂等
    finally:
        asyncio.run(service.aclose())


# ── 建局路径确实会闩锁 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_game_latches_the_switch_for_the_new_game(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, stored=True)
    try:
        game_id = await service.create_game()

        assert service._prompt_capture(game_id) is not None
        assert _capture_file(tmp_path, game_id).exists()
    finally:
        await service.aclose()
