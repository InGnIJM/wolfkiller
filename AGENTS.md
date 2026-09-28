# AGENTS.md

Cursor / Codex / Gemini 等编码助手的仓库入口。与 [CLAUDE.md](CLAUDE.md) 描述同一套命令、架构与门禁；改一处必须改另一处，并同步 [docs/architecture.md](docs/architecture.md)。

人类文档从 [README.md](README.md) 和 [docs/README.md](docs/README.md) 进入。已知失败模式见 [MEMORY.md](MEMORY.md)。`docs/superpowers/` 与 `docs/project-analysis.md` 是按日期归档的历史稿，不要当现行说明书。现行评测结论见 [docs/notes/2026-09-16-mimo-mixed-arena.md](docs/notes/2026-09-16-mimo-mixed-arena.md)。

## 项目

完全由 LLM 驱动的 AI 狼人杀。玩家（狼人、村民、预言家、女巫、猎人，十人局可选守卫，十二人板可选白痴、白狼王、骑士、狼美人与老酒鬼）全部由创建对局时配置的模型控制。前端是时间轴观看/回放界面。

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
| `core/scheduler.py` | 调度点、响应窗口、阶段门禁；`run_point(slot=)` 支持同阶段多次运行；聚合按 `contract_id` 分组（共享契约合并计票） |
| `core/night_settlement.py` | pending damage/protection → 死亡 |
| `agents/prompt_renderer.py` | 只从 Spec/Contract/Context 渲染 Prompt |

夜晚由 `GameEngine._execute_staged_night()` 驱动，顺序固定：

`NIGHT_ACTION`（守卫）→ 狼队讨论/投票（`NightDirector`，`core/night_flow.py`）→ `NIGHT_WOLF_VOTE` → `NIGHT_WITCH_ACTION` → `NIGHT_SEER_ACTION` → `NIGHT_COMMIT`。开警长时随后进入 `SHERIFF_ELECTION`（`SheriffDirector`，`core/sheriff_flow.py`），再转 DAWN。

白天发言/投票/遗言是引擎内与角色无关的路径。投票经校验器后以 `EffectApplier` 的 `ACCEPT_ACTION` 落账。白天另有三个角色无关窗口：每位发言者前跑 `DAY_ACTION`（slot=`vote_round:seat`），全体发言结束后、投票前跑 `POST_SPEECH_ACTION`（slot=`post_speech`），放逐前跑 `EXILE_VERDICT`（事件含 `EXILE_CANCELLED` 则翻牌免死、不走遗言）。前两个窗口的事件含 `DAY_INTERRUPTED` 时都会结算双死、跑 `DAWN_REACTION`、以 `WEREWOLF_EXPLODED` 转 NIGHT。引擎只认这些通用事件，不出现角色名。

狼队按 `camp == Camp.WEREWOLF` 识别（含白狼王、狼美人），不要用 `role == "wolf-killer-werewolf"`。投票资格读 `runtime.statuses`：`no_vote` 不投票，`exile_immune` 不进候选。

`GameEngine` 运行时固定 `PipelineMode.V2`。环境变量 `ROLE_PIPELINE_V2` **不改变对局**。

## 规则（改代码时容易漏）

