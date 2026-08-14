# 夜晚流程重构（分阶段直播 + 顺序修复 + 中文提示词）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把夜晚从"一次批量结算"重构为分阶段执行：旁白（狼人睁眼）→ 狼人讨论（轮转、可跳过、上限 3×狼数）→ 狼人投票（顺序出票、后出可见前票）→ 旁白（女巫睁眼）→ 女巫思考（独立调用）→ 女巫行动 → 旁白（预言家睁眼）→ 预言家思考（独立调用）→ 预言家查验 → 天亮旁白（平安夜/死亡名单）；每步实时落盘供前端 3 秒轮询直播，回放顺序同时修复；夜晚 LLM 提示词英文结构但强制简体中文输出。

**Architecture:** 新增引擎级编排层 `core/night_flow.py`（`NightDirector`，纯同步纯函数+注入的 LLM 调用），引擎在 async 上下文中逐阶段驱动（每轮 LLM 经 `asyncio.to_thread`），状态写入仍走 Scheduler 契约→EffectApplier 唯一写入口；每个点（投票/女巫/预言家/结算）各自经 `run_point` 执行并独立 checkpoint；讨论/思考/旁白是纯事件步骤，直接写 `game.log`（前端轮询即直播）。契约调度点拆分：狼刀→`NIGHT_WOLF_VOTE`、女巫→`NIGHT_WITCH_ACTION`、预言家→`NIGHT_SEER_ACTION`（spec schema_version 升 2；`restore_snapshot` 只校验 stored≤current，旧档兼容）。

**Tech Stack:** Python 3.11 + FastAPI + LangChain（不变）；pytest 100% 覆盖门禁（1439 tests 保持全绿）；`test_guard_extension.py` 的 `CORE_BLOBS_BEFORE_GUARD` 需随 `game_engine.py` 改动重算。

---

## 文件结构

- 新建 `backend/app/core/night_flow.py` — `NightDirector`：讨论/投票/思考的 prompt 构建、JSON 解析（含安全兜底）、旁白文案、预收集狼票存取
- 新建 `backend/tests/test_night_flow.py` — director 单元测试（100% 覆盖）
- 修改 `backend/app/models/pipeline.py` — `SchedulePoint` 加 3 个成员
- 修改 `backend/app/roles/werewolf.py` / `witch.py` / `seer.py` — 契约换点、删 reasoning 事件、schema_version=2
- 修改 `backend/app/core/game_logger.py` — `log_narration`
- 修改 `backend/app/api/routes/game_routes.py` — narration + 新观众事件投影，删除死映射
- 修改 `backend/app/services/game_service.py` — 中文强制系统提示词、注入 `NightDirector`、狼票 provider 分派
- 修改 `backend/app/core/game_engine.py` — 分阶段驱动器、`_PendingNightBatch` 扩展、`_resume_pipeline_night` 顺序修复
- 修改 `backend/tests/test_game_engine.py`（~15 个夜晚测试重写）、`test_werewolf_pipeline.py`、`test_witch_pipeline.py`、`test_seer_pipeline.py`、`test_game_routes.py`、`test_game_service.py`、`test_guard_extension.py`（blob 重算）

---

### Task 1: `SchedulePoint` 枚举扩展

**Files:**
- Modify: `backend/app/models/pipeline.py:29-34`
- Test: `backend/tests/test_pipeline_models.py`（如已有 SchedulePoint 断言则同步）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_pipeline_models.py 追加
def test_schedule_point_has_staged_night_members() -> None:
    assert SchedulePoint.NIGHT_WOLF_VOTE.value == "night_wolf_vote"
    assert SchedulePoint.NIGHT_WITCH_ACTION.value == "night_witch_action"
    assert SchedulePoint.NIGHT_SEER_ACTION.value == "night_seer_action"
    assert SchedulePoint.NIGHT_ACTION.value == "night_action"  # 守卫样例仍用
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_pipeline_models.py::test_schedule_point_has_staged_night_members -q`
Expected: FAIL（AttributeError: NIGHT_WOLF_VOTE）

- [ ] **Step 3: 最小实现**

```python
class SchedulePoint(str, Enum):
    NIGHT_ACTION = "night_action"
    NIGHT_WOLF_VOTE = "night_wolf_vote"
    NIGHT_WITCH_ACTION = "night_witch_action"
    NIGHT_SEER_ACTION = "night_seer_action"
    NIGHT_COMMIT = "night_commit"
    DAWN_REACTION = "dawn_reaction"
    DAY_ACTION = "day_action"
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_pipeline_models.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/models/pipeline.py backend/tests/test_pipeline_models.py
git commit -m "feat(pipeline): add staged night schedule points"
```

---

### Task 2: 角色契约迁移到分阶段调度点（去 reasoning 事件）

**Files:**
- Modify: `backend/app/roles/werewolf.py`、`backend/app/roles/witch.py`、`backend/app/roles/seer.py`
- Test: `backend/tests/test_werewolf_pipeline.py`、`test_witch_pipeline.py`、`test_seer_pipeline.py`

- [ ] **Step 1: 写失败测试（以狼人为例，女巫/预言家同构）**

```python
# backend/tests/test_werewolf_pipeline.py 追加
def test_werewolf_contract_moved_to_vote_point_and_keeps_kill_event_only() -> None:
    contract = next(c for c in WEREWOLF_SPEC.contracts if c.contract_id == "werewolf_kill")
    assert contract.schedule_point is SchedulePoint.NIGHT_WOLF_VOTE
    assert WEREWOLF_SPEC.schema_version == 2
    commands = (
        ActionCommand(action_type="kill", target_seat=2, reasoning="怀疑2号"),
        ActionCommand(action_type="kill", target_seat=3, reasoning="怀疑3号"),
    )
    context = ...  # 复用现有测试的 ActionContext 构造
    effects = aggregate_werewolf_votes(context, commands)
    emitted = [e.payload["event_type"] for e in effects if e.kind is EffectKind.EMIT_EVENT]
    assert emitted == ["WEREWOLF_KILL"]  # 不再有 WEREWOLF_DISCUSSION
