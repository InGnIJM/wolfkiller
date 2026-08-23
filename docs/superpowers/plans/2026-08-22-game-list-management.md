# 首页对局列表管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 首页对局列表支持显示名重命名与删除（进行中先停引擎再清存档），对局 ID / 目录 / 回放 URL 不变。

**Architecture:** 显示名落在 `GameManifest`（`data/games/index.json` 的 `name` 字段）。`GameService` 负责默认名、改名、停引擎+删目录。`PATCH`/`DELETE /api/games/{id}` 暴露操作。首页 `GameCard` 加 ⋮ 菜单，`GameList` 持有对话框与本地列表更新。不改五个核心流水线模块。

**Tech Stack:** Python 3.11+、FastAPI、pydantic v2、pytest；前端 React + MUI + Vitest。

**规格依据:** `docs/superpowers/specs/2026-08-22-game-list-management-design.md`

**约定（每个 Task 通用）：**
- 后端测试：`cd backend` 后 `python -m pytest tests/<file> -q`；全量门禁 `python -m pytest tests --cov=app --cov-branch --cov-fail-under=100 -q`
- 前端测试：`cd frontend` 后 `npm test`
- 禁止修改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py`（守卫 blob 门禁）
- commit message 格式 `<type>(<scope>): <summary>`

---

## File map

| 文件 | 职责 |
|---|---|
| `backend/app/services/game_manifest.py` | `default_game_name`、条目 `name`、`get_entry`、`remove_game` |
| `backend/app/services/game_service.py` | 创建时写入默认名；`get_display_name` / `rename_game` / `delete_game` |
| `backend/app/api/websocket/ws_handler.py` | `WSManager.close_game` |
| `backend/app/api/schemas.py` | `GameListItem.name`、`RenameGameRequest` |
| `backend/app/api/routes/game_routes.py` | 列表带 name；`PATCH`/`DELETE` |
| `frontend/src/store/types.ts` | `GameListItem.name` |
| `frontend/src/api/client.ts` | `renameGame` / `deleteGame` |
| `frontend/src/components/lobby/GameCard.tsx` | 显示名 + ⋮ 菜单 |
| `frontend/src/components/lobby/GameList.tsx` | 重命名/删除对话框与列表更新 |
| `frontend/vite.config.ts` | coverage include 加上 `GameCard.tsx` |

---

### Task 1: Manifest 显示名、默认名、删除条目

**Files:**
- Modify: `backend/app/services/game_manifest.py`
- Test: `backend/tests/test_game_service.py`（已有 `TestGameService` 旁的 manifest 测试）

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_game_service.py` 的 `test_manifest_accepts_canonical_role_counts` 附近追加：

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.services.game_manifest import GameManifest, default_game_name


def test_default_game_name_formats_shanghai_time_without_padding_month_day():
    utc = datetime(2026, 8, 22, 13, 50, tzinfo=timezone.utc)
    assert default_game_name(8, now=utc) == "8人局 · 8月22日 21:50"


def test_default_game_name_treats_naive_datetime_as_shanghai():
    naive = datetime(2026, 1, 5, 9, 5)
    assert default_game_name(9, now=naive) == "9人局 · 1月5日 09:05"


def test_manifest_stores_and_updates_name(tmp_path):
    manifest = GameManifest(str(tmp_path))
    manifest.add_game(
        "game-1",
        {"role_counts": {"wolf-killer-villager": 3}},
        name="开局名",
    )
    (tmp_path / "games" / "game-1").mkdir(parents=True)

    assert manifest.get_entry("game-1")["name"] == "开局名"
    manifest.update_game("game-1", name="新名字")
    restored = GameManifest(str(tmp_path)).load_or_rebuild()["game-1"]
    assert restored["name"] == "新名字"


def test_manifest_remove_game_drops_entry_and_persists(tmp_path):
    manifest = GameManifest(str(tmp_path))
    manifest.add_game("game-1", {"role_counts": {"wolf-killer-villager": 3}}, name="A")
    (tmp_path / "games" / "game-1").mkdir(parents=True)
    manifest.remove_game("game-1")
    manifest.remove_game("missing")

    assert manifest.get_entry("game-1") is None
    assert GameManifest(str(tmp_path)).load_or_rebuild() == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_game_service.py::test_default_game_name_formats_shanghai_time_without_padding_month_day tests/test_game_service.py::test_manifest_stores_and_updates_name tests/test_game_service.py::test_manifest_remove_game_drops_entry_and_persists -q`

Expected: FAIL（`default_game_name` / `remove_game` / `get_entry` 未定义，或 `add_game` 不接受 `name`）

- [ ] **Step 3: 最小实现**

`backend/app/services/game_manifest.py` 顶部已有 `from datetime import datetime, timezone`。追加 `ZoneInfo`：

```python
from zoneinfo import ZoneInfo
```

在 `GameManifest` 类之前加入：

```python
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def default_game_name(player_count: int, now: datetime | None = None) -> str:
    moment = datetime.now(_SHANGHAI) if now is None else now
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_SHANGHAI)
    else:
        moment = moment.astimezone(_SHANGHAI)
    return f"{player_count}人局 · {moment.month}月{moment.day}日 {moment:%H:%M}"
