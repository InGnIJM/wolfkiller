# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. Cursor / Codex / Gemini 请同时遵循根目录 [`AGENTS.md`](AGENTS.md)；两处描述同一套命令、架构与门禁。

面向人类的详细文档在 `docs/`（[索引](docs/README.md)）：游戏规则 `docs/gameplay.md`、系统架构 `docs/architecture.md`、开发指南与踩坑 `docs/development.md`。现行评测结论见 `docs/notes/2026-09-16-mimo-mixed-arena.md`。本文与 `docs/architecture.md` 描述同一套架构，改动后需同步 `AGENTS.md` / `CLAUDE.md` / `docs/architecture.md`。

## 项目概述

Wolf Killer 是一个完全由 LLM 驱动的 AI 狼人杀游戏。所有玩家（狼人、村民、预言家、女巫、猎人，十人局可选的守卫，十二人板可选的白痴、白狼王、狼王、骑士、狼美人、老酒鬼、恶夜骑士、摄梦人、石像鬼、守墓人、纯白之女、狼巫、猎魔人、赤月使徒、噩梦之影、舞者与假面）均由创建对局时配置的大语言模型控制，无需真人参与。前端提供基于时间轴的观看/回放界面。

## 常用命令

### 后端

```bash
cd backend
pip install -r requirements.txt            # 安装依赖
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload   # 启动后端（开发模式）
python -m pytest tests app -q              # 运行所有测试（含 app/ 内嵌测试目录）
python -m pytest tests/test_xxx.py -q      # 运行单个测试文件
python -m pytest tests app --cov=app --cov-branch -q   # 全量测试 + 覆盖率（覆盖率门禁 --cov-fail-under=100 要求 100% statement/branch 覆盖）
```

### 前端

```bash
cd frontend
npm install                           # 安装依赖
npm run dev                           # 启动前端 (localhost:5173)
npm run build                         # 类型检查 + 生产构建
npm test                              # Vitest（数量随代码演进，以实际运行为准）
npm run lint                          # ESLint 检查
npm run test:e2e                      # Playwright 浏览器验收
```

## 核心架构

### 后端游戏流程

入口 `backend/app/main.py`，启动时获取 `ProcessLock`、打开 `GameRepository`（并把仍标记 `running` 的执行标为 `interrupted`），再创建单例 EventBus、WSManager、MemoryService、GameService、AudienceEventService、BenchmarkGameExecutor、BenchmarkService。所有游戏操作通过 REST API 和 WebSocket 暴露。

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
| `core/scheduler.py` | 调度点（NIGHT_ACTION / NIGHT_WOLF_VOTE / NIGHT_WITCH_ACTION / NIGHT_SEER_ACTION / NIGHT_COMMIT / DAWN_REACTION / DAY_ACTION / POST_SPEECH_ACTION / EXILE_VERDICT 等）、稳定排序、请求收集、响应窗口队列与阶段门禁；`run_point(..., slot=)` 允许同一阶段多次运行同一调度点（`PointKey.phase` 为 `phase#slot`）；聚合按 `contract_id` 分组，跨角色完全一致的共享契约合并计票；`point_journal.py` 提供断点续跑检查点 |
| `core/night_settlement.py` | 夜晚结算：pending damage/protection → 死亡批次；保护分档、按死因豁免的免疫、延迟档（`hunt` 为无条件延迟）、舞池裁决 `settle_pool()`；`PROTECTION_SOURCES` 是写入口共用的唯一来源清单 |
| `agents/prompt_renderer.py` | 仅从 RoleSpec/Contract/Context 渲染通用 Prompt（历史以 Base64 不可执行注入） |
| `roles/{werewolf,witch,seer,hunter,villager,guard,idiot,werewolf_king,wolf_king,evil_knight,knight,wolf_beauty,old_drunkard,dream_weaver,gargoyle,gravekeeper,pure_white,wolf_witch,demon_hunter,crimson_envoy,nightmare,dancer,mask,hidden_wolf}.py` | 内置角色：声明式 spec + 纯 Hook（`*_applicable` / `validate_*` / `resolve_*` / `react_*`）；白狼王与狼美人复用狼人导出的 `WEREWOLF_KILL_CONTRACT`，白痴与狼美人殉情是仅 `react` 的无 LLM 契约，老酒鬼只有声明式标记（`charm_immune` / `delayable`）无契约 |