```

女巫/预言家同构：`pass` 时 `resolve_*` 返回 `()`；行动时 EMIT_EVENT 只有 `WITCH_SAVE/WITCH_POISON`、`SEER_CHECK`；spec.schema_version == 2；契约点分别为 `NIGHT_WITCH_ACTION`/`NIGHT_SEER_ACTION`。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_werewolf_pipeline.py tests/test_witch_pipeline.py tests/test_seer_pipeline.py -q`
Expected: FAIL（点未迁移 / reasoning 事件仍在）

- [ ] **Step 3: 最小实现**

`werewolf.py`：
- `aggregate_werewolf_votes` 删除 `WEREWOLF_DISCUSSION` effect（保留 SUBMIT_DAMAGE + WEREWOLF_KILL，sort_key=(1,)/(2,)），删除 `_discussion_channel`
- `WEREWOLF_SPEC`：`schedule_point=SchedulePoint.NIGHT_WOLF_VOTE`，`schema_version=2`，`allowed_effects` 去掉 EMIT_EVENT 之外不变（保留 SUBMIT_DAMAGE + EMIT_EVENT）

`witch.py`：
- `resolve_witch_action`：`pass` → `return ()`；行动分支删除 `_witch_reasoning_effect` 及调用（保留 CONSUME_RESOURCE + SUBMIT_*/PROTECTION + WITCH_SAVE/WITCH_POISON，sort_key=(1,)/(2,)/(3,)）
- `WITCH_SPEC`：`schedule_point=SchedulePoint.NIGHT_WITCH_ACTION`，`schema_version=2`

`seer.py`：
- `resolve_seer_action`：`pass` → `return ()`；查验分支删除 `_seer_reasoning_effect`（保留 RECORD_PRIVATE_FACT + SEER_CHECK，sort_key=(1,)/(2,)）
- `SEER_SPEC`：`schedule_point=SchedulePoint.NIGHT_SEER_ACTION`，`schema_version=2`

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_werewolf_pipeline.py tests/test_witch_pipeline.py tests/test_seer_pipeline.py -q`
Expected: PASS（含既有测试中引用 reasoning 事件的断言，一并改为新预期）

- [ ] **Step 5: 提交**

```bash
git add backend/app/roles/werewolf.py backend/app/roles/witch.py backend/app/roles/seer.py backend/tests/test_werewolf_pipeline.py backend/tests/test_witch_pipeline.py backend/tests/test_seer_pipeline.py
git commit -m "refactor(roles): stage night contracts per role and drop reasoning events"
```

---

### Task 3: `NightDirector`（`core/night_flow.py`）

**Files:**
- Create: `backend/app/core/night_flow.py`
- Test: `backend/tests/test_night_flow.py`

完整实现（一次写出，随后补覆盖到 100%）：

```python
from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Optional

from app.models.game import GameState
from app.models.pipeline import ActionCommand, SchedulePoint
from app.roles.registry import RegistrySnapshot

_MAX_UTTERANCE = 200
_MAX_THOUGHT = 200

_CHINESE_DIRECTIVE = (
    "IMPORTANT: Every piece of text you produce (message, reasoning, thought) "
    "MUST be written in Simplified Chinese (简体中文)."
)

_NARRATIONS: dict[str, tuple[str, str]] = {
    "wolf_open": ("天黑请闭眼", "狼人请睁眼，开始讨论今晚的行动。"),
    "witch_open": ("女巫请睁眼", "昨晚有人被袭击。"),
    "seer_open": ("预言家请睁眼", "请查验一名玩家的身份。"),
}


def _clean(value: object, name: str, maximum: int) -> str:
    if type(value) is not str:
        raise ValueError(f"{name} must be a string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must be UTF-8") from error
    if not value or len(value) > maximum:
        raise ValueError(f"invalid {name}")
    return value


@dataclass(frozen=True)
class DiscussionTurn:
    seat: int
    spoke: bool
    text: str = ""

    def __post_init__(self) -> None:
        if type(self.seat) is not int or self.seat <= 0:
            raise ValueError("invalid seat")
        if type(self.spoke) is not bool:
            raise TypeError("spoke must be bool")
        if self.spoke:
            _clean(self.text, "text", _MAX_UTTERANCE)
        elif self.text != "":
            raise ValueError("skipped turn cannot carry text")


@dataclass(frozen=True)
class WolfVote:
    seat: int
    action_type: str
    target_seat: Optional[int]
    reasoning: str

    def __post_init__(self) -> None:
        if type(self.seat) is not int or self.seat <= 0:
            raise ValueError("invalid seat")
        if self.action_type not in ("kill", "pass"):
            raise ValueError("invalid action_type")
        if self.action_type == "kill" and (type(self.target_seat) is not int or self.target_seat <= 0):
            raise ValueError("kill requires target")
        if self.action_type == "pass" and self.target_seat is not None:
            raise ValueError("pass cannot have target")
        _clean(self.reasoning, "reasoning", 500)

    def to_command(self) -> ActionCommand:
        return ActionCommand(
            action_type=self.action_type,
            target_seat=self.target_seat,
            reasoning=self.reasoning,
        )


