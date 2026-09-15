# Wolf Killer — AI 狼人杀

![License](https://img.shields.io/badge/license-MIT-green) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue) ![Node](https://img.shields.io/badge/Node-20.19%2B-blueviolet)

完全由 LLM 智能体驱动的狼人杀游戏。所有玩家（狼人、平民、预言家、女巫、猎人，以及十人局可选的守卫）均由大语言模型控制，无需真人参与。观众可通过基于时间轴的回放界面观看完整对局；创建游戏时可为环境默认或多个自定义模型分配人数，系统会随机落座并在整局中固定每个座位所用模型。

<p align="center">
  <img src="docs/assets/lobby.png" width="49%" alt="游戏大厅" />
  <img src="docs/assets/replay.png" width="49%" alt="对局回放" />
</p>

## 特性

- **全 AI 对局**：狼人夜谈、夜间行动、白日发言、投票、遗言全部由 LLM 驱动，支持多个模型按人数随机落座且全流程按座位稳定路由
- **时间轴回放**：播放/暂停、逐帧步进、0.5x~8x 倍速、按事件类型筛选的观赛体验
- **通用角色流水线**：夜晚行动由冻结的角色声明 + 纯 Hook + 唯一原子写入口驱动，新增角色无需改动核心模块（守卫样例即验收证明）
- **耐久运行**：SQLite/WAL 原子保存对局检查点、公开事件、模型调用和 benchmark；服务重启后可显式恢复
- **工程化保障**：断点续跑检查点、存档版本化兼容校验、隐私边界扫描、statement/branch 100% 覆盖门禁

## 快速开始

环境要求：Python >= 3.11、Node.js >= 20.19（Vite 8 要求 `^20.19.0 || >=22.12.0`）、至少一个 LLM API Key。默认走 OpenAI 兼容协议（如 [DeepSeek](https://platform.deepseek.com/)）；Anthropic Messages API 与兼容中转需在模型管理里显式选择对应协议。

**1. 配置后端环境变量**：在 `backend/` 下创建 `.env`（完整变量见 [docs/development.md](docs/development.md)）：

```ini
DEEPSEEK_API_KEY=sk-your-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_TEMPERATURE=1.2
DEBUG=true
LOG_LEVEL=INFO
```

**2. 启动后端**：

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

**3. 启动前端**：

```bash
cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173`，点击「创建游戏」，配置人数身份（默认九人标准场：3 狼 3 民 1 预言家 1 女巫 1 猎人；另有带守卫的十人预设）与各模型人数，创建后自动开始。大厅可把对局放进扁平文件夹；评测任务在「模型评测」页，不出现在大厅列表里。

## 使用指南

- **观看回放**：在大厅点击任意已结束的对局，用底部时间轴控制器播放、步进、调速（0.5x~8x）、拖拽进度；右侧历史面板可按事件类型筛选
- **进行中控制**：通过 WebSocket 对运行中的对局发送 `set_speed` / `pause` / `resume` / `skip_phase`
- **模型配置**：大厅的「模型管理」页面集中管理 API 配置（名称、base_url、model、Key 加密存储），支持连通性测试；创建对局时可将环境默认与已存配置混合分配
- **Benchmark**：`run_benchmark.py` 只连接已启动的后端。它先持久化并打印冻结草稿；只有显式传入 `--yes` 才开始真实模型调用：

```bash
cd backend
python scripts/run_benchmark.py --games 20 --model-config-id <id>       # 创建并预览草稿
python scripts/run_benchmark.py --games 20 --model-config-id <id> --yes # 创建、启动并等待
python scripts/run_benchmark.py status <run-id>
python scripts/run_benchmark.py export <run-id> --format markdown -o report.md
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/gameplay.md](docs/gameplay.md) | 游戏规则、角色能力、对局流程、胜负条件 |
| [docs/architecture.md](docs/architecture.md) | 系统架构：模块划分、角色流水线、状态机、持久化 |
| [docs/development.md](docs/development.md) | 开发指南：环境变量全表、测试与门禁、数据存储、踩坑记录 |
| [docs/benchmark.md](docs/benchmark.md) | 耐久 benchmark API/CLI、指标口径与导出 |
| [docs/README.md](docs/README.md) | 文档索引（含历史设计稿归档） |
| [AGENTS.md](AGENTS.md) / [CLAUDE.md](CLAUDE.md) | 编码助手入口：命令、架构速览、测试门禁 |

REST API 由 FastAPI 自动生成文档：启动后端后访问 `http://localhost:8000/docs`。

## 运行测试

```bash
cd backend && python -m pytest tests app -q     # 后端 pytest（含 app/ 内嵌测试；覆盖率门禁见 docs/development.md）
cd frontend && npm test                         # 前端 Vitest
cd frontend && npm run test:e2e                 # Playwright 浏览器验收（需先安装 Chromium）
```

## License

[MIT](LICENSE)
