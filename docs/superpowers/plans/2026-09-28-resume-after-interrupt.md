# 中断续跑（Resume After Interruption）实施方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **状态：Task 1-8 已全部实施完成（2026-09-28）。** 落地结果与本文档只有三处差异：① 恢复审计写**一条** `recovery` operation（带 `forced` 布尔）而不是 `RECOVERY_RESUMED` / `RECOVERY_FORCED` 两种；② 没有新增 `retry_recovery` 端点——`recovery_blocked` 直接进 `recover` 的默认可接受状态集（更窄的 `expected_statuses` 仍可显式传入），因此不需要"清空 `recovery_block_code`"的专用路径；③ `drift` 拒绝时**不写终态**，状态留在 `interrupted` 让用户回答后重试，只有 `incompatible` 才落 `recovery_block_code`。验证：后端 `3548 passed` + statement/branch 100%（`--cov-fail-under=100`）；前端 33 文件 511 测试全绿、9 文件覆盖率 100%、lint/build/tsc 干净。§6 的高风险项「跨版本续跑不得重放」已实测，不是推理：`tests/test_crash_recovery_subprocess.py::test_a_drifted_registry_neither_replays_commits_nor_recalls_models`（同一 step key 重提交 `replayed=True` 且语义视图逐字节一致；同一 request token 二次调用 `reused=True`，provider 只被调一次）。

**Goal:** 保证**本次改动上线之后**新产生的任何中断（进程被杀、崩溃、代码已改动）都能恢复续跑，并把版本漂移从「永久锁死的 `recovery_blocked`」降级为「可诊断、可重试、可强制」。

**范围（2026-09-28 确认）：**

- **在范围内**：上线后新产生的中断必须可恢复；`failed` / `recovery_blocked` 不得再成为没有出口的终态。
- **不在范围内**：**旧档救援**。上线前已经中断/锁死的局（`c60b7ca2`、`5b454829` 等）不承诺、不专门适配、**不做迁移器、不做救援脚本**。附带说明：B 上线后这些旧档大概率能被 `retry_recovery` 顺带捞回来（实测都定级为 `drift`），但这是副作用，不是验收项。
- **不做 C 方案**（checkpoint 内嵌 RoleSpec/Contract 快照）。理由见 D5：它最大的成本来自老档迁移器，而老档已明确不救；且它救不了"角色被删"（Hook 是代码，快照没有实现）。

**Architecture:** 把「持久化身份」与「当前代码版本」解耦。所有落盘的身份键（point journal key、request token / action_key、role resource setup marker）改用**本局冻结的 digest**（`GameState.registry_digest`）；当前注册表只在**恢复入口做一次显式兼容性判定**（新增 `app/persistence/recovery_compat.py`），判定结果决定「直接续跑 / 告警续跑 / 需 `force` / 拒绝」。

**Tech Stack:** Python 3.11+、FastAPI、SQLite（`backend/data/wolfkiller.sqlite3`）、pytest + pytest-cov、React + Zustand + MUI + Vitest。

**Spec:** 本文件即设计与实施方案（无独立 spec 稿）。落地后按需抽出 `docs/superpowers/specs/`。

## Global Constraints

- **不改角色行为语义**：`backend/app/roles/**` 的 Hook 行为、`models/pipeline.py` 的冻结值类型字段不改；`SchedulePoint`/`EffectKind` 不增删。
- **不改公开 DTO 隐私边界**：新增字段不得带出 `role_init / visible_to / night_intel / check_results / has_antidote / has_poison / has_gun`。
- **门禁**：后端 `cd backend && python -m pytest tests app --cov=app --cov-branch --cov-fail-under=100 -q`；前端 `npm test` + `npm run lint` + `npm run build`。
- **TDD**：先写失败测试 → 亲眼确认失败 → 最小实现 → 重构。
- **提交粒度**：每个 commit ≤ 3 个文件，message 用 `<type>(<scope>): <description>`。
- 不提交 `backend/.env`；不删除 `backend/data/` 下进行中的对局目录；运行时备份用 SQLite `.backup`。
- 兼容性判定失败**必须**给出可读原因串，禁止静默降级或吞异常。

---

## 1. 现状与根因

### 1.1 已经具备的能力（不要重写）

续跑机制本身是完整的，只是被版本门挡住：

- 启动 `repository.interrupt_running_games()`（`app/main.py:38`）把残留 `running` 标为 `interrupted`；优雅关停走同一条（`app/services/game_service.py:2532-2537`）。
- `GameService._recover_game()`（`app/services/game_service.py:784-864`）→ `engine.restore()`（`app/core/game_engine.py:363`）实现精确续跑；调度点、夜晚批次、死亡发布、遗言认领均有落盘检查点。
- `tests/test_crash_recovery_subprocess.py` 三个用例证明「子进程崩溃 → 恢复到未完成提交、模型边界不重复调用」。
- 前端大厅已有「恢复对局」入口（`frontend/src/components/lobby/GameCard.tsx:146-155`）。

