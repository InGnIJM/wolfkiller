# 实验方案：per-seat 稳定会话（`x-session-id`）

- 日期：2026-11-03
- 状态：**已实现，待跑**（默认开关 `off`，不改变现有对局）
- 事实源：`backend/data/games/<id>/llm_calls.log`、`summary.json`；可选网关 `~/.antigravity_tools/proxy_logs.db`
- 相关代码：`backend/app/agents/session_affinity.py`、`GameService.create_game` / `_build_recovered_engine`
- 报告脚本：`backend/scripts/session_affinity_report.py`（只读；该目录默认被 `.gitignore` 忽略，提交时需 `git add -f`）

## 背景

最新一局 `dc61ca2d`（12 人、Gemini-3.8-flash-high、狼人胜）的 `llm.cache`：

| 口径 | 值 |
| --- | --- |
| 有缓存字段的调用 | 11 / 184（5.98%） |
| 已知调用上的命中率 | 87.08%（166,325 / 190,992） |
| 整局 prompt tokens | 1,523,063（已知仅占 12.5%） |
| 未知按 0 的整局命中 | 10.9% |

网关自己的 `proxy_logs.db` 与 Agent 日志一致：本局窗口 197 次请求里只有 11 次带 `cached_tokens`，其余 186 次上游没有回报。同一网关/账号/模型在 2026-10-29 对 `deepseek-harness` 客户端（79 次调用只有 2 个上游 session）命中 94.9%。Antigravity-Manager 的会话优先级是 `x-*-session-id` → `x-session-id` → query → body `session_id` → 兜底内容锚点；WolfKiller 的 197 次请求一个都没带，且每条提示词只有 `[system, user]`，user 是整段变化的提示词，所以 196/197 个请求各开一个新 session。

结论：提示词前缀布局不是主因（离线可复用上限 76.7%，同座同契约 96.6–97.2%）；可实验的杠杆是**给同一座位一个稳定的显式会话 id**，让上游 `sessionId` 不再逐请求变化。

## 设置

| 项 | 值 |
| --- | --- |
| 开关 | `WOLFKILLER_SESSION_AFFINITY=off|on|ab`（默认 `off`，进程级，建局/恢复各读一次） |
| header | `x-session-id`（走 `LLMClientConfig.headers`，不进 snapshot/digest/公开记录） |
| 会话键 | `wk-{game_id}-s{seat}` |
| 生效范围 | 解析后的 provider profile = `custom-openai` |
| A/B | `ab` 按 `sha256(game_id:seat)` 排序取前 `total_players // 2` 个座位为 treatment；报告脚本可复算 |
| 粒度 | per `(game, seat)`；per-contract 作为后续备选 |
| 模型/板子 | 12 席全部 Gemini-3.8-flash-high（`custom-openai`，`127.0.0.1:8045`），12 人 native 局 |

## 指标与判定

1. **管道**：网关日志中 treatment 请求带预期 `x-session-id` 的比例 = 100%，control = 0。
2. **会话复用**：treatment 每座位 distinct 上游 `session_id` ≤ 2（目标 1）；control 接近“每次调用一个新 session”。
3. **主指标**：每局 `hit_rate_all = Σcached / Σprompt`（未知按 0），treatment vs control；活跃局（任一 arm 有 `cached_prompt_tokens > 0`）≥ 3 时，pooled treatment − control ≥ +10pp 且 ≥ 2/3 活跃局为正。
4. **安全**：错误/模板兜底/技术弃权率升幅 ≤ 5pp；p95 延迟不劣化 > 20%；prompt tokens/call 不变（本改动不触提示词）。
5. 活跃局 < 3 → **inconclusive**，换缓存窗口重跑，不判“无提升”。

`hit_rate_all` 与 `hit_rate_known` 必须同时报告；summary 的 87.08% 是 known-only 口径，不能当整局命中率。

## 运行步骤

1. 确认 12 席都用同一个 `custom-openai` 网关配置；实验期间不要切模型。
2. 后端进程环境设置 `WOLFKILLER_SESSION_AFFINITY=ab`（或写 `backend/.env` 后重启；`app/config.py` 会 `load_dotenv`），实验期间保持不变。
3. 先跑 1 局 smoke；用网关 `proxy_logs.db` 的 `request_headers` 确认 treatment 座位带 `x-session-id`，control 不带，且 treatment 的 `session_id` 开始重复。
4. 跑 6 局 native 12 人局；不要跑 benchmark。
5. 每局后执行：

```bash
cd backend
python scripts/session_affinity_report.py --mode ab --game-id <id> --gateway-db <proxy_logs.db>
```

6. 若前 3 局都没有活跃缓存，先查网关 `token_stats.db` 近期是否有 `cached_tokens > 0`，等到缓存活跃窗口再继续。
7. 用 Windows 版 Python 跑报告脚本时，`--gateway-db` 传 Windows 路径（如 `C:/Users/<user>/.antigravity_tools/proxy_logs.db`）；脚本会把数据库连同 WAL 复制到临时目录再只读打开，不碰线上文件。
8. 实验结束把结果补在下方，并按阈值决定是否推进“默认开启 / per-contract / 放弃”。

## 结果（待填）

| 指标 | treatment | control | 差值 |
| --- | --- | --- | --- |
| calls | — | — | — |
| coverage | — | — | — |
| hit_rate_all | — | — | — |
| hit_rate_known | — | — | — |
| 每座位 distinct session | — | — | — |
| 错误/兜底/弃权 | — | — | — |
| p95 延迟 | — | — | — |

## 判读注意

- 上游缓存是间歇性的：`on`/`ab` 不保证命中；没有活跃局时只能判“本窗口无结论”。
- 网关的 `[FIX session-1M]` 会按 session 累积上游输入；若出现 session 相关失败，改 per-contract/per-round 或关闭开关。
- 网关的思维库/签名缓存也按 session 存取；若模型行为（错误率、兜底率、发言质量）异常，优先怀疑这一点。
- 本实验不改 summary schema；若要长期保留，再单独提案 coverage 字段和 metric version bump。
