# 评测题：WolfKiller 新增「骑士」与「狼美人」角色板子

> 本文件是一份**模型评测题**（对外提交版），不是项目开发文档。
> 结构：第一部分 = 可下发给被测模型的完整 Prompt（六要素闭环，自包含）；
> 第二部分 = 出题人自用的真值、陷阱清单与评分细则（**不可下发给被测模型**）。

---

# 第一部分：评测 Prompt（下发给被测模型）

---

## 【角色设定】

你是 WolfKiller 项目的**资深后端架构师**。WolfKiller 是一个完全由 LLM 智能体驱动的 AI 狼人杀系统，后端为 Python 3.11 + FastAPI，角色规则以「**冻结声明 + 纯 Hook + 类型化 Effect + 唯一原子写入口**」的通用流水线表达。

你的职责是在**不破坏既有架构承诺**的前提下扩展角色，并交付可被 `pytest --cov-fail-under=100` 门禁接受的代码。

---

## 【背景信息】

项目刚完成一轮架构重构，官方承诺是：**新增角色只需增加一个 `roles/*.py` 声明文件并注册，不必改动任何核心模块**。守卫、白痴、白狼王三个角色被当作该承诺的验收样例。

现在产品侧要求补齐两个**主流 12 人板子**：

1. **12 人骑士板**（预女猎骑）
2. **12 人狼美人板**

团队已提供一份「既有实现说明」（见下方【输入材料】C 节）供你参考。**该说明由一位刚离职的工程师撰写，未经复核。**

---

## 【输入材料】

### A. 平台能力清单（冻结值类型，节选，不可修改）

```python
# app/models/pipeline.py

class SchedulePoint(str, Enum):
    GAME_SETUP = "game_setup"
    NIGHT_ACTION = "night_action"
    NIGHT_WOLF_VOTE = "night_wolf_vote"
    NIGHT_WITCH_ACTION = "night_witch_action"
    NIGHT_SEER_ACTION = "night_seer_action"
    NIGHT_COMMIT = "night_commit"
    DAWN_REACTION = "dawn_reaction"
    EXILE_VERDICT = "exile_verdict"
    DAY_ACTION = "day_action"
    VOTE_ACTION = "vote_action"
    ROUND_END = "round_end"
    GAME_END = "game_end"


class EffectKind(str, Enum):
    ACCEPT_ACTION = "accept_action"
    RECORD_VOTE = "record_vote"
    CONSUME_RESOURCE = "consume_resource"
    SET_RESOURCE = "set_resource"
    SET_PRIVATE_DATA = "set_private_data"
    ADD_STATUS = "add_status"
    REMOVE_STATUS = "remove_status"
    ADD_RELATION = "add_relation"
    REMOVE_RELATION = "remove_relation"
    RECORD_PRIVATE_FACT = "record_private_fact"
    SUBMIT_DAMAGE = "submit_damage"
    SUBMIT_PROTECTION = "submit_protection"
    MARK_DEATH = "mark_death"
    EMIT_EVENT = "emit_event"
```

```python
# app/models/pipeline.py — ActionContract（冻结声明）

@dataclass(frozen=True)
class ActionContract(_FrozenValue):
    contract_id: str
    schedule_point: SchedulePoint
    order: int
    action_types: tuple[str, ...]
    actions_requiring_target: frozenset[str]
    fallback_action_type: str
    allowed_effects: frozenset[EffectKind] = frozenset()
    required_resources: Mapping[str, int] = ...
    visibility_namespaces: frozenset[str] = frozenset()
    response_event_types: frozenset[str] = frozenset()
    response_reasons: frozenset[str] = frozenset()
    per_window_limit: int = 1
    per_round_limit: int | None = None
    per_game_limit: int | None = None
    is_applicable: Callable[..., object] | None = None
    validate: Callable[..., object] | None = None
    resolve: Callable[..., object] | None = None
    react: Callable[..., object] | None = None
    aggregate: Callable[..., object] | None = None
```

```python
# app/models/pipeline.py — ActionContext（Hook 能看到的全部输入）

@dataclass(frozen=True)
class ActionContext(_FrozenValue):
    game_id: str
    revision: int
    facts: Mapping[str, JsonValue]          # 含 actor_identity 与本人 initial_private_data
    round_number: int
    phase: str
    window_id: str
    schedule_point: SchedulePoint
    actor_seat: int
    actor_role_id: str
    actor_alive: bool
    resources: Mapping[str, JsonValue]       # 本人资源，来自 initial_resources
    action_key: str
    counters: Mapping[str, int]
    source_event_id: str | None
    trigger_event: Mapping[str, JsonValue] | None
    trigger_reason: str | None
    aggregate_result: Mapping[str, JsonValue] | None
```

