# 实现方案：新增「骑士 / 狼美人 / 老酒鬼」三角色与对应板子

> 基线：`main` @ `e7e2f1e`，`pytest tests app` **3331 passed**，覆盖率 **100.00%**（statement + branch）
> 环境：WSL venv `/home/jm050711/.venvs/wolfkiller`（Windows 侧 Python 不可用于本任务）

---

## 一、结论摘要

| 角色 | 能否纯 Hook | 需要改的核心模块 |
| --- | --- | --- |
| 骑士 | ❌ 需要 1 处引擎改动 | `game_engine.py`（新增「发言结束后」DAY_ACTION 窗口） |
| 狼美人 | ❌ 需要 1 处投影改动 | `context_projector.py`（目标座位公开事实注入） |
| 老酒鬼 | ❌ 需要 2 处核心改动 | `night_settlement.py` + `effect_applier.py`（延迟死亡）、`context_projector.py`（免疫可见） |

**「新增角色不改核心模块」的架构承诺在这三个角色上无法完全成立**——这不是实现缺陷，而是三张卡的能力本身需要引擎提供新的时机点与新的可见性。守卫/白痴/白狼王之所以能纯 Hook 扩展，是因为它们复用的时机点（夜间行动、放逐裁决、白天行动）已经存在。本次要新增一个时机点。

**核心模块改动共 4 个文件**，全部是「通用机制」而非「角色名硬编码」——引擎不会出现 `knight` / `wolf_beauty` / `old_drunkard` 任何角色名（有测试门禁）。

---

## 二、骑士（Knight）

### 2.1 规则到机制的映射

| 规则 | 机制 |
| --- | --- |
| 全体发言结束、放逐投票**之前**翻牌 | **新增**「发言结束后」的 `DAY_ACTION` 窗口（slot `post_speech`） |
| 指认一名玩家，法官宣布狼人/好人 | 契约声明 `selected_target_fact_namespaces={"camp_label"}`，`resolve` 读 `facts["selected_target"]["camp_label"]` |
| 是狼人 → 该玩家立即死亡 | `SUBMIT_DAMAGE(target, cause="knight_duel")`，由 `DAWN_REACTION` 窗口结算 |
| 是狼人 → 白天立即结束、直接入夜 | `EMIT_EVENT("DAY_INTERRUPTED", cause="knight_duel")`，复用白狼王已跑通的通用事件 |
| 是好人 → 骑士以死谢罪、无遗言 | `SUBMIT_DAMAGE(self, cause="knight_duel")`；**不**发 `DAY_INTERRUPTED`，白天照常继续 |
| 一局只能发动一次 | `initial_resources={"duel": 1}` + `CONSUME_RESOURCE` + `resource_equals` 前置条件 |

### 2.2 契约设计

```python
KNIGHT_DUEL_CONTRACT = ActionContract(
    contract_id="knight_duel",
    schedule_point=SchedulePoint.DAY_ACTION,
    order=30,
    action_types=("duel", "pass"),
    actions_requiring_target=frozenset({"duel"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.SUBMIT_DAMAGE,
                               EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    selected_target_fact_namespaces=frozenset({"camp_label"}),
    per_window_limit=1,
    is_applicable=knight_applicable,       # 存活 且 duel 资源 > 0
    validate=validate_knight_action,       # 不能指认自己；资源必须可用
    resolve=resolve_knight_action,
)
```

`order=30` 取在白狼王（20）之后、其它白天契约之前；白狼王与骑士同时在场时，白狼王先行动。

### 2.3 为什么不能复用现有窗口

- **不能放 `EXILE_VERDICT`**：该窗口只在放逐票已产生后才运行（`_apply_exile` 用 `EXILE_PENDING` 提交），骑士此时再翻牌就等于「投完票才裁决」，违反规则原文。
- **不能放现有 `DAY_ACTION`（`_run_day_action`，每位发言者前）**：那是「发言开始前」，而规则要求「全体发言结束后」。

→ 因此必须在引擎里新增一个发言结束后的窗口。

### 2.4 引擎改动（`game_engine.py`）

在 `_execute_speech_round()` 的发言循环结束后、`SM_Event.SPEECHES_COMPLETE` 之前插入：

```python
# 发言结束后的通用窗口：角色可在放逐投票开始前发动一次白天技能。
if await self._run_post_speech_day_action():
    self.state.current_speaker = None
    self.state.speaking_order = []
    return True
```

