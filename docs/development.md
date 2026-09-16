# 开发指南

本文面向参与开发本项目的工程师：环境变量、本地开发、测试与门禁、数据存储位置和已知的坑。

## 环境变量

完整定义见 `backend/app/config.py`（读取环境变量的默认值以源码为准）。在 `backend/` 目录创建 `.env` 生效；**`.env` 已被 `backend/.gitignore` 忽略、未提交到仓库，请勿提交**（含真实 API Key）。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 空 | LLM API Key（必配） |
| `LLM_PROVIDER` | `deepseek` | 提供方标识（决定 provider profile） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | 常规请求端点 |
| `DEEPSEEK_STRICT_BASE_URL` | `https://api.deepseek.com/beta` | strict tool 请求端点（仅当 provider 声明 `strict_tools` 时使用） |
| `LLM_MODEL` | 无 | 环境默认模型；未配置且 `LLM_MODELS` 无有效值时回退 `deepseek-v4-pro` |
| `LLM_MODELS` | 空 | **已弃用的兼容变量**；只使用首个非空值（优先于 `LLM_MODEL`），配置多个值时每个进程仅警告一次 |
| `LLM_TEMPERATURE` | `1.2` | 常规请求温度（行动请求固定为 0.1） |
| `LLM_MAX_TOKENS` | `768` | 常规请求 token 上限 |
| `LLM_ACTION_MAX_TOKENS` | `2048` | 行动请求 token 上限 |
| `LLM_ACTION_TIMEOUT_SECONDS` | `90` | 行动请求主超时 |
| `LLM_ACTION_RETRY_TIMEOUT_SECONDS` | `120` | 行动请求重试超时 |
| `LLM_ACTION_FINAL_RETRY_TIMEOUT_SECONDS` | `30` | 投票 JSON 强格式短重试超时 |
| `LLM_PROVIDER_MAX_RETRIES` | `4` | SDK 对瞬时 429/5xx 的重试次数 |
| `VOTE_CONCURRENCY` | `5` | 投票并发上限 |
| `VOTE_PHASE_TIMEOUT_SECONDS` | `500` | 投票阶段总超时 |
| `ROLE_PIPELINE_V2` | `v1` | **不改变对局运行时**。仅 `pipeline_mode_from_env()` / `RolePipeline` 适配器测试读取；`GameEngine` 固定走 `PipelineMode.V2` |
| `WOLFKILLER_DATA_DIR` | `data` | SQLite、游戏日志、模型配置的数据根目录 |
| `MODEL_CONFIG_PATH` | `{WOLFKILLER_DATA_DIR}/models.json` | 显式覆盖模型配置文件路径 |
| `WOLFKILLER_API_URL` | `http://127.0.0.1:8000` | benchmark CLI 连接的后端地址 |
| `DEBUG` | `false` | 调试模式 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

`.env` 推荐最小配置：

```ini
DEEPSEEK_API_KEY=sk-your-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_TEMPERATURE=1.2
DEBUG=true
LOG_LEVEL=INFO
```

多模型不再通过 `LLM_MODELS` 逗号列表随机分配。请在「模型管理」保存配置，并在创建向导中为环境默认和各个已存配置填写人数；后端校验数量总和后独立随机落座，同一座位在发言、投票、狼人讨论、夜间行动和重试中始终复用同一客户端。

## 常用命令

### 后端

```bash
cd backend
pip install -r requirements.txt                              # 安装依赖
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload   # 启动（开发模式）
python -m pytest tests app -q                                # 全部测试（含 app/ 内嵌测试）
python -m pytest tests app --cov=app --cov-branch --cov-fail-under=100 -q   # 覆盖率门禁
python scripts/run_benchmark.py list                         # 查询已启动服务中的 benchmark
```

后端运行在 `http://localhost:8000`，OpenAPI 文档在 `http://localhost:8000/docs`（REST API 以此自动生成文档为准，仓库内不再手工维护接口表）。

后端目前是本机应用，没有远程账户鉴权：HTTP 与 WebSocket 只接受回环地址连接及本机 Host，浏览器来源限于 localhost / 127.0.0.1 / [::1] 的 5173、4173、8000 端口或后端同源页面。即使监听 `0.0.0.0`，远程连接也会被拒绝。自定义开发端口需同步调整 `app/api/local_access.py` 的来源列表；不要用通配 CORS 代替访问控制。

修改已存密钥的模型端点时必须重新填写 Key，防止原密钥被自动发送到新地址。模型连通性测试在后台线程执行并关闭客户端，不阻塞对局事件循环。

### 前端

