# 系统架构

本文面向想理解或修改代码的开发者，描述 Wolf Killer 的模块划分与核心设计。AI 编码助手使用的同类说明见根目录 `AGENTS.md` 与 `CLAUDE.md`，三处描述同一套架构；修改架构后请同步更新。

## 总览

```
┌─────────────┐   REST/WS    ┌──────────────────────────────────────────┐
│   前端       │ ◄──────────► │  FastAPI (backend/app/main.py)           │
│  React+MUI  │              │  ProcessLock · GameRepository            │
└─────────────┘              │  EventBus · WSManager · MemoryService    │
                             │  GameService · BenchmarkService          │
                             └──────────────┬───────────────────────────┘
                                            │
                             ┌──────────────▼───────────────────────────┐
                             │  GameEngine（asyncio 驱动完整游戏循环）      │
                             │  夜晚 → 通用角色流水线                      │
                             │  白天 → 引擎内角色无关生命周期（发言/投票）    │
                             └──────┬──────────────┬────────────────────┘
                                    │              │
                          ┌─────────▼─────┐  ┌─────▼──────────────┐
                          │ LLM 客户端     │  │ 耐久存储             │
                          │ agents/       │  │ SQLite/WAL + 兼容日志 │
                          └───────────────┘  └────────────────────┘
```

## 目录结构

```
WolfKiller/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口：单例 + 路由挂载 + /ws/game/{id}
│   │   ├── config.py            # 环境变量配置
│   │   ├── catalog.py           # 角色目录、标准预设、人数约束
│   │   ├── models/              # 游戏数据模型 + 冻结流水线核心类型（pipeline.py）
│   │   ├── core/                # 引擎、调度器、效果应用、投影、校验、解析、事件总线、日志
│   │   ├── agents/              # LLM 客户端、providers、提示渲染、输出解析、状态过滤
│   │   ├── roles/               # 角色声明式 spec + 纯 Hook（狼人/女巫/预言家/猎人/平民/守卫/白痴/白狼王/骑士/狼美人/老酒鬼）
│   │   ├── api/                 # REST 路由（routes/）+ WebSocket 处理（websocket/）
│   │   ├── persistence/         # SQLite repository、检查点编解码、恢复兼容性判定、引擎恢复
│   │   ├── services/            # 游戏/benchmark 服务、公开投影、派生任务
│   │   └── stores/              # 模型配置持久化 + API Key 加密存储 + 应用级设置（settings.json）
│   └── tests/                   # pytest 测试（statement/branch 100% 覆盖门禁）
├── frontend/
│   └── src/
│       ├── api/                 # REST 客户端 + WebSocket hook
│       ├── store/               # Zustand 状态管理 + 时间轴回放引擎 + 模型配置 store + 应用设置 store
│       ├── labels.ts            # 取值→文案唯一来源（20 个命名空间，键类型为联合）
│       ├── theme/               # 设计令牌（tokens.ts 为色值唯一来源）
│       └── components/
│           ├── lobby/           # 大厅（普通对局列表、扁平文件夹、批量操作）
│           ├── benchmarks/      # 评测任务列表、新建与详情（含评测对局表）
│           ├── create/          # 创建游戏向导（人数身份 → 模型配置）
│           ├── game/            # 游戏（座位图、时间轴、历史面板、胜利画面）
│           ├── models/          # 模型配置页与编辑对话框
│           ├── settings/        # 应用设置页（训练语料采集开关，路由 /settings）
│           └── shared/          # 共享组件（头像、角色图标、发言气泡）
│       └── e2e/                 # Playwright 浏览器验收
└── docs/                        # 本文档库
```

## 通用角色流水线（夜晚核心）

角色规则以**冻结声明 + 纯 Hook + 类型化 Effect + 唯一原子写入口**表达，新增角色无需改动任何核心模块（守卫样例 `roles/guard.py` 是验收证明）：

| 模块 | 唯一职责 |
| --- | --- |
| `models/pipeline.py` | 冻结核心值类型、Effect 代数、ActionContext/ActionContract/RoleSpec |
| `roles/registry.py` | 角色/契约发现、静态验证、`freeze()` 不可变注册快照 |
| `core/context_projector.py` | 从 GameState 按可见性标签（PUBLIC/ACTOR/CAMP）生成最小冻结 ActionContext；`project_view()` 提供无契约观测投影 |
| `core/action_validator.py` | 纯校验：Context/Contract/Command → RuleViolation，无任何状态读写 |
| `core/action_resolver.py` | 调用纯 Hook（resolve/aggregate/react），产出确定性 GameEffect 批次（内置 ACCEPT_ACTION） |
| `core/effect_applier.py` | **唯一的写入口**：整批校验、CAS（revision 比较）、原子应用、幂等结果与审计事件 |
| `core/scheduler.py` | 调度点（NIGHT_ACTION / NIGHT_WOLF_VOTE / NIGHT_WITCH_ACTION / NIGHT_SEER_ACTION / NIGHT_COMMIT / DAWN_REACTION / DAY_ACTION / POST_SPEECH_ACTION / EXILE_VERDICT 等）、稳定排序、请求收集、响应窗口队列与阶段门禁；`run_point(..., slot=)` 让同一阶段内多次运行同一调度点（`PointKey.phase` 为 `phase#slot`，请求 token 也混入 slot）；聚合按 `contract_id` 分组，跨角色共享的完全一致契约合并计票；`point_journal.py` 提供断点续跑检查点，journal 键与请求 token 都用**对局冻结的** `registry_digest` 盖章（`core/registry_identity.py`），所以新增/修改角色不会让已完成的调度点被重放、也不会重复发起同一次模型请求 |
| `core/night_settlement.py` | 夜晚结算：pending damage/protection → 死亡批次；保护按来源分三档（`_WOLF_ONLY_SOURCES` / `_DOUBLE_SAVE_SOURCES` / `_ANY_DAMAGE_SOURCES`，`dance_veil` 只挡狼刀且不参与对穿）；按死因豁免的 `night_immune` 与 `poison_immune`、以及延到次日的 `delayable` / `hunt`；假面舞会的舞池裁决 `settle_pool()`（少数方出局，面具只在本次比较中折算阵营）。`PROTECTION_SOURCES` 是写入口共用的唯一来源清单 |
| `agents/prompt_renderer.py` | 仅从 RoleSpec/Contract/Context 渲染通用 Prompt（历史以 Base64 不可执行注入） |
| `roles/{werewolf,witch,seer,hunter,villager,guard,idiot,werewolf_king,wolf_king,evil_knight,knight,wolf_beauty,old_drunkard,dream_weaver,gargoyle,gravekeeper,pure_white,wolf_witch,demon_hunter,crimson_envoy,nightmare,dancer,mask,hidden_wolf}.py` | 内置角色：声明式 spec + 纯 Hook（`*_applicable` / `validate_*` / `resolve_*` / `react_*`）；白狼王与狼美人复用狼人导出的 `WEREWOLF_KILL_CONTRACT`，白痴与狼美人殉情是仅 `react` 的无 LLM 契约，老酒鬼与守墓人只有声明式标记无契约（守墓人的 `last_exiled` 由投影器从死亡记录推导）；`hidden_wolf.py` 是隐狼共用的独狼刀契约（狼窝全灭才开）；狼王复用猎人的死亡响应模式，恶夜骑士的免疫与反伤分别走 `night_settlement` 的 `night_immune` 与 `core/reflect.py` 的标记协议，摄梦人的梦盾走 `night_settlement` 的 any-damage 分档；纯白之女与狼巫的「具体身份」查验结果记进 `role_checks` 私有事实命名空间（投影器回读给本人提示词），验中即杀的 `verify_kill` 走普通伤害批次；猎魔人的狩猎是 `hunt` 死因的**无条件延迟**档；赤月使徒的自爆发 `DAY_INTERRUPTED` 并给全部存活好人落 `silenced`，最后一个狼被放逐时靠 `EXILE_VERDICT` 上的 pure react 发 `EXILE_CANCELLED`（与白痴同款通用窗口）；噩梦之影的恐惧是同名 `silenced` 状态，狼窝被封印时由引擎跳过讨论与逐票；舞者用 `multi_target_actions={"pool": 3}` 一次提交三个目标并由结算判定，假面用 `selected_target.status_labels` 读 `in_pool` |