```

`add_game` 增加 `name: Optional[str] = None`，在 `entry.update({...})` 之后：

```python
        if name is not None:
            entry["name"] = name
```

`update_game` 增加 `name: Optional[str] = None`，在其它字段赋值旁：

```python
        if name is not None:
            entry["name"] = name
```

在 `update_game` 后增加：

```python
    def get_entry(self, game_id: str) -> Optional[dict]:
        return self._entries.get(game_id)

    def remove_game(self, game_id: str) -> None:
        if game_id not in self._entries:
            return
        self._entries.pop(game_id)
        self._persist()
```

空清单也要写出：把 `_persist` 末尾的 `if self._entries: self._persist()` **不要改**（那是 `load_or_rebuild`）。`remove_game` 自己调用 `_persist()`，即使 `_entries` 为空也要写 `[]`。确认 `_persist` 本身不因空 dict 跳过：

```python
    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        entries = sorted(
            self._entries.values(),
            key=lambda e: e.get("created_at") if isinstance(e.get("created_at"), str) else "",
        )
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
```

（现有实现已是这样，不要加 `if not self._entries: return`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_game_service.py::test_default_game_name_formats_shanghai_time_without_padding_month_day tests/test_game_service.py::test_default_game_name_treats_naive_datetime_as_shanghai tests/test_game_service.py::test_manifest_stores_and_updates_name tests/test_game_service.py::test_manifest_remove_game_drops_entry_and_persists tests/test_game_service.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/game_manifest.py backend/tests/test_game_service.py
git commit -m "feat(games): persist display names in the game manifest"
```

---

### Task 2: GameService 默认名、读名、改名

**Files:**
- Modify: `backend/app/services/game_service.py`
- Test: `backend/tests/test_game_service.py`

- [ ] **Step 1: 写失败测试**

```python
class TestGameDisplayName:
    def _service(self, tmp_path):
        return GameService(WSManager(), EventBus(), data_dir=str(tmp_path))

    def test_get_display_name_falls_back_to_short_id(self, tmp_path):
        service = self._service(tmp_path)
        service._games["abcd1234"] = MagicMock()
        service._manifest.add_game(
            "abcd1234", {"role_counts": {"wolf-killer-villager": 3}},
        )
        assert service.get_display_name("abcd1234") == "abcd1234"
        assert service.get_display_name("missing") == "missing"

    def test_rename_game_updates_manifest(self, tmp_path):
        service = self._service(tmp_path)
        service._games["game-1"] = MagicMock()
        service._manifest.add_game(
            "game-1", {"role_counts": {"wolf-killer-villager": 3}}, name="旧名",
        )
        service.rename_game("game-1", "新名字")
        assert service.get_display_name("game-1") == "新名字"

    def test_rename_missing_game_raises_key_error(self, tmp_path):
        service = self._service(tmp_path)
        with pytest.raises(KeyError):
            service.rename_game("missing", "名字")

    @pytest.mark.asyncio
    async def test_create_game_writes_default_name(self, tmp_path):
        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
        game_id = await service.create_game(
            num_werewolves=1, num_villagers=3, num_seers=0, num_witches=0, num_hunters=0,
        )
        name = service.get_display_name(game_id)
        assert name.startswith("4人局 · ")
        assert "月" in name and "日" in name
```

`create_game` 会启动引擎，与现有 `test_create_and_list_games` 相同。测完不要 await 整局。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_game_service.py::TestGameDisplayName -q`

Expected: FAIL（`get_display_name` / `rename_game` 不存在，或创建后无 name）

- [ ] **Step 3: 最小实现**

`game_service.py` 导入改为：

```python
from app.services.game_manifest import GameManifest, default_game_name
```

`create_game` 里 `self._manifest.add_game(...)` 改为：

```python
        self._manifest.add_game(
            game_id,
            {"role_counts": dict(config.role_counts)},
            model_snapshot=model_snapshot,
            name=default_game_name(config.total_players),
        )
```

在 `list_games` 旁加入：

```python
    def get_display_name(self, game_id: str) -> str:
        entry = self._manifest.get_entry(game_id) or {}
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return game_id[:8]

    def rename_game(self, game_id: str, name: str) -> None:
        if game_id not in self._games:
            raise KeyError(game_id)
        self._manifest.update_game(game_id, name=name)
```

短 ID 回退：`missing` 无条目时 `game_id[:8]` 仍是 `"missing"`（长度 < 8 则原样）。测试 `assert service.get_display_name("missing") == "missing"` 成立。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_game_service.py::TestGameDisplayName tests/test_game_service.py::TestGameService::test_create_and_list_games -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/game_service.py backend/tests/test_game_service.py
git commit -m "feat(games): generate and rename game display names"
```

