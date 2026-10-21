# 觉醒狼王（阶段 6.6）决策单

> 日期：2026-10-21
> 状态：**决策单 · 待拍板**（拍板后转实施计划；本文不改代码）
> 范围：`backend/app/roles/`、`catalog.py`、前端角色目录/立绘、测试与文档计数
> 上游：[网易板型扩板计划](2026-10-04-netease-college-boards.md) §11.1 / §15.4 / §15.5
> 目标：在「冻结声明 + 纯 Hook + 唯一写入口」内落地觉醒狼王，**不改核心模块**（最多动一个资源标签常量），并给出一套可复现的旧局兼容结论。

---

## 零、结论先行

推荐组合：**D1=N1 / D2=S1 / D3=A / D4=E1 / D5=推荐值 / D6=两套预设 / D7=V1 / D8=先出图再登记**。

一句话：**新增一套独立的 `awakened_wolf_claw` 契约（单爪 + 双爪两个响应契约），挂到 12 个狼窝角色上；不要改既有的 `wolf_king_shot` 契约**。这样：

- 既有局不会被判 `drift`，可**直接 `compatible` 续跑**（不需要 `force=true`）；
- 普通狼王（`wolf-killer-wolf-king`）的行为与旧档逐字节不变；
- 觉醒狼王不亲传时自己出局带走两人，亲传后队友「相当于狼王」。

代价：11 个既有狼窝角色的 spec 各加一个契约（其中 8 个还要补 `CONSUME_RESOURCE` 权限），`registry.digest` 会变；每个狼窝角色死亡时会多一个「不适用 → 跳过」的响应窗口。

---

## 一、规则原文与来源