新增两个方法（均**不含任何角色名**）：

```python
def _post_speech_day_action_enabled(self) -> bool:
    """当且仅当有角色声明了 DAY_ACTION 契约时为真（与 _day_action_enabled 同构）。"""

async def _run_post_speech_day_action(self) -> bool:
    """跑发言结束后的 DAY_ACTION 窗口；返回 True 表示白天被打断。
    用 slot=post_speech 与「每位发言者前」的窗口区分 journal 相位。"""
```

- 打断时复用现有 `_resolve_day_interruption()`（它已处理结算、去重发布、`DAWN_REACTION`、检查点与转夜）。
- 窗口的 journal 相位由 `Scheduler.point_phase(state, "post_speech")` 自动生成为 `speech#post_speech`，与既有 `phase#slot` 机制一致。

---

## 三、狼美人（Wolf Beauty）

### 3.1 规则到机制的映射

| 规则 | 机制 |
| --- | --- |
| 每晚与狼队共刀 | 原样复用 `WEREWOLF_KILL_CONTRACT`（声明必须完全一致，注册表静态校验会拦不一致） |
| 参与杀人后单独魅惑一名好人 | **独立契约**，挂在 `NIGHT_WITCH_ACTION` 调度点（该点在狼队投票之后、结算之前，且引擎**无条件**执行该阶段） |
| 不能连续两晚魅惑同一人 | 独立私有字段 `last_charmed`（**不复用** `last_guarded`，否则污染守卫连守校验） |
| 出局时当晚被魅惑者殉情 | 在魅惑时记 `ADD_RELATION(target, "charmed_by", self)`；出局时由 `react` 窗口对目标 `SUBMIT_DAMAGE(cause="charm")` |
| 不能自爆、不能自刀 | 不实现自爆契约；`validate` 拒绝自指与其他狼队友 |
| 被魅惑者免疫时无效 | 读目标座位的公开资源（见 3.3），免疫则拒绝魅惑 |

### 3.2 契约设计

```python
WOLF_BEAUTY_CHARM_CONTRACT = ActionContract(
    contract_id="wolf_beauty_charm",
    schedule_point=SchedulePoint.NIGHT_WITCH_ACTION,   # 狼刀投票之后
    order=40,
    action_types=("charm", "pass"),
    actions_requiring_target=frozenset({"charm"}),
    fallback_action_type="pass",
    allowed_effects=frozenset({EffectKind.SET_PRIVATE_DATA, EffectKind.ADD_RELATION,
                               EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    selected_target_fact_namespaces=frozenset({"camp_label", "resource_labels"}),
    per_window_limit=1,
    is_applicable=wolf_beauty_applicable,
    validate=validate_wolf_beauty_action,
    resolve=resolve_wolf_beauty_action,
)
```

**殉情契约**（响应窗口）：

```python
WOLF_BEAUTY_REVENGE_CONTRACT = ActionContract(
    contract_id="wolf_beauty_revenge",
    schedule_point=SchedulePoint.DAWN_REACTION,
    order=40,
    action_types=("revenge",),
    fallback_action_type="revenge",
    allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR"}),
    response_event_types=frozenset({"PLAYER_DIED"}),
    response_reasons=frozenset({"wolf_kill", "witch_poison", "exile",
                                "hunter_shot", "knight_duel"}),
    per_window_limit=1,
    is_applicable=wolf_beauty_revenge_applicable,
    react=react_wolf_beauty_revenge,     # 纯 react，不调用模型
)
```

`response_reasons` **必须排除** `self_explode`（规则：不能自爆带人）与 `wolf_kill` 的自刀场景（`validate` 拒绝自刀，故自刀路径不会产生殉情）。

### 3.3 为什么需要投影器改动

规则要求「免疫狼美人魅惑」生效，但**狼美人的 Hook 看不到目标座位的任何资源或状态**：

- `ActionContext.facts` 只注入 `selected_target` / `actor_identity` / `camp_members` / `sheriff` / `private_checks` / `wolf_kill_target`；
- `project_selected_target()` 目前**只接受** `{"camp_label"}`，且只注入 `{"seat", "camp_label"}`；
- `RoleRegistry` 静态校验写死 `selected_target_fact_namespaces <= {"camp_label"}`。