```python
# app/models/pipeline.py — RoleSpec（冻结声明）

@dataclass(frozen=True)
class RoleSpec(_FrozenValue):
    role_id: str
    display_name: str = ""
    camp_id: str = ""                        # "good" | "werewolf"
    schema_version: int = 1
    contracts: tuple[ActionContract, ...] = ()
    initial_resources: Mapping[str, JsonValue] = ...
    initial_private_data: Mapping[str, JsonValue] = ...
    visibility_namespaces: frozenset[str] = frozenset()   # 仅允许 PUBLIC/ACTOR/CAMP/RELATION
    allowed_effects: frozenset[EffectKind] = frozenset()
    tags: frozenset[str] = frozenset()                    # 目前无任何角色使用
    dependencies: frozenset[str] = frozenset()
    exclusions: frozenset[str] = frozenset()
    min_count: int = 0
    max_count: int | None = None
    instructions: str = ""
```

**Hook 签名约定**（`roles/guard.py`、`roles/werewolf_king.py` 已验证可运行）：

- `is_applicable(context: ActionContext) -> bool`
- `validate(context: ActionContext, command: ActionCommand) -> tuple[RuleViolation, ...]`
- `resolve(context: ActionContext, command: ActionCommand) -> tuple[GameEffect, ...]`
- `react(context: ActionContext) -> tuple[GameEffect, ...]`（响应窗口用，**不经过 `is_applicable`**，Hook 内部须自行复查）
- `aggregate(...)`（仅多座位共享契约需要）

`GameEffect` 必填 `effect_id`（用 `derive_effect_id(context.action_key, ordinal)` 生成）、`kind`、`action_key`，并携带 `expected_revision=context.revision` 与 `source_event_id=context.source_event_id`；写操作需给 `preconditions`（如 `{"resource_equals": {"resource": "x", "value": 1}}`、`{"status_present": {"status": "y", "present": False}}`）。

---

### B. 既有角色实现模板（可直接运行，请作为风格与正确性参照）

```python
# app/roles/guard.py —— 夜间保护型角色，含「不能连续两晚同一人」校验
def guard_applicable(context: ActionContext) -> bool:
    return context.actor_alive


def validate_guard_action(context, command) -> tuple[RuleViolation, ...]:
    if command.action_type == "pass" or command.target_seat is None:
        return ()
    if context.facts.get("last_guarded") == command.target_seat:
        return (RuleViolation("consecutive_guard",
                "the same seat cannot be guarded two consecutive nights"),)
    return ()


def resolve_guard_action(context, command) -> tuple[GameEffect, ...]:
    common = {"expected_revision": context.revision,
              "source_event_id": context.source_event_id}
    if command.action_type == "pass":
        return (_guard_reasoning_effect(context, command, 1, **common),)
    return (
        GameEffect(derive_effect_id(context.action_key, 1), EffectKind.SUBMIT_PROTECTION,
                   context.action_key, target_seat=command.target_seat,
                   payload={"target": command.target_seat, "amount": 1, "source": "guard"},
                   sort_key=(1,), **common),
        GameEffect(derive_effect_id(context.action_key, 2), EffectKind.SET_PRIVATE_DATA,
                   context.action_key, target_seat=context.actor_seat,
                   payload={"target": context.actor_seat, "key": "last_guarded",
                            "value": command.target_seat},
                   sort_key=(2,), **common),
        GameEffect(derive_effect_id(context.action_key, 3), EffectKind.EMIT_EVENT,
                   context.action_key,
                   payload={"event_type": "GUARD_PROTECT",
                            "payload": {"target_seat": command.target_seat}},
                   visibility=("PUBLIC",), sort_key=(3,), **common),
    )


GUARD_SPEC = RoleSpec(
    role_id="wolf-killer-guard", display_name="Guard", camp_id="good",
    contracts=(ActionContract(
        contract_id="guard_action", schedule_point=SchedulePoint.NIGHT_ACTION,
        order=50, action_types=("guard", "pass"),
        actions_requiring_target=frozenset({"guard"}), fallback_action_type="pass",
        allowed_effects=frozenset({EffectKind.SUBMIT_PROTECTION,
                                   EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT}),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        is_applicable=guard_applicable, validate=validate_guard_action,
        resolve=resolve_guard_action,
    ),),
    initial_private_data={"last_guarded": None},
    allowed_effects=frozenset({EffectKind.SUBMIT_PROTECTION,
                               EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    instructions="...（长文本，仅描述本角色规则与战术建议）",
)
```

```python
# app/roles/werewolf.py —— 狼队共享契约（注册表要求跨角色声明完全一致才会合并计票）
WEREWOLF_KILL_CONTRACT = ActionContract(
    contract_id="werewolf_kill", schedule_point=SchedulePoint.NIGHT_WOLF_VOTE,
    order=10, action_types=("kill", "pass"),
    actions_requiring_target=frozenset({"kill"}), fallback_action_type="pass",
    allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    is_applicable=werewolf_applicable, validate=validate_werewolf_action,
    aggregate=aggregate_werewolf_votes,
)

WEREWOLF_SPEC = RoleSpec(
    role_id="wolf-killer-werewolf", display_name="Werewolf", camp_id="werewolf",
    schema_version=2, contracts=(WEREWOLF_KILL_CONTRACT,),
    allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    instructions="...",
)
```

