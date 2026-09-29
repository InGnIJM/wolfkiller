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
│   │   └── stores/              # 模型配置持久化 + API Key 加密存储
│   └── tests/                   # pytest 测试（statement/branch 100% 覆盖门禁）
├── frontend/
│   └── src/
│       ├── api/                 # REST 客户端 + WebSocket hook
│       ├── store/               # Zustand 状态管理 + 时间轴回放引擎 + 模型配置 store
│       ├── theme/               # 设计令牌（tokens.ts 为色值唯一来源）
│       └── components/
│           ├── lobby/           # 大厅（普通对局列表、扁平文件夹、批量操作）
│           ├── benchmarks/      # 评测任务列表、新建与详情（含评测对局表）
│           ├── create/          # 创建游戏向导（人数身份 → 模型配置）
│           ├── game/            # 游戏（座位图、时间轴、历史面板、胜利画面）
│           ├── models/          # 模型配置页与编辑对话框
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
| `core/scheduler.py` | 调度点（NIGHT_ACTION / NIGHT_WOLF_VOTE / NIGHT_WITCH_ACTION / NIGHT_SEER_ACTION / NIGHT_COMMIT / DAWN_REACTION / DAY_ACTION / EXILE_VERDICT 等）、稳定排序、请求收集、响应窗口队列与阶段门禁；`run_point(..., slot=)` 让同一阶段内多次运行同一调度点（`PointKey.phase` 为 `phase#slot`，请求 token 也混入 slot）；聚合按 `contract_id` 分组，跨角色共享的完全一致契约合并计票；`point_journal.py` 提供断点续跑检查点，journal 键与请求 token 都用**对局冻结的** `registry_digest` 盖章（`core/registry_identity.py`），所以新增/修改角色不会让已完成的调度点被重放、也不会重复发起同一次模型请求 |
| `core/night_settlement.py` | 夜晚结算：pending damage/protection → 死亡批次 |
| `agents/prompt_renderer.py` | 仅从 RoleSpec/Contract/Context 渲染通用 Prompt（历史以 Base64 不可执行注入） |
| `roles/{werewolf,witch,seer,hunter,villager,guard}.py` | 内置角色：声明式 spec + 纯 Hook（`*_applicable` / `validate_*` / `resolve_*`） |

辅助模块：

- `core/role_pipeline.py` — 流水线运行器（`RolePipeline.run_point`），引擎按调度点调用
- `core/night_flow.py` — `NightDirector`：狼队夜间讨论/投票与旁白；`build_briefing` 等 Prompt/schema 构造。女巫/预言家思考已交给角色流水线，Director 不再提供 `witch_think` / `seer_think`
- `core/sheriff_flow.py` — `SheriffDirector`：对局级警长职位（竞选、1.5 票、发言方向、交徽/撕徽）。不是角色，不进注册表。
- `core/vote_service.py` — 可信投票域服务：原子、幂等的终局选票收据（accepted / voluntary-abstain / technical-abstain）
- `core/conversation_log.py` — 全部对话记录及按角色过滤的视图
- `core/state_transaction.py` — 状态事务与 revision 管理

### 夜晚流程

`GameEngine._execute_night()` 走分阶段 `_execute_staged_night()`，调度点顺序固定为：

`NIGHT_ACTION`（守卫）→ 狼队讨论/投票 → `NIGHT_WOLF_VOTE` → `NIGHT_WITCH_ACTION` → `NIGHT_SEER_ACTION` → `NIGHT_COMMIT`

随后 `_resume_pipeline_night()` 以分阶段检查点发布死亡、判定胜负、推进阶段；`enable_sheriff` 开局时发 `SHERIFF_ELECTION_START`，关局仍 `NIGHT_ACTIONS_COMPLETE → DAWN`。所有阶段点均持久化检查点，失败后精确续跑不重放。狼队讨论与逐票由 `NightDirector`（`core/night_flow.py`）驱动，不走角色 Hook；讨论预算为每狼 6 次、至少完整两圈（刀口共识也要等两圈后才能提前结束），发言与次日计划各 ≤400 字，狼队成员按 `camp == Camp.WEREWOLF` 识别（含白狼王），白狼王复用狼人导出的 `WEREWOLF_KILL_CONTRACT` 共享聚合。警长流程由 `SheriffDirector`（`core/sheriff_flow.py`）硬编码，Agent 只选当前步骤绑定的工具；竞选、退水与警长投票提示注入已上警座位、当前候选人与未上警（警下）选民名单；狼玩家的竞选、退水与警长投票提示会额外注入狼队成员名单与本夜狼队频道记录（含次日计划），好人视角永不注入。警上/警下名单同时进入竞选发言、白天发言、放逐投票与超时重试投票的权威状态（警上=`office.candidates`，警下=`off_badge_seats`：开选时存活且从未上警，排除开选前死亡，`office.candidates` 整局保留），竞选期再由 `PromptBuilder._format_badge_speaking_progress` 给出竞选发言顺序与已发言/尚未发言名单，避免模型把警下玩家误说成警上。

