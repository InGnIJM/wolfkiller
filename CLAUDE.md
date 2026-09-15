# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

面向人类的详细文档在 `docs/`（[索引](docs/README.md)）：游戏规则 `docs/gameplay.md`、系统架构 `docs/architecture.md`、开发指南与踩坑 `docs/development.md`。本文与 `docs/architecture.md` 描述同一套架构，改动后需同步两处。

## 项目概述

Wolf Killer 是一个完全由 LLM 驱动的 AI 狼人杀游戏。所有玩家（狼人、村民、预言家、女巫、猎人）均由创建对局时配置的大语言模型控制，无需真人参与。前端提供基于时间轴的观看/回放界面。

## 常用命令

### 后端

```bash
cd backend
pip install -r requirements.txt            # 安装依赖
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload   # 启动后端（开发模式）
python -m pytest tests app -q              # 运行所有测试（含 app/ 内嵌测试目录）
python -m pytest tests/test_xxx.py -q      # 运行单个测试文件
python -m pytest tests app --cov=app --cov-branch -q   # 全量测试 + 覆盖率；新增代码需保持 100% 分支覆盖（llm_client 的 env 构造分支为既有未覆盖项）
```

### 前端

```bash
cd frontend
npm install                           # 安装依赖
npm run dev                           # 启动前端 (localhost:5173)
npm run build                         # 类型检查 + 生产构建
npm test                              # Vitest（数量随代码演进，以实际运行为准）
npm run lint                          # ESLint 检查
```

## 核心架构

### 后端游戏流程

入口 `backend/app/main.py`，启动时创建单例 EventBus、WSManager、MemoryService、GameService。所有游戏操作通过 REST API 和 WebSocket 暴露。

**游戏引擎 (`backend/app/core/game_engine.py`)** 是白天的编排器，通过 asyncio 事件循环驱动游戏：

1. `GameService.create_game()` 校验并物化对局级模型数量分配、冻结 `builtin_registry` 快照、构建 `Scheduler`（LLM 命令提供者）并实例化 GameEngine，在 asyncio 任务中启动引擎
2. `GameEngine.start()` 运行完整游戏循环（发放身份 → 夜晚/白天循环 → 游戏结束）
3. 夜晚行动完全交给**通用角色流水线**（见下）；白天发言、投票、平票复投、遗言是引擎内与角色无关的生命周期行为
4. 所有事件通过 `EventBus` 异步发布/订阅，WebSocket 推送给前端

### 通用角色流水线（核心重构成果）

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

- **夜晚流程**：`GameEngine._execute_night()` 分阶段执行（`_execute_staged_night()`），依次运行 `NIGHT_ACTION`（各角色发出命令）与 `NIGHT_COMMIT`（结算伤害、响应窗口触发猎人开枪等），随后 `_resume_pipeline_night()` 以分阶段检查点发布死亡、判定胜负、推进阶段
- **放逐反应**：引擎放逐玩家后，将合成的 PLAYER_DIED 提交注入 `DAWN_REACTION` 调度点的响应队列，让猎人等响应契约通过流水线反应
- **白天发言/投票**：引擎内角色无关路径，经 `BaseRole`（`roles/base.py`）调用 LLM；投票通过纯校验器验证并以 `EffectApplier` 的 ACCEPT_ACTION 记录（唯一写入口）
- **断点续跑**：调度点、夜晚批次、死亡发布、阶段推进均有持久检查点，失败后精确续跑不重放

### 白天阶段与规则

**状态机 (`core/state_machine.py`)** 管理阶段转换，使用 `(current_phase, event, next_phase)` 三元组表：

```
WAITING → ROLE_DEAL → NIGHT → DAWN → LAST_WORDS → SPEECH → VOTE_CASTING → VOTE_RESOLUTION
                                                                    ↑              ↓
                                                              NIGHT ←──────────────┘
```

平票时进入补充发言 + 复投（`vote_round=2`），再次平票则无人被放逐。

**规则引擎 (`core/rule_engine.py`)** 实现屠边规则，关键语义：「狼刀在先」——若双方同时满足胜利条件，狼人阵营优先获胜。

### 角色 LLM 交互（白天路径）