→ 需要把「目标座位的公开事实」这一机制扩展出两个命名空间：

| 命名空间 | 注入内容 | 公开性 |
| --- | --- | --- |
| `camp_label` | `"good"` / `"werewolf"` | 既有，用于骑士裁决 |
| `resource_labels` | 目标座位的**公开资源**（白名单：`charm_immune`） | 新增 |
| `status_labels` | 目标座位的**公开状态**（白名单：`poisoned` / `wounded`） | 新增 |

**老酒鬼的免疫用「公开资源」表达**：`initial_resources={"charm_immune": 1}`。选资源而非状态的原因：

- `initialize_role_resources()` **只写 `SET_RESOURCE`，不写 `ADD_STATUS`**，用状态就需要改这个函数；
- 而 `initial_resources` 是既有机制，开局自动落账，**不需要任何核心改动**。

---

## 四、老酒鬼（Old Drunkard）

### 4.1 规则到机制的映射

| 规则 | 机制 |
| --- | --- |
| 好人阵营**平民牌**（不计入神职） | `camp_id="good"`，且**必须**修 `rule_engine.py` 的计数（见 5.2） |
| 免疫狼美人魅惑 | `initial_resources={"charm_immune": 1}` + 投影器公开（见 3.3） |
| 被撒毒/射杀后进入中毒/负伤状态 | 引擎在「伤害本应致死但目标带延迟标记」时 `ADD_STATUS` |
| 当天不死 | `settle()` 跳过该目标的致死判定 |
| 次日发言结束后死亡 | 引擎在发言轮结束时结算 |

### 4.2 延迟死亡的两个改动点

**改动 A：`night_settlement.settle()` 接受状态，延迟致死**

```python
def settle(damage, protection, seats, alive, round_number,
           statuses=None) -> tuple[deaths, resulting_alive, delayed_seats]:
```

- 新增可选参数 `statuses`（座位 → 状态集合），默认 `None` 保持既有调用兼容；
- 致死判定处增加：若目标带 `delayed_death` 状态，则**不置死、不计入 deaths**，改为收进 `delayed_seats`；
- 返回值由 2 元组扩为 3 元组。

**改动 B：`effect_applier.settle_pending()` 落状态**

- 把 `simulated.statuses` 传给 `settle()`；
- 对返回的 `delayed_seats` 逐个 `ADD_STATUS(delayed_death)`（`status_present: false` 前置条件），并按 cause 区分 `poisoned` / `wounded`（私有或公开由投影器白名单决定）；
- 结算事件里追加这些状态变更的审计事件。

**改动 C：`game_engine` 在发言轮结束时结算延迟死亡**

在 2.4 新增的「发言结束后」窗口**之后**、`SPEECHES_COMPLETE` 之前：

```python
deaths = await self._resolve_delayed_deaths()
```

新增方法（**不含角色名**）：

```python
async def _resolve_delayed_deaths(self) -> tuple[DeathReport, ...]:
    """结算带延迟死亡状态的座位：标记死亡、移除状态、发布 PLAYER_DIED。
    致死原因按伴随的中毒/负伤状态回填（witch_poison / hunter_shot）。"""
```

### 4.3 时机确认

| 时点 | 事件 |
| --- | --- |
| 第 N 夜 | 女巫撒毒 → `NIGHT_COMMIT` 结算时本应致死 → 被延迟，落 `poisoned` + `delayed_death` |
| 第 N+1 天 发言 | 老酒鬼正常发言 |
| 第 N+1 天 发言结束 | `_resolve_delayed_deaths()` → 死亡、公开 `PLAYER_DIED` |
| 第 N+1 天 投票 | 老酒鬼已出局，不参与 |

猎人在第 N 天被放逐后开枪的情况同构（`DAWN_REACTION` 结算 → 延迟 → 次日发言结束后死亡）。

---

## 五、必须修改的既有文件清单

