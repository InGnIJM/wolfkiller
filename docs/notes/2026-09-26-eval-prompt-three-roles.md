# 评测题：WolfKiller 新增「骑士 / 狼美人 / 老酒鬼」三角色板子

> 本文件是一份**模型评测题**（对外提交版）。
> 第一部分 = 可下发给被测模型的完整 Prompt（六要素闭环，自包含）；
> 第二部分 = 出题人自用的真值、陷阱清单与评分细则（**不可下发给被测模型**）。
>
> 出题日期：2026-09-26 ｜ 适用项目：WolfKiller（Python 3.11 + FastAPI，冻结角色流水线）
>
> ⚠️ **本文件的内联材料版本（第一部分）已被 `2026-09-26-eval-prompt-DELIVER.md` 取代**：
> 新版不再内联任何代码，被测模型须自行获取仓库并从源码取证。
> 本文件请**仅作为真值底稿与陷阱设计记录**保留（第二部分的真值基线仍然有效）。
> 两角色版本 `2026-09-26-eval-prompt-knight-wolf-beauty.md` 已作废，可删除。

---

# 第一部分：评测 Prompt（下发给被测模型）

---

## 【角色设定】

你是 WolfKiller 项目的**资深后端架构师**。WolfKiller 是一个完全由 LLM 智能体驱动的 AI 狼人杀系统，角色规则以「**冻结声明 + 纯 Hook + 类型化 Effect + 唯一原子写入口**」的通用流水线表达。

你的职责是在**不破坏既有架构承诺**的前提下扩展角色，并交付可被 `pytest --cov-fail-under=100`（statement + branch 全覆盖）门禁接受的代码。

---

## 【背景信息】

项目刚完成一轮架构重构，官方承诺是：**新增角色只需增加一个 `roles/*.py` 声明文件并注册，不必改动任何核心模块**。守卫、白痴、白狼王三个角色被当作该承诺的验收样例。

现在产品侧要求补齐三个角色与对应板子：

1. **骑士**（预女猎骑板）
2. **狼美人**（狼美人板）
3. **老酒鬼**（狼美人板的第 4 个民坑，免疫狼美人魅惑）

团队提供了一份「既有实现说明」（见【输入材料】C 节）供你参考。**该说明由一位刚离职的工程师撰写，未经复核。**

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
    facts: Mapping[str, JsonValue]          # 见下方「投影器实际注入的 facts 键」
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
    visibility_namespaces: frozenset[str] = frozenset()   # 仅 PUBLIC/ACTOR/CAMP/RELATION
    allowed_effects: frozenset[EffectKind] = frozenset()
    tags: frozenset[str] = frozenset()                    # 目前无任何角色使用
    dependencies: frozenset[str] = frozenset()
    exclusions: frozenset[str] = frozenset()
    min_count: int = 0
    max_count: int | None = None
    instructions: str = ""
```

**Hook 签名约定**（`roles/guard.py`、`roles/werewolf_king.py` 已验证可运行）：

- `is_applicable(context) -> bool`
- `validate(context, command) -> tuple[RuleViolation, ...]`
- `resolve(context, command) -> tuple[GameEffect, ...]`
- `react(context) -> tuple[GameEffect, ...]`（响应窗口用，**不经过 `is_applicable`**，Hook 内部须自行复查）
- `aggregate(...)`（仅多座位共享契约需要）

`GameEffect` 必填 `effect_id`（用 `derive_effect_id(context.action_key, ordinal)` 生成）、`kind`、`source_action_key`，并携带 `expected_revision=context.revision` 与 `source_event_id=context.source_event_id`；写操作需给 `preconditions`（如 `{"resource_equals": {"resource": "x", "value": 1}}`、`{"status_present": {"status": "y", "present": False}}`）。

**Effect 载荷字段白名单**（`effect_applier.py` 严格校验，多一个字段即 `EffectRejected`）：

```python
EffectKind.CONSUME_RESOURCE:      {"target", "resource", "amount"}
EffectKind.SET_RESOURCE:          {"target", "resource", "value"}
EffectKind.SET_PRIVATE_DATA:      {"target", "key", "value"}
EffectKind.ADD_STATUS:            {"target", "status"}
EffectKind.REMOVE_STATUS:         {"target", "status"}
EffectKind.ADD_RELATION:          {"target", "relation", "other_seat"}
EffectKind.REMOVE_RELATION:       {"target", "relation", "other_seat"}
EffectKind.RECORD_PRIVATE_FACT:   {"target", "namespace", "fact"}
EffectKind.SUBMIT_DAMAGE:         {"target", "amount", "cause"}
EffectKind.SUBMIT_PROTECTION:     {"target", "amount"} 或 {"target", "amount", "source"}
EffectKind.MARK_DEATH:            {"target", "cause"}
EffectKind.EMIT_EVENT:            {"event_type", "payload"}
```

> `SUBMIT_PROTECTION` 的 `source` 只接受 `"guard"` 或 `"witch_antidote"`，其他值被拒。

---

### B. 既有实现模板与关键机制（可直接运行，请作为风格与正确性参照）

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
# resolve 产出：CONSUME_RESOURCE(explode) → SUBMIT_DAMAGE(self)
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
# app/core/night_settlement.py —— 夜晚伤害结算（唯一把 pending damage 变成死亡的地方）
def settle(damage, protection, seats, alive, round_number):
    """把 pending damage/protection 折算为死亡批次。"""
    damage_totals, protection_totals = {}, {}
    wolf_totals, other_totals = {}, {}
    protection_sources: dict[int, set[str]] = {}
    ...
    for record in damage:
        target, amount = _record(record, seats, frozenset({"target", "amount", "cause"}))
        cause = record["cause"]          # 任意 token 字符串；"wolf_kill" 特判为狼刀
        _add(damage_totals, target, amount)
        if cause == "wolf_kill":
            _add(wolf_totals, target, amount)
        else:
            _add(other_totals, target, amount)
    ...
    for target in sorted(damage_totals):
        double_save = ("wolf_kill" in damage_causes[target]
                       and {"guard", "witch_antidote"}.issubset(
                           protection_sources.get(target, set())))
        lethal_wolf = double_save or wolf_totals.get(target, 0) > protection_totals.get(target, 0)
        lethal_other = other_totals.get(target, 0) > 0
        if not resulting_alive[target] or not (lethal_wolf or lethal_other):
            continue
        resulting_alive[target] = False                     # ← 立即判定死亡，无延迟机制
        deaths.append({"seat": target, "cause": cause, "round_number": round_number})
    return tuple(deaths), resulting_alive
```

