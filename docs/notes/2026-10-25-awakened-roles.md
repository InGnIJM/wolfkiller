# 觉醒狼王 / 觉醒女巫实现记录

日期：2026-10-25
范围：`backend/app/roles/{awakened_wolf_king,wolf_claw,awakened_witch}.py`、`catalog.py`、
`core/{game_engine,night_flow,scheduler,conversation_log,context_projector}.py`、`models/pipeline.py`、
`api/{schemas,routes/game_routes}.py`、`services/{game_service,audience_projector}.py`、前端目录/事件/立绘登记，
以及文档计数（`README.md`、`AGENTS.md`、`CLAUDE.md`、`docs/*`）。

本记录对应上游计划 [2026-10-04-netease-college-boards.md](../plans/2026-10-04-netease-college-boards.md)
的 **6.5 觉醒女巫 / 6.6 觉醒狼王**，以及决策单
[2026-10-21-awakened-wolf-king.md](../plans/2026-10-21-awakened-wolf-king.md)。
两张牌同批上线：新增角色 **2 个**、新增标准预设 **1 套**（12 人觉醒女巫）；`builtin_registry.freeze()`
实测为 **42 角色 / 25 预设**（`catalog.ROLE_METADATA` 也是 42 条，二者一一覆盖）。

## 一、来源与规则口径