- **夜晚流程**：`GameEngine._execute_night()` 分阶段执行（`_execute_staged_night()`），调度点顺序为 `NIGHT_ACTION` → 狼队讨论/投票 → `NIGHT_WOLF_VOTE` → `NIGHT_SEER_ACTION` → `NIGHT_WITCH_ACTION` → `NIGHT_COMMIT`（结算伤害、响应窗口触发猎人开枪等；**查验点在用药点之前**，见恶夜骑士条），随后 `_resume_pipeline_night()` 以分阶段检查点发布死亡、判定胜负、推进阶段。开警长时进入 `SHERIFF_ELECTION`（`SheriffDirector`，`core/sheriff_flow.py`）：上警/退水/警长投票的决策提示注入本人身份与阵营、私有事实（查验、药）、已上警与候选人名单、未上警（警下）选民名单及本轮警上发言摘录；**交/撕徽提示词同样走 `_context_block`**（它从前是唯一不带上下的警长提示词，预言家因此在不知道自己刚验出金水的情况下撕徽，全场据此把金水读成查杀，规则里「预言家可用交徽方向传递查验结果」落不了地）；模型给出的决策理由（工具 schema 必填的 `reasoning`）由 `badge_reasoning()` 取出（strip + 截断 500），经引擎一次性槽位 `_pending_badge_reasoning` 交给 `sheriff_badge:` 步骤，进 `SHERIFF_BADGE` 观众事件的可选字段 `reasoning`（旧档缺省，`_AUDIENCE_ACTION_OPTIONAL` 与 DTO 按可选处理），前端显示为「警长思考」——**只进观众流，不进 `conversation_log` 公开记录与玩家提示词**，槽位是内存态、label 形状不变（在跑的对局仍可续跑）；警徽流失时按原因（无人上警 / 全员退水 / 全员上警无选民 / 平票）播报（`badge_loss_message`）。`ConversationLog` 在 `create_new()`/恢复时保留对象身份（仅清空或由编解码器替换记录）， director 绑定始终有效；每轮警长投票完成后以 PUBLIC `phase=sheriff_ballot` 记录发布完整票型（`ConversationLog.add_sheriff_ballot`），PK 可见首轮、同轮互不见未完成票，票型进入白天/重试/夜间历史。狼玩家的警长投票提示只有 `vote_turn` 把 `wolf=wolf` 传进 `vote_prompt`/`_context_block` 才会带狼队成员名单与本夜狼队频道；这一行曾漏传（`vote_prompt(wolf=True)` 的提示词单测因此全绿，而真局里狼的警长票提示词 0/46 带队友名单，狼在警长票上不认识队友、22/97 张狼票投给了真预言家），现由 `test_vote_turn_carries_the_team_block_into_the_ballot` 走真实 `vote_turn` 路径把关。狼队讨论与逐票由 `NightDirector`（`core/night_flow.py`）驱动。
- **白天流水线窗口**：每位发言者开口前跑 `DAY_ACTION`（`slot=f"{vote_round}:{seat}"`），提交事件含 `DAY_INTERRUPTED` 时 `_resolve_day_interruption()` 结算双死、按 `death_history` 去重发布、以这些 PLAYER_DIED 跑 `DAWN_REACTION`、写 `day_interrupted:{round}:{seat}` 检查点并以 `WEREWOLF_EXPLODED` 转 NIGHT（续跑由 `_journaled_day_interruption()` 幂等重放）；放逐前 `_apply_exile()` 以 `EXILE_PENDING` 跑 `EXILE_VERDICT`，提交事件含 `EXILE_CANCELLED` 时写 `revealed_role`、记 `vote_result exiled=None` 并跳过遗言。引擎只认这些通用事件名，不出现角色名
- **发言后窗口**：全体存活玩家发言完毕、放逐投票前跑 `POST_SPEECH_ACTION`（`slot="post_speech"`，`_post_speech_action_enabled()` 门禁），骑士翻牌决斗在此发动；窗口跑完先 `_settle_and_publish()`（白天伤害立即生效）再 `_resolve_delayed_deaths()`（老酒鬼延迟死亡落地），最后才 `SPEECHES_COMPLETE`
- **放逐反应**：引擎放逐玩家后，将合成的 PLAYER_DIED 提交注入 `DAWN_REACTION` 调度点的响应队列，让猎人、狼美人殉情等响应契约通过流水线反应；猎人 `_SHOOT_REASONS` 只有 `wolf_kill` 与 `exile`（官方卡牌「**当且仅当**被狼人出局或被投票放逐」），被自爆/狼王爪/殉情带走的猎人都不能开枪
- **白天发言/投票**：引擎内角色无关路径，经 `BaseRole`（`roles/base.py`）调用 LLM；投票通过纯校验器验证并以 `EffectApplier` 的 ACCEPT_ACTION 记录（唯一写入口）；投票资格读 `runtime.statuses`（`core/vote_service.py`：`no_vote` 不进选民、`exile_immune` 不进候选），狼队按 `camp == Camp.WEREWOLF` 识别（夜间狼窝名单——讨论/投票/频道/队友名单——另按 `core/wolf_pack.in_wolf_pack` 判定 = 该阵营减去 `no_wolf_pack` 的隐狼）
- **断点续跑**：调度点、夜晚批次、死亡发布、阶段推进、遗言认领（`last_words_pending:`，在调用 LLM 之前落盘）均有持久检查点，失败后精确续跑不重放

### 白天阶段与规则

**状态机 (`core/state_machine.py`)** 管理阶段转换，使用 `(current_phase, event, next_phase)` 三元组表：

```
WAITING → ROLE_DEAL → NIGHT → [SHERIFF_ELECTION] → DAWN → LAST_WORDS → SPEECH → VOTE_CASTING → VOTE_RESOLUTION
                                                                    ↑              ↓
                                                              NIGHT ←──────────────┘
```

平票时进入补充发言 + 复投（`vote_round=2`），再次平票则无人被放逐。**复投只能投并列候选人**：`vote_service.eligible_exile_targets()` 在 `is_tiebreak` 时收窄为「并列候选人 ∩ 存活 ∩ 未被免于放逐」，投给其他座位判无效；交集为空即全员弃权，按「再次平票」处理。

**规则引擎 (`core/rule_engine.py`)** 实现屠边规则，关键语义：「狼刀在先」——神职含守卫、白痴、骑士，老酒鬼是平民；狼人数（含白狼王、狼美人）大于好人数也算狼人胜；双方同时满足时狼人优先。神职/平民归属由 `RoleSpec.tags`（`god`/`villager`）决定，不按角色名子串匹配。

### 角色 LLM 交互（白天路径）