@dataclass(frozen=True)
class ThinkResult:
    seat: int
    text: str

    def __post_init__(self) -> None:
        if type(self.seat) is not int or self.seat <= 0:
            raise ValueError("invalid seat")
        _clean(self.text, "text", _MAX_THOUGHT)


class NightDirector:
    """Pure night-flow orchestrator: builds prompts, parses LLM answers with
    safe fallbacks, produces narration texts, and hands the engine the
    pre-collected wolf votes for the aggregate contract point."""

    def __init__(
        self,
        snapshot: RegistrySnapshot,
        invoke: Callable[[list[dict[str, str]]], str],
    ) -> None:
        if type(snapshot) is not RegistrySnapshot:
            raise TypeError("snapshot must be RegistrySnapshot")
        if not callable(invoke):
            raise TypeError("invoke must be callable")
        self._snapshot = snapshot
        self._invoke = invoke
        self._collected_votes: dict[int, WolfVote] = {}

    # ── prompts ────────────────────────────────────────────────

    @staticmethod
    def _messages(system: str, human: str) -> list[dict[str, str]]:
        return [{"role": "system", "content": system}, {"role": "user", "content": human}]

    @staticmethod
    def _alive_text(state: GameState) -> str:
        seats = sorted(state.alive_players())
        return "、".join(f"{seat}号" for seat in seats)

    def _wolf_team(self, state: GameState) -> list[int]:
        return sorted(
            seat for seat, player in state.players.items()
            if player.role == "wolf-killer-werewolf" and player.is_alive
        )

    def _history_text(self, history: Sequence[str]) -> str:
        return "\n".join(f"{index + 1}. {line}" for index, line in enumerate(history))

    def _prior_votes_text(self, votes: Sequence[WolfVote]) -> str:
        if not votes:
            return "（还没有人出票）"
        return "\n".join(
            f"{vote.seat}号：{'刀 ' + str(vote.target_seat) + ' 号' if vote.action_type == 'kill' else '弃权'}（{vote.reasoning}）"
            for vote in votes
        )

    def discussion_prompt(self, state: GameState, seat: int, history: Sequence[str]) -> list[dict[str, str]]:
        wolves = self._wolf_team(state)
        alive = self._alive_text(state)
        system = (
            f"You are seat {seat}, a werewolf in an AI Werewolf game. "
            "Discuss tonight's kill target with your teammates. You may speak "
            "or stay silent on your turn. Never reveal that you are a werewolf. "
            + _CHINESE_DIRECTIVE
        )
        human = (
            f"第{state.round_number}晚狼队讨论。你的队友：{('、'.join(str(w) for w in wolves))}号。"
            f"场上存活玩家：{alive}。\n"
            f"已进行的讨论：\n{self._history_text(history) or '（尚无发言）'}\n\n"
            '现在轮到你了。输出 JSON：{"speak": true, "text": "你的发言(≤200字)"} 表示发言，'
            '{"speak": false} 表示跳过本轮发言。'
        )
        return self._messages(system, human)

    def vote_prompt(
        self, state: GameState, seat: int,
        discussion: Sequence[str], prior_votes: Sequence[WolfVote],
    ) -> list[dict[str, str]]:
        wolves = self._wolf_team(state)
        alive = self._alive_text(state)
        system = (
            f"You are seat {seat}, a werewolf in an AI Werewolf game. "
            "Cast your kill vote. You can see the discussion and the votes cast "
            "before you. Never reveal that you are a werewolf. " + _CHINESE_DIRECTIVE
        )
        human = (
            f"第{state.round_number}晚狼队投票。你的队友：{('、'.join(str(w) for w in wolves))}号。"
            f"场上存活玩家：{alive}。\n"
            f"讨论记录：\n{self._history_text(discussion) or '（无）'}\n"
            f"已出票：\n{self._prior_votes_text(prior_votes)}\n\n"
            '输出 JSON：{"schema_version": 1, "action_type": "kill", "target_seat": 目标座位号, '
            '"reasoning": "中文理由(≤500字)"} 或 {"schema_version": 1, "action_type": "pass", '
            '"target_seat": null, "reasoning": "中文理由(≤500字)"}。'
        )
        return self._messages(system, human)

    def _think_prompt(self, state: GameState, seat: int, role_display: str, extra: str) -> list[dict[str, str]]:
        system = (
            f"You are seat {seat}, the {role_display} in an AI Werewolf game. "
            "Think out loud about tonight's decision. " + _CHINESE_DIRECTIVE
        )
        human = (
            f"第{state.round_number}晚。场上存活玩家：{self._alive_text(state)}。\n"
            f"{extra}\n"
            '输出 JSON：{"text": "你的思考(≤200字)"}。'
        )
        return self._messages(system, human)

    def witch_think_prompt(self, state: GameState, seat: int, wolf_target: Optional[int]) -> list[dict[str, str]]:
        extra = (
            f"昨夜狼人袭击了 {wolf_target} 号。" if wolf_target is not None
            else "昨夜没有袭击发生。"
        ) + "你有一瓶解药和一瓶毒药，各只能使用一次。"
        return self._think_prompt(state, seat, "Witch", extra)

    def seer_think_prompt(self, state: GameState, seat: int) -> list[dict[str, str]]:
        return self._think_prompt(state, seat, "Seer", "你每晚可以查验一名玩家的阵营。")

    # ── LLM turns（失败一律安全兜底）───────────────────────────

    def _invoke_json(self, messages: list[dict[str, str]]) -> Mapping[str, object]:
        raw = self._invoke(messages)
        if type(raw) is not str:
            raise ValueError("model response is not text")
        value = json.loads(raw)
        if not isinstance(value, Mapping):
            raise ValueError("model response is not an object")
        return value

    def wolf_discussion_turn(self, state: GameState, seat: int, history: Sequence[str]) -> DiscussionTurn:
        try:
            value = self._invoke_json(self.discussion_prompt(state, seat, history))
            if value.get("speak") is not True:
                return DiscussionTurn(seat, False)
            text = _clean(value.get("text"), "text", _MAX_UTTERANCE)
            return DiscussionTurn(seat, True, text)
        except Exception:
            return DiscussionTurn(seat, False)

    def wolf_vote_turn(
        self, state: GameState, seat: int,
        discussion: Sequence[str], prior_votes: Sequence[WolfVote],
    ) -> WolfVote:
        try:
            value = self._invoke_json(self.vote_prompt(state, seat, discussion, prior_votes))
            if value.get("action_type") == "kill":
                target = value.get("target_seat")
                if type(target) is not int or target <= 0 or target not in state.players:
                    raise ValueError("invalid kill target")
                return WolfVote(seat, "kill", target, _clean(value.get("reasoning"), "reasoning", 500))
            return WolfVote(seat, "pass", None, _clean(value.get("reasoning"), "reasoning", 500))
        except Exception:
            return WolfVote(seat, "pass", None, "safe fallback")

    def witch_think(self, state: GameState, seat: int, wolf_target: Optional[int]) -> ThinkResult | None:
        try:
            value = self._invoke_json(self.witch_think_prompt(state, seat, wolf_target))
            text = _clean(value.get("text"), "text", _MAX_THOUGHT)
            return ThinkResult(seat, text)
        except Exception:
            return None

    def seer_think(self, state: GameState, seat: int) -> ThinkResult | None:
        try:
            value = self._invoke_json(self.seer_think_prompt(state, seat))
            text = _clean(value.get("text"), "text", _MAX_THOUGHT)
            return ThinkResult(seat, text)
        except Exception:
            return None

    # ── narration ──────────────────────────────────────────────

    @staticmethod
    def narration(kind: str) -> tuple[str, str]:
        if kind not in _NARRATIONS:
            raise ValueError(f"unknown narration: {kind}")
        return _NARRATIONS[kind]

    @staticmethod
    def dawn_narration(deaths: Sequence[int]) -> tuple[str, str]:
        if not deaths:
            return ("天亮了", "昨晚是平安夜，没有人死亡。")
        seats = "、".join(f"{seat}号" for seat in sorted(deaths))
        return ("天亮了", f"昨晚 {seats} 玩家死亡。")

    # ── wolf vote hand-off（引擎 → 聚合契约 provider）──────────

    def record_votes(self, votes: Sequence[WolfVote]) -> None:
        self._collected_votes = {vote.seat: vote for vote in votes}

    def collected_vote(self, seat: int) -> ActionCommand | None:
        vote = self._collected_votes.get(seat)
        return None if vote is None else vote.to_command()
