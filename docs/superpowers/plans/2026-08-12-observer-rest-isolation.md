# 旁观者 REST 数据隔离实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development`（推荐）或 `executing-plans`，逐任务执行并在每个任务后复审。步骤以 checkbox 记录。

**Goal:** 让未认证旁观者只能从 REST 与前端回放获得公开游戏事实，身份永不公开。

**Architecture:** `GameState.get_public_state()` 是详情数据的唯一业务来源；`backend/app/api/schemas.py` 定义封闭公开 DTO，`backend/app/api/routes/game_routes.py` 逐字段投影。日志路由把两份 JSONL 转为统一的公开事件 allowlist，前端仅消费这些 DTO。

**Tech Stack:** Python、FastAPI、Pydantic、pytest、React、TypeScript、Zustand、Vite。

---

## 实际文件地图

| 路径 | 责任 |
| --- | --- |
| `backend/app/api/schemas.py` | 公开详情、公开回放事件与日志响应的 Pydantic 契约。 |
| `backend/app/api/routes/game_routes.py` | 详情封闭投影、JSONL 读取、日志 allowlist、双接口 404。 |
| `backend/tests/test_schemas.py` | DTO 字段与拒绝私有字段的单元测试。 |
| `backend/tests/test_game_routes.py` | 详情、日志、未知局和投影器的路由测试。 |
| `frontend/src/store/types.ts` | 公共玩家、详情、回放事件及夜间进度类型。 |
| `frontend/src/api/client.ts` | `fetchGameDetail`、`fetchGameLogs` 的返回类型。 |
| `frontend/src/api/websocket.ts` | 只消费公开夜间进度。 |
| `frontend/src/store/gameStore.ts` | 从公开详情和公开事件重建页面状态。 |
| `frontend/src/components/game/GameBoard.tsx` | 加载公开详情和公开回放。 |
| `frontend/src/components/game/SeatMap.tsx`、`PlayerCard.tsx`、`CenterDisplay.tsx`、`WinOverlay.tsx` | 删除身份、阵营、夜间行动和结算身份展示。 |

本项目 `frontend/package.json` 当前没有测试脚本或测试框架；前端本轮的自动门禁是 `npm run build`（TypeScript 全量检查）与明确的 `rg` 隐私消费扫描。不要为了本次隔离临时引入测试框架或修改依赖。

### Task 1: 先定义并测试后端公开 DTO

**Files:**

- Modify: `backend/app/api/schemas.py`
- Modify: `backend/tests/test_schemas.py`

- [ ] **Step 1: 写出失败的 DTO 测试**

在 `backend/tests/test_schemas.py` 添加公开玩家、公开详情与公开日志事件测试。测试必须构造 `GameDetailResponse`，并断言序列化后玩家仅有座位、存活和警长字段；同时验证包含 `role` 的玩家输入因 `extra="forbid"` 被拒绝。

```python
from pydantic import ValidationError
from app.api.schemas import PublicPlayerResponse


def test_public_player_rejects_role_fields():
    with pytest.raises(ValidationError):
        PublicPlayerResponse(
            seat_number=1, is_alive=True, is_sheriff=False,
            role="wolf-killer-werewolf",
        )
```

- [ ] **Step 2: 运行红灯测试**

Run: `python -m pytest tests/test_schemas.py -q`（工作目录：`backend`）
Expected: FAIL，原因是公开响应模型尚未定义或未禁止额外私有字段。

- [ ] **Step 3: 实现封闭响应模型**

在 `backend/app/api/schemas.py` 定义 `PublicPlayerResponse`、`PublicSpeechResponse`、`PublicDeathResponse`、`PublicReplayEvent`、`GameDetailResponse` 和 `GameLogsResponse`。所有公开模型使用 Pydantic 的 `extra="forbid"`；`GameLogsResponse` 只暴露 `game_id` 与 `events`，不再保留 `conversations` 或 `operations`。

`PublicReplayEvent` 使用 discriminated union 或等效的显式事件模型，字段遵守设计文档表格。不要使用 `dict`、`Any` 或 `extra="allow"` 作为 wire contract。

- [ ] **Step 4: 运行 DTO 覆盖率**

Run: `python -m pytest tests/test_schemas.py --cov=app.api.schemas --cov-report=term-missing -q`（工作目录：`backend`）
Expected: PASS；`app.api.schemas` 本次新增分支为 100%。

- [ ] **Step 5: 提交 DTO 边界**

```bash
git add backend/app/api/schemas.py backend/tests/test_schemas.py
git commit -m "fix(api): define public observer dto"
```

### Task 2: 测试并实现详情与公开日志投影

**Files:**

- Modify: `backend/app/api/routes/game_routes.py`
- Modify: `backend/tests/test_game_routes.py`