- `roles/base.py` 的 `BaseRole` 处理发言与投票，两者都走 **`agents/retry.py` 的分类重试梯子**、全部失败才兜底。发言（4 次，`_SPEECH_RETRY_POLICY`）：transport 故障原样重发、不可用答复（没调工具 / 空 / <15 字 / 用错函数）带 repair 提示重问、超时类失败不等退避；只有预算耗尽才给模板发言。投票（3 次）：strict tool → JSON 降级 → JSON 强格式末次重试，全部失败才显式技术弃票。**`_action_timeout_seconds()` 以第一次尝试的预算为地板**，后续尝试只能更宽不能更窄（末次曾是 30s < 首次 90s，慢到需要重试的 provider 恰恰最容易被它砍掉）。旧实现只给发言两次、第二次还是无条件「加强格式」提示，且空/过短/用错函数**一次都不重试**直接落模板 —— 真局里出现过 275 条发言 268 条是模板。**发言落兜底模板时必须带 `failure_code`**（`provider_unavailable` / `provider_timeout` / `request_timeout`+`timeout_type` / `speech_*`）：这一格从前是空的，「上游没号了」与「模型不肯答」事后完全无法区分。
- **故障码只有一份词表**：`agents/providers/errors.py: classify_provider_error()` 是白天投票（`roles/base._provider_failure_code`）、夜间动作（`game_service._model_failure_code`）与耐久账本（`ModelInvocationService._failure_code`）共用的唯一分类器 —— 从前三处各写一份，同一个 HTTP 503 在白天叫 `provider_server_error`、在夜间与账本里叫 `model_invocation_error`。判定顺序是**超时类名 → 状态码 → SDK 类**：状态码是事实（网关用哪个类回 429/503 不重要，429 就是限流、503 就是容量拒绝 `provider_unavailable`）。**503 走单独的拒绝规则**（`retry_plan` 里 `is_refused_error()`）：给一次长等待（`refused_delay_seconds`，默认 20s；有 `Retry-After` 取大者并封顶）后重发一次，**第二次拒绝即终局**，因为它是分钟级的容量决定，半秒级退避等不到恢复；429 不在其列（带提示且重试确实有效）。
- **provider 不认 action tool 只学一次**：`LLMClient.prefers_json_actions`（一局一座位）在工具通道被拒（400/422）或被无视（没吐原生 tool call）时置位，之后的投票与夜间动作直接从 JSON 通道起步。glm-5.3-flash 曾在一局里为此白花 24/30 张票。
- `agents/prompt_builder.py` / `agents/state_filter.py` 是**委托外壳**：动作提示委托 `PromptRenderer`，角色视图委托 `ContextProjector.project_view()`；两者源码不含任何内置角色名（有测试门禁）。关警长时提示词省略警长词；开警长时仅注入 `SHERIFF_GAME_RULES`，并在权威状态里给出警上/警下名单（`office.candidates` 与 `off_badge_seats`：开选时存活且从未上警，排除开选前死亡）与固定事实约束，竞选期另给出竞选发言顺序与已发言/尚未发言名单，防止模型把警下玩家说成警上。只允许提示里写明的规则，禁止模型用其他版本补流程。
- `agents/output_parser.py` 解析 LLM 返回的 JSON 与 tool call；`parse_tool_call()` 优先原生 function calling，失败回退正则匹配文本模式

### 模型配置与角色目录

- `catalog.py` + `api/routes/catalog_routes.py`：向前端暴露角色目录（roles）、标准预设（presets）与角色人数约束（constraints）；角色条目另带 `rules`（玩家向规则条目）。它是展示元数据，刻意留在冻结的 `RoleSpec` 之外（新增角色只写 `roles/*.py` + 这里的 `name_zh` / `rules`，`registry.digest` 不变）；规则介绍页 `components/rules/RulesPage.tsx`（右上角入口，路由 `/rules`）只读这三条公开目录接口，叙述文案在 `components/rules/ruleSections.ts`
- **提供方接口层 `agents/providers/`**：`registry.py` 按显式 `provider_profile` 或 Base URL 域名解析 `ProviderProfile`（`openai / deepseek / openrouter / opencode / opencode-go / custom-openai` 走 `api_mode=chat_completions`，`anthropic / custom-anthropic` 走 `api_mode=anthropic_messages`，`openai-responses` 走 `api_mode=openai_responses`）；OpenCode 的 Zen 与 Go 同域不同路径（`opencode.ai/zen` 与 `opencode.ai/zen/go`），按路径前缀区分且先匹配更长的前缀；`transports.py` 按 `api_mode` 选择 `OpenAICompatibleTransport`（`ChatOpenAI`）、`OpenAIResponsesTransport`（同一个 builder，只多传 `use_responses_api=True`）或 `AnthropicMessagesTransport`（`ChatAnthropic`，自动去掉 Base URL 尾部 `/v1`、temperature 截断到 0~1、不使用 strict endpoint）；`base.py` 的 `call_budget()` 统一两种传输的 token/超时预算；`errors.py` 提供跨 SDK 的错误分类元组（`CAPABILITY_REJECTION_ERRORS / RATE_LIMIT_ERRORS / SERVER_ERRORS / TIMEOUT_ERRORS / TRANSIENT_PROVIDER_ERRORS`），由 `llm_client.py` 再导出供 `roles/base.py` 使用（core/roles 禁止直接 import providers，有架构边界测试）。工具定义统一用 OpenAI function 格式，`ChatAnthropic.bind_tools` 自行转换。Anthropic 强制工具用 `tool_choice="any"`（thinking 兼容；点名工具会 400），`_content_text()` 展平 text/thinking/reasoning 块供 JSON 降级与投票解析；`map_strict_capability_error()` 沿 `__cause__` 识别被 LangChain 包装的 400
- **Responses 方言（`/responses`）**：部分网关只把模型挂在 Responses API 上，同一域名打 `/chat/completions` 会返回 **HTTP 500**（OpenCode Zen 的 `muse-spark-1.3-contributor-free` 即是）。`base.py` 的 `is_responses_endpoint()` 认路径后缀，`ProviderRegistry._apply_endpoint_dialect()` 用 `replace()` 把命中的 profile 切成 `openai_responses` 并关掉 strict（照搬参考客户端对 `@ai-sdk/openai` 模型 `strict:!1` 的做法）——**只换方言、保留该 provider 自己的 headers 与能力**，所以 Zen 的会话头不会丢。`strip_responses_suffix()` 在交给 SDK 前去掉尾部 `/responses`，否则会 POST 到 `/responses/responses`。因此用户把官网模型表里的 URL（`https://opencode.ai/zen/v1/responses`）原样粘进来即可，`openai-responses` 显式 profile 只在 URL 不带该后缀时才需要。Responses 的输出块被 LangChain 标准化为 `{"type":"text","text":...}`，`_content_text()` 与原生 tool_calls、`usage_metadata` 均无需改动；`finish_reason` 在该方言下为 `None`，但该字段只被存储、从不参与分支判断
- **自定义请求头**：`ModelConfig.headers`（`dict[str, str]`）→ `LLMClientConfig.headers`（**tuple of pairs**，冻结配置要当缓存键，必须可哈希）→ transport 的 `default_headers`。优先级后者覆盖前者：profile 默认头 → 自动生成的会话头 → 用户配置头。`base.py` 的 `header_error()` 是唯一校验入口（RFC 7230 token 名、值禁 CR/LF/NUL、`RESERVED_HEADER_NAMES` 里的 SDK/网关保留头一律拒绝），API 校验器与 transport 共用。`opencode / opencode-go` 两个 profile 声明 `session_header="x-opencode-session"`，值由 transport 实例 `__init__` 用 `new_session_id()`（`ses_` + 26 位十六进制）生成——一个 `LLMClient` 一个 transport，因此天然是**座位级稳定**且互不相同的会话 ID，用户可在 headers 里手填覆盖。**headers 不进 `_MODEL_PARAMETER_FIELDS`、不进 model_snapshot、不进 game.log**：它可能携带中转站凭据，续跑时与 `api_key` 一样从活配置读取，因此新增 headers 不改变 `parameters_digest`，旧检查点仍可续跑
- `stores/model_config_store.py` + `stores/model_key_crypto.py` + `api/routes/model_routes.py`：模型 API 配置 CRUD 与 API Key 加密存储（响应中 Key 仅脱敏返回），含连通性测试端点；`POST /api/models/test` 带 `config_id` 时，请求里显式给出的每个可编辑字段（`base_url` / `model_id` / `strict_base_url` / `api_key` / `provider_profile` / `headers`）都覆盖已存配置、传 `None` 才沿用存储值（`strict_base_url` 传空串表示清空），前端「测试通过才允许保存」的门禁依赖这一点
- `GameService` 将环境默认与已存配置按数量独立随机落座，并为每个座位创建一个稳定客户端；角色工厂、Scheduler、`NightDirector`（`core/night_flow.py`）及其重试都按行为发起座位路由
- `GET /api/games/{id}` 与观众 `GET /api/games/{id}/snapshot` 返回无密钥 v2 `model_snapshot`；前端 `SeatMap` 悬停卡按座位展示所用模型（旧档无 `seats` 时显示「未知」），不把模型字段打进 `PublicPlayerState`
- 前端：`components/create/CreateGameWizard.tsx` 两步向导（人数身份配置 → 多模型数量分配）；`components/models/ModelConfigPage.tsx` 管理页（`ModelConfigDialog.tsx` 含「接口协议」选择器，选项清单在 `providerProfiles.ts`，需与后端 `model_schemas.ProviderProfileId` 同步）；`store/modelConfigStore.ts`
- **应用设置（`stores/app_settings_store.py` + `data/settings.json`）**：应用级操作开关，与模型配置分开存（`models.json` 存 `api_key_encrypted`，混进去还会让设置项以「一条模型配置」的身份出现在 `GET /api/models`）。落盘 `{"version": 1, "settings": {...}}`，四种损坏回落「未设置」+ 告警，写盘 tmp + `os.replace`，**永不对调用方抛异常**；**不做通用 KV**（每个设置一对类型化方法）。现有唯一开关是**训练语料采集**：`GET/PATCH /api/settings/prompt-capture`（`api/settings_schemas.py` + `api/routes/settings_routes.py`，响应闭集 `enabled` + `source`，严格布尔，`null` = 清除界面设置），裁决链只有一份实现 —— `core/prompt_capture.py` 的 `resolve_capture_setting(stored)` = UI > `PROMPT_CAPTURE_ENABLED` > 默认（开），路由与 `GameService` 各有一处调用并互相断言。前端 `store/appSettingsStore.ts` + `components/settings/SettingsPage.tsx`（AppBar「设置」→ `/settings`），与模型配置 UI 一样不进覆盖率白名单但有自己的测试