### 白天流水线窗口

白天生命周期为角色开放两个与角色无关的窗口，引擎只认 `DAY_INTERRUPTED / EXILE_PENDING / EXILE_CANCELLED / PLAYER_REVEALED` 这些通用事件，不出现任何角色名：

- **`DAY_ACTION`**（每位发言者开口前，`slot=f"{vote_round}:{seat}"`）：角色可提交行动；若提交事件含 `DAY_INTERRUPTED`，引擎 `_resolve_day_interruption()` 结算 pending damage、按 `death_history` 去重发布死亡、以这些 PLAYER_DIED 跑 `DAWN_REACTION`、写 `day_interrupted:{round}:{seat}` 检查点，随后判胜负或以 `WEREWOLF_EXPLODED` 转入 NIGHT。续跑时 `_journaled_day_interruption()` 从 journal 识别已发生的中断并幂等重放。白狼王自爆是当前唯一实现。

- **`POST_SPEECH_ACTION`**（全体存活玩家发言完毕、放逐投票前，`slot="post_speech"`）：与 `DAY_ACTION` 同构的第二个白天窗口，只在有角色声明该调度点时运行（`_post_speech_action_enabled()`）。骑士翻牌决斗是当前唯一实现：是狼人则提交 `DAY_INTERRUPTED`（`cause="knight_duel"`）走同一条中断路径，被裁决者在转 NIGHT 前经 `give_last_words(..., daytime=True)` 发表遗言（骑士本人同为该 cause 但不走这条路，因此不会被补发遗言）；是好人则只提交自身伤害。窗口跑完引擎先 `_settle_and_publish()`（白天伤害立即生效、绝不带进夜里），再 `_resolve_delayed_deaths()`，最后才 `SPEECHES_COMPLETE`。`_journaled_day_interruption()` 按 slot 后缀匹配两个窗口的检查点，因此续跑时即使阶段已推进到 NIGHT 也能幂等重放。
- **`EXILE_VERDICT`**（放逐前）：`_apply_exile(seat)` 先以 `EXILE_PENDING{target_seat, cause="exile"}` 跑该点；若提交事件含 `EXILE_CANCELLED`，则对随行 `PLAYER_REVEALED` 写 `revealed_role`、系统消息播报翻牌、`add_vote_result(..., cancelled_seat)` 记为无人出局并跳过遗言；否则走原 `mark_dead("exile")` 路径。白痴翻牌是当前唯一实现。

### 放逐反应

引擎放逐玩家后，将合成的 PLAYER_DIED 提交注入 `DAWN_REACTION` 调度点的响应队列，让猎人等响应契约通过流水线反应。猎人的 `_SHOOT_REASONS` 含 `self_explode`，被白狼王带走时同样可开枪。

## 白天生命周期