```python
# app/roles/werewolf_king.py —— 白天自爆带人：复用狼队契约 + 一次性资源 + 打断白天
# 关键点：不用 per_game_limit 限制一次性，而是用资源 + preconditions 强制
WEREWOLF_KING_EXPLODE_CONTRACT = ActionContract(
    contract_id="werewolf_king_explode", schedule_point=SchedulePoint.DAY_ACTION,
    order=20, action_types=("explode", "pass"),
    actions_requiring_target=frozenset({"explode"}), fallback_action_type="pass",
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE,
                               EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    per_window_limit=1,
    is_applicable=werewolf_king_applicable,
    validate=validate_werewolf_king_action, resolve=resolve_werewolf_king_action,
)
# resolve 产出 5 个 Effect：CONSUME_RESOURCE(explode) → SUBMIT_DAMAGE(self)
#   → SUBMIT_DAMAGE(target) → EMIT_EVENT("SELF_EXPLODE")
#   → EMIT_EVENT("DAY_INTERRUPTED", payload 含 cause="self_explode")
```

```python
# app/roles/idiot.py —— 放逐裁决型角色（无 LLM 调用，纯 react）
IDIOT_SPEC = RoleSpec(
    role_id="wolf-killer-idiot", display_name="Idiot", camp_id="good",
    contracts=(ActionContract(
        contract_id="idiot_flip", schedule_point=SchedulePoint.EXILE_VERDICT,
        order=30, action_types=("flip",),
        actions_requiring_target=frozenset(), fallback_action_type="flip",
        allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE,
                                   EffectKind.ADD_STATUS, EffectKind.EMIT_EVENT}),
        visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
        response_event_types=frozenset({"EXILE_PENDING"}),
        response_reasons=frozenset({"exile"}),
        per_window_limit=1, per_game_limit=1,
        is_applicable=idiot_applicable, react=react_idiot_flip,
    ),),
    initial_resources={"flip": 1},
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE,
                               EffectKind.ADD_STATUS, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    max_count=1,
    instructions="...",
)
```

```python
# app/core/rule_engine.py —— 现有胜负判定（屠边）
class RuleEngine:
    def check_win(self, state: GameState) -> WinResult | None:
        alive_wolves = len(state.alive_by_camp(Camp.WEREWOLF))
        alive_seers    = len([p for p in state.alive_players().values() if "seer"    in p.role])
        alive_witches  = len([p for p in state.alive_players().values() if "witch"   in p.role])
        alive_hunters  = len([p for p in state.alive_players().values() if "hunter"  in p.role])
        alive_guards   = len([p for p in state.alive_players().values() if "guard"   in p.role])
        alive_idiots   = len([p for p in state.alive_players().values() if "idiot"   in p.role])
        alive_villagers= len([p for p in state.alive_players().values() if "villager" in p.role])
        alive_gods = alive_seers + alive_witches + alive_hunters + alive_guards + alive_idiots

        if alive_gods == 0:        # 狼刀在先
            return WinResult(winning_camp=Camp.WEREWOLF.value, reason="all_gods_dead")
        if alive_villagers == 0:
            return WinResult(winning_camp=Camp.WEREWOLF.value, reason="all_villagers_dead")
        alive_good = len(state.alive_players()) - alive_wolves
        if alive_wolves > alive_good:
            return WinResult(winning_camp=Camp.WEREWOLF.value,
                             reason="wolves_outnumber_good")
        if alive_wolves == 0:
            return WinResult(winning_camp=Camp.GOOD.value, reason="all_wolves_dead")
        return None
```

```python
# app/models/game.py —— 阵营判定
def alive_by_camp(self, camp: str) -> dict[int, PlayerState]:
    return {s: p for s, p in self.players.items() if p.camp == camp and p.is_alive}
```

```python
# app/core/vote_service.py —— 角色无关的投票资格状态
NO_VOTE_STATUS = "no_vote"          # 有此状态者不投票
EXILE_IMMUNE_STATUS = "exile_immune" # 有此状态者不进放逐候选
```

```python
# backend/tests/test_catalog_routes.py —— 现有门禁断言（节选）
assert len(items) == 8            # 角色数
assert len(STANDARD_PRESETS) == 4 # 预设数
assert len(response.roles) == 8
assert len(response.presets) == 4
```