```python
# app/core/effect_applier.py —— MARK_DEATH 是立即死亡，不读任何状态
else:
    if not alive[target]: raise EffectRejected("player already dead")
    alive[target] = False
    events.append({"event_type": "PLAYER_DIED",
                   "payload": {"seat": target, "cause": payload["cause"]},
                   "visibility": effect.visibility})
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
# app/core/context_projector.py —— 投影器实际注入 ActionContext.facts 的全部键
facts["selected_target"]    = {"seat": command.target_seat, "camp_label": camp}
facts["actor_identity"]     = {"seat": ..., "role_id": ..., "camp_id": ...}
facts["camp_members"]       = (...)          # 同阵营成员
facts["sheriff"]            = ...            # 开警长时
facts["private_checks"]     = (...)          # 预言家本人查验记录
facts["wolf_kill_target"]   = ...            # 狼队共享的当夜刀口
# 注意：投影器不注入其他座位的 runtime.statuses / runtime.relations
```

```python
# app/core/role_runtime.py —— initial_resources 在开局自动落账
def initialize_role_resources(state, specs, config_version):
    """把每个 spec.initial_resources 逐座位 SET_RESOURCE 写入 runtime；
    已有 resource_setup_digest 时幂等返回。"""
    for seat, player in sorted(state.players.items()):
        spec = specs[player.role]
        for name, raw in sorted(spec.initial_resources.items()):
            declared.append((seat, name, value))
    ...
    # 只写 SET_RESOURCE；不写 ADD_STATUS
```

```python
# app/models/game.py —— 阵营判定
def alive_by_camp(self, camp: str) -> dict[int, PlayerState]:
    return {s: p for s, p in self.players.items() if p.camp == camp and p.is_alive}
```

```python
# app/core/vote_service.py —— 角色无关的投票资格状态（由角色用 ADD_STATUS 授予）
NO_VOTE_STATUS = "no_vote"           # 有此状态者不投票
EXILE_IMMUNE_STATUS = "exile_immune" # 有此状态者不进放逐候选
```

```python
# app/roles/hunter.py —— 猎人开枪的响应窗口配置
_SHOOT_REASONS = frozenset({"wolf_kill", "exile", "hunter_shot", "self_explode"})
# 响应契约：schedule_point=DAWN_REACTION, response_event_types={"PLAYER_DIED"},
#           response_reasons=_SHOOT_REASONS, per_window_limit=1, per_game_limit=1
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
    "EXILE_CANCELLED": ("exile_cancelled", frozenset({"target_seat", "round_number"})),
    "SELF_EXPLODE": ("self_explode", frozenset({"seat", "target_seat", "round_number"})),
    "SHERIFF_ELECTED": ("sheriff_elected", frozenset({"seat", "round_number", "reason"})),
    ...  # 另有 TECHNICAL_ABSTAIN / SHERIFF_BADGE / SHERIFF_RUN / SHERIFF_WITHDRAW /
         #      SHERIFF_VOTE / SHERIFF_SIDE
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
    {"id": "nine-player-standard",    "name": "九人标准场",     ...},
    {"id": "ten-player-standard",     "name": "十人标准场",     ...},
    {"id": "twelve-player-idiot",     "name": "十二人预女猎白", ...},
    {"id": "twelve-player-wolf-king", "name": "十二人白狼王",   ...},
]  # 共 4 条

FIELD_CONSTRAINTS = {"min_players": 4, "max_players": 12, ...}
```

---

### C. 团队「既有实现说明」（⚠️ 未经复核，供参考）

> **1. 关于胜负判定**：神职（god）是通过 `RoleSpec.tags` 打标识别的，`rule_engine.py` 读 `tags` 来统计存活神职，所以新增神职只要在 spec 里加 `tags={"god"}` 就行，不需要动 `rule_engine.py`。
>
> **2. 关于狼队识别**：狼队成员按 `role == "wolf-killer-werewolf"` 判定，所以新加的狼阵营角色（例如狼美人）必须在代码里显式声明自己的 `role_id` 并复用普通狼人的契约，否则不会被算进狼队。
>
> **3. 关于开局前生效的技能**：老酒鬼的魅惑免疫、骑士的翻牌裁决都应在开局前确定，请使用 `SchedulePoint.GAME_SETUP` 承载；该调度点已预留给开局阶段使用。
>
> **4. 关于私有数据复用**：狼美人的「不能连续两晚魅惑同一人」限制，直接复用守卫已有的 `last_guarded` 私有数据字段即可，避免新增字段导致存档不兼容。
>
> **5. 关于一次性技能**：骑士的「一局只能发动一次」请用 `per_game_limit=1` 表达，与白痴翻牌的做法保持一致。
>
> **6. 关于延迟死亡**：老酒鬼「中毒/负伤当天不死、次日发言结束后才死」可以用 `ADD_STATUS` 授予一个状态（例如 `delayed_death`）来表达，不需要改动夜晚结算逻辑。

---

### D. 三个待实现角色的规则原文（**唯一权威规则来源，与 C 节冲突时以本节为准**）

**骑士（Knight）**，好人阵营，**神职**：

- 白天全体发言结束、**放逐投票开始之前**，骑士可以翻出底牌并指定一名玩家，由法官宣布该玩家是狼人还是好人。
- 若被指定者是**狼人**：该玩家立即死亡，**白天立即结束，马上进入夜晚**（当日放逐投票取消）。
- 若被指定者是**好人**：骑士以死谢罪，**且无遗言**；当天的发言与投票**照常继续**。
- 该技能**一局只能发动一次**。

**狼美人（Wolf Beauty）**，狼人阵营：

- 每晚与狼队友一同睁眼、**共享狼刀投票**。
- 在参与杀人后，可**单独**魅惑一名好人阵营玩家（**不能连续两晚魅惑同一人**）。
- 狼美人**出局时**，当晚被其魅惑的玩家**随之殉情**（一同死亡）。
- 狼美人**不能自爆、不能自刀**；除这两种以外的任何出局方式都可以带人。

**老酒鬼（Old Drunkard）**，好人阵营，**平民牌**（不计入神职）：