---

### Task 3: 关闭 WebSocket 并删除对局

**Files:**
- Modify: `backend/app/api/websocket/ws_handler.py`
- Modify: `backend/app/services/game_service.py`
- Test: `backend/tests/test_ws_handler.py`、`backend/tests/test_game_service.py`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_ws_handler.py` 的 `TestWSManager` 内追加：

```python
    @pytest.mark.asyncio
    async def test_close_game_closes_sockets_and_drops_entry(self):
        mgr = WSManager()
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        await mgr.connect("game-1", ws)

        await mgr.close_game("game-1")

        ws.close.assert_awaited_once()
        assert "game-1" not in mgr._connections

    @pytest.mark.asyncio
    async def test_close_game_ignores_missing_and_close_errors(self):
        mgr = WSManager()
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock(side_effect=RuntimeError("already closed"))
        await mgr.connect("game-1", ws)

        await mgr.close_game("missing")
        await mgr.close_game("game-1")

        assert "game-1" not in mgr._connections
```

`backend/tests/test_game_service.py` 追加：

```python
class TestDeleteGame:
    def _seed(self, tmp_path, game_id="game-1", phase=GamePhase.GAME_OVER):
        service = GameService(WSManager(), EventBus(), data_dir=str(tmp_path))
        game_dir = tmp_path / "games" / game_id
        game_dir.mkdir(parents=True)
        (game_dir / "game.log").write_text("{}\n", encoding="utf-8")
        state = MagicMock()
        state.phase = phase
        state.game_id = game_id
        service._games[game_id] = state
        service._manifest.add_game(
            game_id, {"role_counts": {"wolf-killer-villager": 3}}, name="待删",
        )
        service._model_snapshots[game_id] = [{"name": "m"}]
        return service, game_dir

    @pytest.mark.asyncio
    async def test_delete_completed_game_removes_dir_and_index(self, tmp_path):
        service, game_dir = self._seed(tmp_path)
        await service.delete_game("game-1")
        assert "game-1" not in service.list_games()
        assert not game_dir.exists()
        assert service._manifest.get_entry("game-1") is None
        assert "game-1" not in service._model_snapshots

    @pytest.mark.asyncio
    async def test_delete_running_game_stops_engine_and_cancels_task(self, tmp_path):
        service, game_dir = self._seed(tmp_path, phase=GamePhase.NIGHT)
        engine = MagicMock()
        engine.stop = AsyncMock()
        service._engines["game-1"] = engine

        async def hang():
            await asyncio.sleep(3600)

        task = asyncio.create_task(hang())
        service._tasks["game-1"] = task

        await service.delete_game("game-1")

        engine.stop.assert_awaited_once()
        assert task.cancelled() or task.done()
        assert not game_dir.exists()
        assert "game-1" not in service._engines
        assert "game-1" not in service._tasks

    @pytest.mark.asyncio
    async def test_delete_missing_game_raises_key_error(self, tmp_path):
        service, _ = self._seed(tmp_path)
        with pytest.raises(KeyError):
            await service.delete_game("missing")

    @pytest.mark.asyncio
    async def test_delete_keeps_memory_when_rmtree_fails(self, tmp_path, monkeypatch):
        service, game_dir = self._seed(tmp_path)
        monkeypatch.setattr(
            "app.services.game_service.shutil.rmtree",
            lambda path: (_ for _ in ()).throw(OSError("busy")),
        )
        with pytest.raises(OSError, match="busy"):
            await service.delete_game("game-1")
        assert "game-1" in service._games
        assert game_dir.exists()

    @pytest.mark.asyncio
    async def test_delete_continues_after_engine_wait_timeout(self, tmp_path, monkeypatch):
        service, game_dir = self._seed(tmp_path, phase=GamePhase.NIGHT)
        engine = MagicMock()
        engine.stop = AsyncMock()
        service._engines["game-1"] = engine
        task = MagicMock()
        task.done.return_value = False
        service._tasks["game-1"] = task

        async def boom_wait(awaitable, timeout=None):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(asyncio, "wait_for", boom_wait)
        await service.delete_game("game-1")
        task.cancel.assert_called_once()
        assert not game_dir.exists()
        assert "game-1" not in service._games
```

`GamePhase.NIGHT` 确认存在于 `app.models.game.GamePhase`。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_ws_handler.py::TestWSManager::test_close_game_closes_sockets_and_drops_entry tests/test_game_service.py::TestDeleteGame -q`

Expected: FAIL（`close_game` / `delete_game` 不存在）

- [ ] **Step 3: 最小实现**

`ws_handler.py` 的 `WSManager`：

```python
    async def close_game(self, game_id: str) -> None:
        connections = list(self._connections.pop(game_id, []))
        for ws in connections:
            try:
                await ws.close()
            except Exception:
                logger.debug("Ignoring WebSocket close error for game %s", game_id)
```