**结论：同代码版本下重启续跑是通的。坏的是「跨改动续跑」。**

### 1.2 实测证据（本机 `backend/data/wolfkiller.sqlite3` 副本，只读分析）

| 观测 | 结果 |
| --- | --- |
| `execution_status` 分布 | `failed` 106、`completed` 81、`cancelled` 27、`recovery_blocked` 6、`interrupted` 3、`running` 1 |
| 6 个 `recovery_blocked` 的 `recovery_block_code` | **全部** 是 `checkpoint_corrupt` |
| 逐个用**当前代码** `CheckpointCodec.decode` 复现 | 6/6 报 `registry mismatch`；**没有一个是真损坏**（checkpoint JSON 与 sha256 摘要一致） |
| 绕过 digest 门后解码 `c60b7ca2`（12人局 · 9月27日 23:05） | 成功：`phase=night round=5`、存活 [2,3,6,7,11]、8 个角色、32 条 journal 记录 |
| 同法解码 `5b454829`（9月18日） | 越过 digest 后仍报 `request contract mismatch`（契约清单已变更，属另一类） |
| `runtime.resource_setup_digest` 复算（`c60b7ca2`） | 用当前注册表、无论配「当时 digest」还是「当前 digest」都复现不出存储值 → 恢复后首个调度点必抛 `EffectRejected("role resource configuration changed")` |
| 元凶定位 | `c60b7ca2` 开局于 **2026-09-27 23:05**；其后 `a075933`（**2026-09-28 13:37**）给 `WOLF_BEAUTY_SPEC` 加了 `initial_resources={"self_kill_forbidden": 1}` → 声明变了 → digest 变、marker 变、局被锁死 |
| 3 个 `interrupted` 局 | 均有 `deleted_at`（已被删除），无一是"活着的可续跑局" |

**要点：卡住的不是数据，是判定。**

### 1.3 门清单（现状 → 处置）

| # | 位置 | 现状 | 语义 | 处置 |
| --- | --- | --- | --- | --- |
| G1 | `app/persistence/checkpoint_codec.py:291` | `root["registry_digest"] != self._registry.digest` | 全库角色声明全等 | **改**为「与 `state.registry_digest` 内部一致」 |
| G2 | `app/persistence/checkpoint_codec.py:459` | `key_row["registry_digest"] != self._registry.digest` | journal key 全等 | **改**为「与 `state.registry_digest` 内部一致」 |
| G3 | `app/persistence/checkpoint_codec.py:441-443` | `contract_version/contract_digest` 与 live spec 全等 | 契约行为兼容 | **保留**（真行为门），但只校验**本局用到的**契约；不匹配时给可读诊断，`force` 可越过 |
| G4 | `app/services/game_service.py:1713-1716` | `engine_recovery_version` / `prompt_digest` 硬门 | 续跑器 ABI + 系统提示版本 | `engine_recovery_version` **保留**；`prompt_digest` **降级**为告警 + 审计事件 |
| G5 | `app/services/game_service.py:1733-1747` | `parameters_digest` 完整性 + `current_parameters == parameters` 硬门 | 模型参数一字不差 | 完整性校验**保留**（防篡改）；参数漂移**降级**为告警（`api_key`/`headers` 本来就取活配置） |
| G6 | `app/services/game_service.py:1911-1912` | `state.registry_digest != self._registry_snapshot.digest` | 恢复入口全等 | **改**为调用 `recovery_compat.assess()` |
| G7 | `app/services/game_service.py:766-782` + `:569` | `recover` 只接受 `interrupted`；`recoverable` 只认 `paused/interrupted` | 状态门 | **改**：`failed` 也可 recover，`recovery_blocked` 可重试 |
| R1 | `app/core/role_runtime.py:91-98` | `resource_setup_digest` marker 覆盖 `registry.digest` + 每个角色**整份** `spec.stable_digest()`（含 `tags`/`instructions`）；不一致直接抛 `EffectRejected` | 初始资源未变 | **改**为只覆盖「初始资源声明」；不一致不再抛异常，而是上报 `drift`（由恢复入口决定是否 `force` 采纳，采纳时不重放资源） |
| K1 | `app/core/scheduler.py:499` | `PointKey(..., self.registry.digest)` | journal 键 | **改**用冻结 digest |
| K2 | `app/core/scheduler.py:310` | request `token`（= `window_id` = `action_key`）含 `registry.digest` | 幂等键 | **改**用冻结 digest |
| K3 | `app/core/game_engine.py:1883-1886` | 响应点 seeding 用的 `PointKey(..., scheduler.registry.digest)` | journal 键 | **改**用冻结 digest |
| K4 | `app/core/scheduler.py:297` | `initialize_role_resources(..., registry.digest)` | 见 R1 | 随 R1 一起改 |