- **免疫狼美人的魅惑**（被魅惑无效，不会因此殉情）。
- 被**撒毒**（女巫毒药）或**射杀**（猎人枪击）后，分别进入**中毒**与**负伤**状态：**当天不会死亡**，而是在**第二天发言结束后死亡**。
- 老酒鬼不计入神职；其存活与死亡按平民规则计入屠边判定。

---

### E. 板子配置

| 板子 ID | 名称 | 配置 |
| --- | --- | --- |
| `twelve-player-knight` | 十二人预女猎骑 | 4 狼人 / 预言家·女巫·猎人·骑士 / 4 平民；不开警长 |
| `twelve-player-wolf-beauty` | 十二人狼美人 | 3 狼人 + 1 狼美人 / 预言家·女巫·猎人·守卫 / 3 平民 + 1 老酒鬼；开警长 |

---

## 【任务要求】

请基于以上材料，交付一套可落地的实现方案与代码：

1. **设计并写出** `app/roles/knight.py`、`app/roles/wolf_beauty.py`、`app/roles/old_drunkard.py` 三个角色声明文件（含 spec、Hook、必要的事件与资源），风格与 B 节模板一致。
2. **指明**为让这三个角色在真实对局中正确生效，除新增角色文件外，还必须修改**哪些既有文件**的**哪些位置**，并说明每处修改的**具体原因**与**最小改法**。
3. **逐条判定**：对三个角色各自声明「能否在不修改核心模块的前提下实现」，并给出**可验证的材料证据**。
4. **补齐**两个新预设到 `STANDARD_PRESETS`，并写出对应的 `ROLE_METADATA` 条目。
5. **列出**需要同步更新的既有测试断言，以及需要新增的测试文件与关键用例（每条用例一句话说明它锁定什么行为）。
6. **评估** C 节「既有实现说明」中**每一条**陈述的正确性，逐条给出「正确 / 错误」的判定与证据。

---

## 【约束条件】

**必须满足：**

1. Hook 必须是**纯函数**：函数体内**禁止**读写任何全局状态、禁止直接修改 `GameState`、禁止发起 I/O 或 LLM 调用；一切状态变更必须表达为 `GameEffect`。
2. 所有写操作必须携带 `expected_revision` 与 `source_event_id`，并给出 `preconditions`，保证 CAS 原子性。
3. 三个角色的规则必须**严格来自 D 节**。规则里没有写的能力**一律不得实现**（例如：不得给骑士增加夜间行动，不得让狼美人在被毒杀时额外扩大技能范围，不得给老酒鬼增加除中毒/负伤之外的其他延迟死亡来源）。
4. 角色新增必须**不破坏**「新增角色不改核心模块」这一架构承诺；若某处确实无法避免修改核心模块，**必须显式指出、说明为何无法用纯 Hook 表达、并给出最小改法**，不得默默绕过，也不得谎称「无需修改」。
5. 私有信息边界：狼美人的魅惑目标、骑士的裁决结果在**公开前**不得进入任何 PUBLIC 可见的事件载荷。
6. `RoleSpec.visibility_namespaces` 只能取 `PUBLIC` / `ACTOR` / `CAMP` / `RELATION` 四个值。
7. 每个 Effect 的 `payload` 字段必须**严格**落在 A 节载荷白名单内。

**禁止（负向约束）：**

8. **严禁编造**材料中未给出的 API、字段、枚举值、文件路径、函数签名或调度点。若某能力在材料中不存在，必须明确说明「材料未提供该能力」并给出替代方案，**不得虚构**。
9. **严禁**直接照搬 C 节的实现建议。C 节是待验证的参考意见，不是权威依据。
10. **严禁**在未核对材料的情况下断言某个 `SchedulePoint` 已被引擎调度、某个字段已被占用、某个机制已被支持。
11. 不得修改 `SchedulePoint`、`EffectKind`、`ActionContract`、`RoleSpec` 这四个冻结值类型的字段定义。

---

## 【输出格式】

严格按以下 Markdown 结构输出，**不得增删这十个二级标题**：

````markdown
## 一、方案概述
（不超过 400 字：三个角色各自落在哪个调度点、产出哪几类 Effect、哪些部分无法用纯 Hook 表达）

## 二、架构可行性判定
| 角色 | 特性 | 能否纯 Hook 实现 | 材料证据 | 若不能，最小改法 |
（必须覆盖：骑士一次性裁决 / 狼美人共享狼刀 / 狼美人连魅限制 / 狼美人殉情 / 老酒鬼魅惑免疫 / 老酒鬼延迟死亡）

## 三、骑士实现
### 3.1 契约设计
| 字段 | 取值 | 依据 |
（逐字段列出 contract_id / schedule_point / order / action_types / actions_requiring_target / fallback_action_type / allowed_effects / visibility_namespaces / response_event_types / response_reasons / per_window_limit / per_game_limit）
### 3.2 Hook 实现
```python
# 完整可运行代码
```
### 3.3 阶段影响
（说明裁决为狼 / 为好人两种分支各自如何影响白天流程，涉及哪些通用事件名）

## 四、狼美人实现
（结构同第三章）

## 五、老酒鬼实现
（结构同第三章；须单独说明延迟死亡的实现位置与触发时机）

## 六、必须修改的既有文件清单
| # | 文件 | 位置 | 改动 | 原因（一句话） | 属于核心模块？ |
（若认为某处不必改，也需列出并注明「无需修改 + 理由」）

## 七、预设与目录元数据
```python
# STANDARD_PRESETS 与 ROLE_METADATA 的新增条目
```

## 八、测试清单
| 类型（更新/新增） | 文件 | 用例 | 锁定的行为 |
（更新项须写出「原断言值 → 新断言值」）

## 九、C 节实现说明逐条核验
| # | C 节原陈述 | 判定（正确/错误） | 证据 | 若不采纳，正确做法是 |
（必须覆盖 C 节全部 6 条）

## 十、风险与未决问题
（列出你认为材料不足以判定、需要出题方澄清的点；没有则写「无」）
````

---

# 第二部分：真值、陷阱清单与评分细则（**出题人自用，勿下发**）

---

## 一、真值基线（Ground Truth）

### 1.1 架构可行性判定（本题的核心考点）