辅助模块：

- `core/role_pipeline.py` — 流水线运行器（`RolePipeline.run_point`），引擎按调度点调用
- `core/night_flow.py` — `NightDirector`：狼队夜间讨论/投票与旁白；`build_briefing` 等 Prompt/schema 构造。女巫/预言家思考已交给角色流水线，Director 不再提供 `witch_think` / `seer_think`
- `core/sheriff_flow.py` — `SheriffDirector`：对局级警长职位（竞选、1.5 票、发言方向、交徽/撕徽）。不是角色，不进注册表。交/撕徽提示词与其它警长提示词一样带 `_context_block`（身份 + 私有事实）；模型给出的 `reasoning` 由 `badge_reasoning()` 取出，经引擎一次性槽位 `_pending_badge_reasoning` 交给 `sheriff_badge:` 步骤（与 `_pending_narration` 同构，**不进**编排编解码器，label 形状不变）。
- `core/vote_service.py` — 可信投票域服务：原子、幂等的终局选票收据（accepted / voluntary-abstain / technical-abstain）
- `core/conversation_log.py` — 全部对话记录及按角色过滤的视图
- `core/state_transaction.py` — 状态事务与 revision 管理

### 夜晚流程

`GameEngine._execute_night()` 走分阶段 `_execute_staged_night()`，调度点顺序固定为：

`NIGHT_ACTION` → 狼队讨论/投票 → `NIGHT_WOLF_VOTE` → `NIGHT_SEER_ACTION` → `NIGHT_WITCH_ACTION` → `NIGHT_COMMIT`

随后 `_resume_pipeline_night()` 以分阶段检查点发布死亡、判定胜负、推进阶段；`enable_sheriff` 开局时发 `SHERIFF_ELECTION_START`，关局仍 `NIGHT_ACTIONS_COMPLETE → DAWN`。所有阶段点均持久化检查点，失败后精确续跑不重放。夜间开眼旁白是**声明式**的：每个在 `NIGHT_ACTION` 行动的角色自带一句 `RoleSpec.opening_narration`，引擎在 `NIGHT_ACTION` 跑完之后、它的 `night_point:` 提交之前，按 `PointResult.requests` 逐个播报 —— 因此「有请求才播」天然保证死掉的座位与尚未解锁的技能（舞者/假面/猎魔人从第二夜起）不会被叫到，新增角色也不必改引擎；导演表 `_NARRATIONS` 只保留引擎自己拥有的节拍（狼人开眼、封印旁白）。单个调度点内部按 `contract.order` **串行**执行，这是若干规则的实现基础：`NIGHT_ACTION` 里噩梦之影（10）先于守卫（50），所以被恐惧的守卫在同一次运行里就已经拿不到命令；舞者（20）先于假面（30），所以假面能读到当晚舞池名单。调度器在**执行期**（而非发放期）逐成员跳过带 `silenced` 的座位，并往 `stage_telemetry` 落 `actor_silenced`；狼窝里任一存活狼被封印时由引擎跳过讨论、`record_votes(())`，stage 5 照跑、全员降级 `pass`，因此不会产生任何狼刀伤害。`NIGHT_COMMIT` 的结算是 `EffectApplier.commit_night()`：同一原子批次里跑舞池裁决、合并伤害、清扫 `in_pool`/`masked`/`silenced` 这些「只活一夜」的状态；白天路径仍走 `settle_pending()` 且**不清扫**（赤月使徒的封印在白天落下，必须活到它生效的那一夜）。狼队讨论与逐票由 `NightDirector`（`core/night_flow.py`）驱动，不走角色 Hook；**夜晚是全应用唯一曾经零重试的模型路径**（`attempt` 直方图长期是 `{1: N, 2: 0}`，一次连接重置就直接丢掉一次狼刀或整轮讨论），现在由 `GameService._night_turn()` 包一层**分类重试梯子**（3 次 + 退避，`_NIGHT_RETRY_POLICY`）：transport 故障原样重发，不可用载荷（空 / 截断 / 坏 JSON）改用 repair 提示重问——旧实现里后者**一次都不重试**，一问不出来就直接丢掉这个座位的整晚行动。重试放在 `provider_call()` **内部**，所以持久账本仍只看到一次逻辑尝试——否则第一次失败会把 `model_requests` 标成 `failed`，而 `ModelInvocationService` 拒绝重新派发 failed 请求，那一夜就再也问不出来了；讨论预算为每狼 6 次、至少完整两圈（刀口共识也要等两圈后才能提前结束），发言与次日计划各 ≤400 字，**狼窝名单（讨论/投票/频道/队友名单）按 `core/wolf_pack.in_wolf_pack` 判定 = `camp == Camp.WEREWOLF` 减去声明 `no_wolf_pack` 的隐狼**（判胜与共享狼刀仍按阵营），白狼王复用狼人导出的 `WEREWOLF_KILL_CONTRACT` 共享聚合。警长流程由 `SheriffDirector`（`core/sheriff_flow.py`）硬编码，Agent 只选当前步骤绑定的工具；竞选、退水与警长投票提示注入已上警座位、当前候选人与未上警（警下）选民名单；狼玩家的竞选、退水与警长投票提示会额外注入狼队成员名单与本夜狼队频道记录（含次日计划），好人视角永不注入。警上/警下名单同时进入竞选发言、白天发言、放逐投票与超时重试投票的权威状态（警上=`office.candidates`，警下=`off_badge_seats`：开选时存活且从未上警，排除开选前死亡，`office.candidates` 整局保留），竞选期再由 `PromptBuilder._format_badge_speaking_progress` 给出竞选发言顺序与已发言/尚未发言名单，避免模型把警下玩家误说成警上。