`game_service.py` 增加 `import shutil`。在 `rename_game` 后：

```python
    async def delete_game(self, game_id: str) -> None:
        if game_id not in self._games:
            raise KeyError(game_id)
        engine = self._engines.get(game_id)
        if engine is not None:
            await engine.stop()
        task = self._tasks.get(game_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning("Engine task did not stop cleanly for %s", game_id)
        await self.ws_manager.close_game(game_id)
        game_dir = os.path.join(self.data_dir, "games", game_id)
        if os.path.exists(game_dir):
            shutil.rmtree(game_dir)
        self._games.pop(game_id, None)
        self._engines.pop(game_id, None)
        self._tasks.pop(game_id, None)
        self._model_snapshots.pop(game_id, None)
        self._manifest.remove_game(game_id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_ws_handler.py tests/test_game_service.py::TestDeleteGame tests/test_game_service.py::TestGameDisplayName -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/websocket/ws_handler.py backend/app/services/game_service.py backend/tests/test_ws_handler.py backend/tests/test_game_service.py
git commit -m "feat(games): stop engines and delete game archives"
```

---

### Task 4: 列表 DTO 与 PATCH/DELETE 路由

**Files:**
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/api/routes/game_routes.py`
- Test: `backend/tests/test_schemas.py`、`backend/tests/test_game_routes.py`

- [ ] **Step 1: 写失败测试**

`test_schemas.py` 导入增加 `RenameGameRequest`。改现有 `test_game_list_item`：

```python
    def test_game_list_item(self):
        item = GameListItem(
            game_id="abc", name="夜局", phase="night", round_number=2,
            player_count=9, alive_count=7, winner=None,
        )
        assert item.game_id == "abc"
        assert item.name == "夜局"
        assert item.phase == "night"

    def test_game_list_item_with_winner(self):
        item = GameListItem(
            game_id="abc", name="终局", phase="game_over", round_number=5,
            player_count=9, alive_count=4, winner="good",
        )
        assert item.winner == "good"

    def test_rename_game_request_strips_and_rejects_blank(self):
        assert RenameGameRequest(name="  新名字  ").name == "新名字"
        with pytest.raises(ValidationError):
            RenameGameRequest(name="   ")
        with pytest.raises(ValidationError):
            RenameGameRequest(name="x" * 51)
```

`test_game_routes.py` 的 `test_list_games_omits_missing_states` 里给 mock 加上：

```python
    service.get_display_name.side_effect = lambda gid: f"{gid}-name"
```

并断言 `response.games[0].name == "present-name"`。

同文件追加：

```python
from app.api.schemas import RenameGameRequest


@pytest.mark.asyncio
async def test_rename_game_returns_updated_list_item(monkeypatch):
    state = SimpleNamespace(
        phase=SimpleNamespace(value="night"),
        round_number=1,
        alive_players=lambda: [object()],
        win_result=None,
        players={1: object()},
    )
    service = MagicMock()
    service.get_game_state.return_value = state
    service.get_display_name.return_value = "新名字"
    monkeypatch.setattr(game_routes, "get_service", lambda: service)

    response = await game_routes.rename_game("g1", RenameGameRequest(name="新名字"))

    service.rename_game.assert_called_once_with("g1", "新名字")
    assert response.name == "新名字"
    assert response.game_id == "g1"


@pytest.mark.asyncio
async def test_rename_game_returns_404_when_missing(monkeypatch):
    service = MagicMock()
    service.rename_game.side_effect = KeyError("g1")
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.rename_game("g1", RenameGameRequest(name="新名字"))
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_game_returns_204(monkeypatch):
    service = MagicMock()
    service.delete_game = AsyncMock()
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    response = await game_routes.delete_game("g1")
    service.delete_game.assert_awaited_once_with("g1")
    assert response is None


@pytest.mark.asyncio
async def test_delete_game_returns_404_when_missing(monkeypatch):
    service = MagicMock()
    service.delete_game = AsyncMock(side_effect=KeyError("g1"))
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.delete_game("g1")
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_game_returns_500_when_archive_busy(monkeypatch):
    service = MagicMock()
    service.delete_game = AsyncMock(side_effect=OSError("busy"))
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.delete_game("g1")
    assert caught.value.status_code == 500
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_schemas.py::TestSchemas::test_game_list_item tests/test_schemas.py::TestSchemas::test_rename_game_request_strips_and_rejects_blank tests/test_game_routes.py::test_rename_game_returns_updated_list_item tests/test_game_routes.py::test_list_games_omits_missing_states -q`

Expected: FAIL（`name` 字段 / `rename_game` 路由不存在）

- [ ] **Step 3: 最小实现**

`schemas.py`：`from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator`

```python
class GameListItem(BaseModel):
    game_id: str
    name: str
    phase: str
    round_number: int
    player_count: int
    alive_count: int
    winner: Optional[str] = None


class RenameGameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped
```

`game_routes.py` 导入 `RenameGameRequest`。抽出：

```python
def _list_item(service: GameService, game_id: str, state) -> GameListItem:
    win = state.win_result.get("winning_camp") if state.win_result else None
    return GameListItem(
        game_id=game_id,
        name=service.get_display_name(game_id),
        phase=state.phase.value,
        round_number=state.round_number,
        player_count=len(state.players),
        alive_count=len(state.alive_players()),
        winner=win,
    )
```

`list_games` 的 `items.append(GameListItem(...))` 改为 `items.append(_list_item(service, g, state))`。

在 `list_games` 后、`get_game` 前加入：

```python
@router.patch("/{game_id}", response_model=GameListItem)
async def rename_game(game_id: str, req: RenameGameRequest):
    service = get_service()
    try:
        service.rename_game(game_id, req.name)
    except KeyError:
        raise HTTPException(404, "Game not found") from None
    state = service.get_game_state(game_id)
    if state is None:
        raise HTTPException(404, "Game not found")
    return _list_item(service, game_id, state)


@router.delete("/{game_id}", status_code=204)
async def delete_game(game_id: str):
    service = get_service()
    try:
        await service.delete_game(game_id)
    except KeyError:
        raise HTTPException(404, "Game not found") from None
    except OSError as error:
        raise HTTPException(500, "Failed to delete game archive") from error
```

`rename_game` 成功后 `get_game_state` 为 None 的分支：补一条测试以免覆盖率缺口：

```python
@pytest.mark.asyncio
async def test_rename_game_returns_404_when_state_vanishes(monkeypatch):
    service = MagicMock()
    service.get_game_state.return_value = None
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    with pytest.raises(HTTPException) as caught:
        await game_routes.rename_game("g1", RenameGameRequest(name="新名字"))
    assert caught.value.status_code == 404
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_schemas.py tests/test_game_routes.py tests/test_game_service.py::TestGameDisplayName tests/test_game_service.py::TestDeleteGame -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/schemas.py backend/app/api/routes/game_routes.py backend/tests/test_schemas.py backend/tests/test_game_routes.py
git commit -m "feat(api): add game rename and delete endpoints"
```

---

### Task 5: 前端类型与 API client

**Files:**
- Modify: `frontend/src/store/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/test/client.test.ts`
- Modify: `frontend/src/components/lobby/test/GameList.test.tsx`（`game()` helper 补 `name`，避免类型报错）

- [ ] **Step 1: 写失败测试**

`frontend/src/api/test/client.test.ts` 增加导入 `renameGame, deleteGame`，在 `createGame` 测试旁：

```typescript
  it('renames a game with PATCH', async () => {
    const fetchFn = mockFetch({ game_id: 'g1', name: '新名字' });
    const result = await renameGame('g1', '新名字');
    expect(result).toEqual({ game_id: 'g1', name: '新名字' });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe(`${BASE}/api/games/g1`);
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body)).toEqual({ name: '新名字' });
  });

  it('deletes a game', async () => {
    const fetchFn = mockFetch({}, true, 204);
    await deleteGame('g1');
    expect(fetchFn.mock.calls[0][0]).toBe(`${BASE}/api/games/g1`);
    expect(fetchFn.mock.calls[0][1].method).toBe('DELETE');
  });

  it('rejects failed rename and delete', async () => {
    mockFetch({}, false, 400);
    await expect(renameGame('g1', 'x')).rejects.toThrow('Rename game failed: 400');
    mockFetch({}, false, 500);
    await expect(deleteGame('g1')).rejects.toThrow('Delete game failed: 500');
  });