```bash
cd frontend
npm install        # 安装依赖
npm run dev        # 启动（localhost:5173）
npm test           # Vitest
npm run test:coverage   # 覆盖率（见下文门禁说明）
npm run test:e2e   # Playwright 浏览器验收
npm run build      # 类型检查 + 生产构建
npm run lint       # ESLint
```

## 测试与门禁

测试数量随代码演进变化，以 `pytest` / `npm test` 实际收集数和 CI 为准，不要把某个日期的用例个数写进门禁说明。

门禁一览：

| 门禁 | 内容 | 状态 |
| --- | --- | --- |
| 后端覆盖率 | `tests` + `app` 全量，statement/branch 100%（`--cov-fail-under=100`） | 生效（CI） |
| 前端覆盖率 | `vite.config.ts` 对 9 个核心文件（gameStore、benchmarkStore、websocket、GameBoard、TimelineController、HistoryPanel、WinOverlay、GameList、GameCard）要求 statements/branches/functions/lines 均 100% | 生效（CI `npm run test:coverage`） |
| 隐私扫描 | 公开 DTO 与前端消费链不得含私有字段（`role_init`、`visible_to`、`night_intel`、`check_results`、`has_antidote`、`has_poison`、`has_gun` 等） | 生效（测试门禁） |
| 核心源码门禁 | 引擎/校验器/解析器/提示外壳的禁词与角色名测试，证明核心不写死内置角色分支 | 生效（测试门禁） |
| Benchmark 工具链 | 对局质量评测（见 `docs/benchmark.md`） | 工具链，非门禁 |
| CI 浏览器 E2E | Playwright 使用临时目录和无真实 Key 的 FastAPI，验证生命周期、进程恢复、断网重连、时间线、benchmark UI 和旧存档 | 生效 |

### 新增角色

