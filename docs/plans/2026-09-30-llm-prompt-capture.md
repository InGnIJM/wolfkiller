# LLM prompt 原文采集方案

> 目标：在现有日志机制之上，把「模型真实收到的 prompt 原文」落盘，形成 `(prompt, completion)` 训练对，用于后续基于 4060 8G 微调小模型替代 API 模型。
> 硬约束：零行为影响 / 不破坏断点续跑 / 不改 `parameters_digest` / 不泄漏密钥 / 失败无害 / 可开关 / 不阻塞事件循环。
>
> 状态：**采集侧已实现，开关与 UI 未实现**（收尾时补记，见下方「实现现状」）。
> 所有行号基于 `main` @ `16810d0` 实测。

---

## 实现现状（收尾时补记）

**已落地**：

| 项 | 落点 |
| --- | --- |
| 采集模块 | `app/core/prompt_capture.py`（`PromptSink` / `build_prompt_record` / `PromptCapture` / 取值解析）—— `d145ee2` |
| 角色侧记录 | `app/roles/base.py` 在调用前把真实 `messages` 交给 sink —— `c2f87fe` |
| 持久调用链接入 | `app/services/game_service.py` 四条链路（speech / contract_action / pipeline_action / night）接线 + 重放不重采门禁 —— `36c1532` |
| 插桩闭包修复 | 全员共用同一座位身份的晚绑定 bug —— `f07b706` |
| 测试 | `app/core/test/test_prompt_capture.py`、`tests/test_base_role.py`、`tests/test_game_service_prompt_capture.py`、`tests/test_role_instrumentation.py` |

**未落地**（本方案 C6 / C7 / C9–C14）：`app/stores/app_settings_store.py`、
`app/api/settings_schemas.py`、`app/api/routes/settings_routes.py`、前端
`appSettingsStore.ts` / `SettingsPage.tsx` / `/settings` 路由，以及 `AGENTS.md` / `CLAUDE.md` /
`docs/architecture.md` 三处文档同步。

**当前开关只有环境变量**：`GameService._prompt_capture_setting()` 是返回 `None` 的桩，
源码注释写明 *"The settings store is wired in a later change"*。因此
「一局要么全采要么不采」目前由 `prompt_capture_marker_exists()` 这个闩锁保证：
建局按 `resolve_capture_setting()` 决定，恢复时只要 `games/<id>/llm_prompts.jsonl` 存在就继续采。

**实现形状偏差**（相对本方案初稿）：

1. C2′ 计划把 `resolve_capture_setting` / `capture_enabled` 放在 `app/config.py`，实际与采集模块
   同住在 `app/core/prompt_capture.py`。
2. C4′ 计划让 `GameEngine` 持有采集句柄，实际句柄按对局缓存在
   `GameService._prompt_captures`（`_prompt_capture()` 惰性创建、`_close_prompt_capture()` 释放）。
3. C8 的「恢复一律传 `False`」没有实现成入参，语义由上面的 marker 闩锁承担。

---

## 0. 侦察结论复核（先纠正，再设计）

我逐条读了代码并查了事实源，结论如下。

| # | 你的结论 | 复核 | 说明 |
| --- | --- | --- | --- |
| 1 | completion 已有 16991 条 | 🟢 **准确但口径要收紧** | `model_requests` 共 19030 条，但 **night 链路 5070 条里有 2002 条 `failed`（`normalized_result_json` 为 NULL）**。真正可用的 completion = 16984 `consumed` + 7 `resolved` = **16991**（与你说的一致），剩下 2039 条是 `failed/prepared/in_flight/unknown`，**没有 completion 也就没有训练对**。 |
| 2 | 四条链路的条数 5070/6222/5773/1965 | 🟢 完全准确 | 实查 = 5070 / 6222 / 5773 / 1965，无残留 `other`。 |
| 3 | 夜间链路 digest 覆盖 messages，5070/5070 尾段吻合 | 🟢 **实测完全吻合** | `request_id` 尾段 == `request_digest[:16]`：**ok=5070, bad=0**。这是本方案最强的验证器，见 §5.3。 |
| 4 | 3/4 路径的 prompt 在 `provider_call` 闭包内才渲染 | 🟡 **表述需修正** | 精确地说：<br>• `speech`：prompt 在 `BaseRole.speak()` 内部（`base.py:115`）渲染，`_instrument_roles` 的闭包**只是不持有它**，而不是「在闭包里才生成」。<br>• `contract_action`：同上（`base.py:430`，注意是 `_build_contract_action_prompt`）。<br>• `pipeline_action`：**prompt 确实就在 `_command_provider` 的 `provider()` 里边**（`game_service.py:2363`），`durable_provider` 手握 `provider`，不需要「下沉」就能拿到。 |
| 5 | 构造点在 1282 / 2058 / 2090 / 2176 / 2437 行附近 | 🟢 准确 | 精确为 **1282（create 夜间）/ 2058（speech）/ 2090（contract_action）/ 2176（recover 夜间）/ 2437（pipeline_action）**。注意夜间有**两个**构造点（建局 + 恢复），改动要成对。 |
| 6 | `game_logger._write_line` 的写入方式是否适合 10K 文本 | 🔴 **不适合，必须另写** | `_write_line` 是 `open(..., "a")` → `write` → `close`，**同步、无缓冲、无锁**。在**本机 WSL 的 `/mnt/e` 挂载上实测每次 10.5 ms**（16.6 KB payload，60 次均值）；持久 fd 只要 **0.86 ms**。见 §4.3 实测数据。 |
| 7 | 「一天 prompt 没人落盘」 | 🟢 准确 | 全仓 `grep` 无任何写 prompt 原文的代码；`log_llm_call` docstring 明写 *"never prompt text"*，`log_model_error` 明写 *"without prompts or credentials"*。 |
| 8 | 「日志覆盖了 1.5%」 | 🟢 量级正确 | 按 (prompt, completion) 对算：可用 16991 条，其中 night 仅 3061 条。而 `llm_calls.log` 现存 22440 行（action 11313 / speech 6120 / night 5007，**action 一条请求平均落 1.96 行**，因为 `_request_action_with_transport` 每个 attempt 都写一行）。所以「prompt 覆盖率」= 0%，「completion 覆盖率」约 76%。 |
| 9 | （补充）`prompt_chars` 的口径 | ⚠️ 新发现 | `base.py:599` 的 `prompt_chars = sum(len(m.content))` 是 **system + human 全量**；而 `_night_invoke`（`game_service.py:1269`）的 `prompt_chars` 只统计 `messages` 里的 content（夜间 messages 本就含 system，所以一致）。所以「中位 10032」这个数字对应的是**完整 API prompt 字符数**，采集时不要只存 human 段，否则会再次低估。 |

**一条补充风险，你没提但成立**：`speech`/`pipeline` 链路的 prompt 里含 `_fallback_target()` 的 `RANDOM_HINT`，而那一步**会消费 `engine._rng`**（`game_service.py:2355-2362`）。所以「事后重新渲染一次 prompt」在 pipeline 链路上**会多消费一次 RNG，改变后续对局**——这条直接判死了「重渲染」路线，见 §1.3。

---

## 1. 可行性结论与两条路线对比

### 1.1 结论

**可行。** 采集点不需要碰模型调用本身，只需要把「已经存在但被丢弃的那个字符串」多传一个参数、多写一行文件。四链路里三条（night / speech / pipeline）的 prompt 在采集点**已经是现成对象**，只有 `contract_action` 需要把 prompt 从 `BaseRole` 里「带出来」一次。

### 1.2 路线 A：在四处构造点把 prompt 塞进 `frozen_request`

即在 `game_service.py` 的 1282 / 2058 / 2090 / 2176 / 2437 处给 `frozen_request` 加 `"messages"` / `"prompt"` 字段。

**否决，理由是致命的：**

- **会改变 `request_digest`。** `digest = sha256(规范化 frozen_request)`（`model_invocation_service.py:48`）。加字段 = 新 digest = **`night:{r}:{tool}:{seat}:{digest[:16]}` 这个 request_id 变了**。
- request_id 变了意味着：对局进行中重启后，`prepare_model_request` 拿新 request_id 查不到旧记录 → **不去重、直接重打一次 LLM**，等于**破坏断点续跑的「不重放」保证**（CLAUDE.md：「失败后精确续跑不重放」）。
- 对已完成的旧局，旧 `request_id` 与新代码算出的对不上，`CommitConflict` 风险与 digest 校验逻辑全部扰动作废。
- 即便只在采集副本上加字段，也**多一处 prompt 真相**，两处会漂移。

> 这条路唯一的好处是「digest 顺带成了 prompt 指纹」，但 §5.3 会给出**不改 digest 也能拿到同等验证强度**的做法，所以这个好处不值这个代价。

### 1.3 路线 B：下沉到 transport / provider 层统一拦截

即包一层 `LLMClient.invoke_action / invoke_json / ainvoke_json`（`llm_client.py:501/545/590`）。

- **优点**：一处收口，天然覆盖未来新增调用点，一个都不漏。
- **否决理由 1（会改变行为）**：`LLMClient` 内部**自带重试**（native tool 失败降级 JSON 重打，`llm_client.py:514-527` / `565-575`），`_invoke_json_action`（`base.py:827`）与 `_request_action_with_transport` 又叠加最多 3 次重试。下沉后**一次逻辑请求会落多条 prompt**，其中大部分是「没被采纳的尝试」。过滤只能靠猜，等于把噪声塞进训练集。
- **否决理由 2（拿不到关联键）**：`LLMClient` 是按座位创建的长生命周期对象（`game_service.py:1241`），**不知道 `game_id`**，更不知道 `request_id` / `call_kind` / `contract_id` / `window_id`。这些字段只在 `_instrument_roles` 与 `_night_invoke` 的闭包里可见。要补就得往 `LLMClient` 上塞可变上下文，那才是真的「污染核心」。
- **否决理由 3（架构边界）**：`agents/providers/` 与 `llm_client` 属于传输层，让它反向依赖「对局日志目录 / 轮次 / 座位」会把两层焊死。
- **否决理由 4（分支爆炸）**：三个 invoke 方法 × 各自的 tool/JSON/async 三分支，100% 分支覆盖要多写 6~8 个测试，成本远高于收益。

### 1.4 ✅ 推荐路线 C：三处真实取值 + 统一汇聚点写盘

把方案拆成两个互不耦合的部分：

1. **取值**：在**三个**真正拿得到 prompt 的地方，把**实际发出去的**消息对取出来，放进一个 per-call 的 `PromptSink`：
   - `_night_invoke` / `recover night_invoke` —— 本地已持有 `messages`（`messages` 就是 `[{role:system},{role:user}]`，见 `night_flow.py:258`）。
   - `durable_speak` / `durable_request_action` —— 让 `BaseRole` 把渲染出来的 `(system, user)` 写进 sink。
   - `durable_provider`（pipeline）—— 让 `provider()` 在**已经算完 `RANDOM_HINT` 之后、`invoke_action` 之前**把 `messages` 写进 sink（`game_service.py:2363-2367` 之间的位置）。
2. **写盘**：三个采集点都只调用**同一个** `PromptCapture.flush()`，落同一个文件、同一套 schema。汇聚点仍然是「逻辑请求级」的唯一收口，与 `ModelInvocationService` 一一对应。

**为什么推荐它：**

- **零行为影响**：不新增、不删除、不重排任何 LLM 调用；**不重渲染**（避开 §0.9 的 RNG 陷阱）；不改 `frozen_request`（避开 §1.2 的 digest 陷阱）；只在**响应返回之后**多一次文件写（§4）。
- **与事实源一一对应**：每条 prompt 记录携带 `request_id`，而 `request_id` 就是 `model_requests` 的主键后缀 → `(prompt, completion)` 天然可 join（§2.4）。
- **只在 `reused == False` 时落盘**：一次逻辑请求恰好一条 prompt，恢复重放不会重复采集。
- **可开关、可单测**：sink 是一个鸭子类型的纯数据对象，不碰 `GameState` / checkpoint / model_snapshot。