R1/K2 是**隐藏杀手**：即使拆掉 G1/G2/G6，声明变过的局也会在恢复后第一个调度点抛 `EffectRejected`，被 `_attach_task_watcher`（`app/services/game_service.py:1344-1356`）标成 `failed` —— 从"不可恢复"变成"崩一次再不可恢复"。本机 `c60b7ca2` 正是这个组合：`a075933`（2026-09-28 13:37）给 `WOLF_BEAUTY_SPEC` 加了 `initial_resources={"self_kill_forbidden": 1}`，而该局开局于 2026-09-27 23:05。

### 1.4 两个没有出口的状态

- `recovery_blocked`：全仓库**没有任何** `transition_execution(expected=(...,"recovery_blocked",...))` 指向非终态（仅 `cancel_game` 在 `app/services/game_service.py:2293` 允许转入 `cancelled`）。**代码回滚也救不回来。**
- `failed`：`recover` 的 `expected_statuses=("interrupted",)` 不含它（`app/services/game_service.py:768`），前端也不给按钮（`GameCard.tsx:146`）。引擎运行中抛异常（LLM 网关报错、上面 R1 的 `EffectRejected`）就走这里。
- 启动路径还把「registry mismatch」误标成 `checkpoint_corrupt`（`app/services/game_service.py:525-532`），把「可诊断的版本漂移」伪装成「数据损坏」，用户无从判断。
- `tests/test_startup_registry_mismatch_quiet.py:1-7,76-77` 目前**把这个行为写成了契约**（docstring：`can never resume`），本方案要显式推翻它。

---

## 2. 设计决策

### D1 冻结 digest 贯通

新增单一来源 helper：

```python
# app/persistence/recovery_compat.py
def frozen_registry_digest(state: GameState, registry: RegistrySnapshot) -> str:
    """本局冻结的注册表身份；新局等于 live digest。"""
```

规则：返回 `state.registry_digest or registry.digest`，并校验 64 位小写十六进制（不合法时回退 live digest）。`Scheduler.issue/run_point`、`GameEngine._run_response_point`、`initialize_role_resources` 全部改用它。**新局 `state.registry_digest` 在建局时由 `game_service.py:1229` 写入 = live digest，因此对正常对局零行为变化。**

理由：一旦 digest 变化就让 journal 键、幂等键、资源 marker 全部换身份，等于把"版本漂移"升级成"重复副作用风险"。冻结后这些键跨版本稳定，兼容性只需在恢复入口判一次。

### D2 恢复兼容性分级

`app/persistence/recovery_compat.py` 的唯一职责：给「checkpoint 文档 + 当前注册表」出一个报告。

```python
CompatLevel = Literal["exact", "compatible", "drift", "incompatible"]

@dataclass(frozen=True)
class CompatReport:
    level: CompatLevel
    reasons: tuple[str, ...]        # 人类可读，含 role_id / contract_id
    blocking: tuple[str, ...]       # 仅 incompatible 时非空
```

判定规则（按严重度收敛）。**核心口径：这次变化会不会改变本局后续可观测行为？**

1. `exact`：全库 digest 相同，零差异。**只改 Hook 函数体落在这里**——`_stable_json` 对 callable 只记 `module.qualname`（`models/pipeline.py:149-150`），函数实现不进 digest；改函数**名**才会变。
2. `compatible`：**只动了本局用不到的角色**（新增/删除/改声明）→ 可直接续跑。绝大多数"加了别的角色"的场景落这里。
3. `drift`：本局**用到的**角色的声明变了，状态仍自洽，但后续行为可能不同——`tags`（胜负判定）、`instructions`（提示词）、`initial_private_data`、`initial_resources`、`visibility_namespaces`、或本局用到的契约 digest 变化 → **默认拒绝**，带 `force=true`（前端二次确认）才能续跑，且必须写审计事件。**`c60b7ca2` 落在这一级**（狼美人新增 `self_kill_forbidden`）。
4. `incompatible`：本局角色在当前注册表缺失、`RoleSpec.schema_version` 变化、契约 `schema_version` 变化、`pipeline_version`/`effect_schema_version`/`engine_recovery_version` 不匹配 → 永久拒绝（`5b454829` 那类）。

判定**只信 checkpoint 里的 `state.players` 与 `state.spec_versions`**，不用 live 全库 digest 做代理。

`drift` 续跑时的语义约定（必须写进代码注释与测试）：

- **不重放初始资源/私有数据**：`force` 续跑时把 `runtime.resource_setup_digest` 更新为新声明的 marker（采纳新基线），把资源差异写进审计事件；已消耗的资源（药、枪）保持原值，**不得**因为声明变化被重置。
- 私有事实（`initial_private_data`）同理：只影响后续写入，不回填过去。

