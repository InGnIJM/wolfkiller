# 共享缓存 session：把 12 路流量收敛到一台 OpenCode Go 后端（2026-11-05）

## 结论与状态

- **已实现，默认 `off`**；与 `WOLFKILLER_CACHE_ROUTE_PIN` 正交，可单独或组合开启。
- 动机：对局 `795bce17`（12 席 deepseek-v4.1-flash via OpenCode Go）`hit_rate_all` = 35.4%，逐座位 4%–66%。离线分析确认瓶颈是路由不是 prompt：全局 LCP 可复用上限 ~74%，25 次 >50% 相同前缀的调用报 0 命中，4/9 号两座位占全局 48% token 却落在冷后端。对照 `2026-11-03` note：deepseek-harness 79 次调用只用 ~2 个上游 session 命中 94.9%——流量集中是高命中的充分条件。

## 设计

| 项 | 值 |
| --- | --- |
| 开关 | `WOLFKILLER_SHARED_CACHE_SESSION=off|on`（进程级，建局/恢复各解析一次；无 `ab`——共享是对局全局属性，只能跨局对比） |
| 会话键 | `ses_` + `sha256("wk-shared:{session_header}:{game_id}")[:26]`（`cache_route.shared_session_id`）：与 `new_session_id` 同形状，且**按 game_id 确定**——进程重启/恢复对局回落同一后端 |
| 应用点 | `transport.session_id`（`LLMClient.set_session_id`），不走 `config.headers`：否则用户 header 永远压制 `reset_session` 的轮换 |
| 生效范围 | `cache_mode=routing` 且声明 `session_header` 的 client；按 `session_header` 值分组（防御异网关混局） |
| 接线 | `create_game` 建客户端后、`_pin_cache_routes` 之前；`_recover_game` 建引擎后（确定性 id 让续跑回到同一后端，恢复仍不探针） |
| 探测联动 | `pin_cache_routes` 改按 `client.session_id` 分组：每组只探代表座位，判冷时全组 `set_session_id(同一个新 id)`；solo 组行为与旧实现一致；结果加 `group_size` 写 `cache_route.jsonl` |

## 验证

1. `backend/.env`：`WOLFKILLER_SHARED_CACHE_SESSION=on`（可与 `WOLFKILLER_CACHE_ROUTE_PIN=on` 并存），重启后端，开一局 GO deepseek 12 人局。
2. `data/games/<id>/cache_route.jsonl`：应只有 1 个探测组（`group_size=12`），不再 12 行独立探测。
3. 对局结束 `summary.json` → `llm.cache.overall.hit_rate_all`：**基线 35.4%，判定阈值 ≥50%，目标 60–74%**（上限为全局 LCP 74%，私有上下文永远打不中别人的缓存）；各座位命中率应趋同（同一后端）。
4. `model_errors.log`：确认网关对单 session 无速率限制（429/超时激增即回退 env）。

## 风险与回退

- 单 session 限流是黑盒：DSH 79 次/2 session 无恙，但 OpenCode Go 未承诺。首局盯错误率；超限则回 `off`，后续迭代再考虑"每组 3–4 座位"分片。
- session 语义超出路由键的概率存在但无正确性风险（prompt 无状态全量）。
- 回退 = 删 env 或置 `off` + 重启；session 不进任何持久化状态。

## 追加实验：投票预热首发 `WOLFKILLER_BURST_WARMUP`（同日）

### 动机

共享 session 实跑（`9b811a7f`）`hit_rate_all` 35.4% → 56.3%，但低于 75.5% 的 prompt 结构上限。逐调用配对发现剩余缺口集中在 `action`（放逐投票）类：`_execute_vote_casting` 用 `Semaphore(5)` + `gather` 并行发 10–12 张票，大量调用只命中 2048 token（≈公共 system 块）或 0——后续调用吃不到几秒钟前刚提交的同契约前缀。间隔证据：`gap<60s` 平均命中 44.9%（40 次 0 命中），`gap 60–300s` 平均 83.6%/中位 98.6%——是缓存**写入可见性延迟**而非淘汰。警长票/狼刀票本就是串行 for 循环（天然互暖），无需改。

同轮同契约跨座位 LCP 呈**双峰**：高簇 0.77–0.91（通用 prompt 结构座位），低簇 0.30–0.38（女巫等私有块在 ~2172 字符即分叉）。预热首发只能提升高簇，低簇本就是跨座位上限。

### 设计