- `roles/base.py` 的 `BaseRole` 处理发言（tool calling 两层防线 + ≥15 字校验 + 兜底）与投票（strict tool → JSON 降级 → 安全 fallback）
- `agents/prompt_builder.py` / `agents/state_filter.py` 是**委托外壳**：动作提示委托 `PromptRenderer`，角色视图委托 `ContextProjector.project_view()`；两者源码不含任何内置角色名（有测试门禁）。公开规则与系统提示写明本局无警长/警徽/竞选；渲染给 LLM 的事实会去掉未实现的 `sheriff` 字段
- `agents/output_parser.py` 解析 LLM 返回的 JSON 与 tool call；`parse_tool_call()` 优先原生 function calling，失败回退正则匹配文本模式

### 模型配置与角色目录

- `catalog.py` + `api/routes/catalog_routes.py`：向前端暴露角色目录（roles）、标准预设（presets）与角色人数约束（constraints）
- **提供方接口层 `agents/providers/`**：`registry.py` 按显式 `provider_profile` 或 Base URL 域名解析 `ProviderProfile`（`openai / deepseek / openrouter / custom-openai` 走 `api_mode=chat_completions`，`anthropic / custom-anthropic` 走 `api_mode=anthropic_messages`）；`transports.py` 按 `api_mode` 选择 `OpenAICompatibleTransport`（`ChatOpenAI`）或 `AnthropicMessagesTransport`（`ChatAnthropic`，自动去掉 Base URL 尾部 `/v1`、temperature 截断到 0~1、不使用 strict endpoint）；`base.py` 的 `call_budget()` 统一两种传输的 token/超时预算；`errors.py` 提供跨 SDK 的错误分类元组（`CAPABILITY_REJECTION_ERRORS / RATE_LIMIT_ERRORS / SERVER_ERRORS / TIMEOUT_ERRORS / TRANSIENT_PROVIDER_ERRORS`），由 `llm_client.py` 再导出供 `roles/base.py` 使用（core/roles 禁止直接 import providers，有架构边界测试）。工具定义统一用 OpenAI function 格式，`ChatAnthropic.bind_tools` 自行转换。Anthropic 强制工具用 `tool_choice="any"`（thinking 兼容；点名工具会 400），`_content_text()` 展平 text/thinking/reasoning 块供 JSON 降级与投票解析；`map_strict_capability_error()` 沿 `__cause__` 识别被 LangChain 包装的 400
- `stores/model_config_store.py` + `stores/model_key_crypto.py` + `api/routes/model_routes.py`：模型 API 配置 CRUD 与 API Key 加密存储（响应中 Key 仅脱敏返回），含连通性测试端点；`POST /api/models/test` 的 `provider_profile` 为可选项，显式传入时覆盖已存配置的 profile（便于保存前测试协议切换）
- `GameService` 将环境默认与已存配置按数量独立随机落座，并为每个座位创建一个稳定客户端；角色工厂、Scheduler、NightDirector 及其重试都按行为发起座位路由
- `GET /api/games/{id}` 与观众 `GET /api/games/{id}/snapshot` 返回无密钥 v2 `model_snapshot`；前端 `SeatMap` 悬停卡按座位展示所用模型（旧档无 `seats` 时显示「未知」），不把模型字段打进 `PublicPlayerState`
- 前端：`components/create/CreateGameWizard.tsx` 两步向导（人数身份配置 → 多模型数量分配）；`components/models/ModelConfigPage.tsx` 管理页（`ModelConfigDialog.tsx` 含「接口协议」选择器，选项清单在 `providerProfiles.ts`，需与后端 `model_schemas.ProviderProfileId` 同步）；`store/modelConfigStore.ts`

### 大厅与评测隔离

- `GET /api/games` 只列出普通对局（`benchmark_run_id is None` 且 `source != "benchmark"`）；`GET /api/games/{id}` 仍可打开评测回放
- 大厅扁平文件夹：`GET/POST /api/folders`、`PATCH/DELETE /api/folders/{id}`（删夹只解散成员）、`PUT /api/games/{id}/folder`、`POST /api/games/batch-move` / `batch-delete`（上限 100，逐条部分失败）
- 评测页：`GET /api/benchmarks/{id}/games` 并上名称/阶段/胜负等投影；`DELETE /api/benchmarks/{id}/games/{game_id}` 与 `POST .../games/batch-delete` 先解绑 `benchmark_items` 再 soft-delete；`DELETE /api/benchmarks/{id}` 先 cancel 再级联删报告、条目与绑定对局
- 大厅 `DELETE /api/games/{id}` 对评测局继续 409 `game_referenced_by_benchmark`

### 持久化与存档