- 白天发言、投票、平票复投、遗言是引擎内与角色无关的行为，经 `BaseRole`（`roles/base.py`）调用 LLM：发言走 tool calling 两层防线 + 字数校验 + 兜底；投票走三级重试梯子（strict tool 90s → JSON 压缩上下文 120s → JSON 强格式短重试 30s，`LLM_ACTION_FINAL_RETRY_TIMEOUT_SECONDS`），全部失败才显式技术弃票，并按并发上限（`VOTE_CONCURRENCY`，默认 5）执行。`give_last_words()` 在调用 LLM **之前**先写 `last_words_pending:{round}:{seat}:{cause}` 检查点并认领该死者，因此发言中途崩溃续跑时最坏是**丢掉这条遗言**，而不是把它重放成第二条（时间线重复比缺失更难排查）
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
  - `base.py`：`CallPurpose`、`ProviderProfile`、`call_budget()`（两种传输共用的 token/超时预算）
  - **自定义请求头**：`ProviderProfile.default_headers` / `session_header` + `ModelConfig.headers` → `merge_request_headers()`，优先级为 profile 默认头 → 自动会话头 → 用户配置头（后者覆盖前者）。`header_error()` 是唯一校验入口（RFC 7230 token 名、值禁 CR/LF/NUL、SDK/网关保留头拒绝），API 校验器与 transport 共用同一函数。`opencode / opencode-go` 声明 `session_header="x-opencode-session"`，值由 transport 实例 `__init__` 生成（`ses_` + 26 位十六进制），一个座位一个 transport，所以会话 ID 座位级稳定且互不相同；用户填了同名头即覆盖。
  - `errors.py`：跨 openai/anthropic SDK 的错误分类元组；`llm_client.py` 再导出，`roles/base.py` 只从 `llm_client` 导入（core/roles 不得 import providers，见 `test_architecture_boundary.py`）
  - `LLMClient` 对上层 API 不变：`_structured_response()` / `_content_text()` 同时兼容 OpenAI 字符串内容 / `finish_reason` 与 Anthropic 内容块列表（text、thinking、reasoning）/ `stop_reason`；Anthropic 强制工具绑定 `tool_choice="any"`（thinking 模式下点名工具会被 400）；`map_strict_capability_error()` 沿异常 cause 链识别 SDK 400/422；`aclose()` 同时关闭 `ChatOpenAI` 与 `ChatAnthropic` 的 SDK 客户端
- `agents/prompt_builder.py` / `agents/state_filter.py` 是**委托外壳**：动作提示委托 `PromptRenderer`，角色视图委托 `ContextProjector.project_view()`；两者源码不含任何内置角色名（有测试门禁）。关警长时提示词省略警长词，不写「本局没有警长」；开警长时仅注入 `SHERIFF_GAME_RULES`。只允许提示里写明的规则，禁止模型用其他版本补流程。
- `agents/output_parser.py` 解析 LLM 返回的 JSON 与 tool call；`parse_tool_call()` 优先原生 function calling，失败回退正则匹配文本模式

## 模型配置与角色目录

- `catalog.py` + `api/routes/catalog_routes.py`：向前端暴露角色目录、标准预设与人数约束
- `stores/model_config_store.py` + `stores/model_key_crypto.py` + `api/routes/model_routes.py`：模型 API 配置 CRUD 与 API Key 加密存储（响应中 Key 仅脱敏返回），含连通性测试端点；配置项还含自定义请求头 `headers`（管理面可见可编辑）与严格模式地址 `strict_base_url`；`POST /api/models/test` 带 `config_id` 时，请求里显式给出的可编辑字段（`base_url` / `model_id` / `strict_base_url` / `api_key` / `provider_profile` / `headers`）一律覆盖已存配置、传 `None` 沿用存储值（`strict_base_url` 传空串表示清空），所以对话框能在保存前试通当前表单值
- `GameService` 在启动引擎前完整解析 `model_assignments`，按数量独立洗牌得到 `seat → LLMClientConfig`；每座创建并复用一个客户端。角色工厂、Scheduler 和 `NightDirector`（`core/night_flow.py`）都以行为发起座位路由，因此白天、夜晚与失败重试不会串用模型
- `GET /api/games/{id}` 与 `GET /api/games/{id}/snapshot` 向观众暴露无密钥的 v2 `model_snapshot`（`name / model_id / provider_profile / seats`）；旧档缺少 `seats` 的条目会被滤掉而不是 500。前端座位图悬停卡按 `seats` 反查模型，不把 `model_id` 写入玩家 DTO 或隐私白名单。**自定义请求头不进快照**：它可能携带中转站凭据，`ModelSnapshotEntry` 也不声明该字段，多传也会被丢弃
- 模型参数 digest 与检查点**不含 headers**，续跑时与 `api_key` 一样从当前模型配置读取。这样新增 headers 不会改变 `parameters_digest`，处于 `interrupted` 的旧局仍可续跑；代价是续跑不检测 headers 变化（与 api_key 的既有语义一致）
- 分配只引用创建时物化的配置快照；之后编辑或删除 `models.json` 中的配置不会改变进行中的对局
- 前端：`components/create/CreateGameWizard.tsx` 两步向导；`ModelStep.tsx` 以数量分配环境默认与已存配置；`components/models/ModelConfigPage.tsx` 管理页；`store/modelConfigStore.ts`