| 角色 | 特性 | 能否纯 Hook | 材料证据 | 最小改法 |
| --- | --- | --- | --- | --- |
| 骑士 | 一次性裁决（翻牌指认） | ✅ **能** | 白痴已在 `EXILE_VERDICT` 用 `react` + `per_game_limit` 跑通同类裁决 | 无需改核心 |
| 骑士 | 裁决为狼 → 白天立即结束 | ✅ **能** | 白狼王自爆已用 `EMIT_EVENT("DAY_INTERRUPTED")` 跑通同一机制 | 复用该事件名 |
| 狼美人 | 共享狼刀投票 | ✅ **能** | `WEREWOLF_KILL_CONTRACT` 可被多角色原样复用，注册表在同 id 声明完全一致时合并计票 | 无需改核心 |
| 狼美人 | 连魅限制 | ✅ **能** | `guard.py` 用 `initial_private_data={"last_guarded": None}` + `SET_PRIVATE_DATA` 实现同构限制 | 用**独立**字段名 |
| 狼美人 | 出局时殉情 | ✅ **能** | `ADD_RELATION` + `SUBMIT_DAMAGE` 均在白名单内；`hunter.py` 已证明 `PLAYER_DIED` 响应窗口可用 | 无需改核心（响应窗口配置需覆盖全部出局 cause） |
| 老酒鬼 | 免疫魅惑 | ❌ **不能** | 投影器只注入 `selected_target` / `actor_identity` / `camp_members` / `sheriff` / `private_checks` / `wolf_kill_target`，**不注入其他座位的 `runtime.statuses`** → 狼美人 Hook 无法读目标的免疫标记；且 `initialize_role_resources` **只写 `SET_RESOURCE`，不写 `ADD_STATUS`**，开局无法自动授予免疫状态 | 改 `context_projector`，把目标座位的可见状态纳入 `selected_target` |
| 老酒鬼 | 延迟死亡 | ❌ **不能** | `night_settlement.settle()` 在伤害致死时**立即**置 `resulting_alive[target] = False` 并产出死亡记录，无任何延迟判定点；`MARK_DEATH` 也是立即 `alive[target] = False`。`ADD_STATUS("delayed_death")` 没有任何代码读取它 | ① `settle()` 识别「已带中毒/负伤状态」的目标时跳过本次致死；② 在引擎白天推进处新增「次日发言结束后结算延迟死亡」 |
| 老酒鬼 | 计入平民（屠边） | ⚠️ **潜在缺陷**（非阻断） | `"old-drunkard"` 不含 `"villager"` 子串 → 漏出 `alive_villagers`；但该分支被更早的 `alive_gods == 0` 掩盖，**只改 `reason` 标签、不改胜负结论**（实测复现） | 建议一并修正为读 `tags`，但不属阻断项 |

> **结论**：三个角色共 **8 项**特性中，**5 项可完全遵守「不改核心」承诺**（骑士 2 项 + 狼美人 3 项），**老酒鬼的 2 项核心特性都必须改核心模块**，另 1 项（计入平民）是建议一并修正的**潜在缺陷**。这是本题最重要的专业判断点。
>
> 提示模型输出的第二章只强制覆盖其中 **6 项**（不含「裁决为狼结束白天」与「计入平民」）；后两项属加分观察点，见 T11/T12。

### 1.2 骑士（Knight）

| 项 | 正确取值 | 依据 |
| --- | --- | --- |
| `role_id` | `wolf-killer-knight` | 与既有 `wolf-killer-*` 命名一致 |
| `camp_id` | `good` | 好人阵营 |
| 神职归属 | **是**（必须计入 `alive_gods`） | D 节明示「神职」 |
| 调度点 | `EXILE_VERDICT` | 裁决发生在「放逐投票之前」，与白痴同一裁决点；**`GAME_SETUP` 是错的** |
| `contract_id` | 建议 `knight_duel` | 语义化，无强制 |
| `action_types` | `("duel", "pass")` | 需要「不发动」的合法选项 |
| `fallback_action_type` | `"pass"` | 与既有约定一致 |
| `actions_requiring_target` | `frozenset({"duel"})` | 裁决必须指定目标 |
| 一次性表达 | **`initial_resources={"duel": 1}` + `CONSUME_RESOURCE` + `preconditions`** | 与白狼王自爆一致；`per_game_limit` 只约束被接受的命令，`pass` 也会占用窗口，不足以保证「一局一次」 |
| 需要 LLM 决策 | **需要**（`resolve`） | 与白痴不同：白痴是被动触发（`react`），骑士是主动发动 |
| 关键 Effect 序列 | 1) `CONSUME_RESOURCE(duel)` 2) 目标为狼时 `SUBMIT_DAMAGE(target, cause="knight_duel")` 3) 目标为好人时 `SUBMIT_DAMAGE(self, cause="knight_duel")` 4) `EMIT_EVENT` 公开裁决结果 5) 目标为狼时 `EMIT_EVENT("DAY_INTERRUPTED")` | 白天结束走既有 `DAY_INTERRUPTED` 通用事件 |
| 遗言 | 骑士以死谢罪**无遗言**；被裁决致死的狼人是否留遗言，D 节未写 | 应在第十章标为待澄清 |
| 公开边界 | 裁决结果（好人/狼人）是**公开信息**（法官宣布）；`duel` 资源用量属私有 | D 节 + 约束 5 |

### 1.3 狼美人（Wolf Beauty）

| 项 | 正确取值 | 依据 |
| --- | --- | --- |
| `role_id` | `wolf-killer-wolf-beauty` | 命名一致 |
| `camp_id` | `werewolf` | 狼人阵营 |
| 契约 | `WEREWOLF_KILL_CONTRACT`（**原样复用，声明须完全一致**）+ 新增魅惑契约 | 注册表仅在同 id 声明完全一致时合并计票 |
| 魅惑调度点 | `NIGHT_ACTION` | 独立于狼刀的单人行动；`order` 建议取 40（女巫 20 / 守卫 50 之间），可自定但须说明理由 |
| 连魅限制 | **必须用独立私有数据字段**（如 `last_charmed`），**禁止复用 `last_guarded`** | `last_guarded` 被 `validate_guard_action` 读取；复用会让守卫「不能连守」校验误判 → 守卫技能静默失效 |
| 殉情触发 | 狼美人**出局时**触发，需覆盖夜刀/毒/放逐/枪杀/被自爆带走等**除自爆与自刀以外**的所有出局路径 | D 节 |
| 殉情表达 | `ADD_RELATION`（记录魅惑关系）+ `SUBMIT_DAMAGE(target, cause="charm")` | 两者均在载荷白名单内；`relations` 机制已实现且当前零角色使用 |
| 殉情响应窗口 | 需覆盖 `hunter.py` 已用的 cause 集合 `{wolf_kill, exile, hunter_shot, self_explode}` **再加上** `witch_poison`、`knight_duel` | 不覆盖则会漏掉毒杀/骑士裁决两种出局路径 |
| 禁止能力 | **不得**实现自爆、**不得**允许自刀 | D 节负向约束 |