---

## 2. 具体设计

### 2.1 新增文件

#### `backend/app/core/prompt_capture.py`（新建，~110 行）

不放进 `game_logger.py`：`GameLogger` 的每个方法都往 `game.log` 家族写，而采集有独立的开关、独立的失败语义、独立的文件生命周期。放同目录新模块，架构上仍属 `core`（无外部依赖）。

```python
PROMPT_CAPTURE_FILENAME = "llm_prompts.jsonl"

def resolve_capture_enabled() -> bool: ...        # 读 env，容错（任何非法值 -> 默认）
def resolve_capture_max_chars() -> int: ...       # 读 env，<=0 / 非数字 -> 0（不截断）

class PromptSink:
    """per-call 缓冲：BaseRole / provider 往里写，wrapper 读它写盘。"""
    def record(self, system: str, user: str) -> None: ...   # 永不抛
    @property
    def messages(self) -> tuple[dict[str, str], ...]: ...
    @property
    def complete(self) -> bool: ...                         # 未被截断才为 True

def build_prompt_record(*, game_id, request_id, call_kind, round_number, phase,
                        seat, contract_id, transport, attempt, model_id,
                        request_digest, sink, now=None) -> dict:
    """纯函数：从 sink 构造一条 JSONL 记录（不触盘，可单测）。"""

class PromptCapture:
    """per-game 落盘器：持久 fd + 单锁串行 + best-effort。"""
    def __init__(self, data_dir, game_id, *, max_chars, record_enabled=True): ...
    def flush(self, record: dict) -> None: ...   # 永不抛；失败 -> 记一次 warning 并自降级
    def close(self) -> None: ...                 # 幂等，吞异常
```

关键实现约束：

- **持久 fd**：`os.open(path, O_WRONLY|O_CREAT|O_APPEND, 0o644)` 只做一次，`_ensure_dir` 只做一次，用 `self._path is not None` 缓存（不要每次 `os.makedirs`，实测 10.5 ms 里大头就是 open/close）。
- **单锁串行**：`threading.Lock`。因为同一个 `GameEngine` 的三个写线程来源不同——夜间走 `asyncio.to_thread` 的线程池、投票走 `asyncio.gather`（并发上限 `vote_concurrency=5`）、发言走事件循环。CPython 的文件对象对 >8 KB 的写入会绕过缓冲、拆成多次 `write()`，**不加锁会交错出损坏行**。（`_read_jsonl` 会跳过坏行，但那等于静默丢训练样本。）
- **锁内只做 `os.write`**：`json.dumps` 在锁外做（实测 0.022 ms/条，无所谓），锁持有时间 = 一次 `write` 系统调用。

### 2.2 修改文件

#### `backend/app/core/game_engine.py`（+6 行）

```python
# __init__，紧跟 self.game_logger = GameLogger(data_dir=data_dir)（第 229 行后）
self.prompt_capture = (                      # type: ignore[assignment]
    None
    if not resolve_capture_enabled()
    else create_prompt_capture(data_dir, self.game_id)   # 内部用 resolve_capture_max_chars()
)
```

```python
# speak()（第 2163 行），唯一一处 role.speak 调用（第 2188 行）
result = await role.speak(
    self.state, self.conversation_log, context, capture=self.prompt_capture,
)
```

- **不进 checkpoint**：`prompt_capture` 是运行期句柄，**不是** `GameState`/编排状态字段。`orchestration_codec` 的字段集是 `_exact` 精确匹配（`checkpoint_codec.py:80-83`），加任何字段都会让旧检查点报 `CheckpointError`。**这一条是红线**：采集相关的任何东西都不得进 codec、不得进 `PointKey`、不得进 `last_words_pending:*` 那类 label。
- **不改 GameConfig**：`_CONFIG_FIELDS = {"role_counts","reveal_on_death","enable_sheriff"}` 也是 `_exact` 匹配（`checkpoint_codec.py:40/329`）。所以**开关只能放 env / AppConfig，绝不能放 GameConfig**。

#### `backend/app/roles/base.py`（+18 行）

只做一件事：把「已经渲染好、马上要发出去的那两段文本」抄进 sink（**不是重新渲染**）。

```python
_UNSET = object()

# speak(): 渲染 prompt 之后（第 117 行后）
user_prompt = prompt
...
# _speak_with_tools 拿到 prompt 后立刻
if capture is not None:
    capture.record(self.prompt_builder.get_system_prompt(), prompt)

# request_action(): 组完 messages 之后（第 434 行后）
if capture is not None:
    capture.record(_content_of(messages[0]), _content_of(messages[1]))
```

- `capture` 只在 `speak(..., capture=...)` 与 `request_action(..., capture=...)` 上各加一个 **keyword-only 默认 `None`** 参数 → **现有 6 个 `role.speak(...)` 调用点与 `game_engine.py:2258` 的 `request_action` 都不用改**，`app/roles/test/*` 也不用改。
- `capture` 用鸭子类型（只调 `.record`），**不 import `prompt_capture`**——避免 roles 反向依赖采集细节。
- 重试变体（`_speak_with_tools` 的 `retry_prompt`，`base.py:199-205`）**另记一次** `record`，因为那确实是一条不同的 API 请求；`PromptSink.complete` 取所有 record 的与。
- `speak()` 里有个既有 bug：`except _ActionTransportTimeout` 分支里 `retry_messages` 可能未定义（`base.py:447-453`）——**本方案不碰它**，只在旁边插一行 `record`。

#### `backend/app/services/game_service.py`（+~45 行）

四处改动，全部是「多传一个参数 / 多调一次 flush」：

1. **`_instrument_roles`（第 2042 / 2070 行）**：两个 wrapper 各造一个 `PromptSink`，把捕获到的 prompt 与 `request_id` 一起在 `reused == False` 时 flush。

```python
async def durable_speak(state, conversation_log, context, *,
                        _seat=seat, _role=role, _call=original_speak):
    request_id = f"speech:{state.round_number}:{context}:{_seat}"
    sink = PromptSink() if capture is not None else None
    ...
    async def provider_call():
        value = await _call(state, conversation_log, context, capture=sink)  # 新增 capture
        return ({"text": value, "is_none": value is None}, None)
    result = await invocations.invoke(..., provider_call=provider_call, recovery=...)
    if capture is not None and not result.reused:
        capture.flush(build_prompt_record(
            game_id=game_id, request_id=request_id, call_kind="speech",
            round_number=state.round_number, phase=state.phase.value,
            seat=_seat, contract_id=context, transport="speech",
            attempt=1, model_id=model, request_digest=None, sink=sink,
        ))
```

2. **`_command_provider`（第 2335 行 `provider` + 第 2430 行 `durable_provider`）**：给 `provider()` 加 keyword-only `prompt_sink=None`，在 `messages` 组装完（第 2367 行后）调一次 `prompt_sink.record(system, prompt)`；`durable_provider` 造 sink 传入 `provider_call`，`invoke_sync` 返回 `reused == False` 时 flush。

3. **`_night_invoke`（第 1267 行，建局）与 `night_invoke`（第 2174 行，恢复）**：两处对称加 flush。夜间 messages 已是 `{"role": "system"|"user", "content": str}` 的列表（`night_flow.py:258`），**直接存原样**，不需要 sink。放在 `_log_night_llm_telemetry` 旁边（第 1347 / 2213 行），保证「prompt 记录」和「llm_call 元数据行」永远同点产生。

```python
if capture is not None and not durable.reused:
    capture.flush(build_night_prompt_record(
        game_id=game_id, request_id=request_id, round_number=round_number,
        seat=seat, tool_name=tool_name, messages=messages, digest=digest,
        model_id=getattr(llm_client, "model_name", None),
    ))
```

放在 `payload = durable.normalized_result` **之后**（响应已回），所以写盘不占 LLM 的关键路径。

#### `backend/app/config.py`（+~8 行）

见 §3。

#### 文档（不可省）

`AGENTS.md` / `CLAUDE.md` / `docs/architecture.md` 必须同步加一行：新增 `data/games/<id>/llm_prompts.jsonl`，说明它是**训练语料**、`llm_calls.log` 仍然只记元数据、开关是 env、**绝不进 checkpoint / model_snapshot / `_MODEL_PARAMETER_FIELDS`**。

### 2.3 存哪：新文件 `llm_prompts.jsonl`（推荐）

同目录 `backend/data/games/<id>/llm_prompts.jsonl`，与 `game.log` / `llm_calls.log` / `conversation.log` / `model_errors.log` 并列。

**为什么不像你允许的那样塞进 `llm_calls.log`：**

| 维度 | 塞进 `llm_calls.log` | 新开 `llm_prompts.jsonl` ✅ |
| --- | --- | --- |
| 既有契约 | `log_llm_call` docstring 明写 *"never prompt text"*，`game_summary._llm_aggregate` 按 `data` 的固定字段做聚合；加了 prompt 就是**在既有审计文件里破坏承诺** | 元数据契约原样不动，审计链与训练语料彻底分离 |
| 体量 | 22440 行 × ~10 KB ≈ **220 MB** 混进原本只有几 MB 的审计文件 | 独立文件，可单独设上限、单独删除、单独归档 |
| 开关语义 | 开关=「同一文件里某个 key 在不在」→ 下游解析器必须容错 | 极端开关 = 「文件在不在」；关闭时**一个字节都不写** |
| 磁盘压力 | 写不进去会连带影响审计行（同一次 `_write_line` 调用） | 写不进去只丢训练样本，审计完全无感 |
| 单测 | 「prompt 绝不进日志」这类既有断言会变脆 | 可以断言 `llm_prompts.jsonl` 存在而 `llm_calls.log` 里**没有** `prompt` 键 |

**保留的代价**：每条记录要重复 `request_id / round / phase / seat / call_kind / model_id`。用 §2.2 的 `build_prompt_record` 一处构造，重复成本被控制在一个函数里。

### 2.4 JSONL 记录 schema

外层结构与既有日志对齐（`timestamp / round / phase / operation / seat / data`），便于同一套工具读。

```json
{
  "timestamp": "2026-09-30T12:34:56.789012+00:00",
  "round": 2,
  "phase": "speech",
  "operation": "llm_prompt",
  "seat": 5,
  "data": {
    "schema_version": 1,
    "call_kind": "speech",
    "request_id": "speech:2:day_speech:5",
    "contract_id": "day_speech",
    "transport": "speech",
    "attempt": 1,
    "model_id": "deepseek-v4-pro",
    "request_digest": null,
    "prompt_chars": 10032,
    "stored_chars": 10032,
    "max_chars": 0,
    "truncated": false,
    "prompt_digest_scope": "full",
    "prompt_sha256": "9f2c…（64 hex）",
    "messages": [
      {"role": "system", "content": "……"},
      {"role": "user",   "content": "……"}
    ]
  }
}
```

字段语义（下游必须按此实现，否则会误用）：

