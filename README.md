# Wolf Killer - AI 狼人杀

完全由 LLM 智能体驱动的狼人杀游戏。所有玩家（狼人、平民、预言家、女巫、猎人）均由大语言模型控制，无需真人参与。观众可通过基于时间轴的回放界面观看完整的游戏过程。

## 技术栈

| 层级     | 技术                                   |
| -------- | -------------------------------------- |
| 前端     | React 19 + TypeScript + Vite 8 + MUI 9 |
| 状态管理 | Zustand 5                              |
| 后端     | Python + FastAPI                       |
| AI 框架  | LangChain (langchain-openai)           |
| LLM      | DeepSeek (deepseek-chat)               |
| 实时通信 | WebSocket                              |
| 数据校验 | Pydantic v2                            |

## 项目结构

```
WolfKiller/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── config.py            # 环境变量配置
│   │   ├── models/              # 游戏数据模型 + 冻结流水线核心类型（pipeline.py）
│   │   ├── core/                # 引擎、调度器、效果应用、投影、校验、解析、事件总线、日志
│   │   ├── agents/              # LLM 客户端、提示渲染、输出解析、状态过滤
│   │   ├── roles/               # 角色声明式 spec + 纯 Hook（狼人/女巫/预言家/猎人/平民/守卫样例）
│   │   ├── api/                 # REST API + WebSocket
│   │   └── services/            # 游戏服务、记忆持久化、存档清单与版本校验
│   └── tests/                   # 34 个测试文件，statement/branch 100% 覆盖
├── frontend/
│   └── src/
│       ├── api/                 # REST 客户端 + WebSocket hook
│       ├── store/               # Zustand 状态管理 + 时间轴回放引擎
│       └── components/
│           ├── lobby/           # 大厅（游戏列表、创建游戏）
│           ├── game/            # 游戏（座位图、时间轴、历史面板、胜利画面）
│           └── shared/          # 共享组件（头像、角色图标、发言气泡）
└── README.md
```

## 架构速览

- **通用角色流水线**：夜晚行动由冻结的角色 spec + 纯 Hook 经「校验 → 解析 → EffectApplier 唯一写入口」驱动，新增角色无需改动核心模块（守卫样例 `backend/app/roles/guard.py` 为验收证明）
- **白天生命周期**：发言、投票、平票复投、遗言是引擎内与角色无关的行为，经 `BaseRole` 调用 LLM
- **断点续跑**：调度点、夜晚批次、死亡发布均有持久检查点，失败后精确续跑
- **存档版本化**：`GameManifest` 持久化 pipeline/registry/spec/effect 版本并在恢复时校验兼容性

## 快速开始

### 环境要求

- Python >= 3.11
- Node.js >= 18
- DeepSeek API Key（[前往获取](https://platform.deepseek.com/)）

### 1. 配置环境变量

在 `backend/` 目录下创建 `.env` 文件：

```ini
# DeepSeek API
DEEPSEEK_API_KEY=sk-your-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_TEMPERATURE=1.2

# App
DEBUG=true
LOG_LEVEL=INFO
```

### 2. 启动后端

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

后端运行在 `http://localhost:8000`，API 文档见 `http://localhost:8000/docs`。

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端运行在 `http://localhost:5173`。

## 使用指南

### 创建游戏

1. 打开前端页面 `http://localhost:5173`
2. 点击「创建游戏」按钮
3. 配置各角色人数（默认 9 人局：3 狼人、3 平民、1 预言家、1 女巫、1 猎人）
4. 游戏创建后自动开始，所有角色由 AI 控制

### 观看游戏回放

1. 在大厅页面点击任意已完成的游戏进入回放
2. 使用底部时间轴控制器：
   - **播放/暂停**：控制回放进度
   - **步进**：逐帧查看每个事件
   - **速度调节**：0.5x ~ 8x 倍速
   - **拖拽进度条**：跳转到任意时间点
3. 右侧历史面板可按事件类型筛选（发言、投票、夜间行动、死亡等）

### 进行中的游戏控制

通过 WebSocket 可以对正在运行的游戏进行控制：

- `set_speed` — 调整游戏速度
- `pause` / `resume` — 暂停/恢复游戏
- `skip_phase` — 跳过当前阶段

## 游戏规则

### 角色配置（默认 9 人局）

| 阵营 | 角色   | 人数 | 能力                                             |
| ---- | ------ | ---- | ------------------------------------------------ |
| 好人 | 平民   | 3    | 无特殊能力，通过发言和投票找出狼人               |
| 好人 | 预言家 | 1    | 每晚查验一名玩家的阵营                           |
| 好人 | 女巫   | 1    | 拥有一瓶解药（救人）和一瓶毒药（杀人），各限一次 |
| 好人 | 猎人   | 1    | 死亡时可开枪带走一名玩家                         |
| 狼人 | 狼人   | 3    | 每晚可击杀一名玩家，互相知道身份                 |

### 游戏流程

```
发放身份 → 夜晚 → 天亮了 → 遗言 → 顺序发言 → 投票放逐 → 夜晚（循环）
```

- **夜晚**：狼人投票刀人 → 女巫获取刀口信息（可救/可毒）→ 预言家查验 → 猎人死亡可开枪 → 结算死亡
- **白天**：公布死亡 → 遗言（仅第一天）→ 顺序发言 → 投票放逐 → 平票则加赛发言重投

### 胜利条件

- **好人阵营**：放逐所有狼人
- **狼人阵营**：屠边（消灭所有神职或所有平民）
- **狼刀在先**：若双方同时满足条件，狼人阵营获胜

## REST API

| 方法      | 路径                     | 说明                     |
| --------- | ------------------------ | ------------------------ |
| GET       | `/api/health`          | 健康检查                 |
| GET       | `/api/config`          | 获取应用配置             |
| POST      | `/api/games`           | 创建新游戏               |
| GET       | `/api/games`           | 获取游戏列表             |
| GET       | `/api/games/{id}`      | 获取游戏详情             |
| GET       | `/api/games/{id}/logs` | 获取游戏日志（用于回放） |
| WebSocket | `/ws/game/{id}`        | 实时事件推送             |

## 运行测试

```bash
cd backend
python -m pytest tests/ -q                                                       # 全部测试（1331 个）
python -m pytest tests --cov=app --cov-branch --cov-fail-under=100 -q           # 覆盖率门禁（statement/branch 100%）

cd frontend
npm test                                                                         # Vitest（57 个测试）
npm run build                                                                    # TypeScript + Vite 构建
```

## 数据存储

游戏数据存储在 `backend/data/games/<game_id>/` 目录下：

- JSONL 格式的完整游戏日志（`game.log`）与 LLM 对话记录（`conversation.log`）
- 每个角色的记忆状态 JSON 文件（`memories/`）
- `index.json` 游戏清单，携带流水线版本信息，重启后可恢复并校验兼容性