## 持久化与存档

- **事实源**：`backend/data/wolfkiller.sqlite3` 保存对局、版本化检查点、领域事件、公开事件/快照、模型请求/尝试、运行时计时、benchmark 计划/条目/报告、大厅扁平文件夹（`game_folders` / `game_folder_items`）与派生任务。schema 版本 2 起支持增量迁移；`game_folder_items.game_id` 不外键到 `games`，以便 JSONL 旧档也能归档。外键开启，写入由单写线程串行化；每次规则步骤以 `BEGIN IMMEDIATE` 事务同时提交检查点、事件、消费的模型请求和派生任务，`step_key` 与 digest 提供幂等冲突检测。
- **WAL**：连接使用 SQLite WAL，`synchronous=FULL`，并设置 5 秒 busy timeout。WAL 提升并发读取能力，但 `-wal` 不是独立备份；运行时只复制主 `.sqlite3` 文件可能漏掉尚未 checkpoint 的已提交事务。
- **兼容数据**：`GameLogger` 的 JSONL 日志、`GameManifest` 的 `games/index.json`、对话日志和逐座位记忆仍服务于旧格式/审计链。新运行时的恢复判断以 SQLite 检查点及其 SHA-256 digest 为准，不能用旧 JSON 文件覆盖数据库事实。
- **启动恢复**：进程启动会把原先 `running` 的执行标为 `interrupted`，把 `in_flight` 模型尝试标为 `unknown`；不会假定外部模型请求未执行。只有可恢复且版本兼容的对局/benchmark 才能通过显式 resume 继续，恢复重试也受持久化次数约束。
- **恢复兼容性**（`persistence/recovery_compat.py`）：检查点里的 `registry_digest` 是**冻结身份**，不是"当前代码"的副本——journal 键（`PointKey`）、请求 token（`window_id` / `action_key`）与角色资源 setup marker 都用建局时的 digest 盖章（`core/registry_identity.py` 的 `frozen_registry_digest()`），所以之后新增或修改角色**不会**把已提交的工作重新编号。编解码器因此只校验文档**自洽**（根 digest 与 `state.registry_digest` 一致、每个 journal 键与根 digest 一致；不一致才是真损坏），"代码变了还算不算同一局"交给恢复入口一次性裁决 `assess(document, state, registry)`，四个等级：
  - `exact`：注册表与建局时一致。
  - `compatible`：注册表变了，但这局用到的东西没变（例如新增了本局没用到的角色）。直接续跑。
  - `drift`：这局用到的东西变了——已发出的契约 digest 变了，或座位资源声明变了。**可续跑但必须显式确认**：`POST /api/games/{id}/recover|resume?force=true`（409 响应带 `confirmation_required`，前端弹二次确认）。确认后只改写 setup marker，**不重放任何资源效果**：药已经用掉就还是用掉，声明新增的资源不补发；若本局从未建立过资源（marker 为空）则不动 marker，交给下一个调度点按当前声明建立。
  - `incompatible`：角色或契约已消失，或 `RoleSpec.schema_version` / 契约 `schema_version` 在这局之下变动。永久拒绝（`registry_incompatible` / `contract_incompatible`），写进 `games.recovery_block_code`。