| 字段 | 语义 |
| --- | --- |
| `request_id` | **join key**。`night:*` 时尾段即 `request_digest[:16]`；其余链路直接==`model_requests.request_id`（`speech:{r}:{ctx}:{seat}` / `action:{idempotency_key}` / `pipeline:{action_key}`）。 |
| `call_kind` | `night` / `speech` / `action` / `pipeline`，与 `llm_calls.log` 的 `data.call_kind` 同名，便于跨文件对照。 |
| `request_digest` | 仅夜间链路非 null：`ModelInvocationService.request_digest(frozen_request)`（**覆盖 messages**，是真指纹）。其余链路为 `null`——写一个不覆盖 prompt 的 digest 会误导下游，**宁可留空**。 |
| `prompt_sha256` | sha256(规范化 messages + `max_chars`)。**只有 `truncated == false` 时才写**；截断记录该键缺失。 |
| `prompt_digest_scope` | `"full"`（完整）或 `"stored"`（仅对已截断文本可校验）。与上一行二选一，保证「有 digest ⇒ digest 覆盖的就是全量原文」。 |
| `messages` | **唯一真相**：模型实际收到的逐条消息（含 system）。夜间原样是 `night_flow` 的 list；其余链路是 `[{system},{user}]`。 |
| `prompt_chars` / `stored_chars` | 全量字符数 / 落盘字符数。等值即未截断。 |

**不存派生字段**（如 `prompt_text`）：训练侧按 `"\n\n".join(m["content"] for m in messages)` 自行拼。理由是留两份文本会漂移；而且**注意**：day/night 用的是**两个不同的 system prompt**（`DAY_SYSTEM_PROMPT` vs `NIGHT_SYSTEM_PROMPT`，`game_service.py:30`），下游不能假设 system 固定。

**截断语义（防代理对撕裂）**：`max_chars` 按 **Python 字符**计数并做**字符串切片**（`content[:max_chars]`），因为 Python `str` 长度≈用户感知长度且切片不会切开 UTF-16 代理对——按字节截断才会产出非法序列。`max_chars=0` 表示不截断。

### 2.5 与 completion 配对

```sql
SELECT p.data.messages, r.normalized_result_json
FROM games/g p JOIN model_requests r
  ON r.game_id = <game_id> AND r.request_id = p.data.request_id
WHERE r.status IN ('consumed','resolved')
  AND r.normalized_result_json IS NOT NULL
  AND p.data.truncated = 0
```

`truncated = 0` 与 `request_digest`/`prompt_sha256` 存在性一起，构成三道质量闸门。

---

## 3. 开关与配置

### 3.1 放哪

`backend/app/config.py` 新增读取函数（**不放 `GameConfig`**，理由见 §2.2：`_CONFIG_FIELDS` 精确匹配，加字段会让旧检查点无法续跑；也不进 `LLMClientConfig`，那会改 `parameters_digest`）。

```python
_TRUTHY = ("1", "true", "yes", "on")

def capture_enabled(...) -> bool:
    raw = os.getenv("PROMPT_CAPTURE_ENABLED", <sentinel>)
    # 未设置 -> 默认值；设置 -> 小写后落 _TRUTHY 才算开，其余一律关（失败闭合）

def capture_max_chars(...) -> int:
    raw = os.getenv("PROMPT_CAPTURE_MAX_CHARS", "0")
    # 非数字 / <0 -> 0（0 = 不截断）
```

签名留一个 `value: str | None = <sentinel>` 参数，`None` 表示「显式关闭」、未传表示「读 env」——这样每个分支都能在单测里打到，不用 `monkeypatch` 依赖真实进程环境（也契合 `pipeline_mode_from_env(value=None)` 的既有写法）。

### 3.2 默认值

| 变量 | 默认 | 理由 |
| --- | --- | --- |
| `PROMPT_CAPTURE_ENABLED` | **`true`（默认开）** | 本项目的**主目标**就是攒微调语料。用户明说「不要干扰正常运行」，而本设计已经做到零行为影响，默认关会让「忘了开」直接导致数据永久丢失（无法回溯）。 |
| `PROMPT_CAPTURE_MAX_CHARS` | **`0`（不截断）** | 训练要完整 prompt。逃生阀留给磁盘紧张的场景。 |

**关闭方式（三种，都不需要改代码）**：`PROMPT_CAPTURE_ENABLED=false` 写进 `backend/.env`（`.env` 已被 `backend/.gitignore` 忽略，不要提交）/ 进程环境变量 / `PROMPT_CAPTURE_MAX_CHARS=2000` 降级为限量采集。

### 3.3 为什么不做「按对局开」

按对局的开关需要走 API → `GameConfig` → checkpoint codec，**这条路正好踩在断点续跑的精确字段匹配上**。落盘开关（`data_dir` 侧）与对局状态（checkpoint 侧）分离，是这个项目已有的正确姿势（参考 `ModelConfig.headers`：敏感面从活配置读，不进冻结结构）。若将来确实要按对局控制，正确做法是**另建一张 `game_capture_flags` 表**，而不是塞进 `GameConfig`。

---

## 4. 失败与性能处理

### 4.1 best-effort 的三层防线

```python
# 层 1：调用点永不因采集失败而变道
try:
    capture.flush(record)
except Exception:                       # noqa: BLE001 - 采集绝不冒泡
    logger.exception("prompt capture failed for game=%s", game_id)

# 层 2：flush 内部自降级（诊断一次，然后彻底闭嘴）
class PromptCapture:
    def flush(self, record: dict) -> None:
        if self._disabled:
            return
        try:
            ...
        except OSError as error:
            self._disabled = True          # 磁盘满/权限/句柄失效 -> 本局不再尝试
            logger.warning(
                "prompt capture disabled for game=%s after %s: %s",
                self._game_id, type(error).__name__, error,
            )

# 层 3：PromptSink.record 是纯内存 list.append，永不触盘、永不抛
```

**关键顺序约束**：`flush` **只在 provider_call 返回之后**调用。即使写盘炸了，也炸在「已经拿到模型答案、已经落库 `model_requests`」之后，**对局状态推进完全无感**。

### 4.2 `_write_line` 能不能复用？——不能，且有实测依据

`game_logger._write_line`（`game_logger.py:29-33`）：

```
dir_path = self._ensure_dir(game_id)   # 每次 os.makedirs
with open(filepath, "a", encoding="utf-8") as f:   # open + close 每条
    f.write(json.dumps(record, ensure_ascii=False) + "\n")
```

- **同步**：没有 `to_thread`、没有队列，调用方在哪个线程就在哪个线程写。
- **无缓冲**：文本模式默认缓冲 8 KB，但 `close()` 每条都强制 flush，等于没有缓冲。
- **无锁**：多个写者靠 O_APPEND 的原子性，但 16.6 KB 的 payload 会被拆成多次 `write()`。

**实测（本机，WSL2 挂载 `/mnt/e`，同目录真实路径，60 次均值，16.6 KB payload）**：

| 写法 | 耗时/条 |
| --- | --- |
| A. `_write_line` 现写法（open/write/close） | **10.460 ms** |
| B. 持久 fd + `os.write` | **0.861 ms** |
| C. 持久文本句柄 + 一次 flush | **0.975 ms** |
| 纯 `json.dumps` | 0.022 ms |

（`/tmp` ext4 上 A 只要 0.035 ms——**12 倍差异全部来自 open/close 跨 9p/drvfs 的元数据开销**。所以「在本机 WSL 上开发」这件事本身就是必须按最坏情况设计的理由。）

**结论与建议**：

1. **不要复用 `_write_line`**。一局 ~100 次 LLM 调用 × 10.46 ms ≈ **1 秒纯写盘阻塞**；投票阶段 `asyncio.gather` 并发 5 个座位同时 flush，事件循环会被卡住约 50 ms（而且在同一个桶里串行化）。用**持久 fd 方案 B**，同样场景降到 ~86 ms，且单次 <1 ms。
2. **不做异步写 / 不做后台线程**。理由：(a) 1 ms 相对 5~90 s 的 LLM 延迟是噪声；(b) 起后台线程/`atexit` flush 会引入**新的线程与关闭时序**，与项目已记录过的「未关闭 httpx 客户端在 Windows 上段错误」同类风险；(c) 写发生在响应之后，天然不占关键路径。
3. **不做批量缓冲**。批量要把「进程被杀时丢掉最后 N 条」纳入语义，还会引入 flush-on-exit 钩子（`GameService._close_game_clients` / `engine.stop()` / 异常路径全部要挂）。对训练语料，「一条都不丢」比「写得快」重要。代价是每局多一个常驻 fd——项目本来每次写就在开关文件，这不算新增一类资源。
4. **若将来实测仍有压力**，第一优先是 `PROMPT_CAPTURE_MAX_CHARS` 降级，第二优先才是批量写；**不要把写操作挪到事件循环之外的新线程**。

### 4.3 密钥与敏感信息

- prompt 由 `PromptRenderer` / `PromptBuilder` 从 `RoleSpec` + `ContextProjector` 投影 + 对话历史渲染，**结构上不含 `api_key` / `headers`**（它们只进 `LLMClientConfig` → transport `default_headers`，见 CLAUDE.md 既有明令）。
- 本方案**不新增任何采集 `LLMClientConfig` 字段的代码路径**——这是最关键的一条：只要不去读 `config.api_key` / `config.headers`，就不可能泄漏。
- 加一条**主动扫描测试**（§5.4）兜底：用真实 `GameService` + 假 provider 跑一段对局，把 `api_key` 与 `headers` 的值设成哨兵字符串，断言它们**不出现在 `llm_prompts.jsonl` 的任何字节里**。这条测试同时保护 `game.log` / `llm_calls.log`，是回归网而不是形式主义。
- 仓库侧还有 `.githooks/secret-scan.sh` 兜底，且 `backend/.gitignore` 的 `data/*` 保证采集文件不入库。**不要提交 `backend/.env`。**

---

## 5. 测试方案

### 5.1 测试文件落位

按既有布局（同目录 `test/` 子目录模式）：

| 文件 | 内容 |
| --- | --- |
| `app/core/test/test_prompt_capture.py`（新） | `PromptCapture` / `PromptSink` / `build_prompt_record` 单测 + 配置解析单测（**含全部错误分支**） |
| `app/test/test_config.py`（改） | 若配置辅助函数放 `config.py`，补它的分支 |
| `roles/test/test_base.py`（改） | `capture=` 传入 / 不传 / sink 抛异常 三态 |
| `tests/test_game_service_prompt_capture.py`（新） | 四链路端到端采集 + `request_digest` 验证断言 + 密钥扫描 + `reused` 不重采 |
| `tests/test_llm_client_*.py` | **不改**（本方案不碰 `llm_client`，这正是路线 C 的收益） |

### 5.2 100% statement / branch 覆盖的达成路径

新增代码的每个分支都必须有对应 case。逐条列（这也是实现时的 checklist）：

`resolve_capture_enabled`
- [ ] 未传 → 读 env；env 未设 → `True`
- [ ] 传 `""` / `"false"` / `"0"` / `"off"` → `False`
- [ ] 传 `"true"` / `"TRUE"` / `"1"` / `"yes"` / `"on"` → `True`
- [ ] 传非字符串（`int`）→ 抛 `TypeError`（对齐 `pipeline_mode_from_env` 的既有风格）

`resolve_capture_max_chars`
- [ ] 未设 → `0`
- [ ] `"2048"` → `2048`
- [ ] `"abc"` → `0`；`"-1"` → `0`；`"0"` → `0`

`PromptSink`
- [ ] `record` 后 `messages` 内容与顺序正确
- [ ] 无 record → `messages == ()`、`complete is True`
- [ ] 单条被截断 → `complete is False`
- [ ] 多条（含重试变体）→ 全保留，`complete` 取与

`build_prompt_record`（纯函数，最容易打满）
- [ ] `request_digest` 为 `None` / 非 `None` 两态
- [ ] `max_chars == 0`（不截断）→ `truncated=False`、有 `prompt_sha256`、`prompt_digest_scope="full"`
- [ ] `max_chars > 0` 且总长超限 → 截断、`truncated=True`、**无 `prompt_sha256`**、`prompt_digest_scope="stored"`、`stored_chars < prompt_chars`
- [ ] `max_chars > 0` 但未超限 → 与不截断等价
- [ ] 单条消息本身超限（截到只剩余量）
- [ ] `now` 注入 → `timestamp` 可断言
- [ ] 空 `messages` → 仍产出合法记录（或明确不产出，二者选一并在测试里钉死）