### 1.4 老酒鬼（Old Drunkard）

| 项 | 正确取值 | 依据 |
| --- | --- | --- |
| `role_id` | `wolf-killer-old-drunkard` | 命名一致 |
| `camp_id` | `good` | 好人阵营 |
| 神职归属 | **否**（平民牌） | D 节明示「不计入神职」；但需注意 `"old-drunkard"` 不含 `"villager"` 子串 → **当前 `rule_engine` 也不会把它计入 `alive_villagers`** → 必须一并修正 |
| 魅惑免疫表达 | `initial_resources={"charm_immune": 1}` 是**可行的开局初始化路径**（`initialize_role_resources` 会自动落账），但**跨角色读取需要改投影器** | 见 1.1 |
| 延迟死亡来源 | 仅**毒杀（`witch_poison`）与枪杀（`hunter_shot`）**两种 cause | D 节；**夜刀不触发延迟** |
| 延迟死亡表达 | 需在 `settle()` 与白天推进处各加一个判定点 | 见 1.1 |
| 状态命名 | 建议 `poisoned` / `wounded`（对应中毒/负伤），或统一 `delayed_death` + 私有数据记录 cause | D 节区分了两种状态，两种建模都算正确，但须自洽 |

### 1.5 必须修改的既有文件（真值清单）

| # | 文件 | 位置 | 改动 | 原因 | 核心模块 |
| --- | --- | --- | --- | --- | --- |
| 1 | `app/core/rule_engine.py` | 第 10–16 行 | 新增骑士的神职计数（`alive_gods` 必须含骑士）；建议整体改为读 `RoleSpec.tags` | `"knight"` **不命中** seer/witch/hunter/guard/idiot 任一子串 → 骑士漏出 `alive_gods` → **「神职全灭」提前触发，胜负判定反转**（实测复现：2狼+骑士为唯一存活神职时，正确应 `continue`，漏计则误判狼胜）。老酒鬼漏出 `alive_villagers` 属**潜在**缺陷：被更早的 `alive_gods == 0` 掩盖，只改 `reason` 标签、不改胜负结论 | ⚠️ 是 |
| 2 | `app/core/night_settlement.py` | `settle()` 致死判定分支 | 目标已带中毒/负伤状态时跳过本次致死 | 否则老酒鬼当天就死，延迟机制完全失效 | ⚠️ 是 |
| 3 | `app/core/game_engine.py` | 白天发言结束后的推进处 | 新增延迟死亡结算：结算中毒/负伤座位并发布 `PLAYER_DIED` | 延迟死亡需要「次日发言结束后」这个时点，纯 Hook 无此调度点 | ⚠️ 是 |
| 4 | `app/core/context_projector.py` | `selected_target` 组装处 | 把目标座位的可见状态纳入投影 | 否则狼美人无法得知目标免疫魅惑 | ⚠️ 是 |
| 5 | `app/roles/registry.py` | 顶部 import 区 | 导入并注册三个 SPEC | 不注册则角色不可用 | 否 |
| 6 | `app/catalog.py` | `ROLE_METADATA` | 新增 3 条中文名/图标/描述 | 前端角色选择依赖 | 否 |
| 7 | `app/catalog.py` | `STANDARD_PRESETS` | 新增 2 个预设 | 产品要求 | 否 |
| 8 | `app/services/audience_projector.py` | `_PUBLIC_ROLE_ACTIONS` / `_PUBLIC_REASONING_EVENTS`（复合事件另需 `_AUDIENCE_EVENTS`） | 新增三角色事件映射 | 不加则**观众端看不到这三个角色的行动** | 否 |
| 9 | `tests/test_catalog_routes.py` | 第 56、114 行 | `== 8` → `== 11` | 角色数变化 | 否 |
| 10 | `tests/test_catalog_routes.py` | 第 70、124 行 | `== 4` → `== 6` | 预设数变化 | 否 |
| 11 | 前端 `theme/tokens.ts` | `ROLE_COLORS` | 新增 3 个角色色 | 组件禁止硬编码色值 | 否 |
| 12 | 前端 `components/shared/RoleIcon.tsx` | `ROLE_ICONS` | 新增 3 条图标映射 | 否则显示「未知身份」问号 | 否 |
| 13 | 前端 `components/game/ActivityCard.tsx`、`CenterDisplay.tsx` | 事件名映射表 | 新增事件中文名 | 否则事件卡片显示原始英文键 | 否 |

### 1.6 门禁与运行影响

- 新增角色会改变 `registry.digest`，处于 `interrupted` 的旧局**无法续跑**；上线前须先结束进行中的对局。
- 后端覆盖率门禁为 statement + branch **100%**（`--cov-fail-under=100`），新代码每条分支都必须有测试。

---

## 二、陷阱清单（区分度设计）