```python
# app/services/audience_projector.py —— 观众端事件投影表（节选，共三张表）
_AUDIENCE_EVENTS = {                     # 需声明公开字段白名单的复合事件
    "NIGHT_ACTION": ("night_action", frozenset({"action_type", "target_seat",
                                                "round_number", "vote_counts", "result"})),
    "TECHNICAL_ABSTAIN": ("technical_abstain", frozenset({"player_seat", "voter_seat",
                                                          "round_number", "failure_code"})),
    "EXILE_CANCELLED": ("exile_cancelled", frozenset({"target_seat", "round_number"})),
    "SELF_EXPLODE": ("self_explode", frozenset({"seat", "target_seat", "round_number"})),
    "SHERIFF_ELECTED": ("sheriff_elected", frozenset({"seat", "round_number", "reason"})),
    ...  # 另有 SHERIFF_BADGE / SHERIFF_RUN / SHERIFF_WITHDRAW / SHERIFF_VOTE / SHERIFF_SIDE
}

_PUBLIC_ROLE_ACTIONS = {                 # 角色行动 → 观众可见动作名
    "WEREWOLF_KILL": "werewolf_kill",
    "WITCH_SAVE": "witch_save",
    "WITCH_POISON": "witch_poison",
    "SEER_CHECK": "seer_check",
    "HUNTER_SHOT": "hunter_shot",
    "GUARD_PROTECT": "guard_protect",
}

_PUBLIC_REASONING_EVENTS = {             # 角色思考 → 上帝视角可见推理
    "HUNTER_REASONING": "hunter_reasoning",
    "WITCH_REASONING": "witch_reasoning",
    "SEER_REASONING": "seer_reasoning",
    "GUARD_REASONING": "guard_reasoning",
    "WEREWOLF_KING_REASONING": "werewolf_king_reasoning",
}
```

```python
# app/catalog.py —— 展示元数据（刻意放在冻结 RoleSpec 之外，新增不改 registry.digest）
ROLE_METADATA = {
    "wolf-killer-werewolf": {"name_zh": "狼人", "icon": "wolf", "description": "..."},
    "wolf-killer-guard":    {"name_zh": "守卫", "icon": "guard", "description": "..."},
    "wolf-killer-idiot":    {"name_zh": "白痴", "icon": "idiot", "description": "..."},
    "wolf-killer-werewolf-king": {"name_zh": "白狼王", "icon": "wolf_king", "description": "..."},
    ...  # 共 8 条
}

STANDARD_PRESETS = [
    {"id": "nine-player-standard",  "name": "九人标准场", ...},
    {"id": "ten-player-standard",   "name": "十人标准场", ...},
    {"id": "twelve-player-idiot",   "name": "十二人预女猎白", ...},
    {"id": "twelve-player-wolf-king", "name": "十二人白狼王", ...},
]  # 共 4 条

FIELD_CONSTRAINTS = {"min_players": 4, "max_players": 12, ...}
```

---

### C. 团队「既有实现说明」（⚠️ 未经复核，供参考）

> **关于胜负判定**：神职（god）是通过 `RoleSpec.tags` 打标识别的，`rule_engine.py` 读 `tags` 来统计存活神职，所以新增神职只要在 spec 里加 `tags={"god"}` 就行，不需要动 `rule_engine.py`。
>
> **关于狼队识别**：狼队成员按 `role == "wolf-killer-werewolf"` 判定，所以新加的狼阵营角色（例如狼美人）必须在代码里显式声明自己的 `role_id` 并复用普通狼人的契约，否则不会被算进狼队。
>
> **关于开局前生效的技能**：骑士的翻牌裁决、狼美人的初始状态都应在开局前确定，请使用 `SchedulePoint.GAME_SETUP` 承载；该调度点已预留给开局阶段使用。
>
> **关于私有数据复用**：狼美人的「不能连续两晚魅惑同一人」限制，直接复用守卫已有的 `last_guarded` 私有数据字段即可，避免新增字段导致存档不兼容。
>
> **关于一次性技能**：骑士的「一局一次」请用 `per_game_limit=1` 表达，与白痴翻牌的做法保持一致。

---

### D. 两个待实现角色的规则原文（**唯一权威规则来源，与 C 节冲突时以本节为准**）

**骑士（Knight）**，好人阵营，神职：

- 白天全体发言结束、**放逐投票开始之前**，骑士可以翻出底牌并指定一名玩家，由法官宣布该玩家是狼人还是好人。
- 若被指定者是**狼人**：该玩家立即死亡，**白天立即结束，马上进入夜晚**（当日放逐投票取消）。
- 若被指定者是**好人**：骑士以死谢罪，**且无遗言**；当天的发言与投票**照常继续**。
- 该技能**一局只能发动一次**。

**狼美人（Wolf Beauty）**，狼人阵营：

- 每晚与狼队友一同睁眼、**共享狼刀投票**。
- 在参与杀人后，可**单独**魅惑一名好人阵营玩家（**不能连续两晚魅惑同一人**）。
- 狼美人**出局时**，当晚被其魅惑的玩家**随之殉情**（一同死亡）。
- 狼美人**不能自爆、不能自刀**；除这两种以外的任何出局方式都可以带人。

---

### E. 板子配置

| 板子 ID | 名称 | 配置 |
| --- | --- | --- |
| `twelve-player-knight` | 十二人预女猎骑 | 4 狼人 / 预言家·女巫·猎人·骑士 / 4 平民；不开警长 |
| `twelve-player-wolf-beauty` | 十二人狼美人 | 3 狼人 + 1 狼美人 / 预言家·女巫·猎人·守卫 / 4 平民；开警长 |

> 说明：民间「狼美人」板常用 3 民 + 1 老酒鬼（老酒鬼免疫魅惑）。**本项目暂无老酒鬼角色，本期不新增该角色**，该位置以平民替代。

---

## 【任务要求】

请基于以上材料，交付一套可落地的实现方案与代码：