### D3 状态出口

- `recover` 接受 `("interrupted", "failed")`。
- 新增服务方法 `retry_recovery(game_id)`：`recovery_blocked` → 重新跑一遍 `_recover_game` 判定；通过则转 `running`，失败则**原样保留**且用新错误码覆盖 `recovery_block_code`（这样修好代码/恢复模型配置后能自愈）。
- `recovery_block_code` 语义收紧，只用这组值：`checkpoint_missing`、`checkpoint_corrupt`、`checkpoint_version_unsupported`、`registry_incompatible`、`role_declaration_drift`、`contract_incompatible`、`prompt_drift`、`model_config_missing`、`model_key_unavailable`、`legacy_archive`。（`registry_mismatch` 保留为兼容旧档读值，新写入改用 `registry_incompatible`。）
- `_mark_recovery_blocked` 的写入改为「仅在**永久性**原因时」，`drift` 类原因不得进终态（停在 `interrupted` 并带上 `recovery_block_code` 供展示）。

### D4 模型配置与提示词漂移降级

**这两条是"保证以后可恢复"的必要条件，不是可选项**：只要 `prompt_digest` 或模型参数还是硬门，改一次系统提示/调一次温度就会让在跑的局不可恢复。

- `prompt_digest` 变化 → 告警 + 审计事件（`RECOVERY_PROMPT_DRIFT`），继续续跑：prompt 变了只影响后续发言风格，不影响已落账状态。
- 模型 `parameters` 变化 → 告警 + 审计事件（`RECOVERY_MODEL_DRIFT`），继续续跑：`api_key`/`headers` 本来就取活配置。
- `parameters_digest` 与 `parameters` **本身不自洽**（篡改）仍判 `checkpoint_corrupt`。
- 模型配置被删除 → 仍拒绝（`model_config_missing`），但错误信息要指出是哪个 `config_id`、哪些座位。

### D5 非目标

- **不做 C 方案**（checkpoint 内嵌本局 RoleSpec/Contract 快照 + 迁移器）。它的主要成本是老档迁移器，而老档已明确不救；剩下的收益只覆盖"本局角色的 `schema_version` 被 bump"这类边角情形，却要付出"旧声明 + 新 Hook"双事实源、`prompt_renderer`/`context_projector` 连带改造的代价。
- **不承诺"角色被删/改名"也能恢复**：`role_factory` 与 Hook 都是代码，快照恢复不出实现。若这条也要覆盖，正确做法是编码约定而非快照——**角色 id 永不删除；改名保留旧 id 别名并指向同一 Spec/Hook**（见 Task 8）。
- 不改角色语义、不改前端时间轴、不动 benchmark 指标口径。
- 不做自动恢复：**恢复必须由人触发**（大厅按钮/API），与 benchmark 的 `recover_benchmark_game` 保持一致。因此"崩溃→恢复→再崩溃"不会自动刷 LLM 调用。

---

## 3. 兼容性矩阵（新产生的中断 → 处置）

> 下表针对**本方案上线之后**产生的中断。上线前已锁死的旧档不在范围内（不迁移、不救援脚本）；它们若被判为 `compatible`/`drift`，会因 `retry_recovery` 顺带恢复，属副作用而非验收项。

| 中断情形 | 现状 | 本方案后 |
| --- | --- | --- |
| 进程被杀 / Ctrl+C / 同代码版本崩溃 | 可恢复 | `exact` → 直接恢复（不变） |
| 只改了 Hook 函数体（digest 不变） | 可恢复 | 可恢复（不变） |
| 增删/修改了**本局用不到**的角色 | 锁死 | `compatible` → 直接恢复 |
| 本局用到的角色 `tags`/`instructions` 变了 | 锁死 | `drift` → 一次确认后续跑 |
| 本局用到的角色 `initial_resources` 变了（如 `a075933` 那类） | `recovery_blocked: checkpoint_corrupt` | `drift` → 一次确认后续跑，资源**不重置** |
| 本局用到的**契约** digest 变了（如女巫契约加 `SET_PRIVATE_DATA`） | 锁死 | `drift` → 一次确认后续跑 |
| 提示词 / 模型配置参数改了 | 锁死 | 告警 + 审计事件 → 直接恢复 |
| 引擎运行中抛异常（LLM 网关报错、`EffectRejected`） | `failed`，无入口 | 可 `recover` |
| 已进 `recovery_blocked` | 无出口 | `retry_recovery` 可重试 |
| 本局角色被删除/改名、`schema_version` 被 bump | 锁死 | `incompatible` → 仍拒绝（见 §3.1） |
| checkpoint 缺失/真损坏 | 锁死 | 仍拒绝（不变） |