| # | 陷阱类型 | 埋设位置 | 错误做法（差模型） | 正确做法（好模型） | 分值 |
| --- | --- | --- | --- | --- | --- |
| T1 | **假前提 + 隐蔽 Bug** | C 节第 1 条：神职靠 `tags` 识别 | 照搬「加 `tags={"god"}` 即可」，不核对 `rule_engine.py`；骑士不计入神职 → 屠边判定反转 | 指出实际是**角色名子串匹配**、`rule_engine` **根本不读 `tags`**，`"knight"` 不命中任何子串 → **必须改 `rule_engine.py`**；`tags` 只能作为配套的长期方案，单独加无效 | 4 |
| T2 | **假前提（与明文规则冲突）** | C 节第 2 条：狼队按 `role == "wolf-killer-werewolf"` 判定 | 照搬该判定，或为狼美人另写一套狼队识别 | 指出 `alive_by_camp` 实际按 `p.camp == camp`，狼美人只需 `camp_id="werewolf"` 自动入队 | 3 |
| T3 | **不存在的机制** | C 节第 3 条：用 `GAME_SETUP` 承载开局前技能 | 把魅惑免疫/骑士裁决挂在 `GAME_SETUP`，或声称该点已在使用 | 指出 `GAME_SETUP` 只作为 `ActionContext.schedule_point` 的默认值出现，**全项目零调度调用**；魅惑免疫须走 `initial_resources` | 3 |
| T4 | **复用即破坏（隐蔽）** | C 节第 4 条：复用 `last_guarded` | 复用 `last_guarded` 存魅惑目标 → `validate_guard_action` 读到被污染的值，**守卫「不能连守」校验静默失效** | 使用独立字段 `last_charmed`，并说明复用会跨角色污染守卫校验 | 4 |
| T5 | **机制误用** | C 节第 5 条：`per_game_limit=1` 表达一局一次 | 用 `per_game_limit=1` 表达骑士一次性 | 指出 `pass` 也计入窗口/次数，必须用**资源 + `preconditions`**（白狼王自爆即此模式） | 2 |
| T6 | **核心陷阱：假称纯 Hook 可解** | C 节第 6 条：延迟死亡用 `ADD_STATUS` 即可 | 照搬「加个 `delayed_death` 状态即可」，声称无需改核心；**老酒鬼当天就死，机制静默失效** | 指出 `settle()` 立即置死、`MARK_DEATH` 立即置死、**没有任何代码读取该状态** → 必须改 `night_settlement.py` + `game_engine.py` | 6 |
| T7 | **核心陷阱：跨角色读取能力缺失** | 无 C 节提示，需自行发现 | 让狼美人 Hook 读目标座位的免疫状态，声称可行 | 指出投影器只注入 6 个固定 facts 键，**不含其他座位的 statuses**；且 `initialize_role_resources` 只写 `SET_RESOURCE` 不写 `ADD_STATUS` → 免疫判定需改 `context_projector` | 5 |
| T8 | **不完整的 cause 覆盖** | D 节规则细节 | 殉情响应窗口只配 `PLAYER_DIED` 而漏掉 cause 集合，或只覆盖 `wolf_kill` | 覆盖 `{wolf_kill, exile, hunter_shot, self_explode, witch_poison, knight_duel}`，并说明「除自爆与自刀外全部路径」 | 3 |
| T9 | **强约束遵循（11 条）** | 约束条件全节 | 漏 1–2 条（最常见：漏「Hook 纯函数」、漏「载荷白名单」、漏「禁止编造」） | 11 条全部满足且可逐条核对 | 3 |
| T10 | **抗幻觉** | D 节未给遗言细则、未给魅惑 `order`、未给状态命名 | 编造「被裁决的狼人无遗言」「魅惑 order 必须是 40」等材料外结论 | 在第十章明确标为**待澄清**，给出建议值并声明是自定 | 2 |
| T11 | **加分项：区分「真缺陷」与「潜在缺陷」** | B 节 `rule_engine.py` + C 节第 1 条 | 笼统说「骑士和老酒鬼都漏计 → 都导致胜负出错」，不做区分 | 指出骑士漏计**直接反转胜负**（神职全灭提前触发），而老酒鬼漏计被更早的 `alive_gods == 0` 掩盖、**只改 reason 标签不改结论** | 加分 3 |
| T12 | **加分项：发现既有角色同类缺陷** | B 节 `rule_engine.py`（无 C 节提示） | 只处理三个新角色，不检查既有角色 | 发现 **`wolf-killer-werewolf` 与 `wolf-killer-werewolf-king` 同样不命中任何子串**，即**白狼王角色上线时就已漏计**（实测已存 218 局中有 12 个白狼王座位） | 加分 3 |

**设计要点**：T1–T6 全部藏在 C 节「看似合理、且有具体字段名支撑」的建议里，且**每条都能被 B 节材料证伪**。其中 **T6 与 T7 是本题的主要区分点**——它们要求模型真正读完 `night_settlement.settle()`、`effect_applier` 的 `MARK_DEATH` 分支、以及 `context_projector` 的 facts 键清单，才能发现「看起来能表达，实际没有任何代码消费它」。T4 是最容易踩的：即使模型已识破 C 节整体不可信，也可能因「复用更省事」而中招。**T11/T12 是精度加分项**，用于区分「知道有问题」与「知道问题的真实影响边界」。

**T2 的精度提示（评卷时勿误判）**：`wolf-killer-wolf-beauty` 的 role_id **确实包含** `"werewolf"` 子串，所以 C 节第 2 条的**结论**（复用普通狼人契约即可入队）碰巧成立，错的是**推理**（`alive_by_camp` 按 `camp` 而非 `role` 判定）。模型若指出这一层，属于加分表现；模型若只说「C 节错了」而说不出为什么，按 T2 的 1 分档计。

---

## 三、评分细则（满分 100，按点计分/扣分制）

### 维度权重

| 维度 | 权重 | 说明 |
| --- | --- | --- |
| A. 架构可行性判定（第二章） | 25 | 六项强制特性逐项判定是否正确、证据是否充分 |
| B. 角色实现准确度（第三～五章） | 25 | 契约字段、Effect 序列、时机选择、载荷合法性 |
| C. 陷阱识别（T1–T10，满分 22） | 22 | 逐条判定 + 材料证据 |
| D. 既有文件清单完整性（第六章） | 16 | 是否找全 13 处必改点、是否正确标注核心模块 |
| E. 约束遵循（11 条） | 7 | 逐条可核对 |
| F. 输出格式规范 | 5 | 十个二级标题完整、表格齐全 |
| **加分项（T11 + T12，上限 +6，总分封顶 100）** | +6 | 精度与主动性加分，不加不扣 |

### A. 架构可行性判定（25 分）

| 采分点 | 分值 | 判定标准 |
| --- | --- | --- |
| 骑士两项特性判定为「可纯 Hook」 | 3 | 每项 1.5 分；判成「必须改核心」得 0 |
| 狼美人共享狼刀判定为「可纯 Hook」 | 2 | 判错得 0 |
| 狼美人连魅限制判定为「可纯 Hook」 | 2 | 判错得 0 |
| 狼美人殉情判定为「可纯 Hook」 | 3 | 判成「必须改核心」得 0 |
| **老酒鬼魅惑免疫判定为「不能」**（T7） | 5 | **判成「能」得 0**；判定为「不能」得 3；给出「投影器不注入 statuses」的具体证据得 5 |
| **老酒鬼延迟死亡判定为「不能」**（T6） | 6 | **判成「能」得 0**；判定为「不能」得 3；给出「`settle()` 立即置死 + 无代码读取该状态」的证据得 6 |
| 每项判定均附材料证据（引用具体函数/字段） | 4 | 无证据的判定不计分 |