### 白天流水线窗口

白天生命周期为角色开放三个与角色无关的窗口，引擎只认 `DAY_INTERRUPTED / EXILE_PENDING / EXILE_CANCELLED / PLAYER_REVEALED` 这些通用事件，不出现任何角色名：

- **`DAY_ACTION`**（每位发言者开口前，`slot=f"{vote_round}:{seat}"`）：角色可提交行动；若提交事件含 `DAY_INTERRUPTED`，引擎 `_resolve_day_interruption()` 结算 pending damage、按 `death_history` 去重发布死亡、以这些 PLAYER_DIED 跑 `DAWN_REACTION`、写 `day_interrupted:{round}:{seat}` 检查点，随后判胜负或以 `WEREWOLF_EXPLODED` 转入 NIGHT。续跑时 `_journaled_day_interruption()` 从 journal 识别已发生的中断并幂等重放。白狼王自爆与赤月使徒自爆是当前的两个实现（后者不带乘客，只把全部存活好人落 `silenced`）。

- **`POST_SPEECH_ACTION`**（全体存活玩家发言完毕、放逐投票前，`slot="post_speech"`）：与 `DAY_ACTION` 同构的第二个白天窗口，只在有角色声明该调度点时运行（`_post_speech_action_enabled()`）。骑士翻牌决斗是当前唯一实现：是狼人则提交 `DAY_INTERRUPTED`（`cause="knight_duel"`）走同一条中断路径，被裁决者在转 NIGHT 前经 `give_last_words(..., daytime=True)` 发表遗言（骑士本人同为该 cause 但不走这条路，因此不会被补发遗言）；是好人则只提交自身伤害。窗口跑完引擎先 `_settle_and_publish()`（白天伤害立即生效、绝不带进夜里），再 `_resolve_delayed_deaths()`，最后才 `SPEECHES_COMPLETE`。`_journaled_day_interruption()` 按 slot 后缀匹配两个窗口的检查点，因此续跑时即使阶段已推进到 NIGHT 也能幂等重放。
- **`EXILE_VERDICT`**（放逐前）：`_apply_exile(seat)` 先以 `EXILE_PENDING{target_seat, cause="exile"}` 跑该点；若提交事件含 `EXILE_CANCELLED`，则对随行 `PLAYER_REVEALED` 写 `revealed_role`、系统消息播报翻牌、`add_vote_result(..., cancelled_seat)` 记为无人出局并跳过遗言；否则走原 `mark_dead("exile")` 路径。白痴翻牌与赤月使徒的「最后一个狼被放逐后延到次日」是当前的两个实现。延迟死亡落地后引擎**立即判胜**（`_execute_speech_round` 末尾），不再为残局多走一轮放逐投票。

### 放逐反应

引擎放逐玩家后，将合成的 PLAYER_DIED 提交注入 `DAWN_REACTION` 调度点的响应队列，让猎人等响应契约通过流水线反应。猎人的 `_SHOOT_REASONS` 含 `self_explode`，被白狼王带走时同样可开枪。

## 白天生命周期

- 白天发言、投票、平票复投、遗言是引擎内与角色无关的行为，经 `BaseRole`（`roles/base.py`）调用 LLM：发言与投票都走**分类重试梯子**，全部失败才兜底（发言给模板、投票给显式技术弃票），并按并发上限（`VOTE_CONCURRENCY`，默认 5）执行。**投票**是三级传输梯子（strict tool → JSON 压缩上下文 → JSON 强格式末次重试，`LLM_ACTION_FINAL_RETRY_TIMEOUT_SECONDS`）；**发言**是同一条 `retry_plan` 分类的 4 次梯子（`_SPEECH_RETRY_POLICY`）——旧实现只给两次，且第二次是无条件「加强格式」提示：那适合「没有调用工具」，但对一条断掉的连接毫无用处，而空/过短/用错函数的答复**连第二次机会都没有**、直接落到模板（真实统计：一局 275 条发言里 268 条是模板文本）。现在 transport 失败原样重发、不可用答复带 `_SPEECH_REPAIR_REMINDER` 重问，超时类失败不等退避（`_IMMEDIATE_RETRY_ERRORS`：模型只是慢，等待只会吃掉这个回合——与投票梯子的取舍一致）。**瞬时故障与能力拒绝走不同分支**：能力拒绝与超时切到更小的 JSON 通道，瞬时故障（连接 / 429 / 5xx）则**保持原传输**，先按 `agents/retry.py` 的指数退避（含 `Retry-After`，封顶 30s）等待再问——传输从来不是连接重置的原因，而 SDK 早已在失败的那次调用里耗尽了自己的重试，立即重发只会落进同一个故障窗口。**末次重试的预算不再是三者中最小的**：`_action_timeout_seconds()` 以第一次尝试的预算为地板（`action_timeout_seconds`），后续尝试只能更宽、不能更窄——此前末次只有 30s，而一个慢到需要重试的 provider 恰恰最容易被这个更小的本地 deadline 砍掉，「再问一次」于是变成了「更早放弃」。`give_last_words()` 在调用 LLM **之前**先写 `last_words_pending:{round}:{seat}:{cause}` 检查点并认领该死者，因此发言中途崩溃续跑时最坏是**丢掉这条遗言**，而不是把它重放成第二条（时间线重复比缺失更难排查）
- 投票通过纯校验器验证，以 `EffectApplier` 的 ACCEPT_ACTION 记录（唯一写入口）；阶段超时的缺票席位统一转换为显式技术弃票后再结算
- 投票资格读 `runtime.statuses`（`core/vote_service.py`）：`no_vote` 座位不进选民，`exile_immune` 座位不进候选；`BaseRole` 投票候选取窗口的 `eligible_targets`，提示词列出免于放逐的座位
- 状态机（`core/state_machine.py`）以 `(current_phase, event, next_phase)` 三元组表驱动：

```
WAITING → ROLE_DEAL → NIGHT → [SHERIFF_ELECTION] → DAWN → LAST_WORDS → SPEECH → VOTE_CASTING → VOTE_RESOLUTION
                                                     │ WEREWOLF_EXPLODED  ↑              ↓
                                                     └──────────→ NIGHT ←──────────────┘
```

- 胜负判定（`core/rule_engine.py`）实现屠边规则与「狼刀在先」语义：神职含守卫、白痴、骑士，老酒鬼是平民；狼人数（含白狼王、狼美人）大于好人数也算狼人胜。归属读 `RoleSpec.tags`（`god`/`villager`），不再按角色名子串匹配——子串匹配会漏掉骑士，导致「骑士是最后一名存活神职」时提前判狼胜

