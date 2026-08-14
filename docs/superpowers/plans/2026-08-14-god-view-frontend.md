# 上帝视角 + 夜晚直播前端展示 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 观众（用户）以上帝视角观看：座位卡显示身份与阵营、夜晚按"旁白页→狼讨论→狼投票→女巫思考/行动→预言家思考/查验→天亮"实时推进（复用 3 秒轮询直播）、狼讨论以聊天形式展示、记忆面板完整展示每人私有信息与信息差。

**Architecture:** 后端已在 Plan A 产出有序事件流（narration/wolf_chat_message/wolf_vote/witch_thought/seer_thought/night_action/death）并落盘 game.log；本计划只做两件事：① 后端公开 DTO 给观众暴露 `role`/`camp`（Agent 侧投影不变，隐私边界仍对局内 Agent 生效）；② 前端消费新事件类型做直播与上帝视角渲染。直播走现有 3 秒轮询（GameBoard 已实现），WS 不动。

**Tech Stack:** TypeScript + React + MUI（沿用现有 theme）+ Zustand 5 + Vitest（57 tests 保持全绿）+ ESLint 平面配置 + `npm run build` 类型检查。

---

## 文件结构

- 修改 `backend/app/models/game.py` — `get_public_state` 玩家项加 `role`/`camp`
- 修改 `backend/app/api/schemas.py` — 详情响应玩家 schema 加 `role`/`camp`
- 修改 `backend/tests/test_schemas.py`、隐私扫描测试 — 同步新公开字段（观众字段白名单）
- 修改 `frontend/src/store/types.ts` — 新事件类型 + 玩家身份字段
- 修改 `frontend/src/store/gameStore.ts` — deriveState 处理新事件
- 修改 `frontend/src/components/game/CenterDisplay.tsx` — 旁白页/讨论/投票/思考渲染
- 修改 `frontend/src/components/game/HistoryPanel.tsx` — 新事件卡片 + 记忆完整化
- 修改 `frontend/src/components/game/SeatMap.tsx` — 身份徽标
- 测试：`frontend/src/store/test/gameStore.test.ts`、`frontend/src/components/game/test/*.test.tsx` 同步 + 新用例

---

### Task 1: 后端公开 DTO 暴露观众身份

**Files:**
- Modify: `backend/app/models/game.py:150-167`、`backend/app/api/schemas.py`
- Test: `backend/tests/test_schemas.py`（隐私扫描断言同步）

- [ ] **Step 1: 写失败测试**

```python
def test_public_state_exposes_viewer_roles_and_camps() -> None:
    state = GameState(game_id="g", players={1: PlayerState(1, "wolf-killer-werewolf", "werewolf")})
    public = state.get_public_state()
    assert public["players"]["1"]["role"] == "wolf-killer-werewolf"
    assert public["players"]["1"]["camp"] == "werewolf"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_schemas.py -q`
Expected: FAIL（KeyError: 'role'）

- [ ] **Step 3: 实现**

`get_public_state` 玩家项改为：

```python
        "players": {
            s: {
                "seat_number": p.seat_number,
                "is_alive": p.is_alive,
                "is_sheriff": p.is_sheriff,
                "role": p.role,
                "camp": p.camp,
            }
            for s, p in self.players.items()
        },
```

`schemas.py` 详情玩家 schema 加两个必填 str 字段。隐私扫描测试白名单中把 `role`/`camp` 加入"观众可见"集合（Agent 视角的 `role_init/night_intel/check_results/has_antidote/has_poison/has_gun/visible_to` 仍禁止——边界只对局内 Agent 生效，不推翻）。

- [ ] **Step 4: 运行确认通过 + 全量覆盖**

Run: `python -m pytest tests --cov=app --cov-branch --cov-fail-under=100 -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/models/game.py backend/app/api/schemas.py backend/tests/test_schemas.py
git commit -m "feat(api): expose viewer roles and camps in public state"
```

---

### Task 2: 前端类型扩展

**Files:**
- Modify: `frontend/src/store/types.ts`

- [ ] **Step 1: 失败测试**（types 无运行时测试，由 build 类型检查把关；先写消费方测试见 Task 3）
- [ ] **Step 2: 实现**