参照守卫样例 `backend/app/roles/guard.py`：声明式 spec + 纯 Hook（`*_applicable` / `validate_*` / `resolve_*`），不需要改动任何核心模块。在 `roles/registry.py` 注册后补充对应 `tests/test_*_pipeline.py`。注意：修改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py` 后需同步更新禁词测试（`tests/test_game_engine.py`、`tests/test_action_resolver.py`、`tests/test_prompt_builder.py`、`tests/test_prompt_renderer.py`）。**不要**再去改 `test_guard_extension.py` 里已经不存在的 `CORE_BLOBS_BEFORE_GUARD`。

### Benchmark 基准评测

采集层随对局事务性落入 SQLite；批量真实对局评测用仓库内的 `backend/scripts/run_benchmark.py` 连接已经运行的 FastAPI 服务。脚本自身不会创建 `GameService`，服务不可达时会明确失败。对局结束时服务会自动写 `summary.json`。指标定义与用法见 `docs/benchmark.md`。最近一次本机跑批结论见 [notes/2026-09-16-mimo-mixed-arena.md](notes/2026-09-16-mimo-mixed-arena.md)。`.gitignore` 目前只放行 `run_benchmark.py`，其它 `backend/scripts/*` 不在版本控制中。

## 数据存储

- 耐久事实库：`backend/data/wolfkiller.sqlite3`；SQLite 使用 WAL 与 `synchronous=FULL`，同目录可能出现 `wolfkiller.sqlite3-wal` / `wolfkiller.sqlite3-shm`
- 游戏数据：`backend/data/games/<game_id>/`（JSONL 日志 `game.log`、LLM 对话 `conversation.log`、角色记忆 `memories/`）；`game.log` 的无密钥 `model_assignment` 记录可用于重建模型分配
- 游戏清单：`backend/data/games/index.json`（重启后恢复游戏列表并校验流水线版本兼容性）；新对局以 `model_snapshot_version: 2` 保存按配置分组的 `count` 与 `seats`
- 模型配置：`backend/data/models.json`（API Key 加密存储）
- 设置 `WOLFKILLER_DATA_DIR` 后，默认模型配置随数据目录迁移；`MODEL_CONFIG_PATH` 可显式覆盖模型配置路径。测试同时隔离两个路径，不使用真实 Key。
- **禁止删除正在进行的游戏数据，否则会导致游戏中断**

旧存档不会虚构或迁移座位映射：快照缺少 `model_snapshot_version: 2`、`count` 或 `seats` 时，汇总中的 `model_assignment_known` 为 `false`。

### SQLite 备份与手动恢复

运行中备份应使用 SQLite 在线备份命令，它会生成单个一致文件并包含当时 WAL 中已提交的数据：

```bash
cd backend
sqlite3 data/wolfkiller.sqlite3 ".backup 'data/backups/wolfkiller-2026-09-06.sqlite3'"
sqlite3 data/backups/wolfkiller-2026-09-06.sqlite3 "PRAGMA integrity_check;"
```

若没有 `sqlite3` CLI，请先正常停止后端，确认进程退出，再整体复制 `wolfkiller.sqlite3`、`wolfkiller.sqlite3-wal` 和 `wolfkiller.sqlite3-shm`。不能在服务运行时只复制主数据库文件，也不要把 `-wal` 文件单独当备份。

手动恢复没有自动回滚按钮：先停止后端，把当前 `data/` 完整移到隔离目录留作取证；再将已通过 `PRAGMA integrity_check` 的备份复制为 `data/wolfkiller.sqlite3`，删除的仅应是由旧库遗留且已确认不配套的 `-wal/-shm`，随后用相同或兼容的新版本启动。启动后检查 `/api/health`、对局/benchmark 状态和日志；恢复点上处于 `running` 的任务会变为 `interrupted`，确认模型配置仍可用后再显式 resume。不要直接编辑表或把旧 JSON 清单覆盖到 SQLite。

### CI

`.github/workflows/ci.yml` 分别运行后端 pytest 与覆盖率门禁、前端 Vitest/lint/build，以及 `npm run test:e2e`。后端测试通过 `conftest.py` 将数据和模型配置隔离到临时目录，并清空真实 Key。Playwright 启动独立前后端进程，以真实 API 验证生命周期、进程恢复和观战同步；benchmark 页面交互使用受控 API 响应。浏览器失败时 CI 上传 trace 等产物，保留 7 天。工作流不注入真实 API Key。

## 已知的坑

### WSL 下验证前端（挂载 `/mnt/e`）

- 在 WSL 中对 Windows 挂载路径直接跑 vitest/eslint/build，worker 会在 ~60s 超时（"Timeout waiting for worker to respond"）。做法：把 frontend（src + 配置 + lockfile）拷到 ext4 镜像目录（如 `/tmp/wk-verify`），在那里 `npm ci` 并执行命令。
- 在挂载路径上执行过 `npm install` 后，用 `git checkout -- package.json package-lock.json` 还原——npm 会把 CRLF 改写成 LF 并丢弃 `libc` 字段，污染 diff。

### 接入 Anthropic / Anthropic 兼容中转

- 官方地址 `https://api.anthropic.com` 会被自动识别为 `anthropic` profile；第三方中转站域名无法识别，必须在「模型配置 → 接口协议」显式选择「自定义 · Anthropic 兼容」，否则会按 OpenAI Chat Completions 协议请求。
- Base URL **不要**带 `/v1`（SDK 自行拼接 `/v1/messages`）；即便填了也会被 `normalize_base_url()` 去掉尾部 `/v1`。
- Messages API 的 temperature 仅接受 0~1，`.env` 默认 `LLM_TEMPERATURE=1.2` 会被截断为 1.0；行动请求固定 0.1 不受影响。
- Anthropic profile 的 `strict_tools=False`：夜间行动/投票走「强制工具 + JSON 降级」。强制方式是 `tool_choice="any"`，不能点名某个工具——thinking（含 DeepSeek V4 Flash 一类默认开 thinking 的中转）会以 400 `Thinking mode does not support this tool_choice` 拒绝 named `tool_choice`。`strict_base_url` 对该协议无意义。
- 投票 JSON 降级与夜晚 `_structured_response` 都必须经 `_content_text()` 展平 Anthropic content 块（含 `thinking` / `reasoning`）；直接判断 `isinstance(content, str)` 会把合法块列表误判成 `action_response_not_text`。
- 依赖：`langchain-anthropic>=1.0.0`（连带 `anthropic>=1.x`，其异常类与 openai SDK 不同，请统一通过 `app.agents.llm_client` 再导出的错误元组捕获，不要在游戏代码中直接 import 任一 SDK）。

### LLM 行动请求失败模式

- 行动请求（夜间行动、投票）与常规请求走不同的 token/超时预算（见上表）；只有当 provider profile 声明 `strict_tools` 时才尝试强制工具调用，否则直接用 JSON。
- 投票阶段超时后，缺票席位统一转换为**显式技术弃票**再结算，不存在静默缺票；提供方失败会持久化失败类别（不含响应/推理文本），详见 `MEMORY.md` 中的触发-行动清单。

### 杂项

- 后端要求 Python >= 3.11；前端要求 Node.js >= 20.19（Vite 8 要求 `^20.19.0 || >=22.12.0`）。