## LLM 交互层

- `agents/llm_client.py` + `agents/providers/` — 提供方抽象，按 provider profile 决定传输协议、strict tool 端点、超时与重试策略：
  - `registry.py`：显式 `provider_profile` 或按 Base URL 域名（`api.openai.com / api.deepseek.com / openrouter.ai / api.anthropic.com / opencode.ai`）解析 `ProviderProfile`；未识别域名回退 `custom-openai`，Anthropic 兼容中转站需显式选 `custom-anthropic`。OpenCode 的 Zen 与 Go 共用 `opencode.ai` 域名，只能按路径前缀区分（`/zen/go` 必须先于 `/zen` 匹配；未知路径回落 Zen）
  - **Responses 方言**：Base URL 路径以 `/responses` 结尾时，`_apply_endpoint_dialect()` 用 `replace()` 把 profile 切成 `openai_responses` 并关掉 strict —— 只换方言与 strict，provider 自己的 headers/能力全部保留，所以把官网模型表里的 URL 原样粘进来既走对了协议又不会丢 Zen 的会话头。这条覆盖是必需的：Zen 的部分模型（如 `muse-spark-1.3-contributor-free`，其目录项为 `provider.npm = "@ai-sdk/openai"`）只在 Responses API 上提供，同一域名打 `/chat/completions` 返回 500
  - `transports.py`：按 `api_mode` 选择传输——`openai_compatible.py`（`ChatOpenAI`，Chat Completions）、`openai_responses.py`（同一 builder 的子类，只多传 `use_responses_api=True`；交给 SDK 前用 `strip_responses_suffix()` 去掉尾部 `/responses`）或 `anthropic_messages.py`（`ChatAnthropic`，Messages API；去掉 Base URL 尾部 `/v1`、temperature 截断到 0~1、无 strict endpoint）
  - `base.py`：`CallPurpose`、`ProviderProfile`、`call_budget()`（两种传输共用的 token/超时预算）。`NIGHT_JSON` 是唯一**不带重试余量**的行动 purpose：夜晚跑在 `asyncio.to_thread` worker 上、外面没有 `asyncio.wait_for`，所以它的传输超时本身就是那个 deadline
  - `agents/retry.py`：全应用共用的退避策略（`RetryPolicy` / `retry_delay` / `delay_for` / `retry_plan` / `call_with_retry` / `acall_with_retry`）。**失败分两类、重试方式不同**：`retry_on`（= `RETRYABLE_PROVIDER_ERRORS`：429 / 5xx / 连接 / provider 超时）表示「根本没拿到答复」，按指数退避**原样重发**；`repair_on`（= `RETRYABLE_OUTPUT_ERRORS`：`UnusableModelOutput` 与 pydantic `ValidationError`）表示「答复拿到了但用不了」（空内容、截断、坏 JSON、schema 不匹配），同样计入重试预算但**改用 repair 提示重问**。`retry_plan()` 返回 `(kind, delay)`，kind 为 `transport` 或 `repair`；两类都命中按 transport 处理——重发不会让被拒的载荷更糟，而修复一条断掉的连接毫无意义。`call_with_retry` / `acall_with_retry` 把 kind 交给被调方（首次恒为 `transport`），调用点因此能在 repair 时收紧请求而不是复读一遍。`retry_unknown_errors` 默认关（确定性 bug 只报一次），但**会白丢一个玩家回合**的调用点（发言、夜间 turn、夜间动作）打开它：仅仅是没分类成功的异常（网关自定义包装之类）应该换一次重发，而不是让该座位整轮作废。**容量拒绝单独一条规则**：`is_refused_error()`（HTTP 503）不走普通梯子——它是分钟级的容量决定（额度、上游账号池为空），半秒到两秒的退避既等不到恢复也保不住这一回合，所以给**一次长等待**（`refused_delay_seconds`，默认 20s；有 `Retry-After` 时取其值与地板的大者，并封顶 `max_retry_after_seconds`）后重发一次，**第二次拒绝即终局**。429 刻意不在此列：它带 `Retry-After` 提示且重试确实能成功。能力拒绝（400/422）永不重试——那是请求形状的问题，再来一次也修不好，该换的是传输。`sleep` 与 `rng` 可注入，所以测试确定且不真的等待；`acall_with_retry` 不吞 `CancelledError`，投票取消与 deadline 必须照常传播。**同步变体只能在 worker 线程用**：`roles/base.py` 的投票/发言在事件循环上，必须用 `asyncio.sleep`
  - **自定义请求头**：`ProviderProfile.default_headers` / `session_header` + `ModelConfig.headers` → `merge_request_headers()`，优先级为 profile 默认头 → 自动会话头 → 用户配置头（后者覆盖前者）。`header_error()` 是唯一校验入口（RFC 7230 token 名、值禁 CR/LF/NUL、SDK/网关保留头拒绝），API 校验器与 transport 共用同一函数。`opencode / opencode-go` 声明 `session_header="x-opencode-session"`，值由 transport 实例 `__init__` 生成（`ses_` + 26 位十六进制），一个座位一个 transport，所以会话 ID 座位级稳定且互不相同；用户填了同名头即覆盖。
  - `errors.py`：跨 openai/anthropic SDK 的错误分类元组 + **全应用唯一的故障码分类器**（`classify_provider_error` / `status_code_of` / `error_chain` / `is_refused_error` / `REFUSED_STATUS_CODES` / `FAILURE_REFUSED`）；`llm_client.py` 再导出，`roles/base.py` 只从 `llm_client` 导入（core/roles 不得 import providers，见 `test_architecture_boundary.py`）。`classify_provider_error` 是**唯一**词表：白天投票（`roles/base._provider_failure_code`）、夜间/动作（`game_service._model_failure_code`）与耐久账本（`ModelInvocationService._failure_code`）三处曾经各写一份、口径不一，同一个 503 于是有了两个名字。超时按**类名**沿 cause 链最先判（SDK 的超时类名不止一种，各调用点本来也把它们当一个桶），随后**状态码优先于 SDK 类**：网关用哪个类回 429/503 不重要，读到 429 就是限流、读到 503 就是容量拒绝；只有读不到状态码时才由 isinstance 分桶
  - `LLMClient` 对上层 API 不变：`_structured_response()` / `_content_text()` 同时兼容 OpenAI 字符串内容 / `finish_reason` 与 Anthropic 内容块列表（text、thinking、reasoning）/ `stop_reason`；Anthropic 强制工具绑定 `tool_choice="any"`（thinking 模式下点名工具会被 400）；`map_strict_capability_error()` 沿异常 cause 链识别 SDK 400/422；`aclose()` 同时关闭 `ChatOpenAI` 与 `ChatAnthropic` 的 SDK 客户端
