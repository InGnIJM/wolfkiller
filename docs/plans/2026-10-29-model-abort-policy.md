# 模型调用失败分类与自动中止（Abort）策略

> 日期：2026-10-29
> 状态：**已实现（P1）** —— 失败词表归一化、provider 健康监控、live 暂停 / benchmark 失败、暂停原因落库与前端展示；P2（设置页、no-progress、全局 registry）待后续。
> 触发事故：2026-10-28 的 `53ab4c55` 对局，12 个座位共用一个 Gemini 网关，上游 HTTP 400「User location is not supported」，所有调用降级为模板发言/技术弃票，牌局空转 9 天。

---

## 1. 目标

把“LLM 调用失败”从“一律降级、继续空转”改为按失败性质分级：

- **继续**：重试/修复；预算耗尽后降级（模板发言、技术弃票、`pass`），牌局继续。
- **暂停**（live）：provider 级系统性故障，`execution_status=paused`，修好后 resume 从同一 checkpoint 续跑。
- **失败**（benchmark / 结构性缺陷）：`failed`，诊断性终止。
- **忽略**：取消、陈旧结果、自愿弃票等。

---

## 2. 失败处置矩阵（摘要）

| 类别 | 典型判据 | 处置 |
| --- | --- | --- |
| 传输/超时/429/5xx/连接 | `provider_timeout` / `provider_rate_limit` / `provider_server_error` / `provider_connection_error` | 继续；窗口内跨座持续 → 暂停 |
| 503 容量拒绝 | `provider_unavailable` | 继续（一次长等待）；跨座重复 → 暂停 |
| 401/403/402/404/地区限制/上下文超限 | `provider_auth_error` / `provider_quota_exhausted` / `provider_model_not_found` / `provider_region_blocked` / `provider_context_too_large` | 暂停（live）/ 失败（benchmark）；≥2 座快熔断 |
| 400/422 工具 schema | `provider_bad_request` | 继续（JSON fallback / repair）；仅 ≥3 契约且 ≥3 座时暂停 |
| 内容审查 / 模型拒答 | 200 但无可用载荷 | 继续（repair → 降级） |
| 解析/ schema / 发言质量 | `response_*` / `action_payload_*` / `speech_*` / `target_required` / `strict_tool_*` | 继续；跨 ≥3 座连续 ≥4 次 → 质量暂停 |
| 规则校验 / `EffectConflict` | `action_validation_error` / `effect_rejected` | 继续（重试/降级），不计 provider |
| 一次性契约耗尽 | `per_game_limit == 1` | 暂停（`one_shot_budget_exhausted`） |
| 结构性错误 | `PipelinePaused` / `EffectRejected` / `CommitConflict` / 检查点损坏 | 失败 |
| 取消 / 陈旧 / 调度超时 | `CancelledError` / `StaleModelResult` / `phase_deadline` / `missing_vote_result` | 忽略 |
| benchmark | provider/质量/no-progress | 失败 item（不自动 resume） |

完整枚举见实现门禁 `tests/test_failure_taxonomy.py`（源码里的每个 `code="..."` 字面量必须注册）。

---

## 3. 实现

### 3.1 词表

- `app/agents/providers/errors.py`：`classify_provider_error` 增加 401/403/402/404/408/413/504、5xx 裸状态、地区/额度/上下文消息识别；新增 `provider_auth_error` / `provider_quota_exhausted` / `provider_model_not_found` / `provider_context_too_large` / `provider_region_blocked`。
- `app/agents/failure_taxonomy.py`：`FailureKind`（TERMINAL / TRANSIENT / REQUEST_SHAPE / QUALITY / IGNORED / INTERNAL）、`normalize_failure_code`、`sample_kind`（过滤 `*_retry`、`strict_capability_fallback`、`phase_deadline`、`missing_vote_result`）。
- 四处调用点（`game_service._model_failure_code`、`roles/base._provider_failure_code`/`_speech_failure_codes`、`ModelInvocationService._failure_code`、夜间错误遥测）统一走 `normalize_failure_code`。

### 3.2 监控与守卫

- `app/agents/model_health.py`：`ProviderHealthMonitor`（按 `provider_key` 的窗口/连续/比例/质量/终局规则、线程安全、可注入 clock）；`AbortPolicy`（env `MODEL_ABORT_*`，默认 live `pause`、benchmark `fail`）。
- `app/core/model_health_guard.py`：`ModelHealthGuard`（`check(seat)` 抛 `ProviderOutagePaused` / `ProviderOutageFailed`；`record_telemetry` 从 `llm_calls` 行取样；`record_client_failure` 兜底；`reset`）。
- 采集：`GameLogger.__init__(on_llm_call=...)`，`log_llm_call` 写完后回调；所有五条模型路径都经过它。
- 执行前检查：`GameEngine.speak/vote`、夜间讨论/狼票/助理发言、警长竞选/退水/投票/传徽/发言方向、`_command_provider` 入口。**必须在宽泛 `except Exception` 之外**。
- 暂停落库：`games` schema v4 新增 `pause_code` / `pause_detail_json` / `paused_at`；`transition_execution(..., pause_code=, pause_detail=)`，resume/recover 清空；监听器广播 `paused_state`（含 reason）。
- benchmark：`ProviderOutageFailed` 落 `failed`（不置 ERROR phase），`BenchmarkGameExecutor` 对 `provider_*`/`quality_*`/`no_progress` 的 paused 局不自动 resume。
- 公开面：`GameListItem` / `GameDetailResponse` / `GameExecutionResponse` / 观众快照 `state` 增加可选 pause 字段；前端 `isPausedState` 接受可选 `code`/`reason`，`CenterDisplay`/`GameBoard` 显示原因。

### 3.3 默认阈值

| 项 | 默认 |
| --- | --- |
| 连续失败 / 跨座 | 5 / 3 |
| 窗口 | 120s |
| 失败率 / 最小样本 | 0.8 / 10 |
| 终局错误跨座 | 2 |
| 质量失败连续 / 跨座 | 4 / 3 |
| 单座位策略 | `warn`（可配 `pause`） |
| 重新打开次数 | 2（之后 resume 需 `force`） |
| 自动恢复 | 关 |

---

## 4. 门禁

- `tests/test_failure_taxonomy.py`：词表覆盖 + 源码 code 字面量注册。
- `tests/test_model_health.py` / `test_model_health_guard.py`：阈值、窗口、快熔断、质量、单座位、reset/reopen、env。
- `test_engine_health_guard.py`：engine turn 边界在宽泛 catch 之前。
- `test_game_service_health.py` / `test_game_service_lifecycle.py`：暂停原因落库、失败分支、恢复、广播容错。
- `test_benchmark_game_executor.py`：systemic pause 不自动 resume。
- `test_game_repository.py`：v4 迁移与 pause 字段。
- 前端：`websocket.test.tsx`（可选 code/reason）、`CenterDisplay`/`GameBoard`/`gameStore`。

---

## 5. 后续（P2）

- 设置页 / `GET/PATCH /api/settings/game-abort`（UI > env > 默认）。
- `no_progress_rounds`（连续 N 轮 0 死亡 + 0 放逐 → 暂停），默认关。
- 进程级 `ProviderHealthRegistry`：跨局聚合、benchmark 建局前预检、`GET /api/models/health`。
- 失败模型自动切换（failover）——当前只暂停，不换模型。
