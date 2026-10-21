# 统一模型调用：InvocationRunner 组件

> 日期：2026-10-07
> 状态：**进行中** —— P0 组件 / P1 repair 分类已落地；P2–P5 部分落地（见下表）。2026-10-21 复跑门禁：后端 **5043 passed / 100.00%**（statement + branch），前端 **695 passed / lint / build 全绿**；同日复核 P2 仍未开始、P3 梯子未迁移、P4 六处默认值未统一、P5 两项未落，结论与下表一致。
> 范围：`backend/app/agents/`、`backend/app/roles/base.py`、`backend/app/services/game_service.py`、`backend/app/core/sheriff_flow.py`、评测口径（`benchmark_metrics.py` / `audience_projector.py`）
> 问题来源：`docs/notes/2026-10-07-eval-review.md`（§2.4/2.5/2.11/3.1、§4 跨层不统一清单）

---

## 实施记录（随阶段推进更新）

| 阶段 | 内容 | 结果 | 落点 |
| --- | --- | --- | --- |
| P0 | 组件与纯函数（分类读取 / 结果类型 / 梯子 / 降级策略），不动调用点 | ✅ | `app/agents/invocation.py`（650 行，含 docstring）、`tests/test_invocation_runner.py`（45 例） |
| P1 | 解析失败改抛 `UnusableModelOutput`，repair 分支生效；失败码带上解析层 token | ✅ | `app/agents/llm_client.py`（`_structured_response` / `_require_schema_fields`）、`app/agents/retry.py`（`FailureClass` / `classify_failure`）、`app/services/game_service.py`（`_model_failure_code` / `_exception_diagnostics`）、`tests/test_failure_classification.py`（16 例）、`tests/test_game_service.py` |
| P2 | 迁移 `_command_provider` 与 `_night_turn`/`_night_invoke` | ⬜ 未开始（三条路已先回传 usage，见下） | — |
| P3 | 迁移 `durable_speak` / `durable_request_action`（含流式与投票 repair） | ⚠️ 成果已落、梯子未迁移 | 三条路回传 usage（`BaseRole._record_usage`）；`durable_speak` 修回 `on_chunk` 转发（生产上流式此前静默失效）。**梯子留在 `roles/base.py`**：组件只有 `kind` 没有 attempt 序号、没有 strict→JSON→压缩提示的传输升级、`timeout_type` 在任何路径都只能是 `None`、capture 在组件内 flush 与「先过消费闸门再 flush」的次序冲突（audit G2/G4/G6/G7）—— 硬搬会变成组件 + 角色两份梯子 |
| P4 | 降级策略统一（`exhausted_bucket` 接管 6 处手写默认值） | ⚠️ 决策点已接、六处尚未全部改走工厂 | 一次性契约（`per_game_limit == 1`）耗尽改为可恢复暂停：`DecisionPaused` / `Scheduler._command` 放行 / 账本记 `unknown` / 监听器落 `paused` / 同点只暂停一次。`_pause_once_or_degrade` 目前只接在 `_command_provider` 的兜底分支；夜间导演、白天弃票、警长票仍是各自手写的默认值（行为与 `exhausted_bucket` 一致，但没有统一走工厂） |
| P5 | 可见性与口径（账本 `outcome` → 报表 / `TECHNICAL_ABSTAIN` 覆盖警长票 / 投影字段门禁） | ⚠️ 部分 | 已落：报告保留窗口、真实 `llm_retry_rate`、回放 DTO 门禁补可选键样本断言。**2026-10-10 补记**：技术弃票按票种统计已落（`requests.votes.{exile,sheriff}`，认 receipt 不认请求状态，`METRIC_VERSION` → v4；见 `docs/notes/2026-10-10-known-unfixed-and-metric-leftovers.md`）。未落：账本 `call` 信封 → 报表的 `degraded` 类（要等 P2/P3 迁移把信封写进生产行）、警长票失败可见性（新观众事件） |