| # | 文件 | 改动 | 属于核心模块 |
| --- | --- | --- | --- |
| 1 | `core/game_engine.py` | 新增发言结束后的 `DAY_ACTION` 窗口 + 延迟死亡结算 | ✅ 是 |
| 2 | `core/night_settlement.py` | `settle()` 接受 `statuses`、跳过延迟致死、返回 `delayed_seats` | ✅ 是 |
| 3 | `core/effect_applier.py` | `settle_pending()` 传状态、落延迟状态 | ✅ 是 |
| 4 | `core/context_projector.py` | `selected_target` 注入 `resource_labels` / `status_labels` | ✅ 是 |
| 5 | `roles/registry.py` | 校验放行新命名空间；注册三个新 spec | 否 |
| 6 | `catalog.py` | 3 条 `ROLE_METADATA` + 2 个 `STANDARD_PRESETS` | 否 |
| 7 | `services/audience_projector.py` | 三角色的事件映射 | 否 |
| 8 | `tests/test_catalog_routes.py` | 断言 8→11、4→6 | 否 |
| 9 | 前端 `theme/tokens.ts` / `RoleIcon.tsx` / 事件中文名映射 | 新增三角色 | 否 |

### 5.1 为什么 `rule_engine.py` **不**必改（推翻我此前评测题里的结论）

我此前在评测题真值里写「骑士漏计导致胜负反转」。**重新核对后该结论部分有误**：

- `rule_engine.py` 按**角色名子串**匹配：`"seer"/"witch"/"hunter"/"guard"/"idiot"/"villager"`；
- `wolf-killer-knight` 不命中任何子串 → 确实漏出 `alive_gods`；
- **但**：`alive_gods == 0` 只在**所有**神职都死光时成立。骑士是神职之一，只要其它神职还活着，`alive_gods > 0`，判定不受影响。**只有「骑士是最后一名存活神职」时才会提前触发狼胜**——这是真实缺陷，但触发条件比评测题描述的更窄；
- `wolf-killer-old-drunkard` 不命中 `"villager"` → 漏出 `alive_villagers`，但该分支被更早的 `alive_gods == 0` 掩盖，**只改 `reason` 标签、不改胜负结论**。

**结论：仍应修**（骑士作为最后神职时的提前判负是真 bug），但优先级低于延迟死亡。方案里把它列为**第 10 项、可独立提交**。

### 5.2 `rule_engine.py` 的修法（建议）

改为读 `RoleSpec.tags` 而非角色名子串：

- 给每个 spec 加 `tags={"god"}` / `tags={"villager"}`；
- `rule_engine` 从 registry 取 tags 统计；
- 同时修掉既有 `wolf-killer-werewolf` / `wolf-killer-werewolf-king` 也不命中任何子串的问题（狼不计入两类，实际无影响，但语义上应显式排除）。

⚠️ 此改动会让 `registry.digest` 变化 → **进行中的对局无法续跑**，需先结束在途对局。

---

## 六、测试清单

| 类型 | 文件 | 关键用例 |
| --- | --- | --- |
| 新增 | `tests/test_knight_extension.py` | 契约声明（调度点/一次性/命名空间）；裁决狼人→伤害+`DAY_INTERRUPTED`；裁决好人→自身死亡+**不**打断；`pass` 分支；资源耗尽后不可用；指认自己被拒；引擎级：发言结束后窗口触发、打断后转夜 |
| 新增 | `tests/test_wolf_beauty_extension.py` | 复用狼刀契约（声明一致）；连魅限制（独立字段）；魅惑免疫目标被拒；`validate` 拒绝自指/狼队友/连魅；殉情 `react` 对全部 cause 生效、对 `self_explode` **不**生效；无魅惑关系时 `react` 静默 |
| 新增 | `tests/test_old_drunkard_extension.py` | 免疫资源已落账；毒杀被延迟（`settle` 返回 `delayed_seats`）；状态已落；次日发言结束后死亡；枪杀同构；**夜刀不延迟**（正常当夜死）；延迟死亡后状态被清除；胜负判定 |
| 新增 | `core/test/test_delayed_death.py` | `settle()` 新签名与 3 元组返回；`statuses=None` 兼容路径；延迟与致死的边界 |
| 更新 | `tests/test_catalog_routes.py` | `8 → 11`、`4 → 6` |
| 更新 | `tests/test_context_projector.py` | 新命名空间注入；非白名单资源/状态**不**注入 |
| 更新 | `tests/test_game_engine.py` | 引擎禁词（新增代码不得出现角色名） |

**门禁**：`python -m pytest tests app --cov=app --cov-branch --cov-fail-under=100 -q` 必须 100% 通过。

---

## 七、风险与未决问题

