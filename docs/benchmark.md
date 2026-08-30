# Benchmark 基准评测指南

本文说明 WolfKiller 的 benchmark 工具链：如何采集数据、如何跑批量对局评测、如何做引擎性能回归，以及各指标的定义。

## 两层基准

| 层 | LLM | 测什么 | 成本 | 频率 |
| --- | --- | --- | --- | --- |
| 回归层（`perf_benchmark.py`） | 脚本 mock，零成本 | 引擎侧性能：调度、Hook、验证、落盘 | 无 | 可反复跑，适合改动后回归 |
| 评测层（`run_benchmark.py`） | 真实模型调用 | 对局质量、token 成本、调用延迟 | 消耗 token | 版本对比 / 模型对比时手动触发 |

两者的数据来源相同：每局对局的 `game.log`、`llm_calls.log` 与 `summary.json`。

## 数据采集层

对局过程中自动落盘到 `data/games/<game_id>/`：

- `game.log`（既有）：阶段切换、发言、投票、死亡、胜负；本次新增两类记录：
  - `stage_telemetry`（`scope=phase`）：引擎每个相位段的耗时（毫秒）；
  - `stage_telemetry`（`scope=hook`）：调度器 slow_rule 事件（超过 Hook 软预算的规则调用，含精确 `elapsed_ms`）。
- `llm_calls.log`（新增）：每次 LLM 调用一条记录，字段包括 `call_kind`（action / speech / speech_plain / night）、`contract_id`、`transport`、`attempt`、`model_id`、`prompt_chars`、`elapsed_ms`、`prompt_tokens` / `completion_tokens` / `total_tokens`、`retried`、`parse_result`、`failure_code`。只记录元数据，不含 prompt 内容与模型输出。
- `summary.json`（新增）：对局结束（`game_over` 事件）时自动生成的结构化终局档案，含角色真值表、逐轮行动/发言/投票/死亡、LLM 聚合（调用量、token、延迟分位数）、引擎相位耗时汇总。历史存档可用脚本补生成：

```bash
cd backend
python scripts/export_game_summary.py --all                # 为所有存档补 summary
python scripts/export_game_summary.py --game-id <game_id>  # 单局补生成
```

token 统计的兼容性：优先读取 LangChain 的 `usage_metadata`，回退 OpenAI 风格的 `response_metadata["token_usage"]`；提供商不返回用量时对应字段缺省。JSON fallback 重试烧掉的 token 会计入该次调用的总量（`llm_attempts` 记录实际调用次数）。

## 评测层：批量对局评测

```bash
cd backend
# 使用 data/models.json 中已配置的模型（推荐）
python scripts/run_benchmark.py --games 20 --label base-v1 --model-config-id <id> --yes

# 使用 .env 默认模型（需要 DEEPSEEK_API_KEY）
python scripts/run_benchmark.py --games 20 --label base-v1 --yes
```

- `--concurrency` 同时进行的对局数，默认 1。每局是独立的引擎实例；加大前建议先验证提供商的并发限额。
- 运行前会打印局数与模型并要求确认（`--yes` 跳过）。脚本执行**真实模型调用**。
- 输出到 `data/benchmarks/<label>/<时间戳>/`：`run.json`（对局清单与结局）、`report.json`（指标聚合）、`report.md`（可读报告）。
- 胜率类指标建议每配置至少 30–100 局；10–20 局只能看趋势。

## 回归层：引擎性能基准

```bash
cd backend
python scripts/perf_benchmark.py --games 5
python scripts/perf_benchmark.py --games 5 --label nightly --compare-to nightly
```

- 所有 LLM 调用由脚本内的确定性 stub 应答（不联网、零 token 成本），因此测得的是纯引擎耗时。
- 输出 `data/perf/<label>/<时间戳>/perf_report.json`：每个相位每局的平均/ p95 耗时、slow_rule 计数；`--compare-to <label>` 会与该 label 的 `latest.json` 对比并打印各相位变化。
- 计时受机器负载影响，不建议作为 CI 硬门禁；用于改动前后的相对对比。

## 指标定义

报告（`report.md`）中的指标分为四组：

**对局结果**
- 阵营胜率：完成对局中狼人 / 好人获胜的占比（`werewolf` / `good`）。
- 角色胜率：某角色的座位所在阵营获胜的占比。
- 平均局长：完成对局的平均天数（`rounds_played`）。
- 中止率：以 `error` 结束或超时的对局占比。

**决策质量**（跨局汇总：命中数 ÷ 机会数）
- 预言家验中狼率：`SEER_CHECK` 事件中目标座位真实身份为狼人的比例。
- 女巫毒药命中狼率：`WITCH_POISON` 目标为狼人的比例；救药使用次数单独计数。
- 猎人开枪带狼率：`HUNTER_SHOT` 目标为狼人的比例。
- 狼刀命中神职率：`WEREWOLF_KILL` 目标为神职（预言家/女巫/猎人/守卫）的比例。
- 好人投票命中率 / 弃票率：好人阵营座位投票目标为狼人的比例 / 弃票比例。
- 放逐命中狼率 / 误伤神职率：被放逐座位为狼人 / 神职的比例。
- 狼队投票集中度：狼人票中最多票目标的占比（协同度参考）。

**规范性**
- LLM fallback 率：`parse_result` 为 `fallback` / `parse_failed` 的调用占比。
- LLM 错误率：`parse_result=error`（含重试后失败）的调用占比。
- 技术性弃票：超时或解析失败导致的系统代投弃权次数。
- 模型错误：`model_errors.log` 记录条数。

**LLM 成本与延迟**
- 每局 prompt / completion / 总 token（评测层有效，取决于提供商是否返回用量）。
- 每局调用次数；单次调用延迟 p50 / p95 / p99（按局平均后再平均）。

## A/B 对比

两次 run 的 `report.json` 可直接对比：

```python
from app.benchmark.report import compare, render_comparison_markdown
rows = compare(base_aggregate, candidate_aggregate)   # 数值路径增量表
markdown = render_comparison_markdown(base_aggregate, candidate_aggregate)
```

`model_snapshot` 与 `pipeline_version` 已写入每局 summary，可追溯每局使用的模型与管线版本。

## 质量门禁

- 所有 `app/` 内新增模块（`app/benchmark/`、`game_summary.py`、遥测路径）均纳入 pytest 覆盖率 100%（语句+分支）门禁。
- `scripts/` 下的三个脚本不参与覆盖率门禁；`perf_benchmark.py` 与 `run_benchmark.py` 不依赖真实模型即可通过参数校验测试。
- 遥测只记录元数据（计数、耗时、token 用量），不记录 prompt、模型原始输出或私有上下文，符合隐私边界约束。