```

- [ ] **Step 1: 写失败测试（框架）**

```python
# backend/tests/test_night_flow.py（节选；覆盖到 100% 需补全部异常分支）
def test_discussion_turn_speak_skip_and_fallbacks() -> None:
    snapshot = builtin_registry.freeze()
    director = NightDirector(snapshot, lambda messages: '{"speak": true, "text": "我怀疑2号"}')
    state = _state()  # 9 玩家 GameState，round=1
    turn = director.wolf_discussion_turn(state, 1, ())
    assert turn == DiscussionTurn(1, True, "我怀疑2号")
    skip = NightDirector(snapshot, lambda messages: '{"speak": false}')
    assert skip.wolf_discussion_turn(state, 1, ()) == DiscussionTurn(1, False)
    broken = NightDirector(snapshot, lambda messages: "not json")
    assert broken.wolf_discussion_turn(state, 1, ()) == DiscussionTurn(1, False)

def test_wolf_vote_fallback_and_collection() -> None:
    ...
    director = NightDirector(snapshot, lambda messages: '{"schema_version":1,"action_type":"kill","target_seat":2,"reasoning":"像神"}')
    vote = director.wolf_vote_turn(state, 1, (), ())
    assert vote == WolfVote(1, "kill", 2, "像神")
    director.record_votes((vote,))
    command = director.collected_vote(1)
    assert command.action_type == "kill" and command.target_seat == 2

def test_narrations_and_dawn() -> None:
    assert NightDirector.narration("wolf_open")[0] == "天黑请闭眼"
    assert NightDirector.dawn_narration(()) == ("天亮了", "昨晚是平安夜，没有人死亡。")
    assert NightDirector.dawn_narration((2, 4)) == ("天亮了", "昨晚 2号、4号 玩家死亡。")

def test_chinese_directive_in_prompts() -> None:
    ...
    messages = director.discussion_prompt(state, 1, ())
    assert "简体中文" in messages[0]["content"] and "简体中文" in messages[1]["content"]
