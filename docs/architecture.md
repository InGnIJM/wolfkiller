# 系统架构

本文面向想理解或修改代码的开发者，描述 Wolf Killer 的模块划分与核心设计。AI 编码助手使用的同类说明见根目录 `CLAUDE.md`，两者描述同一套架构；修改架构后请同步更新这两处。

## 总览

```
┌─────────────┐   REST/WS    ┌──────────────────────────────────────────┐
│   前端       │ ◄──────────► │  FastAPI (backend/app/main.py)           │
│  React+MUI  │              │  EventBus · WSManager · MemoryService    │
└─────────────┘              │  GameService                             │
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
│   │   ├── roles/               # 角色声明式 spec + 纯 Hook（狼人/女巫/预言家/猎人/平民/守卫）
│   │   ├── api/                 # REST 路由（routes/）+ WebSocket 处理（websocket/）
│   │   ├── persistence/         # SQLite repository、检查点编解码、引擎恢复
│   │   ├── services/            # 游戏/benchmark 服务、公开投影、派生任务
│   │   └── stores/              # 模型配置持久化 + API Key 加密存储
│   └── tests/                   # pytest 测试（statement/branch 100% 覆盖门禁）
├── frontend/
│   └── src/
│       ├── api/                 # REST 客户端 + WebSocket hook
│       ├── store/               # Zustand 状态管理 + 时间轴回放引擎 + 模型配置 store
│       ├── theme/               # 设计令牌（tokens.ts 为色值唯一来源）
│       └── components/
│           ├── lobby/           # 大厅（普通对局列表、扁平文件夹）
│           ├── benchmarks/      # 评测任务列表、新建与详情（含评测对局表）
│           ├── create/          # 创建游戏向导（人数身份 → 模型配置）
│           ├── game/            # 游戏（座位图、时间轴、历史面板、胜利画面）
│           ├── models/          # 模型配置页与编辑对话框
│           └── shared/          # 共享组件（头像、角色图标、发言气泡）
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
| `core/scheduler.py` | 调度点（NIGHT_ACTION/NIGHT_COMMIT/DAWN_REACTION 等）、稳定排序、请求收集、响应窗口队列与阶段门禁；`point_journal.py` 提供断点续跑检查点 |
| `core/night_settlement.py` | 夜晚结算：pending damage/protection → 死亡批次 |
| `agents/prompt_renderer.py` | 仅从 RoleSpec/Contract/Context 渲染通用 Prompt（历史以 Base64 不可执行注入） |
| `roles/{werewolf,witch,seer,hunter,villager,guard}.py` | 内置角色：声明式 spec + 纯 Hook（`*_applicable` / `validate_*` / `resolve_*`） |

辅助模块：

- `core/role_pipeline.py` — 流水线运行器（`RolePipeline.run_point`），引擎按调度点调用
- `core/night_flow.py` — 狼队夜间讨论/投票的 Prompt 与 schema 构造（`build_briefing` 等）
- `core/vote_service.py` — 可信投票域服务：原子、幂等的终局选票收据（accepted / voluntary-abstain / technical-abstain）
- `core/conversation_log.py` — 全部对话记录及按角色过滤的视图
- `core/state_transaction.py` — 状态事务与 revision 管理

### 夜晚流程

`GameEngine._execute_night()`（分阶段执行 `_execute_staged_night()`）依次运行 `NIGHT_ACTION` 调度点（各角色发出命令）与 `NIGHT_COMMIT`（结算伤害、响应窗口触发猎人开枪等），随后 `_resume_pipeline_night()` 以分阶段检查点发布死亡、判定胜负、推进阶段。所有阶段点均持久化检查点，失败后精确续跑不重放。

### 放逐反应

引擎放逐玩家后，将合成的 PLAYER_DIED 提交注入 `DAWN_REACTION` 调度点的响应队列，让猎人等响应契约通过流水线反应。

## 白天生命周期

- 白天发言、投票、平票复投、遗言是引擎内与角色无关的行为，经 `BaseRole`（`roles/base.py`）调用 LLM：发言走 tool calling 两层防线 + 字数校验 + 兜底；投票走三级重试梯子（strict tool 90s → JSON 压缩上下文 120s → JSON 强格式短重试 30s，`LLM_ACTION_FINAL_RETRY_TIMEOUT_SECONDS`），全部失败才显式技术弃票，并按并发上限（`VOTE_CONCURRENCY`，默认 5）执行
- 投票通过纯校验器验证，以 `EffectApplier` 的 ACCEPT_ACTION 记录（唯一写入口）；阶段超时的缺票席位统一转换为显式技术弃票后再结算
- 状态机（`core/state_machine.py`）以 `(current_phase, event, next_phase)` 三元组表驱动：

```
WAITING → ROLE_DEAL → NIGHT → DAWN → LAST_WORDS → SPEECH → VOTE_CASTING → VOTE_RESOLUTION
                                                                    ↑              ↓
                                                              NIGHT ←──────────────┘