`PromptCapture`
- [ ] 正常 flush → 一行合法 JSON、UTF-8、`ensure_ascii=False`
- [ ] 连续 flush 追加不覆盖
- [ ] **首次 `os.open` 抛 `OSError`**（用 `monkeypatch` 打 `os.open`）→ 自降级、只 warning 一次、第二次调用**不再尝试 open**
- [ ] **`os.write` 中途抛 `OSError`** → 同上
- [ ] `close()` 正常 / 二次 `close()` 幂等 / `close()` 抛异常被吞
- [ ] 并发：`ThreadPoolExecutor` 8 线程 × 50 条 → 行数正确且**每行可 `json.loads`**（防交错回归）
- [ ] `record_enabled=False` 的测试态不写盘

`GameEngine`
- [ ] `PROMPT_CAPTURE_ENABLED=false` → `engine.prompt_capture is None`，且**不创建任何文件**

`_instrument_roles` / `durable_provider` / `_night_invoke`
- [ ] `capture is None` 分支（现有测试已覆盖大部分，补齐）
- [ ] `reused == True`（恢复回放）→ **不 flush**，文件不新增行
- [ ] `reused == False` → flush 恰好一次
- [ ] `capture.flush` 抛异常 → 对局继续（用 mock capture 验证异常不外泄）

`BaseRole`
- [ ] `capture=None`（默认）→ 行为与原实现完全一致（现有 6 个调用点不动即为证）
- [ ] `capture` 有值 → `record` 被调，参数 = system/user 原文
- [ ] `capture.record` 抛异常 → **不冒泡**（`speak` 仍返回正常文本）

预计新增测试 55~70 个 case，`pytest tests app --cov=app --cov-branch -q` 应保持 100%。**若出现物理不可达的防御分支**（如 `os.close` 在 `_disabled` 状态下被跳过），用 `# pragma: no cover` 并在提交信息里写明理由——仓内已有先例（`model_invocation_service.py:73`）。

> ⚠️ 环境提醒：我在当前 WSL 会话里跑 `pytest` 失败于 `ModuleNotFoundError: No module named 'langchain_core'`，**本机 WSL 侧没有装后端依赖**。实现者必须在**装好依赖的环境**（Windows 侧 venv 或在 WSL 里 `pip install -r requirements.txt`）跑门禁，不能把「跑不起来」当成通过。

### 5.3 用 `request_digest` 写忠实性断言（核心资产）

`ModelInvocationService.request_digest`（`model_invocation_service.py:48`）= `sha256(canonical_json(frozen_request))`，夜间链路的 `frozen_request` **包含 `messages`**，所以它就是 prompt 指纹；而 `request_id` 尾段就是它的前 16 位（实测 5070/5070 吻合）。

**A. 夜间链路：最强的等价性断言（在真实对局里跑）**

```python
def test_night_prompt_is_the_hashed_request(game_service, tmp_path):
    # 跑一段真实夜间流程（假 provider），然后：
    for prompt in read_jsonl(tmp_path / "games" / game_id / "llm_prompts.jsonl"):
        if not prompt["data"]["request_id"].startswith("night:"):
            continue
        d = prompt["data"]
        frozen = {"kind": "night_json", "round_number": prompt["round"],
                  "tool_name": d["contract_id"], "seat": prompt["seat"],
                  "messages": d["messages"], "schema": SCHEMA}
        digest = ModelInvocationService.request_digest(frozen)
        # 1) 记录里的 digest 就是重算值
        assert d["request_digest"] == digest
        # 2) 重算值 == 库里存的那一个
        assert repo.get_model_request(game_id, d["request_id"])["request_digest"] == digest
        # 3) request_id 尾段 == digest 前 16 位
        assert d["request_id"] == f"night:{r}:{d['contract_id']}:{seat}:{digest[:16]}"
        # 4) 结构完整
        assert [m["role"] for m in d["messages"]] == ["system", "user"]
```

含义：**存放在 `llm_prompts.jsonl` 的 messages 重新哈希后必须等于数据库里那条请求的 digest。** 这是「采集器没有漏字段、没有改写、没有截断、没有错序」的机器可验证证明——一条断言把「忠实性」钉死。

**B. 全链路：`request_id` 闭合性**

```python
def test_every_durable_request_got_exactly_one_prompt(game_service, repo, tmp_path):
    rows = repo.list_model_requests(game_id)            # status in consumed/resolved
    prompts = {p["data"]["request_id"] for p in read_jsonl(...)}
    assert prompts == set(rows) — {failed/prepared/...}
    assert len(prompts) == len(list_of_records)          # 无重复
```

**C. 恢复场景：不重采（`reused` 语义）**

```python
def test_resume_does_not_duplicate_prompt_records(...):
    before = read_jsonl(...)
    await service.recover_game(game_id)                 # 走 resumed 分支
    assert read_jsonl(...) == before                    # 行数、内容都不变
```

这一条同时是**「不破坏断点续跑」的回归网**：如果实现里不小心在 `reused` 分支也 flush，或把采集数据写进了 checkpoint，这条会红。

**D. 截断时的断言改写**

`truncated=True` 的记录**不再**满足 A（digest 覆盖的是被截断的文本），所以要做成显式契约：

```python
if d["truncated"]:
    assert d["request_digest"] is None or <仅对已存文本可校验>
    assert "prompt_sha256" not in d
else:
    assert d["prompt_sha256"] == sha256(canonical(d["messages"]))
```

**E. 不做的事**：删掉所有 `request_digest` 的历史值都要重算的「事后对齐」逻辑。旧数据本来就没 prompt，重算无意义。

### 5.4 密钥扫描测试

```python
def test_prompt_capture_never_writes_credentials(...):
    sentinel_key, sentinel_header = "sk-SENTINEL-KEY-XYZ", "Bearer SENTINEL-HEADER-XYZ"
    # 用带 sentinel 的 ModelConfig 建局跑几步
    for name in ("llm_prompts.jsonl", "game.log", "llm_calls.log", "model_errors.log"):
        blob = (tmp_path / "games" / game_id / name).read_text(encoding="utf-8")
        assert sentinel_key not in blob
        assert sentinel_header not in blob
```

---

## 6. 风险清单

| # | 风险 | 严重度 | 触发条件 | 缓解措施 |
| --- | --- | --- | --- | --- |
| R1 | **采集数据进 checkpoint → 旧局无法续跑** | 🔴 P0 | 把 capture 句柄/sink/开关加进 `GameState`、编排状态、或 `GameConfig` | 硬红线：采集的任何值都不进 codec。`_CONFIG_FIELDS` / `_STATE_FIELDS` / `PointKey` 全部不动。**加一个测试**：`test_checkpoint_codec` 全量回归 + 用旧格式 checkpoint 反序列化必须成功。实现时**先跑一遍现有 codec 测试做基线**。 |
| R2 | **改 `frozen_request` → `request_digest`/`request_id` 变化 → 续跑重放 LLM** | 🔴 P0 | 采用路线 A，或在 `frozen_request` 里加任何采集字段 | 采用路线 C：prompt 走独立 `PromptSink`，**不经过** `frozen_request`。加断言：采集开启前后 `request_digest` 逐字节不变（同一局跑两次对比）。 |
| R3 | **泄漏 `api_key` / `headers`** | 🔴 P0 | 有人「顺手」把 `LLMClientConfig` 存进 prompt 记录，或把 transport 的 `default_headers` 打进记录 | 采集代码**只接收 messages 与元数据**，签名里没有 config；加 §5.4 哨兵扫描测试；`.env` 不入库。 |
| R4 | **改 `parameters_digest` → 旧局恢复失败** | 🔴 P0 | 把开关做成 `LLMClientConfig` 的字段（那样它会进 `_MODEL_PARAMETER_FIELDS` 的邻居/被冻结） | 开关只读 env（§3.1）。`_MODEL_PARAMETER_FIELDS` 一个字都不改。 |
| R5 | **写盘失败影响对局** | 🟠 P1 | 磁盘满 / 权限 / 句柄失效 | 三层防线（§4.1）：调用点 try/except + flush 内部自降级 + sink 纯内存。**flush 必须在响应之后**。测试覆盖 `os.open`/`os.write` 抛错。 |
| R6 | **JSONL 行交错损坏** | 🟠 P1 | 投票/夜间并发写入，>8 KB 绕过缓冲被拆成多次 `write()` | 每局一个 `threading.Lock`，锁内只 `os.write`；加 8 线程 × 50 条的并发测试。 |
| R7 | **事件循环阻塞** | 🟡 P2 | 复用 `_write_line`（实测 10.46 ms/条） | 用持久 fd（0.861 ms/条，12× 改善）。已量化，见 §4.2。 |
| R8 | **重渲染 prompt → 多消费 RNG → 改变对局** | 🟠 P1 | 为省事在 wrapper 里重新调 `build_*_prompt` / `renderer.render`（`RANDOM_HINT` 消费 `engine._rng`，`game_service.py:2355`） | **只搬运已渲染的文本，绝不重渲染**。加断言：采集开/关两次跑同一局，`engine._rng` 状态与最终事件序列逐字节一致。 |
| R9 | **磁盘无限增长** | 🟡 P2 | 长期运行，~10 KB/条 × 100 条/局 | 提供 `PROMPT_CAPTURE_MAX_CHARS`；文件独立可单独删除/归档；在 `docs/architecture.md` 写明体量。**不要加自动清理**（会删掉训练语料）。 |
| R10 | **恢复时重复采集 / 采集噪声** | 🟡 P2 | 在 `reused == True` 分支也 flush；或把 transport 层内部重试也记进来 | 只在 `reused == False` 时 flush（§2.2）；`request_id` 唯一性由 §5.3-B 断言。 |
| R11 | **`contract_action` 路径的 `retry_messages` 未定义**（既有隐患） | 🟢 P3 | `base.py:447-453` 的 `except _ActionTransportTimeout` 分支里 `retry_messages` 可能未被赋值 | **本方案不修它**（不在范围内），只在旁边插 `record` 调用，避免夹带行为变更。单独开 issue。 |
| R12 | **`llm_calls.log` 与 `llm_prompts.jsonl` 不同步**（记录数不一致） | 🟢 P3 | 夜间有 63 条请求没有对应 `llm_call` 行（`_log_night_llm_telemetry` 需 `engine_ref is not None`） | 把两者放在**同一个代码点**产生（§2.2 第 3 点），把差异压到既有范围内；在 schema 里用 `request_id` 做 join，不假设行数相等。 |
| R13 | **前端 / 公开 DTO 泄漏私有 prompt** | 🟠 P1 | 有人把 `llm_prompts.jsonl` 加进 summary 或 API 响应 | 本方案**不新增任何 API / DTO / summary 字段**。加断言：`GET /api/games/{id}` 与观众快照的响应体里不含 prompt 文本。 |
| R14 | **100% 分支覆盖拖垮工期** | 🟡 P2 | 新增 ~100 行代码要 55~70 个 case | 把 `build_prompt_record` 设计成**纯函数**（无 IO 分支最容易打满）；IO 分支用 `monkeypatch` 打 `os.open`/`os.write`；真正不可达的用 `# pragma: no cover` + 提交信息说明。 |

---

## 7. 实施步骤拆解（每 commit ≤3 文件）

依赖顺序：**C1 → C2/C3 可并行 → C4 → C5 → C6**。每个 commit 前都必须跑
`cd backend && python -m pytest tests app --cov=app --cov-branch -q` 且保持 100%。