### 大厅与评测隔离

- `GET /api/games` 只列出普通对局（`benchmark_run_id is None` 且 `source != "benchmark"`）；`GET /api/games/{id}` 仍可打开评测回放
- 大厅扁平文件夹：`GET/POST /api/folders`、`PATCH/DELETE /api/folders/{id}`（删夹只解散成员）、`PUT /api/games/{id}/folder`、`POST /api/games/batch-move` / `batch-delete`（上限 100，逐条部分失败）
- 评测页：`GET /api/benchmarks/{id}/games` 并上名称/阶段/胜负等投影；`DELETE /api/benchmarks/{id}/games/{game_id}` 与 `POST .../games/batch-delete` 先解绑 `benchmark_items` 再 soft-delete；`DELETE /api/benchmarks/{id}` 先 cancel 再级联删报告、条目与绑定对局
- 大厅 `DELETE /api/games/{id}` 对评测局继续 409 `game_referenced_by_benchmark`

### 持久化与存档

- **事实源**：`backend/data/wolfkiller.sqlite3` 保存对局、检查点、公开事件、模型请求与 benchmark；JSONL 不能覆盖数据库事实
- **游戏日志**：`GameLogger` 以 JSONL 写 `backend/data/games/<id>/game.log`，启动前记录无密钥的 `model_assignment`；`GameManifest` 维护 `index.json`，索引损坏时可从日志恢复模型座位映射
- **记忆系统**：`MemoryService` 每个角色一个 JSON 文件（`memories/seat_N_<role>.json`）
- **快照版本化**：`GameState` 携带 `pipeline_version / registry_digest / spec_versions / effect_schema_version / state_revision / last_consistent_checkpoint`；`game_manifest.restore_snapshot()` 校验兼容性（缺规范/迁移器、V2 回滚到 V1 均抛 `SnapshotVersionError`），旧档经显式 v1→v2 迁移器读取
- **模型快照版本化**：新档以 `model_snapshot_version: 2` 保存按配置分组的 `count/seats`；旧快照不补写座位，summary 以 `model_assignment_known: false` 标记分配未知

### 前端架构

**状态管理 (`frontend/src/store/gameStore.ts`)** 是前端的核心，使用 Zustand 5。它同时处理：
- 直播模式：通过 WebSocket 实时接收游戏事件并更新状态
- 回放模式：从 HTTP 加载完整游戏日志，支持播放/暂停、逐帧步进、0.5x~8x 速度调节、按事件类型筛选
- 对局级 `modelSnapshot`：从详情或观众快照加载，供座位悬停卡按座位反查模型；回放 seek 不改写

**座位图 (`frontend/src/components/game/SeatMap.tsx`)** 渲染椭圆/双列座位；悬停弹出详情卡（身份、存活状态、所用模型名 / model_id / 提供方）。公开 DTO 带 `is_sheriff`；仅开启 `enable_sheriff` 的对局才会出现警长。

**WebSocket (`frontend/src/api/websocket.ts`)** 自定义 hook，建立 WebSocket 连接后将 JSON 消息路由到 Zustand store 对应的处理函数。

**主题 (`frontend/src/theme.ts`)** 定义了完整的 MUI 深色主题「血月剧场」（Crimson Gothic）：血红 `#C22E42` × 鎏金 `#D4A853` 双主轴、衬线字体栈（Cinzel + Noto Serif SC）、金色发丝线分隔。设计令牌唯一来源为 `frontend/src/theme/tokens.ts`，组件禁止硬编码色值；风格稿见 `frontend/design-demos/`。