- 守卫：不能连续两晚守同一人；守护只抵消狼刀，毒药与猎枪无视守卫；守卫守护与女巫解药同时作用于同一狼刀目标时目标仍死亡（对穿）。
- 白痴：被放逐时翻牌，公开身份、不死、失去投票权、不再能被放逐，仍可发言；夜刀/毒/枪/自爆带走时正常死亡。计入神职。
- 白狼王：夜晚与狼队共享 `werewolf_kill` 契约；白天 SPEECH 阶段每位发言前可自爆带走一人，双方无遗言，当日发言投票取消直接入夜；被毒/放逐/枪杀不能带人。被带走的猎人可开枪（`_SHOOT_REASONS` 含 `self_explode`）。
- 神职含守卫、白痴、骑士。老酒鬼是**平民**（`tags={"villager"}`），不是神职。狼人胜：神职全灭 / 平民全灭 / 狼人数（含白狼王、狼美人）大于好人数。先判狼（狼刀在先）。
- 神职/平民归属**读 `RoleSpec.tags`（`god` / `villager` / `wolf`）**，不要用角色名子串匹配：`rule_engine._role_tags()` 从 registry 取 tag，未注册的角色不带任何 tag。历史上按 `"seer"/"villager"` 等子串计数会漏掉骑士，导致「骑士是最后一名存活神职」时提前判狼胜。
- 骑士：好人神职。**全体发言结束后、放逐投票前**（`POST_SPEECH_ACTION` 调度点）翻牌决斗一名玩家；是狼人则该玩家立即死亡、**在进入夜晚前发表遗言**、当日发言投票取消直接入夜；是好人则骑士以死谢罪、**无遗言**、当日投票照常。一局一次（`duel` 资源）。两种死亡共用死因 `knight_duel`，遗言只发给被裁决者：中断路径给 `give_last_words` 传 `daytime=True`，夜间遗言路径不传，因此骑士本人不会被补发。
- 狼美人：狼人阵营。夜晚与狼队共享 `werewolf_kill` 契约；另在 `NIGHT_WITCH_ACTION` 调度点（狼刀投票之后）可魅惑一名好人（不能连续两晚同一人、不能自指/魅狼队友/魅免疫者）。出局时当晚被魅惑者殉情（死因 `charm`）。不能自爆、不能自刀：自刀由 `WOLF_BEAUTY_SPEC.initial_resources={"self_kill_forbidden": 1}` + `validate_werewolf_action()` 拒绝（狼刀契约按阵营共享、必须字节一致，所以能力差异只能走资源标记，禁止在 `roles/werewolf.py` 里写角色名），狼队讨论/投票提示词对声明该标记的座位改写成「不能投自己」（`NightDirector._self_kill_clause()`），否则模型会白丢一票而不自知。被魅惑带走的猎人**不能开枪**。
- 老酒鬼：好人平民。免疫魅惑（`initial_resources={"charm_immune": 1}`，经 `selected_target.resource_labels` 对狼美人可见）。被毒或枪杀时 `delayable` 资源让 `settle()` 跳过致死、落 `poisoned`/`wounded` + `delayed_death` 状态，**次日发言结束后投票前**由 `_resolve_delayed_deaths()` 结算；夜刀/放逐/自爆当夜即死。
- 新增调度点 `POST_SPEECH_ACTION`（`post_speech_action`）承载「发言结束后」的白天技能；引擎的发言后窗口只在有角色声明该调度点时才跑（`_post_speech_action_enabled()`），跑完会 `_settle_and_publish()` 让 pending damage 立即生效、再结算延迟死亡，最后才 `SPEECHES_COMPLETE`。
- 可选警长（`GameConfig.enable_sheriff`，默认关）。关局时提示词省略警长词、状态机不进入竞选；开局时由 `SheriffDirector` 硬编码竞选/交徽，Agent 只选当前工具，竞选决策提示注入本人身份、私有事实、已上警/未上警（警下）名单与警上发言摘录；竞选发言与白天发言、放逐投票、超时重试投票的权威状态都带警上/警下名单（警上=`office.candidates`，警下=`off_badge_seats`：开选时存活且从未上警，排除开选前死亡）与固定事实约束，竞选期另给竞选发言顺序与已发言/尚未发言名单，避免模型把警下玩家说成警上；`ConversationLog` 全程同一对象（新局清空记录、恢复时由编解码器替换记录），每轮警长投票后以 PUBLIC `phase=sheriff_ballot` 记录发布完整票型供后续决策使用。九人、十人预设建议关，十二人预设建议开。
- 公开 DTO / 前端消费链不得出现 `role_init`、`visible_to`、`night_intel`、`check_results`、`has_antidote`、`has_poison`、`has_gun` 等私有字段。
- `prompt_builder.py` / `state_filter.py` 是委托外壳，源码不得出现内置角色名。
- 提供方在 `agents/providers/`；`core/` 与 `roles/` 禁止直接 import providers。OpenCode 的 Zen/Go 是同一域名下的两个 profile，只能按路径（`/zen/go` 先于 `/zen`）区分。
- Base URL 路径以 `/responses` 结尾时走 OpenAI Responses 方言（`openai_responses`），并**关掉 strict**；该方言下同一域名打 `/chat/completions` 会 500。`strip_responses_suffix()` 会把尾部 `/responses` 去掉再交给 SDK，否则会 POST 到 `/responses/responses`。方言覆盖用 `replace()`，只换 dialect 与 strict，provider 自己的 headers 必须保留。
- 自定义请求头走 `ModelConfig.headers` → `LLMClientConfig.headers`（tuple，保持可哈希）→ transport `default_headers`；校验入口只有 `base.py` 的 `header_error()`。headers 属于敏感面，禁止进 `_MODEL_PARAMETER_FIELDS`、model_snapshot 或 game.log。
- 耐久事实源是 `backend/data/wolfkiller.sqlite3`，不是 JSONL。不要删除进行中的 `data/` 对局目录。
- 大厅 `GET /api/games` 不含评测局；评测局从评测页进出。
- 注册表新增角色会改变 `registry.digest`，处于 `interrupted` 的旧局无法续跑。