### 3.1 「保证以后可恢复」的边界（必须对用户讲清楚）

"保证"覆盖到下面第 1-3 行，第 4-6 行是**设计边界**，靠工程约定而非代码兜住：

| 情形 | 能否恢复 | 兜底手段 |
| --- | --- | --- |
| 同版本中断、崩溃、进程被杀 | ✅ 自动 | 已有能力 |
| 代码改动但本局角色声明未变（含只改 Hook、只加别的角色） | ✅ 自动 | B 的 `compatible` |
| 本局角色声明/契约变了、提示词或模型配置变了 | ✅ 一次确认 | B 的 `drift` + D4 降级 |
| 本局角色被从代码**删除**（无别名） | ❌ | 编码约定：角色 id 永不删（Task 8） |
| 角色 id **改名** | ❌ | 编码约定：保留旧 id 别名指向同一 Spec/Hook（Task 8） |
| checkpoint 真损坏 / 被删 | ❌ | 已有事实源与备份纪律（SQLite `.backup`） |
| 模型配置被删除且无同款可替代 | ❌ | 恢复时按 `config_id` 报明确原因，重新配置同参数模型即可恢复 |

---

## 4. 任务分解

### Task 1: 冻结 digest helper 与键位贯通（K1/K2/K3）

**Files:**
- Create: `backend/app/persistence/recovery_compat.py`（本任务只放 `frozen_registry_digest`）
- Modify: `backend/app/core/scheduler.py`（`:297`、`:310`、`:499`）
- Modify: `backend/app/core/game_engine.py`（`:1883-1886`）
- Test: `backend/tests/test_recovery_compat.py`（新建）

**Interfaces:**
- Produces: `frozen_registry_digest(state: GameState, registry: RegistrySnapshot) -> str`
- Consumes: `GameState.registry_digest`（已存在，`checkpoint_codec.py:366` 只解码不比较）

- [x] **Step 1: 写失败测试**：`state.registry_digest = "a"*64` 时 `frozen_registry_digest` 返回该值；为空/非 hex 时回退 `registry.digest`。
- [x] **Step 2: 确认失败**（`python -m pytest tests/test_recovery_compat.py -q`）。
- [x] **Step 3: 实现 helper**，并在 `scheduler.py:499`、`scheduler.py:310`、`game_engine.py:1885` 把 `self.registry.digest` / `scheduler.registry.digest` 换成它（`scheduler.py:297` 留给 Task 2）。
- [x] **Step 4: 回归**：`tests/test_point_journal.py`、`tests/test_scheduler.py`、`tests/test_engine_recovery.py` 全绿（新局 digest 未变，键位应与现在完全一致）。

**验收**：把 registry 换成「多注册一个假角色」的快照后，用同一 `GameState` 生成的 `PointKey` 与 request `token` 与换之前**逐字节相同**。

---

### Task 2: role resource marker 只覆盖资源声明（R1/K4）

**Files:**
- Modify: `backend/app/core/role_runtime.py`（`:67-98`）
- Modify: `backend/app/core/scheduler.py`（`:297`）
- Test: `backend/tests/test_effect_applier.py:842` 一带（现有 marker 用例所在地）+ 新增 `backend/tests/test_role_runtime.py`

**Interfaces:**
- `initialize_role_resources(state, specs, config_version)` 签名不变；marker 计算改为只覆盖 `declared`（排序后的 seat→resource→value）与资源键集合，**不再混入** `config_version` 与整份 `spec.stable_digest()`。
- 新增窄接口供 Task 5 的 `drift` 路径使用：`adopt_resource_declaration(state, marker)`（只更新 `runtime.resource_setup_digest`，**不重放** SET_RESOURCE 效果）。

- [x] **Step 1: 写失败测试**：同一 `state` + 资源声明相同但 `tags`/`instructions`/`initial_private_data` 变化的 spec → marker 一致，不抛 `EffectRejected`；资源**新增/改值/删键** → marker 变化（供上层判 `drift`）；`adopt_resource_declaration` 后已消耗资源（如 `has_antidote=False`）**不被重置**。
- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现**，并让 `scheduler.py:297` 传冻结 digest（保持签名语义一致）。
- [x] **Step 4: 回归**：`tests/test_effect_applier.py`、`tests/test_context_projector.py` 与各角色扩展测试（`test_guard_extension.py` / `test_idiot_extension.py` / `test_werewolf_king_extension.py` / `test_knight_extension.py` / `test_wolf_beauty_extension.py` / `test_old_drunkard_extension.py`）全绿。

**验收**：模拟 `a075933` 那次改动（给 `WOLF_BEAUTY_SPEC` 加 `initial_resources={"self_kill_forbidden": 1}`）后，恢复一个已开局的对局：首个 `NIGHT_ACTION` 调度点不再抛 `EffectRejected`，且该座位已消耗的资源保持不变。