```

- [ ] **Step 2: 运行确认失败** → `python -m pytest tests/test_night_flow.py -q`（模块不存在）
- [ ] **Step 3: 写入上面的完整实现**
- [ ] **Step 4: 运行确认通过 + 覆盖率**

Run: `python -m pytest tests/test_night_flow.py --cov=app.core.night_flow --cov-branch --cov-fail-under=100 -q`
Expected: PASS, 100%

- [ ] **Step 5: 提交**

```bash
git add backend/app/core/night_flow.py backend/tests/test_night_flow.py
git commit -m "feat(core): add NightDirector for staged night flow"
```

---

### Task 4: 旁白与新观众事件的日志 + 回放投影

**Files:**
- Modify: `backend/app/core/game_logger.py`
- Modify: `backend/app/api/routes/game_routes.py`
- Test: `backend/tests/test_game_logger.py`、`backend/tests/test_game_routes.py`

- [ ] **Step 1: 写失败测试**

```python
# test_game_logger.py
def test_log_narration_writes_structured_record(tmp_path):
    logger = GameLogger(data_dir=str(tmp_path))
    logger.log_narration("g1", 1, "night", "天亮了", "昨晚是平安夜，没有人死亡。")
    record = json.loads(read_last_line(tmp_path / "games" / "g1" / "game.log"))
    assert record["operation"] == "narration" and record["phase"] == "night"
    assert record["data"] == {"title": "天亮了", "text": "昨晚是平安夜，没有人死亡。"}

# test_game_routes.py
def test_narration_and_staged_night_events_project_to_public_events():
    events = _public_replay_events([], [narration_record, wolf_message_record, wolf_vote_record, witch_thought_record])
    assert [e["event_type"] for e in events] == ["narration", "wolf_chat_message", "wolf_vote", "witch_thought"]
```

- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**

`game_logger.py`：

```python
def log_narration(self, game_id: str, round_num: int, phase: str,
                  title: str, text: str) -> None:
    """Record one narrator page shown to the audience during the night."""
    self.log_operation(game_id, "narration", round_num, phase,
                       data={"title": title, "text": text})
```

`game_routes.py`：
- `_AUDIENCE_ACTION_SCHEMAS` 增补并新增投影函数：

```python
_AUDIENCE_ACTION_SCHEMAS = {
    "WEREWOLF_KILL": frozenset({"target_seat", "vote_counts"}),
    "WITCH_SAVE": frozenset({"target_seat"}),
    "WITCH_POISON": frozenset({"target_seat"}),
    "SEER_CHECK": frozenset({"target_seat", "result"}),
    "HUNTER_SHOT": frozenset({"target_seat"}),
    "WOLF_CHAT_MESSAGE": frozenset({"seat", "text"}),
    "WOLF_VOTE": frozenset({"seat", "target_seat", "reasoning"}),
    "WITCH_THOUGHT": frozenset({"seat", "text"}),
    "SEER_THOUGHT": frozenset({"seat", "text"}),
}
```

`_public_audience_action_event` 增分支：

```python
    if event_type in ("WOLF_CHAT_MESSAGE", "WITCH_THOUGHT", "SEER_THOUGHT"):
        seat = payload.get("seat"); text = payload.get("text")
        if not _is_positive_int(seat) or not isinstance(text, str) or not text or len(text) > 200:
            return []
        public_type = {"WOLF_CHAT_MESSAGE": "wolf_chat_message",
                       "WITCH_THOUGHT": "witch_thought",
                       "SEER_THOUGHT": "seer_thought"}[event_type]
        return [{"event_type": public_type, "payload": {
            "round_number": round_number, "seat": seat, "text": text}}]
    if event_type == "WOLF_VOTE":
        seat = payload.get("seat"); target = payload.get("target_seat")
        reasoning = payload.get("reasoning")
        if (not _is_positive_int(seat)
                or (target is not None and not _is_positive_int(target))
                or not _is_reasoning_text(reasoning)):
            return []
        return [{"event_type": "wolf_vote", "payload": {
            "round_number": round_number, "seat": seat,
            "target_seat": target, "reasoning": reasoning}}]
```

- `_REASONING_EVENT_TYPES` 只保留 `"HUNTER_REASONING"`（删 witch/seer 死映射）
- `_public_operation_events` 增：

```python
    if operation == "narration":
        title, text = data.get("title"), data.get("text")
        if (not isinstance(title, str) or not isinstance(text, str)
                or not title or not text or len(title) > 100 or len(text) > 200):
            return []
        return [{"event_type": "narration", "payload": {
            "round_number": round_number, "title": title, "text": text}}]
```

- [ ] **Step 4: 运行确认通过 + 全量覆盖**（`test_game_logger.py`、`test_game_routes.py` 的既有死映射断言同步改）
- [ ] **Step 5: 提交**

```bash
git add backend/app/core/game_logger.py backend/app/api/routes/game_routes.py backend/tests/test_game_logger.py backend/tests/test_game_routes.py
git commit -m "feat(api): log and project staged night narration events"
```

---

### Task 5: `game_service` 注入导演 + 中文强制提示词 + 狼票分派

**Files:**
- Modify: `backend/app/services/game_service.py`
- Test: `backend/tests/test_game_service.py`

- [ ] **Step 1: 写失败测试**

```python
def test_system_prompt_demands_chinese_output() -> None:
    from app.services.game_service import _SYSTEM_PROMPT
    assert "简体中文" in _SYSTEM_PROMPT