1. **设计并写出** `app/roles/knight.py` 与 `app/roles/wolf_beauty.py` 两个角色声明文件（含 spec、Hook、必要的事件与资源），风格与 B 节模板一致。
2. **指明**为让这两个角色在真实对局中正确生效，除新增角色文件外，还必须修改**哪些既有文件**的**哪些行**，并说明每处修改的**具体原因**。
3. **补齐**两个新预设到 `STANDARD_PRESETS`，并写出对应的 `ROLE_METADATA` 条目。
4. **列出**需要同步更新的既有测试断言，以及需要新增的测试文件与关键用例（每条用例一句话说明它锁定什么行为）。
5. **评估** C 节「既有实现说明」中每一条陈述的正确性，逐条给出「正确 / 错误」的判定与证据。

---

## 【约束条件】

**必须满足：**

1. Hook 必须是**纯函数**：函数体内**禁止**读写任何全局状态、禁止直接修改 `GameState`、禁止发起 I/O 或 LLM 调用；一切状态变更必须表达为 `GameEffect`。
2. 所有写操作必须携带 `expected_revision` 与 `source_event_id`，并给出 `preconditions`，保证 CAS 原子性。
3. 两个角色的规则必须**严格来自 D 节**。规则里没有写的能力**一律不得实现**（例如：不得给骑士增加夜间行动、不得让狼美人在被毒杀时也能带人之外地扩大技能范围）。
4. 角色新增必须**不破坏**「新增角色不改核心模块」这一架构承诺；若某处确实无法避免修改核心模块，**必须显式指出并给出理由与最小改法**，不得默默绕过。
5. 私有信息边界：狼美人的魅惑目标、骑士的裁决结果在**未公开前**不得进入任何 PUBLIC 可见的事件载荷。
6. `RoleSpec.visibility_namespaces` 只能取 `PUBLIC` / `ACTOR` / `CAMP` / `RELATION` 四个值。

**禁止（负向约束）：**

7. **严禁编造**材料中未给出的 API、字段、枚举值、文件路径或函数签名。若某能力在材料中不存在，必须明确说明「材料未提供该能力」并给出替代方案，**不得虚构**。
8. **严禁**直接照搬 C 节的实现建议。C 节是待验证的参考意见，不是权威依据。
9. **严禁**在未核对材料的情况下断言某个 `SchedulePoint` 已被使用、某个字段已被占用。
10. 不得修改 `SchedulePoint`、`EffectKind`、`ActionContract`、`RoleSpec` 这四个冻结值类型的字段定义。

---

## 【输出格式】

严格按以下 Markdown 结构输出，**不得增删这八个二级标题**：

````markdown
## 一、方案概述
（不超过 300 字，说明两个角色各自落在哪个调度点、产出哪几类 Effect、为什么这样切分）

## 二、骑士实现
### 2.1 契约设计
| 字段 | 取值 | 依据 |
（逐字段列出 contract_id / schedule_point / order / action_types / actions_requiring_target / fallback_action_type / allowed_effects / visibility_namespaces / response_event_types / response_reasons / per_window_limit / per_game_limit）
### 2.2 Hook 实现
```python
# 完整可运行代码
```
### 2.3 胜负与阶段影响
（说明裁决为狼时如何结束白天、为好人时如何继续，以及涉及哪些通用事件名）

## 三、狼美人实现
（结构同第二章）

## 四、必须修改的既有文件清单
| 文件 | 位置 | 改动 | 原因（一句话） |
（若认为某处不必改，也需列出并注明「无需修改 + 理由」）

## 五、预设与目录元数据
```python
# STANDARD_PRESETS 与 ROLE_METADATA 的新增条目
```

## 六、测试清单
| 类型（更新/新增） | 文件 | 用例 | 锁定的行为 |
（更新项须写出「原断言值 → 新断言值」）

## 七、C 节实现说明逐条核验
| # | C 节原陈述 | 判定（正确/错误） | 证据 | 若不采纳，正确做法是 |
（必须覆盖 C 节全部 5 条）

## 八、风险与未决问题
（列出你认为材料不足以判定、需要出题方澄清的点；没有则写「无」）
````

---

# 第二部分：真值、陷阱清单与评分细则（**出题人自用，勿下发**）

---

## 一、真值基线（Ground Truth）

### 1.1 骑士（Knight）