### B. 角色实现准确度（25 分）

| 采分点 | 分值 | 判定标准 |
| --- | --- | --- |
| 骑士落在 `EXILE_VERDICT` | 4 | 写 `GAME_SETUP` 或 `DAY_ACTION` 得 0 |
| 骑士用资源 + `preconditions` 表达一次性 | 4 | 仅用 `per_game_limit` 得 0 |
| 骑士为狼时走 `DAY_INTERRUPTED` 结束白天 | 4 | 自造新事件名得 0 |
| 骑士为好人时骑士自身死亡且当日流程继续 | 3 | 遗漏「继续」得 0 |
| 狼美人原样复用 `WEREWOLF_KILL_CONTRACT`（声明一致） | 3 | 自建一份不同声明的狼刀契约得 0 |
| 狼美人连魅限制用独立字段 | 3 | 复用 `last_guarded` 得 0（并触发 T4 扣分） |
| 老酒鬼延迟死亡实现位置正确（`settle()` + 白天推进） | 4 | 只写在角色文件里得 0 |

### C. 陷阱识别（T1–T10，满分 22）

| 项 | 分值 | 判定标准 |
| --- | --- | --- |
| T1 神职子串匹配 | 4 | 仅说「C 节错误」得 1；指出子串匹配机制得 2；**明确 `rule_engine` 不读 `tags`、单加 `tags` 无效、必须改 `rule_engine.py`** 得 4 |
| T2 狼队按 camp 判定 | 3 | 仅否定得 1；引 `alive_by_camp` 实现得 3；**额外指出「role_id 含 werewolf 子串故 C 的结论碰巧成立」再加 1（计入加分项）** |
| T3 `GAME_SETUP` 未被调度 | 3 | 仅否定得 1；指出它只作默认值、全项目无调用得 3 |
| T4 复用 `last_guarded` 破坏守卫 | 4 | **未识别得 0**；识别不当得 2；说出「守卫连守校验静默失效」得 4 |
| T5 `per_game_limit` 不足以表达一局一次 | 2 | 未识别得 0 |
| T6 延迟死亡无法用状态表达 | 6 | **未识别得 0**；仅说「需改核心」得 3；给出 `settle()` / `MARK_DEATH` 证据得 6 |
| T7 跨角色状态读取缺失 | 5 | **未识别得 0**；指出投影器不含 statuses 得 5 |
| T8 cause 覆盖完整性 | 3 | 只覆盖 `wolf_kill` 得 1；覆盖 `{wolf_kill, exile, hunter_shot, self_explode, witch_poison, knight_duel}` 得 3 |
| 逐条覆盖 C 节全部 6 条 | 3 | 每漏一条扣 0.5 |
| 未盲从：最终方案与 C 节建议**无任何一条**一致 | 3 | 每沿用一条错误建议扣 1 |

> 本维度各项分值之和（4+3+3+4+2+6+5+3+3+3 = 36）**超过满分 22**，属刻意的「加分制」设计：先累加实际得分，再按 22 封顶。这样即使漏掉个别陷阱，只要抓住 T6/T7 仍可接近满分；反之若只答对零散小项则拿不到高分，从而拉开档次。

### D. 既有文件清单完整性（16 分）

| 采分点 | 分值 |
| --- | --- |
| 找出 `rule_engine.py` 必改（骑士神职计数）（#1） | 4 |
| 找出 `night_settlement.py` 必改（#2） | 3 |
| 找出 `game_engine.py` 必改（延迟死亡结算时点）（#3） | 3 |
| 找出 `context_projector.py` 必改（#4） | 3 |
| 找出 `registry.py` 注册 + `catalog.py` 元数据与预设（#5#6#7） | 1 |
| 找出 `audience_projector.py` 事件映射（#8） | 1 |
| 找出 `test_catalog_routes.py` 两处断言（#9#10，须写出原值→新值） | 0.5 |
| 找出前端 3 处（tokens / RoleIcon / 事件中文名） | 0.5 |

> 「属于核心模块」列标注正确额外计入 A 维度；每多列一处**不存在的必改点**（幻觉），倒扣 1 分，本维度最低 0 分。

### 加分项（上限 +6，总分封顶 100）

| 项 | 分值 | 判定标准 |
| --- | --- | --- |
| T11 区分「真缺陷」与「潜在缺陷」 | +3 | 指出骑士漏计**直接反转胜负**，而老酒鬼漏计被 `alive_gods == 0` 掩盖、**只改 reason 不改结论** |
| T12 发现既有角色同类缺陷 | +3 | 发现 `wolf-killer-werewolf` 与 `wolf-killer-werewolf-king` 同样不命中任何子串（**白狼王上线时即已漏计**），并建议一并修复 |

### E. 约束遵循（7 分）

11 条约束按可核对性均分（每条约 0.64 分）。**硬否决项**（触发则本题总分不超过 40）：

- 编造材料中不存在的 API / 枚举 / 文件路径 / 调度点；
- Hook 中出现直接修改 `GameState` 或 I/O；
- 实现 D 节规则之外的能力；
- 使用 A 节载荷白名单之外的字段。

### F. 输出格式（5 分）

| 项 | 分值 |
| --- | --- |
| 十个二级标题齐全且顺序正确 | 2 |
| 第二章表格含「材料证据」与「最小改法」两列 | 1 |
| 第六章表格含「属于核心模块」列 | 1 |
| 代码块为完整可运行 Python（非伪代码） | 1 |

### 等级换算

| 等级 | 分数 | 行为特征 |
| --- | --- | --- |
| 优 | 85–100 | T6/T7 均识别并给出源码级证据；A 维度六项判定全对；必改点找全 ≥11 处；方案无一处沿用 C 节错误建议；拿到 T11/T12 加分 |
| 良 | 70–84 | 识别 T6 但漏 T7（或反之）；A 维度错 1 项；必改点 8–10 处；契约设计正确 |
| 中 | 50–69 | 仅识别 T1–T5 中的 1–2 个；把老酒鬼判为「可纯 Hook」；漏 `rule_engine.py` 或 `night_settlement.py` |
| 差 | < 50 | 整体照搬 C 节；或触发任一硬否决项 |

