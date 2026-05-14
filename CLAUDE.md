# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Wolf Killer 是一个完全由 LLM 驱动的 AI 狼人杀游戏。所有玩家（狼人、村民、预言家、女巫、猎人）均由 DeepSeek 大语言模型控制，无需真人参与。前端提供基于时间轴的观看/回放界面。

## 常用命令

### 后端

```bash
cd backend
pip install -r requirements.txt       # 安装依赖
python -m app.main                    # 启动后端 (localhost:8000)
python -m pytest tests/ -q            # 运行所有测试
python -m pytest tests/test_xxx.py -q  # 运行单个测试文件
```

### 前端

```bash
cd frontend
npm install                           # 安装依赖
npm run dev                           # 启动前端 (localhost:5173)
npm run build                         # 类型检查 + 生产构建
npm run lint                          # ESLint 检查
```

## 核心架构

### 后端游戏流程

入口 `backend/app/main.py`，启动时创建单例 EventBus、WSManager、GameService。所有游戏操作通过 REST API 和 WebSocket 暴露。

**游戏引擎 (`backend/app/core/game_engine.py`)** 是整个系统的核心编排器（最大源文件 ~32KB），通过 asyncio 事件循环驱动游戏：

1. `GameService.create_game()` 实例化角色对象和 GameEngine，在 asyncio 任务中启动引擎
2. `GameEngine.start()` 运行完整游戏循环（发放身份 → 夜晚/白天循环 → 游戏结束）
3. 所有事件通过 `EventBus` 异步发布/订阅，WebSocket 推送给前端

**状态机 (`backend/app/core/state_machine.py`)** 管理阶段转换，使用 `(current_phase, event, next_phase)` 三元组表：

```
WAITING → ROLE_DEAL → NIGHT → DAWN → LAST_WORDS → SPEECH → VOTE_CASTING → VOTE_RESOLUTION
                                                                    ↑              ↓
                                                              NIGHT ←──────────────┘
```
平票时进入 TIEBREAK_SPEECH → TIEBREAK_VOTE，达到平票上限则无人被放逐。

**规则引擎 (`backend/app/core/rule_engine.py`)** 实现屠边规则，关键语义：「狼刀在先」——若双方同时满足胜利条件，狼人阵营优先获胜。

**角色系统**：每个角色类（Werewolf、Villager、Seer、Witch、Hunter）继承 `BaseRole`。BaseRole 定义了标准的游戏阶段生命周期方法（`on_night()` / `on_dawn()` / `on_speech()` / `on_vote()` 等），子类重写特定行为。所有角色通过 LLMClient（langchain-openai ChatOpenAI 封装）与 DeepSeek 通信。

**提示词 (`backend/app/agents/prompt_builder.py`)** 是代码库最大文件（~42KB），包含所有角色的系统提示模板和阶段级提示。提示词使用结构化 JSON 输出格式约束 LLM 行为。

**事件总线 (`backend/app/core/event_bus.py`)** 提供异步发布/订阅，所有游戏事件（阶段变更、发言、投票、死亡等）通过它分发，最终推送到 WebSocket。

### 前端架构

**状态管理 (`frontend/src/store/gameStore.ts`)** 是前端的核心（~20KB），使用 Zustand 5。它同时处理：
- 直播模式：通过 WebSocket 实时接收游戏事件并更新状态
- 回放模式：从 HTTP 加载完整游戏日志，支持播放/暂停、逐帧步进、0.5x~8x 速度调节、按事件类型筛选

**WebSocket (`frontend/src/api/websocket.ts`)** 自定义 hook，建立 WebSocket 连接后将 JSON 消息路由到 Zustand store 对应的处理函数。

**主题 (`frontend/src/theme.ts`)** 定义了完整的 MUI 深色主题，使用自定义蓝/紫色系配色方案。

### 关键设计细节

- **记忆系统**：`MemoryService` 以 JSON 文件格式持久化每个角色的记忆状态（在 `backend/data/games/<id>/` 目录下），跨游戏保留上下文
- **输出解析**：LLM 返回的 JSON 输出由 `output_parser.py` 解析，通过正则表达式提取、校验、容错处理
- **状态过滤**：`state_filter.py` 根据角色身份筛选游戏状态信息，确保狼人看不到好人专属信息（反之亦然）
- **夜间行动顺序**：狼人刀人 → 女巫获知刀口 → 女巫可救/可毒 → 预言家查验 → 猎人死亡开枪 → 结算死亡。`action_resolver.py` 处理行动优先级和冲突解决
- **发言顺序**：从死亡玩家左手边开始逆时针发言，LLM 玩家需要知晓当前发言进度（由 `prompt_builder.py` 注入轮次上下文）

## 注意事项

- `backend/.env` 已提交到仓库（含 API key），修改时注意不要推送到公开仓库
- 游戏数据存储在 `backend/data/games/`，每个游戏有独立子目录存放 JSONL 日志和角色记忆
- 后端 Python 需要 >= 3.11
- 前端使用 TypeScript 6.0，ESLint 平面配置格式
- 禁止删除 `data/` 目录下正在进行的游戏数据，否则会导致游戏中断