def test_provider_returns_director_collected_vote_for_wolf_vote_point() -> None:
    # 构造 service + director，record_votes 后调用 provider(request, context, 0)
    # request.contract.schedule_point is SchedulePoint.NIGHT_WOLF_VOTE
    # 断言返回 ActionCommand 与预收集一致；LLM 不被调用（invoke 计数器为 0）
```

- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**

`_SYSTEM_PROMPT`：

```python
_SYSTEM_PROMPT = (
    "You are a player in an AI Werewolf game. Follow the ROLE_CONTRACT exactly, "
    "reason from PROJECTED_CONTEXT and UNTRUSTED_HISTORY, and respond with only "
    "the JSON described by OUTPUT_ACTION_COMMAND_SCHEMA. IMPORTANT: write ALL "
    "reasoning fields in Simplified Chinese (简体中文)."
)
```

`create_game` 中：

```python
from app.core.night_flow import NightDirector

def _night_invoke(messages):
    response = llm_client.get_model().invoke(messages)
    content = response.content if hasattr(response, "content") else str(response)
    if not isinstance(content, str):
        raise ValueError("model response is not text")
    return content

director = NightDirector(snapshot, _night_invoke)
...
engine = GameEngine(..., pipeline_scheduler=scheduler, director=director)
```

`_command_provider` 的 `provider` 首行分派：

```python
def provider(request, context, attempt):
    if request.contract.schedule_point is SchedulePoint.NIGHT_WOLF_VOTE:
        command = director.collected_vote(request.actor_seat)
        return command if command is not None else PipelineActionCommand(
            action_type=request.contract.fallback_action_type,
            target_seat=None, reasoning="safe fallback",
        )
    ...  # 原有渲染 + LLM 路径不变
```

（`provider` 闭包需引用 `director`；`SchedulePoint` 从 `app.models.pipeline` 导入。）

- [ ] **Step 4: 运行确认通过 + 覆盖**（`test_game_service.py` 全绿 + 100%）
- [ ] **Step 5: 提交**

```bash
git add backend/app/services/game_service.py backend/tests/test_game_service.py
git commit -m "feat(service): inject NightDirector and enforce Chinese night output"
```

---

### Task 6: 引擎分阶段夜晚驱动器（核心，拆 3 小步）

**Files:**
- Modify: `backend/app/core/game_engine.py`
- Test: `backend/tests/test_game_engine.py`

#### 6a. `_PendingNightBatch` 扩展 + 驱动器骨架

- [ ] **Step 1: 写失败测试**

```python
async def test_staged_night_runs_narrations_discussion_vote_and_points_in_order(tmp_path):
    engine = _engine_with_fake_director(tmp_path)   # fake scheduler + fake director
    engine.state.round_number = 1
    await engine._execute_staged_night()
    ops = _ops(tmp_path)  # 读 game.log 的 operation 序列
    assert ops == ["narration", "audience_action", "narration", "audience_action",
                   "narration", "audience_action", "narration"]
    # 断言讨论 utterance、投票、以及 dawn 旁白文案含 "平安夜" 或 "玩家死亡"
```

- [ ] **Step 2: 运行确认失败**（`_execute_staged_night` 不存在）
- [ ] **Step 3: 实现**

`game_engine.py` 关键改动：

```python
from app.core.night_flow import NightDirector, WolfVote

_NIGHT_POINTS = (
    SchedulePoint.NIGHT_WOLF_VOTE,
    SchedulePoint.NIGHT_WITCH_ACTION,
    SchedulePoint.NIGHT_SEER_ACTION,
    SchedulePoint.NIGHT_COMMIT,
)

@dataclass(frozen=True)
class _PendingNightBatch:
    round_number: int
    stage: int
    discussion_history: tuple[str, ...]
    wolf_votes: tuple[WolfVote, ...]
    raw_results: tuple[PointResult, ...]
    # 校验：stage 0..10；len(raw_results) == 已完成的 point 数
```

`__init__` 增加 `director: object | None = None` 并 `self._director = director`。

```python
    async def _execute_night_owned(self) -> None:
        if self._pending_night_completion is not None:
            await self._resume_pipeline_night(); return
        if self._pipeline_scheduler is None: raise ValueError("pipeline scheduler is required")
        if self._director is None: raise ValueError("night director is required")
        if self._pending_night_batch is None:
            self._prepare_night()
            self._pending_night_batch = _PendingNightBatch(self.state.round_number, 0, (), (), ())
        await self._execute_staged_night()

    async def _narrate(self, title: str, text: str) -> None:
        self.game_logger.log_narration(self.game_id, self.state.round_number, "night", title, text)

    async def _log_stage_audience(self, result: PointResult) -> None:
        self._log_audience_events(
            PipelineResult(tuple(), tuple(), result.state_digest, result.events, PipelineMode.V2),
            "night",
        )

    async def _execute_staged_night(self) -> None:
        pending = self._pending_night_batch
        director = self._director
        state = self.state
        wolves = [seat for seat in sorted(state.players) 
                  if state.players[seat].role == "wolf-killer-werewolf" and state.players[seat].is_alive]

        # stage 0: 狼人睁眼
        if pending.stage == 0:
            title, text = director.narration("wolf_open")
            await self._narrate(title, text)
            pending = replace(pending, stage=1); self._pending_night_batch = pending

        # stage 1: 狼人讨论（轮转、可跳过、上限 3×狼数或整轮跳过）
        if pending.stage == 1:
            history = list(pending.discussion_history)
            max_turns = 3 * len(wolves)
            turn = len(history)  # 检查点粒度：每轮一条
            while turn < max_turns:
                seat = wolves[turn % len(wolves)]
                result = await asyncio.to_thread(
                    director.wolf_discussion_turn, state, seat, tuple(history),
                )
                if result.spoke:
                    history.append(f"{seat}号：{result.text}")
                    self.game_logger.log_audience_action(
                        self.game_id, state.round_number, "night",
                        "WOLF_CHAT_MESSAGE", {"seat": seat, "text": result.text},
                    )
                else:
                    history.append(f"{seat}号：（跳过）")
                    if history[-len(wolves):].count and all(line.endswith("（跳过）") for line in history[-len(wolves):]):
                        break
                turn += 1
                pending = replace(pending, discussion_history=tuple(history)); self._pending_night_batch = pending
            pending = replace(pending, stage=2); self._pending_night_batch = pending