---

## 四、提交前自查（出题人已核对）

| 检查项 | 结论 |
| --- | --- |
| 真实性 | ✅ 题目直接来自本项目「新增骑士 + 狼美人 + 老酒鬼板子」的真实开发任务 |
| 专业性 | ✅ 需读懂冻结流水线、Effect 代数、CAS 前置条件、结算时机与投影边界才能作答 |
| 充分性 | ✅ 材料 A–E 自包含（枚举、载荷白名单、四个模板、结算与投影源码、规则原文、板子配置、误导性参考实现） |
| 明确性 | ✅ 六要素闭环，输出格式为强制十个二级标题，约束 11 条可逐条核对 |
| 可评性 | ✅ 100 分按点计分，T1–T10 各有明确证据要求，加分项单列且可核对，无「分析到位」类主观表述 |
| 区分度 | ⚠️ **尚未用主流模型实跑验证**（规范要求出题人亲自跑测）。建议先用 2–3 个模型跑一遍，重点看 T6/T7 的触发率：预期 T6 触发率 30%–70%、T7 触发率 10%–40%。若两者都被轻松识别，需把 T7 换为更隐蔽的陷阱（例如「狼美人殉情的 cause 覆盖」） |
| 参考答案完整性 | ⚠️ 已给出「真值基线 + 关键采分点清单」（规范允许二者之一），但**未提供完整满分示例代码**。若评测方要求逐行对照，需补一份三角色的参考实现 |

---

## 五、与规范条目的逐条对照（出题人自检记录）

| 规范要求 | 落点 | 状态 |
| --- | --- | --- |
| 任务选型：真场景、真材料 | 全部材料取自本仓库真实源码与真实开发任务 | ✅ |
| 任务选型：专业壁垒、非单一解 | 需判断 6 项特性的架构可行性，答案非唯一（状态命名、order 取值允许自定） | ✅ |
| 任务选型：复杂度充足 | 3 角色 × 6 特性 × 13 处必改点，含多步跨文件推理 | ✅ |
| Prompt 六要素闭环 | 【角色设定】【背景信息】【输入材料】【任务要求】【约束条件】【输出格式】 | ✅ 6/6 |
| 显式约束 4~6 个 | 实际 11 条（含 4 条负向约束） | ✅ 超出要求 |
| 陷阱：假前提 / 数据矛盾 | C 节 6 条未经复核的实现说明，每条均可被 B 节材料证伪 | ✅ |
| 陷阱：抗幻觉 | 约束 8/10 + T10 + 硬否决项 | ✅ |
| 输出格式：指定 Markdown 层级 | 强制十个二级标题 + 四张指定列表格 | ✅ |
| 标准答案：专家真值 | 第二部分「真值基线」（1.1–1.6） | ✅ |
| 标准答案：关键采分点清单 | 评分细则 A–F + 加分项，逐点可勾 | ✅ |
| 客观量化：按点计分/扣分制 | 全部采分点带分值；无主观模糊措辞 | ✅ |
| 客观量化：分值档次有具体行为描述 | 等级换算表四档均绑定可核对的行为特征 | ✅ |
| 自检 1 真实性 | 见上表 | ✅ |
| 自检 2 专业性 | 见上表 | ✅ |
| 自检 3 充分性 | 材料自包含，无需访问仓库 | ✅ |
| 自检 4 明确性 | 六要素 + 10 标题 + 11 约束 | ✅ |
| 自检 5 可评性 | 按点计分、证据要求明确、加分项单列 | ✅ |
| **自检 6 出题人实跑验证** | **未执行**（本机无模型跑测环境） | ❌ **待补** |

### 5.1 反向检查：下发部分**不得**含逐步指导

评测题必须考「判断力」。一旦把流程拆成「第一步做什么、第二步做什么」，题目就退化成照着做的执行题。据此对下发部分（第 1–607 行）做反向扫描：

| 反向检查项 | 扫描方式 | 结果 |
| --- | --- | --- |
| 无「步骤 / 首先 / 其次 / 然后 / 依次 / 逐一」等流程词 | 全文 grep | ✅ 零命中 |
| 无「先读…再判断…最后给出」式解题顺序 | 全文 grep | ✅ 零命中 |
| 任务要求用**交付物动词**而非**过程动词** | 人工核对 6 条 | ✅ 全部为「写出 / 指明 / 判定 / 补齐 / 列出 / 评估」，无「先分析、再比较、最后决定」 |
| 输出格式只规定**文档结构**，不规定**解题顺序** | 人工核对 10 个标题 | ✅ 标题是成果章节（如「必须修改的既有文件清单」），不是行动步骤 |
| 约束条件只规定**结果约束**，不规定**实现方法** | 人工核对 11 条 | ✅ 全部为「必须满足 / 禁止」式结果约束，无「你应该这样实现」 |
| 陷阱未泄露（模型需自行发现矛盾） | grep T1–T12 /「陷阱」/「假前提」 | ✅ 下发部分零命中，陷阱清单仅存在于第二部分 |
| 材料 B 只含**通用模板**，不含本题三角色的任何解法 | grep 三角色名 | ✅ 三角色名仅出现在【背景信息】、C 节诱饵与 D 节规则原文中；B 节模板全部是既有角色（守卫 / 狼人 / 白狼王 / 白痴） |

**一处需向评卷员说明的设计（非违规）**：C 节的 6 条「未复核实现说明」在**形式上**最接近逐步指导（它直接点名 `GAME_SETUP`、`last_guarded`、`per_game_limit`、`ADD_STATUS` 等具体做法）。但它的性质是**待验证的输入材料 / 诱饵**，不是指导：

- 已被显式标注「由一位刚离职的工程师撰写，未经复核」「待验证的参考意见，不是权威依据」；
- 约束 9 明文禁止照搬，任务要求第 6 条要求逐条判定正误；
- 它的**唯一作用**是制造「盲从 vs 纠错」的区分度——若删掉它，T1–T6 六个陷阱将全部失效。

因此 C 节必须保留，且**不得**在下发时附加任何「注意其中有错」的提示。
