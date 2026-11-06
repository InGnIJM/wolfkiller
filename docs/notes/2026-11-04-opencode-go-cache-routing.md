# OpenCode Go 前缀缓存按会话路由（2026-11-04）

## 结论

- **直连 DeepSeek 的自动前缀缓存是确定性的**：同一 prompt 第二次起 96.1%，换 session、跨座位、跨局都不影响（内容前缀缓存）。
- **OpenCode Go 不是“没有缓存”，而是按 `x-opencode-session` 路由到上游后端**：缓存只落在被路由到的那个后端。不带 session 直接 400 `MissingSessionID`；8 个新 session × 2 次相同请求，两轮分别 hot 7 / cold 1 与 hot 5 / cold 3；同一个 session 甚至出现“先 3968、后 0”。
- 因此 run `5ea62b23`（2026-11-04）的 **36.2% 是 gateway 路由属性**，不是提示词布局（离线可复用上限 79.3%），也不是模型（直连 96%）。48 个座位里 8 个整局 0% 命中，其余 60–72%；同分钟同内容 10 号 0、11 号 4096。

## 探针

脚本：`backend/scripts/opencode_go_cache_probe.py`（`backend/scripts/` 默认被忽略，提交需 `git add -f`）。

```bash
cd backend
PY=/home/jm050711/.venvs/wk/bin/python
$PY scripts/opencode_go_cache_probe.py matrix --repeats 5 --growth-steps 4
$PY scripts/opencode_go_cache_probe.py matrix --real-replay --game-id <game-id>
$PY scripts/opencode_go_cache_probe.py matrix --targets deepseek   # 对照组
$PY scripts/opencode_go_cache_probe.py matrix --lottery 8 --lottery-only
```

关键结果：

| 实验 | OpenCode Go | 直连 DeepSeek |
|---|---|---|
| 同 session 重复同一 prompt | 通常 98%，但 run 间会 0 | 96.1% |
| 换 session 发同一 prompt | 随机（2/5、3/3） | 96.1% |
| 会话增长（DSH 模式） | 非流式 97–98%；流式曾 0/3 | 95–96% |
| 跨座位共享前缀 | 有时 0/2，有时 2/2 | 96.1% |
| 无 `x-opencode-session` | 400 `MissingSessionID` | 不需要 |
| 8 个新 session 抽签 | hot 7/1、5/3；出现“先命中后 0” | — |

## 修复方案与状态

### P0 评测切直连 DeepSeek（待执行，需在 Windows 后端/模型页操作）

1. `GET https://api.deepseek.com/v1/models` 确认可用模型 id（探针用的是 `deepseek-chat`；若 `deepseek-v4.1-flash` 不在直连目录，先用 `deepseek-chat`，并在报告里标注模型不同）。
2. 模型管理页新增配置：`provider_profile=deepseek`、`base_url=https://api.deepseek.com/v1`、`model_id=<上一步>`、名称 `DS direct (cache-safe)`。
3. 用该配置跑 4 局 `twelve-player-fragrance`、seed 42、`concurrency=4`。
4. 验收：`attempts.token_usage.cache_hit_rate ≥ 70%`（目标 75–80%）、`usage_completeness_rate ≥ 0.95`、`failed_items=0`；`probe --targets deepseek` 稳定 96%。
5. 正式评测（30+ 局）改用该配置；Go 配置保留给普通对局。benchmark run 的 `model_config_id` 冻结在 run 里，回滚不影响历史。

直连与 Go 的模型快照可能不同，**胜率是新的基线，不与 36.2% 那批直接比较**。

### P1 可观测性（已实现）

- `ProviderProfile.cache_mode`：`automatic`（deepseek）/ `routing`（opencode、opencode-go）/ `unknown`；代码元数据，不进 GameConfig/checkpoint/snapshot。
- 单局 summary：`llm.cache.by_seat`（每个座位一行 `known_calls` / `hit_rate` / `hit_rate_all` / `coverage`）。
- 评测报告 `METRIC_VERSION=v8`：`attempts.token_usage` 新增 `cache_known_seats` / `cache_cold_seats` / `cache_hit_rate_excluding_cold_seats`（冷座位 = 已知调用 ≥3 且 cached=0）。
- 测试：`tests/test_game_summary.py`、`tests/test_benchmark_metrics.py`、`tests/test_benchmark_service.py`、`app/agents/providers/test/test_registry.py`。

### P2 会话路由实验（已实现，默认 off）

- 开关：`WOLFKILLER_CACHE_ROUTE_PIN=off|on|ab`（进程级，建局时解析一次；`ab` 复用 `session_affinity.treatment_seats` 的确定性座位划分）。
- `app/agents/cache_route.py`：固定短 prompt 连发两次（第二次读 `cached_prompt_tokens`），冷 session 走 `LLMClient.reset_session()` 轮换 `x-opencode-session`，最多 2 次；只对 `cache_mode=routing` 的 profile 生效。
- `GameService.create_game` 在客户端建好后、引擎启动前接线；结果写 `data/games/<id>/cache_route.jsonl`。探针不进入耐久账本、summary 或 `llm_calls.log`。恢复路径**不探针**（session 反正是新的）。
- 测试：`tests/test_cache_route.py`（轮换、A/B 选座、超时、异常、日志、建局开关分支、`probe_cache`/`reset_session`）。

A/B 判定：treatment − control ≥ +10pp 且 ≥2/3 对局为正；错误/兜底率升幅 ≤5pp；p95 延迟劣化 ≤20%；否则保持 `off`。正式评测必须 `off`。

### P3 缓存提示（未做）

优先用探针扩展 `--cache-control` / `--prompt-cache-key` / `--api-mode anthropic` 验证；只有稳定提升且无 400 才实现。

### P4 文档

`docs/architecture.md`、`docs/benchmark.md`、`docs/development.md`、`CLAUDE.md`、`AGENTS.md` 已同步；本 note 记录决策与验证命令。

## 风险

- Go 的路由不透明，P2 只能提高概率，不能保证 100%。
- 探针会增加建局延迟（总预算 30s，仅 `on`）和少量额度。
- 直连 DeepSeek 可能遇到限流/区域限制；先 4 局 smoke，必要时降并发。