- **可恢复状态**：`paused / interrupted / failed / recovery_blocked` 都算"还能救"（`get_execution_info.recoverable`）。`recovery_blocked` 是**诊断结论**而不是终审：修好原因后直接再 `recover` 即可（默认接受这三种状态，更窄的 `expected_statuses` 仍可显式传入）。成功续跑在 `games/<id>/game.log` 写一行 `recovery` 审计（等级、代码、原因、是否 `force`、`from_status`、`execution_generation`、告警、冻结与当前 digest）；该 operation 不在 `_public_operation_events` 白名单里，所以不进观众时间线。
- **降级为告警的门禁**：prompt digest 变化与模型参数变化只记告警（进上面的 `recovery` 审计行），不再拒绝续跑——它们改不了已提交的状态，而 `api_key` / `headers` 本来就从活配置读取。真正拒绝的只剩：缺模型配置、密钥不可用、检查点版本不受支持、检查点损坏。
- **已知边界（有意为之）**：检查点记录每个角色的 `spec_versions`，但**不记录角色声明 digest**，所以只改**已落座角色**的 tags / instructions 判不出来，会落在 `compatible`；能判出来的是资源声明与已用契约 digest——本仓库历史上真正发生的两类变更（`WOLF_BEAUTY_SPEC.initial_resources`、`witch_action.allowed_effects`）。因此编码约定是**角色 id 永不删除、改名保留别名**：Hook 是代码，删掉的角色无法从旧档恢复，只能判 `incompatible`。
- **快照版本化**：检查点包含 pipeline、registry、spec/effect schema 与编排状态；缺少规范/迁移器时拒绝恢复。
- **写入口不变式**：`runtime.statuses` / `runtime.relations` 的值一律是普通 `set`，不能写 `frozenset`（`_set_map` 归一化时两者等价，但 `_apply_one` 对既有值直接 `.add()`，frozenset 会 `AttributeError`）。`ADD_RELATION` 是集合成员关系：**跨批次重复写入是幂等的**（狼美人隔一晚重魅同一人合法），但同一批次内重复仍是批次完整性违规。
- **拒绝的分类**：`EffectConflict`（`EffectRejected` 子类）表示「批次合法、状态不允许」——模型可能合法地选到状态已不允许的目标。模型驱动的契约在 `Scheduler._apply_or_degrade` 遇到它降级为 `fallback_action_type` 并落 `stage_telemetry` 故障行（`code=effect_rejected`），不丢整局；结构性违规（effect id 错序、权限/visibility 越界、revision mismatch、`clone()` 期 runtime 损坏）仍是普通 `EffectRejected`，保持致命。结算（`settle_pending`）与纯 `react` 契约没有 fallback，其拒绝仍致命。
注册表 digest 只与**文档自身**比对（见上一条），不再要求与运行中的注册表全等。模型快照不含 API Key；旧快照缺 `count/seats` 时保持未知，不反推座位映射。**编排状态的字段集是精确匹配的**（`persistence/engine_checkpoint.py` 会拒绝字段集不符的检查点），所以只为提交一次而存在的临时槽位（白天窗口事件 `_pending_point_events`、旁白 `_pending_narration`）刻意不进编排状态：它们在写入后立刻由紧随其后的那一步提交，崩溃时最坏丢掉这一步。

### 公开事件同步

公开视图由领域事件白名单投影。**观众流是上帝视角**（快照直接给每个座位的身份与阵营），所以角色的隐藏信息照常投影：狼美人魅惑（`wolf_beauty_charm`）带 `target_seat`，老酒鬼的 `charm_immune` 也随 `selected_target.resource_labels` 对狼美人可见。对**玩家**保密靠另外三层，不靠剪观众字段——`ContextProjector._public_facts` 只给发言/投票/角色规则，`RELATION` 命名空间不投影（`charmed_by` 只落库），`ConversationLog.visible_to` 按座位过滤。观众可见的夜晚思考（`night_thought`：守卫/女巫/预言家/猎人/白狼王/骑士/狼美人的 `reasoning`）、狼人队内发言（`wolf_chat_message`，附该次发言给出的次日计划 `day_plan`）、狼票（`wolf_vote`）、结算留给座位的状态标记（`player_status`：`poisoned` / `wounded` / `delayed_death`，即老酒鬼被毒或中枪但当晚不死）、白痴翻牌（`exile_cancelled`）、白狼王自爆（`self_explode`）、警长当选（`sheriff_elected`）与交徽/撕徽（`sheriff_badge`）、座位开始生成发言（`speaking`，早于发言文本）会进入 audience 表；私有 `thought` 模板、夜间情报与身份资源字段仍被剥离。状态快照带其 `seq` 和 `projection_version`；增量页使用 `after_seq`（排他游标）、`next_seq`、`high_watermark` 和 `has_more`。客户端先取得快照，从该 `seq` 之后分页追到一个固定的 `high_watermark`；下一轮再取新的 watermark。`through_seq` 可把一次追赶固定在同一上界，避免持续写入导致永远翻不完。WebSocket 只用于低延迟提示，断线重连始终用耐久游标补齐；游标大于服务端 watermark 会明确报错，客户端应重新取快照，而不是静默跳过事件。

