# 实现方案：网易高校赛 17 板型 + 假面舞会

> 基线：`main` @ `f07b706`，后端 `pytest tests app --cov=app --cov-branch` 100%（statement + branch）
> 目标：把项目从「11 角色 / 6 预设」扩到「覆盖网易高校赛 17 个官方赛制板型 + 假面舞会」
> 状态：**已批准，阶段 0 实施中**

---

## 一、目标与验收标准

**成功标准**（按阶段累加）：

| 阶段 | 完成板子 | 网易累计 | 备注 |
| --- | --- | --- | --- |
| 0 | 狼美人骑士 | 3（含已有 2） | 零新角色 |
| 1 | 狼王守卫、恶夜骑士 | 5 | S 档 |
| 2 | 石像鬼守墓人、狼王摄梦人 | 7 | 隐狼 + 免疫 + 多目标命令 |
| 3 | 纯白夜影、赤月猎魔人、噩梦之影 | 10 | 封锁 + 验杀 |
| **3.5** | **假面舞会**（非网易 17 板，京城大师赛原创） | 10 | **舞池结算** |
| 4 | 奇迹商人、孤独少女、寻香识命 | 13 | 技能赋予 + 胜利条件继承 |
| 5 | 永序之轮 | 14 | 白天流程 |
| 6 | 觉醒狼王、镜隐迷踪、猎日逐光 | **17** | 动态技能复制 |

**关键里程碑**：阶段 3 结束即可复刻 2025 高校夏季赛 6 个板型中的 5 个（仅差镜隐迷踪，卡在觉醒隐狼）。这是最适合停下来评估的节点。

**全局验收**：

1. 每个新角色必须有 `backend/tests/test_<role>_extension.py`，覆盖 happy path + 边界 + 异常路径
2. 后端 `python -m pytest tests app --cov=app --cov-branch -q` 覆盖率保持 100.00%
3. 每个新死因/胜因/阵营同步 5 处目录（见 §2.1），`tests/test_values.py`、`tests/test_schemas.py`、`tests/test_game_routes.py` 全绿
4. 每个新观众事件同步 5 处（见 §2.1），`tests/test_audience_projector.py`、`eventCoverage.test.tsx` 全绿
5. 引擎/校验器/解析器禁词门禁不破：`core/`、`roles/base.py`、`prompt_builder.py`、`state_filter.py` 源码不得出现任何内置角色名
6. 旧档兼容：每阶段结束跑 `tests/test_recovery_compat.py` 与 `tests/test_checkpoint_codec.py`，`interrupted` 旧局仍可续跑

---

## 二、四项基础设施改造

### 2.1 改造 A：取值目录同步（每个新死因/事件都要走，机械但不可漏）

**新死因目录 —— 5 处必须同步**：

| # | 文件 | 改什么 |
| --- | --- | --- |
| 1 | `backend/app/models/values.py:37-43` | `DeathCause` 加成员（**权威目录**） |
| 2 | `backend/app/api/schemas.py:153` | `PublicDeathCause` Literal 加值 |
| 3 | `backend/app/api/routes/game_routes.py:57` 附近 | 旧档回放白名单加值 |
| 4 | `frontend/src/store/types.ts:18-26` | `DeathCause` 联合类型加值 |
| 5 | `frontend/src/labels.ts:38` | `DEATH_CAUSE_LABELS` 加文案 |

**注意**：`values.py` 的目录**只做门禁锚点**，不要把它传进检查点或 Effect 载荷 —— `checkpoint_codec` 与 `effect_applier` 用 `type(value) is not str` 精确校验，枚举成员会被拒。生产代码继续写字符串字面量，由 `tests/test_values.py` 扫描比对。

本计划引入的新死因（共 8 个，分批加）：

| 死因 | 用于 | 阶段 |
| --- | --- | --- |
| `wolf_king_shot` | 狼王/觉醒狼王爪 | 1 |
| `reflect` | 恶夜骑士反伤、蚀时狼妃反弹 | 1 |
| `dream_death` | 摄梦人连续两晚/连坐 | 2 |
| `verify_kill` | 纯白之女/狼巫验杀 | 3 |
| `hunt` | 猎魔人狩猎 | 3 |
| `dance` | 假面舞会舞池少数方出局 | 3.5 |
| `bond_death` | 寻香魅影氤氲缠绕 | 4 |
| `devour_backlash` | 流光反噬 | 6 |

**与延迟死亡交互**：`night_settlement.py:20-26` 的 `_POISON_CAUSES` / `_WOUND_CAUSES` / `_DELAYED_DEATH_CAUSES` 是"哪些死因会留下可见标记"。`dream_death`、`verify_kill`、`hunt` 需要判定是否走延迟路径 —— 猎魔人狩猎是"次日结算"，需要加入延迟族（见阶段 3）。

**触发技能的死因白名单**：

- `backend/app/roles/hunter.py:16` `_SHOOT_REASONS`（猎人能开枪的死因）
- `backend/app/roles/wolf_beauty.py:26-28` `_REVENGE_REASONS`（狼美人殉情触发的死因）

**新观众事件 —— 5 处必须同步**：`app/services/audience_projector.py` 的 `_EVENTS` → `tests/test_audience_projector.py` 类型全集快照 → `api/schemas.py` 的 `PublicReplayEvent` 联合 + `game_routes.py` 的 `_AUDIENCE_ACTION_SCHEMAS`/`_REASONING_EVENT_TYPES`/`_public_day_verdict_event`/`_public_role_skill_event` → `frontend/src/components/game/test/eventCoverage.test.tsx` 期望表 → `frontend/src/labels.ts` 的 `EVENT_TAGS`。门禁：`tests/test_game_routes.py::test_every_public_audience_action_type_has_a_replay_branch` 会遍历投影器声明的每个类型要求回放产出事件，**漏一个分支即红**。

**观众事件必须有耐久提交**：audience 表只由 `GameService._checkpoint_domain_events` 的 label 分支写入。白天窗口用 `day_point:`、旁白用 `narration:`、夜间调度点用 `night_point:`、系统代投用 `vote_technical_abstain:`。只发领域事件而没有对应 step label 的事件会**静默丢掉**。

---

### 2.2 改造 B：隐狼机制（阶段 2，一次解锁 5 个板子 + 假面舞会的 LAL 变体）

**需求**：狼阵营角色但不进狼窝 —— 不参与讨论、不参与狼刀投票、队友不知道它；其他狼全出局后才获得带刀权。

**现状问题**：狼队识别是硬编码的 `player.camp == Camp.WEREWOLF`，共 **6 处**（逐行核实）：

| 文件:行号 | 用途 | 是否必改 |
| --- | --- | --- |
| `core/game_engine.py:524` | 夜间狼名单（驱动讨论 + 逐票） | 必改 |
| `core/night_flow.py:270` | `_wolf_team` 提示词"你的队友是 X" | 必改 |
| `core/conversation_log.py:202` | 狼频道可见性 | 必改 |
| `core/context_projector.py:202` / `:281` | CAMP `camp_members` | 必改（否则隐狼身份泄露给队友） |
| `core/sheriff_flow.py:409` / `:413` | 警长竞选提示列队友 | 必改 |
| `services/memory_service.py:77` / `:80` | 记忆里的队友名单 | 必改 |

**不要改**：`rule_engine.py:27`（判胜按 camp 算，隐狼仍算狼）、`models/game.py:164/167`、`registry.py` 里三狼的 `camp_id`、`context_projector.py:268/374`。

**设计**：资源标记 `no_wolf_pack`，由角色在 `initial_resources` 声明（与 `self_kill_forbidden` 同一套模式，核心模块不出现角色名）。

**新增读取助手** —— `backend/app/core/role_runtime.py` 追加：

```python
def has_role_marker(state: GameState, seat: int, marker: str) -> bool:
    """Read a role-declared resource marker for one seat."""
    return role_resource_view(state, seat).get(marker, 0) > 0
```

（`role_resource_view` 已存在于 `role_runtime.py:151`）

**6 处改法统一为**：把 `p.camp == Camp.WEREWOLF` 换成

```python
p.camp == Camp.WEREWOLF and not has_role_marker(state, seat, NO_WOLF_PACK_RESOURCE)
```

常量 `NO_WOLF_PACK_RESOURCE = "no_wolf_pack"` 放 `backend/app/core/night_flow.py`（与 `SELF_KILL_FORBIDDEN_RESOURCE` 同处，`night_flow.py:25`），由其他模块 import。

**投影侧的不对称设计**（关键，别做错）—— `context_projector.py` 的 CAMP 分支（`:196-204` 与 `:276-283`）：