## 改这些文件后必须同步测试

修改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py` 后，更新：

- `tests/test_game_engine.py` 引擎禁词
- `tests/test_action_resolver.py` 与 `tests/test_prompt_builder.py` 角色名禁词
- `tests/test_prompt_renderer.py` 渲染器禁词
- 角色扩展样例：`tests/test_guard_extension.py`、`tests/test_idiot_extension.py`、`tests/test_werewolf_king_extension.py`、`tests/test_knight_extension.py`、`tests/test_wolf_beauty_extension.py`、`tests/test_old_drunkard_extension.py`；`tests/test_catalog_routes.py` 断言 11 角色 6 预设
- 共享狼刀契约的角色差异：改 `roles/werewolf.py` 的 `validate_werewolf_action()` 或 `core/night_flow.py` 的 `_self_kill_clause()` 后，更新 `tests/test_wolf_beauty_extension.py`（自刀被拒 / 队友可切 / 其他狼不受限）与 `tests/test_night_flow.py`（三态提示词 + 常量与角色侧声明一致）
- 观众事件类型：`app/services/audience_projector.py` 的 `_EVENTS` 增删类型后，同步 `tests/test_audience_projector.py` 的类型全集快照与前端 `components/game/test/eventCoverage.test.tsx` 的期望表
- 观众事件必须有一步耐久提交：audience 表只由 `GameService._checkpoint_domain_events` 的 label 分支写入，只发射领域事件而没有对应 step label 的事件会静默丢掉（白天窗口用 `day_point:`、旁白用 `narration:`、系统代投用 `vote_technical_abstain:`）。死亡按 label 里的座位集合宣布，别再用死因白名单（`charm` 会被过滤掉）。引擎里只为提交一次的临时槽位（`_pending_point_events` / `_pending_narration`）不进编排状态编解码器——它的字段集是精确匹配的，加字段会让旧检查点无法续跑；同理不要改调度点的 slot 形状，`PointKey` 变了对局就不能续跑

**不要**再改 `test_guard_extension.py` 里已经不存在的 `CORE_BLOBS_BEFORE_GUARD`。

前端覆盖率只卡 `vite.config.ts` 列出的 9 个文件；最复杂的 UI 不在门禁内，所以渲染面的事件覆盖由 `components/game/test/eventCoverage.test.tsx` 兜底（三个渲染面 × 全部观众事件类型逐项表态，漏 case 即红）。WSL 挂载 `/mnt/e` 上不要直接跑 vitest，做法见 `MEMORY.md`。