```

`GameList.test.tsx` 的 `game()` 增加 `name: `${id}-name``。

- [ ] **Step 2: 跑测试确认失败**

Run: `npm test -- src/api/test/client.test.ts`

Expected: FAIL（`renameGame` / `deleteGame` 未导出）

- [ ] **Step 3: 最小实现**

`types.ts` 的 `GameListItem`：

```typescript
export interface GameListItem {
  game_id: string;
  name: string;
  phase: GamePhase;
  round_number: number;
  player_count: number;
  alive_count: number;
  winner: WinningCamp | null;
}
```

`client.ts`：

```typescript
export async function renameGame(gameId: string, name: string): Promise<GameListItem> {
  const res = await fetch(`${getApiBase()}/api/games/${gameId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`Rename game failed: ${res.status}`);
  return res.json();
}

export async function deleteGame(gameId: string): Promise<void> {
  const res = await fetch(`${getApiBase()}/api/games/${gameId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Delete game failed: ${res.status}`);
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `npm test -- src/api/test/client.test.ts src/components/lobby/test/GameList.test.tsx`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/store/types.ts frontend/src/api/client.ts frontend/src/api/test/client.test.ts frontend/src/components/lobby/test/GameList.test.tsx
git commit -m "feat(web): add rename and delete game API client"
```

---

### Task 6: GameCard 显示名与操作菜单

**Files:**
- Create: `frontend/src/components/lobby/test/GameCard.test.tsx`
- Modify: `frontend/src/components/lobby/GameCard.tsx`
- Modify: `frontend/vite.config.ts`（coverage include 加入 `src/components/lobby/GameCard.tsx`）

- [ ] **Step 1: 写失败测试**

```tsx
// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';

import GameCard from '../GameCard';

afterEach(() => cleanup());

function renderCard(overrides: Partial<Parameters<typeof GameCard>[0]> = {}) {
  const onClick = vi.fn();
  const onRename = vi.fn();
  const onDelete = vi.fn();
  render(
    <GameCard
      gameId="abcd1234"
      name="8人局 · 8月22日 21:50"
      phase="黑夜"
      roundNumber={2}
      playerCount={8}
      aliveCount={7}
      winner={null}
      onClick={onClick}
      onRename={onRename}
      onDelete={onDelete}
      {...overrides}
    />,
  );
  return { onClick, onRename, onDelete };
}

describe('GameCard', () => {
  it('shows the display name instead of the short id', () => {
    renderCard();
    expect(screen.getByText('8人局 · 8月22日 21:50')).toBeInTheDocument();
    expect(screen.queryByText('abcd1234')).not.toBeInTheDocument();
  });

  it('enters the game when the card body is clicked', () => {
    const { onClick } = renderCard();
    fireEvent.click(screen.getByText('8人局 · 8月22日 21:50'));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('does not enter the game when rename or delete is chosen', () => {
    const { onClick, onRename, onDelete } = renderCard();
    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    fireEvent.click(screen.getByRole('menuitem', { name: '重命名' }));
    expect(onRename).toHaveBeenCalledOnce();
    expect(onClick).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    fireEvent.click(screen.getByRole('menuitem', { name: '删除' }));
    expect(onDelete).toHaveBeenCalledOnce();
    expect(onClick).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npm test -- src/components/lobby/test/GameCard.test.tsx`

Expected: FAIL（无 `name` / `onRename` props，无「对局操作」按钮）

- [ ] **Step 3: 最小实现**

`GameCard.tsx` 改为：标题用 `name`；`CardActionArea` 只包左侧内容；右侧 `IconButton` 放在 `CardActionArea` **外面**，避免误触发进入。

```tsx
import { useState } from 'react';
import { Card, CardActionArea, Typography, Chip, Box, IconButton, Menu, MenuItem } from '@mui/material';
import GroupsIcon from '@mui/icons-material/Groups';
import MoreVertIcon from '@mui/icons-material/MoreVert';

interface Props {
  gameId: string;
  name: string;
  phase: string;
  roundNumber: number;
  playerCount: number;
  aliveCount: number;
  winner: string | null;
  onClick: () => void;
  onRename: () => void;
  onDelete: () => void;
}

const WINNER_META: Record<string, { label: string; color: 'success' | 'error' }> = {
  good: { label: '好人胜', color: 'success' },
  werewolf: { label: '狼人胜', color: 'error' },
};

const PHASE_LABELS: Record<string, string> = {
  error: '异常终止',
};

export default function GameCard({
  name, phase, roundNumber, playerCount, aliveCount, winner, onClick, onRename, onDelete,
}: Props) {
  const win = winner ? WINNER_META[winner] : null;
  const [menuEl, setMenuEl] = useState<null | HTMLElement>(null);

  return (
    <Card variant="outlined" sx={{
      transition: 'background-color 0.2s, border-color 0.2s',
      '&:hover': { bgcolor: 'action.hover', borderColor: 'primary.main' },
    }}>
      <Box sx={{ display: 'flex', alignItems: 'stretch' }}>
        <CardActionArea onClick={onClick} sx={{ p: 0, flex: 1 }}>
          <Box sx={{ px: 2.5, py: 2 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <Box>
                <Typography variant="subtitle1" sx={{ fontWeight: 500, fontSize: '0.9rem' }}>
                  {name}
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3 }}>
                  {PHASE_LABELS[phase] ?? phase} · 第 {roundNumber} 轮
                </Typography>
              </Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <GroupsIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
                  <Typography variant="body2" color="text.secondary">
                    {aliveCount}/{playerCount}
                  </Typography>
                </Box>
                {win && (
                  <Chip
                    label={win.label}
                    color={win.color}
                    size="small"
                    variant="outlined"
                    sx={{ fontWeight: 500, height: 24, fontSize: '0.7rem' }}
                  />
                )}
              </Box>
            </Box>
          </Box>
        </CardActionArea>
        <Box sx={{ display: 'flex', alignItems: 'center', pr: 1 }}>
          <IconButton
            aria-label="对局操作"
            onClick={(event) => setMenuEl(event.currentTarget)}
          >
            <MoreVertIcon />
          </IconButton>
          <Menu
            anchorEl={menuEl}
            open={Boolean(menuEl)}
            onClose={() => setMenuEl(null)}
          >
            <MenuItem onClick={() => { setMenuEl(null); onRename(); }}>重命名</MenuItem>
            <MenuItem onClick={() => { setMenuEl(null); onDelete(); }}>删除</MenuItem>
          </Menu>
        </Box>
      </Box>
    </Card>
  );
}
```

`gameId` 留在 Props 里给列表 `key` 用，组件内不展示。若 ESLint 报未使用，从解构中省略即可。

`vite.config.ts` 的 `coverage.include` 增加 `'src/components/lobby/GameCard.tsx'`。

`GameList.tsx` 此刻还没传 `name`/`onRename`/`onDelete`，Task 7 再改。若 Task 6 单独跑前端全量测试，`GameList.tsx` 会类型失败：本 Task 同步给 `GameList` 传占位回调（`onRename={() => undefined}` 等）和 `name={g.game_id}`，Task 7 换成真逻辑。更干净的做法：本 Task 只改 GameCard + 测试，GameList 暂不编译进 `tsc` 的 `npm test`（Vitest 不跑 tsc）。`npm run build` 才会类型检查。因此 Task 6 可以先不改 GameList，Task 7 一起改。

- [ ] **Step 4: 跑测试确认通过**

Run: `npm test -- src/components/lobby/test/GameCard.test.tsx src/components/lobby/test/GameList.test.tsx`

Expected: PASS（GameList 仍 mock GameCard）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/lobby/GameCard.tsx frontend/src/components/lobby/test/GameCard.test.tsx frontend/vite.config.ts
git commit -m "feat(web): show game names and overflow actions on cards"
```

---

### Task 7: GameList 重命名/删除对话框

**Files:**
- Modify: `frontend/src/components/lobby/GameList.tsx`
- Modify: `frontend/src/components/lobby/test/GameList.test.tsx`

- [ ] **Step 1: 写失败测试**

更新 `GameList.test.tsx` 的 mock 与 `game()`（`name` 已在 Task 5 加上）。mock 改为暴露操作按钮：

```tsx
vi.mock('../../../api/client', () => ({
  listGames: vi.fn(),
  renameGame: vi.fn(),
  deleteGame: vi.fn(),
}));

vi.mock('../GameCard', () => ({
  default: ({
    gameId,
    name,
    phase,
    onClick,
    onRename,
    onDelete,
  }: {
    gameId: string;
    name: string;
    phase: string;
    onClick: () => void;
    onRename: () => void;
    onDelete: () => void;
  }) => (
    <div>
      <button data-testid={`game-${gameId}`} data-phase={phase} onClick={onClick}>
        {name}
      </button>
      <button data-testid={`rename-${gameId}`} onClick={onRename}>rename</button>
      <button data-testid={`delete-${gameId}`} onClick={onDelete}>delete</button>
    </div>
  ),
}));
```

导入 `renameGame, deleteGame`。追加（可放在新 `describe('GameList management')`）：

```tsx
import { waitFor } from '@testing-library/react';
import { renameGame, deleteGame } from '../../../api/client';

  it('renames a game from the dialog and updates the card title', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1')] });
    vi.mocked(renameGame).mockResolvedValue({ ...game('game-1'), name: '新名字' });
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());

    fireEvent.click(screen.getByTestId('rename-game-1'));
    const input = screen.getByLabelText('对局名称');
    fireEvent.change(input, { target: { value: '新名字' } });
    fireEvent.click(screen.getByRole('button', { name: '确定' }));

    await waitFor(() => expect(renameGame).toHaveBeenCalledWith('game-1', '新名字'));
    expect(screen.getByTestId('game-game-1')).toHaveTextContent('新名字');
  });

  it('keeps the rename dialog open when the name is blank', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1')] });
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('rename-game-1'));
    fireEvent.change(screen.getByLabelText('对局名称'), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    expect(renameGame).not.toHaveBeenCalled();
    expect(screen.getByLabelText('对局名称')).toBeInTheDocument();
  });

  it('shows rename errors in the dialog', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1')] });
    vi.mocked(renameGame).mockRejectedValue(new Error('Rename game failed: 400'));
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('rename-game-1'));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    await waitFor(() => expect(screen.getByText(/Rename game failed: 400/)).toBeInTheDocument());
    expect(screen.getByLabelText('对局名称')).toBeInTheDocument();
  });

  it('warns when deleting an in-progress game and removes it after confirm', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1', 'night')] });
    vi.mocked(deleteGame).mockResolvedValue(undefined);
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('delete-game-1'));
    expect(screen.getByText(/对局正在进行，删除将立即中断/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(deleteGame).toHaveBeenCalledWith('game-1'));
    expect(screen.queryByTestId('game-game-1')).not.toBeInTheDocument();
  });

  it('does not warn when deleting a finished or error game', async () => {
    vi.mocked(listGames).mockResolvedValue({
      games: [game('done', 'game_over'), game('bad', 'error')],
    });
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('delete-done'));
    expect(screen.queryByText(/对局正在进行/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    fireEvent.click(screen.getByTestId('delete-bad'));
    expect(screen.queryByText(/对局正在进行/)).not.toBeInTheDocument();
  });

  it('keeps the delete dialog open when delete fails', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1', 'game_over')] });
    vi.mocked(deleteGame).mockRejectedValue(new Error('Delete game failed: 500'));
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('delete-game-1'));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(screen.getByText(/Delete game failed: 500/)).toBeInTheDocument());
    expect(screen.getByTestId('game-game-1')).toBeInTheDocument();
  });