```python
if "CAMP" in visible:
    pack_seats = tuple(sorted(
        seat for seat, player in state.players.items()
        if player.camp == actor.camp
        and not has_role_marker(state, seat, NO_WOLF_PACK_RESOURCE)
    ))
    if not has_role_marker(state, actor.seat_number, NO_WOLF_PACK_RESOURCE):
        facts["wolf_pack_seats"] = pack_seats          # 普通狼：看到完整队友名单
    else:
        facts["wolf_pack_count"] = len([              # 隐狼：只知道还剩几只狼，不知道是谁
            s for s in pack_seats if state.players[s].is_alive
        ])
```

**为什么必须不对称**：石像鬼的规则是"不能与其他狼人互知身份"—— 它可能刀死自己队友。若给它完整名单，这个板子的核心博弈就没了。

**带刀权解锁**：隐狼**不声明** `WEREWOLF_KILL_CONTRACT`（`scheduler.py:305-310` 按契约找请求，不声明就不进狼刀投票），改为声明一个**独立契约**：

```python
HIDDEN_WOLF_KILL_CONTRACT = ActionContract(
    contract_id="hidden_wolf_kill",
    schedule_point=SchedulePoint.NIGHT_WOLF_VOTE,   # 与狼刀同点，pack 空时不会撞
    order=60,                                       # 排在狼刀契约之后
    action_types=("kill", "pass"),
    actions_requiring_target=frozenset({"kill"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    is_applicable=hidden_wolf_applicable,   # context.facts.get("wolf_pack_count", 0) == 0
    validate=validate_hidden_wolf_kill,
    resolve=resolve_hidden_wolf_kill,
)
```

**单向可见变体（供阶段 6 蚀日侍女用）**：侍女知道其他狼、其他狼不知晓她。在 `no_wolf_pack` 之外再加一个 `wolf_knows_pack` 标记，控制投影方向：

```python
actor_hidden = has_role_marker(state, actor.seat_number, NO_WOLF_PACK_RESOURCE)
actor_knows  = has_role_marker(state, actor.seat_number, WOLF_KNOWS_PACK_RESOURCE)
if not actor_hidden or actor_knows:
    facts["wolf_pack_seats"] = pack_seats
else:
    facts["wolf_pack_count"] = ...
```

**验收**：新增 `tests/test_hidden_wolf_extension.py`，断言（a）隐狼不进狼队讨论名单（b）狼队提示词不含隐狼座位（c）隐狼看不到队友座位、只看到数量（d）pack 存活时隐狼 `is_applicable` 为假（e）pack 全灭后隐狼可单独刀人（f）隐狼仍算狼阵营参与屠边判胜（g）单向可见变体下侍女能看到队友、队友看不到侍女。

---

### 2.3 改造 C：免疫与保护分账（阶段 2 起，阶段 3.5 扩展）

**现状**：`core/night_settlement.py:144-145`

```python
lethal_wolf = double_save or wolf_totals.get(target, 0) > protection_totals.get(target, 0)
lethal_other = other_totals.get(target, 0) > 0     # ← 毒药等完全无视 protection
```

protection 只对狼刀生效，`lethal_other` 是裸判。所以"免疫毒药""免疫全部夜间伤害"这类根本表达不了。

**设计：保护来源分三档**（`night_settlement.py:177-185` 的 `_protection_source` 白名单同步扩展）：

```python
# 只抵消狼刀的保护来源（旧"守护"语义）
_WOLF_ONLY_SOURCES = frozenset({"guard", "witch_antidote", "dance_veil"})
# 参与"同守同救"对穿判定的来源（舞池守护不是守卫，不参与对穿）
_DOUBLE_SAVE_SOURCES = frozenset({"guard", "witch_antidote"})
# 抵消全部夜间伤害的来源
_ANY_DAMAGE_SOURCES = frozenset({"dream", "radiance", "secret_body"})
```

`settle()` 的分账改为：

```python
wolf_only = sum(amount for src, amount in sources[target] if src in _WOLF_ONLY_SOURCES)
any_damage = sum(amount for src, amount in sources[target] if src in _ANY_DAMAGE_SOURCES)
lethal_wolf = double_save or wolf_totals.get(target, 0) > wolf_only
lethal_other = other_totals.get(target, 0) > any_damage
```

**对穿语义必须保持**：`night_settlement.py:140-143` 的 `double_save` 判定源改为 `_DOUBLE_SAVE_SOURCES`。

**三种"免疫"是三条路，别混**：

| 标记 | 语义 | 用于 | 实现位置 |
| --- | --- | --- | --- |
| `delayable` | 跳过致死 → 落标记 → 次日死 | 老酒鬼 | `night_settlement.py:154-160`（已存在） |
| `night_immune` | **永久**跳过一切夜间致死、不留标记 | 恶夜骑士 | 新增分支，放在 delayable 判定**之前** |
| `poison_immune` | 只跳过 `poison` 死因 | 猎魔人、舞者、假面 | 新增分支，按 `cause` 过滤 |

```python
markers = role_resources.get(target, {})
if markers.get(NIGHT_IMMUNE_RESOURCE, 0) > 0:
    continue                                    # 免疫一切夜间伤害
if cause == "poison" and markers.get(POISON_IMMUNE_RESOURCE, 0) > 0:
    continue                                    # 只免疫毒
if markers.get(DELAYABLE_RESOURCE, 0) > 0:      # 原有延迟逻辑
    ...
```

**"无视保护"的死因**：`verify_kill`（纯白之女/狼巫验杀）官方明确"守卫守护无效、女巫不能救"。新增：

```python
_UNPROTECTABLE_CAUSES = frozenset({"verify_kill", "hunt"})
```

判定时该死因不参与 `any_damage` 抵扣。

**验收**：扩 `tests/test_night_settlement.py`，断言（a）`dream` 来源能挡毒（b）`guard` 来源挡不住毒（c）`dance_veil` 只挡狼刀且不触发对穿（d）对穿仍生效（e）`night_immune` 既不死也不延迟（f）`poison_immune` 只挡毒不挡狼刀（g）`verify_kill` 无视任何保护。

---

### 2.4 改造 D：多目标命令（阶段 2 前必须决策）

**现状**：`backend/app/models/pipeline.py:371-377`

```python
class ActionCommand(BaseModel):
    schema_version: Literal[1] = 1
    action_type: StrictStr
    target_seat: StrictInt | None      # ← 单目标
    reasoning: StrictStr
```

需要一次选多人的角色：**觉醒预言家**（验 2 人）、**寻香魅影**（绑 2 人）、**舞者**（选 3 人进舞池）、炼金魔女（未明之雾选 3 人，非高校赛）。

**两个方案**：

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| **D1 扩 schema（推荐）** | `target_seat` → `target_seats: tuple[StrictInt, ...]`，`schema_version` 升 2，保留 `target_seat` 作为兼容读 | 要动 `ActionCommand`、`action_validator.py`、`action_resolver.py`、`roles/base.py` 的 tool 定义、`agents/output_parser.py`、`point_journal`/`checkpoint_codec` 的命令编解码。**改完旧档必须仍能续跑**（旧命令只有 `target_seat`） |
| **D2 分多次问** | 契约声明 `per_window_limit=3`，靠 slot 或计数器问三轮 | 零 schema 改动，但 prompt 轮次翻三倍、模型容易把三轮答成同一人 |

**决策**：走 **D1**，因为它是契约形状问题 —— 改晚了所有已落库的 `action_key`/`window_id` 都会变，旧局无法续跑。**必须在阶段 2 之前做完**，之后所有新角色直接用新 schema。

**验收**：`tests/test_pipeline_models.py` 加 schema 2 往返测试；`tests/test_recovery_compat.py` 加"旧档 v1 命令可续跑"用例；`tests/test_checkpoint_codec.py` 加命令编解码兼容用例。

---

## 三、每个新角色的通用改动清单（模板）

**新增一个角色 = 改这 6 处，缺一处就有门禁变红**：

| # | 文件 | 改什么 |
| --- | --- | --- |
| 1 | `backend/app/roles/<new>.py`（**新建**） | `RoleSpec` + `ActionContract` + 纯 Hook |
| 2 | `backend/app/roles/registry.py` | **两处**注册：`register_pipeline(SPEC)`（`:430-440` 区）**和** `register(LegacyRoleSpec(id, Camp.X, RoleClass, ()))`（`:441-551` 区）。**缺后者会让 `rule_engine._role_tags()` 返回空集，该角色既不算神职也不算平民，直接误判狼胜** |
| 3 | `backend/app/catalog.py:16` `ROLE_METADATA` | `name_zh` / `icon` / `description` / `rules`（**`rules` 非空是门禁**） |
| 4 | `backend/tests/test_catalog_routes.py:80` / `:169` | `assert len(items) == 11` → 新角色数；`len(response.roles)` 同理 |
| 5 | `backend/tests/test_<new>_extension.py`（**新建**） | 照抄 `tests/test_guard_extension.py:50-194` 的结构 |
| 6 | 前端四件套 | `components/shared/roleMeta.ts:13-25`（ROLE_ROWS）、`components/shared/RoleIcon.tsx:32`（图标）、`src/labels.ts:256-267`（ROLE_LABELS）、`components/rules/RulesPage.tsx:20-32`（ROLE_ORDER）+ `roleMeta.test.ts:6-18` |