```

- 胜负判定（`core/rule_engine.py`）实现屠边规则与「狼刀在先」语义

## LLM 交互层

- `agents/llm_client.py` + `agents/providers/` — 提供方抽象，按 provider profile 决定传输协议、strict tool 端点、超时与重试策略：
  - `registry.py`：显式 `provider_profile` 或按 Base URL 域名（`api.openai.com / api.deepseek.com / openrouter.ai / api.anthropic.com`）解析 `ProviderProfile`；未识别域名回退 `custom-openai`，Anthropic 兼容中转站需显式选 `custom-anthropic`
  - `transports.py`：按 `api_mode` 选择传输——`openai_compatible.py`（`ChatOpenAI`，Chat Completions）或 `anthropic_messages.py`（`ChatAnthropic`，Messages API；去掉 Base URL 尾部 `/v1`、temperature 截断到 0~1、无 strict endpoint）
  - `base.py`：`CallPurpose`、`ProviderProfile`、`call_budget()`（两种传输共用的 token/超时预算）
  - `errors.py`：跨 openai/anthropic SDK 的错误分类元组；`llm_client.py` 再导出，`roles/base.py` 只从 `llm_client` 导入（core/roles 不得 import providers，见 `test_architecture_boundary.py`）
  - `LLMClient` 对上层 API 不变：`_structured_response()` / `_content_text()` 同时兼容 OpenAI 字符串内容 / `finish_reason` 与 Anthropic 内容块列表（text、thinking、reasoning）/ `stop_reason`；Anthropic 强制工具绑定 `tool_choice="any"`（thinking 模式下点名工具会被 400）；`map_strict_capability_error()` 沿异常 cause 链识别 SDK 400/422；`aclose()` 同时关闭 `ChatOpenAI` 与 `ChatAnthropic` 的 SDK 客户端
- `agents/prompt_builder.py` / `agents/state_filter.py` 是**委托外壳**：动作提示委托 `PromptRenderer`，角色视图委托 `ContextProjector.project_view()`；两者源码不含任何内置角色名（有测试门禁）。公开规则与系统提示写明本局无警长/警徽/竞选；渲染给 LLM 的事实会去掉未实现的 `sheriff` 字段
- `agents/output_parser.py` 解析 LLM 返回的 JSON 与 tool call；`parse_tool_call()` 优先原生 function calling，失败回退正则匹配文本模式

## 模型配置与角色目录

- `catalog.py` + `api/routes/catalog_routes.py`：向前端暴露角色目录、标准预设与人数约束
- `stores/model_config_store.py` + `stores/model_key_crypto.py` + `api/routes/model_routes.py`：模型 API 配置 CRUD 与 API Key 加密存储（响应中 Key 仅脱敏返回），含连通性测试端点
- `GameService` 在启动引擎前完整解析 `model_assignments`，按数量独立洗牌得到 `seat → LLMClientConfig`；每座创建并复用一个客户端。角色工厂、Scheduler 和 NightDirector 都以行为发起座位路由，因此白天、夜晚与失败重试不会串用模型
- `GET /api/games/{id}` 与 `GET /api/games/{id}/snapshot` 向观众暴露无密钥的 v2 `model_snapshot`（`name / model_id / provider_profile / seats`）；旧档缺少 `seats` 的条目会被滤掉而不是 500。前端座位图悬停卡按 `seats` 反查模型，不把 `model_id` 写入玩家 DTO 或隐私白名单
- 分配只引用创建时物化的配置快照；之后编辑或删除 `models.json` 中的配置不会改变进行中的对局
- 前端：`components/create/CreateGameWizard.tsx` 两步向导；`ModelStep.tsx` 以数量分配环境默认与已存配置；`components/models/ModelConfigPage.tsx` 管理页；`store/modelConfigStore.ts`

## 持久化与存档

- **事实源**：`backend/data/wolfkiller.sqlite3` 保存对局、版本化检查点、领域事件、公开事件/快照、模型请求/尝试、运行时计时、benchmark 计划/条目/报告、大厅扁平文件夹（`game_folders` / `game_folder_items`）与派生任务。schema 版本 2 起支持增量迁移；`game_folder_items.game_id` 不外键到 `games`，以便 JSONL 旧档也能归档。外键开启，写入由单写线程串行化；每次规则步骤以 `BEGIN IMMEDIATE` 事务同时提交检查点、事件、消费的模型请求和派生任务，`step_key` 与 digest 提供幂等冲突检测。
- **WAL**：连接使用 SQLite WAL，`synchronous=FULL`，并设置 5 秒 busy timeout。WAL 提升并发读取能力，但 `-wal` 不是独立备份；运行时只复制主 `.sqlite3` 文件可能漏掉尚未 checkpoint 的已提交事务。
- **兼容数据**：`GameLogger` 的 JSONL 日志、`GameManifest` 的 `games/index.json`、对话日志和逐座位记忆仍服务于旧格式/审计链。新运行时的恢复判断以 SQLite 检查点及其 SHA-256 digest 为准，不能用旧 JSON 文件覆盖数据库事实。
- **启动恢复**：进程启动会把原先 `running` 的执行标为 `interrupted`，把 `in_flight` 模型尝试标为 `unknown`；不会假定外部模型请求未执行。只有可恢复且版本兼容的对局/benchmark 才能通过显式 resume 继续，恢复重试也受持久化次数约束。
- **快照版本化**：检查点包含 pipeline、registry、spec/effect schema 与编排状态；缺少规范/迁移器或 digest 不匹配时拒绝恢复。模型快照不含 API Key；旧快照缺 `count/seats` 时保持未知，不反推座位映射。

### 公开事件同步

公开视图由领域事件白名单投影。观众可见的夜晚思考（`night_thought`：守卫/女巫/预言家/猎人的 `reasoning`）、狼人队内发言（`wolf_chat_message`）与狼票（`wolf_vote`）会进入 audience 表；私有 `thought` 模板、夜间情报与身份资源字段仍被剥离。状态快照带其 `seq` 和 `projection_version`；增量页使用 `after_seq`（排他游标）、`next_seq`、`high_watermark` 和 `has_more`。客户端先取得快照，从该 `seq` 之后分页追到一个固定的 `high_watermark`；下一轮再取新的 watermark。`through_seq` 可把一次追赶固定在同一上界，避免持续写入导致永远翻不完。WebSocket 只用于低延迟提示，断线重连始终用耐久游标补齐；游标大于服务端 watermark 会明确报错，客户端应重新取快照，而不是静默跳过事件。

## 前端架构

- **状态管理**（`frontend/src/store/gameStore.ts`，Zustand 5）：同时处理直播模式（WebSocket 实时事件）与回放模式（HTTP 全量日志 + 播放/暂停、逐帧步进、0.5x~8x 倍速、按事件类型筛选）。对局级 `modelSnapshot` 在详情/观众快照加载时写入，回放 seek 不改写。
- **座位图**（`frontend/src/components/game/SeatMap.tsx`）：椭圆/双列布局；悬停弹出血月风格详情卡（身份、存活/警长/发言、所用模型名与提供方）
- **WebSocket**（`frontend/src/api/websocket.ts`）：自定义 hook，建立连接后将 JSON 消息路由到 Zustand store 对应处理函数
- **主题**：深色主题「血月剧场」（Crimson Gothic）；设计令牌唯一来源为 `frontend/src/theme/tokens.ts`，组件禁止硬编码色值；风格稿见 `frontend/design-demos/`
- **大厅与评测隔离**：`GET /api/games` 只返回 `benchmark_run_id` 为空且 `source != benchmark` 的普通对局；评测局仍可通过 `GET /api/games/{id}` 回放。大厅用扁平文件夹（`GET/POST /api/folders`、`PUT /api/games/{id}/folder`、`POST /api/games/batch-move|batch-delete`）收纳与批量删除。评测对局只从评测页进出：`GET /api/benchmarks/{id}/games` 并上名称/阶段/胜负等投影；`DELETE /api/benchmarks/{id}/games/{game_id}`、`POST .../games/batch-delete` 先解绑再删档；`DELETE /api/benchmarks/{id}` 取消进行中的 run 并级联删除绑定对局与报告。大厅独立 `DELETE /api/games/{id}` 对评测局仍返回 409。

## 关键设计约束

- **隐私边界**：公开 DTO 与前端消费链不含任何私有字段（`role_init / visible_to / night_intel / check_results / has_antidote / has_poison / has_gun` 等）；观众可见的 `night_thought.reasoning`、狼聊与狼票是上帝视角公开事件，私有 `thought` 模板不进入 audience 表。有隐私扫描测试保障；角色 Hook 函数体零状态访问
- **测试门禁**：后端 pytest 全量 + `--cov-fail-under=100`（statement/branch）；守卫样例证明五个核心模块 blob 不变即可扩展新角色
- **核心源码门禁**：修改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py` 后需同步更新 `tests/test_guard_extension.py` 中的 `CORE_BLOBS_BEFORE_GUARD`