> **2026-10-09 补充（P2–P5 状态不变，但迁移时必须保留下面两条）**：在制品「降级动作自报家门」
> 已随 `503c92a` 提交，评测口径的三处有界修复随 `410c97f` 提交。与本计划直接相关的两点：
>
> 1. **durable 通道靠 `game_service._durable_command_payload()` 保住 `degraded`**：该字段是
>    `exclude=True`（`stable_digest()` 要它不进 `model_dump()`），而 durable 包装用
>    `model_dump()` + `model_validate` 往返 —— 直接用 `model_dump()` 会让标记在生产路径上消失，
>    写入口照旧把兜底计进 `game` 配额。**P2/P3 迁移到 `InvocationRunner` 时必须保留这一行为**，
>    门禁是 `tests/test_degraded_durable.py`（它走真实 `_command_provider`，把调用点改回去即红）。
> 2. **A1 的「四处一致」其实还有第五处：公开回放 DTO**。`degraded` 进了投影器白名单与回放分支、
>    却没进 `api/schemas.py`，含降级动作的对局在 `/api/games/{id}/logs` 上直接 500。
>    新门禁 `tests/test_game_routes.py::test_every_projected_public_payload_is_accepted_by_the_replay_dto`
>    覆盖「投影器放行的键（含**可选键**）必须被公开 DTO 接受」；同一条门禁也守住了
>    `pool_seats` / `silenced_seats` 的 list/tuple 之争（实测 13/253 局回放 500）。
>
> 仍未落地的与本计划相关的 P0 项（`docs/notes/2026-10-07-eval-review.md` §五）：
> **2026-10-21 复核**：三条路回传 token usage、技术弃票按票种统计、`retry_rate` 与报告保留策略均已落地
> （见 P3 / P5 行）；仍未落：账本 `outcome` 与三处可见性门禁、警长票失败可见性（新观众事件）。

### P1 实际落地的行为变化

1. **repair 分支从死代码变为可达**：解析失败现在抛 `UnusableModelOutput`（`ValueError` 子类，`ValueError` 的既有两个 `except` 分支不受影响），`classify_failure` 归入 `REPAIR` → 夜间 / 流水线 / 发言 / 投票四条梯子的第 2、3 次尝试从此会附格式提醒重问，而不是原样重发。实测四套策略的分类：`UnusableModelOutput`/pydantic `ValidationError` → repair；`ToolCallError`、超时、未分类 → 各自的旧分类不变（对齐 `tests/test_failure_classification.py`）。
2. **失败码带上解析层 token**：`response_no_tool_call` / `response_multiple_tool_calls` / `response_unexpected_tool` / `tool_args_not_object` / `response_missing_fields` 现在会进 `model_errors.log` 的 `failure_code`（此前一律 `model_invocation_error`）。
3. **诊断不再丢异常原文**：`_exception_diagnostics` 在没有 OpenAI 风格 `.body` 时回落到 `str(error)`，`response_missing_fields` 的「omitted required fields: target_seat」因此可读（此前被替换成 “model invocation failed”）。
4. **`retry_plan` 仍是唯一决策实现**：它现在委托 `classify_failure(policy, error)`，行为逐条等价（refusal 优先 → `retry_on` → `repair_on` → `retry_unknown_errors` → STOP），既有 `tests/test_retry.py` 全绿。

### 顺带修复的门禁问题（非本计划引入）

`git worktree` 上对 HEAD（`84e27db`）跑覆盖率门禁的结果是 **99.97% / 3 条语句 + 4 个分支未覆盖**——即 `624b147 feat(stream)` 提交时门禁已经是红的：

| 文件 | 缺口 | 本轮处理 |
| --- | --- | --- |
| `app/agents/llm_client.py:219->248, 238`（`extract_partial_speech` 的 “text 只是别的键的子串” 与未知转义两条路径） | 1 stmt / 2 br | ✅ 在 `app/agents/test/test_llm_client_streaming.py` 补 3 个参数化用例 |
| `app/agents/test/test_llm_client_streaming.py:214`（测试自己的 `on_chunk` 从未被调用） | 1 stmt | ✅ 该用例补一个能提取出文本的 chunk |
| `app/agents/llm_client.py:702->700`（无回调时流式循环回边） | 1 br | ✅ 补“无回调、两个 chunk”用例 |
| `app/core/game_engine.py:2331`（`TypeError` 与 `on_chunk` 无关时的 re-raise） | 1 stmt | ✅ `tests/test_game_engine.py` 补一例 |

**实现偏差（相对初始方案）**：

