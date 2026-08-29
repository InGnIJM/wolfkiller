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
                          │ LLM 客户端     │  │ 持久化              │
                          │ agents/       │  │ JSONL 日志/记忆/存档  │
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
│   │   ├── services/            # 游戏服务、记忆持久化、存档清单与版本校验
│   │   └── stores/              # 模型配置持久化 + API Key 加密存储
│   └── tests/                   # pytest 测试（statement/branch 100% 覆盖门禁）
├── frontend/
│   └── src/
│       ├── api/                 # REST 客户端 + WebSocket hook
│       ├── store/               # Zustand 状态管理 + 时间轴回放引擎 + 模型配置 store
│       ├── theme/               # 设计令牌（tokens.ts 为色值唯一来源）
│       └── components/
│           ├── lobby/           # 大厅（游戏列表）
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

- 白天发言、投票、平票复投、遗言是引擎内与角色无关的行为，经 `BaseRole`（`roles/base.py`）调用 LLM：发言走 tool calling 两层防线 + 字数校验 + 兜底；投票走 strict tool → JSON 降级 → 安全 fallback，并按并发上限（`VOTE_CONCURRENCY`，默认 5）执行
- 投票通过纯校验器验证，以 `EffectApplier` 的 ACCEPT_ACTION 记录（唯一写入口）；阶段超时的缺票席位统一转换为显式技术弃票后再结算
- 状态机（`core/state_machine.py`）以 `(current_phase, event, next_phase)` 三元组表驱动：

```
WAITING → ROLE_DEAL → NIGHT → DAWN → LAST_WORDS → SPEECH → VOTE_CASTING → VOTE_RESOLUTION
                                                                    ↑              ↓
                                                              NIGHT ←──────────────┘
```

- 胜负判定（`core/rule_engine.py`）实现屠边规则与「狼刀在先」语义

## LLM 交互层

- `agents/llm_client.py` + `agents/providers/` — 提供方抽象（`base.py` / `openai_compatible.py` / `registry.py`），按 provider profile 决定 strict tool 端点、超时与重试策略
- `agents/prompt_builder.py` / `agents/state_filter.py` 是**委托外壳**：动作提示委托 `PromptRenderer`，角色视图委托 `ContextProjector.project_view()`；两者源码不含任何内置角色名（有测试门禁）
- `agents/output_parser.py` 解析 LLM 返回的 JSON 与 tool call；`parse_tool_call()` 优先原生 function calling，失败回退正则匹配文本模式

## 模型配置与角色目录

- `catalog.py` + `api/routes/catalog_routes.py`：向前端暴露角色目录、标准预设与人数约束
- `stores/model_config_store.py` + `stores/model_key_crypto.py` + `api/routes/model_routes.py`：模型 API 配置 CRUD 与 API Key 加密存储（响应中 Key 仅脱敏返回），含连通性测试端点
- 前端：`components/create/CreateGameWizard.tsx` 两步向导；`components/models/ModelConfigPage.tsx` 管理页；`store/modelConfigStore.ts`

## 持久化与存档

- **游戏日志**：`GameLogger` 以 JSONL 写 `backend/data/games/<id>/game.log`；LLM 对话记录写 `conversation.log`
- **游戏清单**：`GameManifest` 维护 `backend/data/games/index.json`（位于 games 根目录），携带流水线版本信息，重启后可恢复并校验兼容性
- **记忆系统**：`MemoryService` 每个角色一个 JSON 文件（`memories/seat_N_<role>.json`）
- **快照版本化**：`GameState` 携带 `pipeline_version / registry_digest / spec_versions / effect_schema_version / state_revision / last_consistent_checkpoint`；`game_manifest.restore_snapshot()` 校验兼容性（缺规范/迁移器、V2 回滚到 V1 均抛 `SnapshotVersionError`），旧档经显式 v1→v2 迁移器读取

## 前端架构

- **状态管理**（`frontend/src/store/gameStore.ts`，Zustand 5）：同时处理直播模式（WebSocket 实时事件）与回放模式（HTTP 全量日志 + 播放/暂停、逐帧步进、0.5x~8x 倍速、按事件类型筛选）
- **WebSocket**（`frontend/src/api/websocket.ts`）：自定义 hook，建立连接后将 JSON 消息路由到 Zustand store 对应处理函数
- **主题**：深色主题「血月剧场」（Crimson Gothic）；设计令牌唯一来源为 `frontend/src/theme/tokens.ts`，组件禁止硬编码色值；风格稿见 `frontend/design-demos/`

## 关键设计约束

- **隐私边界**：公开 DTO 与前端消费链不含任何私有字段（`role_init / visible_to / night_intel / check_results / has_antidote / has_poison / has_gun` 等），有隐私扫描测试保障；角色 Hook 函数体零状态访问
- **测试门禁**：后端 pytest 全量 + `--cov-fail-under=100`（statement/branch）；守卫样例证明五个核心模块 blob 不变即可扩展新角色
- **核心源码门禁**：修改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py` 后需同步更新 `tests/test_guard_extension.py` 中的 `CORE_BLOBS_BEFORE_GUARD`