**观众事件必须先有一步耐久提交**。audience 表只由 `GameService._checkpoint_domain_events(engine, step_key)` 的映射写入，所以一个领域事件要进观众流，必须有一个带分支的 step label；缺任何一环都会静默丢事件。四层失败模式分别是：**A** 发射端 payload 缺字段；**B** 事件进了耐久流但 `AudienceProjector._EVENTS` 白名单没有该类型；**C** 事件根本没进耐久流（没有 label 分支，白天的 `DAY_ACTION` / `POST_SPEECH_ACTION` / `EXILE_VERDICT` 窗口、放逐反应、延迟死亡、技术性弃权、旁白都曾属于这一类）；**D** 前端三个渲染面没有 case。承载观众的 step label 现有：`night_point:`（夜间流水线批次）、`day_point:`（白天窗口批次，跳过已由死亡/翻牌步骤宣布的 `PLAYER_DIED` / `EXILE_CANCELLED` / `PLAYER_REVEALED`）、`narration:`（旁白，槽位 `_pending_narration`）、`vote_received:` 与 `vote_technical_abstain:`（互斥：同一座位只落一步，系统代投带 `failure_code`）、`night_death:` / `delayed_death:` / `day_interrupted:` / `day_reaction:` / `exile_reaction:`（死亡按 label 里的座位集合宣布，不按死因白名单，否则 `charm` 这类死因会被过滤掉；同一个步骤还发布结算留给座位的 `STATUS_ADDED` 标记，因为白天结算跑在流水线之外，没有别的步骤能承载它）、`exile_cancelled:`、`sheriff_*`、`wolf_discussion:`、`night_complete:`。新增领域事件时先问：**哪一步把它送进耐久流？**

## 前端架构

- **状态管理**（`frontend/src/store/gameStore.ts`，Zustand 5）：同时处理直播模式（WebSocket 实时事件）与回放模式（HTTP 全量日志 + 播放/暂停、逐帧步进、0.5x~8x 倍速、按事件类型筛选）。倍速只作用于已落盘事件；直播下一格仍要等当前座位的模型调用结束。对局级 `modelSnapshot` 在详情/观众快照加载时写入，回放 seek 不改写。
- **座位图**（`frontend/src/components/game/SeatMap.tsx`）：椭圆/双列布局；悬停弹出血月风格详情卡（身份、存活/发言、所用模型名与提供方）。公开 DTO 带 `is_sheriff`；仅 `enable_sheriff` 开局才会出现警长徽标。时间轴展示竞选当选与交徽/撕徽事件。
- **WebSocket**（`frontend/src/api/websocket.ts`）：自定义 hook，建立连接后将 JSON 消息路由到 Zustand store 对应处理函数
- **主题**：深色主题「血月剧场」（Crimson Gothic）；设计令牌唯一来源为 `frontend/src/theme/tokens.ts`，组件禁止硬编码色值；风格稿见 `frontend/design-demos/`
- **大厅与评测隔离**：`GET /api/games` 只返回 `benchmark_run_id` 为空且 `source != benchmark` 的普通对局；评测局仍可通过 `GET /api/games/{id}` 回放。大厅用扁平文件夹（`GET/POST /api/folders`、`PUT /api/games/{id}/folder`、`POST /api/games/batch-move|batch-delete`）收纳与批量删除。评测对局只从评测页进出：`GET /api/benchmarks/{id}/games` 并上名称/阶段/胜负等投影；`DELETE /api/benchmarks/{id}/games/{game_id}`、`POST .../games/batch-delete` 先解绑再删档；`DELETE /api/benchmarks/{id}` 取消进行中的 run 并级联删除绑定对局与报告。大厅独立 `DELETE /api/games/{id}` 对评测局仍返回 409。

## 关键设计约束

- **隐私边界**：公开 DTO 与前端消费链不含任何私有字段（`role_init / visible_to / night_intel / check_results / has_antidote / has_poison / has_gun` 等）；观众可见的 `night_thought.reasoning`、狼聊与狼票是上帝视角公开事件，私有 `thought` 模板不进入 audience 表。有隐私扫描测试保障；角色 Hook 函数体零状态访问
- **测试门禁**：后端 pytest 全量（`tests` + `app/` 内嵌测试）+ `--cov-fail-under=100`（statement/branch）。守卫样例证明新增角色不必改核心模块；`test_guard_extension.py` **不再**校验五个核心文件的 blob 哈希。
- **核心源码门禁**：修改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py` 后需同步更新禁词/角色名测试：`tests/test_game_engine.py`、`tests/test_action_resolver.py`、`tests/test_prompt_builder.py`、`tests/test_prompt_renderer.py`。