### 关键设计细节

- **隐私边界**：公开 DTO 与前端消费链不含任何私有字段（`role_init / visible_to / night_intel / check_results / has_antidote / has_poison / has_gun` 等）；观众可见的 `night_thought.reasoning`、狼聊与狼票是上帝视角公开事件，私有 `thought` 模板不进入 audience 表。有隐私扫描测试保障；角色 Hook 函数体零状态访问
- **状态过滤**：`state_filter.py` 委托 `ContextProjector` 返回冻结投影的安全纯数据副本，狼人看不到好人专属信息（反之亦然）
- **发言顺序**：存活玩家从"最近死亡玩家的下一位存活玩家"开始按座位号依次发言（`game_engine.py` 的 `_execute_speech_round`；无死亡记录时回退为最小存活座位开局）；平票复投时排除断点续跑中已完成补充发言的座位，LLM 玩家需要知晓当前发言进度（由 `prompt_builder.py` 注入轮次上下文）
- **测试门禁**：后端 pytest 全量（`tests` + `app/`）+ statement/branch 100%（`--cov-fail-under=100`），数量以实际运行为准；守卫 / 白痴 / 白狼王 / 骑士 / 狼美人 / 老酒鬼六个扩展样例（`test_guard_extension.py` / `test_idiot_extension.py` / `test_werewolf_king_extension.py` / `test_knight_extension.py` / `test_wolf_beauty_extension.py` / `test_old_drunkard_extension.py`）；`test_catalog_routes.py` 断言 23 角色 15 预设，并要求每个已注册角色都有非空 `rules` 规则条目（新增角色漏写展示规则即红）；阶段 3 与 3.5 的扩展样例：`test_pure_white_extension.py` / `test_wolf_witch_extension.py` / `test_demon_hunter_extension.py` / `test_crimson_envoy_extension.py` / `test_nightmare_extension.py` / `test_masquerade_extension.py`。`test_guard_extension.py` **不再**维护 `CORE_BLOBS_BEFORE_GUARD`。

## 注意事项