- [ ] **Step 1: 写失败的路由/投影测试**

在 `backend/tests/test_game_routes.py` 建立含角色、阵营、药剂、枪与公开字段的 `SimpleNamespace` 状态。通过 mock `get_service()` 调用 `get_game()`，递归检查响应结构只包含 DTO 已声明字段。新增日志测试：mock `_read_jsonl()` 返回 public 与 private conversation，以及 `role_init`、`werewolf_kill`、`vote`、`night_deaths`、`game_over`、未知 operation；只断言 allowlist 事件进入 `events`。

```python
@pytest.mark.asyncio
async def test_logs_return_only_public_replay_events(monkeypatch):
    service = MagicMock()
    service.get_game_state.return_value = SimpleNamespace(game_id="game-a")
    monkeypatch.setattr(game_routes, "get_service", lambda: service)
    monkeypatch.setattr(game_routes, "_read_jsonl", MagicMock(side_effect=[
        [{"scope": "public", "speaker_role": "wolf-killer-werewolf",
          "speaker_seat": 1, "content": "发言", "round_number": 1,
          "phase": "speech", "timestamp": "t"},
         {"scope": "werewolf", "content": "夜聊", "timestamp": "t"}],
        [{"operation": "role_init", "data": {"players": {"1": {"role": "x"}}}},
         {"operation": "vote", "round": 1, "seat": 1,
          "data": {"target": 2}, "timestamp": "v"}],
    ]))

    response = await game_routes.get_game_logs("game-a")

    assert [event.type for event in response.events] == ["speech", "vote"]
    assert response.events[0].model_dump() == {
        "type": "speech", "timestamp": "t", "round_number": 1,
        "phase": "speech", "speaker_seat": 1, "content": "发言",
    }
```

另加两个测试：`get_game("missing")` 与 `get_game_logs("missing")` 都抛出 404，后者在 404 前不调用 `_read_jsonl()`；以及已死亡、`win_result` 非空的详情仍无角色字段。

- [ ] **Step 2: 运行红灯测试**

Run: `python -m pytest tests/test_game_routes.py -q`（工作目录：`backend`）
Expected: FAIL，当前详情直接包含角色/资源，当前日志原样返回两份 JSONL，且日志未知局不会 404。

- [ ] **Step 3: 实现封闭详情投影与日志 allowlist**

在 `backend/app/api/routes/game_routes.py`：

1. `get_game()` 调用一次 `state.get_public_state()`，仅以 `game_id`、`phase`、`round_number`、`players`、`sheriff`、`speeches`、`death_history`、`win_result` 构造 `GameDetailResponse`；不能读取 `state.players` 的 `role`、`camp`、资源字段。
2. 让 `get_game_logs()` 先调用 `get_service().get_game_state(game_id)`；无状态立即 `HTTPException(404, "Game not found")`。
3. 添加私有纯函数，将 public conversation 与允许的 operation 映射为 `PublicReplayEvent`；只从设计表中列出的字段取值，私有/未知记录返回 `None`。`night_deaths` 展开为多个 `death` 事件。
4. 合并两个来源后按 `timestamp` 排序，返回 `GameLogsResponse(game_id=game_id, events=events)`。缺少必需公开字段的损坏记录直接丢弃，不能以私有数据补全。

- [ ] **Step 4: 运行路由回归与覆盖率**

Run: `python -m pytest tests/test_game_routes.py tests/test_schemas.py --cov=app.api.routes.game_routes --cov=app.api.schemas --cov-report=term-missing -q`（工作目录：`backend`）
Expected: PASS；本次路由投影的成功、私密过滤、未知过滤、损坏记录、两条 404 路径均有覆盖。

- [ ] **Step 5: 提交 REST 隔离**

```bash
git add backend/app/api/routes/game_routes.py backend/tests/test_game_routes.py
git commit -m "fix(api): isolate observer rest data"
```

### Task 3: 将前端迁移到公开契约

**Files:**

- Modify: `frontend/src/store/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/websocket.ts`
- Modify: `frontend/src/store/gameStore.ts`
- Modify: `frontend/src/components/game/GameBoard.tsx`
- Modify: `frontend/src/components/game/SeatMap.tsx`
- Modify: `frontend/src/components/game/PlayerCard.tsx`
- Modify: `frontend/src/components/game/CenterDisplay.tsx`
- Modify: `frontend/src/components/game/WinOverlay.tsx`

- [ ] **Step 1: 先收紧 TypeScript 契约并确认红灯**

在 `frontend/src/store/types.ts` 将 `PlayerFullState` 替换为只含 `seat_number`、`is_alive`、`is_sheriff` 的 `PublicPlayerState`；定义与后端一致的 `PublicGameDetail`、`PublicReplayEvent`、`PublicGameLogs`。把 `NightSubstepData` 缩为 `step` 与 `roundNumber`。在 `frontend/src/api/client.ts` 的两个 fetch 函数改用这些类型。