| Commit | 类型 / scope | 文件（≤3） | 内容 | 依赖 |
| --- | --- | --- | --- | --- |
| **C1** | `feat(core): add best-effort prompt capture sink` | `app/core/prompt_capture.py`（新，~110 行）<br>`app/core/test/test_prompt_capture.py`（新） | `PromptSink` / `build_prompt_record` / `PromptCapture`（持久 fd + 锁 + 自降级 + `close`）。**纯新增，不接线**，对局行为零变化。 | — |
| **C2** | `feat(config): gate prompt capture behind env flags` | `app/config.py`<br>`app/core/prompt_capture.py`（接上解析）<br>`app/test/test_config.py` | `capture_enabled` / `capture_max_chars` + 全错误分支测试。 | C1 |
| **C3** | `feat(roles): let speak and request_action expose their prompt` | `app/roles/base.py`<br>`app/roles/test/test_base.py` | keyword-only `capture=None` 参数 + `record`（含重试变体）。默认 `None` ⇒ 现有调用点/测试零改动。 | —（可与 C1 并行） |
| **C4** | `feat(engine): own one prompt capture per game` | `app/core/game_engine.py`<br>`app/core/test/test_game_engine.py` | `self.prompt_capture`（env 关则 `None`）+ `speak()` 透传 `capture=`。 | C1, C2, C3 |
| **C5** | `feat(service): record prompts for all four model chains` | `app/services/game_service.py`<br>`tests/test_game_service_prompt_capture.py`（新） | `_instrument_roles`（speech/action）、`_command_provider` 的 `provider`/`durable_provider`、`_night_invoke` 与 `night_invoke` 两处对称 flush；只在 `reused == False` 落盘。**含 §5.3 的 `request_digest` 等价断言 + 密钥扫描 + 重放不重采。** | C4 |
| **C6** | `docs: document prompt capture for finetuning` | `AGENTS.md`<br>`CLAUDE.md`<br>`docs/architecture.md` | 三处同步：新文件、开关、以及「不进 checkpoint / model_snapshot / `_MODEL_PARAMETER_FIELDS` / 公开 DTO」的红线。 | C5 |

**业务代码行数核算**：`prompt_capture.py` ~110 + `config.py` ~8 + `base.py` ~18 + `game_engine.py` ~6 + `game_service.py` ~45 ≈ **187 行**，远低于 400 行/PR 上限。

**C5 是唯一的高风险 commit**（同时碰三处生产路径），建议它单独走一次代码审查，并额外跑一遍真实短局做冒烟（`PROMPT_CAPTURE_ENABLED=true` 跑 2 轮，人工核对 `llm_prompts.jsonl` 内容与 `llm_calls.log` 能对上）。

---

## 8. 待你确认的三个决策点

1. **默认开还是默认关？** 我按「项目主目标是攒语料」推荐**默认开**。若你更保守（怕意外写满盘），把默认改成 `false` 即可，其余设计不变——但这会导致「未配置的机器永远不采集」。
2. **是否需要按对局关闭？** 我推荐**不做**（理由见 §3.3）。若确实需要，第二 commit 要改成新建 `game_capture_flags` 表的方案，工期 +1 commit。
3. **`max_chars` 是否需要「按 token 估算」的上限？** 我推荐只做**字符上限**（`0` = 不限）。token 估算要引依赖，且训练侧自己会做过滤。

---

# 附录 A（2026-09-30 增补）：采集开关的 UI 与应用级设置落点

> 起因：用户拍板 —— ① 默认开 ② 可手动关 ③ **提供前端按钮（UI 开关）** ④ **做好备注标识**。
> 原方案 §3 把开关做成**只有环境变量**。本附录只解决「开关怎么落成一个可持久化的应用级设置，并暴露成一个 UI 开关」，**不覆盖原文任何结论**。
> 状态：方案（不含实现）。附录内所有代码事实均基于本工作区实测（行号可能随 `main` 漂移，以符号名为准）。

## A.0 本附录对原方案的三处修正（其余原样）

| 位置 | 原文 | 修正后 | 理由 |
| --- | --- | --- | --- |
| §3.1 / C2 | `capture_enabled(value=<sentinel>)` 只读 env | `resolve_capture_setting(stored=None, value=<sentinel>) -> PromptCaptureSetting`，**落点仍是 `app/config.py`** | 开关多了一个输入（界面设置），两个输入必须在**同一个纯函数**里裁决，否则路由与引擎会各算一套。只多一个入参 + 一个 `source` 回显，分层不变 |
| C4 | `GameEngine` 自己读 env | `GameEngine.__init__(..., prompt_capture_enabled: bool = False)`，由 `GameService` 传入 | 引擎要读设置就得新增 `core → stores` 依赖边；改成传参后 `core` 一行新 import 都不加，且**引擎既有测试全部默认关闭采集**，零扰动 |
| §6 R13 | 「本方案**不新增任何 API / DTO / summary 字段**」 | 「不新增任何**携带 prompt 原文**的 API / DTO / summary 字段」，允许新增**一个只回开关状态**的端点 | 原 R13 的实质是「prompt 原文不许出圈」，而用户新需求必然要一个读/写开关的端点，冲突是表面的。见 §A.6 |

**红线不动**（逐条确认过）：`GameConfig._CONFIG_FIELDS`（`checkpoint_codec.py` 的 `_exact` 精确匹配）、编排状态字段集、`PointKey`、`last_words_pending:*` 类 label、`_MODEL_PARAMETER_FIELDS`、`model_snapshot` —— 开关的任何值都不进这些结构。

## A.1 六个设计问题的一页答案

| # | 问题 | 结论 |
| --- | --- | --- |
| 1 | 扩 `model_config_store` 还是新建？ | **新建** `app/stores/app_settings_store.py` + `data/settings.json`（§A.2） |
| 2 | env 与 UI 谁赢？ | **UI > env > 内建默认（开）**；三个来源都回显（§A.3） |
| 3 | 生效时机？ | **下一局生效**（对局级闩锁，靠 `llm_prompts.jsonl` 自身做标记）。**不会出现半局采集**（§A.4） |
| 4 | 备注文案？ | §A.5 逐字稿，含「写哪 / 占多大 / 关掉无影响 / 生效时机」 |
| 5 | 与隐私扫描冲突？ | **不冲突**。现有扫描是「指定 DTO 的字段名集合断言」+ `extra="forbid"`，**没有**全仓/全路由扫描。新端点照旧用 `extra="forbid", strict=True` 加固（§A.6） |
| 6 | 测试方案？ | 后端新增 ~6 个测试文件、~55 case，逐分支清单见 §A.8；前端**写测试但不进覆盖率白名单**（§A.8.3） |

## A.2 落点：新建 `app_settings_store`，不要扩 `model_config_store`

### A.2.1 读到的代码事实（实测）

`app/stores/model_config_store.py`（146 行）是**一个实体表**，不是 KV 设置表：`ModelConfig` 是 11 字段 dataclass，Protocol 是 `list_all / get / upsert / delete`；`data/models.json` 形状 `{"version": 1, "configs": [...]}`，`_read` 对 `OSError` / `JSONDecodeError` / 根非 dict **三种情况都回落空表**，`_write` 走 tmp + `os.replace` 原子替换 + `RLock` 串行。`get_model_config_store()` 是**惰性单例**（默认 `$WOLFKILLER_DATA_DIR/models.json`，`$MODEL_CONFIG_PATH` 可覆盖）；`model_routes.py`（231 行）的 `GET /api/models` 直接返回 `store.list_all()`。

### A.2.2 为什么不能扩（四条，第 2 条是硬伤）

1. **Protocol 会变，所有实现者与桩都要改。** 加 `get_setting/set_setting` 意味着 `ModelConfigStore` 的每个假实现（测试里大量 `MagicMock` / 手写桩）都得跟着长，与原方案 §5.1「不改既有调用点」的价值正好相反。
2. **`list_all()` 是建局路径的输入。** 设置项若以「一条 `ModelConfig`」的形式混进去，它会**出现在 `GET /api/models` 与模型管理页里，成为创建对局时可选的模型**。这不是语义污染，是功能 bug；要绕开就得给 `list_all` 加过滤参数 → 又回到第 1 条。
3. **`data/models.json` 是凭据面。** 它存 `api_key_encrypted`；把非凭据数据塞进同一文件，会让「这个文件要不要脱敏/备份/清理」的答案变模糊（项目已在刻意做这类隔离：`ModelConfig.headers` 不进 model_snapshot / game.log）。
4. **schema 形状不同。** 设置是「少数具名标量」，模型是「实体列表」；共用 `{"version", "configs"}` 必须再加一层 `settings`，等于让 models.json 的读者理解两种形状。

### A.2.3 新建物（照抄现有先例的形状）

`backend/app/stores/app_settings_store.py`（新，~85 行）：

```python
SETTINGS_FILENAME = "settings.json"          # 与 models.json 并列于 WOLFKILLER_DATA_DIR

class AppSettingsStore(Protocol):
    def get_prompt_capture_enabled(self) -> bool | None: ...   # None = 未做界面设置
    def set_prompt_capture_enabled(self, enabled: bool | None) -> None: ...  # None = 清除

class JsonAppSettingsStore:
    VERSION = 1
    def __init__(self, path: str = "data/settings.json"): ...
    # _read(): OSError / JSONDecodeError / 根非 dict / settings 非 dict —— 四种都回落 {}
    # _write(): mkdir(parents=True) + tmp + os.replace（原子，与 model store 同款）
    # get(): 键缺失 -> None；值非 bool -> None（防御，不抛）；set(None): 删键

def get_app_settings_store() -> AppSettingsStore:
    # 惰性单例；默认 $WOLFKILLER_DATA_DIR/settings.json；$APP_SETTINGS_PATH 覆盖
```

- 落盘形状：`{"version": 1, "settings": {"prompt_capture_enabled": true}}`。**键缺失 = 未做界面设置**（回落 env）—— 这一条是 §A.3 的全部基础。
- **不做通用 KV**：不做 `get(key)/set(key, value)`。理由与「公开 DTO 用 `extra="forbid"` 闭集」同源 —— 通用 KV 会变成堆放处，且新增设置项时没人会审查它的优先级与生效时机。**新增一个设置 = 一对类型化方法 + 一个 DTO 字段 + 一次显式评审**，这是有意的摩擦。
- `backend/.gitignore` 已有 `data/*`（已核实），所以 `data/settings.json` 与采集文件都不入库。单例测试照 `tests/test_model_config_store.py`：`monkeypatch.setattr(mod, "_store", None)` + `monkeypatch.setenv("APP_SETTINGS_PATH", ...)`。

## A.3 优先级：UI > env > 默认（开），并且**回显来源**

### A.3.1 裁决链（`app/config.py`，纯函数）

```python
_PROMPT_CAPTURE_ENV = "PROMPT_CAPTURE_ENABLED"
_TRUTHY = ("1", "true", "yes", "on")
_UNSET: object = object()          # 哨兵：区分「没传」与「显式传 None」
SOURCE_DEFAULT, SOURCE_ENV, SOURCE_UI = "default", "env", "ui"

@dataclass(frozen=True)
class PromptCaptureSetting:
    enabled: bool
    source: str

def resolve_capture_setting(
    stored: bool | None = None, value: object = _UNSET,
) -> PromptCaptureSetting:
    if stored is not None:                         # 界面显式设置过 -> 只认它
        return PromptCaptureSetting(stored, SOURCE_UI)
    if value is _UNSET:
        value = os.getenv(_PROMPT_CAPTURE_ENV)     # 调用时读，不是导入时读
    if value is None:                              # 环境变量也没设 -> 内建默认「开」
        return PromptCaptureSetting(True, SOURCE_DEFAULT)
    if type(value) is not str:
        raise TypeError("prompt capture flag must be a string")
    return PromptCaptureSetting(value.strip().lower() in _TRUTHY, SOURCE_ENV)
```