1. `FailureClass` 落在 `app/agents/retry.py`（而非新组件里），并收敛为**四类重试决策**（`REFUSED / TRANSPORT / REPAIR / STOP`）。原因：决策必须与 `RetryPolicy` 同处一地才不会出现第二份默认值；而“能力拒绝 / 结构性错误”不改变决策（落 `STOP` 或按 `retry_unknown_errors` 视作 transport），**失败命名**已有唯一词表 `classify_provider_error`（提供方层）与 `UnusableModelOutput.code`（解析层）。初始方案里的 `CAPABILITY`/`STRUCTURAL` 因此并入 `STOP` + 命名 token。
2. `RetryBudget` 不新建类型，直接复用 `RetryPolicy`。
3. 组件的 `send` **必须包在 try 内**（初版把 `send` 放在 try 外，只有 `parse` 被分类）：客户端是在 `send` 内部抛 `UnusableModelOutput` 的，包在外面等于让最该 repair 的失败形态继续走原样重发。`run_sync` 收到 async `send` 时用一个专用 `_SyncSendViolation` 转成 `TypeError`，避免把接线错误当成可重试的 provider 故障。
4. `exhausted_bucket` 只推导两条规则（`call_kind == "speech"` → DEGRADE；`per_game_limit == 1` → PAUSE；夜间导演 `contract is None` → RAISE），**暂不**启用“强制动作（`actions_requiring_target`）即 PAUSE”：按本批 30 局实测，它会让 12+ 次失败变成暂停。强制动作的损失改由 P5 的“可见性”兜住，待有真实暂停/恢复成本数据后再决策。
5. 账本载荷用**信封**（`{"payload": …, "call": {…}}`）：调用点的载荷形状原样保留在 `payload` 里，驱动自己的元数据放 `call`，两者永不撞键。`decode` 需同时接受信封与旧行（P2/P3 迁移时必须做到，否则在跑对局的缓存行会解不出来——`recovery_compat` 不管载荷形状）。

---

## 1. 目标

把「一次逻辑模型调用」的生命周期（发送 → 解析 → 重试 → 账本 → 遥测 → prompt 采集 → 降级决策）收进一个组件，使五个调用点只声明「怎么发、怎么解析、预算耗尽后怎么办」。

**验收标准**

| # | 标准 | 现状 | 目标 |
| --- | --- | --- | --- |
| A1 | 同一场失败在 `model_errors.log` / 账本 `outcome` / `llm_calls.log` / 观众流四处给出同一 `failure_code` 与状态 | 37 / 25 / 27 / 1 | 四处相等 |
| A2 | 「模型答在 content 里」时 repair（换问法）分支被真实触发 | 0/37 | ≥1，且可由测试复现 |
| A3 | token 记账覆盖所有调用路径 | 42.3% | >95% |
| A4 | 降级不得计入 `action_counts`；一次性技能不得静默作废 | 猎人枪被记为已用 | 断言级门禁 |
| A5 | 「系统异常/系统超时/代投弃权」不得进观众事件与玩家提示词 | 19 + 15 条 | 0 |
| A6 | `pytest tests app --cov=app --cov-branch` 全绿且 100% 覆盖 | 全绿 | 全绿 |

**非目标**：不改规则语义、不动 `RoleSpec`/`ActionContract`、不改 `providers/` 的 transport 选择、不新增第三方依赖。

---

## 2. 现状：五个入口

| # | 入口 | 调用者 | 结果形状 | 谁的重试 | 预算耗尽后 | usage | 账本 request_id |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `_night_invoke`（`game_service.py:1444-1563`） | NightDirector / SheriffDirector | JSON `str` | `_night_turn` + `_NIGHT_RETRY_POLICY` | 抛出 → `status=failed` | ✓ | `night:*` |
| 2 | `night_invoke`（恢复 `:2495-2545`） | 同上 | 同上 | 同上 | 同上 | ✓ | 同上 |
| 3 | `_command_provider.durable_provider`（`:2818-2870`） | Scheduler | `PipelineActionCommand` | 内联 `while` + `_PIPELINE_RETRY_POLICY` | 吞掉 → `pass` + 「系统异常」 | ✗ | `pipeline:*` |
| 4 | `durable_speak`（`:2327-2363`） | 白天发言 | `{"text":…}` | `BaseRole` 自建循环 + `_SPEECH_RETRY_POLICY` | 模板发言 | ✗ | `speech:*` |
| 5 | `durable_request_action`（`:2365-2430`） | 投票 | `{"command":…,"technical_failure_code":…}` | `BaseRole` 手写三段 | 技术弃票 | ✗ | `action:*` |

---

## 3. 设计

### 3.1 边界

`app/agents/invocation.py`：组件负责失败分类、重试梯子、usage 合并、账本写入、遥测、prompt 采集、统一结果；**不**负责 prompt 渲染、transport 选择（留在 `LLMClient`）、领域解析规则（调用点传入）、观众投影。

### 3.2 接口