- `agents/prompt_builder.py` / `agents/state_filter.py` 是**委托外壳**：动作提示委托 `PromptRenderer`，角色视图委托 `ContextProjector.project_view()`；两者源码不含任何内置角色名（有测试门禁）。关警长时提示词省略警长词，不写「本局没有警长」；开警长时仅注入 `SHERIFF_GAME_RULES`。只允许提示里写明的规则，禁止模型用其他版本补流程。
- `agents/output_parser.py` 解析 LLM 返回的 JSON 与 tool call；`parse_tool_call()` 优先原生 function calling，失败回退正则匹配文本模式

## 模型配置与角色目录

- `catalog.py` + `api/routes/catalog_routes.py`：向前端暴露角色目录、标准预设与人数约束；角色条目另带 `rules`（玩家向规则条目，供规则介绍页渲染）。它是**展示元数据**，刻意留在冻结的 `RoleSpec` 之外：新增角色只需加 `roles/*.py` 与这里的 `name_zh` / `rules`，`registry.digest` 不变，旧档仍可续跑
- `stores/model_config_store.py` + `stores/model_key_crypto.py` + `api/routes/model_routes.py`：模型 API 配置 CRUD 与 API Key 加密存储（响应中 Key 仅脱敏返回），含连通性测试端点；配置项还含自定义请求头 `headers`（管理面可见可编辑）与严格模式地址 `strict_base_url`；`POST /api/models/test` 带 `config_id` 时，请求里显式给出的可编辑字段（`base_url` / `model_id` / `strict_base_url` / `api_key` / `provider_profile` / `headers`）一律覆盖已存配置、传 `None` 沿用存储值（`strict_base_url` 传空串表示清空），所以对话框能在保存前试通当前表单值
- `GameService` 在启动引擎前完整解析 `model_assignments`，按数量独立洗牌得到 `seat → LLMClientConfig`；每座创建并复用一个客户端。角色工厂、Scheduler 和 `NightDirector`（`core/night_flow.py`）都以行为发起座位路由，因此白天、夜晚与失败重试不会串用模型
- `GET /api/games/{id}` 与 `GET /api/games/{id}/snapshot` 向观众暴露无密钥的 v2 `model_snapshot`（`name / model_id / provider_profile / seats`）；旧档缺少 `seats` 的条目会被滤掉而不是 500。前端座位图悬停卡按 `seats` 反查模型，不把 `model_id` 写入玩家 DTO 或隐私白名单。**自定义请求头不进快照**：它可能携带中转站凭据，`ModelSnapshotEntry` 也不声明该字段，多传也会被丢弃
- 模型参数 digest 与检查点**不含 headers**，续跑时与 `api_key` 一样从当前模型配置读取。这样新增 headers 不会改变 `parameters_digest`，处于 `interrupted` 的旧局仍可续跑；代价是续跑不检测 headers 变化（与 api_key 的既有语义一致）
- 分配只引用创建时物化的配置快照；之后编辑或删除 `models.json` 中的配置不会改变进行中的对局
- 前端：`components/create/CreateGameWizard.tsx` 两步向导；`ModelStep.tsx` 以数量分配环境默认与已存配置；`components/models/ModelConfigPage.tsx` 管理页；`store/modelConfigStore.ts`
- 规则介绍页：`components/rules/RulesPage.tsx`（右上角「规则介绍」，路由 `/rules`）只消费 `/api/catalog/*` 的公开字段——板子构成与警长开关来自 presets，角色卡来自 roles 的 `name_zh` / `camp` / `rules`，所以新增角色会自动出现；流程、遗言、票制、胜负与可选规则的叙述文案在 `components/rules/ruleSections.ts`，与 `docs/gameplay.md` 同一套规则

## 应用设置

- `stores/app_settings_store.py` + `api/settings_schemas.py` + `api/routes/settings_routes.py`：**应用级**（不是对局级、也不是模型级）的操作开关，落在 `data/settings.json`（与 `models.json` 并列，刻意不合并：那个文件存 `api_key_encrypted`，混进设置项还会让设置以「一条模型配置」的身份出现在 `GET /api/models` 里）。落盘形状 `{"version": 1, "settings": {...}}`，键缺失即「没设置过」——四种损坏（文件缺失 / JSON 坏 / 根非对象 / `settings` 非对象）一律回落「未设置」并告警，写盘走 tmp + `os.replace`，**永不对调用方抛异常**
- **不做通用 KV**：每个设置一对类型化方法（现在是 `get/set_prompt_capture_enabled`），新增一个设置是一次显式评审，而不是往一个桶里塞键
- **唯一裁决链**：`core/prompt_capture.py` 的 `resolve_capture_setting(stored)` = UI（`stored` 是布尔就短路）> 环境变量 `PROMPT_CAPTURE_ENABLED` > 内建默认（**开**）；失败闭合（`maybe` → 关）。路由与 `GameService` 调的是同一个函数，有交叉断言测试，所以界面显示的值与引擎采取的值不可能分叉
- **端点闭集**：`GET/PATCH /api/settings/prompt-capture` 的响应**只有** `enabled` + `source`（`default` / `env` / `ui`），请求体是严格布尔（`"yes"` / `1` 一律 422）；`PATCH` 传 `null`（或缺省）= 清除界面设置、回落 env/default。响应模型带 `extra="forbid", strict=True`，往响应里加 `game_id` / 文件路径 / prompt 片段会直接 422 而不是静默泄漏
- **生效时机是「下一局」**：`GameService._latch_capture_request()` 只在建局时解析一次开关并记进 `_capture_requests`；`_prompt_capture()` 按 `capture_enabled_for_game(requested, marker_exists)` 决定是否开文件，即 `effective = requested_at_start or games/<id>/llm_prompts.jsonl 存在`。**恢复路径不解析开关**，所以中断期间把开关打开不会让旧局从半局开始采集；关掉开关后正在跑的对局仍会写到自己结束（一局要么全采要么不采）。`llm_prompts.jsonl` 本身既是语料文件也是这个闩锁，且**不依赖 checkpoint**
- **三条红线**：采集开关**不进** `GameConfig` / checkpoint / `PointKey` / `_MODEL_PARAMETER_FIELDS`；恢复路径不读开关；开关端点响应是闭集。`data/settings.json` 与 `data/games/<id>/llm_prompts.jsonl` 都由 `backend/.gitignore` 的 `data/*` 覆盖，不入库
- 前端：`store/appSettingsStore.ts` + `components/settings/SettingsPage.tsx`（AppBar「设置」，路由 `/settings`）。它与模型配置 UI 一样**不在覆盖率白名单**内，但有自己的测试；页面按 `source` 三态渲染副标题，并在 `source === 'ui'` 时提供「恢复为跟随环境变量」（即 `setEnabled(null)`）

## 持久化与存档