**RoleSpec 样板**（照抄 `backend/app/roles/guard.py:80-110`）：

```python
GUARD_SPEC = RoleSpec(
    role_id="wolf-killer-guard",                    # :82 唯一必填
    display_name="Guard",                           # :83
    camp_id="good",                                 # :84 必须 == PlayerState.camp
    contracts=(GUARD_CONTRACT,),                    # :85-97
    initial_private_data={"last_guarded": None},    # :98 hook 经 context.facts 读
    allowed_effects=frozenset({...}),               # :99 必须 ⊇ 每个契约的 allowed_effects
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),  # :100 ⊇ 契约声明
    tags=frozenset({"god"}),                        # :81 胜负判定唯一依据
    max_count=1,
    instructions="...",                             # :101-109
)
```

**ActionContract 样板**（照抄 `backend/app/roles/guard.py:85-97`）：

```python
ActionContract(
    contract_id="guard_action",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    order=50,                                       # 0..1_000_000
    action_types=("guard", "pass"),                 # 非空且唯一
    actions_requiring_target=frozenset({"guard"}),  # ⊆ action_types，不含 fallback
    fallback_action_type="pass",                    # ∈ action_types
    allowed_effects=frozenset({EffectKind.SUBMIT_PROTECTION, EffectKind.SET_PRIVATE_DATA,
                               EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    is_applicable=guard_applicable,
    validate=validate_guard_action,
    resolve=resolve_guard_action,
)
```

**Hook 契约**（`registry.py:239-311` 用 `inspect.signature` + `get_type_hints` 精确比对，参数名/注解/返回注解**一字不差**）：

| hook | 签名 | 返回 |
| --- | --- | --- |
| `is_applicable` | `(context: ActionContext)` | `bool` |
| `validate` | `(context: ActionContext, command: ActionCommand)` | `tuple[RuleViolation, ...]` |
| `resolve` | `(context: ActionContext, command: ActionCommand)` | `tuple[GameEffect, ...]` |
| `aggregate` | `(context: ActionContext, commands: tuple[ActionCommand, ...])` | `tuple[GameEffect, ...]` |
| `react` | `(context: ActionContext)` | `tuple[GameEffect, ...]` |

**硬约束**：hook 必须是模块级函数（禁 lambda/闭包/bound method）；只能读冻结的 `ActionContext`，**不能写状态**（唯一写入口是 `EffectApplier`）；`react` 窗口**不查 `is_applicable`**，必须自检（见 `idiot.py:30-32`）。

**可用 Effect 种类**（`pipeline.py:45-59`，共 14 种）：`accept_action` / `record_vote` / `consume_resource` / `set_resource` / `set_private_data` / `add_status` / `remove_status` / `add_relation` / `remove_relation` / `record_private_fact` / `submit_damage` / `submit_protection` / `mark_death` / `emit_event`。**单条 effect 恒为单 target**，多座位靠一个批次放多条。

**调度点**（`pipeline.py:29-42`）：`GAME_SETUP` / `NIGHT_ACTION` / `NIGHT_WOLF_VOTE` / `NIGHT_WITCH_ACTION` / `NIGHT_SEER_ACTION` / `NIGHT_COMMIT` / `DAWN_REACTION` / `EXILE_VERDICT` / `DAY_ACTION` / `POST_SPEECH_ACTION` / `VOTE_ACTION` / `ROUND_END` / `GAME_END`。

**注册失败的常见原因**（`registry.py:152-311`）：契约 effects/visibility 超出 RoleSpec 声明、`selected_target` 命名空间越界、`resolve`/`aggregate` 同给或全无且无 `react`、有 `react` 无 `response_event_types`、hook 签名不符。

---

## 四、阶段 0 —— 预设与规则口径对齐（零新角色）

### 0.1 新增「狼美人骑士」预设

`backend/app/catalog.py:117` `STANDARD_PRESETS` 追加第 7 项：

```python
{
    "id": "twelve-player-wolf-beauty-knight",
    "name": "十二人狼美人骑士",
    "description": "3狼 1狼美人 4民 1预言家 1女巫 1守卫 1骑士",
    "role_counts": {
        "wolf-killer-werewolf": 3, "wolf-killer-wolf-beauty": 1,
        "wolf-killer-villager": 4, "wolf-killer-seer": 1,
        "wolf-killer-witch": 1, "wolf-killer-guard": 1, "wolf-killer-knight": 1,
    },
    "enable_sheriff": True,          # 网易 12 人局标配警长
    "witch_self_save": "never",
    "recommended_timeout_seconds": 3600,
},
```

同步：`tests/test_catalog_routes.py:103` `== 6` → `== 7`；`:179` 同理；`backend/app/api/catalog_schemas.py` 的 `PresetItem` 加 `witch_self_save: str = "first_night_only"`。

### 0.2 狼美人被骑士决斗不发动殉情（网易口径）

`backend/app/roles/wolf_beauty.py:26-28`：

```python
# 现状
_REVENGE_REASONS = frozenset({"wolf_kill", "poison", "exile", "hunter_shot", "knight_duel"})
# 改为（网易：被骑士决斗出局不发动魅惑也不翻牌；被猎人带走仍发动）
_REVENGE_REASONS = frozenset({"wolf_kill", "poison", "exile", "hunter_shot"})
```

**安全性已核实**：现有 6 个预设里骑士与狼美人**从不同时在场**，改这一行不影响任何既有对局行为。
同步：`tests/test_wolf_beauty_extension.py` 加"被骑士决斗不殉情"用例。

### 0.3 女巫自救策略做成配置开关

网易口径是"**女巫全程不能自救**"，项目现状是"自救仅限第一晚"（硬编码在 `roles/witch.py:28-36`）。

| 文件 | 改什么 |
| --- | --- |
| `backend/app/models/game.py:53-91` | `GameConfig` 加字段 `witch_self_save: str = "first_night_only"`（取值 `first_night_only` / `never`），`__init__` 加同名参数 |
| `backend/app/persistence/checkpoint_codec.py:40` | `_CONFIG_FIELDS` 加 `"witch_self_save"`（**这是精确匹配集合，漏了会拒绝旧档**） |
| 同上 `:178` | 编码加 `"witch_self_save": state.config.witch_self_save` |
| 同上 `:328` | 解码加 `config_row.setdefault("witch_self_save", "first_night_only")` + `type(...) is str` 校验 |
| 同上 `:365` | `GameConfig(...)` 加参数 |
| `backend/app/core/context_projector.py:424-426` | 照 `sheriff_enabled` 的模式注入，**只在非默认值时注入**，避免改变旧局 prompt：`if policy != "first_night_only": facts["witch_self_save"] = policy` |
| `backend/app/roles/witch.py:28-36` | `validate_witch_action` 改读 `context.facts.get("witch_self_save", "first_night_only")`，`never` 时任何轮次都拒绝自救，违规码改为 `witch_self_save_forbidden` |
| `backend/app/api/schemas.py:9-31` | `CreateGameRequest` 加 `witch_self_save: Literal["first_night_only", "never"] = "first_night_only"` |
| `backend/app/services/game_service.py:1226` | 建局透传 |

**验收**：`tests/test_witch_pipeline.py` 加 `never` 策略正反例；`tests/test_checkpoint_codec.py` 加"旧档缺字段回退到默认"用例。

---

## 五、阶段 1 —— S 档角色（狼王、恶夜骑士）

### 5.1 狼王 → 解锁「狼王守卫」

**新文件 `backend/app/roles/wolf_king.py`**

> **命名决策**：狼王用 `wolf-killer-wolf-king`，白狼王保持 `wolf-killer-werewolf-king`。两者只差一个词序，**极易混淆** —— 文件头必须写明注释，`catalog.py` 的 `name_zh` 必须是「狼王」与「白狼王」全称，禁止简写。

**规则 → 机制映射**：

| 规则 | 机制 |
| --- | --- |
| 夜晚与狼队共用一次狼刀 | 复用 `roles/werewolf.py` 导出的 `WEREWOLF_KILL_CONTRACT`（**共享契约必须字节一致**，`registry.py:178-183`） |
| 出局后可带走一人，一局一次 | `initial_resources={"wolf_claw": 1}` + react 契约 + `per_game_limit=1` |
| 仅夜间中刀 / 白天被放逐 / 被猎人带走可发动 | `response_reasons={"wolf_kill", "exile", "hunter_shot"}` |
| **自爆出局不能发动** | `response_reasons` **不含** `self_explode` |
| **被女巫毒不能发动** | 不含 `poison` |
| **被殉情带走不能发动** | 不含 `charm` |