- `backend/.env` 含 API key，已被 `backend/.gitignore` 忽略、**未提交到仓库**；保持该状态，不要提交或推送
- 游戏数据存储在 `backend/data/`：SQLite 事实库 `wolfkiller.sqlite3`，以及 `games/<id>/` 下的 JSONL 日志和角色记忆。训练语料采集写 `games/<id>/llm_prompts.jsonl`，应用设置写 `data/settings.json`（两者都被 `backend/.gitignore` 的 `data/*` 覆盖）
- **训练语料采集开关的三条红线**：(a) 开关**不进** `GameConfig` / checkpoint / `PointKey` / `_MODEL_PARAMETER_FIELDS`（它是进程配置，不是对局状态）；(b) 生效时机是**下一局** —— `GameService._latch_capture_request()` 只在建局时解析一次开关，**恢复路径不解析**，`games/<id>/llm_prompts.jsonl` 的存在与否就是那个耐久闩锁（所以关掉开关后正在跑的对局仍会写到自己结束，这是故意的，不是 bug）；(c) `/api/settings/prompt-capture` 的响应是**闭集**（只有 `enabled` + `source`），不得携带 prompt 内容 / `game_id` / 文件路径，请求体是严格布尔（`"yes"` / `1` → 422）
- 后端 Python 需要 >= 3.11；前端 Node.js >= 20.19
- 禁止删除 `data/` 目录下正在进行的游戏数据，否则会导致游戏中断。运行时备份请用 SQLite `.backup`，不要只拷主 `.sqlite3` 文件
- 环境默认 LLM 配置在 `backend/.env`（含 API key、model、temperature 等），已存模型由模型管理页维护；`LLM_MODELS` 仅兼容首个非空值且已弃用
- 「不能连续两晚同一人」**按回合比较**：守卫 / 噩梦之影 / 假面 / 狼美人 / 摄梦人的目标记忆由 `roles/target_memory.py` 统一写入 `{"seat","round"}`，`is_consecutive_repeat()` 只在 `round == 当前夜-1` 时判连续（裸 `int` 旧档按老语义读）。旧实现只记座位号，被跳过的一夜（模型失败 / `pass` / 被封印）不清状态：禁令型角色拒掉合法选择并连丢两夜，摄梦人则把隔一夜的重复读成「连续两晚」而杀死本该保护的座位。
- 守卫：不能连续两晚守同一人；守护只抵消狼刀，毒药与猎枪无视守卫；守卫守护与女巫解药同时作用于同一狼刀目标时目标仍死亡（对穿）
- 白痴：被放逐时翻牌，公开身份、不死、失去投票权、不再能被放逐，仍可发言；夜刀/毒/枪/自爆带走时正常死亡；计入神职
- 白狼王：夜晚与狼队共享 `werewolf_kill` 契约；白天 SPEECH 阶段每位发言前可自爆带走一人，两人均无遗言，当日发言与投票取消直接入夜；被毒/放逐/枪杀时不能带人
- 骑士：全体发言结束后、放逐投票前（`POST_SPEECH_ACTION`）翻牌决斗一人；狼人则立即死亡、**在进入夜晚前发表遗言**并直接入夜，好人则骑士以死谢罪、无遗言、投票照常；一局一次。两种死亡共用死因 `knight_duel`，遗言只发给被裁决者（中断路径传 `daytime=True`，夜间遗言路径不传）
- 狼美人：与狼队共享狼刀；狼刀投票后可魅惑一名好人（不能连续两晚同一人、不能自指/魅狼队友/魅免疫者）；出局时被魅惑者殉情（死因 `charm`），但**只有被放逐与被猎人射击两种死因才发动**（官方卡牌只列这两种：`_REVENGE_REASONS == {"exile", "hunter_shot"}`；被夜刀、被毒、被狼王爪带走、被骑士决斗都不带走人），不能自爆；自刀由 `WOLF_BEAUTY_SPEC` 声明的 `self_kill_forbidden` 资源 + `validate_werewolf_action()` 拒绝（狼刀契约按阵营共享、必须字节一致，能力差异只能走资源标记，禁止在核心模块写角色名），狼队讨论/投票提示词对声明该标记的座位改写成「不能投自己」（`NightDirector._self_kill_clause()`），否则模型会白丢一票；被魅惑带走的猎人不能开枪
- 狼王：与狼队共享狼刀；出局时可用一次「狼王爪」带走一名存活玩家（死因 `wolf_king_shot`）。可发动的出局方式只有夜间被狼刀、被放逐、被猎人带走，**自爆/被毒/被殉情/被骑士决斗都不能发动**。不翻牌；被带走的猎人不能开枪、被带走的狼美人不发动殉情。一局一次。**`wolf-killer-wolf-king`（狼王）与 `wolf-killer-werewolf-king`（白狼王）只差词序，不许简写。**
- 恶夜骑士：狼人阵营。**无法在夜间出局**（`night_immune` 标记，按死因豁免 `hunter_shot` / `knight_duel` / `self_explode`，且按伤害类别分开判 —— 同批既挨刀又挨枪时枪生效）；不能自爆、不能自刀；一次性反伤走 `core/reflect.py`（生产者侧读 `selected_target.resource_labels`，`reflect_used` + `resource_equals` 去重，`selected_target` 不进 prompt）；**查验点排在用药点之前**，所以同夜同验同毒时预言家先出局、女巫不会出局。狼队不能选它为刀口：共享狼刀契约是 `aggregate`、不能读 per-target facts，改由 `NightDirector._night_immune_clause()` 提示词点名 + 结算免疫兜底。旧称「恶灵骑士」。
- 摄梦人：好人神职。每晚必须选一名自己以外的存活玩家为梦游者；梦游者免疫一切夜间伤害（`source="dream"` 走 any-damage 档，能挡毒）、本人不知情；连续两晚同一人则该人第二夜出局（死因 `dream_death`）；摄梦人夜间出局时当夜梦游者同亡（`DAWN_REACTION` 的 react 契约，排除白天死因）。`pass` 只是引擎安全阀（`registry.py:203` 禁止 fallback 动作要求目标），「不可放弃」靠提示词强制。
- 石像鬼：狼人阵营但不属于狼窝（`no_wolf_pack`）；不参与狼队讨论与狼刀投票、不与其他狼互知身份、不进狼频道、不能自爆吞警徽；狼窝全灭后靠 `hidden_wolf_kill` 契约独自带刀——**该契约与狼刀同在 `NIGHT_WOLF_VOTE`，命令提供方必须按「是否狼窝席位」（`in_wolf_pack`）而不是按调度点分流**（`GameService._pack_ballot_point`）：按调度点早退会让石像鬼每次都拿到兜底 `pass`，带刀权成为死代码，同时狼窝被噩梦之影封印所依赖的「取不到票即 pass」也会被破坏。每晚查验一名存活玩家的具体身份（新增 `selected_target` 命名空间 `role_label`，只投影给纯 hook、不进模型 prompt），结果记入私有事实 `role_checks`。被预言家查验仍显示狼人。
- 守墓人：好人神职，**零契约**。`context_projector._actor_private_facts` 的 `last_exiled` 特例从 `death_history` 推导上一个被放逐者的座位与阵营（不给 `GameState` 加字段，因此不动精确匹配的检查点字段集），首夜无人被放逐时读作 `None`。
- 老酒鬼：好人平民（非神职），免疫魅惑；被毒或枪杀不当天死亡，落 `poisoned`/`wounded` 后于次日发言结束后、投票前死亡；夜刀/放逐/自爆当夜即死
- 纯白之女 / 狼巫（纯白夜影板）：每晚查验一人的**具体身份**，结果记进 `role_checks` 私有事实命名空间（必须由 `context_projector._actor_private_facts` 的 `role_checks` 分支回读，否则查验等于白验——石像鬼的旧缺口正是这条通道没接上）。**第二夜起**命中即当夜处决：纯白之女验中狼人、狼巫验中纯白之女，死因 `verify_kill`（普通伤害，守卫挡不住）。狼巫拒验队友，同夜互验同归于尽
- 猎魔人：`demon_hunt` @ `NIGHT_ACTION` order=40，第 2 夜起可发动；目标为狼则目标次日出局、为好人则自己次日出局，死因 `hunt`。`hunt` 在 `night_settlement._ALWAYS_DELAYED_CAUSES`：**不要求目标自带 `delayable`**（猎魔人加不了标记），无条件延到次日发言结束后落地。女巫毒药无效（`poison_immune` 按死因过滤，且判在延迟档之前）
- 赤月使徒：`crimson_explode` @ `DAY_ACTION`，不带目标、不带走任何人；当批消耗 `explode`、自伤 `self_explode`、对全部存活好人落 `silenced` 并逐座宣告，再发 `CRIMSON_EXPLODE` + `DAY_INTERRUPTED`。封印由调度器执行期门禁生效，只在 `NIGHT_COMMIT` 的 `commit_night` 批次清扫（白天结算不清扫）。最后一个存活狼被放逐时 `crimson_linger`（`EXILE_VERDICT` 纯 react，白痴同款）发 `EXILE_CANCELLED` + `delayed_death` + `lingering_exile`，次日以 `cause="exile"` 出局
- 噩梦之影：`nightmare_fear` @ `NIGHT_ACTION` order=10（早于守卫 50，同点内按 order 串行）；不能连续两晚同一人，可以恐惧自己与狼队友；**恐惧到狼人则整队当夜无法袭击**（引擎 `_pack_sealed()` 跳过讨论并 `record_votes(())`，stage 5 全员降级 `pass`）。首夜按普通狼处理
- 舞者 / 假面（假面舞会，京城大师赛原创板，非网易）：舞者 `dance_pool` @ order=20，`multi_target_actions={"pool": 3}`（fallback 只能是 `pass`），resolve 只落 `in_pool`+`pooled_seats`，裁决由 `night_settlement.settle_pool()` 在结算时读真实 camp 判少数方（舞者看不到阵营）；舞者在池内则三人各得 `dance_veil`（只挡狼刀、不参与对穿）；免疫毒药。假面 `mask_grant` @ order=30（舞者之后），靠 `selected_target.status_labels` 读 `in_pool`（该状态进了 `_PUBLIC_STATUSES`，答案只经 `pool_checks` 私有数据进本人提示词）；面具只写 `masked`、**不改 camp**，仅在舞池比较中折算阵营；不能连续两晚同一目标；免疫毒药
- 夜间开眼旁白由角色声明：`RoleSpec.opening_narration`（`(标题, 正文)` 或空）是唯一来源，引擎在 `NIGHT_ACTION` 跑完后按 `PointResult.requests` 逐个播报（死了 / 未解锁 / 没声明都不播），新增夜间角色不必改引擎；导演表 `_NARRATIONS` 只剩引擎自己的节拍（`wolf_open` / `seer_open` / `pack_sealed`），不要按 `role_counts` 判断角色是否开眼。预言家与女巫两句仍是引擎节拍，但**位置与门禁单独处理**：stage 6 播预言家、stage 8 播女巫（各自紧邻自己的调度点；旧实现两句对调，观众先听「女巫请睁眼」、再在查验结果之后才听「预言家请睁眼」），并各自经 `_night_openers(point)` 按「注册表里在该调度点有契约 且 存活 且 未被 `silenced`」门禁，死了或被封印就不再被叫醒；stage 号与 `_NIGHT_POINTS` 顺序不可动（旧档持久化这些数字）
- 保护来源清单唯一：`night_settlement.PROTECTION_SOURCES`，`effect_applier` 直接 import 它校验 `SUBMIT_PROTECTION(source=...)`。曾两处各自硬编码，摄梦人的 `source="dream"` 在真跑局时被写入口拒掉（hook 级测试全绿、端到端没人跑过）；新增守护档只改这一处，并补写入口 + 结算的端到端测试