要点：

- **`stored` 非 `None` 就短路**，env 完全不参与。这就是「UI 设置后覆盖 env」的字面实现。
- **失败闭合**：`PROMPT_CAPTURE_ENABLED=maybe` → `False`。方向正确（宁可少采，不可意外写盘）。原方案 §3.1 已定，沿用。
- **`resolve_capture_setting()` 每次调用都重读 env 与存储**，所以**界面开关不需要重启后端**（下一局建局时现读）；而改 `backend/.env` 需要重启 —— 因为 `load_dotenv()` 在 `app/config.py` 导入时执行一次。这个差异必须在 UI 文案里说清（§A.5 第 3 条）。另：`PROMPT_CAPTURE_MAX_CHARS` 保持 env-only（`resolve_capture_max_chars`，落点不变），**不进 UI** —— 它是逃生阀而不是日常开关，且做得越多分支越多。

### A.3.2 「UI 改了但 env 还在生效」为什么不会发生

这是用户明确点名的困惑。三条机制把它堵死：

1. **短路是绝对的**：只要 `settings.json` 里有这个键，env 一个字节都不参与。不存在「两者同时生效」的中间态。
2. **来源可见**：`GET /api/settings/prompt-capture` 回 `source`，UI 把三态渲染成明确的副标题（§A.5）。用户看到「由环境变量 `PROMPT_CAPTURE_ENABLED` 控制」就知道自己的界面点击还没落定；看到「由本页开关控制」就知道 env 已被屏蔽。
3. **有回退路**：`PATCH {"enabled": null}` 删除该键，明确回到 env/default。UI 在 `source === "ui"` 时显示「恢复为跟随环境变量」按钮。没有这条路，用户就只能去手改 `data/settings.json`。

**一种看起来像故障的正确行为**：`.env` 写 `PROMPT_CAPTURE_ENABLED=false`，界面却是「开」且 `source=ui`。这不是 bug（UI 优先级更高），解法不是改优先级而是文案直说 —— 副标题写「由本页开关控制（已忽略 `PROMPT_CAPTURE_ENABLED`）」，见 §A.5。

## A.4 生效时机：下一局生效（对局级闩锁），不存在「半局采集」

### A.4.1 规则（三个句子，全部可测）

```
effective_enabled = requested_at_start or marker_exists
```

1. `requested_at_start`：**只在新建对局时解析一次**（`resolve_capture_setting(...).enabled`），此后整局不再重读。
   **恢复对局时不读开关**（`GameService` 走 recover 路径时传 `prompt_capture_enabled=False`）。
2. `marker_exists`：`<data_dir>/games/<game_id>/llm_prompts.jsonl` 是否存在。它由 `PromptCapture.__init__` 建持久 fd 时顺带创建（0 字节），是一个**不依赖 checkpoint 的耐久闩锁**。
3. 二者取或：**已开始采集的对局，恢复后继续采到结束；从未开始采集的对局，恢复后依然不采。**

### A.4.2 为什么是「下一局生效」而不是「立即生效」

| 理由 | 说明 |
| --- | --- |
| **原方案最强的验证资产会失效** | §5.3-B 断言的是「本局每一个耐久化的逻辑请求都恰好有一条 prompt 记录」（`prompts == set(rows)`）。这是一个**对局级完整性**不变量。允许局中切换，这个断言从「机器可验证的忠实性证明」退化成「取决于你什么时候点的按钮」 |
| **半局采集** | 局中关闭 → 文件停在半局；局中打开 → 文件从中间开始。后者更糟：它会让 §5.3-B 在**恢复后**看起来「本来就有文件」（marker 是中途才出现的） |
| **恢复路径天然会重读设置** | 引擎在 recover 时重建。若恢复也读开关，就会出现「建局时开着 → 关掉 → 中断 → 恢复」= 前半局有文件、后半局没有。这正是要防的场景。`marker_exists` 把它堵死 |
| **红线约束** | 「对局开始时的开关值」不能进 checkpoint（`_CONFIG_FIELDS` / 编排状态字段集精确匹配）。**除了落盘标记，没有别的耐久信号可用**。用文件自身做标记是唯一不碰红线又精确的方案 |

### A.4.3 三个必须说清的行为（写进 UI 文案）

| 操作 | 对**正在跑 / 已中断**的对局 | 对**新建**的对局 |
| --- | --- | --- |
| 打开开关 | 无效（若开局时是关的，本局不采） | 立即采集 |
| 关闭开关 | **无效** —— 该局仍会写到自己结束 | 不再采集，一个字节都不写 |
| 恢复为跟随 env | 无效 | 按 env 生效 |

「关闭后正在进行的对局仍在写」是**故意的**，必须明写，否则会被当成 bug。

### A.4.4 紧急止血（磁盘满 / 误开在敏感局）

因为 `marker_exists` 是唯一信号，且 Windows 上**打开的文件句柄会阻止删除**（`os.open` 不带 `FILE_SHARE_DELETE`），流程必须是：**暂停 / 中断该局 → 删除 `data/games/<id>/llm_prompts.jsonl` → 恢复该局**。

恢复后 `requested=False` 且 marker 已不存在 → 不再采集。代价是该局只留下前半段语料 —— 这是用户显式选择的止血，不是默认行为。**不要为这个场景在代码里加自动清理**（原方案 R9 已说明：自动清理会删掉训练语料）。

### A.4.5 与断点续跑的关系

marker 是**游戏目录里的一个文件**，不是 checkpoint 字段、不是 `GameConfig`、不是编排状态；采集对象（`PromptCapture` 句柄）依旧是运行期句柄，不进 `engine_checkpoint.py` 的字段集。与 `recovery_compat.assess()` 无关：采集既不产生 digest，也不改变任何已提交状态。**原方案 R1 红线完好。**

## A.5 UI 落点与备注文案（逐字稿）

### A.5.1 落点

**新建 `/settings` 页**（`frontend/src/components/settings/SettingsPage.tsx`）+ AppBar 新增「设置」文字按钮（与既有「模型评测」「模型管理」同款，纯文字、无图标）。不放进 `/models` 页：理由与 §A.2 不扩 store 同源 —— 「应用设置」不是「模型配置」，混在一起会让「这个开关属于哪个模型」的误读无解。也不放进创建对局向导：开关是**全局**的、只在建局时生效，放进向导会被读成「只对这一局生效」。整页只有一个开关是合理的 —— 它承载 §A.5.2 那块必须说清的备注，向导里塞不下。

### A.5.2 备注文案（可直接落进组件）

**卡片标题**：训练语料采集（LLM Prompt 原文）

**开关行**
- 主标签：`采集 LLM Prompt 原文`
- 状态：`开` / `关`
- 副标题（随 `source` 三态）：
  - `ui` → 「由本页开关控制（已忽略环境变量 `PROMPT_CAPTURE_ENABLED`）」
  - `env` → 「由环境变量 `PROMPT_CAPTURE_ENABLED` 控制（后端启动时读取，改动需重启后端）」
  - `default` → 「默认开启（未做任何界面设置，环境变量也未设置）」

**说明区（Markdown 段落，标题「这个开关做什么」）**

> 1. **用途** —— 把每个座位实际发给模型的 prompt 原文（system + user 全文）和它的回复配成 `(prompt, completion)` 训练对，用于以后在本地微调小模型。它**只写文件**，不改变对局的任何逻辑。
> 2. **写到哪** —— `backend/data/games/<对局 ID>/llm_prompts.jsonl`，一局一个文件，和 `game.log`、`llm_calls.log` 放在同一个目录。文件只追加，不覆盖、不自动清理。
> 3. **占多大磁盘** —— 按本机已有 143 个对局实测（统计源：各局 `llm_calls.log` 的 `prompt_chars`）：典型一局约 **2 MB**（prompt 原文中位 136 万字符、约 111 条记录）；多数对局在 **1.4 ~ 2.9 MB**；调用次数多的长局可到 **6 ~ 12 MB**。磁盘紧张时请关闭本开关，或手动清理不再需要的 `data/games/<id>/`。
> 4. **生效时机** —— **只对新建的对局生效。** 已经在跑或已中断的对局按它开局时的设置跑完：开局时开着就采到本局结束，开局时关着就一直不采。这是故意的 —— 一局只采一半的语料是不完整的，训练时不好用。所以**关掉开关后，正在进行的对局仍会继续写**到本局结束。
> 5. **关闭后有什么影响** —— 没有影响。发言、投票、夜间行动、断点续跑、回放全部照旧，只是不再产生新的采集文件（一个字节都不写）。
> 6. **会不会写进密钥** —— 不会。落盘的只有 prompt 正文和少量元数据（轮次、阶段、座位、模型名、请求 ID）；API Key 与自定义请求头从不进入这些文件。
> 7. **想立刻停掉某一局** —— 暂停该局 → 删除该局的 `llm_prompts.jsonl` → 恢复该局（恢复时不读开关，只看这个文件在不在）。注意这样该局只留下前半段语料；对局运行中无法删除该文件（句柄被占用），必须先暂停或中断。

**底部按钮**（仅 `source === "ui"` 时显示）：`恢复为跟随环境变量`

**操作反馈**（`Alert` / Snackbar）
- 打开 → 「已开启：从下一个新建的对局开始采集。」
- 关闭 → 「已关闭：新建的对局不再采集；正在进行的对局会在本局结束后停止。」
- 恢复 → 「已恢复为跟随环境变量 `PROMPT_CAPTURE_ENABLED`（当前：开 / 关）。」

### A.5.3 文案合规自检（逐条对照既有约束）

| 约束 | 核对 |
| --- | --- |
| Material Symbols，**禁止 emoji** | 文案零 emoji；新增图标若有，必须来自 Material Symbols。此处不新增图标 |
| 公开 DTO / 前端消费链不得出现 `role_init` / `visible_to` / `night_intel` / `check_results` / `has_antidote` / `has_poison` / `has_gun` | 逐词核对通过。文案里出现的是「夜间行动」，不是 `night_intel`；「解药/毒药」等词一律未用 |
| 不暴露内部路径 | 只用仓库相对路径 `backend/data/games/<对局 ID>/llm_prompts.jsonl`（CLAUDE.md / `docs/architecture.md` 已公开同一信息）；无绝对路径、无用户名、无主机名 |
| 不泄漏凭据 | 无 Key、无 header 名值、无 relay 信息 |
| 组件禁止硬编码色值 | 只用 MUI 组件（`Switch` / `Alert` / `Typography` / `List`）与 `theme/tokens.ts`；不写 hex |
| 数字必须可追溯；不与实现打架 | §A.5.2 第 3 条自带口径与统计源并标注「实测」而非保证；第 4 条对应 §A.4.1 规则、第 5 条对应 §4.1 三层 best-effort +「flush 在响应之后」、第 6 条对应 §4.3 |

**磁盘数字的推导（可复核）**：`data/games/*/llm_calls.log` 中 `prompt_chars` 逐局求和，得到 143 局的中位 = 1,368,670 字符、p75 = 1,872,504、p90 = 4,209,625、max = 7,943,894。字节换算用 1.6 字节/字符 —— 该系数由真实系统提示词实测（`DAY_SYSTEM_PROMPT` 1995 字符 / 3565 字节 = 1.787；`NIGHT_SYSTEM_PROMPT` 2037 / 3411 = 1.675；渲染出的 user 段 XML/JSON 占比更高，故取偏低的 1.6）。交叉验证：中位 1,368,670 ÷ 111 条 ≈ 12,330 字符/条，与原方案 §0.9 的「中位 10032 字符」同量级。🟡 系数是估算，量级可信。