| 项 | 值 |
| --- | --- |
| 开关 | `WOLFKILLER_BURST_WARMUP=off|on`（进程级，建局/恢复各解析一次；与共享 session 独立，便于同条件 A/B） |
| 调度 | `GameEngine(vote_warmup=True)`：`_execute_vote_casting` 先 `await collect_vote(seats[0])`，完成后再 `gather` 其余座位（`seats` 已排序 → 确定性） |
| 预算 | 两阶段仍在同一个 `wait_for(vote_phase_timeout)` 内；首发即真实投票（走相同 `collect_vote` 路径，失败照样技术弃权），不是探针 |
| 退化 | `len(seats)<=1` 时与原路径一致；`off` 时代码路径完全等价 |
| 适用范围 | 仅放逐投票；警长/狼队票本就串行。仅对共享 session 有意义——非共享时 followers 吃不到暖，白付 ~15-30s/轮 |

### 验证口径

- `llm_calls.log` 时间戳：每轮 `seats[0]` 完成后其余才发起
- 判定：`llm.cache.by_kind.action.hit_rate` ≥65%（基线 50.7%）、`hit_rate_all` ≥62%（基线 56.3%）；低簇座位不变属预期
- 若 action 无提升 → 写入延迟 > 单调用耗时（观测到 partner_age>100s 仍 2048 的案例，可能是分钟级落盘），方案改「错峰 stagger」或放弃；回退 = env 置 `off` + 重启
- 副作用观察：投票阶段时长 +15~30s/轮（540s 预算内可吸收）；benchmark 时长口径注意

## 追加修复：截断重试、夜晚重排、预热等待上限（2026-11-06）

### 基线（对局 `53266566`，三开关全开）

全局 `hit_rate_all` = 57.3%（action 55.1 / speech 65.4 / night 44.1），离线前缀上限 74.9%，实际约为上限的 79%。

### 发现

1. **放逐投票大量截断**：建局快照 `action_max_tokens=4096`（`games` 表持久化，不随 .env 更新），29 次首投中 11 次 `completion_tokens=4096`、空内容、`action_response_not_text`。每次浪费 ~8.7k prompt + 4096 completion + 30–50s。
2. **重试全部打不中缓存**：`build_vote_retry_prompt` 以 `<authoritative_state>`+私有身份开头，和原 prompt 只共享 ~2.2k 字符 system；`.env` 已在赛后改为 `LLM_ACTION_MAX_TOKENS=8192`。
3. **夜晚狼队前缀早分叉**：system 里拼了契约语句（讨论/投票不同）；user 里座位私有段（身份/自刀条款/RANDOM_HINT）排在讨论记录之前。

### 修复（已实现）

| 项 | 改动 |
| --- | --- |
| 简短推理 | `_vote_instruction` 尾部要求「先结论、reasoning ≤100 字、不展开长篇分析」 |
| 截断分类 | `finish_reason∈{length,max_tokens}` 且空响应 → `ActionValidationError(code="action_response_truncated")`；tool 通道同因不再误判 `StrictCapabilityError` |
| 重试吃缓存 | **仅超时**用紧凑 `build_vote_retry_prompt`；校验失败/截断/瞬时故障/能力降级均在原 messages 后追加一条提醒（截断时用专门的中文简短指令），第 3 次尝试同理（`_final_retry_messages(timed_out)`） |
| 夜晚重排 | system 只剩全体狼共用前缀；user = `_TARGET_RULE` → 共享简报 → 当前局势 → 讨论记录 → 已出票(投票) → 契约说明+JSON → 座位私有尾（身份/思考/自刀/倡导者/轮次）；投票与讨论共用 `已进行的讨论` 标题、`## 当前局势` 行不含阶段词（修复后补），使首票能复用最后一次讨论的前缀 |
| 预热等待 | `WOLFKILLER_BURST_WARMUP_MAX_WAIT_SECONDS`：默认 3.0 秒后放行其余座位；`<=0` 恢复等首票全部完成；非法值建局时报错 |

### 实测（对局 `77a92115`，2026-11-06，修复后第一局）

全局 `hit_rate_all` = **57.0%**（action 57.8 / speech 60.5 / night 47.3），与基线 57.3% 基本持平、未达 62–65 目标。机制全部生效，但两个残余问题吃掉了收益：

| 验证点 | 结果 |
| --- | --- |
| 非超时重试吃缓存 | ✅ 重试 `cached/prompt` ≈ **97–99.7%**（旧方案 ~0），如 8960/9001、11520/11577 |
| 预热 3s 上限 | ✅ 跟随座位在首票起步后 **3.0–3.5s** 发出，cached=6144（首票为 0） |
| `action_response_truncated` 上报 | ✅ 8 次正确归类，未再误判 `StrictCapabilityError`/not_text |
| 首投截断 | ❌ 仍有 **8 次打满 8192**（reasoning 模型把预算全花在隐藏推理，简短指令压不住）；重试成功但每次仍浪费 ~9k prompt + 8192 completion + 40–60s |
| night 命中率 | ⚠️ 44.1→47.3%，未达 52：发现 `## 当前局势` 行「第N晚狼队讨论/投票」阶段词在共享段内分叉（实跑 LCP 仅 ~3.9k 字符），已改为中性「狼队行动」并补测试 |
| 首只狼票 cached=0 | ⚠️ 每夜最低座号狼首票连续 0 命中——讨论结束到投票仅数秒~30s，疑似网关写入延迟，代码侧无解 |