```

`game()` 的 `name` 已是 `` `${id}-name` ``。删除确认框的主按钮文案用「删除」，避免和菜单项在未 mock 时冲突；mock 后页面上确认框的 `name: '删除'` 即可。

- [ ] **Step 2: 跑测试确认失败**

Run: `npm test -- src/components/lobby/test/GameList.test.tsx`

Expected: FAIL（无对话框 / 未调 `renameGame`）

- [ ] **Step 3: 最小实现**

`GameList.tsx` 用 `GameListItem` 替换本地 `GameInfo`，并从 `../../api/client` 导入 `listGames, renameGame, deleteGame`。增加 MUI `Dialog, DialogTitle, DialogContent, DialogActions, TextField, Alert`。

```tsx
const [games, setGames] = useState<GameListItem[]>([]);
const [renameTarget, setRenameTarget] = useState<GameListItem | null>(null);
const [renameValue, setRenameValue] = useState('');
const [renameError, setRenameError] = useState('');
const [deleteTarget, setDeleteTarget] = useState<GameListItem | null>(null);
const [deleteError, setDeleteError] = useState('');

const openRename = (g: GameListItem) => {
  setRenameTarget(g);
  setRenameValue(g.name);
  setRenameError('');
};

const submitRename = async () => {
  if (renameTarget === null) return;
  const name = renameValue.trim();
  if (!name) {
    setRenameError('名称不能为空');
    return;
  }
  try {
    const updated = await renameGame(renameTarget.game_id, name);
    setGames((prev) => prev.map((item) => (
      item.game_id === updated.game_id ? { ...item, ...updated } : item
    )));
    setRenameTarget(null);
  } catch (error) {
    setRenameError(error instanceof Error ? error.message : String(error));
  }
};