## A.6 隐私与 DTO 约束：新端点不会被现有扫描命中

### A.6.1 现有「隐私扫描」的真实实现（实测，共 4 类）

| # | 位置 | 机制 | 是否会被新端点影响 |
| --- | --- | --- | --- |
| 1 | `tests/test_game_routes.py:608`、`:657`、`tests/test_schemas.py:442` | `forbidden = {"check_results", "has_antidote", "has_poison", "has_gun"}`，对 **`GameDetailResponse` / `GameLogsResponse`** 的 `model_dump()` 递归收 key（`_all_keys`）后取交集 | **否**。只作用于这两个 DTO，不遍历路由表 |
| 2 | `tests/test_game_routes.py:1574` | `repr(response.model_dump())` 断言 `role_init / werewolf / seer result / private thought / reasoning / roles / tally` 不出现在**游戏日志响应**里 | **否**。只针对 `/api/games/{id}/logs` |
| 3 | `app/api/schemas.py:135` `_PublicResponse` = `ConfigDict(extra="forbid", strict=True)` | 所有公开 DTO 的基类：**多一个字段就 `ValidationError`**。这是真正的强制机制（不是字符串扫描） | **是（正面）** —— 新 DTO 照抄即得同等强度 |
| 4 | `tests/test_main.py:32` | **正向**成员断言（`assert "/api/models" in paths`），**不是**「只允许这些路径」的闭集 | **否**。但按仓库惯例新路径要补一行断言 |

**结论**：不存在全仓 grep、也不存在「遍历 `app.routes` 检查响应体」的测试。新增 `GET/PATCH /api/settings/prompt-capture` 不会被任何现有扫描命中。**而且开关状态本身也确实不是私有字段** —— 它描述的是进程配置，不是任何座位的隐藏信息。

### A.6.2 新端点必须遵守的四条（否则会自己造出问题）

1. **响应体闭集**：`_SettingsResponse(BaseModel)` 带 `ConfigDict(extra="forbid", strict=True)`（与 `_PublicResponse` 同款）。这样「以后有人顺手往响应里加 `game_id` / 文件路径 / prompt 片段」会直接 422，而不是静默泄漏。
2. **响应体只允许 `enabled` + `source`**：不回 `max_chars`、不回路径、不回「当前有多少局在采集」。**测试里对响应 key 集合做精确断言**（`set(payload) == {"enabled", "source"}`）—— 这是本轮新增的、与前 4 类扫描同精神的一道网。
3. **请求体严格布尔，且不回显存储内容**：`Optional[Annotated[bool, Field(strict=True)]]` —— pydantic v2 非严格模式会把 `"yes"` / `1` 强制转成 `True`，等于给了一个绕过 UI 的隐式写入口（先例：`model_schemas.py` 的 `Annotated[int, Field(strict=True, ge=1)]`）。写设置只回写后的状态，不回 `settings.json` 的路径或内容。
4. **顺带补一条哨兵断言**：原方案 §5.4 的扫描（`api_key` / `headers` 的值不得出现）加扫 `data/settings.json` —— 现在多了一个可能被「顺手」写进配置值的文件，成本一行。

## A.7 API 设计（与 `model_routes.py` 同风格）

### A.7.1 端点

| 方法 | 路径 | 请求体 | 响应 | 语义 |
| --- | --- | --- | --- | --- |
| `GET` | `/api/settings/prompt-capture` | — | `{enabled, source}` | 读**生效值**与来源 |
| `PATCH` | `/api/settings/prompt-capture` | `{enabled: true \| false \| null}` | `{enabled, source}` | 写界面设置；`null` 或缺省 = **清除界面设置**，回落 env/默认 |

新建 `app/api/routes/settings_routes.py`（~55 行，`APIRouter(prefix="/api/settings", tags=["settings"])`）+ `app/api/settings_schemas.py`（~25 行，与 `model_schemas.py` 平行）+ `app/main.py` 里 `app.include_router(settings_router)`。

```python
# app/api/settings_schemas.py
PromptCaptureSource = Literal["default", "env", "ui"]

class _SettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class PromptCaptureSettingsResponse(_SettingsResponse):
    enabled: bool
    source: PromptCaptureSource

class PromptCaptureSettingsRequest(BaseModel):
    # None（或缺省）= 清除界面覆盖，重新让环境变量/内建默认生效。
    enabled: Optional[Annotated[bool, Field(strict=True)]] = None
```

```python
# app/api/routes/settings_routes.py
router = APIRouter(prefix="/api/settings", tags=["settings"])

def _current() -> PromptCaptureSettingsResponse:
    setting = resolve_capture_setting(
        get_app_settings_store().get_prompt_capture_enabled(),
    )
    return PromptCaptureSettingsResponse(enabled=setting.enabled, source=setting.source)

@router.get("/prompt-capture", response_model=PromptCaptureSettingsResponse)
async def get_prompt_capture():
    return _current()

@router.patch("/prompt-capture", response_model=PromptCaptureSettingsResponse)
async def update_prompt_capture(req: PromptCaptureSettingsRequest):
    get_app_settings_store().set_prompt_capture_enabled(req.enabled)
    return _current()
```

### A.7.2 设计取舍

- **用 `PATCH` 而不是 `PUT` + `DELETE`**：`ModelTestRequest` 已确立「`None` = 未设置」的语义（`strict_base_url=None` 沿用存储值，空串表示清空）。三态用一个动词表达，少一个路由函数与一组测试；`PATCH` 在本仓库有先例（`PATCH /api/folders/{id}`）。
- **不复用 `GET /api/config`**：它是 LLM/游戏配置的聚合**只读**端点（`llm_models` / `debug` / `active_games`），没有对应写端点。塞进去会让「读在哪、写在哪」不一致，还要改 `test_main.py` 的 `test_get_config_returns_models_and_games`。**一个写端点对应一个读端点。**
- **`source` 用 `Literal`**（与 `PublicGamePhase` / `ProviderProfileId` 同风格，前端可穷举）；**不为「哪些局在采集」加端点**（scope creep，用户在文件系统里看得到）。

### A.7.3 前端对应关系

| 前端 | 后端 |
| --- | --- |
| `fetchPromptCaptureSetting(): Promise<PromptCaptureSetting>` | `GET /api/settings/prompt-capture` |
| `updatePromptCaptureSetting(enabled: boolean \| null): Promise<PromptCaptureSetting>` | `PATCH /api/settings/prompt-capture`，body `{enabled}` |
| `useAppSettingsStore()` 的 `load()` / `setEnabled(v)` / `followEnv()` | 上面的两个函数 |
| `types.ts` 的 `PromptCaptureSetting { enabled: boolean; source: 'default' \| 'env' \| 'ui' }` | `PromptCaptureSettingsResponse` |

`frontend/src/api/client.ts` 的写法照 `listModels` / `updateModel`：裸 `fetch` + `getApiBase()` + 非 2xx 抛 `Error(\`... failed: ${res.status}\`)`。Zustand store 照 `modelConfigStore.ts` 的形状（`{setting, loading, error, load, setEnabled}` + `messageOf(error)`）。「恢复为跟随环境变量」就是 `setEnabled(null)`。

## A.8 测试方案与 100% 覆盖

### A.8.1 后端测试文件

| 文件 | 内容 |
| --- | --- |
| `backend/tests/test_app_settings_store.py`（新） | 存储读写、四种损坏回落、`None` 清除、单例与 `APP_SETTINGS_PATH` |
| `backend/app/test/test_config.py`（改） | `resolve_capture_setting` / `resolve_capture_max_chars` 全分支（含非字符串 `TypeError`） |
| `backend/app/core/test/test_prompt_capture.py`（改） | 原有 sink / record / flush 分支 + **新增** `capture_enabled_for_game`、marker 存在性、`__init__` 建 fd 即产生 0 字节标记、`__init__` 的 `OSError` 自降级 |
| `backend/app/core/test/test_game_engine.py`（改） | 引擎侧闩锁四态（见下） |
| `backend/tests/test_game_service_prompt_capture_settings.py`（新） | 建局/恢复两条路径 × 设置三态 |
| `backend/tests/test_settings_routes.py`（新） | GET 三来源、PATCH 三态、严格布尔 422、响应 key 闭集、`/api/config` 不受影响 |

### A.8.2 逐分支清单（实现时的 checklist，也是达 100% 的证明）

`resolve_capture_setting`
- [ ] `stored=True` → `(True, "ui")`；`stored=False` → `(False, "ui")`（**env 不参与**：env 设成相反值也要拿到 ui 的值）
- [ ] `stored=None` + 未传 `value` + env 未设 → `(True, "default")` ← **默认开**
- [ ] env = `"true" / "TRUE" / "1" / "yes" / "on" / " on "` → `(True, "env")`
- [ ] env = `"" / "false" / "0" / "off" / "maybe"` → `(False, "env")`（失败闭合）
- [ ] `value=123`（非字符串）→ `TypeError`

`resolve_capture_max_chars`
- [ ] 未设 + env 未设 → `0`；`"2048"` → `2048`；`"abc"` / `"-1"` / `"0"` / `None` → `0`

`capture_enabled_for_game` / `prompt_capture_marker_exists`
- [ ] `(True, False)` → `True`；`(False, True)` → `True`（**闩锁**）；`(False, False)` → `False`
- [ ] marker 文件存在 → `True`；不存在（含目录都不存在）→ `False`

`PromptCapture.__init__`（由懒开改成**急切建 fd**）
- [ ] 成功 → `llm_prompts.jsonl` 立即存在且为 **0 字节**（既是标记，也是「开着但还没调用」的可见证据）
- [ ] `os.makedirs` / `os.open` 抛 `OSError` → 自降级、warning 一次、`flush` 变 no-op、**不抛给引擎**、不再重试 open
- [ ] 已有文件时重开 → **追加**，不截断、不丢旧行（恢复续采的关键）

`GameEngine(prompt_capture_enabled=...)`
- [ ] `False` + 无 marker → `prompt_capture is None`，且**不创建 `llm_prompts.jsonl`**（断言要针对文件而非目录：`game.log` 的目录可能已被 logger 建立）
- [ ] `False` + 有 marker → 有采集对象（恢复续采）；`True` → 有采集对象 + marker 存在
- [ ] `True` + `os.open` 失败 → 对象存在但自降级，构造不抛
- [ ] **默认值（不传参）→ `None`**：这条保证既有全部引擎用例零扰动

`GameService`
- [ ] 建局：存储无值 + env 未设 → 引擎带采集
- [ ] 建局：`set_prompt_capture_enabled(False)` → 不带采集；`(True)` → 带采集（**且不重启进程**，证明是调用时读）
- [ ] 恢复：marker 存在 → 继续采集（哪怕存储已改成 `False`）
- [ ] 恢复：marker 不存在 → 不采集（**哪怕存储是 `True`** ← 本条就是「下一局生效」的回归网）

`settings_routes`
- [ ] GET：`default` / `env` / `ui` 三来源各一例
- [ ] PATCH `{"enabled": true}` → `{"enabled": true, "source": "ui"}` 且文件里落了值；`false` 取反
- [ ] PATCH `{"enabled": null}` → 清除并回落 env/default；`{}` 同效
- [ ] PATCH `{"enabled": "yes"}` / `{"enabled": 1}` → **422**（严格布尔）
- [ ] 响应 key 集合精确等于 `{"enabled", "source"}`
- [ ] `test_main.py` 补 `"/api/settings/prompt-capture" in paths`