- **事实源**：`backend/data/wolfkiller.sqlite3` 保存对局、版本化检查点、领域事件、公开事件/快照、模型请求/尝试、运行时计时、benchmark 计划/条目/报告、大厅扁平文件夹（`game_folders` / `game_folder_items`）与派生任务。schema 版本 2 起支持增量迁移；`game_folder_items.game_id` 不外键到 `games`，以便 JSONL 旧档也能归档。外键开启，写入由单写线程串行化；每次规则步骤以 `BEGIN IMMEDIATE` 事务同时提交检查点、事件、消费的模型请求和派生任务，`step_key` 与 digest 提供幂等冲突检测。
- **WAL**：连接使用 SQLite WAL，`synchronous=FULL`，并设置 5 秒 busy timeout。WAL 提升并发读取能力，但 `-wal` 不是独立备份；运行时只复制主 `.sqlite3` 文件可能漏掉尚未 checkpoint 的已提交事务。
- **兼容数据**：`GameLogger` 的 JSONL 日志、`GameManifest` 的 `games/index.json`、对话日志和逐座位记忆仍服务于旧格式/审计链。新运行时的恢复判断以 SQLite 检查点及其 SHA-256 digest 为准，不能用旧 JSON 文件覆盖数据库事实。
- **训练语料采集**（默认开，见「应用设置」）：`data/games/<id>/llm_prompts.jsonl` 每行一条 `(prompt, completion)` 记录（`build_prompt_record` 的 `data.messages` 是模型真实收到的 system + user 原文），与 `game.log` / `llm_calls.log` 同目录、只追加、不自动清理；四条链路（speech / contract_action / pipeline_action / night）在耐久提交之后、且**只在本次不是重放**时 flush 一次，所以断点续跑不会重复写语料。落盘只有 prompt 正文与轮次/阶段/座位/模型名/请求 ID 等元数据，**API Key 与自定义请求头从不进入该文件**。`data/settings.json` 只存应用级开关的值，不进快照、不进任何 DTO。
- **启动恢复**：进程启动会把原先 `running` 的执行标为 `interrupted`，把 `in_flight` 模型尝试标为 `unknown`；不会假定外部模型请求未执行。只有可恢复且版本兼容的对局/benchmark 才能通过显式 resume 继续，恢复重试也受持久化次数约束。
- **恢复兼容性**（`persistence/recovery_compat.py`）：检查点里的 `registry_digest` 是**冻结身份**，不是"当前代码"的副本——journal 键（`PointKey`）、请求 token（`window_id` / `action_key`）与角色资源 setup marker 都用建局时的 digest 盖章（`core/registry_identity.py` 的 `frozen_registry_digest()`），所以之后新增或修改角色**不会**把已提交的工作重新编号。编解码器因此只校验文档**自洽**（根 digest 与 `state.registry_digest` 一致、每个 journal 键与根 digest 一致；不一致才是真损坏），"代码变了还算不算同一局"交给恢复入口一次性裁决 `assess(document, state, registry)`，四个等级：
  - `exact`：注册表与建局时一致。
  - `compatible`：注册表变了，但这局用到的东西没变（例如新增了本局没用到的角色）。直接续跑。
  - `drift`：这局用到的东西变了——已发出的契约 digest 变了，或**已经建立过资源**而座位资源声明变了。**可续跑但必须显式确认**：`POST /api/games/{id}/recover|resume?force=true`（409 响应带 `confirmation_required`，前端弹二次确认）。确认后只改写 setup marker，**不重放任何资源效果**：药已经用掉就还是用掉，声明新增的资源不补发；若本局从未建立过资源（marker 为空，例如首夜前就中断）则**不判 `drift`**：不动 marker，交给下一个调度点按当前声明建立。
  - `incompatible`：角色或契约已消失，或 `RoleSpec.schema_version` / 契约 `schema_version` 在这局之下变动。永久拒绝（`registry_incompatible` / `contract_incompatible`），写进 `games.recovery_block_code`。
- **可恢复状态**：`paused / interrupted / failed / recovery_blocked` 都算"还能救"（`get_execution_info.recoverable`）。`recovery_blocked` 是**诊断结论**而不是终审：修好原因后直接再 `recover` 即可（默认接受这三种状态，更窄的 `expected_statuses` 仍可显式传入）。成功续跑在 `games/<id>/game.log` 写一行 `recovery` 审计（等级、代码、原因、是否 `force`、`from_status`、`execution_generation`、告警、冻结与当前 digest）；该 operation 不在 `_public_operation_events` 白名单里，所以不进观众时间线。
- **降级为告警的门禁**：prompt digest 变化与模型参数变化只记告警（进上面的 `recovery` 审计行），不再拒绝续跑——它们改不了已提交的状态，而 `api_key` / `headers` 本来就从活配置读取。真正拒绝的只剩：缺模型配置、密钥不可用、检查点版本不受支持、检查点损坏。
- **已知边界（有意为之）**：检查点记录每个角色的 `spec_versions`，但**不记录角色声明 digest**，所以只改**已落座角色**的 tags / instructions 判不出来，会落在 `compatible`；能判出来的是资源声明与已用契约 digest——本仓库历史上真正发生的两类变更（`WOLF_BEAUTY_SPEC.initial_resources`、`witch_action.allowed_effects`）。因此编码约定是**角色 id 永不删除、改名保留别名**：Hook 是代码，删掉的角色无法从旧档恢复，只能判 `incompatible`。
- **快照版本化**：检查点包含 pipeline、registry、spec/effect schema 与编排状态；缺少规范/迁移器时拒绝恢复。
- **写入口不变式**：`runtime.statuses` / `runtime.relations` 的值一律是普通 `set`，不能写 `frozenset`（`_set_map` 归一化时两者等价，但 `_apply_one` 对既有值直接 `.add()`，frozenset 会 `AttributeError`）。`ADD_RELATION` 是集合成员关系：**跨批次重复写入是幂等的**（狼美人隔一晚重魅同一人合法），但同一批次内重复仍是批次完整性违规。
- **拒绝的分类**：`EffectConflict`（`EffectRejected` 子类）表示「批次合法、状态不允许」——模型可能合法地选到状态已不允许的目标。模型驱动的契约在 `Scheduler._apply_or_degrade` 遇到它降级为 `fallback_action_type` 并落 `stage_telemetry` 故障行（`code=effect_rejected`），不丢整局；结构性违规（effect id 错序、权限/visibility 越界、revision mismatch、`clone()` 期 runtime 损坏）仍是普通 `EffectRejected`，保持致命。结算（`settle_pending`）与纯 `react` 契约没有 fallback，其拒绝仍致命。
注册表 digest 只与**文档自身**比对（见上一条），不再要求与运行中的注册表全等。模型快照不含 API Key；旧快照缺 `count/seats` 时保持未知，不反推座位映射。**编排状态的字段集是精确匹配的**（`persistence/engine_checkpoint.py` 会拒绝字段集不符的检查点），所以只为提交一次而存在的临时槽位（白天窗口事件 `_pending_point_events`、旁白 `_pending_narration`）刻意不进编排状态：它们在写入后立刻由紧随其后的那一步提交，崩溃时最坏丢掉这一步。