**下一步**：截断是剩余最大浪费源——8192 都不够说明是隐藏推理不受指令约束，考虑更高的 `LLM_ACTION_MAX_TOKENS`（如 16384）、换非 reasoning 投票模型、或接受每次 ~1 次截断重试的现状（重试已 ~99% 命中，代价可控）。

### 判定口径（下一局）

- 全局 `hit_rate_all` ≥62%（目标 62–65）；`action.hit_rate_all` 提升
- `exile_vote` 首投截断次数（`action_response_truncated` 或打满输出上限）≤2
- 非超时重试 `cached/prompt` ≥70%；`night.hit_rate_all` ≥52%
- 时间戳：跟随座位在首票发起 ≤3.x 秒内发出；若预热跟随者命中无改善 → `WOLFKILLER_BURST_WARMUP_MAX_WAIT_SECONDS=0` 回退并在本 note 记录

## 关联

- `2026-11-04-opencode-go-cache-routing.md`（路由黑盒实测、P2 探针、P0 直连建议——直连因成本放弃）
- `2026-11-03-session-affinity-experiment.md`（custom-openai 网关的 `x-session-id` 实验，目标 profile 不重叠）
- 代码：`app/agents/cache_route.py`（`SharedSessionMode` / `shared_session_id` / `apply_shared_session` / 分组 `pin_cache_routes` / `BurstWarmupMode` / `burst_warmup_mode`）、`LLMClient.session_id` / `set_session_id`、`game_service._share_cache_session`、`GameEngine(vote_warmup=)` + `_execute_vote_casting` 两阶段收集
- 测试：`tests/test_cache_route.py`（模式解析、id 形状与确定性、仅 routing 生效、按 header 分组、组探测只探代表、冷组同 id 轮换、组成员轮换失败、create_game 接线顺序、`_share_cache_session` 容错、burst warmup env 解析与建局/恢复传参）；`tests/test_game_engine.py`（warmup 首座位先行、单座退化、首阶段/次阶段超时、默认 off）

## 追加实验：放逐投票独立模型（2026-11-06）

### 动机

修复后首局 `77a92115` 仍有 8/52 次投票打满 `action_max_tokens=8192`——reasoning 模型的隐藏推理不受「简短」指令约束，每次截断浪费 ~9k prompt + 8192 completion + 40–60s。提高阈值只是把税加大；治本是给 `exile_vote` 换一个非 reasoning 模型。

### 机制（已实现）

- `create_game(vote_model_config_id=)` / `CreateGameRequest.vote_model_config_id` / 前端 ModelStep「投票模型」下拉（空 = 跟随座位模型）。
- 建局时额外建**一个全座位共享的 `LLMClient`**，注入每个 `BaseRole.vote_llm_client`；`request_action` 仅在 `contract_id == "exile_vote"` 时改走它（`_VOTE_MODEL_CONTRACTS`），发言/夜晚/其他契约/警长票仍走座位模型。
- 能力学习跨座位共享：`supports_action_tools` / `prefers_json_actions` / `note_action_tools_unsupported` 挂在 client 上，首个座位探出的能力全局生效；`llm_calls.log` 的 `model_id` 如实显示投票模型。
- 选择冻结进 config doc `vote_model`（`config_id` + `parameters` + `parameters_digest`），恢复路径 `_resolve_recovery_vote_model` 按同口径重建；缺键老档自动回落座位模型，benchmark 不传参行为不变。
- 共享缓存 session 把它当 `seat 0` 并入同组 `ses_`（routing profile 自动生效，direct 天然跨座位）；`pin_cache_routes` / `health_guard` 仍只按座位。预热 3s 机制对投票 client 照常适用。

### 验收口径（下一局）

- `llm_calls.log` 的 `contract_id=exile_vote` 行 `model_id` 为新投票模型。
- `action_response_truncated` 次数：**目标 0**（非 reasoning 模型无隐藏推理）。
- `exile_vote` 的 `elapsed_ms` 显著下降（对照 ~40–60s 截断调用）。
- 全局 `hit_rate_all` 变化记录于此；投票共享 client 后跨座位前缀复用预期略有帮助（同一后端同一 session）。
- 投票质量抽查：放逐票 `reasoning` 合理性人工看 2–3 条。