Run: `npm run build`（工作目录：`frontend`）
Expected: FAIL；现有 store、WebSocket 和组件仍访问 `role`、`camp`、资源、私密 night 字段或旧的 `conversations`/`operations`。

- [ ] **Step 2: 迁移数据层与公开夜间消息**

在 `frontend/src/api/websocket.ts`，`night_substep` 只传递 `step`、`round_number`；在 `frontend/src/store/gameStore.ts`，从 `detail.players` 初始化公开座位，按 `PublicReplayEvent` 构造 timeline，删除 `role_init`、`speaker_role`、`visible_to`、`werewolf_kill`、女巫、预言家、猎人等私密分支。`GameBoard.tsx` 继续先加载详情再加载日志，但只传入新的公开响应。

提交一（不超过三个文件）：

```bash
git add frontend/src/store/types.ts frontend/src/api/client.ts frontend/src/api/websocket.ts
git commit -m "fix(frontend): define public observer contracts"
```

提交二（不超过三个文件）：

```bash
git add frontend/src/store/gameStore.ts frontend/src/components/game/GameBoard.tsx frontend/src/components/game/SeatMap.tsx
git commit -m "fix(frontend): replay public game events"
```

- [ ] **Step 3: 迁移所有直接展示私密字段的组件**

在 `PlayerCard.tsx` 删除阵营背景、角色图标、狼人爪标记与角色 fallback；在 `CenterDisplay.tsx` 删除狼人频道、夜间情报、thought 与夜间 action 分支，只渲染公开 speech、vote、vote_result、death、phase、winner；在 `WinOverlay.tsx` 删除身份揭晓列表，仅显示胜负与回放控制。

```bash
git add frontend/src/components/game/PlayerCard.tsx frontend/src/components/game/CenterDisplay.tsx frontend/src/components/game/WinOverlay.tsx
git commit -m "fix(frontend): hide observer private identity"
```

- [ ] **Step 4: 运行前端契约门禁**

Run: `npm run build`（工作目录：`frontend`）
Expected: PASS。

Run: `rg -n "role_init|speaker_role|visible_to|werewolf_kill|witch_save|witch_poison|seer_check|hunter_shoot|\.role\\b|\.camp\\b|has_antidote|has_poison|has_gun|highlight_seats|action_seat|wolf_kill_target" frontend/src/api frontend/src/store frontend/src/components/game`
Expected: 对旁观者数据消费路径无匹配；若命中，必须逐个移除或证明其不在旁观者路径后再继续。

### Task 4: 完整验证与范围审计

**Files:** 仅当验证发现缺陷时，新增最多三个逻辑相关文件的修复提交。

- [ ] **Step 1: 后端完整验证**

Run: `python -m pytest tests --cov=app --cov-report=term-missing`（工作目录：`backend`）
Expected: 全部通过。记录实际全仓覆盖率；不得把未达到的仓库基线报告为 100%，但 Tasks 1–2 新增分支必须 100%。

- [ ] **Step 2: 前端构建与隐私回归**

Run: `npm run build`（工作目录：`frontend`）
Expected: PASS。

再次运行 Task 3 的 `rg` 命令，并人工检查 `frontend/src/components/game/WinOverlay.tsx` 不含身份列表、`backend/app/api/routes/game_routes.py` 不直接读取 `state.players` 私有字段。

- [ ] **Step 3: 审计变更范围**

Run: `git diff HEAD~5..HEAD -- backend/app/api backend/tests frontend/src docs`
Expected: 仅包含此计划列出的 REST、测试与旁观者前端迁移文件；不修改认证、私有玩家视图、角色规则、日志写入格式或 WebSocket 服务端路由。

- [ ] **Step 4: 报告可复核证据**

报告每条命令的实际测试数量、覆盖率和 build 输出；若发现泄露或类型回归，回到对应任务执行红绿重构，并以独立的 `fix(...)` 或 `test(...)` 提交修复。

## 自检

- 设计与计划只引用当前真实路径：`backend/app/api/routes/game_routes.py`、`backend/app/api/schemas.py`、`backend/tests/test_game_routes.py`、`backend/tests/test_schemas.py`、`frontend/src/api/client.ts`、`frontend/src/store/types.ts`、`frontend/src/store/gameStore.ts` 与实际游戏组件。
- 不含不存在的路由、状态模型或前端 API 路径，也没有占位路径。
- 每个提交最多三个文件；后端测试先写失败断言再实现。
- 旁观者模式、身份永不公开、封闭详情 DTO、公开日志 allowlist、双接口 404 与前端不再重建私密身份均有对应任务和验证命令。