```typescript
export interface PublicPlayerState {
  seat_number: number;
  is_alive: boolean;
  is_sheriff: boolean;
  role?: string;
  camp?: string;
}

export interface NarrationPayload { round_number: number; title: string; text: string; }
export interface WolfChatMessagePayload { round_number: number; seat: number; text: string; }
export interface WolfVotePayload { round_number: number; seat: number; target_seat: number | null; reasoning: string; }
export interface NightThoughtPayload { round_number: number; seat: number; text: string; }

export type PublicReplayEvent =
  | { event_type: 'narration'; payload: NarrationPayload }
  | { event_type: 'wolf_chat_message'; payload: WolfChatMessagePayload }
  | { event_type: 'wolf_vote'; payload: WolfVotePayload }
  | { event_type: 'witch_thought' | 'seer_thought'; payload: NightThoughtPayload }
  | { event_type: 'night_action'; payload: NightActionRecord }
  | { event_type: 'death'; payload: DeathRecord }
  | { event_type: 'speech'; payload: SpeechRecord }
  | { event_type: 'vote'; payload: VoteRecord }
  | { event_type: 'vote_result'; payload: { round_number: number; exiled_seat: number | null } }
  | { event_type: 'phase'; payload: { phase: GamePhase; round_number: number } }
  | { event_type: 'winner'; payload: WinResult };
```

（删除旧 `wolf_chat` 与 `night_thought` 事件类型，后端已不再产出。）

- [ ] **Step 3: 提交**

```bash
git add frontend/src/store/types.ts
git commit -m "feat(store): add god-view and staged night event types"
```

---

### Task 3: gameStore 处理新事件

**Files:**
- Modify: `frontend/src/store/gameStore.ts`
- Test: `frontend/src/store/test/gameStore.test.ts`

- [ ] **Step 1: 写失败测试**

```typescript
it('keeps night phase and round across staged night events', () => {
  const logs: GameLogs = { game_id: 'g', events: [
    { event_type: 'phase', payload: { phase: 'night', round_number: 1 } },
    { event_type: 'narration', payload: { round_number: 1, title: '天黑请闭眼', text: '狼人请睁眼' } },
    { event_type: 'wolf_chat_message', payload: { round_number: 1, seat: 1, text: '我怀疑2号' } },
    { event_type: 'wolf_vote', payload: { round_number: 1, seat: 1, target_seat: 2, reasoning: '像神' } },
    { event_type: 'witch_thought', payload: { round_number: 1, seat: 5, text: '考虑救人' } },
  ]};
  useGameStore.getState().loadLogs(logs);
  useGameStore.getState().seekTo(logs.events.length - 1);
  expect(useGameStore.getState().phase).toBe('night');
  expect(useGameStore.getState().roundNumber).toBe(1);
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run src/store/test/gameStore.test.ts`
Expected: FAIL（类型不匹配 / switch 无分支）

- [ ] **Step 3: 实现**：`deriveState` 的 switch 增加四个新事件分支（只推进 `roundNumber`，不改 phase/玩家状态）；删除 `wolf_chat`/`night_thought` 旧分支。`buildInitialPlayers`/`copyPlayers` 透传 `role`/`camp`。
- [ ] **Step 4: 运行确认通过**（同 Step 2 命令 + 相关 store 测试全绿）
- [ ] **Step 5: 提交**

```bash
git add frontend/src/store/gameStore.ts frontend/src/store/test/gameStore.test.ts
git commit -m "feat(store): derive state across staged night events"
```

---

### Task 4: CenterDisplay 分阶段渲染

**Files:**
- Modify: `frontend/src/components/game/CenterDisplay.tsx`
- Test: `frontend/src/components/game/test/CenterDisplay.test.tsx`（如不存在则新建）

- [ ] **Step 1: 写失败测试**

```tsx
it('renders narrator page for narration events', () => {
  const { result } = renderHook(() => useGameStore());
  act(() => result.current.loadLogs({ game_id: 'g', events: [
    { event_type: 'narration', payload: { round_number: 1, title: '天黑请闭眼', text: '狼人请睁眼' } },
  ]}));
  act(() => result.current.seekTo(0));
  render(<CenterDisplay />);
  expect(screen.getByText('天黑请闭眼')).toBeInTheDocument();
  expect(screen.getByText('狼人请睁眼')).toBeInTheDocument();
});

it('renders wolf discussion as chat bubbles', () => {
  // timeline: wolf_chat_message 两条 → 断言两条气泡文本 "1号：我怀疑2号"
});
```

- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**：`PublicEventContent` 增加分支：
  - `narration`：全屏居中大字号标题 + 正文（`PhaseContent` 同款动画）
  - `wolf_chat_message`：左对齐聊天气泡（`1号：{text}`，狼队深红描边）
  - `wolf_vote`：`{seat}号 出票 → {target_seat}号 / 弃权（{reasoning}）`
  - `witch_thought`/`seer_thought`：`女巫/预言家思考 · {seat}号：{text}`
  - 删除旧 `wolf_chat`/`night_thought` 分支
- [ ] **Step 4: 运行确认通过 + 全组件测试**
- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/game/CenterDisplay.tsx frontend/src/components/game/test/CenterDisplay.test.tsx
git commit -m "feat(ui): render staged night events in center display"
```

---

### Task 5: HistoryPanel 新卡片 + 记忆完整化

**Files:**
- Modify: `frontend/src/components/game/HistoryPanel.tsx`
- Test: `frontend/src/components/game/test/HistoryPanel.test.tsx`（如不存在则新建）

- [ ] **Step 1: 失败测试**：`narration`/`wolf_chat_message`/`wolf_vote`/`witch_thought` 卡片渲染断言；"思考"标签聚合 `wolf_chat_message`+`witch_thought`+`seer_thought`；"夜晚"标签聚合 `wolf_vote`+`night_action`+`narration`。
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**：
  - `EventCard` 增 4 个分支（旁白灰底居中、讨论气泡、投票黄框、思考蓝框）
  - 过滤逻辑：`thoughts` 改含 `wolf_chat_message`/`witch_thought`/`seer_thought`；`nightActions` 改含 `wolf_vote`/`narration`/`night_action`
  - `MemoryCard` 完整化：渲染 `action_history` 条目（`round` + `action_type` + `target`）与 `witnessed_events` 条目（`round` + `event`），不再只显示条数
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/game/HistoryPanel.tsx frontend/src/components/game/test/HistoryPanel.test.tsx
git commit -m "feat(ui): history cards for staged night and full memory detail"
```

---

### Task 6: SeatMap 身份徽标

**Files:**
- Modify: `frontend/src/components/game/SeatMap.tsx`
- Test: `frontend/src/components/game/test/SeatMap.test.tsx`

- [ ] **Step 1: 失败测试**：`role='wolf-killer-werewolf'` 的玩家渲染"狼人"徽标；`camp='good'` 边框绿色、`camp='werewolf'` 边框红色。
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**：`PublicSeat` 内加角色徽标：

```tsx
const ROLE_BADGES: Record<string, { label: string; color: string }> = {
  'wolf-killer-werewolf': { label: '狼人', color: 'error.main' },
  'wolf-killer-witch': { label: '女巫', color: 'secondary.main' },
  'wolf-killer-seer': { label: '预言家', color: 'info.main' },
  'wolf-killer-hunter': { label: '猎人', color: 'warning.main' },
  'wolf-killer-villager': { label: '村民', color: 'success.main' },
  'wolf-killer-guard': { label: '守卫', color: 'success.light' },
};
```

死亡玩家角色仍显示但降低透明度（现有 `is_alive` 样式已处理）。
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/game/SeatMap.tsx frontend/src/components/game/test/SeatMap.test.tsx
git commit -m "feat(ui): show role badges on seat map for god view"
```

---

### Task 7: 前端全量回归

- [ ] **Step 1**

Run: `cd frontend && npm test`
Expected: 全部 PASS（新增 + 既有 57+ 用例）

- [ ] **Step 2**

Run: `cd frontend && npm run lint && npm run build`
Expected: 0 error，构建成功（tsc 类型检查通过）

- [ ] **Step 3: 手动冒烟**（后端已跑 Plan A 后）：新建一局，确认直播顺序为 旁白→讨论（中文）→逐票→女巫→预言家→天亮文案；历史面板与记忆面板信息完整；回放逐帧一致。

---

## Self-Review

1. **Spec coverage**：上帝视角身份（Task 1/6）✓；信息不对等展示（Task 5 记忆完整化）✓；夜晚分步直播顺序（Task 3/4/5 消费 Plan A 事件流）✓；狼讨论聊天化（Task 4/5）✓；中文讨论展示（后端 Plan A 强制，前端直接渲染）✓。
2. **Placeholder scan**：组件测试若原文件不存在则以现有 `GameBoard.test.tsx` 的模式新建（renderHook + act 与 store 联动）。
3. **Type consistency**：事件 payload 类型定义在 Task 2，Task 3/4/5 复用；`role`/`camp` 可选字段（回放旧档无此字段时安全降级，SeatMap 用 `??` 兜底）。
