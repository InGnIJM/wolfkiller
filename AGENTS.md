# AGENTS.md

Cursor / Codex / Gemini 等编码助手的仓库入口。与 [CLAUDE.md](CLAUDE.md) 描述同一套命令、架构与门禁；改一处必须改另一处，并同步 [docs/architecture.md](docs/architecture.md)。

人类文档从 [README.md](README.md) 和 [docs/README.md](docs/README.md) 进入。已知失败模式见 [MEMORY.md](MEMORY.md)。`docs/superpowers/` 与 `docs/project-analysis.md` 是按日期归档的历史稿，不要当现行说明书。

## 项目

完全由 LLM 驱动的 AI 狼人杀。玩家（狼人、村民、预言家、女巫、猎人，十人局可选守卫）全部由创建对局时配置的模型控制。前端是时间轴观看/回放界面。

## 常用命令

```bash
# 后端（Python >= 3.11）
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
python -m pytest tests app -q
python -m pytest tests app --cov=app --cov-branch -q

# 前端（Node.js >= 20.19）
cd frontend
npm install
npm run dev          # localhost:5173
npm test
npm run test:coverage
npm run test:e2e
npm run lint
npm run build
```

后端测试必须带 `tests app`（含 `app/` 内嵌测试）。覆盖率门禁 `--cov-fail-under=100`（statement/branch）。不要提交 `backend/.env`。

## 架构要点

入口 `backend/app/main.py`：`ProcessLock` → `GameRepository`（启动时把 `running` 标为 `interrupted`）→ EventBus / WSManager / MemoryService / GameService / AudienceEventService / BenchmarkService。

角色规则是 **冻结声明 + 纯 Hook + 类型化 Effect + 唯一原子写入口**。新增角色只加 `roles/*.py` spec/Hook 并注册，不要改核心模块。守卫是验收样例。

| 模块 | 职责 |
| --- | --- |
| `models/pipeline.py` | 冻结值类型、Effect、Contract/RoleSpec |
| `roles/registry.py` | 发现、静态验证、`freeze()` |
| `core/context_projector.py` | 按 PUBLIC/ACTOR/CAMP 投影最小上下文 |
| `core/action_validator.py` | 纯校验，零状态读写 |
| `core/action_resolver.py` | 调纯 Hook，产出 Effect 批次 |
| `core/effect_applier.py` | **唯一写入口**：整批校验、CAS、原子应用 |
| `core/scheduler.py` | 调度点、响应窗口、阶段门禁 |
| `core/night_settlement.py` | pending damage/protection → 死亡 |
| `agents/prompt_renderer.py` | 只从 Spec/Contract/Context 渲染 Prompt |

夜晚由 `GameEngine._execute_staged_night()` 驱动，顺序固定：

`NIGHT_ACTION`（守卫）→ 狼队讨论/投票（`NightDirector`，`core/night_flow.py`）→ `NIGHT_WOLF_VOTE` → `NIGHT_WITCH_ACTION` → `NIGHT_SEER_ACTION` → `NIGHT_COMMIT`

白天发言/投票/遗言是引擎内与角色无关的路径。投票经校验器后以 `EffectApplier` 的 `ACCEPT_ACTION` 落账。

`GameEngine` 运行时固定 `PipelineMode.V2`。环境变量 `ROLE_PIPELINE_V2` **不改变对局**。

## 规则（改代码时容易漏）

- 守卫：不能连续两晚守同一人；守卫守护与女巫解药同时作用于同一狼刀目标时目标仍死亡（对穿）。
- 神职含守卫。狼人胜：神职全灭 / 平民全灭 / 狼人数大于好人数。先判狼（狼刀在先）。
- 本局无警长。公开 DTO 的 `is_sheriff` 只为兼容旧档。
- 公开 DTO / 前端消费链不得出现 `role_init`、`visible_to`、`night_intel`、`check_results`、`has_antidote`、`has_poison`、`has_gun` 等私有字段。
- `prompt_builder.py` / `state_filter.py` 是委托外壳，源码不得出现内置角色名。
- 提供方在 `agents/providers/`；`core/` 与 `roles/` 禁止直接 import providers。
- 耐久事实源是 `backend/data/wolfkiller.sqlite3`，不是 JSONL。不要删除进行中的 `data/` 对局目录。
- 大厅 `GET /api/games` 不含评测局；评测局从评测页进出。

## 改这些文件后必须同步测试

修改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py` 后，更新：

- `tests/test_game_engine.py` 引擎禁词
- `tests/test_action_resolver.py` 与 `tests/test_prompt_builder.py` 角色名禁词
- `tests/test_prompt_renderer.py` 渲染器禁词

**不要**再改 `test_guard_extension.py` 里已经不存在的 `CORE_BLOBS_BEFORE_GUARD`。

前端覆盖率只卡 `vite.config.ts` 列出的 9 个文件。WSL 挂载 `/mnt/e` 上不要直接跑 vitest，做法见 `MEMORY.md`。