**契约**（照抄 `roles/hunter.py:108-109` 的 response 配置写法）：

```python
WOLF_KING_SHOT_CONTRACT = ActionContract(
    contract_id="wolf_king_shot",
    schedule_point=SchedulePoint.DAWN_REACTION,
    order=40,
    action_types=("shoot", "pass"),
    actions_requiring_target=frozenset({"shoot"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE,
                               EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    required_resources=MappingProxyType({"wolf_claw": 1}),
    response_event_types=frozenset({"PLAYER_DIED"}),
    response_reasons=frozenset({"wolf_kill", "exile", "hunter_shot"}),
    per_game_limit=1,
    is_applicable=wolf_king_applicable,
    validate=validate_wolf_king_action,
    resolve=resolve_wolf_king_action,
)
```

`resolve` 产出 `CONSUME_RESOURCE(wolf_claw)` + `SUBMIT_DAMAGE(target, cause="wolf_king_shot")` + `EMIT_EVENT("WOLF_KING_SHOT")`。

**待定项**：被骑士决斗（`knight_duel`）能否发动 —— 官方未明文。**默认排除**（与毒同理），在 `instructions` 里写明"被决斗、被毒、被殉情、自爆均不能发动"。

**同步**：`DeathCause` 加 `wolf_king_shot`（5 处，§2.1）；新观众事件 `WOLF_KING_SHOT`（5 处）；`catalog.py` 元数据；`test_catalog_routes.py` 角色数 11 → 12。

**预设**：`twelve-player-wolf-king-guard`（3狼 1狼王 4民 预女猎守，`enable_sheriff=True`，`witch_self_save="never"`）→ `test_catalog_routes.py` 预设数 7 → 8。

### 5.2 恶夜骑士 → 解锁「恶夜骑士」板

**新文件 `backend/app/roles/evil_knight.py`**（角色 id `wolf-killer-evil-knight`）

> 官方 2024 写「恶灵骑士」、2025/2026 写「恶夜骑士」，是**同一角色**。id 用 `wolf-killer-evil-knight`，`catalog.py` 的 `name_zh` 用「恶夜骑士」并在 `rules` 里注明旧称。

**三条能力，成本比表面高**：

**(1) 永久免疫夜间伤害** —— 复用 §2.3 的 `NIGHT_IMMUNE_RESOURCE`：

```python
initial_resources={"night_immune": 1, "reflect_check": 1, "reflect_poison": 1,
                   "reflect_used": 0, "no_self_explode": 1, "self_kill_forbidden": 1}
```

**(2) 不能被狼队选为夜袭目标** —— 把 `night_immune` 加入 `context_projector.py:63` 的 `_PUBLIC_RESOURCES`，让狼刀契约通过 `selected_target.resource_labels` 看到它并在 `validate_werewolf_action`（`roles/werewolf.py:27-37`，与 `self_kill_forbidden` 同一处）里拒绝。**这是公开信息，符合官方规则**（狼队本就不该刀它）。

**(3) 一次性反伤** —— **不用引擎改动，用生产者侧 + 原子去重**：

- `roles/seer.py` 的 `resolve`：读 `context.facts["selected_target"]["resource_labels"]`，若含 `reflect_check`，追加
  ```python
  GameEffect(..., EffectKind.SET_RESOURCE, target_seat=target,
      payload={"target": target, "resource": "reflect_used", "value": 1},
      preconditions={"resource_equals": {"resource": "reflect_used", "value": 0}},
      sort_key=(...)),
  GameEffect(..., EffectKind.SUBMIT_DAMAGE, target_seat=context.actor_seat,
      payload={"target": context.actor_seat, "amount": 1, "cause": "reflect"}, ...),
  ```
- `roles/witch.py` 的 `resolve`：毒药命中 `reflect_poison` 时同理
- **去重靠 `resource_equals` 前置条件**（CAS）：同夜两者都命中时，第二笔会因 `reflect_used == 1` 被拒 → 天然满足"同夜叠加只发动一次"。注意 `EffectConflict` 在模型驱动的契约里会被 `Scheduler._apply_or_degrade` 降级为 fallback，不会丢局
- **守卫守护可阻断反伤**：`reflect` 死因走 `SUBMIT_DAMAGE`，需要在 §2.3 的分账里把 `reflect` 归入"可被 `guard` 抵消"的伤害类

**同步**：`DeathCause` 加 `reflect`；新观众事件 `EVIL_KNIGHT_REFLECT`；`test_evil_knight_extension.py` 覆盖三条能力的正反例。

**预设**：`twelve-player-evil-knight`（3狼 1恶夜骑士 4民 预女猎守，`enable_sheriff=True`，`witch_self_save="never"`）。

---

## 六、阶段 2 —— 隐狼 + 免疫 + 多目标命令（石像鬼守墓人、狼王摄梦人）

**本阶段先做基础设施改造 B / C / D，再做角色。**

### 6.1 摄梦人（`backend/app/roles/dream_weaver.py`）

出现在 **4 个板子**，是复用度最高的新角色。

| 规则 | 机制 |
| --- | --- |
| 每晚**必须**选一名自己以外的玩家为梦游者（不可放弃） | `action_types=("dream",)`，**无 `pass`** —— `fallback_action_type` 必须是 `"dream"`；`actions_requiring_target={"dream"}` |
| 梦游者不知情 | 只 `RECORD_PRIVATE_FACT` 到施法者，不发 PUBLIC 事件 |
| 梦游者免疫夜间伤害（含毒） | `SUBMIT_PROTECTION(target, source="dream")` → 走 §2.3 的 `any_damage` 通道 |
| **连续两晚同一人 → 该人出局** | `initial_private_data={"last_dreamed": None}`（照 `guard.py:18-20` 的 `last_guarded` 模式）+ 校验期比较 + `SUBMIT_DAMAGE(cause="dream_death")` |
| **摄梦人夜晚出局 → 当夜梦游者一并出局** | react 契约 @ `DAWN_REACTION`，`response_event_types={"PLAYER_DIED"}`，`response_reasons` 排除白天死因 |

**注意"强制不可放弃"是新形状**：现有契约都有 `pass` fallback。`fallback_action_type` 必须 ∈ `action_types`（`registry.py:199`），所以取 `action_types=("dream",)` + `fallback_action_type="dream"`。超时时模型降级为 `dream` 但 `target_seat=None` → `validate` 需给出 `target_required` 违规 → **必须在 `resolve` 里兜底随机选人**（官方规则："超时随机从自己以外存活者中选取"）。这是本角色最容易漏的一条。

**预设**：`twelve-player-wolf-king-dream`（3狼 1狼王 4民 预女猎 摄梦人，**无守卫**）。

### 6.2 石像鬼 + 守墓人 → 解锁「石像鬼守墓人」

**石像鬼**（`backend/app/roles/gargoyle.py`，id `wolf-killer-gargoyle`）：

| 规则 | 机制 |
| --- | --- |
| 不进狼窝、不与狼互知身份 | `initial_resources={"no_wolf_pack": 1}` → §2.2 的 6 处过滤 |
| 不参与袭击 | 不声明 `WEREWOLF_KILL_CONTRACT` |
| 不能自爆 | `initial_resources={"no_self_explode": 1}` |
| 其他狼全出局后可带刀 | `HIDDEN_WOLF_KILL_CONTRACT`（§2.2） |
| 每晚查验一人**具体身份**（不可查自己、不可重复） | 新契约 @ `NIGHT_SEER_ACTION`，`RECORD_PRIVATE_FACT(namespace="role_checks")` + `initial_private_data={"checked_seats": ()}` 做"不可重复" |
| 被预言家查验恒显示为狼人 | 预言家的 camp 查验读 `camp` 即可（石像鬼 camp 本来就是 werewolf）→ **零改动** |

**守墓人**（`backend/app/roles/gravekeeper.py`）：

| 规则 | 机制 |
| --- | --- |
| 每晚**强制**获知上一白天被放逐者的阵营 | 需要一个新的 ACTOR 私有事实 `last_exiled` |
| 放逐石像鬼报"狼人" | 石像鬼 camp 是 werewolf → 自然正确 |
| 首夜无信息 | 事实值为 `None` |

**实现要点**：在 `context_projector.py` 的 `_actor_private_facts` 里新增一个特例命名空间 `last_exiled`（照 `:505-513` 的 `private_checks` 特例写法），从 `state` 的放逐记录推导 `{"seat": N, "camp_label": "good"|"werewolf"}`。**需要确认 `GameState` 里是否已持久化"上一个被放逐座位"** —— 若没有，最小改动是在 `GameState` 加一个字段并同步 `checkpoint_codec`（**字段集精确匹配，加字段会让旧检查点无法续跑**，必须走 `setdefault` 回退）。