---

### Task 3: codec 两道门改为内部一致性（G1/G2）

**Files:**
- Modify: `backend/app/persistence/checkpoint_codec.py`（`:291`、`:459`，以及 `_decode_journal_entry` 需要拿到 state 行的 digest）
- Test: `backend/tests/test_checkpoint_codec.py`（`:226`、`:296` 两条断言要改语义）

- [x] **Step 1: 改测试**：把「root digest 与 live 注册表不符 → 报错」改成「root digest 与 `state.registry_digest` 不符 → 报错」；新增「root 与 state 自洽、但 live 注册表不同 → 解码成功」。
- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现**：`decode()` 先解 `state`，用其 `registry_digest` 校验 root 与每个 journal key；`_decode_request` 的 G3 保留，但对照 `spec.contracts` 时给出带 `role_id`/`contract_id` 的原因串。
- [x] **Step 4: 容忍历史/异常档**：`state.registry_digest` 为空或非 64-hex 时跳过该自洽校验（不因此拒绝），避免畸形档被新规则误杀成"损坏"。
- [x] **Step 5: 回归** `tests/test_checkpoint_codec.py` 全绿。

---

### Task 4: 兼容性判定模块（D2）

**Files:**
- Modify: `backend/app/persistence/recovery_compat.py`
- Test: `backend/tests/test_recovery_compat.py`

**Interfaces:**
- Produces: `assess(state: GameState, document: Mapping[str, object], registry: RegistrySnapshot) -> CompatReport`
- `CompatReport.level ∈ {"exact","compatible","drift","incompatible"}`，`reasons` / `blocking` 均为 `tuple[str, ...]`（稳定排序，可直接进日志与 API）。

- [x] **Step 1: 写失败测试**（参数化，覆盖 §3 矩阵每一行）：
  - 全等 → `exact`
  - 新增/删除**本局用不到**的角色 → `compatible`
  - 本局角色的 `tags`/`instructions`/`initial_private_data`/`initial_resources` 变化 → `drift`，且 `reasons` 含 role_id 与差异字段名
  - 本局用到的契约 digest 变化 → `drift`
  - 角色缺失 / `RoleSpec.schema_version` 变化 / 契约 `schema_version` 变化 / `effect_schema_version` 不匹配 → `incompatible`
- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现**（纯函数，零 IO，禁止读全局注册表）。
- [x] **Step 4:** 覆盖率必须 100%（含 `reasons` 排序分支）。

---

### Task 5: 服务层接入（G4/G5/G6/G7 + D3）

**Files:**
- Modify: `backend/app/services/game_service.py`（`_load_native_games` `:507-549`、`_mark_recovery_blocked` `:575-588`、`get_execution_info` `:551-573`、`_recover_game` `:784-864`、`_resolve_recovery_model_configs` `:1707-1787`、`_build_recovered_engine` `:1907-1913`）
- Modify: `backend/app/persistence/repository.py`（如需 `retry` 用的 transition 白名单）
- Test: `backend/tests/test_game_service_lifecycle.py:1169-1174`、`backend/tests/test_startup_registry_mismatch_quiet.py`、`backend/tests/test_game_service_durable_regressions.py:513`

- [x] **Step 1: 改测试**：
  - 启动隔离测试 docstring 改为「版本漂移不再进终态，只记录原因」；断言改为 `execution_status == "interrupted"` 且 `recovery_block_code == "registry_incompatible"`，且**只有一条** WARNING、无 ERROR。
  - 新增：`failed` 局可 `recover`；`recovery_blocked` 局 `retry_recovery` 在条件恢复后转 `running`。
- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现**：
  - `_load_native_games`：区分 `CheckpointError` 文案 → `checkpoint_corrupt` / `checkpoint_version_unsupported` / `registry_incompatible`；`drift`/`compatible` 类原因**不写终态**，只把原因写进 `recovery_block_code` 留在 `interrupted`。
  - `_recover_game(..., force: bool = False)`：`expected_statuses=("interrupted","failed")`；`assess()` 为 `incompatible` → `_mark_recovery_blocked(..., "registry_incompatible")` 并抛 `ValueError`；`drift` 且未带 `force` → 抛 `ValueError("role_declaration_drift" / "contract_incompatible")` 但**不**写终态；`force` 时先调 Task 2 的 `adopt_resource_declaration`，再续跑。
  - `prompt_digest` / `parameters` 漂移改告警 + 审计事件（`_mark_recovery_blocked` 之前）。
  - 新增 `retry_recovery(game_id)`：`recovery_blocked` → 清空 `recovery_block_code` 后重跑判定。
- [x] **Step 4: 回归**：`test_game_service*.py`、`test_engine_recovery.py`、`test_crash_recovery_subprocess.py` 全绿。