const submitDelete = async () => {
  if (deleteTarget === null) return;
  try {
    await deleteGame(deleteTarget.game_id);
    setGames((prev) => prev.filter((item) => item.game_id !== deleteTarget.game_id));
    setDeleteTarget(null);
  } catch (error) {
    setDeleteError(error instanceof Error ? error.message : String(error));
  }
};

const inProgress = deleteTarget !== null
  && deleteTarget.phase !== 'game_over'
  && deleteTarget.phase !== 'error';
```

`GameCard` 增加 `name={g.name} onRename={() => openRename(g)} onDelete={() => { setDeleteTarget(g); setDeleteError(''); }}`。

重命名对话框：`TextField label="对局名称"`，按钮「取消」「确定」。删除对话框正文：`确定删除「{deleteTarget.name}」？此操作不可恢复。`；`inProgress` 时再显示「对局正在进行，删除将立即中断。」；按钮「取消」「删除」。

- [ ] **Step 4: 跑测试确认通过**

Run: `npm test -- src/components/lobby/test/GameList.test.tsx src/components/lobby/test/GameCard.test.tsx src/api/test/client.test.ts`

Expected: PASS

再跑：

```bash
cd frontend && npm test && npm run build
cd backend && python -m pytest tests --cov=app --cov-branch --cov-fail-under=100 -q
```

Expected: 前端测试通过、`tsc` 通过；后端覆盖率 100%。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/lobby/GameList.tsx frontend/src/components/lobby/test/GameList.test.tsx
git commit -m "feat(web): rename and delete games from the home list"
```

---

## 自检（对照规格）

| 规格项 | 任务 |
|---|---|
| `index.json` 的 `name`；50 字；去空格；不要求唯一 | Task 1 / 4 |
| 默认名 `N人局 · M月D日 HH:mm`（上海，月日不补零） | Task 1 / 2 |
| 旧档回退短 ID，不回写 | Task 2 |
| `GET` 带 `name`；`PATCH` 200/400/404；`DELETE` 204/404/500 | Task 4 |
| 删进行中：stop → cancel（5s）→ close WS → rmtree → 清内存/manifest | Task 3 |
| 目录删失败保持列表原样 | Task 3 / 4 |
| 首页卡片显示名 + ⋮；对话框；进行中额外警告；成功后立刻更新列表 | Task 6 / 7 |
| 不改五个核心 blob 模块 | 全程 |
