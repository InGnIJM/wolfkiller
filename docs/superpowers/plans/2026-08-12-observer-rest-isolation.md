# 旁观者 REST 数据隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让无认证旁观者只能通过 REST 获得公开详情与公开回放，且身份永不公开。

**Architecture:** 后端在游戏详情路由边界从 `GameState.get_public_state()` 构造封闭 DTO，并在日志路由边界对白名单事件投影。前端以新的公开类型消费响应，绝不直接使用内部状态或原始日志。

**Tech Stack:** FastAPI、Pydantic、pytest、React、TypeScript、Vitest。

---

## 文件结构

- 修改：`backend/app/api/routes/games.py` — 公开详情和日志响应边界。
- 修改：`backend/app/models/game_state.py` — 若当前公开状态缺少必要公开字段，补足显式投影。
- 修改：`backend/tests/test_game_routes.py`（先检查现有路由测试位置）— REST 隔离契约测试。
- 修改：`frontend/src/services/api.ts` — 公开 DTO/时间线请求类型。
- 修改：`frontend/src/store/gameStore.ts`、`frontend/src/components/game/GameBoard.tsx`、`frontend/src/types/game.ts` — 只消费公开数据。
- 修改：现有 `frontend/src/**/test/*` 中相应测试 — 前端契约测试。

### Task 1: 定义并验证公开 REST 投影

**Files:**
- Modify: `backend/tests/test_game_routes.py`（或现有 games 路由测试文件）
- Modify: `backend/app/api/routes/games.py`
- Modify: `backend/app/models/game_state.py`（仅在公开投影缺字段时）

- [ ] **Step 1: 写失败的详情与日志隔离测试**

```python
def assert_private_keys_absent(value):
    text = json.dumps(value, ensure_ascii=False)
    for key in ("role", "role_id", "camp", "faction", "resources", "role_init", "night_action", "thought"):
        assert f'"{key}"' not in text

def test_game_detail_is_public_and_unknown_game_is_404(client, seeded_game):
    assert_private_keys_absent(client.get(f"/api/games/{seeded_game.id}").json())
    assert client.get("/api/games/missing").status_code == 404

def test_logs_project_public_events_and_unknown_game_is_404(client, seeded_game):
    response = client.get(f"/api/games/{seeded_game.id}/logs")
    assert {event["type"] for event in response.json()} <= {"speech", "vote", "death", "phase", "winner"}
    assert_private_keys_absent(response.json())
    assert client.get("/api/games/missing/logs").status_code == 404
```

- [ ] **Step 2: 运行并确认失败**

Run: `cd backend; pytest tests/test_game_routes.py -q`  
Expected: FAIL，当前响应泄露私有字段或缺少白名单投影。

- [ ] **Step 3: 实现封闭 DTO 与事件白名单**

在 `games.py` 中添加只接受 `state.get_public_state()` 字段的响应模型/序列化函数；不能通过 `model_dump()`、`__dict__` 或原始存档转发。添加日志投影函数，只返回 `speech`、`vote`、`death`、`phase`、`winner` 的明确字段；任何其他类型返回 `None` 并过滤。路由先查游戏，不存在时 `HTTPException(status_code=404)`。

```python
def project_public_event(event: dict) -> dict | None:
    if event.get("type") not in {"speech", "vote", "death", "phase", "winner"}:
        return None
    return {key: event[key] for key in PUBLIC_EVENT_FIELDS[event["type"]] if key in event}
```

- [ ] **Step 4: 验证后端契约**

Run: `cd backend; pytest tests/test_game_routes.py -q`  
Expected: PASS。

- [ ] **Step 5: 提交后端变更**

```text
git add backend/app/api/routes/games.py backend/app/models/game_state.py backend/tests/test_game_routes.py
git commit -m "fix(api): isolate observer game data"
```

### Task 2: 将前端切换至公开契约

**Files:**
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/types/game.ts`
- Modify: `frontend/src/store/gameStore.ts`、`frontend/src/components/game/GameBoard.tsx`（分两个不超过三文件的提交）
- Test: 现有 `frontend/src/**/test/*` 对应 store/component 测试

- [ ] **Step 1: 写失败的前端公开数据测试**

```ts
it('renders public timeline without role_init or role fields', () => {
  render(<GameBoard game={publicGame} events={[{ type: 'death', seat: 2 }]} />)
  expect(screen.queryByText(/狼人|预言家|女巫/)).not.toBeInTheDocument()
})
```

- [ ] **Step 2: 运行并确认失败**

Run: `cd frontend; npm test -- --run`  
Expected: FAIL，现有类型或组件仍读取私有字段。

- [ ] **Step 3: 定义公开 TypeScript 类型并删除私有读取**

`api.ts` 和 `types/game.ts` 仅声明公开座位、阶段、轮次、公开状态与五种公开事件。 `gameStore.ts` 与 `GameBoard.tsx` 只保存/渲染这些类型；删除 `role_init` 及角色、阵营、资源、夜间字段的读取和基于死亡/结算的身份展示。

```ts
export type PublicTimelineEvent =
  | { type: 'speech'; seat: number; content: string }
  | { type: 'vote'; seat: number; targetSeat: number | null }
  | { type: 'death'; seat: number }
  | { type: 'phase'; phase: string }
  | { type: 'winner'; winner: string }
```

- [ ] **Step 4: 验证前端与禁止消费点**

Run: `cd frontend; npm test -- --run; npm run typecheck`  
Expected: PASS。

Run: `rg -n "role_init|\\.role\\b|\\.camp\\b|\\.resources\\b|night_action" frontend/src`  
Expected: 不存在前端旁观者数据消费；保留的纯类型名须人工确认不来自 API 响应。

- [ ] **Step 5: 分批提交前端变更**

```text
git add frontend/src/services/api.ts frontend/src/types/game.ts frontend/src/store/gameStore.ts
git commit -m "fix(frontend): consume public game state"
git add frontend/src/components/game/GameBoard.tsx frontend/src/**/test/*
git commit -m "test(frontend): verify public game rendering"
```

### Task 3: 端到端回归与审查

**Files:** 无新增实现文件；仅在失败时按所属 Task 修复并单独提交。

- [ ] **Step 1: 运行后端完整测试与覆盖率**

Run: `cd backend; pytest --cov=app --cov-report=term-missing`  
Expected: 全部通过；本次修改模块 100% 覆盖。

- [ ] **Step 2: 运行前端完整验证**

Run: `cd frontend; npm test -- --run; npm run typecheck; npm run build`  
Expected: 全部通过。

- [ ] **Step 3: 做泄露回归检查**

使用测试夹具访问详情和日志，递归断言响应没有 `role`、`camp`、`faction`、`resources`、`role_init`、`night`、`thought`、`conversation` 字段或身份值；确认未知局两路由都是 404。

- [ ] **Step 4: 提交任何回归测试修正**

```text
git add <at-most-three-related-files>
git commit -m "test(api): cover observer isolation regression"
```

## 自检

- 设计中的“身份永不公开”由 Task 1 的封闭 DTO、日志事件白名单和 Task 2 的无身份渲染共同保证。
- 两条 404、详情/日志私有字段、公开回放、前端 `role_init` 消费均有独立测试或搜索验证。
- 无待填占位符；所有提交每次最多暂列三项实现文件（测试与实现按相关性拆分）。