守墓人契约用 `action_types=("pass",)`（无选择）—— 会多一次 LLM 调用，但形状最简单、零新机制。**备选**：做成零契约 + 纯私有事实注入（像老酒鬼那样只有声明），成本更低，但需要确认 `_actor_private_facts` 能否承载动态值。

**预设**：`twelve-player-gargoyle-gravekeeper`（3狼 1石像鬼 4民 预女猎 守墓人）。

### 6.3 预设同步

本阶段新增 2 个预设，`test_catalog_routes.py` 的预设数断言随之递增。**每个预设都要带 `witch_self_save="never"` 与 `enable_sheriff=True`**（网易口径）。

---

## 七、阶段 3 —— 封锁 + 验杀（纯白夜影、赤月猎魔人、噩梦之影）

### 7.1 纯白之女 + 狼巫 → 解锁「纯白夜影」

两个角色是**镜像**设计，建议放同一个文件 `roles/pure_white.py` 或分两个文件但共用一套代码。

| 规则 | 机制 |
| --- | --- |
| 纯白之女：每晚查一人具体身份 | 同石像鬼的"具体身份查验"（复用同一套 `role_checks` 命名空间） |
| **第 2 夜起**查中狼人则该狼人当夜出局 | resolve 里读 `selected_target.camp_label`，命中则 `SUBMIT_DAMAGE(cause="verify_kill")` |
| 守卫守护无效、女巫不能救 | `verify_kill` 走 §2.3 的 `_UNPROTECTABLE_CAUSES` |
| 狼巫：每晚在**狼队袭击之后**查验一名非狼玩家 | 新契约 @ `NIGHT_SEER_ACTION`（该点顺序在狼刀之后，符合规则） |
| 同夜双方互验可同归于尽 | 两笔 `SUBMIT_DAMAGE` 同批次结算，天然同归于尽 |
| 若该结算击杀最后一神，判狼人胜 | `rule_engine` 现有 `alive_gods == 0` 分支已覆盖，零改动 |

**新死因 `verify_kill`**（5 处同步）+ 新观众事件。

**预设**：`twelve-player-pure-white`（3狼 1狼巫 4民 守卫 女巫 猎人 纯白之女，**无预言家**）。

### 7.2 猎魔人 + 赤月使徒 → 解锁「赤月猎魔人」

**猎魔人**（`roles/demon_hunter.py`）：

| 规则 | 机制 |
| --- | --- |
| 第 2 晚起每晚狩猎 1 人，可放弃 | 新契约 @ `NIGHT_ACTION`（与守卫同点），`action_types=("hunt", "pass")` |
| 目标为狼 → **次日**目标出局 | 走**延迟死亡**：把 `hunt` 加入 `_DELAY_CAUSES`（`night_settlement.py:20-22`），并让 `delayable` 判定同时接受"目标声明了 delayable **或** 死因在 `_DELAY_CAUSES` 中" —— 因为猎魔人不能给目标加资源标记 |
| 目标为好人 → **次日**猎魔人出局 | 同上，`SUBMIT_DAMAGE(target=actor_seat, cause="hunt")` |
| 女巫毒药对猎魔人无效 | `initial_resources={"poison_immune": 1}`（§2.3） |
| 不能自指 | `validate` 期拒绝 |
| 未出局可连续两晚狩同一人 | **不设** `last_hunted` 限制 |

**赤月使徒**（`roles/crimson_envoy.py`）：

| 规则 | 机制 |
| --- | --- |
| 白天任意时刻可自爆（遗言、投票除外） | 复用白狼王 `DAY_ACTION` 模式（`roles/werewolf_king.py`，靠 per-seat slot 反复询问） |
| 自爆后当天立即入夜 | 复用 `DAY_INTERRUPTED` 通用事件（`game_engine.py:1464` → `_resolve_day_interruption`:1471） |
| **当晚所有好人技能被封印** | **窄版本实现**：自爆时给**所有存活好人座位** `ADD_STATUS("silenced")`，复用 `runtime.statuses`，零新增全局状态 |
| 自爆后不参与当晚袭击 | 自爆时给自己 `ADD_STATUS("left_pack")`，狼队识别的 6 处把该状态与 `no_wolf_pack` 资源合并判定 |

**新死因 `hunt`**；新观众事件 `HUNT`、`CRIMSON_EXPLODE`。

**预设**：`twelve-player-crimson-hunter`（3狼 1赤月使徒 4民 预女 猎魔人 白痴）。

### 7.3 噩梦之影 → 解锁「噩梦之影」

| 规则 | 机制 |
| --- | --- |
| 每晚**在其他所有人行动之前**恐惧一人，使其当夜无法行动 | 复用 `NIGHT_ACTION` + `order=10`（守卫是 50）。**不新增调度点**（新增点要改 `_NIGHT_POINTS`/`_NIGHT_POINT_STAGES`/stage 下标，风险高） |
| 被恐惧者当夜无法行动 | 给目标 `ADD_STATUS("silenced")`；各契约的 `is_applicable` 读 actor 自身状态标签拒绝 |
| **恐惧到狼人 → 整个狼人阵营当夜无法袭击** | 在 `game_engine.py:547/605` 两处加门禁：读恐惧目标是否狼阵营 → 是则跳过讨论 + `record_votes(())`。stage 5 照跑，全员降级 `pass` → `aggregate_werewolf_votes` 返回空 → 无伤害（`roles/werewolf.py:47-48` 已支持） |
| 不能连续两晚恐惧同一人、不能对自己、不能恐惧狼队友 | `initial_private_data={"last_feared": None}` + `validate` 期比较 |
| 首夜恐惧时与其他狼不互知身份 | **降级处理**：首夜也按正常狼处理，在 `instructions` 里说明（列为待定项） |

**"被恐惧者当夜无法行动"需要一个通用的 `silenced` 读取路径** —— 在 `context_projector` 把 actor 自身的 `status_labels` 投影进 ACTOR facts（当前 `status_labels` 只用于 `selected_target`），让每个契约的 `is_applicable` 统一判断。这条通道同时服务赤月使徒、蚀日侍女。

**预设**：`twelve-player-nightmare`（3狼 1噩梦之影 4民 预女猎 摄梦人）。

---

## 八、阶段 3.5 —— 假面舞会（京城大师赛原创板，非网易 17 板）

**阵容（12 人）**：好人 = 预言家、女巫、白痴、**舞者**、平民×4；狼人 = 小狼×3、**假面**。