### 公开事件同步

公开视图由领域事件白名单投影。**观众流是上帝视角**（快照直接给每个座位的身份与阵营），所以角色的隐藏信息照常投影：狼美人魅惑（`wolf_beauty_charm`）带 `target_seat`，老酒鬼的 `charm_immune` 也随 `selected_target.resource_labels` 对狼美人可见。对**玩家**保密靠另外三层，不靠剪观众字段——`ContextProjector._public_facts` 只给发言/投票/角色规则，`RELATION` 命名空间不投影（`charmed_by` 只落库），`ConversationLog.visible_to` 按座位过滤。观众可见的夜晚思考（`night_thought`：守卫/女巫/预言家/猎人/白狼王/骑士/狼美人的 `reasoning`）、狼人队内发言（`wolf_chat_message`，附该次发言给出的次日计划 `day_plan`）、狼票（`wolf_vote`）、结算留给座位的状态标记（`player_status`：`poisoned` / `wounded` / `delayed_death`，即老酒鬼被毒或中枪但当晚不死）、白痴翻牌（`exile_cancelled`）、白狼王自爆（`self_explode`）、警长当选（`sheriff_elected`）与交徽/撕徽（`sheriff_badge`，附警长的决策理由 `reasoning`，旧档缺省）、座位开始生成发言（`speaking`，早于发言文本）会进入 audience 表；私有 `thought` 模板、夜间情报与身份资源字段仍被剥离。状态快照带其 `seq` 和 `projection_version`；增量页使用 `after_seq`（排他游标）、`next_seq`、`high_watermark` 和 `has_more`。客户端先取得快照，从该 `seq` 之后分页追到一个固定的 `high_watermark`；下一轮再取新的 watermark。`through_seq` 可把一次追赶固定在同一上界，避免持续写入导致永远翻不完。WebSocket 只用于低延迟提示，断线重连始终用耐久游标补齐；游标大于服务端 watermark 会明确报错，客户端应重新取快照，而不是静默跳过事件。

**观众事件必须先有一步耐久提交**。audience 表只由 `GameService._checkpoint_domain_events(engine, step_key)` 的映射写入，所以一个领域事件要进观众流，必须有一个带分支的 step label；缺任何一环都会静默丢事件。四层失败模式分别是：**A** 发射端 payload 缺字段；**B** 事件进了耐久流但 `AudienceProjector._EVENTS` 白名单没有该类型；**C** 事件根本没进耐久流（没有 label 分支，白天的 `DAY_ACTION` / `POST_SPEECH_ACTION` / `EXILE_VERDICT` 窗口、放逐反应、延迟死亡、技术性弃权、旁白都曾属于这一类）；**D** 前端三个渲染面没有 case。承载观众的 step label 现有：`night_point:`（夜间流水线批次）、`day_point:`（白天窗口批次，跳过已由死亡/翻牌步骤宣布的 `PLAYER_DIED` / `EXILE_CANCELLED` / `PLAYER_REVEALED`）、`narration:`（旁白，槽位 `_pending_narration`）、`vote_received:` 与 `vote_technical_abstain:`（互斥：同一座位只落一步，系统代投带 `failure_code`）、`night_death:` / `delayed_death:` / `day_interrupted:` / `day_reaction:` / `exile_reaction:`（死亡按 label 里的座位集合宣布，不按死因白名单，否则 `charm` 这类死因会被过滤掉；同一个步骤还发布结算留给座位的 `STATUS_ADDED` 标记，因为白天结算跑在流水线之外，没有别的步骤能承载它）、`exile_cancelled:`、`sheriff_*`、`wolf_discussion:`、`night_complete:`。新增领域事件时先问：**哪一步把它送进耐久流？**

## 前端架构

- **状态管理**（`frontend/src/store/gameStore.ts`，Zustand 5）：同时处理直播模式（WebSocket 实时事件）与回放模式（HTTP 全量日志 + 播放/暂停、逐帧步进、0.5x~8x 倍速、按事件类型筛选）。倍速只作用于已落盘事件；直播下一格仍要等当前座位的模型调用结束。对局级 `modelSnapshot` 在详情/观众快照加载时写入，回放 seek 不改写。
- **座位图**（`frontend/src/components/game/SeatMap.tsx`）：椭圆/双列布局；悬停弹出血月风格详情卡（身份、存活/发言、所用模型名与提供方）。公开 DTO 带 `is_sheriff`；仅 `enable_sheriff` 开局才会出现警长徽标。时间轴展示竞选当选与交徽/撕徽事件。
- **WebSocket**（`frontend/src/api/websocket.ts`）：自定义 hook，建立连接后将 JSON 消息路由到 Zustand store 对应处理函数
- **主题**：深色主题「血月剧场」（Crimson Gothic）；设计令牌唯一来源为 `frontend/src/theme/tokens.ts`，组件禁止硬编码色值；风格稿见 `frontend/design-demos/`
- **旧档回放的两条路径**：`GET /api/games/{id}/events`（观众流，`audience_events` 表）是首选；无投影的对局回退到 `GET /api/games/{id}/logs`，它把 `game.log` 的记录投影成同一套公开事件。两条路径必须覆盖同一批公开类型 —— `tests/test_game_routes.py::test_every_public_audience_action_type_has_a_replay_branch` 遍历投影器声明的每个类型，用规范 payload 要求回放路径产出对应事件；承载不了的行改为**逐条跳过 + 计数 + 服务端告警**（`api/replay_diagnostics.py`），计数经内部只读接口 `GET /api/diagnostics/replay-drops` 查看，丢弃内容不进公开 DTO。
- **文案**：后端只发机器值（`wolf_kill`、`wolves_outnumber_good`、`sheriff_election`…），取值→文案表唯一来源为 `frontend/src/labels.ts`，按**渲染语境**分 20 个命名空间（死因表与编年史短标签对同一个键可以有不同措辞），键类型尽量收紧为联合类型。三处门禁绑住它：`src/test/labels.test.ts` 扫描组件源码（再出现本地 `*_LABELS`/`*_TAGS` 即红）并断言命名空间非空；后端 `tests/test_values.py::test_frontend_labels_cover_every_declared_value` 直接读该文件，断言文案覆盖 `app/models/values.py`（死因/胜因/阵营）与 `GamePhase` 的每个取值 —— 这条跨语言断言补上了「后端加了值、界面没加文案」只能靠人眼发现的缝
- **大厅与评测隔离**：`GET /api/games` 只返回 `benchmark_run_id` 为空且 `source != benchmark` 的普通对局；评测局仍可通过 `GET /api/games/{id}` 回放。大厅用扁平文件夹（`GET/POST /api/folders`、`PUT /api/games/{id}/folder`、`POST /api/games/batch-move|batch-delete`）收纳与批量删除。评测对局只从评测页进出：`GET /api/benchmarks/{id}/games` 并上名称/阶段/胜负等投影；`DELETE /api/benchmarks/{id}/games/{game_id}`、`POST .../games/batch-delete` 先解绑再删档；`DELETE /api/benchmarks/{id}` 取消进行中的 run 并级联删除绑定对局与报告。大厅独立 `DELETE /api/games/{id}` 对评测局仍返回 409。