```python
class FailureClass(StrEnum):        # 重试决策
    REFUSED = "refused"             # 503 容量：一次长等待，二次终局
    TRANSPORT = "transport"         # 原样重发
    REPAIR = "repair"               # 换问法重发
    STOP = "stop"                   # 不重试

class FailureBucket(StrEnum):       # 预算耗尽后的归宿
    DEGRADE = "degrade"             # 产出带 failure_code 的降级值
    PAUSE = "pause"                 # 可恢复暂停（PipelinePaused）
    RAISE = "raise"                 # 向上抛，由调用层决定

def classify_failure(policy: RetryPolicy, error: BaseException) -> FailureClass
def exhausted_bucket(contract: ActionContract | None, *, call_kind: str) -> FailureBucket

@dataclass(frozen=True)
class CallSpec:                     # request_id/action_position/frozen_request 逐字节由调用点提供
    request_id: str
    action_position: str
    frozen_request: Mapping[str, object]
    call_kind: str                  # night | action | speech | pipeline
    actor_seat: int
    round_number: int
    phase: str
    contract_id: str | None = None

@dataclass(frozen=True)
class RawAnswer:                    # send 的返回值
    value: object                   # 交给 parse（LangChain 响应或 StructuredResponse）
    transport: str
    usage: LLMUsage | None = None
    elapsed_ms: int = 0

@dataclass(frozen=True)
class CallOutcome(Generic[T]):
    value: T
    degraded: bool
    failure_code: str | None
    timeout_type: str | None
    attempts: int
    transports: tuple[str, ...]
    usage: LLMUsage | None
    elapsed_ms: int

@dataclass(frozen=True)
class CallRecord:                   # 一次逻辑调用的遥测（由调用点转写 llm_calls.log）
    spec: CallSpec
    attempts: int
    transport: str | None
    prompt_chars: int
    elapsed_ms: int
    usage: LLMUsage | None
    parse_result: str               # ok | degraded | error
    failure_code: str | None
    timeout_type: str | None

@dataclass(frozen=True)
class FailureRecord:                # 预算耗尽时的失败遥测（由调用点转写 model_errors.log）
    spec: CallSpec
    failure_code: str
    attempt: int
    error: BaseException

@dataclass(frozen=True)
class CallAdapter(Generic[T]):
    send: Callable[..., RawAnswer]                 # send(kind, sink) -> RawAnswer（async 可返回 awaitable）
    parse: Callable[[RawAnswer], T]
    encode: Callable[[T], Mapping[str, object]] | None = None   # 写账本（有账本时必填）
    decode: Callable[[Mapping[str, object]], T] | None = None   # 读缓存（有账本时必填）

class InvocationRunner:
    def __init__(self, *, ledger=None, record_call=None, record_failure=None,
                 capture=None, sleeper=time.sleep, async_sleeper=asyncio.sleep) -> None
    def run_sync(self, spec, adapter, *, policy, on_exhausted) -> CallOutcome[T]
    async def run_async(self, spec, adapter, *, policy, on_exhausted) -> CallOutcome[T]
```

### 3.3 账本

`request_id` / `action_position` / `frozen_request` 逐字节沿用调用点提供值；`normalized_result` 新增 `"outcome"` 键（`status` / `failure_code` / `attempts` / `transports`），旧行缺该键时由消费方回落。**不改** `model_requests.status` 列取值、不改 `PointKey`/label、不改检查点字段集、不改 `RoleSpec`/`ActionContract`。

---

## 4. 门禁测试

1. `tests/test_invocation_runner.py`：四类失败决策 × 三种归宿 × sync/async × 账本复用/新建 × 遥测/采集钩子。
2. `tests/test_failure_classification.py`：`classify_failure` 与 `retry_plan` 的等价性；解析失败必须落 `REPAIR`。
3. `tests/test_failure_visibility.py`（P5）：一次失败在四处可见且 `action_counts` 不自增。
4. `tests/test_projection_field_gate.py`（P5）：投影字段 ⊆ 领域事件字段。

**必须保持绿**：`test_llm_call_telemetry.py`、`test_llm_usage_capture.py`、`test_game_service_durable_regressions.py`、`test_base_role.py`、`test_vote_result_projection.py`、`test_night_flow.py`、`test_audience_technical_abstain.py`。

> **2026-10-09 执行记录（本次）**：P2 未开始；P3 只落成果（usage + 流式）不搬梯子；P4 落了「让 PAUSE 真正可用」的一跳，六处默认值仍分头手写；P5 落了保留窗口 / `llm_retry_rate` / 门禁补丁，账本信封与警长票可见性未落。**文档与代码不符的十处**（本次核对，以代码为准）：`CallSpec` 首字段 `game_id` 必填、`FailureRecord.attempts`（非 `attempt`）、`encode(value, outcome)` 两参、`__init__` 还有 `sink_factory`、`run_sync/run_async` 另有 `prompt_chars/model_id/provider_profile/execution_generation/recovery` 五个关键字参数、信封键是 `payload`/`call`（不是 `outcome`）、`exhausted_bucket` 形参类型是 `object | None`、§2 的五行号已过期、P1-1 声称 repair 覆盖投票（投票梯子不调用 `retry_plan`）、`build_prompt_record` 注释说「四个调用点」实际是五个。