| 项 | 正确取值 | 依据 |
| --- | --- | --- |
| `role_id` | `wolf-killer-knight` | 与既有 `wolf-killer-*` 命名一致 |
| `camp_id` | `good` | 好人阵营 |
| 神职归属 | **是**（必须计入 `alive_gods`） | 屠边规则下骑士是神 |
| 调度点 | `EXILE_VERDICT` | 裁决发生在「放逐投票之前」，与白痴同一裁决点；**`GAME_SETUP` 是错的** |
| `contract_id` | 建议 `knight_duel` | 语义化，无强制 |
| `action_types` | `("duel", "pass")` | 需要「不发动」的合法选项 |
| `fallback_action_type` | `"pass"` | 与既有约定一致 |
| `actions_requiring_target` | `frozenset({"duel"})` | 裁决必须指定目标 |
| 一次性表达 | **`initial_resources={"duel": 1}` + `CONSUME_RESOURCE` + `preconditions`** | 与白狼王自爆一致；`per_game_limit` 只约束被接受的命令，`pass` 也会计入窗口，不足以保证「一局一次」 |
| 需要 LLM 决策 | **需要**（`resolve`，模型选择是否翻牌、指认谁） | 与白痴不同：白痴是被动触发（`react`），骑士是主动发动 |
| 关键 Effect 序列 | 1) `CONSUME_RESOURCE(duel)` 2) 目标为狼时 `SUBMIT_DAMAGE(target, cause="knight_duel")` 3) 目标为好人时 `SUBMIT_DAMAGE(self, cause="knight_duel")` 4) `EMIT_EVENT` 公开裁决结果 5) 目标为狼时 `EMIT_EVENT("DAY_INTERRUPTED")` | 白天结束走既有 `DAY_INTERRUPTED` 通用事件，与白狼王自爆同一机制 |
| 遗言 | 骑士以死谢罪**无遗言**；被裁决致死的狼人**是否留遗言规则未明确** | 需在第八节标为待澄清 |
| 公开边界 | 裁决结果（好人/狼人）是**公开信息**（法官宣布）；`duel` 资源用量属私有 | D 节 + 约束 5 |

### 1.2 狼美人（Wolf Beauty）

| 项 | 正确取值 | 依据 |
| --- | --- | --- |
| `role_id` | `wolf-killer-wolf-beauty` | 命名一致 |
| `camp_id` | `werewolf` | 狼人阵营 |
| 契约 | `WEREWOLF_KILL_CONTRACT`（**原样复用，声明须完全一致**）+ 新增魅惑契约 | 注册表仅在同 id 声明完全一致时合并计票 |
| 魅惑调度点 | `NIGHT_ACTION` | 独立于狼刀的单人行动；`order` 建议取 40（女巫 20 / 守卫 50 之间，可自定但要说明理由） |
| 连魅限制 | **必须用独立私有数据字段**（如 `last_charmed`），**禁止复用 `last_guarded`** | `last_guarded` 被 `validate_guard_action` 读取；复用会让守卫「不能连守」校验误判 → 守卫技能静默失效 |
| 殉情触发 | 狼美人**出局时**触发；需覆盖夜刀/毒/放逐/枪杀/自爆带走等**除自爆与自刀以外**的所有出局路径 | D 节 |
| 殉情表达 | 建议 `ADD_RELATION`（记录魅惑关系）+ `SUBMIT_DAMAGE(target, cause="charm")` | `relations` 机制已在 `effect_applier.py` 实现且**当前零角色使用** |
| 殉情时机 | 「当晚被魅惑的玩家随之殉情」——触发点应在**夜晚结算/放逐结算**中读取魅惑关系，而非依赖单一 `PLAYER_DIED` 事件 | 死亡可能由多种 cause 产生 |
| 禁止能力 | **不得**实现自爆、**不得**允许自刀 | D 节负向约束 |

### 1.3 必须修改的既有文件（真值清单）

| # | 文件 | 位置 | 改动 | 原因 |
| --- | --- | --- | --- | --- |
| 1 | `app/core/rule_engine.py` | 第 10–16 行 | 新增骑士的神职计数（或改为读 `RoleSpec.tags`） | **`"knight"` 不包含 seer/witch/hunter/guard/idiot 任一子串，当前子串匹配会把骑士漏出 `alive_gods`，导致屠边判定错误** |
| 2 | `app/roles/registry.py` | 顶部 import 区 | 导入并注册 `KNIGHT_SPEC` / `WOLF_BEAUTY_SPEC` | 不注册则角色不可用 |
| 3 | `app/catalog.py` | `ROLE_METADATA` | 新增 2 条中文名/图标/描述 | 前端角色选择依赖 |
| 4 | `app/catalog.py` | `STANDARD_PRESETS` | 新增 2 个预设 | 产品要求 |
| 5 | `app/services/audience_projector.py` | `_PUBLIC_ROLE_ACTIONS` / `_PUBLIC_REASONING_EVENTS`（复合事件另需 `_AUDIENCE_EVENTS`） | 新增骑士/狼美人的事件映射 | 不加则**观众端看不到这两个角色的行动** |
| 6 | `tests/test_catalog_routes.py` | 第 56、114 行 | `== 8` → `== 10` | 角色数变化 |
| 7 | `tests/test_catalog_routes.py` | 第 70、124 行 | `== 4` → `== 6` | 预设数变化 |
| 8 | 前端 `theme/tokens.ts` | `ROLE_COLORS` | 新增 2 个角色色 | 组件禁止硬编码色值 |
| 9 | 前端 `components/shared/RoleIcon.tsx` | `ROLE_ICONS` | 新增 2 条图标映射 | 否则显示「未知身份」问号 |
| 10 | 前端 `components/game/ActivityCard.tsx`、`CenterDisplay.tsx` | 事件名映射表 | 新增事件中文名 | 否则事件卡片显示原始英文键 |

