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
| `LLM_MODEL` | 无 | 单模型；未配置且无 `LLM_MODELS` 时回退到代码内置兜底值 |
| `LLM_MODELS` | 空 | 多模型随机分配（逗号分隔），优先于 `LLM_MODEL` |
| `LLM_TEMPERATURE` | `1.2` | 常规请求温度（行动请求固定为 0.1） |
| `LLM_MAX_TOKENS` | `768` | 常规请求 token 上限 |
| `LLM_ACTION_MAX_TOKENS` | `2048` | 行动请求 token 上限 |
| `LLM_ACTION_TIMEOUT_SECONDS` | `90` | 行动请求主超时 |
| `LLM_ACTION_RETRY_TIMEOUT_SECONDS` | `120` | 行动请求重试超时 |
| `VOTE_CONCURRENCY` | `5` | 投票并发上限 |
| `VOTE_PHASE_TIMEOUT_SECONDS` | `500` | 投票阶段总超时 |
| `ROLE_PIPELINE_V2` | `v1` | 角色流水线模式：`v1 \| shadow \| v2` |
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

## 常用命令

### 后端

```bash
cd backend
pip install -r requirements.txt                              # 安装依赖
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload   # 启动（开发模式）
python -m pytest tests/ -q                                   # 全部测试
python -m pytest tests --cov=app --cov-branch --cov-fail-under=100 -q   # 覆盖率门禁
```

后端运行在 `http://localhost:8000`，OpenAPI 文档在 `http://localhost:8000/docs`（REST API 以此自动生成文档为准，仓库内不再手工维护接口表）。

### 前端

```bash
cd frontend
npm install        # 安装依赖
npm run dev        # 启动（localhost:5173）
npm test           # Vitest
npm run test:coverage   # 覆盖率（见下文门禁说明）
npm run build      # 类型检查 + 生产构建
npm run lint       # ESLint
```

## 测试与门禁

测试数量随代码演进变化（以实际运行为准），截至 2026-08-29：后端 1919 个用例 / 44 个测试文件，前端 184 个用例 / 19 个测试文件。

门禁一览：

| 门禁 | 内容 | 状态 |
| --- | --- | --- |
| 后端覆盖率 | statement/branch 100%（`--cov-fail-under=100`） | 生效 |
| 前端覆盖率 | `vite.config.ts` 对 8 个核心文件（gameStore、websocket、GameBoard、TimelineController、HistoryPanel、WinOverlay、GameList、GameCard）要求 statements/branches/functions/lines 均 100% | 门禁存在，但基线在部分环境下不达标（见下方踩坑） |
| 隐私扫描 | 公开 DTO 与前端消费链不得含私有字段（`role_init`、`visible_to`、`night_intel`、`check_results`、`has_antidote`、`has_poison`、`has_gun` 等） | 生效（测试门禁） |
| 核心源码门禁 | 五个核心模块 blob 不变测试（守卫样例证明扩展性） | 生效（测试门禁） |

### 新增角色

参照守卫样例 `backend/app/roles/guard.py`：声明式 spec + 纯 Hook（`*_applicable` / `validate_*` / `resolve_*`），不需要改动任何核心模块。在 `roles/registry.py` 注册后补充对应 `tests/test_*_pipeline.py`。注意：修改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py` 后需同步更新 `tests/test_guard_extension.py` 中的 `CORE_BLOBS_BEFORE_GUARD`。

## 数据存储

- 游戏数据：`backend/data/games/<game_id>/`（JSONL 日志 `game.log`、LLM 对话 `conversation.log`、角色记忆 `memories/`）
- 游戏清单：`backend/data/games/index.json`（重启后恢复游戏列表并校验流水线版本兼容性）
- 模型配置：`backend/data/models.json`（API Key 加密存储）
- **禁止删除正在进行的游戏数据，否则会导致游戏中断**

## 已知的坑

### WSL 下验证前端（挂载 `/mnt/e`）

- 在 WSL 中对 Windows 挂载路径直接跑 vitest/eslint/build，worker 会在 ~60s 超时（"Timeout waiting for worker to respond"）。做法：把 frontend（src + 配置 + lockfile）拷到 ext4 镜像目录（如 `/tmp/wk-verify`），在那里 `npm ci` 并执行命令。
- 在挂载路径上执行过 `npm install` 后，用 `git checkout -- package.json package-lock.json` 还原——npm 会把 CRLF 改写成 LF 并丢弃 `libc` 字段，污染 diff。

### LLM 行动请求失败模式

- 行动请求（夜间行动、投票）与常规请求走不同的 token/超时预算（见上表）；只有当 provider profile 声明 `strict_tools` 时才尝试强制工具调用，否则直接用 JSON。
- 投票阶段超时后，缺票席位统一转换为**显式技术弃票**再结算，不存在静默缺票；提供方失败会持久化失败类别（不含响应/推理文本），详见 `MEMORY.md` 中的触发-行动清单。

### 杂项

- 后端要求 Python >= 3.11；前端要求 Node.js >= 20.19（Vite 8 要求 `^20.19.0 || >=22.12.0`）。
- `frontend/src/components/layout/` 目前是空目录。