---

### Task 6: API 与前端（D3）

**Files:**
- Modify: `backend/app/api/routes/game_routes.py`（`_control_game` `:297-346`，新增 `POST /api/games/{id}/retry-recovery`）
- Modify: `frontend/src/components/lobby/GameCard.tsx`（`:146-155`）、`frontend/src/api/client.ts`（`:228`）、`frontend/src/components/lobby/GameList.tsx`（`:97`）
- Test: `backend/tests/test_game_routes.py`、`frontend/src/components/lobby/test/GameCard.test.tsx`、`GameList.test.tsx`

- [x] **Step 1: 写失败测试**：`failed` 显示「恢复对局」；`recovery_blocked` 显示「重试恢复」按钮且不置灰为永久不可用；`role_declaration_drift` / `contract_incompatible` 时弹二次确认（「以当前角色规则继续」）后才带 `force=true` 再调；`recovery_block_code` 以中文可读文案映射展示（新增映射表，未知码回退原串）。
- [x] **Step 2: 确认失败**。
- [x] **Step 3: 实现**（前端只加菜单项、确认框与文案，不加新页面）。
- [x] **Step 4:** `npm test` + `npm run lint` + `npm run build` 全绿；`eventCoverage.test.tsx` 若涉 `execution_status` 期望表需同步。

---

### Task 7: 审计与可观测

**Files:**
- Modify: `backend/app/services/game_service.py`（恢复成功/漂移各写一条事件）
- Modify: `backend/app/core/game_logger.py`（如需新 operation 名）
- Test: `backend/tests/test_game_service_lifecycle.py`

- [x] **Step 1: 写失败测试**：恢复成功写 `RECOVERY_RESUMED`（含 level / reasons / from_status / generation）；`force` 续跑写 `RECOVERY_FORCED`。
- [x] **Step 2-3:** 实现。事件只进 game.log 与 domain events，**不进**公开 DTO，避免把内部版本信息泄漏到观众视图（`audience_projector._EVENTS` 不动）。

---

### Task 8: 文档与门禁同步

**Files:**
- Modify: `AGENTS.md`、`CLAUDE.md`（把「注册表新增角色会改变 digest，处于 interrupted 的旧局无法续跑」改为新语义；**新增角色 id 永不删除、改名保留别名**的编码约定）
- Modify: `docs/architecture.md`（断点续跑一节 + `execution_status` 状态机 + 兼容性分级表）
- Modify: `MEMORY.md`（失败模式：digest 泄漏进持久化身份）

- [x] **Step 1:** 三处文档同改，`docs/README.md` 索引无需新增（`superpowers/plans/` 已收录）。
- [x] **Step 2:** 全量门禁：`cd backend && python -m pytest tests app --cov=app --cov-branch -q`。

---

## 5. 测试清单汇总

**必改（现有断言会被推翻）**
- `backend/tests/test_checkpoint_codec.py:226,296`
- `backend/tests/test_startup_registry_mismatch_quiet.py`（docstring + `:76-77`）
- `backend/tests/test_game_service_lifecycle.py:1169-1174`
- `backend/tests/test_game_service_durable_regressions.py:513`（角色工厂查不到 → 现在应报 `registry_incompatible`）
- `frontend/src/components/lobby/test/GameCard.test.tsx:92-160`、`GameList.test.tsx:213-219`

**必须新增**
1. 跨版本解码：用「多注册一个角色」的快照编码、用原快照解码 → 成功。
2. 跨版本续跑：`test_crash_recovery_subprocess.py` 加一个子进程用例，注入 digest 漂移后恢复，断言**事件序列与无漂移时逐条一致、无重复死亡、模型请求不重复调用**。
3. 本局角色声明变化（`tags` / `initial_resources`）→ 报 `drift` 而非锁死；`force` 前拒绝、`force` 后成功（就是 `a075933` 的真实场景）。
4. 契约 digest 变化 → 无 `force` 拒绝、有 `force` 续跑。
5. `failed` 局 recover 成功；`recovery_blocked` 局 `retry_recovery` 成功。
6. 真损坏（root/state digest 不自洽、JSON 截断）仍拒绝且码为 `checkpoint_corrupt`。

## 6. 风险与验证