- **写入口不变式**：`runtime.statuses` / `runtime.relations` 的值一律是普通 `set`，不能写 `frozenset`（`_set_map` 归一化时两者等价，但 `_apply_one` 对既有值直接 `.add()`，frozenset 会 `AttributeError`）。`ADD_RELATION` 是集合成员关系：**跨批次重复写入是幂等的**（狼美人隔一晚重魅同一人合法，规则只禁连续两晚同一人），但同一批次内重复仍是批次完整性违规。**延迟死亡标记同样跨批次幂等**：同一夜两种延迟死因（女巫毒 + 猎人枪）命中同一 `delayable` 座位时，`settle_pending` 的后续批次整套跳过该标记、保首因、不报错 —— 它曾经抛 `EffectRejected`，而结算没有 fallback，直接把整局打成 `failed` 且恢复会重炸。
- **拒绝的分类**：`EffectConflict`（`EffectRejected` 子类）表示「批次合法、状态不允许」——模型可能合法地选到状态已不允许的目标。模型驱动的契约在 `Scheduler._apply_or_degrade` 遇到它降级为 `fallback_action_type` 并落 `stage_telemetry` 故障行（`code=effect_rejected`），不丢整局；结构性违规（effect id 错序、权限/visibility 越界、revision mismatch、`clone()` 期 runtime 损坏）仍是普通 `EffectRejected`，保持致命。结算（`settle_pending`）与纯 `react` 契约没有 fallback，其拒绝仍致命。
- `registry.digest` 是**对局的冻结身份**：journal 键、请求 token（`window_id`/`action_key`）与角色资源 setup marker 都用建局时的 digest 盖章（`core/registry_identity.py`），所以新增/改角色**不会**让 `interrupted` 的旧局无法续跑。兼容性由 `persistence/recovery_compat.py` 在恢复入口一次性裁决：`exact` / `compatible`（本局没用到的东西变了）/ `drift`（本局用到的契约 digest 变了，或**已经建立过资源**而座位资源声明变了，需 `force=true` 显式确认）/ `incompatible`（角色或契约消失、schema 版本在这局之下变动 → 永久拒绝）。**角色 id 永不删除**，改名要保留别名；改已落座角色的 `initial_resources` 会让**已经建立过资源的**旧局判 `drift`，`force` 续跑只改写 setup marker、不重放任何资源效果（药已用掉就还是用掉、新声明的资源不补发）；从未建立过资源的局（marker 为空，例如首夜前就中断）**不判 `drift`**，由下一个调度点按当前声明建立——缺 marker 表示「还没建立」而不是「变了」；只改 tags/instructions 判不出来（检查点只记 `spec_versions`，不记声明 digest）
- `paused / interrupted / failed / recovery_blocked` 都可 `recover`（`recovery_blocked` 是诊断结论、可重试，修好原因后直接再 `recover`）；`POST /api/games/{id}/recover|resume?force=true` 才能跨 `drift` 续跑，前端收到 409 `confirmation_required` 时弹二次确认。prompt digest 与模型参数变化只记告警（`games/<id>/game.log` 的 `recovery` 审计行，不进公开 DTO），不再拒绝续跑
- `ROLE_PIPELINE_V2` 环境变量不改变对局：`GameEngine` 固定 `PipelineMode.V2`
- 修改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py` 后需同步更新源码门禁测试（注意：不是 `test_guard_extension.py` 的 blob 清单）：`tests/test_game_engine.py` 的引擎禁词测试、`tests/test_action_resolver.py` 与 `tests/test_prompt_builder.py` 的角色名禁词测试、`tests/test_prompt_renderer.py` 的渲染器禁词测试
- 改 `roles/werewolf.py` 的 `validate_werewolf_action()` 或 `core/night_flow.py` 的 `_self_kill_clause()` 后更新 `tests/test_wolf_beauty_extension.py`（自刀被拒 / 队友可切 / 其他狼不受限）与 `tests/test_night_flow.py`（提示词三态 + 常量与角色侧声明一致）；改 `audience_projector.py` 的 `_EVENTS` 后同步四处：`tests/test_audience_projector.py` 的类型全集快照、旧档回放的公开 DTO（`api/schemas.py` 的 `PublicReplayEvent`）与白名单/分支（`api/routes/game_routes.py`）、前端 `components/game/test/eventCoverage.test.tsx` 的期望表、`src/labels.ts` 的 `EVENT_TAGS`；`tests/test_game_routes.py::test_every_public_audience_action_type_has_a_replay_branch` 会遍历投影器声明的每个类型要求回放产出事件，漏分支即红（投影器不声明公开的内部类型登记在 `_INTERNAL_AUDIENCE_ACTION_TYPES`，静默跳过）
- 取值目录（死因 / 胜因 / 阵营 / 阶段）：新增或改名要同步**五处**，缺一处就有门禁变红 —— `app/models/values.py` 的 `DeathCause`/`WinReason`/`WinningCamp`（与 `models/game.py` 的 `GamePhase`）是权威目录；`api/schemas.py` 的公开 DTO 必须等于目录（`tests/test_schemas.py`）；`api/routes/game_routes.py` 的三张旧档白名单必须等于公开 DTO（`tests/test_game_routes.py`）；前端 `store/types.ts` 的联合类型 + `src/labels.ts` 的文案必须覆盖目录里的每个值（后端 `tests/test_values.py::test_frontend_labels_cover_every_declared_value` 直接读前端文案比对，前端 `src/test/labels.test.ts` 断言命名空间非空）。生产代码里的 `cause=` / `reason=` 字面量与各规则子集由 `tests/test_values.py` 扫描校验 —— **目录只做门禁锚点**，不要把它传进检查点或 Effect 载荷（`checkpoint_codec` 与 `effect_applier` 用 `type(value) is not str` 精确校验，枚举成员会被拒）
- 观众事件必须有一步耐久提交：audience 表只由 `GameService._checkpoint_domain_events` 的 label 分支写入，只发射领域事件而没有对应 step label 的事件会静默丢掉（白天窗口用 `day_point:`、旁白用 `narration:`、系统代投用 `vote_technical_abstain:`）；死亡按 label 里的座位集合宣布，别再用死因白名单（`charm` 会被过滤掉）。引擎里只为提交一次的临时槽位（`_pending_point_events` / `_pending_narration`）不进编排状态编解码器（字段集精确匹配，加字段会让旧检查点无法续跑）；也不要改调度点的 slot 形状，`PointKey` 变了对局就不能续跑
- 前端覆盖率只卡 `vite.config.ts` 列出的 10 个文件；渲染面的事件覆盖由 `components/game/test/eventCoverage.test.tsx` 兜底（三个渲染面 × 全部观众事件类型逐项表态，漏 case 即红），新增观众事件必须回来补一行。取值→文案表统一放 `src/labels.ts`（20 个命名空间按渲染语境划分，键类型尽量为联合），`src/test/labels.test.ts` 扫描组件源码，谁再写一张本地 `*_LABELS`/`*_TAGS` 即红
- **降级动作必须自报家门**：引擎替座位提交的兜底动作（`Scheduler._fallback`、`_command_provider` 的两处兜底）与狼票兜底（`NightDirector.wolf_vote_turn`）都带 `degraded=True`，理由用 `app/models/pipeline.py` 的 `DEGRADED_REASONING`（「本轮未提交决策」）与 `DEGRADED_THOUGHT`（「本轮未提交决策（系统代行）」）。旧文案「系统异常，本轮未行动」两头都错：降级不等于故障（3 次校验不过也算降级），而「本轮未行动」读起来是玩家自己决定不动手。**`degraded` 在 `ActionCommand` 上一律 `exclude=True`** —— `stable_digest()` 是 `aggregate_effects` 的排序末位键，进序列化会让两条仅标记不同的兜底命令交换顺序；`WolfVote.degraded` 是显式持久化的（`engine_checkpoint` 的 `wolf_votes`），解码缺键回落 `False`，所以旧检查点原样续跑。17 个 `*_reasoning_effect` hook 在 `command.degraded` 时直接给 `DEGRADED_THOUGHT`、并把 `degraded` 放进 payload；`GameEngine._log_audience_pairs` 见到 `degraded is True` **不写 ConversationLog**（否则下一轮提示词把引擎文案当成「你自己上轮的思考」喂回模型），`WOLF_VOTE` 事件同样带该键。观众侧复用 `night_thought` / `wolf_vote` 并新增可选`degraded` 字段，**五处必须同步**：投影器白名单、`game_routes` 的 `_AUDIENCE_ACTION_OPTIONAL`、回放分支、**`api/schemas.py` 的公开 DTO**（`PublicNightThoughtResponse` / `PublicWolfVoteResponse`；`_PublicResponse` 是 `extra="forbid"`，漏掉这一处就是含降级动作的对局在 `/api/games/{id}/logs` 上 500）、前端三个渲染面渲染成「系统代行」。**durable 通道必须显式带上标记**：`degraded` 是 `exclude=True`，durable 包装用 `model_dump()` 序列化、`model_validate` 还原 —— 直接交 `model_dump()` 会让标记在**生产路径**上消失（写入口照旧计 `game` 配额，猎人枪仍被静默作废）；统一走 `game_service._durable_command_payload()`（只在为真时补键，旧行形状逐字节不变），模型答复里的同名字段由 `_pipeline_command()` 剔除。门禁：`tests/test_degraded_hooks.py`（AST 扫描发现全部 17 个 hook，漏改即红）、`test_degraded_audience.py`、`test_degraded_reasoning.py`、`test_degraded_durable.py`（账本往返 + 真实 `_command_provider` 路径）、`test_game_routes.py::test_every_projected_public_payload_is_accepted_by_the_replay_dto`（投影键含可选键都必须被公开 DTO 接受）；`scripts/audit-degraded-wording.py` 用于把「历史脏数据」与「新代码仍在产生」分开。 **配额与资产是两套账**:`action_counts` 的 `window`/`round` 是「已问过」(降级也算,否则一次故障会在本回合每个槽位再花一次 60s 重问),而 `game` 是「这张牌已兑现」——它和 `role_resources` 是同一件事的第二次记账,所以降级**不写** `game`,否则模型失败一次就会让猎人再也开不了枪(枪还在手上,校验器却一直答 `game_limit_reached`)。标记经 `EffectPermission.degraded` 与 `ActionContext.degraded` 传到写入口(`EffectApplier` → `record_accepted_action`),**不进 ACCEPT_ACTION 载荷**(两个提交点 `action_resolver` / `vote_service` 写同一份四键载荷且精确校验,资源建立点 `role_runtime` 是空载荷并因此被跳过;旧检查点里的提交记录会因多一个键被拒)。注册表因此新增断言:声明 `per_game_limit == 1` 的契约必须消耗资源,让「一次性」永远由资产而非计数兜底。`PROJECTED_CONTEXT` 也带上 `degraded`,因为本人历史里不再记录兜底思考,模型需要知道上一轮不是它答的。门禁:`tests/test_degraded_limits.py`。