## 关键设计约束

- **隐私边界**：公开 DTO 与前端消费链不含任何私有字段（`role_init / visible_to / night_intel / check_results / has_antidote / has_poison / has_gun` 等）；观众可见的 `night_thought.reasoning`、狼聊与狼票是上帝视角公开事件，私有 `thought` 模板不进入 audience 表。有隐私扫描测试保障；角色 Hook 函数体零状态访问。开关状态本身**不是**私有字段（它描述进程配置，不是任何座位的隐藏信息），但 `/api/settings/prompt-capture` 的响应体仍然闭集（`enabled` + `source`），不允许携带 prompt 内容 / `game_id` / 文件路径
- **降级动作必须自报家门**：引擎替座位提交的兜底动作（`Scheduler._fallback`、`_command_provider` 的两处兜底）与狼票兜底（`NightDirector.wolf_vote_turn`）一律带 `degraded=True`，理由取 `app/models/pipeline.py` 的 `DEGRADED_REASONING`（「本轮未提交决策」）与 `DEGRADED_THOUGHT`（「本轮未提交决策（系统代行）」）。这条不改「什么时候降级」，只改**降级在读者眼里是什么**：旧文案「系统异常，本轮未行动」既把「3 次校验不过」误报成故障，又读起来像玩家自己决定不动手，而命令上没有标记，四个下游只能照字面理解——契约的 `resolve` hook 把它拼成 `决定今晚不使用药水：系统异常，本轮未行动`，投影器发成 `night_thought`，`_log_audience_pairs` 又把它写进本人的 THOUGHT 记录，下一轮提示词遂以「你的历史思考回顾」把引擎报错喂回给模型（真局 `09982d7c` 有一手：噩梦之影第 2 轮的自述就是这句）。**`degraded` 在 `ActionCommand` 上 `exclude=True`**：`stable_digest()` 是 `aggregate_effects` 的排序末位键，字段进序列化会让两条仅标记不同的兜底命令交换顺序；`WolfVote.degraded` 则由 `persistence/engine_checkpoint.py` 在 `wolf_votes` 里显式持久化，解码缺键回落 `False`，所以旧检查点原样续跑。落点：17 个 `*_reasoning_effect` hook 见 `command.degraded` 直接输出 `DEGRADED_THOUGHT` 并把标记放进 payload；`_log_audience_pairs` 对 `degraded is True` 跳过 `add_thought`（动作本身照常记 `game.log`，失败可见性不受影响）；`WOLF_VOTE` 事件带同一个键。观众侧不新增事件类型——`night_thought` / `wolf_vote` 各加一个可选 `degraded` 字段，**五处必须同步**：投影器白名单、`game_routes` 的 `_AUDIENCE_ACTION_OPTIONAL`、回放分支、**`api/schemas.py` 的公开 DTO**（`PublicNightThoughtResponse` / `PublicWolfVoteResponse`；`_PublicResponse` 是 `extra="forbid"`，漏掉这一处就是含降级动作的对局在 `/api/games/{id}/logs` 上 500，而旧档缺该键照旧合法）、前端三个渲染面渲染成「系统代行」。**durable 通道必须显式带上标记**：`degraded` 是 `exclude=True`，而 `_command_provider` 的 durable 包装用 `model_dump()` 序列化、`model_validate` 还原 —— 直接交 `model_dump()` 会让标记在**生产路径**上消失（写入口照旧计 `game` 配额，猎人枪仍被静默作废）；序列化统一走 `game_service._durable_command_payload()`（只在为真时补键，旧行形状逐字节不变），模型答复里的同名字段由 `_pipeline_command()` 剔除。门禁：`tests/test_degraded_hooks.py`（AST 扫描发现全部 17 个 hook，漏改即红）、`tests/test_degraded_audience.py`（三层各自的表态 + 旧档兼容 + 实时狼票 label 分支）、`tests/test_degraded_reasoning.py`（`exclude` 与 digest 不变式）、`tests/test_degraded_durable.py`（账本往返 + 真实 `_command_provider` 路径）、`tests/test_game_routes.py::test_every_projected_public_payload_is_accepted_by_the_replay_dto`（投影器放行的键含**可选键**都必须被公开 DTO 接受；`pool_seats` / `silenced_seats` 的 list/tuple 之争也归它守）；`scripts/audit-degraded-wording.py` 用于把历史脏数据与「新代码仍在产生」分开。 **配额与资产是两套账**:`action_counts` 的 `window`/`round` 是「已问过」(降级也算,否则一次故障会在本回合每个槽位再花一次 60s 重问),而 `game` 是「这张牌已兑现」——它和 `role_resources` 是同一件事的第二次记账,所以降级**不写** `game`,否则模型失败一次就会让猎人再也开不了枪(枪还在手上,校验器却一直答 `game_limit_reached`)。标记经 `EffectPermission.degraded` 与 `ActionContext.degraded` 传到写入口(`EffectApplier` → `record_accepted_action`),**不进 ACCEPT_ACTION 载荷**(两个提交点 `action_resolver` / `vote_service` 写同一份四键载荷且精确校验,资源建立点 `role_runtime` 是空载荷并因此被跳过;旧检查点里的提交记录会因多一个键被拒)。注册表因此新增断言:声明 `per_game_limit == 1` 的契约必须消耗资源,让「一次性」永远由资产而非计数兜底。`PROJECTED_CONTEXT` 也带上 `degraded`,因为本人历史里不再记录兜底思考,模型需要知道上一轮不是它答的。门禁:`tests/test_degraded_limits.py`。
- **测试门禁**：后端 pytest 全量（`tests` + `app/` 内嵌测试）+ `--cov-fail-under=100`（statement/branch）。六个扩展样例（`test_guard_extension.py` / `test_idiot_extension.py` / `test_werewolf_king_extension.py` / `test_knight_extension.py` / `test_wolf_beauty_extension.py` / `test_old_drunkard_extension.py`；`test_catalog_routes.py` 断言 11 角色 6 预设）；`test_guard_extension.py` **不再**校验五个核心文件的 blob 哈希。
- **核心源码门禁**：修改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py` 后需同步更新禁词/角色名测试：`tests/test_game_engine.py`、`tests/test_action_resolver.py`、`tests/test_prompt_builder.py`、`tests/test_prompt_renderer.py`。