- **游戏日志**：`GameLogger` 以 JSONL 写 `backend/data/games/<id>/game.log`，启动前记录无密钥的 `model_assignment`；`GameManifest` 维护 `index.json`，索引损坏时可从日志恢复模型座位映射
- **记忆系统**：`MemoryService` 每个角色一个 JSON 文件（`memories/seat_N_<role>.json`）
- **快照版本化**：`GameState` 携带 `pipeline_version / registry_digest / spec_versions / effect_schema_version / state_revision / last_consistent_checkpoint`；`game_manifest.restore_snapshot()` 校验兼容性（缺规范/迁移器、V2 回滚到 V1 均抛 `SnapshotVersionError`），旧档经显式 v1→v2 迁移器读取
- **模型快照版本化**：新档以 `model_snapshot_version: 2` 保存按配置分组的 `count/seats`；旧快照不补写座位，summary 以 `model_assignment_known: false` 标记分配未知

### 前端架构

**状态管理 (`frontend/src/store/gameStore.ts`)** 是前端的核心，使用 Zustand 5。它同时处理：
- 直播模式：通过 WebSocket 实时接收游戏事件并更新状态
- 回放模式：从 HTTP 加载完整游戏日志，支持播放/暂停、逐帧步进、0.5x~8x 速度调节、按事件类型筛选
- 对局级 `modelSnapshot`：从详情或观众快照加载，供座位悬停卡按座位反查模型；回放 seek 不改写

**座位图 (`frontend/src/components/game/SeatMap.tsx`)** 渲染椭圆/双列座位；悬停弹出详情卡（身份、存活状态、所用模型名 / model_id / 提供方）。

**WebSocket (`frontend/src/api/websocket.ts`)** 自定义 hook，建立 WebSocket 连接后将 JSON 消息路由到 Zustand store 对应的处理函数。

**主题 (`frontend/src/theme.ts`)** 定义了完整的 MUI 深色主题「血月剧场」（Crimson Gothic）：血红 `#C22E42` × 鎏金 `#D4A853` 双主轴、衬线字体栈（Cinzel + Noto Serif SC）、金色发丝线分隔。设计令牌唯一来源为 `frontend/src/theme/tokens.ts`，组件禁止硬编码色值；风格稿见 `frontend/design-demos/`。

### 关键设计细节

- **隐私边界**：公开 DTO 与前端消费链不含任何私有字段（`role_init / visible_to / night_intel / check_results / has_antidote / has_poison / has_gun` 等）；观众可见的 `night_thought.reasoning`、狼聊与狼票是上帝视角公开事件，私有 `thought` 模板不进入 audience 表。有隐私扫描测试保障；角色 Hook 函数体零状态访问
- **状态过滤**：`state_filter.py` 委托 `ContextProjector` 返回冻结投影的安全纯数据副本，狼人看不到好人专属信息（反之亦然）
- **发言顺序**：存活玩家从"最近死亡玩家的下一位存活玩家"开始按座位号依次发言（`game_engine.py` 的 `_execute_speech_round`；无死亡记录时回退为最小存活座位开局）；平票复投时排除断点续跑中已完成补充发言的座位，LLM 玩家需要知晓当前发言进度（由 `prompt_builder.py` 注入轮次上下文）
- **测试门禁**：后端 pytest 全量 + statement/branch 100% 覆盖（`--cov-fail-under=100`），数量以实际运行为准；守卫样例证明五个核心模块 blob 不变即可扩展新角色

## 注意事项

- `backend/.env` 含 API key，已被 `backend/.gitignore` 忽略、**未提交到仓库**；保持该状态，不要提交或推送
- 游戏数据存储在 `backend/data/games/`，每个游戏有独立子目录存放 JSONL 日志和角色记忆
- 后端 Python 需要 >= 3.11
- 前端使用 TypeScript，ESLint 平面配置格式
- 禁止删除 `data/` 目录下正在进行的游戏数据，否则会导致游戏中断
- 环境默认 LLM 配置在 `backend/.env`（含 API key、model、temperature 等），已存模型由模型管理页维护；`LLM_MODELS` 仅兼容首个非空值且已弃用
- 修改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py` 后需同步更新源码门禁测试（注意：不是 `test_guard_extension.py` 的 blob 清单）：`tests/test_game_engine.py` 的引擎禁词测试、`tests/test_action_resolver.py` 与 `tests/test_prompt_builder.py` 的角色名禁词测试、`tests/test_prompt_renderer.py` 的渲染器禁词测试