| 角色 | 权威来源 | 来源级别 | 本作取舍 |
| --- | --- | --- | --- |
| 觉醒狼王 | 卡牌原文 [`docs/reference/netease-role-skills.md`](../reference/netease-role-skills.md#L16-L18) | **卡牌原文**（最高） | 卡面四件事全做：两次狼王爪、夜间亲传、自己出局可带走两人、可自爆；官方公告（<http://langrensha.163.com/news/official/20231113/26904_1119723.html>，2023-07-28）补充的「保留狼王基本特征 / 可自刀 / 递枪后队友相当于狼王 / 不给队友则带走两人」按公告落地 |
| 觉醒女巫 | 官方公告 <http://langrensha.163.com/news/official/20240805/26904_1171626.html>（2024-08-02） | **官方公告**（卡牌表未收录，低于卡牌原文） | 一瓶解药 + 三次毒药调配；每次调配先选毒杀目标，再按第 1 / 2 / 3 次分别选 1 / 2 / 3 名协助者；协助者夜里交流并逐人表态，**全部同意**才生效；女巫不知道表态结果；每人整局只能协助一次 |

**板型**：官方公告同时给了「12 人觉醒女巫进阶场」和「10 人觉醒女巫娱乐场」。本作只实现前者：

```
twelve-player-awakened-witch（12 人 / 屠边 / 开警长 / 女巫全程不可自救）
3× 狼人 · 1× 觉醒狼王 · 4× 平民 · 1× 预言家 · 1× 守卫 · 1× 猎人 · 1× 觉醒女巫
```

**没有实现**的板：10 人觉醒女巫娱乐场（公告未给完整阵容），以及官方公告里觉醒狼王自己的
「娱乐板 / 进阶场」两套专属板（决策单 D6 推荐的两套预设也没做，觉醒狼王目前只随觉醒女巫板上线，
自定义板可选）。这两项是覆盖缺口，不是「已支持」——不要在规则页或文档里写成官方全板支持。

## 二、觉醒狼王（`wolf-killer-awakened-wolf-king`）

### 2.1 采用的规则

- 狼人阵营，与狼队共用同一条 `werewolf_kill` 契约（夜间讨论、逐票、自刀都按普通狼处理；不声明
  `self_kill_forbidden`）。
- 初始资源：`awakened_claw=2`、`explode=1`；`max_count=1`；`tags={"wolf"}`。
- **两次狼王爪**：夜间可把其中一次亲传给一名存活、尚未持爪的狼窝队友；每名队友整局最多收到一次；
  不能传给自己；隐狼（`no_wolf_pack`）不在传递范围。
- **不亲传则双爪**：出局时可一次带走两名存活玩家（`sweep`）；已传出一爪则只剩单爪。
- **爪的发动死因**：`{wolf_kill, exile, hunter_shot}`。被毒、自爆、殉情、骑士决斗、被另一只爪带走都不发动。
  **成为最后一狼时也不能发动**（实现选择，见「已知简化」第 2 条；该判断只做在新的爪契约里，
  普通狼王完全不受影响）。
- **自爆**：白天发言前可以自爆——公开身份、自己出局、当日结束、不带走任何人，且不触发狼王爪；
  警长竞选期也可以自爆吞徽（走既有 `SheriffDirector` 的 `explode` 路径）。
- **被爪带走者**：死因沿用 `wolf_king_shot`（不新增死因），所以「被带走的猎人不能开枪、被带走的
  狼美人不殉情」等既有语义自动成立。

### 2.2 契约与调度点（`roles/wolf_claw.py` + `roles/awakened_wolf_king.py`）

| 契约 id | 调度点 | order | 谁持有 | 作用 |
| --- | --- | --- | --- | --- |
| `awakened_wolf_claw_pass` | `NIGHT_ACTION` | 56 | 觉醒狼王 | 每夜一次（`per_round_limit=1`）：把 1 爪传给队友；或 pass。守卫 50、摄梦人 55 之后、狼刀投票之前 |
| `awakened_wolf_claw_sweep` | `DAWN_REACTION` | 41 | 觉醒狼王 | 两爪在手且满足死因时，一次选 2 名不同存活目标（`multi_target_actions={"sweep": 2}`），或 pass |
| `awakened_wolf_claw` | `DAWN_REACTION` | 42 | 觉醒狼王 | 至少 1 爪时单发带走 1 人，或 pass |
| `awakened_wolf_claw_gift` | `DAWN_REACTION` | 42 | **11 个既有狼窝角色**（受赠后） | 队友收到的爪：`awakened_claw_gift` 私有槽非空时开窗，单发带走 1 人 |
| `awakened_wolf_king_explode` | `DAY_ACTION` | 20 | 觉醒狼王 | 无目标自爆：消耗 `explode`、自伤（死因 `self_explode`）、公开身份（`PLAYER_REVEALED`）、`DAY_INTERRUPTED` |

**亲传写什么**：消耗自己 1 爪，向目标写 `SET_RESOURCE awakened_claw_received=1`（永久标记，
用于「每名队友最多一次」和 `selected_target.resource_labels` 校验），再向目标的
`awakened_claw_gift` 私有槽写一段赠爪说明。**目标不获得 `awakened_claw` 资源**——受赠者的
爪由私有槽承载，由 `awakened_wolf_claw_gift` 契约发动；发动时清空该槽（一爪一次）。
11 个既有狼窝角色（`werewolf` / `werewolf-king` / `wolf-king` / `wolf-beauty` / `awakened-wolf-beauty` /
`evil-knight` / `mask` / `nightmare` / `wolf-witch` / `crimson-envoy` / `time-wolf`）的 spec 各自追加
`AWAKENED_CLAW_GIFT_CONTRACT`、`initial_private_data` 里的 `awakened_claw_gift: None` 和
`SET_PRIVATE_DATA` 权限；**它们的 `initial_resources` / `schema_version` 没有改**。

**双爪顺序**：sweep（41）先于单爪（42）。sweep 打完后资源归零，单爪窗口重新判 `is_applicable`
后自行跳过；sweep pass 则单爪窗口照常出现。两爪在手时若先 pass 双爪再打单爪，会只剩一爪可用
（第二爪作废）——这是固定目标数 schema 的已知体验取舍，不是规则 bug。

**观众事件**：复用 `WOLF_KING_SHOT`（每条一个目标）与 `WOLF_KING_REASONING`（`action_type` 扩到
`shoot / pass / sweep / pass_claw / explode`），**没有新增 DTO / 投影器事件**。亲传的语义靠 reason
文本承载；如果实跑发现观众读不懂，再升级为独立事件（决策单 V2）。

## 三、觉醒女巫（`wolf-killer-awakened-witch`）

### 3.1 采用的规则

- 好人阵营**神职**（`camp_id="good"`、`tags={"god"}`），`max_count=1`。
- 初始资源：`antidote=1`、`brew=3`、`brew_used=0`，以及协助者票据/目标/人数/回合/票值的五个
  零值资源；私有数据：`wolf_kill_target`、`saved_seat`、`brew_plan`、`assist_history`。
- 每晚只能选一个技能：`save` / `poison` / `pass`。
- **解药**只能救当晚狼刀目标；本预设 `witch_self_save="never"`（12 人场全程不能自救，自定义板仍可
  按配置选首夜可自救）。解药与守卫守护同时落在同一刀口时仍然死亡（同守同救），与普通女巫共用
  同一条 `SUBMIT_PROTECTION(source="witch_antidote")` 与 `night_settlement` 的对穿规则。
- **毒药调配**：先选一名存活、非自己的目标（`self_poison_forbidden`），消耗 1 次 `brew` 并写
  `brew_plan`；随后按第 1 / 2 / 3 次分别选 1 / 2 / 3 名存活、整局未协助过的协助者。可以选自己，
  也可以选毒药目标；不限制阵营（狼人也可以当协助者，并可以故意反对）。
- **表态**：全部协助者都 `agree` 才生效；任一人 `veto` / `pass` / 未表态都不生效，但这次调配
  次数已经消耗。女巫不知道任何人的选择，也看不到私聊（除非她把自己选进协助者）。
- **毒药无视守卫**；被毒死的猎人不能开枪、狼王 / 觉醒狼王不能发动爪（既有 poison 语义）。
- **每人整局只能协助一次**：选取协助者时写 `assist_used=1`，用 `selected_target.resource_labels`
  拒掉重复选择；即使本次调配被否决或中止，协助次数也算已消费。

### 3.2 契约与子调度点

| 契约 id | 调度点 | order | 说明 |
| --- | --- | --- | --- |
| `awakened_witch_brew` | `NIGHT_WITCH_ACTION` | 20 | `save` / `poison` / `pass`；解药走 `SUBMIT_PROTECTION`，毒药只写 `brew_plan`，不立刻产生伤害 |
| `awakened_witch_assist_pick_1` | `NIGHT_WITCH_ASSIST_PICK` | 11 | 第 1 瓶：选 1 名协助者（`target_seat`） |
| `awakened_witch_assist_pick_2` | `NIGHT_WITCH_ASSIST_PICK` | 12 | 第 2 瓶：选恰好 2 名（`multi_target_actions={"assist": 2}`） |
| `awakened_witch_assist_pick_3` | `NIGHT_WITCH_ASSIST_PICK` | 13 | 第 3 瓶：选恰好 3 名（`multi_target_actions={"assist": 3}`） |
| `awakened_witch_assist_vote` | `NIGHT_WITCH_ASSIST_VOTE` | 10 | **借出契约**（`granted_contracts`）：向持有 `assist_token` 的协助者发请求，`agree` / `veto` / `pass`，目标必须是毒药目标 |
| `awakened_witch_assist_tally` | `NIGHT_WITCH_ASSIST_TALLY` | 10 | 确定性计票；引擎合成 `resolve` 命令，不调用模型 |

**三个子调度点不在 `_NIGHT_POINTS` 里**：引擎在既有女巫 stage 跑完 `NIGHT_WITCH_ACTION` 之后、
进入 stage 10 之前调用 `_run_assist_steps()`，所以**旧档持久化的 stage 号与 `_NIGHT_POINTS`
顺序都没有动**。夜晚流程：

```
NIGHT_WITCH_ACTION
  ├─ brew: save → 立即结算解药；poison → 只写 brew_plan；pass → 结束
  ├─ 若本轮有 brew_plan：NIGHT_WITCH_ASSIST_PICK（按 brew_used 选 1/2/3 人）
  ├─ ≥2 名协助者：逐座位私聊一轮（每人至多一条，历史累积；写 NIGHT_INTEL 记录）
  ├─ NIGHT_WITCH_ASSIST_VOTE（逐一 agree/veto/pass）
  └─ NIGHT_WITCH_ASSIST_TALLY（passed → SUBMIT_DAMAGE(poison) + WITCH_POISON；否则不产生伤害）
```

- **计票是确定性的**：`assist_votes` 派生事实（`PROJECTED_FACT_NAMES` / `_DERIVED_FACT_BUILDERS` /
  `prompt_renderer._HOISTED_FACTS` 同步）把各协助者的 `assist_vote` 0/1 交给纯 hook；
  `GameService._command_provider` 对 `awakened_witch_assist_tally` 直接返回合成命令
  （`action_type="resolve"`），**不产生模型请求行，也不计 token**。
- **结果四态**（`AWAKENED_WITCH_POISON_RESULT`）：`passed`（全员同意，落毒）/ `vetoed`（有人反对或弃权）/
  `incomplete`（有人没有产生票，例如被封印）/ `aborted`（女巫放弃选协助者，`brew` 已消耗）。
- **私聊**：`ConversationLog.add_night_intel()` 写 `NIGHT_INTEL` 记录，`visible_to` 恰为协助者座位，
  **不写 `speaker_role`**（避免泄露身份）；`get_conversations_for_role()` 只让这些座位在各自的历史里
  看到它。私聊走 `NightDirector.assist_discussion_prompt()`，提示词只给本人身份、毒药目标、协助者
  名单和已有发言，明确要求不透露身份/阵营；`AWAKENED_WITCH_ASSIST_CHAT` 同时对观众（上帝视角）公开。
- **女巫不知道表态**：`assist_votes` 在 `_HOISTED_FACTS` 里被挡出提示词；女巫的提示词里也没有
  任何协助者的选择。只有她在协助者名单里时，才会像其他人一样在自己的历史里看到私聊。

### 3.3 观众侧

新增四个公开观众事件（`audience_projector._EVENTS` → `api/schemas.py` 的公开 DTO → 回放分支 →
前端 `store/types.ts` / `labels.ts` / `eventCoverage`）：

- `AWAKENED_WITCH_ASSIST_PICK` → `awakened_witch_assist_pick`（目标 + 协助者名单）
- `AWAKENED_WITCH_ASSIST_CHAT` → `awakened_witch_assist_chat`（座位 / 目标 / 文本）
- `AWAKENED_WITCH_ASSIST_VOTE` → `awakened_witch_assist_vote`（座位 / 目标 / `agree|veto|pass` / 理由）
- `AWAKENED_WITCH_POISON_RESULT` → `awakened_witch_poison_result`（`passed|vetoed|incomplete|aborted`）

`WITCH_REASONING` 的 `action_type` 额外接受 `assist`；`WOLF_KING_REASONING` 额外接受
`sweep` / `pass_claw` / `explode`。两条思考事件都走既有的「推理事件五处同步」，没有新增事件名。

## 四、预设、目录与前端

- `catalog.py`：`ROLE_METADATA` 新增两条（`wolf-killer-awakened-wolf-king`、`wolf-killer-awakened-witch`，
  各带非空 `rules`）；`STANDARD_PRESETS` 新增 `twelve-player-awakened-witch`（`enable_sheriff=true`、
  `witch_self_save="never"`、`recommended_timeout_seconds=7200`、`victory` 缺省即屠边）。
- 计数：**42 角色 / 25 预设**（原 40 / 24）；`test_catalog_routes.py` 的角色、预设、端点数量断言同步。
- 前端：`roleMeta.ts` 两个新角色行、`labels.ts` / `store/types.ts` 的新事件与结果值；
  `theme/tokens.ts` 的 `ROLE_ART_BY_ID` **沿用母角色立绘**（`role-wolf-king` / `role-witch`），
  没有新增立绘素材，`theme.test` 的「一角色一图」把它们列为已知例外。
- 规则页（`/rules`）直接读 `catalog.py` 的 `rules`，所以两张牌的玩家向说明随目录一起上线。

## 五、旧局兼容性

结论：**旧局判 `compatible`，不需要 `force=true`**（`recovery_compat.assess()` 的判据逐条核对，
并用 HEAD 的 registry 生成了一份含 6 个旧角色、两条已发出请求（`werewolf_kill` / `wolf_king_shot`）
的检查点，对当前 registry 实测：`level=compatible`、`code=None`、`reasons=('registry_changed',)`、
`needs_confirmation=False`）。

- 新增契约 / 修改既有角色的 `contracts` 与 `initial_private_data` / 加 `SET_PRIVATE_DATA` 权限
  → `registry.digest` 变；`assess()` 此时返回 `COMPATIBLE`（`registry_changed`）。
- **既有 11 个狼窝角色的 `initial_resources` 与 `schema_version` 没变**：受赠的爪只写
  `awakened_claw_gift` 私有槽（不进 `resource_setup_digest`）与 `awakened_claw_received` 标记
  （`initial_resources` 里不声明它，marker 只覆盖声明值），所以资源声明 marker 不变，
  不会触发 `ROLE_DECLARATION_DRIFT`。
- **普通狼王的 `WOLF_KING_SHOT_CONTRACT` 逐字节未改**，其 digest 不变；旧局已发出的
  `wolf_king_shot` 请求仍匹配，`CONTRACT_DIGEST_DRIFT` 不会出现，普通狼王的行为也不变。
- 新角色的两个 spec、新预设、新 `SchedulePoint` 成员只影响新局；`NIGHT_WITCH_ASSIST_*` 不在
  `_NIGHT_POINTS` 里，旧检查点的 stage 号与 `PointKey` 形状不变。
- 四个新观众事件只由新角色发出，旧回放没有这些行；`PublicReplayEvent` 只是联合类型加分支，
  旧档解析不受影响。

> 开发过程中曾短暂给 11 个既有狼窝角色补零值 `initial_resources`，那会让已建立资源的旧局判
> `ROLE_DECLARATION_DRIFT`；最终实现已改为「私有槽 + `awakened_wolf_claw_gift` 契约」，不再有
> 这项副作用。上面结论以最终实现为准。

## 六、已知简化与实现差异

1. **亲传时机**：`NIGHT_ACTION` order=56（守卫 50、摄梦人 55 之后，狼刀投票之前）。官方没有给出
   精确的法官顺序，这是按公告「守卫 → 传承 → 狼队袭击」选的近似值。
2. **最后一狼不能发动爪**：卡面与公告都没有写这一条；实现注释把它记为「官方 2023 问答」口径（LAL
   规则页也明确最后一只狼不能发动）。本作按该口径实现，且只写在新的爪契约里（普通狼王的旧行为不变）。
   这是一条**实现选择**，不是卡牌明文；决策单 §D1 原先建议不做该限制，实际与建议不同。
3. **自爆永不触发爪**：`self_explode` 不在 `_CLAW_REASONS` 里；自爆只死自己、不带走任何人。
4. **不能毒自己**：官方公告没有明说，本作按普通女巫的惯例禁止自毒（`self_poison_forbidden`）。
5. **毒药目标可以当协助者**：公告只写「选择 1 / 2 / 3 名玩家」，没有排除目标本人；本作允许，
   也允许协助者包含女巫自己。
6. **私聊只有一轮**：每名协助者至多发言一次（按座位顺序累积历史），没有多轮辩论；这足够让
   「全部同意」有一个真实的交流前提，但不是完整的多轮协商模拟。
7. **确定性计票**：tally 由引擎合成命令，不调用模型、不产生思考事件或 token 行；结果只取决于
   各协助者的 `agree` / 非 `agree`。模型产物在投票步就固定，因此续跑时 tally 可重放。
8. **「双爆吞警徽」未实现**：竞选期任一狼人自爆即吞徽（沿用既有 `_election_explode()`），没有实现
   部分赛制「连续两次自爆才吞徽」的口径；觉醒狼王与普通狼/白狼王走同一条竞选自爆路径。
9. **既有 11 个狼窝角色各多一个跳过窗口**：每次死亡都会为 `awakened_wolf_claw_gift` 开一个
   响应窗口；没有收到爪的座位由 `is_applicable` 判否并写检查点——语义无影响，journal 会多几行。
10. **蚀时狼妃 / 恶夜骑士联动没有镜像到觉醒女巫**：普通女巫的 `poison` 会读 `time_locked`
    把目标改向自己、会读 `reflect_poison` 反伤自己；觉醒女巫的毒药调配**没有**这两条处理
    （她的 `brew` 只声明了 `resource_labels`，不读 `status_labels`，也没有 reflect 分支）。
    这两张牌不在官方觉醒女巫 12 人板里；自定义板混用时会与普通女巫口径不一致。
11. **竞选期自爆不消耗 `explode`**：`_election_explode()` 走既有竞选路径，只把座位判死、吞徽、
    结束当天，不经过 `awakened_wolf_king_explode` 契约，因此 `explode` 资源仍是 1；座位已经死了，
    不会再有白天自爆机会，这个残留值只是内部数据，不影响可玩性。
12. **板型覆盖**：只有 12 人觉醒女巫板；10 人娱乐场、觉醒狼王娱乐场 / 进阶场均未做预设（2026-10-25 决定不纳入 4.5，按产品口径排除）。
13. **立绘**：两张新牌沿用母角色（狼王 / 女巫）的现有立绘，没有新增素材。
14. **双爪的「先 pass 再单爪」**：两爪在手时若先放弃 sweep，再打单爪，会白丢第二爪；这是固定
    目标数 schema 的取舍（决策单 §D4 已记录），规则上不损失。
15. **中止的调配也消耗次数**：女巫选完毒药目标后放弃选协助者 → `aborted`，`brew` 已扣；官方
    没有写「放弃」的语义，这里按「一次调配就是一次机会」实现。
16. **协助者弃权/兜底算否决**：`pass`（包括模型失败时的兜底 `pass`）写 `assist_vote=0`，计票结果
    是 `vetoed`；`incomplete` 只在协助者**完全没有产生票**（如被封印）时出现。两者都不产生毒杀。

## 七、门禁与验证

实现侧新增 / 更新的门禁：

- `backend/tests/test_awakened_wolf_king_extension.py`：亲传消耗与获赠、每名队友最多一次、双爪 sweep /
  单爪 / 零爪跳过、出局死因正反例、最后一狼、自爆不触发爪、11 个既有角色的 gift 契约与私有槽、
  `WOLF_KING_SHOT_CONTRACT` 未改。
- `backend/tests/test_awakened_witch_extension.py`：解药 / 毒药 / pass、自毒与自保策略、1/2/3 人协助者
  契约、重复协助拒绝、私聊、投票绑定目标、全员同意 / 否决 / 未完成 / 中止、真实 `Scheduler` 端到端
  跑 pick → vote → tally（**唯一协助者是普通村民**，断言毒药当夜落到目标）、预设配置。
- `backend/tests/test_awakened_engine_flow.py`：协助者提示词、私聊记录只对被点名的座位可见、`assist_votes`
  派生事实的容错、引擎对畸形 `brew_plan` 的拒绝、`night_assist:` 检查点的观众事件、tally 走合成命令
  （不产生模型请求）。
- `backend/tests/test_catalog_routes.py`：42 角色 / 25 预设、每个角色非空 `rules`、新预设的
  `witch_self_save="never"` 与警长开关。
- `backend/tests/test_audience_projector.py` 的事件全集快照，`test_game_routes.py` 的回放分支 / DTO
  默认字段，前端 `eventCoverage.test.tsx` / `labels.test.ts`。

本文档中的计数用 `builtin_registry.freeze()` + `catalog.ROLE_METADATA` / `STANDARD_PRESETS` 实测过
（42 / 42 / 25）；全量 pytest、前端 test/lint/build 由实现方在合入前运行。

## 八、相关文档

- 卡牌原文：[`docs/reference/netease-role-skills.md`](../reference/netease-role-skills.md)
- 决策单：[`docs/plans/2026-10-21-awakened-wolf-king.md`](../plans/2026-10-21-awakened-wolf-king.md)
- 上游计划：[`docs/plans/2026-10-04-netease-college-boards.md`](../plans/2026-10-04-netease-college-boards.md)
  §11.1 / §15.4 / §15.5
- 玩家向规则：[`docs/gameplay.md`](../gameplay.md)