> 出处：**京城大师赛原创**（[知乎讨论](https://www.zhihu.com/question/628011981/answer/3298589930)明确写"京城大师赛原创的狼人杀新板型"）。两个官方 app 的板型清单里都没有它。

### 8.1 核心设计难点：舞池的"阵营少数方"判定

**问题**：舞者要在结算时判断"舞池三人里哪个阵营是少数方"，但**舞者是好人，`camp_members` 是 CAMP 可见性，它看不到狼**。hook 是纯函数、读不到全局状态 —— **舞者的 `resolve` 在架构上算不出少数方**。

**三个方案**：

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| A 给舞者开特殊可见性 | 新增 `POOL` 命名空间，把三个目标的阵营标签投影给舞者 | 让好人直接看到狼的阵营 → **破坏隐私边界，否决** |
| B 新增 Effect 类型 `SUBMIT_POOL` | 舞者提交三个目标，由 `EffectApplier` 在结算时判定 | 要改 `EffectKind` + `effect_applier` 载荷 schema + 校验器 |
| **C 用 status 传递 + 新增结算函数（推荐）** | 舞者给三个目标 `ADD_STATUS("in_pool")`，假面给目标 `ADD_STATUS("masked")`；结算时由 `night_settlement` 读 statuses + camps 判定 | **只用现有 Effect 种类**，新增一个独立结算函数 |

**采用方案 C**。

### 8.2 具体实现

**新文件 `backend/app/roles/dancer.py`（舞者，id `wolf-killer-dancer`）**

```python
DANCER_SPEC = RoleSpec(
    role_id="wolf-killer-dancer",
    display_name="Dancer",
    camp_id="good",
    contracts=(DANCE_POOL_CONTRACT,),
    initial_resources={"poison_immune": 1},              # 舞者免疫女巫毒药
    initial_private_data={"pooled_seats": ()},
    allowed_effects=frozenset({EffectKind.ADD_STATUS, EffectKind.SUBMIT_PROTECTION,
                               EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    tags=frozenset({"god"}),
    max_count=1,
    instructions="...",
)
```

**契约**（依赖 §2.4 的多目标命令 D1）：

```python
DANCE_POOL_CONTRACT = ActionContract(
    contract_id="dance_pool",
    schedule_point=SchedulePoint.NIGHT_ACTION,
    order=30,                                  # 守卫是 50；假面是 20（先于舞者）
    action_types=("pool",),
    actions_requiring_target=frozenset({"pool"}),
    fallback_action_type="pool",               # 强制发动，无 pass
    allowed_effects=frozenset({EffectKind.ADD_STATUS, EffectKind.SUBMIT_PROTECTION,
                               EffectKind.SET_PRIVATE_DATA, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    per_window_limit=1,
    is_applicable=dancer_applicable,           # round_number >= 2 且可用座位 >= 3
    validate=validate_dance_pool,              # 恰好 3 个不同目标；不含已进过舞池者
    resolve=resolve_dance_pool,
)
```

`resolve_dance_pool` 产出的批次：

1. 对 3 个目标各一条 `ADD_STATUS(target, "in_pool")`
2. `SET_PRIVATE_DATA(pooled_seats = 旧值 + 3 个目标)` —— 实现"每名玩家整局只能进一次舞池"
3. **若舞者自己在这 3 人里** → 对 3 个目标各一条 `SUBMIT_PROTECTION(target, source="dance_veil")`（只挡狼刀，且**不参与对穿**，见 §2.3）
4. `EMIT_EVENT("DANCE_POOL", payload={"seats": [...]})` —— 观众可见（上帝视角），玩家侧不可见

**新文件 `backend/app/roles/mask.py`（假面，id `wolf-killer-mask`）**

```python
MASK_SPEC = RoleSpec(
    role_id="wolf-killer-mask",
    display_name="Mask",
    camp_id="werewolf",
    contracts=(WEREWOLF_KILL_CONTRACT, MASK_CONTRACT),   # 共享狼刀契约，字节一致
    initial_resources={"poison_immune": 1},              # 假面免疫女巫毒药
    initial_private_data={"last_masked": None},
    allowed_effects=frozenset({EffectKind.ADD_STATUS, EffectKind.SET_PRIVATE_DATA,
                               EffectKind.RECORD_PRIVATE_FACT, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    tags=frozenset({"wolf"}),
    max_count=1,
    instructions="...",
)
```

**契约**：`mask` @ `NIGHT_ACTION`，`order=20`（**先于舞者**），`action_types=("mask", "pass")`，`round_number >= 2` 门禁，"不能连续两晚同一人"用 `last_masked` 校验。`resolve` 产出 `ADD_STATUS(target, "masked")` + `RECORD_PRIVATE_FACT` + `SET_PRIVATE_DATA(last_masked)` + `EMIT_EVENT("MASK_GRANTED")`。

### 8.3 新增结算通道：`settle_pool()`

**`backend/app/core/night_settlement.py` 追加**：

```python
DANCE_POOL_STATUS = "in_pool"
MASKED_STATUS = "masked"
DANCE_CAUSE = "dance"

def settle_pool(
    statuses: Mapping[int, object],
    camps: Mapping[int, str],
    alive: Mapping[int, bool],
    round_number: int,
) -> tuple[tuple[dict[str, object], ...], dict[int, tuple[str, ...]]]:
    """Resolve the masquerade pool: the minority camp among the pooled seats dies.

    A masked seat counts as the opposite camp for this comparison only; the mask
    never changes the seat's real camp, so it does not affect the win check.
    """
    pooled = sorted(seat for seat, held in statuses.items()
                    if DANCE_POOL_STATUS in frozenset(held) and alive.get(seat, False))
    cleared = {seat: (DANCE_POOL_STATUS, MASKED_STATUS) for seat in pooled}
    if len(pooled) != 3:
        return (), cleared                       # 不足三人（有人当夜已死）→ 不结算
    def effective_camp(seat: int) -> str:
        camp = camps[seat]
        if MASKED_STATUS in frozenset(statuses[seat]):
            return "werewolf" if camp == "good" else "good"
        return camp
    good = [s for s in pooled if effective_camp(s) == "good"]
    wolves = [s for s in pooled if effective_camp(s) != "good"]
    if not good or not wolves:
        return (), cleared                       # 同阵营 → 无人出局
    losers = wolves if len(wolves) < len(good) else good   # 少数方
    deaths = tuple({"seat": s, "cause": DANCE_CAUSE, "round_number": round_number}
                   for s in losers)
    return deaths, cleared
```

**接入点**：`backend/app/core/effect_applier.py` 的 `settle_pending()`（`:409-421`）—— 在调用 `settle()` 之前先调 `settle_pool()`，合并两份 deaths（按座位去重，舞池死因优先），并在同一原子批次里清掉 `in_pool` / `masked` 状态。

**注意**：`settle_pool` 需要 `statuses` 与 `camps`，而现在的 `settle()` 只收 `role_resources` —— 这是**新增参数**，不是改现有签名（保持 `settle()` 原样，向后兼容）。

### 8.4 规则边界与待定项

| 边界 | 处理 |
| --- | --- |
| 舞池三人里有当夜已被狼刀/毒死的 | 官方未明文。默认：**按结算时的存活快照过滤 → 实际不足 3 人则跳过结算**。列为待定项 |
| 舞池少数方同时被狼刀 | 两处都判死 → 按座位去重，只死一次 |
| 舞者当夜出局 | 舞池已选定 → **仍结算**。列为待定项 |
| 可进舞池的存活玩家不足 3 人 | `is_applicable` 返回假 → 契约不发放，舞者不行动 |
| 面具是否影响预言家查验 | **不影响**：面具只作用于舞池结算，camp 字段不动 → 零改动 |
| 面具是否影响屠边判胜 | **不影响**：同上 |

**同步**：`DeathCause` 加 `dance`（5 处）；新观众事件 `DANCE_POOL`、`MASK_GRANTED`（5 处，见 §2.1）；`catalog.py` 加两个角色元数据；`test_catalog_routes.py` 角色数 +2。

**预设**：`twelve-player-masquerade`（3狼 1假面 4民 预女白痴 舞者，`enable_sheriff=True`）。

**验收**：新增 `backend/tests/test_masquerade_extension.py`，覆盖：

- 舞池同阵营 → 无人出局
- 舞池 2 好 1 狼 → 狼出局
- 舞池 1 好 2 狼 → 好人出局
- 面具把 1 好翻成狼 → 该好人变成少数方出局
- 舞者进舞池 → 3 人免疫狼刀（但挡不住毒）
- 舞者免疫毒药；假面免疫毒药
- 同一人第二次进舞池被 `validate` 拒绝
- 可进池人数 < 3 时契约不发放
- 舞池死因不影响 `rule_engine` 的屠边计数（camp 未变）

---

## 九、阶段 4 —— 技能赋予与胜利条件继承

### 9.1 奇迹商人 + 幸运儿 → 解锁「奇迹商人」

**幸运儿是状态不是身份牌** —— 这是本项目"一座位一 `RoleSpec`"模型装不下的东西。

**推荐设计**：把幸运儿的三种技能做成**三个预声明的专用契约**，挂在奇迹商人自己的 `RoleSpec` 上：

```python
LUCKY_SEER_CONTRACT   = ActionContract(contract_id="lucky_seer",   schedule_point=NIGHT_SEER_ACTION,  order=80, action_types=("check", "pass"), ...)
LUCKY_POISON_CONTRACT = ActionContract(contract_id="lucky_poison", schedule_point=NIGHT_WITCH_ACTION, order=80, action_types=("poison", "pass"), ...)
LUCKY_GUARD_CONTRACT  = ActionContract(contract_id="lucky_guard",  schedule_point=NIGHT_ACTION,       order=80, action_types=("guard", "pass"), ...)
```

- 三个契约的 `is_applicable` 都读**座位资源** `lucky_seer` / `lucky_poison` / `lucky_guard`（由奇迹商人在 `NIGHT_ACTION` 的 `SET_RESOURCE` 授予）
- **但 `Scheduler` 是按"角色声明的契约"发放请求的**（`scheduler.py:305-310`）→ 幸运儿座位如果是平民，它不会收到 `lucky_seer` 的请求
- **解决方案**：`Scheduler` 的请求发放改成"按座位资源判定" —— 当座位持有 `lucky_*` 资源时，即使其角色没声明该契约也发放请求。需要动 `scheduler.py:304-306`，并保证 `registry` 里能找到契约定义（可让 `Scheduler` 持有一个"通用契约池"）。**这是本阶段最大的设计改动，需单独评审**

**预设**：`twelve-player-miracle-merchant`（3狼 1狼王 4民 预 守 女 奇迹商人）。

### 9.2 孤独少女 → 解锁「孤独少女」

**好消息：不需要第三方阵营**。官方口径：孤独少女的胜负**继承崇拜对象**，她**始终算民**，预言家验她**始终显示好人**。

| 规则 | 机制 |
| --- | --- |
| 第一夜最开始时选一名崇拜对象，仅一次 | 新契约 @ `NIGHT_ACTION`，`order=5`（最早），`per_game_limit=1`，`RECORD_PRIVATE_FACT(namespace="idol")` |
| 胜负条件与崇拜对象一致 | **改 `rule_engine.check_win` 的返回** —— 增加"per-seat 归属覆盖" |
| 始终算民 | `tags={"villager"}` + `camp_id="good"` → **屠边计数零改动** |
| 预言家验她显示好人 | camp 是 good → **零改动** |
| 放逐投票时身份永远为平民 | 同上，零改动 |

**最小实现**：`WinResult` 增加 `winners: frozenset[int]` 字段（或 `defectors: frozenset[int]`），由 `RuleEngine` 从"崇拜对象在胜方"推导。会动 `models/actions.py:103`、`api/schemas.py`、前端结算弹窗 —— 但**不触碰 `WinningCamp` 目录、不触碰 `Camp` 枚举、不触碰那 11 个阵营耦合点**。

**预设**：`twelve-player-lonely-girl`（4狼 3民 1孤独少女 预女猎守）。

### 9.3 觉醒预言家 + 寻香魅影 → 解锁「寻香识命」

**觉醒预言家**：每晚任选 **2 名**玩家，结果只回答"这两人中有/无狼人"。

- **依赖 §2.4 的多目标命令改造（D1）**
- `resolve` 产出 `RECORD_PRIVATE_FACT(namespace="pair_checks", fact={"seats": [...], "has_wolf": bool})`
- 结果粒度只给"有/无"（官方未明文区分，列为待定项）
- `camp_label` 需要跨两个目标 → 需要在 `context_projector` 为多目标扩展 `selected_targets`

**寻香魅影**：

| 规则 | 机制 |
| --- | --- |
| 开局不入狼队，其他狼全出局后才能刀 | `no_wolf_pack` + `HIDDEN_WOLF_KILL_CONTRACT`（§2.2，**零新增**） |
| 首夜被动获知 1 名随机其他狼人的位置 | `RECORD_PRIVATE_FACT`，由 `is_applicable` 在首夜触发 |
| 氤氲缠绕：每晚绑 2 名玩家，一方出局另一方随之出局，**一局仅触发一次** | `ADD_RELATION("bonded_with", other_seat)`（`add_relation` 已存在，**当前无读取方**，正好用上）+ react 契约 @ `DAWN_REACTION` 读 `runtime.relations` + `per_game_limit=1` |
| 可绑自己/可绑狼队友 | `validate` 不设限（与官方一致） |

**预设**：`twelve-player-fragrance`（3狼 1寻香魅影 4民 觉醒预言家 女巫 猎人 守卫）。

---

## 十、阶段 5 —— 白天流程（永序之轮）

### 10.1 蚀时狼妃（`roles/time_wolf.py`）

| 规则 | 机制 |
| --- | --- |
| 每晚率先睁眼封锁 1 人，不能连续两晚锁同一人 | `NIGHT_ACTION` + `order=10`（比守卫早）+ `initial_private_data={"last_locked": None}` |
| 当晚好人阵营对该目标的**验人/毒药/守护**反弹给施放者 | **同恶夜骑士的反伤模式**（§5.2）：把 `reflect_check` / `reflect_poison` / `reflect_guard` 三个标记加入 `_PUBLIC_RESOURCES`，由 `seer.py` / `witch.py` / `guard.py` 的 `resolve` 各自读取并追加反弹效果。**复用同一套 CAS 去重** |
| 每局仅生效一次 | `reflect_used` 标记 + `resource_equals` 前置条件 |
| 生效后下一夜永久失去技能 | `SET_RESOURCE(reflect_used=1)` 后，`is_applicable` 读它返回假 |
| 解药不反弹、守卫反弹后次夜不能守前一晚目标 | 细粒度规则，写在三个生产者各自的 `resolve` 里 |

### 10.2 定序王子（`roles/order_prince.py`）—— 本阶段最贵的一项

| 规则 | 机制 |
| --- | --- |
| 每个白天**第一次**放逐投票结束后翻牌发动，回到本次投票前**重新投票** | **动白天投票流程** |
| 平票后的第二轮不可发动 | 判定 `vote_round == 1` |
| 重投前可额外发言 1 次 | 需要重新跑发言窗口 |
| 发动后的发言阶段狼人不可自爆 | 一个 `no_explode` 状态 |
| 翻牌公开身份 | `EMIT_EVENT` 走已有的翻牌模式（照 `idiot.py` 的 `EXILE_CANCELLED` 路径） |
| 每局限 1 次 | `per_game_limit=1` |

**必须动的代码**：

| 文件:行号 | 改什么 |
| --- | --- |
| `core/game_engine.py:1615-1616` | 清票逻辑要能被"重投"复用 |
| `models/vote.py:69-77` | **`window_id` 由 `game_id + round + vote_round` 决定 → 同轮重投必然撞窗与 `action_key`**。必须抬 `vote_round` 或引入独立的 re-vote 计数 |
| `core/game_engine.py:2370` `_clear_tiebreak_state` | 需要清 `voted_seats` / `_active_vote_window_id` |
| `core/game_engine.py:1856` `_execute_vote_resolution` | 插入"重投判定"分支 |
| `core/game_engine.py:1799` `_execute_tiebreak` | **骨架直接抄这个** |

**这是全计划中唯一会改到 `window_id` 形状的地方** —— `window_id` 是请求 token 的组成部分，**改形状会让进行中的旧局无法续跑**。必须在 `PointKey`/token 生成处做向后兼容。

**新调度点**：**推荐用 `VOTE_ACTION`**（`pipeline.py` 里已声明但无引擎 run 点），无需改 `_NIGHT_POINTS` 那套。

**预设**：`twelve-player-eternal-cycle`（3狼 1蚀时狼妃 4民 预 守 女 定序王子）。

---

## 十一、阶段 6 —— 动态技能复制（觉醒狼王、镜隐迷踪、猎日逐光）

### 11.1 觉醒狼王（`roles/awakened_wolf_king.py`）

| 规则 | 机制 |
| --- | --- |
| 保留狼王全部特征 + **2 次**狼王爪 | `initial_resources={"wolf_claw": 2}` |
| 夜间可将一次狼王爪**亲传给任意存活狼队友** | **运行期能力转移** |
| 每亲传一次即消耗一次自己的爪 | `CONSUME_RESOURCE(wolf_claw, 1)` |
| 每个狼队友最多获得 1 次 | 目标侧 `wolf_claw` 标记，`resource_equals == 0` 前置条件 |
| 不亲传则可发动两次（带走两人） | `per_game_limit=2` |

**亲传的实现**：`SET_RESOURCE(target=队友, resource="wolf_claw", value=1)`。但队友能否**发动**取决于它是否有 react 契约。

**推荐方案**：把 `WOLF_KING_SHOT_CONTRACT` 声明在**所有狼阵营角色**上，`is_applicable` 统一读 `context.resources.get("wolf_claw", 0) > 0`。普通狼初始 `wolf_claw=0`。**代价**：所有狼角色的 spec 都要加这个契约 → `registry.digest` 全变 → 旧局判 `drift`（可 `force` 续跑）。**这是本阶段最需要评审的决策**。

### 11.2 觉醒隐狼 + 魔镜少女 → 解锁「镜隐迷踪」

**魔镜少女**：验具体身份 —— **复用石像鬼的 `role_checks` 机制，零新增**。

**觉醒隐狼**：`no_wolf_pack` + 模仿一名玩家 → **当夜结束后**获得其技能。

| 路径 | 做法 | 代价 |
| --- | --- | --- |
| **E1 预声明池（推荐）** | 把可被模仿的技能全部预声明在觉醒隐狼自己的 spec 上（查验/毒/守/狼刀/开枪，共 5 个契约），`is_applicable` 读"模仿到的角色类型"标记 | 零架构改动。官方规则恰好是**穷举式**的，**这条路够用** |
| E2 动态契约 | 运行期从 registry 取被模仿角色的契约 | 与 `registry.digest` 冻结身份冲突。**不建议** |

**决策：走 E1。** 这条决策把觉醒隐狼从 XL 降到 L。

**预设**：`twelve-player-mirror`（3狼 1觉醒隐狼 4民 守卫 女巫 猎人 魔镜少女）。

### 11.3 蚀日侍女 + 流光伯爵 → 解锁「猎日逐光」

**流光伯爵**：

| 规则 | 机制 |
| --- | --- |
| 第 2 晚起每晚庇护 1 名**其他**玩家，免疫**夜间伤害**（狼刀、毒、梦游） | `SUBMIT_PROTECTION(source="radiance")` → 走 §2.3 的 `any_damage` 通道 |
| 不能连续两晚庇护同一人 | `last_shielded` 私有数据 |
| 流光反噬：侍女吞噬到伯爵、或吞噬到**被伯爵庇护的玩家** → 侍女第二白天出局 | `ADD_RELATION("shielded_by", ...)`，由侍女侧 react 读取 |

**蚀日侍女**：

| 规则 | 机制 |
| --- | --- |
| 知道其他狼，其他狼不知晓她 | **§2.2 的 `wolf_knows_pack` 单向可见变体** |
| 夜间不参与讨论袭击、不能自爆、其余狼全出局后才可袭击 | `no_wolf_pack` + `HIDDEN_WOLF_KILL_CONTRACT` |
| 第 2 晚起每晚吞噬 1 名非狼玩家，获得其技能**并继承当前状态** | **走 E1 预声明池** + `SET_RESOURCE` 复制目标资源值 |
| 当晚即可使用 | 同上 |
| 被吞噬者当晚失去技能直到天亮 | 给目标 `ADD_STATUS("silenced")` |
| 不能连续两晚吞同一人 | `last_devoured` 私有数据 |

**预设**：`twelve-player-sun-hunt`（3狼 1蚀日侍女 4民 预 女 摄梦人 流光伯爵）。

---

## 十二、未决决策与风险

**必须在对应阶段开工前拍板的 6 件事**：

| # | 决策 | 影响 | 建议 |
| --- | --- | --- | --- |
| 1 | 多目标命令走 D1（扩 schema）还是 D2（分多次问） | 契约形状，改晚了旧局不能续跑 | **D1**，阶段 2 前完成 |
| 2 | 觉醒隐狼/蚀日侍女走 E1（预声明池）还是 E2（动态契约） | 是否与冻结声明架构冲突 | **E1**，官方规则本就是穷举式 |
| 3 | 觉醒狼王"亲传"是否把 `WOLF_KING_SHOT_CONTRACT` 挂到所有狼角色 | 会让 `registry.digest` 全变、旧局判 `drift` | 倾向**是**，但需接受旧局 `force` 续跑 |
| 4 | 噩梦之影"首夜不与其他狼互知"是否完整实现 | 影响改造 B 的复杂度 | 先**降级**，后续再补 |
| 5 | 赤月使徒"当晚所有好人技能封印"的窄版本是否够用 | 是否需要新增 `runtime.global_statuses` | 用 **per-seat `silenced` 状态**，避免新增全局状态 |
| 6 | **假面舞会用哪个规则口径** | **决定假面是否进狼窝、行动顺序、是否有"查舞池"能力** | 见下方详解 |

**决策 6 详解（假面舞会口径）**：两个来源在关键点上不一致：

| 争议点 | [LAL 赛事口径](https://werewolves.games/jia-mian-wu-hui/) | [另一版整理](https://page.sm.cn/blm/node-page-new-995/index?id=28_2ad530fe96e6f51f6caf2e0f484ad2bc) |
| --- | --- | --- |
| 假面是否进狼窝 | **不与普通狼人共同睁眼**（隐狼模式） | 第一晚见面，第二晚先单独行动再共同行动 |
| 假面有没有"查舞池" | **有**：先查验一人是否在舞池，再戴面具 | 没有 |
| 行动顺序 | 舞者 → 假面 → 狼队 | 假面 → 舞者 → 狼队 |

**推荐采用「夸克/赛事通行口径」**（假面参与狼刀、先于舞者行动、无查舞池能力）：不需要隐狼机制、行动顺序与本计划的 `order` 设计一致、更符合"狼队大哥"的定位。**LAL 口径列为可配置变体** —— 因为 §2.2 已实现 `no_wolf_pack`，切换口径只需在 `MASK_SPEC` 里增删一个资源标记，**两种口径共用同一套代码**。

**已知风险**：

1. **`window_id` 形状变更**（阶段 5 定序王子）—— 请求 token 的组成部分，是唯一会让进行中旧局无法续跑的地方。必须做向后兼容。
2. **`GameConfig` 字段集是精确匹配**（`checkpoint_codec.py:40`）—— 阶段 0 加 `witch_self_save` 时必须同时改 `_CONFIG_FIELDS` + 编码 + 解码 `setdefault`，否则旧档全部拒绝。
3. **`runtime` 字段集也是精确匹配** —— 临时槽位不进编解码器，加字段会让旧检查点无法续跑。本计划中"守墓人需要新状态字段"和"赤月使徒需要全局状态"是两个高风险点，已给出规避方案。
4. **共享狼刀契约必须字节一致**（`registry.py:178-183`）—— 所有新狼角色的差异只能走资源标记，**禁止**在 `roles/werewolf.py` 里写角色名。已有测试门禁。
5. **`settle_pool` 需要 `statuses` 与 `camps`** —— 这是**新增参数**而非改 `settle()` 签名，但要在 `effect_applier.settle_pending()` 里正确合并两份 deaths 并原子清理状态，是实现时最容易出错的一处。
6. **官方资料有两处自相矛盾**（🟡）：奇迹商人的觉醒狼王板"狼人×2"（合计 11）vs "狼人×3"（合计 12）；觉醒隐狼的模仿次数/冷却未查到。落地前以游戏内为准。

---

## 十三、每阶段统一验收门禁

每个阶段结束必须全部通过：

```bash
cd backend && python -m pytest tests app --cov=app --cov-branch -q   # 100% statement + branch
cd frontend && npm test && npm run lint && npm run build
```

外加：

| 门禁 | 文件 |
| --- | --- |
| 角色数 / 预设数断言 | `tests/test_catalog_routes.py:80/103/169/179` |
| 每个已注册角色有非空 `rules` | `tests/test_catalog_routes.py:63-65` |
| 引擎禁词（不得出现角色名） | `tests/test_game_engine.py:23-29` |
| 解析器 / 渲染器 / prompt_builder 禁词 | `tests/test_action_resolver.py`、`tests/test_prompt_renderer.py`、`tests/test_prompt_builder.py` |
| 取值目录 5 处一致 | `tests/test_values.py:144-151`、`tests/test_schemas.py:113-114`、`tests/test_game_routes.py:962` |
| 观众事件分支完整 | `tests/test_game_routes.py::test_every_public_audience_action_type_has_a_replay_branch` |
| 前端事件覆盖 | `frontend/src/components/game/test/eventCoverage.test.tsx` |
| 旧档兼容 | `tests/test_recovery_compat.py`、`tests/test_checkpoint_codec.py` |
| 前端文案表唯一 | `frontend/src/test/labels.test.ts`（谁再写本地 `*_LABELS` 即红） |

**提交粒度**（`AGENTS.md` 规定）：每个 commit ≤ 3 个文件，`<type>(<scope>): <description>`。建议按「一个角色一个 commit 组」拆，基础设施改造单独成组。

---

## 十四、假设与边界

1. **基线为网易口径**：所有网易预设默认 `enable_sheriff=True` + `witch_self_save="never"`。假面口径的 6 个既有预设**保持原状**，不做批量改写。假面舞会预设按京城大师赛口径单独配置。
2. **假面舞会不属于网易 17 板**：它是京城大师赛原创板，独立计为「附加板型」。网易板计数不含它。
3. **不实现非高校赛板型**：2026 规则角色池里的炼金魔女、子狐、河豚、白昼学者、寂夜导师、觉醒愚者、觉醒女巫、觉醒猎人、狼鸦之爪、夜之贵族、觉醒狼美人属于迷雾鸦影/动物梦境/时波之乱/觉醒之夜等**非高校赛板型**，本计划不含。2026 高校赛板型官方尚未公布，公布后需重新评估。
4. **不删除、不改名任何既有角色 id**（`AGENTS.md` 硬规定）。狼王新增 `wolf-killer-wolf-king`，与既有白狼王 `wolf-killer-werewolf-king` 并存。
5. **阶段顺序可调整**：阶段 4–6 之间无硬依赖，可按优先级重排。但阶段 2 的**多目标命令（D1）是阶段 3.5 舞者与阶段 4.3 觉醒预言家的共同前置**；阶段 2 的**隐狼机制是阶段 3、4、6 以及假面舞会 LAL 变体的前置**；阶段 3 的 **`poison_immune` 是假面舞会的前置**。
6. **假面舞会可提前**：若想更早拿到这个板子，可在阶段 2 结束时插入（多目标命令 D1 + 隐狼已就绪），只需把 §2.3 的 `poison_immune` 标记提前到阶段 2 一并实现。
7. **工作量未给天数**：项目的测试门禁（后端 100% statement/branch + 前端事件覆盖表）对每个新角色都是硬成本，估算意义不大。以「阶段」为交付单位。