`app_settings_store`
- [ ] `_read`：文件缺失（`OSError`）/ JSON 坏 / 根非 dict / `settings` 非 dict → 全部回落空设置且 warning
- [ ] `get`：键缺失 → `None`；`true` → `True`；`false` → `False`；`"true"` / `1` → `None`（防御）
- [ ] `set(True/False)` 落值并原子替换；`set(None)` 删键；目标目录不存在时自动建
- [ ] 单例：二次调用同一对象；`APP_SETTINGS_PATH` 覆盖；`WOLFKILLER_DATA_DIR` 生效

预计新增 ~55 case。若出现物理不可达的防御分支，用 `# pragma: no cover` + 提交信息写明理由（仓内先例：`model_invocation_service.py:73`）。

### A.8.3 Windows 侧跑门禁的注意事项

用户在 **Windows** 跑 pytest（不在 WSL），所以：

1. 命令与 CI 一致：`cd backend && python -m pytest tests app --cov=app --cov-branch --cov-fail-under=100 -q`。**不要用 WSL**：本机 WSL 侧没装后端依赖（原方案 §5.2 末尾已记录 `ModuleNotFoundError: langchain_core`）。
2. **不要写依赖 POSIX 权限的测试**（`chmod 0o500` 让 `makedirs` 失败的写法在 Windows 上不成立，Windows 的 `os.chmod` 只影响只读位）。用 `monkeypatch.setattr(os, "makedirs"/"open", boom)` 注入 `OSError`：跨平台且精确命中目标分支。
3. **不要写「运行中删除 `llm_prompts.jsonl`」的测试**（Windows 上打开的句柄会拒绝删除）。测「删除后恢复不采集」时必须先显式 `capture.close()`。
4. `os.replace` 覆盖已存在文件在 Windows 上成立，`model_config_store` 已在用，可照抄。新增测试全部用 `tmp_path` / `monkeypatch`，不碰真实的 `backend/data/`。

### A.8.4 前端：写测试，**不进覆盖率白名单**

- **不进白名单**。`frontend/vite.config.ts` 的 `coverage.include` 恰好 9 个文件（`gameStore.ts` / `benchmarkStore.ts` / `websocket.ts` / `GameBoard.tsx` / `TimelineController.tsx` / `HistoryPanel.tsx` / `WinOverlay.tsx` / `GameList.tsx` / `GameCard.tsx`）—— **整个模型配置 UI（`ModelConfigPage.tsx` / `ModelConfigDialog.tsx` / `modelConfigStore.ts`）都不在里面**，这是既有先例。新增设置页照此办理，`CLAUDE.md` / `AGENTS.md` 里「9 个文件」的表述**不需要改**。
- **但仍然要写测试**（先例证明这是仓库惯例，不是为了门禁）：`modelConfigStore.ts` 有 `src/store/test/modelConfigStore.test.ts`，`ModelConfigPage.tsx` 有 `src/components/models/test/ModelConfigPage.test.tsx`。照此新增 `src/store/test/appSettingsStore.test.ts`（`load` 成功/失败、`setEnabled(true/false/null)`、错误信息）与 `src/components/settings/test/SettingsPage.test.tsx`（`// @vitest-environment jsdom` + `vi.mock('../../../api/client')`：三来源副标题、开关点击调用、仅 `source==='ui'` 才出现「恢复为跟随环境变量」、失败弹 `Alert`）；`src/test/App.test.tsx` 补一行「渲染 设置 nav button」（对齐既有的 `renders the 模型管理 nav button`）。
- 门禁照跑：`npm run lint` / `npm test` / `npm run build` 必须全绿（`build` 是 `tsc -b && vite build`，所以新增的 `types.ts` 字段与 `client.ts` 签名必须类型正确）。
- **若评审坚持把 `appSettingsStore.ts` 纳入门禁**，它是唯一值得纳入的文件（逻辑小、可 100%），但必须同时把两份规则文档的「9 个文件」改成「10 个文件」。**默认建议：不加。**

## A.9 实施步骤（每 commit ≤3 文件 + 全绿门禁）

原方案 §7 的 C1 / C3 / C5 / C6 **原样保留**；C2 / C4 按 §A.0 扩写；新增 C7~C12。

| Commit | 类型 / scope | 文件（≤3） | 内容 | 依赖 |
| --- | --- | --- | --- | --- |
| **C2′** | `feat(config): gate prompt capture behind env and stored settings` | `app/config.py`<br>`app/test/test_config.py` | `PromptCaptureSetting` + `resolve_capture_setting(stored, value=)` + `resolve_capture_max_chars`，全分支测试 | C1 |
| **C4′** | `feat(engine): latch prompt capture per game` | `app/core/game_engine.py`<br>`app/core/test/test_game_engine.py`<br>`app/core/prompt_capture.py` | `prompt_capture_enabled: bool = False` 入参 + `capture_enabled_for_game` + marker 助手 + `__init__` 急切建 fd；引擎四态测试 | C1, C2′, C3 |
| **C7** | `feat(stores): persist app-level settings` | `app/stores/app_settings_store.py`（新）<br>`tests/test_app_settings_store.py`（新） | Protocol + JSON 实现 + 惰性单例 + `APP_SETTINGS_PATH` | — |
| **C8** | `feat(service): resolve prompt capture at game creation only` | `app/services/game_service.py`<br>`tests/test_game_service_prompt_capture_settings.py`（新） | 建局读设置、**恢复一律传 `False`**（靠 marker 续采） | C4′, C7 |
| **C9** | `feat(api): expose the prompt capture switch` | `app/api/settings_schemas.py`（新）<br>`app/api/routes/settings_routes.py`（新）<br>`tests/test_settings_routes.py`（新） | GET + PATCH + 严格布尔 + `extra="forbid"` + key 闭集断言 | C7 |
| **C10** | `feat(api): mount the settings router` | `app/main.py`<br>`tests/test_main.py` | `include_router` + 路径断言 | C9 |
| **C11** | `feat(web): add the training-corpus capture switch` | `src/store/types.ts`<br>`src/api/client.ts`<br>`src/store/appSettingsStore.ts`（新） | 类型 + 两个 fetch + Zustand store | C10 |
| **C12** | `feat(web): add the settings page and nav entry` | `src/components/settings/SettingsPage.tsx`（新）<br>`src/components/settings/test/SettingsPage.test.tsx`（新）<br>`src/store/test/appSettingsStore.test.ts`（新） | §A.5 的文案与交互 + 测试 | C11 |
| **C13** | `feat(web): route /settings and expose it in the app bar` | `src/App.tsx`<br>`src/test/App.test.tsx` | 路由 + 「设置」按钮 + 断言 | C12 |
| **C14** | `docs: document the capture switch, its UI and the settings store` | `CLAUDE.md`<br>`AGENTS.md`<br>`docs/architecture.md` | 见下 | C10, C13 |

**文档同步的具体落点**（三份文件描述同一套架构，必须一致）：

- `docs/architecture.md`：`## 目录结构` 的 `stores/` 注释加「应用级设置」、`frontend/src/components/` 加 `settings/`；`## 模型配置与角色目录` 加一段「应用设置（`stores/app_settings_store.py` + `data/settings.json`）」；`## 持久化与存档` 补 `llm_prompts.jsonl` 与 `settings.json` 两个文件及「下一局生效」语义；`## 关键设计约束` 的隐私边界条目补「开关状态不是私有字段，但开关端点响应必须闭集」。
- `CLAUDE.md` / `AGENTS.md`：`stores/model_config_store.py + ...` 的邻居加 `stores/app_settings_store.py`；「模型配置与角色目录」小节补 `api/routes/settings_routes.py` 与前端 `components/settings/SettingsPage.tsx`；注意事项补**三条红线** —— (a) 采集开关**不进** `GameConfig` / checkpoint / `PointKey` / `_MODEL_PARAMETER_FIELDS`；(b) 生效时机是**下一局**，恢复路径不读开关，`llm_prompts.jsonl` 的存在与否就是闩锁；(c) `/api/settings/prompt-capture` 的响应是闭集（`enabled` + `source`），不得携带 prompt 内容 / `game_id` / 文件路径。

**业务代码行数核算（本轮增量）**：`app_settings_store.py` ~85 + `settings_schemas.py` ~25 + `settings_routes.py` ~55 + `config.py` ~35 + `prompt_capture.py` ~40 + `game_engine.py` ~10 + `game_service.py` ~8 + 前端 `appSettingsStore.ts` ~55 + `SettingsPage.tsx` ~110 + `client.ts` ~20 + `types.ts` ~6 ≈ **449 行**，**超过 400 行/PR 的业务代码上限**，所以必须拆成两个 PR：**PR-1（后端）** C2′→C10 ≈ 258 行 ✅；**PR-2（前端 + 文档）** C11→C14 ≈ 191 行 ✅。即便按「diff 行数」口径，仍建议按后端/前端切分：C5 已是原文认定的高风险 commit，再叠一个前端 PR 会让评审面过大。

## A.10 风险增补

| # | 风险 | 严重度 | 触发条件 | 缓解措施 |
| --- | --- | --- | --- | --- |
| R15 | **开关被塞进 `GameConfig`**（「这样每局可配多好」） | 🔴 P0 | 有人觉得「按对局开关」更自然 | 硬红线。原文 §3.3 给了正确做法（另建 `game_capture_flags` 表），本附录进一步证明**不需要**：`llm_prompts.jsonl` 的存在与否已是精确的对局级闩锁，零新表 |
| R16 | **恢复路径读了开关 → 半局采集** | 🔴 P0 | `recover` 也调 `resolve_capture_setting()` | 恢复一律传 `prompt_capture_enabled=False`；C8 的「存储是 `True` 但 marker 不存在 → 不采集」测试是专门的回归网 |
| R17 | **两个 `resolve_capture_setting` 副本 → 路由与引擎报不一致的值**；或 `settings.json` 写坏导致 500 | 🟠 P1 | 有人在路由里重写一遍优先级 / 手改文件 / 磁盘故障 | 唯一实现放 `app/config.py`，路由与 `GameService` 都调它，并对「UI 与 env 冲突」做交叉断言；`_read` 四种情况全回落空设置（照抄 `model_config_store`），**永不对调用方抛异常** |
| R19 | **新端点变成私有字段的泄洪口** | 🟠 P1 | 后续有人往响应里加 `game_id` / 路径 / prompt 摘要 | `extra="forbid", strict=True` + 测试断言 key 集合精确等于 `{"enabled", "source"}` |
| R20 | **文案承诺了实现做不到的事** | 🟡 P2 | 文案写「立即停止」但实现是下一局生效；或写「不影响对局」而 flush 被挪到响应之前 | §A.5 文案逐条对应 §A.4.1 与 §4.1；C12 的 UI 测试断言「生效时机」这段文字存在 |
| R21 | **急切建 fd 让每局常驻一个句柄** | 🟢 P3 | 多局并发 | 与「每个写者本来就持有日志路径」同量级；`close()` 挂在既有清理点。收益是 (a) 0 字节标记即刻可用、(b) 躲开 §4.2 实测的 10.46 ms/条的 open/close 开销 |

## A.11 仍需你拍板的两点

1. **「关闭后正在进行的对局仍继续写」能接受吗？** 这是「下一局生效」的必然推论，也是唯一能保证「一局要么全采要么不采」的做法（§A.4）。若你要求「关掉就立刻停」，代价是接受半局语料，且 §5.3-B 的对局级完整性断言要降级成「弱断言」——请明确取舍。
2. **前端覆盖率白名单要不要从 9 个变 10 个？** 我推荐**不变**（照模型配置 UI 的先例，不进白名单但仍写测试）。若你要纳入，只纳入 `appSettingsStore.ts`，并同步改 `CLAUDE.md` / `AGENTS.md` 的「9 个文件」。