```

（跳过标记 `（跳过）` 不落盘为事件，只进 LLM 历史；完整一圈跳过即 break。注意：`history` 里跳过行仅用于轮询计数，进 prompt 用 `director._history_text` 会带上——可接受。）

```python
        # stage 2: 狼人投票（顺序出票）
        if pending.stage == 2:
            votes = list(pending.wolf_votes)
            discussion = tuple(line for line in pending.discussion_history if not line.endswith("（跳过）"))
            for seat in wolves:
                result = await asyncio.to_thread(
                    director.wolf_vote_turn, state, seat, discussion, tuple(votes),
                )
                votes.append(result)
                self.game_logger.log_audience_action(
                    self.game_id, state.round_number, "night", "WOLF_VOTE",
                    {"seat": seat, "target_seat": result.target_seat, "reasoning": result.reasoning},
                )
                pending = replace(pending, wolf_votes=tuple(votes)); self._pending_night_batch = pending
            director.record_votes(tuple(votes))
            pending = replace(pending, stage=3); self._pending_night_batch = pending

        # stage 3: 狼刀契约点（聚合结算）
        if pending.stage == 3:
            raw = await self._execute_v2_point(_NIGHT_POINTS[0])
            await self._log_stage_audience(raw)
            pending = replace(pending, raw_results=pending.raw_results + (raw,), stage=4)
            self._pending_night_batch = pending

        # stage 4: 女巫睁眼
        if pending.stage == 4:
            title, text = director.narration("witch_open")
            await self._narrate(title, text)
            pending = replace(pending, stage=5); self._pending_night_batch = pending

        # stage 5: 女巫思考（独立调用）
        if pending.stage == 5:
            witch = next((s for s in wolves_witches...)  # 女巫席位
            wolf_target = 最近 pending damage 目标（读取 state._pipeline_runtime.pending_damage 的 target）
            thought = await asyncio.to_thread(director.witch_think, state, seat, wolf_target)
            if thought is not None:
                self.game_logger.log_audience_action(self.game_id, state.round_number, "night",
                    "WITCH_THOUGHT", {"seat": thought.seat, "text": thought.text})
            pending = replace(pending, stage=6); self._pending_night_batch = pending

        # stage 6: 女巫行动契约点
        if pending.stage == 6:
            raw = await self._execute_v2_point(_NIGHT_POINTS[1])
            await self._log_stage_audience(raw)
            pending = replace(pending, raw_results=pending.raw_results + (raw,), stage=7)
            self._pending_night_batch = pending

        # stage 7/8/9: 预言家睁眼 → 思考 → 查验（同女巫模式，stage=9 后接）
        # stage 10: 结算点
        if pending.stage == 10:
            raw = await self._execute_v2_point(_NIGHT_POINTS[3])
            pending = replace(pending, raw_results=pending.raw_results + (raw,), stage=11)
            self._pending_night_batch = pending

        # stage 11: 天亮旁白（读取本轮死亡）
        if pending.stage == 11:
            deaths = [d.player_seat for d in self.state.death_history
                      if d.round_number == self.state.round_number]
            title, text = director.dawn_narration(deaths)
            await self._narrate(title, text)
            pending = replace(pending, stage=12); self._pending_night_batch = pending

        # 汇总 PipelineResult → 完成流水线（同旧 _execute_v2_night_batch 的合并逻辑）
        observations = tuple(RolePipeline.observe_v2(raw) for raw in pending.raw_results)
        result = PipelineResult(
            tuple(item for value in observations for item in value.accepted_actions),
            tuple(item for value in observations for item in value.effects),
            observations[-1].state_digest,
            tuple(item for value in observations for item in value.public_events),
            PipelineMode.V2,
        )
        self._pending_night_completion = _PendingNightCompletion(result)
        self._pending_night_batch = None
        await self._resume_pipeline_night()
```

- [ ] **Step 4: 运行确认通过**（`test_game_engine.py` 中 6a 相关）
- [ ] **Step 5: 提交（6a）**

```bash
git add backend/app/core/game_engine.py backend/tests/test_game_engine.py
git commit -m "feat(engine): drive staged night with narrations discussion and votes"
```

#### 6b. `_resume_pipeline_night` 顺序修复

- [ ] **Step 1: 写失败测试**

```python
async def test_resume_pipeline_night_logs_deaths_after_narrations_only() -> None:
    # 断言 stage 0 不再调用 _log_audience_events；log_deaths 仍在
    # （fake 结果带 PLAYER_DIED 事件，验证 game.log 顺序：narration(dawn) → night_deaths）
```

- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**：删除 `_resume_pipeline_night` stage 0 中的 `self._log_audience_events(pending.result, "night")`（观众事件已在各阶段即时落盘）。
- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: 提交（6b）**

```bash
git add backend/app/core/game_engine.py backend/tests/test_game_engine.py
git commit -m "fix(engine): log night audience events per stage before deaths"
```

#### 6c. 旧批量路径删除 + 遗留测试重写

- [ ] **Step 1: 失败测试**：删除 `_execute_v2_night_batch` 后，所有引用它的旧测试转为断言新驱动器行为（约 15 个，清单：`test_schedule_points_batches_once_and_single_point_delegates`、`test_execute_night_prepares_once_and_runs_v2_batch`、`test_v2_night_batch_resumes_only_failed_point_and_aggregates_exactly`、`test_pending_batch_is_frozen_exact_and_start_resets_checkpoints`、`test_v2_batch_rejects_non_v2_point_result_without_checkpoint`、`test_v2_observation_failure_reuses_checkpointed_raw_results`、`test_execute_night_v2_publishes_public_deaths_and_advances`、`test_pipeline_night_resumes_delivery_without_rerunning_batch_or_stages`、`test_malformed_pipeline_event_is_persisted_and_never_reruns_batch`、`test_pipeline_public_event_validation_is_closed_before_side_effects`、`test_start_resets_pending_night_checkpoints`、`test_pipeline_terminal_completion_resumes_log_and_publish_without_rechecking`、`test_pipeline_transition_hook_failure_resumes_without_retransition`、`test_pipeline_phase_publish_failure_does_not_repeat_phase_log`、`test_v2_night_runs_real_scheduler_end_to_end`）。新增共享 fake：

```python
class _FakeDirector:
    def narration(self, kind): return ("T", "text")
    def dawn_narration(self, deaths): return ("天亮了", "平安夜" if not deaths else "有人死了")
    def wolf_discussion_turn(self, state, seat, history): return DiscussionTurn(seat, True, "刀2号")
    def wolf_vote_turn(self, state, seat, discussion, prior): return WolfVote(seat, "pass", None, "观望")
    def witch_think(self, state, seat, target): return ThinkResult(seat, "考虑救人")
    def seer_think(self, state, seat): return ThinkResult(seat, "查验2号")
    def record_votes(self, votes): pass
    def collected_vote(self, seat): return None
```

- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**：`_execute_v2_night_batch` 整体删除；`_PendingNightBatch` 新校验；`start()` 重置新字段
- [ ] **Step 4: 运行确认通过**（`tests/test_game_engine.py` 全绿）
- [ ] **Step 5: 提交（6c）**

```bash
git add backend/app/core/game_engine.py backend/tests/test_game_engine.py
git commit -m "refactor(engine): replace batched night with staged driver"
```

---

### Task 7: 守卫门禁 blob 重算 + 全量回归

- [ ] **Step 1**：`python -m pytest tests/test_guard_extension.py -q` → 期望 FAIL（`game_engine.py` blob 变化）
- [ ] **Step 2**：更新 `backend/tests/test_guard_extension.py` 的 `CORE_BLOBS_BEFORE_GUARD`：用 `python -c "import hashlib,pathlib; p=pathlib.Path('app/core/game_engine.py'); c=p.read_bytes(); print(hashlib.sha1(b'blob '+str(len(c)).encode()+b'\0'+c).hexdigest())"`（在 `backend/` 目录运行）替换第一项
- [ ] **Step 3**：全量门禁

Run: `python -m pytest tests --cov=app --cov-branch --cov-fail-under=100 -q`
Expected: 全部 PASS、100.00% 覆盖

- [ ] **Step 4: 提交**

```bash
git add backend/tests/test_guard_extension.py
git commit -m "test(guard): refresh engine blob after staged night refactor"
```

---

### Task 8: 真实 LLM 端到端冒烟（不落仓库）

- [ ] 在仓库根目录（backend 外）写一次性脚本，用真实 `GameService` 接线跑 `NightDirector` 的讨论→投票→思考全流程，断言：讨论文本为中文、投票命令合法、天亮旁白文案正确。运行后删除脚本（**不可放入 backend/ 目录，避免触发 uvicorn reload**）。

---

## Self-Review

1. **Spec coverage**：旁白页（Task 3/4/6）✓；狼人讨论轮转+跳过+上限（Task 3/6）✓；狼人顺序投票+可见前票（Task 3/6）✓；女巫睁眼→思考→行动（Task 6 stage 4-6）✓；预言家睁眼→思考→查验（Task 6 stage 7-9）✓；天亮平安夜/死亡文案（Task 3 `dawn_narration` + Task 6 stage 11）✓；死亡公告不再先于狼刀（Task 6b）✓；结果不再先于过程（Task 2 删除 reasoning 事件 + 分阶段落盘）✓；中文输出（Task 5）✓；实时直播（每步即时写 game.log，前端 3 秒轮询即直播）✓；回放顺序正确（同一落盘顺序）✓。
2. **Placeholder scan**：6c 的旧测试重写清单已列名；女巫席位/wolf_target 取值的具体代码在实现时按 `state._pipeline_runtime.pending_damage` 与 `state.players` 角色字段取（引擎已有同类代码可参照）。
3. **Type consistency**：`DiscussionTurn`/`WolfVote`/`ThinkResult` 定义于 Task 3，Task 6 直接使用；`director.collected_vote` 与 `record_votes` 签名一致；`_PendingNightBatch` 字段在 6a 定义、6c 沿用。
