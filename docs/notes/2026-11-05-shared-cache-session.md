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

## 关联

- `2026-11-04-opencode-go-cache-routing.md`（路由黑盒实测、P2 探针、P0 直连建议——直连因成本放弃）
- `2026-11-03-session-affinity-experiment.md`（custom-openai 网关的 `x-session-id` 实验，目标 profile 不重叠）
- 代码：`app/agents/cache_route.py`（`SharedSessionMode` / `shared_session_id` / `apply_shared_session` / 分组 `pin_cache_routes` / `BurstWarmupMode` / `burst_warmup_mode`）、`LLMClient.session_id` / `set_session_id`、`game_service._share_cache_session`、`GameEngine(vote_warmup=)` + `_execute_vote_casting` 两阶段收集
- 测试：`tests/test_cache_route.py`（模式解析、id 形状与确定性、仅 routing 生效、按 header 分组、组探测只探代表、冷组同 id 轮换、组成员轮换失败、create_game 接线顺序、`_share_cache_session` 容错、burst warmup env 解析与建局/恢复传参）；`tests/test_game_engine.py`（warmup 首座位先行、单座退化、首阶段/次阶段超时、默认 off）