> **注意 #1 与架构承诺的冲突**：文件 #1 属于核心模块。真值要求模型**显式指出**「当前承诺在此处不成立」，并给出最小改法（推荐改读 `tags`，因为 `RoleSpec.tags` 已存在且**目前零角色使用**，加 `tags={"god"}` 是零破坏的演进路径）。

### 1.4 门禁与运行影响

- 新增角色会改变 `registry.digest`，处于 `interrupted` 的旧局**无法续跑**；上线前须先结束进行中的对局。
- 后端覆盖率门禁为 statement + branch **100%**（`--cov-fail-under=100`），新代码每条分支都必须有测试。

---

## 二、陷阱清单（区分度设计）

| # | 陷阱类型 | 埋设位置 | 错误做法（差模型） | 正确做法（好模型） | 分值 |
| --- | --- | --- | --- | --- | --- |
| T1 | **假前提 + 隐蔽 Bug** | C 节第 1 条声称神职靠 `tags` 识别 | 照搬「加 `tags={"god"}` 即可」，不核对 `rule_engine.py`；结果骑士不计入神职，屠边判定错误 | 读 B 节 `rule_engine.py` 源码，指出实际是**角色名子串匹配**，`"knight"` 不匹配任何子串 → 必须改 `rule_engine.py`；并建议改用 `tags` 作为长期方案 | 3 |
| T2 | **假前提（与明文规则直接冲突）** | C 节第 2 条声称狼队按 `role == "wolf-killer-werewolf"` 判定 | 照搬该判定，或为狼美人另写一套狼队识别逻辑 | 指出 `alive_by_camp` 按 `p.camp == camp` 判定，狼美人只需 `camp_id="werewolf"` 即自动入狼队；C 节说法与材料 B 节矛盾 | 3 |
| T3 | **不存在的机制** | C 节第 3 条要求用 `GAME_SETUP` | 把骑士裁决挂在 `GAME_SETUP`（该点从未被引擎调度），或声称已在使用 | 指出 `GAME_SETUP` 只作为 `ActionContext.schedule_point` 的默认值出现，**全项目无任何调度调用**；骑士裁决必须用 `EXILE_VERDICT` | 3 |
| T4 | **复用即破坏（最隐蔽）** | C 节第 4 条建议复用 `last_guarded` | 复用 `last_guarded` 存魅惑目标 → `validate_guard_action` 读到被污染的值，**守卫的「不能连续两晚守同一人」校验静默失效** | 使用独立字段 `last_charmed`；并说明复用会跨角色污染守卫校验 | 4 |
| T5 | **机制误用** | C 节第 5 条建议 `per_game_limit=1` | 用 `per_game_limit=1` 表达骑士「一局一次」 | 指出 `pass` 也会作为被接受的命令计入窗口/次数，必须用**资源 + `preconditions`** 表达一次性（白狼王自爆即此模式） | 2 |
| T6 | **强约束遵循（10 条）** | 约束条件全节 | 漏 1–2 条（最常见：漏「Hook 纯函数」、漏「私有边界」、漏「禁止编造」） | 10 条全部满足，并在输出中可被逐条核对 | 3 |
| T7 | **抗幻觉** | D 节未给遗言细则、未给魅惑 `order` | 编造「被裁决的狼人无遗言」「魅惑 order 必须是 40」等材料外结论 | 在第八节明确标为**待澄清**，给出建议值并说明是自定 | 2 |

**陷阱设计要点**：T1/T2/T3/T4/T5 全部藏在 C 节「看似合理、且有具体字段名支撑」的建议里，且**每一条都能被 B 节材料证伪**——这正是「主动纠错 vs 盲从编造」的区分点。T4 是最难的一条：即使模型识别出 C 节整体不可信，也可能因为「复用更省事」而踩中，需要真正读完 `validate_guard_action` 才能发现后果。

---

## 三、评分细则（满分 100，按点计分/扣分制）

### 维度权重

| 维度 | 权重 | 说明 |
| --- | --- | --- |
| A. 专业准确度（角色机制与 Effect 设计正确性） | 35 | 契约字段、Effect 序列、时机选择 |
| B. 陷阱识别（T1–T5） | 25 | 逐条判定 + 证据 |
| C. 架构影响完整性（第四章清单） | 20 | 是否找全 10 处必改点 |
| D. 约束遵循（10 条） | 12 | 逐条可核对 |
| E. 输出格式规范 | 8 | 八级标题完整、表格齐全 |

### A. 专业准确度（35 分）

| 采分点 | 分值 | 判定标准 |
| --- | --- | --- |
| 骑士落在 `EXILE_VERDICT` | 5 | 写 `GAME_SETUP` 或 `DAY_ACTION` 得 0 |
| 骑士用资源 + `preconditions` 表达一次性 | 5 | 仅用 `per_game_limit` 得 0 |
| 骑士为狼时走 `DAY_INTERRUPTED` 结束白天 | 5 | 自造新事件名得 0 |
| 骑士为好人时骑士自身死亡且当日流程继续 | 4 | 遗漏「继续」得 0 |
| 狼美人原样复用 `WEREWOLF_KILL_CONTRACT`（声明一致） | 5 | 自建一份不同声明的狼刀契约得 0 |
| 狼美人连魅限制用独立字段 | 4 | 复用 `last_guarded` 得 0（并触发 T4 扣分） |
| 殉情覆盖「除自爆/自刀外全部出局路径」 | 4 | 只覆盖夜刀得 1 |
| 未实现自爆/自刀 | 3 | 实现了得 0 |