| # | 问题 | 处理 |
| --- | --- | --- |
| 1 | 骑士裁决为狼人时，**被裁决的狼人是否留遗言**？ | ✅ **已查证**，见第 7.1 节 |
| 2 | 狼美人「参与杀人后」若狼队当夜空刀，是否仍可魅惑？ | ✅ **已查证**，见第 7.2 节 |
| 3 | 老酒鬼被延迟期间**再次**中毒/负伤 | 状态已存在则不重复落；死亡只结算一次 |
| 4 | 狼美人魅惑的**目标座位信息公开性** | 魅惑关系走 `RELATION` 私有；公开事件只报「某人被魅惑」不带目标座位，避免泄露 |
| 5 | `settle()` 返回值由 2 元组变 3 元组是**破坏性签名变更** | 用默认参数 + 显式元组解包，并更新既有调用点与测试 |
| 6 | 改动会变 `registry.digest` | 部署前须结束在途对局；已在报告中提示 |

### 7.1 骑士裁决的遗言（已查证）

**官方遗言规则**（[网易《狼人杀-官方正版》规则介绍](http://langrensha.163.com/wanfa/guize/2017/10/18/26899_719311.html)，原文）：

> 【遗言规则】晚上死亡的玩家，只有首夜有遗言；**所有白天死亡的玩家都有遗言**。

**官方骑士技能原文**（[网易《狼人杀-官方正版》特色玩法](http://langrensha.com/wanfa/tese/2017/10/17/26900_719112.html)）：

> 骑士可以在白天全体发言结束，放逐投票前，翻出底牌并指定一名玩家，由法官宣布此玩家是狼人还是好人，若是狼人，则此玩家立即死亡，白天结束，马上进入晚上；如果不是，则骑士以死谢罪，当天的投票继续。该技能一局游戏只能发动一次。

**据此裁定**：

| 场景 | 官方遗言规则推导 | 采用 |
| --- | --- | --- |
| 被裁决的**狼人**白天死亡 | 「所有白天死亡的玩家都有遗言」→ **有遗言** | ✅ **留遗言** |
| 骑士裁决好人后以死谢罪 | 同属白天死亡 → 官方规则下**有遗言**；但需求方规则原文明确「**且无遗言**」 | ✅ **不留遗言**（需求方规则优先，属该版本的刻意差异） |

> ⚠️ 需求方规则与官方遗言规则在「骑士自己」这一项上存在冲突，已按需求方规则实现，并在报告中标注。
>
> 附带结论：被裁决的狼人留遗言**不改变**「白天立即结束」——遗言属于出局结算的一部分，在进入夜晚前说完。

### 7.2 空刀夜能否魅惑（已查证）

**来源**：[狼美人最后出局能殉情带人吗？](https://m.langrensha.net/strategy/2022122701.html)（原文）：

> 狼美人可以**在任意一个晚上**对**任意一名玩家**进行魅惑，当自己出局后可以带走这位被魅惑的玩家，但**无法连续两个晚上对同一名玩家进行魅惑**。

**据此裁定**：魅惑是**独立的夜间行动**，不以后续是否刀中人为前提；狼队当夜空刀（全 `pass`）时，狼美人**仍可魅惑**。

> 补充：同一来源明确「狼美人是**无法自爆和自刀**的，而其余任意情况出局都可以带人」——与本方案 3.2 节的 `response_reasons` 排除 `self_explode` 一致。

---

## 八、实施顺序（每步都可独立验证）

1. **投影器机制**（`context_projector` + `registry` 校验 + 测试）—— 最小、无行为影响
2. **骑士**（新角色 + 引擎发言后窗口 + 测试）
3. **狼美人**（新角色 + 殉情响应 + 测试）
4. **老酒鬼**（新角色 + `night_settlement` + `effect_applier` + 引擎延迟结算 + 测试）
5. **目录/预设/观众端/前端**（元数据与映射）
6. **`rule_engine` 计数修正**（可独立提交）
7. 全量门禁 + 前端构建验证

---

## 九、需要你确认的决策点

1. **是否接受修改 4 个核心模块**？（不改就无法让这三个角色真正生效——延迟死亡与魅惑免疫没有现成时机点/可见性）
2. **`rule_engine` 计数修正是否本次一并做**？（建议做，但它会让 digest 变化、在途对局不可续跑）
3. **第 7 节两个规则歧义**（骑士裁决的狼人遗言、空刀夜能否魅惑）按我的处理走，还是你有别的裁定？