| 风险 | 级别 | 验证手段 |
| --- | --- | --- |
| journal 键跨版本稳定后，**已完成调度点被重放**导致重复伤害/重复发言 | 高 | 复用 `test_crash_recovery_subprocess.py` 的断言风格：恢复后比对 `runtime.commits`、`accepted_action_keys`、`model_requests.status`；现有三层幂等（`EffectApplier` 按 `action_key`、`ModelInvocationService` 按 `request_id`）是主要防线，**必须逐点验证，不得只靠推理** |
| 冻结 digest 让「本局 A 版代码 + 全局 B 版代码」并存，恢复后行为与旧局不一致 | 中 | 恢复入口写 `drift` 审计事件；前端二次确认展示本次恢复的 level 与原因 |
| `resource_setup_digest` 判定放宽后，真实资源变更被静默采纳 | 中 | Task 2 反向用例：资源增删/改值必须让 marker 变化并上报 `drift`；`adopt_resource_declaration` 不得重置已消耗资源 |
| 旧档 `state.registry_digest` 缺失导致新校验误判 | 中 | Task 3 Step 4 的回退分支 + v1 老档样例测试 |
| `recovery_block_code` 值域变更影响前端映射与旧档展示 | 低 | 前端映射表未知码回退原串；`test_game_routes.py` 断言新值 |

## 7. 滚动与回滚

- 建议按 Task 顺序分 8 个 commit 落地，每个 commit 自带测试。**Task 1-5 需一起上线**才有完整效果：Task 3/5 打开判定，Task 2 防止「恢复后首个调度点崩成 `failed`」，Task 1 保证键位跨版本稳定（只做 Task 1 不改变任何判定）。
- 回滚：本方案只新增一个模块并放宽判定，回滚到旧提交即恢复「严格全等」语义；`recovery_block_code` 新旧值域兼容（旧 UI 对未知码回退原串），无需数据迁移。
- 上线前先结束进行中的对局（现状如此）；上线后 `recovery_blocked` 的旧档可以试 `retry_recovery`（大概率能捞回来），但**不作为验收项**。

## 8. 待确认问题

1. ~~是否要 C 方案~~ → **已决：不做**（2026-09-28，旧档不救，C 的主要成本随之消失、剩余收益不抵复杂度）。
2. `drift`（本局角色声明/契约变化）默认「需一次确认」是否可接受？还是要求一律拒绝、只保留 `compatible` 自动续跑？（默认取"一次确认"，因为它直接决定 `a075933` 那类改动后能否续跑）
3. `prompt_digest` 漂移改为告警续跑，是否接受「同一局前后提示词版本不同」的观感差异？（默认接受，否则"保证可恢复"不成立）
4. `failed` 开放 `recover` 是否需要次数限制？（默认不需要：恢复由人触发，不会自动刷调用；但可以在大厅显示 `interruption_count` 提示反复崩溃）

---

## 附录 A: 应急抢救旧卡死局（**不在本次范围**，仅备查）

旧档已明确不救，本附录只在"临时想捞某一局"时参考。理论上可手工改写 checkpoint：把 `checkpoint_json` 的 root `registry_digest`、每个 `point_journal[].key.registry_digest` 改成当前 digest。**但这不够**：`pipeline_runtime.resource_setup_digest` 无法用当前注册表复现（`c60b7ca2` 的差异来自 `WOLF_BEAUTY_SPEC.initial_resources`），恢复后首个调度点仍会抛 `EffectRejected`，把局从"锁死"变成"崩成 `failed`"。所以要么等 Task 2 上线后用 `retry_recovery` + `force`，要么手工同时改写 `resource_setup_digest`（需自行评估一致性风险）。

**不要**在正式库上直接试；先 `sqlite3 .backup` 一份副本。

## 附录 B: 复现本文档的实测结论（只读，不动正式库）

```bash
# 1. 拷副本（WAL 模式在 /mnt/e 上直接只读打开会 disk I/O error）
mkdir -p /tmp/wk && cp backend/data/wolfkiller.sqlite3* /tmp/wk/
```

```python
# 2. 确认所有 recovery_blocked 局的真实原因（不是 checkpoint_corrupt）
import sqlite3, json
from app.roles.registry import builtin_registry
from app.persistence.checkpoint_codec import CheckpointCodec
snap = builtin_registry.freeze(); codec = CheckpointCodec(snap)
db = sqlite3.connect('/tmp/wk/wolfkiller.sqlite3'); db.row_factory = sqlite3.Row
for gid, in db.execute("SELECT game_id FROM games WHERE execution_status='recovery_blocked'"):
    doc = json.loads(db.execute("SELECT checkpoint_json FROM game_checkpoints WHERE game_id=?", (gid,)).fetchone()[0])
    try:
        codec.decode(doc); print(gid, "OK")
    except Exception as e:
        print(gid, "->", e)

# 3. 证明内容完好：把两道 digest 门改成当前 digest 后再解码
doc['registry_digest'] = snap.digest
for e in doc['point_journal']: e['key']['registry_digest'] = snap.digest
state, orch = codec.decode(doc)      # c60b7ca2 → night / round 5
```

（`PYTHONPATH` 指向本机依赖目录，例如 `PYTHONPATH=$HOME/.wolfkiller-libs:$PWD/backend`。）