### B. 陷阱识别（25 分）

| 项 | 分值 | 判定标准 |
| --- | --- | --- |
| T1 神职子串匹配 | 3 | 仅说「C 节错误」得 1；给出 `"knight"` 不匹配具体子串 + 影响（屠边误判）得 3 |
| T2 狼队按 camp 判定 | 3 | 仅否定得 1；引 `alive_by_camp` 实现得 3 |
| T3 `GAME_SETUP` 未被调度 | 3 | 仅否定得 1；指出它只作默认值、全项目无调用得 3 |
| T4 复用 `last_guarded` 破坏守卫 | 4 | **未识别得 0**；识别出复用不当得 2；能说出「守卫连守校验静默失效」得 4 |
| T5 `per_game_limit` 不足以表达一局一次 | 2 | 未识别得 0 |
| 逐条覆盖 C 节全部 5 条 | 4 | 每漏一条扣 0.8 |
| 每条判定均给出材料内证据 | 3 | 无证据的判定不计分 |
| 未盲从：最终方案与 C 节建议**无任何一条**一致 | 3 | 每沿用一条错误建议扣 1 |

### C. 架构影响完整性（20 分）

| 采分点 | 分值 |
| --- | --- |
| 找出 `rule_engine.py` 神职计数必改（#1） | 4 |
| 找出 `registry.py` 注册（#2） | 2 |
| 找出 `catalog.py` 元数据 + 预设（#3#4） | 3 |
| 找出 `audience_projector.py` 事件映射（#5） | 4 |
| 找出 `test_catalog_routes.py` 两处断言（#6#7，须写出原值→新值） | 3 |
| 找出前端 3 处（tokens / RoleIcon / 事件中文名） | 3 |
| 主动指出「新增角色不改核心」承诺在 #1 处不成立 | 1 |

> 每多列一处**不存在的必改点**（幻觉），倒扣 1 分，本维度最低 0 分。

### D. 约束遵循（12 分）

10 条约束每条 1.2 分。**硬否决项**（触发则本题总分不超过 40）：

- 编造材料中不存在的 API / 枚举 / 文件路径；
- Hook 中出现直接修改 `GameState` 或 I/O；
- 实现 D 节规则之外的能力（如给骑士加夜间行动）。

### E. 输出格式（8 分）

| 项 | 分值 |
| --- | --- |
| 八个一级标题齐全且顺序正确 | 3 |
| 第四章为表格且含「原因」列 | 2 |
| 第七章覆盖 C 节全部 5 条 | 2 |
| 代码块为完整可运行 Python（非伪代码） | 1 |

### 等级换算

| 等级 | 分数 | 行为特征 |
| --- | --- | --- |
| 优 | 85–100 | T1–T5 全部识别并给出证据；必改点找全 ≥9 处；方案无一处沿用 C 节错误建议 |
| 良 | 70–84 | 识别 T1/T2/T3，漏 T4 或 T5；必改点 7–8 处；契约设计正确 |
| 中 | 50–69 | 仅识别 1–2 个陷阱；漏 `rule_engine.py` 或 `audience_projector.py`；骑士一次性表达错误 |
| 差 | < 50 | 整体照搬 C 节；或触发任一硬否决项 |

---

## 四、提交前自查（出题人已核对）

| 检查项 | 结论 |
| --- | --- |
| 真实性 | ✅ 题目直接来自本项目「新增骑士 + 狼美人板子」的真实开发任务 |
| 专业性 | ✅ 需要读懂冻结流水线、Effect 代数、CAS 前置条件与注册表约束才能作答 |
| 充分性 | ✅ 材料 A–E 自包含（枚举、模板、规则原文、板子配置、误导性参考实现） |
| 明确性 | ✅ 六要素闭环，输出格式为强制八级标题，约束 10 条可逐条核对 |
| 可评性 | ✅ 100 分按点计分，T1–T5 各有明确证据要求，无「分析到位」类主观表述 |
| 区分度 | ⚠️ **尚未用主流模型实跑验证**（规范要求出题人亲自跑测）。建议先用 2–3 个模型跑一遍，确认 T4 的触发率在 20%–60% 区间；若所有模型都识别 T4，则需把 T4 换为更隐蔽的陷阱 |

### 待出题方决策的一个问题

`十二人狼美人` 板子的第 4 个民坑，民间规则是「老酒鬼」（免疫魅惑）。本材料 E 节已声明「本期不新增老酒鬼，以平民替代」。**若希望评测题更贴近民间规则，需要先决定是否把老酒鬼也纳入范围**——那会显著提高难度（需新增第 3 个角色），也会改变真值清单。
