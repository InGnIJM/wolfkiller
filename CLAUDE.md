# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Wolf Killer 是一个完全由 LLM 驱动的 AI 狼人杀游戏。所有玩家（狼人、村民、预言家、女巫、猎人）均由 DeepSeek 大语言模型控制，无需真人参与。前端提供基于时间轴的观看/回放界面。

## 常用命令

### 后端

```bash
cd backend
pip install -r requirements.txt            # 安装依赖
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload   # 启动后端（开发模式）
python -m pytest tests/ -q                 # 运行所有测试
python -m pytest tests/test_xxx.py -q      # 运行单个测试文件
python -m pytest tests --cov=app --cov-branch --cov-fail-under=100 -q   # 全量覆盖率门禁（当前 100%）
```

### 前端

```bash
cd frontend
npm install                           # 安装依赖
npm run dev                           # 启动前端 (localhost:5173)
npm run build                         # 类型检查 + 生产构建
npm test                              # Vitest（当前 162 个测试）
npm run lint                          # ESLint 检查
```

## 核心架构

### 后端游戏流程

入口 `backend/app/main.py`，启动时创建单例 EventBus、WSManager、MemoryService、GameService。所有游戏操作通过 REST API 和 WebSocket 暴露。

**游戏引擎 (`backend/app/core/game_engine.py`)** 是白天的编排器，通过 asyncio 事件循环驱动游戏：

1. `GameService.create_game()` 冻结 `builtin_registry` 快照、构建 `Scheduler`（LLM 命令提供者）并实例化 GameEngine，在 asyncio 任务中启动引擎
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

- **夜晚流程**：`GameEngine._execute_v2_night_batch()` 依次运行 `NIGHT_ACTION`（各角色发出命令）与 `NIGHT_COMMIT`（结算伤害、响应窗口触发猎人开枪等），随后 `_resume_pipeline_night()` 以分阶段检查点发布死亡、判定胜负、推进阶段
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
- `agents/prompt_builder.py` / `agents/state_filter.py` 是**委托外壳**：动作提示委托 `PromptRenderer`，角色视图委托 `ContextProjector.project_view()`；两者源码不含任何内置角色名（有测试门禁）
- `agents/output_parser.py` 解析 LLM 返回的 JSON 与 tool call；`parse_tool_call()` 优先原生 function calling，失败回退正则匹配文本模式

### 模型配置与角色目录

- `catalog.py` + `api/routes/catalog_routes.py`：向前端暴露角色目录（roles）、标准预设（presets）与角色人数约束（constraints）
- `stores/model_config_store.py` + `stores/model_key_crypto.py` + `api/routes/model_routes.py`：模型 API 配置 CRUD 与 API Key 加密存储（响应中 Key 仅脱敏返回），含连通性测试端点
- 前端：`components/create/CreateGameWizard.tsx` 两步向导（人数身份配置 → 整局模型选择，一期整局一个模型，可选「环境默认 .env」或已存配置）；`components/models/ModelConfigPage.tsx` 管理页；`store/modelConfigStore.ts`

### 持久化与存档

- **游戏日志**：`GameLogger` 以 JSONL 写 `backend/data/games/<id>/game.log`；`GameManifest` 维护 `index.json`（重启后可恢复游戏列表）
- **记忆系统**：`MemoryService` 每个角色一个 JSON 文件（`memories/seat_N_<role>.json`）
- **快照版本化**：`GameState` 携带 `pipeline_version / registry_digest / spec_versions / effect_schema_version / state_revision / last_consistent_checkpoint`；`game_manifest.restore_snapshot()` 校验兼容性（缺规范/迁移器、V2 回滚到 V1 均抛 `SnapshotVersionError`），旧档经显式 v1→v2 迁移器读取

### 前端架构

**状态管理 (`frontend/src/store/gameStore.ts`)** 是前端的核心，使用 Zustand 5。它同时处理：
- 直播模式：通过 WebSocket 实时接收游戏事件并更新状态
- 回放模式：从 HTTP 加载完整游戏日志，支持播放/暂停、逐帧步进、0.5x~8x 速度调节、按事件类型筛选

**WebSocket (`frontend/src/api/websocket.ts`)** 自定义 hook，建立 WebSocket 连接后将 JSON 消息路由到 Zustand store 对应的处理函数。

**主题 (`frontend/src/theme.ts`)** 定义了完整的 MUI 深色主题「血月剧场」（Crimson Gothic）：血红 `#C22E42` × 鎏金 `#D4A853` 双主轴、衬线字体栈（Cinzel + Noto Serif SC）、金色发丝线分隔。设计令牌唯一来源为 `frontend/src/theme/tokens.ts`，组件禁止硬编码色值；风格稿见 `frontend/design-demos/`。

### 关键设计细节

- **隐私边界**：公开 DTO 与前端消费链不含任何私有字段（`role_init / visible_to / night_intel / check_results / has_antidote / has_poison / has_gun` 等），有隐私扫描测试保障；角色 Hook 函数体零状态访问
- **状态过滤**：`state_filter.py` 委托 `ContextProjector` 返回冻结投影的安全纯数据副本，狼人看不到好人专属信息（反之亦然）
- **发言顺序**：从死亡玩家左手边开始逆时针发言，LLM 玩家需要知晓当前发言进度（由 `prompt_builder.py` 注入轮次上下文）
- **测试门禁**：后端当前 1902 个测试 + statement/branch 100% 覆盖（`--cov-fail-under=100`）；守卫样例证明五个核心模块 blob 不变即可扩展新角色

## 注意事项

- `backend/.env` 已提交到仓库（含 API key），修改时注意不要推送到公开仓库
- 游戏数据存储在 `backend/data/games/`，每个游戏有独立子目录存放 JSONL 日志和角色记忆
- 后端 Python 需要 >= 3.11
- 前端使用 TypeScript，ESLint 平面配置格式
- 禁止删除 `data/` 目录下正在进行的游戏数据，否则会导致游戏中断
- LLM 配置在 `backend/.env`（含 API key、model、temperature 等），游戏参数在 `backend/app/config.py`
- 修改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py` 后需同步更新 `tests/test_guard_extension.py` 中的 `CORE_BLOBS_BEFORE_GUARD` 与对应源码门禁测试