**卡牌原文**（[docs/reference/netease-role-skills.md](../reference/netease-role-skills.md#L16-L18)，本作权威）：

> 狼人阵营，可以自曝身份，可以选中自己夜间出局。觉醒狼王拥有两次狼王爪技能，可以选择在夜晚将一次狼王爪技能亲传给任意存活狼队友。拥有狼王爪，出局后可以使用狼王爪技能，选择一名玩家，使其一并出局。

**官方公告**（2023-07-28，[网易官网](http://langrensha.163.com/news/official/20231113/26904_1119723.html)）补充了三条卡面没展开的语义：

1. 「保留了狼王的基本特征，可以参与夜间讨论，**能自爆身份，也能自刀**」；
2. 「可以选择在夜间将一次狼王爪**亲传**给任意存活队友……获得狼枪的队友就相当于『狼王』」；
3. 「如果觉醒狼王不给队友递技能，那么他在出局后也能**直接带走两个人**」。

**二级资料**（[LAL 规则页](https://werewolves.games/jue-xing-lang-wang/)，仅作对照、不作权威）：该页把爪限制为「白天符合条件出局」并在夜间传承步骤上排在守卫之后、狼刀之前；同时明确「被毒杀、夜间死亡、自爆或最后一狼出局不能发动」。**这是 LAL 口径，与本作现行普通狼王（允许 `wolf_kill`）不同**，所以只作为 D1 的备选而不是默认。

**本作口径**：以卡牌原文为准；公告用来补「自刀 / 自爆 / 递枪 / 双爪带走两人」这四件事；卡面与公告都没写的（被毒/被殉情/被决斗/最后一狼）按「保留狼王全部特征」沿用普通狼王的死因集合。

---

## 二、现状盘点（可直接复用的基建）

| 能力 | 现有实现 | 觉醒狼王怎么用 |
| --- | --- | --- |
| 爪是出局反应 | `WOLF_KING_SHOT_CONTRACT`（[roles/wolf_king.py](../../backend/app/roles/wolf_king.py#L125)）：`DAWN_REACTION`、`PLAYER_DIED`、`response_reasons={wolf_kill,exile,hunter_shot}` | 另建同名家族的独立契约，不改老契约 |
| 一次性计数 | `initial_resources` + `CONSUME_RESOURCE` + `resource_equals` 前置（同上） | `awakened_claw: 2`；传递/开枪各消耗 1 |
| 自爆 | `WEREWOLF_KING_EXPLODE_CONTRACT`（[werewolf_king.py](../../backend/app/roles/werewolf_king.py#L114)）带目标；`CRIMSON_EXPLODE`（[crimson_envoy.py](../../backend/app/roles/crimson_envoy.py)）无目标 + 日中断 | 自爆按无目标（S1），走 `DAY_INTERRUPTED` |
| 自刀 | 共享 `werewolf_kill` 允许自指；只有声明 `self_kill_forbidden` 的角色被拒（[werewolf.py](../../backend/app/roles/werewolf.py#L21)） | **不声明** `self_kill_forbidden` 即可 |
| 多目标命令 | `multi_target_actions` 要求**固定精确个数**，schema 的 `minItems==maxItems`（[pipeline.py](../../backend/app/models/pipeline.py#L476)、[action_validator.py](../../backend/app/core/action_validator.py#L145)） | 双爪用「2 目标」契约；单爪用「1 目标」契约 |
| 出局反应窗口 | 每个 `(事件, 契约, 座位)` 开一个窗口；`is_applicable` 不适用则跳过并写检查点（[scheduler.py](../../backend/app/core/scheduler.py#L272)） | 双爪契约 + 单爪契约各开一个窗口，按 `order` 先双后单 |
| 队友名单 | `context.facts["camp_members"]` = 狼窝名单（含已死、永不含隐狼；[context_projector.py](../../backend/app/core/context_projector.py#L164)） | 亲传目标校验：存活 ∩ 狼窝 ∩ 非自己 |
| 资源展示 | 提示词带 `resources`（[prompt_renderer.py](../../backend/app/agents/prompt_renderer.py#L110)） | 持有者在自己的提示词里看到 `awakened_claw` |
| 死亡原因目录 | 复用 `wolf_king_shot`；猎人 `_SHOOT_REASONS` 不含它，狼美人 `_REVENGE_REASONS` 也不含 | 不新增死因，不动「五处同步」 |

> 注：`RoleSpec.granted_contracts` / `granted_notes` 的「借出契约」机制**不能**用于本技能——注册表明确禁止借出 `react` 契约，且响应队列只读 `registry.specs`，不读 `acting_spec`。

---

## 三、旧局兼容性：本次最关键的结论

上游计划 §12 决策 3 **曾**写的是「把 `WOLF_KING_SHOT_CONTRACT` 挂到所有狼 → `registry.digest` 全变 → 旧局判 `drift`」。**这个结论不准确，计划原文已于 2026-10-21 修正**。按当前代码（[recovery_compat.py](../../backend/app/persistence/recovery_compat.py#L116)）：

`assess()` 只在四类情况下判 `drift` / `incompatible`：

1. 本局**用到过**的契约 digest 变了（`CONTRACT_DIGEST_DRIFT`）；
2. **已经建立过资源**的座位，其 `initial_resources` 声明变了（`ROLE_DECLARATION_DRIFT`）；
3. 角色的 `schema_version` 变了（`role_spec_version` → `incompatible`）；
4. 角色或契约消失（`incompatible`）。

`registry.digest` 本身变了、但以上四项没变时，返回 **`compatible`**（`registry_changed`），恢复**不需要** `force=true`。资源标记在 `assess` 里用**冻结的** `state.registry_digest` 重算（[role_runtime.py](../../backend/app/core/role_runtime.py#L128)），所以给既有角色「加契约」不会移动 marker。

### 安全 / 不安全改动对照

| 改动 | 对旧局的影响 | 结论 |
| --- | --- | --- |
| 给既有狼角色**新增**契约、给 `allowed_effects` 加 `CONSUME_RESOURCE` | `registry.digest` 变 → `compatible`；契约 digest、资源 marker、spec 版本都不变 | ✅ 安全 |
| 给既有狼角色**新增** `initial_resources`（如 `awakened_claw: 0`） | 已建立资源的旧局 marker 变 → `ROLE_DECLARATION_DRIFT`（需 `force`） | ❌ 不要做；缺失键按 0 读即可 |
| 改 `WOLF_KING_SHOT_CONTRACT.per_game_limit`（1 → 2） | 该契约 digest 变 → 用过它的普通狼王旧局判 `CONTRACT_DIGEST_DRIFT` | ❌ 不要改老契约 |
| 改既有 hook 的函数名 | `_json_value` 把 callable 序列化成函数名（[pipeline.py](../../backend/app/models/pipeline.py#L226)）→ 契约 digest 变 | ❌ 不要重命名既有 hook |
| 改既有角色的 `schema_version` | 已落座旧局 `incompatible` | ❌ 不要 bump |

**门禁建议**：给 `WOLF_KING_SHOT_CONTRACT.stable_digest()` 钉一个十六进制常量测试（当前值在实现时写入），任何对老契约的误改会立刻变红；再加一条 `assess()` 的 `compatible` 用例（构造一个「只缺新契约」的旧 registry 快照/旧 journal 形状）。

---

## 四、决策项（每项请拍板一个）

### D1 爪的发动死因

| 选项 | 口径 | 影响 |
| --- | --- | --- |
| **N1（推荐）** | 沿用普通狼王：`{wolf_kill, exile, hunter_shot}`；毒、自爆、殉情、决斗、连带都不发动 | 与「保留狼王全部特征」一致；复用现有 `_CLAW_REASONS` 语义；自爆不会连带双爪 |
| N2 | 卡面字面：任何「出局」都发动（含毒、自爆、决斗、殉情） | 强牌；自爆 + 双爪 = 一次带走 3 人；需要逐条处理 `DAY_INTERRUPTED` 后的反应窗口 |
| N3 | LAL 口径：只允许白天符合条件出局（`exile` / `hunter_shot`），夜间死亡不发动 | 与官方赛事口径对齐；但与普通狼王不一致，需要额外解释 |

建议 **N1**。另附「最后一狼」：普通狼王与本作规则没有「最后一狼不能发动」的限制；N1 下觉醒狼王同样不限制（LAL 的该限制作为已知差异记录）。

### D2 自爆的语义

| 选项 | 口径 | 影响 |
| --- | --- | --- |
| **S1（推荐）** | 白天发言窗口可自爆：自己出局、公开身份、当日立即结束、**不带走任何人** | 与「自爆身份」字面一致；与赤月使徒同一套日中断路径；与白狼王的差别就是不带人 |
| S2 | 白狼王式：自爆并带走一名玩家 | 卡面没有「带走」；与双爪叠加会过强 |
| S3 | 只翻牌不死亡（愚言家/铁好人式） | 与「自爆」的通行语义冲突 |

建议 **S1**，并**显式发 `PLAYER_REVEALED`**（自曝身份本身就该公开；普通白狼王的旧路径没有这一步，本次不复刻那个缺口）。

### D3 爪的契约承载方式（本单核心）

| 选项 | 做法 | 旧局 | 代价 |
| --- | --- | --- | --- |
| **A（推荐）** | 新建 `awakened_wolf_claw`（单爪）+ `awakened_wolf_claw_sweep`（双爪）；单爪挂到**全部 12 个狼窝角色**（含新角色），双爪只挂觉醒狼王；资源名用独立的 `awakened_claw` | **compatible（无需 force）** | 11 个既有 spec 各 +1 契约；8 个还要 +`CONSUME_RESOURCE`；每个狼窝角色死亡多一个跳过窗口 |
| B | 复用并修改 `WOLF_KING_SHOT_CONTRACT`（`per_game_limit` 1→2，再挂给所有狼） | 用过它的旧局 **drift**（需 force） | 代码少，但破坏普通狼王旧档 |
| C | 核心改响应队列：座位持有 `awakened_claw` 就开窗，不要求角色声明契约 | 取决于是否动 `window_id` 形状，风险最大 | 不动 spec，但违反「角色声明」架构 |

建议 **A**。同时明确两点：

- **只挂「狼窝成员」**（`in_wolf_pack`，共 11 个既有 + 新角色），不挂石像鬼 / 觉醒隐狼 / 蚀日侍女 / 寻香魅影——它们是隐狼，觉醒狼王看不到它们，也无法把爪「传给队友」；给它们挂契约只会制造无意义的窗口。
- 普通狼王的 `wolf_king_shot` **原样保留**；它同时拥有新的单爪契约，但资源是 0，两个窗口不会双发。

### D4 双爪的两枪怎么结算

| 选项 | 做法 | 结果 |
| --- | --- | --- |
| **E1（推荐）** | 双爪契约（2 目标，`multi_target_actions={"sweep": 2}`，只挂觉醒狼王，`order` 先于单爪）+ 单爪契约（1 目标，`shoot`） | 两爪在手：先问「带走两人」；若 pass，再问「带走一人」。一爪在手：只问单爪。零爪：都跳过 |
| E2 | 单契约 + 核心重开窗口（打出一枪后再开一个窗口） | 需要改响应队列/引擎，风险大 |
| E3 | 只允许一枪 | 与「带走两个人」冲突 |

建议 **E1**。已知体验取舍：两爪在手时若先 pass 双爪再打单爪，会只剩一爪可用（第二爪作废）；提示词要写清「想带走两人就选 sweep；只想带走一人就先 pass 双爪，再用单爪」。这是可选技能的取舍，不是规则 bug。

### D5 亲传（时机 / 范围 / 频率 / 防重复）

| 子项 | 推荐值 | 依据 |
| --- | --- | --- |
| 调度点 | `NIGHT_ACTION`，`order=56`（守卫 50、摄梦人 55 之后，狼刀投票之前） | 公告夜间顺序「守卫 → 觉醒狼王传承 → 狼队袭击」；引擎 `NIGHT_ACTION` 整体先于 `NIGHT_WOLF_VOTE` |
| 频率 | **每夜最多一次**（契约在一次 `NIGHT_ACTION` 中只发一个请求）；资源总数 2 限制整局次数 | 公告「将一次狼王爪亲传」；比「同夜连续传两次」更接近法官流程 |
| 目标 | 存活 ∩ `camp_members` ∩ 非自己；**每名队友整局最多 1 次** | 公告「任意存活队友」「每个狼队友最多获得 1 次」（上游计划） |
| 资源名 | `awakened_claw`（与普通狼王的 `wolf_claw` 隔离） | 防止普通狼王/普通狼的双契约互相触发 |
| 防重复目标 | `selected_target_fact_namespaces={"resource_labels"}`，并把 `awakened_claw` 加进 `_PUBLIC_RESOURCES`（[context_projector.py](../../backend/app/core/context_projector.py#L81)，一行常量） | 让 `validate_*` 能在模型选到「已持爪队友」时给 repair 机会，而不是靠 effect 冲突降级成 pass |
| 计数限制 | 亲传契约**不要设 `per_game_limit`**（pass 也算一次动作，设 1 会把第二夜作废） | 与白狼王自爆契约同款陷阱（[werewolf_king.py](../../backend/app/roles/werewolf_king.py#L120) 的注释） |

### D6 板型与预设

| 选项 | 内容 | 影响 |
| --- | --- | --- |
| **P1（推荐）** | 两套：`twelve-player-awakened-wolf-king`（3 狼 + 觉醒狼王 + 4 民 + 预女猎守）与 `twelve-player-awakened-wolf-king-dream`（3 狼 + 觉醒狼王 + 4 民 + 预女猎摄梦人），均开警长、女巫全程不可自救、屠边 | 预设 24 → 26；与公告「娱乐板 / 含摄梦人的进阶场」对齐 |
| P2 | 只做一套（与 LAL 12 人配置一致） | 变更最小；「进阶场」留到之后 |
| P3 | 暂不做预设，只做角色，供自定义 | 角色无法在创建向导里一键开 |

建议 **P1**，但注意：网易官方「娱乐场 / 进阶场」的完整阵容公告没有给全，P1 的两套是按「3+1 狼 / 4 民 / 四神」推的；如你有官方截图，以截图为准。觉醒女巫板（含觉醒狼王）要等 6.5 觉醒女巫一起做，不在本单范围。

### D7 观众可见性 / 事件

| 选项 | 做法 | 代价 |
| --- | --- | --- |
| **V1（推荐）** | 复用现有事件：双爪发两条 `WOLF_KING_SHOT`（每条一个目标）+ 一条 `WOLF_KING_REASONING`（`target_seat` 可空，reasoning 写清两人）；亲传也复用 `WOLF_KING_REASONING`（`target_seat=队友`） | 零 DTO / 前端事件覆盖改动；缺点：投影器会把 `action_type` 覆写成 `wolf_king_reasoning`（[audience_projector.py](../../backend/app/services/audience_projector.py#L221-L226)），观众看到的是「狼王思考 + 目标」，亲传语义靠 reasoning 文本 |
| V2 | 新增 `AWAKENED_WOLF_KING_REASONING` / `AWAKENED_CLAW_PASSED` | 表达清楚；要动投影器白名单、`api/schemas.py`、回放分支、前端 eventCoverage 与 labels（五处同步） |

建议 **V1** 先落地；如果实跑发现观众读不懂亲传，再升级 V2（不影响规则/档）。

### D8 立绘

| 选项 | 做法 | 影响 |
| --- | --- | --- |
| **G1（推荐）** | 按 40 张同一套模板生成 768×1024 自制图，补 `role-awakened-wolf-king.webp` 并登记 | 角色目录 41/41，规则页/座位卡统一 |
| G2 | 先不登记，回退卡背 | `theme.test.ts` 的「登记角色都必须有图」不会红（只检查已登记项），但视觉上缺一张 |

建议 **G1**；图未就绪时先按 G2 合并也可以（不登记即可），不要把没有文件的 id 写进 `ROLE_ART_BY_ID`。

### D9 文案 / 计数 / 门禁

- 角色 40 → **41**；预设 24 → **26**（若 P1）。
- `catalog.py` 加 `ROLE_METADATA`（`name_zh`、`icon`、`description`、非空 `rules`）；`test_catalog_routes.py` 的 40/24 断言改 41/26。
- 前端：`roleMeta.ts` 的 `ROLE_ROWS` 加一行（配色键建议 `werewolf_king`，label「觉醒狼王」）；如 G1 再改 `ROLE_ART_BY_ID`。
- 文档：`README.md` 角色清单、`docs/gameplay.md` 角色表与板子数、`AGENTS.md` / `CLAUDE.md` 角色清单与「40 角色 24 预设」、`docs/architecture.md` 的目录说明、大计划 §11.1/§15.5 状态、立绘盘点笔记的 40/40 说明、`docs/README.md` 索引。
- 死因**不新增**（复用 `wolf_king_shot`），所以取值目录五处同步不用动。

---

## 五、推荐方案的精确规则口径（拍板后照此实现）

| 场景 | 行为 |
| --- | --- |
| 夜间亲传 | 觉醒狼王每夜可传一次，最多两次；目标为存活狼窝队友且未持爪；消耗自己 1 爪，目标得 1 爪 |
| 不亲传 | 自己出局时双爪都在：一次问「带走两人」（sweep），可 pass 后改打单爪 |
| 一爪在手 | 出局时单爪提示，带走一名存活玩家或 pass |
| 零爪 | 不提示（窗口跳过） |
| 出局死因（N1） | 夜间被狼刀、被放逐、被猎人带走 → 可发动；被毒/自爆/殉情/决斗/连带 → 不发动 |
| 自爆（S1） | 白天每位发言者开口前可自爆一次：公开身份、自己出局、当日结束、不带人；不触发爪 |
| 自刀 | 允许（不声明 `self_kill_forbidden`）；仍然走狼队共享票，不是单方面自尽 |
| 被爪带走者 | 死因 `wolf_king_shot`；猎人不能开枪、狼美人不殉情、被带走者不发动自己的爪 |
| 队友持爪出局 | 同 N1 死因集合，单爪提示（每名队友只有 1 爪） |
| 最后一狼 | 不额外限制（与普通狼王一致）；LAL 口径不同，记录为已知差异 |

---

## 六、实现骨架（拍板后）

| 文件 | 改动 |
| --- | --- |
| `backend/app/roles/wolf_claw.py`（新） | 共享契约与 hooks：`AWAKENED_CLAW_RESOURCE`、`AWAKENED_WOLF_CLAW_CONTRACT`（单爪，`DAWN_REACTION` order=42）、`AWAKENED_WOLF_CLAW_SWEEP_CONTRACT`（双爪，order=41）、`*_applicable` / `validate_*` / `resolve_*`；复用 `wolf_king_shot` 死因与 `WOLF_KING_SHOT`/`WOLF_KING_REASONING` 事件 |
| `backend/app/roles/awakened_wolf_king.py`（新） | `AWAKENED_WOLF_KING_SPEC`：`camp_id="werewolf"`，`contracts=(WEREWOLF_KILL_CONTRACT, 亲传契约, 自爆契约, 双爪契约, 单爪契约)`，`initial_resources={"awakened_claw": 2, "explode": 1}`，`allowed_effects` 含 `CONSUME_RESOURCE / SET_RESOURCE / SUBMIT_DAMAGE / EMIT_EVENT / SET_PRIVATE_DATA`，`max_count=1`，完整 `instructions` |
| 11 个狼窝角色文件 | 导入并追加 `AWAKENED_WOLF_CLAW_CONTRACT`；8 个补 `EffectKind.CONSUME_RESOURCE`（`werewolf` / `awakened_wolf_beauty` / `evil_knight` / `mask` / `nightmare` / `time_wolf` / `wolf_beauty` / `wolf_witch`） |
| `backend/app/catalog.py` | `ROLE_METADATA` 新条目 + `rules`；两套预设（P1） |
| 前端 | `roleMeta.ts` 新行；`ROLE_ART_BY_ID`（G1）；不需要新页面 |
| 测试 | `tests/test_awakened_wolf_king_extension.py`：亲传/双爪/单爪/自爆/死因集合/自刀允许/队友持爪/最后一狼；`test_catalog_routes.py` 计数；`test_recovery_compat` 的 compatible 用例；老契约 digest 钉值 |
| 文档 | 见 D9 |

**多爪窗口的顺序**：`AWAKENED_WOLF_CLAW_SWEEP_CONTRACT.order = 41`，单爪 `42`；普通狼王的 `wolf_king_shot` 仍是 40。响应队列按 `(order, role_id, contract_id, actor)` 排序，所以双爪先问、单爪后问。

**提示词**（`instructions` 必须写清，否则模型会误用）：

> 你是觉醒狼王……每晚可把一次狼王爪传给一名存活狼窝队友（每名队友最多一次，整局共两次）。你不传时，出局可以带走两人；已传出一爪时只能带走一人；被毒/自爆/殉情/决斗带走时不能发动。白天发言前可自爆：公开身份、自己出局、当日结束、不带人。

---

## 七、验收门禁

1. 后端全量 `pytest tests app --cov=app --cov-branch --cov-fail-under=100` 100%；前端 `npm test` / `lint` / `build`。
2. 新角色扩展测试（≥12 例）覆盖：两爪 sweep / 一爪单发 / 零爪跳过 / 亲传消耗与获赠 / 每名队友最多一次 / 亲传后队友出局单发 / 自爆日中断且不触发爪 / N1 死因正反例 / 自刀不被拒 / 最后一狼可发动 / 观看事件两条 shot。
3. `test_catalog_routes.py` 41 角色 / 26 预设，新角色 `rules` 非空。
4. `test_recovery_compat`：只新增契约时旧局 `compatible`；`WOLF_KING_SHOT_CONTRACT.stable_digest()` 钉值不变。
5. `test_values.py` / 回放 DTO / 前端 eventCoverage 不变（复用死因与事件，V1 前提下应零改动）。
6. 文档计数与角色清单同步（D9）。

---

## 八、风险与已知简化

1. **跳过窗口的开销**：每个狼窝角色死亡都会为单爪契约开一个不适用窗口（写检查点）；这是 D3-A 的代价，不影响语义，但会让 journal 多几行。若不能接受，改走 D3-C（核心池），风险更大。
2. **获赠者是否知道**：`resources` 会显示 `awakened_claw: 1`，但普通狼的 `instructions` 不知道这是什么。建议先在提示词里靠资源名与契约动作名承载；实跑若发现获赠者总是 pass，再加 `RoleSpec.private_facts` 的获赠说明（会多一个私有键，不影响旧档）。
3. **双爪只能选 2 或（先 pass 再）1**：无法在一次提示里选「正好 1 人但保留另一爪」；这是固定目标数 schema 的限制，规则上不损失（第二爪在死亡后本来也只能用一次）。
4. **LAL 口径差异**：LAL 不允许夜间死亡/自爆发动、且最后一狼不能发动；本作按网易卡面 + 普通狼王口径，差异已记录。
5. **立绘**：G1 需要新图；G2 期间角色目录仍显示卡背。

---

## 九、需要你拍板的清单

- [ ] D1：N1 / N2 / N3
- [ ] D2：S1 / S2 / S3（是否显式 `PLAYER_REVEALED`）
- [ ] D3：A / B / C（是否接受 11 个既有 spec 变更与跳过窗口）
- [ ] D4：E1 / E2 / E3
- [ ] D5：接受推荐值（order 56、每夜一次、狼窝限定、`awakened_claw`、`resource_labels` 校验）
- [ ] D6：P1 / P2 / P3，以及两套阵容是否按推荐
- [ ] D7：V1 / V2
- [ ] D8：G1 / G2
- [ ] D9：计数按 41 / 26

拍板后我会把本文状态改为「已批准」，补实施计划与代码提交。

---

## 十、来源

- 卡牌原文：[`docs/reference/netease-role-skills.md`](../reference/netease-role-skills.md#L16-L18)
- 官方公告（2023-07-28）：<http://langrensha.163.com/news/official/20231113/26904_1119723.html>
- LAL 规则页（对照）：<https://werewolves.games/jue-xing-lang-wang/>
- 上游计划：[网易板型扩板计划](2026-10-04-netease-college-boards.md) §11.1 / §12 / §15.4 / §15.5
