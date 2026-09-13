# Benchmark 基准评测指南

本文说明 WolfKiller 的 benchmark 工具链：如何采集数据、如何跑批量对局评测、如何做引擎性能回归，以及各指标的定义。

## 两层基准

| 层 | LLM | 测什么 | 成本 | 频率 |
| --- | --- | --- | --- | --- |
| 回归层（`perf_benchmark.py`） | 脚本 mock，零成本 | 引擎侧性能：调度、Hook、验证、落盘 | 无 | 可反复跑，适合改动后回归 |
| 评测层（`run_benchmark.py`） | 真实模型调用 | 对局质量、token 成本、调用延迟 | 消耗 token | 版本对比 / 模型对比时手动触发 |

评测层以 SQLite 中冻结的 benchmark 计划、对局、模型请求和模型尝试为事实源；回归层仍读取脚本产生的本地性能结果。JSONL/`summary.json` 保留兼容与人工审计用途，不参与耐久 benchmark 报告的主口径。

## 数据采集层

新运行时在一个事务中把检查点、领域/公开事件、模型请求消费状态和派生任务写入 `data/wolfkiller.sqlite3`。以下兼容文件仍会写入 `data/games/<game_id>/`：

- `game.log`（既有）：阶段切换、发言、投票、死亡、胜负；本次新增两类记录：
  - `stage_telemetry`（`scope=phase`）：引擎每个相位段的耗时（毫秒）；
  - `stage_telemetry`（`scope=hook`）：调度器 slow_rule 事件（超过 Hook 软预算的规则调用，含精确 `elapsed_ms`）。
- `llm_calls.log`（新增）：每次 LLM 调用一条记录，字段包括 `call_kind`（action / speech / speech_plain / night）、`contract_id`、`transport`、`attempt`、`model_id`、`prompt_chars`、`elapsed_ms`、`prompt_tokens` / `completion_tokens` / `total_tokens`、`retried`、`parse_result`、`failure_code`。只记录元数据，不含 prompt 内容与模型输出。
- `summary.json`（新增）：对局结束（`game_over` 事件）时自动生成的结构化终局档案，含角色真值表、逐轮行动/发言/投票/死亡、LLM 聚合（调用量、token、延迟分位数）、引擎相位耗时汇总，以及对局级模型座位快照。历史存档可用脚本补生成：

```bash
cd backend
python scripts/export_game_summary.py --all                # 为所有存档补 summary
python scripts/export_game_summary.py --game-id <game_id>  # 单局补生成
```

token 统计的兼容性：优先读取 LangChain 的 `usage_metadata`，回退 OpenAI 风格的 `response_metadata["token_usage"]`；提供商不返回用量时对应字段缺省。JSON fallback 重试烧掉的 token 会计入该次调用的总量（`llm_attempts` 记录实际调用次数）。

## 评测层：API 与 CLI

先启动后端。CLI 是 HTTP 客户端，不会在命令进程里创建第二个 `GameService`，也不会绕过服务的进程锁与恢复逻辑。默认地址为 `http://127.0.0.1:8000`，可用 `--api-url` 或 `WOLFKILLER_API_URL` 修改；服务不可达会以退出码 2 和可操作错误退出。

```bash
cd backend
# 兼容旧参数：单配置 mixed_arena；不带 --yes 只创建并打印冻结草稿
python scripts/run_benchmark.py --games 20 --label base-v1 --model-config-id <id>

# 确认预览后创建、启动并等待（真实模型调用）
python scripts/run_benchmark.py --games 20 --label base-v1 --model-config-id <id> --yes

# 成对回归：每个 repetition 生成 baseline/candidate 两局
python scripts/run_benchmark.py create --mode paired --repetitions 30 \
  --baseline-model-config-id <base-id> --candidate-model-config-id <candidate-id>

# 控制已有运行；start/resume 必须显式带 --yes
python scripts/run_benchmark.py start <run-id> --yes --detach
python scripts/run_benchmark.py status <run-id>
python scripts/run_benchmark.py pause <run-id>
python scripts/run_benchmark.py resume <run-id> --yes
python scripts/run_benchmark.py export <run-id> --format json -o report.json
python scripts/run_benchmark.py export <run-id> --format csv -o report.csv
python scripts/run_benchmark.py export <run-id> --format markdown -o report.md
```

- `create` 和兼容 `run` 形式先 `POST /api/benchmarks`；服务在任何执行前冻结完整配置和确定性 schedule，响应就是 CLI 打印的预览。相同 `client_request_id` 与相同内容幂等重放，内容冲突会拒绝。
- 不带 `--yes` 的兼容 `run` 会让草稿保持 `pending` 并打印后续 start 命令；`start` 和 `resume` 同样要求 `--yes`。该标志表示授权真实模型调用，不是关闭 TLS 或错误检查。
- `--detach` 在启动/恢复后立即返回；否则 CLI 轮询服务端状态。客户端等待超时不会取消服务端运行。
- 旧 `--concurrency` 会传给服务端（上限 4），`--poll-interval` 和 `--timeout-seconds` 控制 CLI 等待；后者也作为默认单局超时写进冻结配置。`--no-report` 可继续传入，但报告本就只在查询/导出时生成。自定义 `--data-dir` 会明确拒绝，因为存储目录属于服务进程。
- 服务端按持久化的 `active_elapsed_ms` 执行单局超时，暂停时间不会消耗时限。冻结配置中的 `max_attempts_per_game` 按真实 provider attempt 计数，并在开始调用前以数据库事务原子占用；恢复重试也受同一上限约束。
- 旧 `--model-config-id/--games/--label` 清晰映射到只有一个 `models` 条目的 `mixed_arena`；省略模型 id 表示服务的环境默认配置。成对比较使用 `--mode paired` 与两个模型 id，不把单模型 run 伪装成 A/B。
- 状态与结果保存在服务数据库中；文件输出必须通过 export 接口显式生成，不再由 CLI 直接遍历 `data/games` 拼报告。
- 胜率类指标建议每配置至少 30–100 局；10–20 局只能看趋势。

REST 资源为：`POST/GET /api/benchmarks`、`GET /api/benchmarks/{id}`、`POST /api/benchmarks/{id}/start|pause|resume|cancel`、`GET /api/benchmarks/{id}/games`、`GET /api/benchmarks/{id}/report`、`POST /api/benchmarks/{id}/report/rebuild`、`GET /api/benchmarks/{id}/export?format=json|csv|markdown`。只有服务端执行器能改变运行状态；CLI 不直接打开 SQLite。

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

API 报告带 `metric_version` 与 `input_digest`。当前原生指标版本为 `v2`。digest 对四张冻结事实表做规范 JSON 排序后计算，因此数据库行顺序和字典键顺序不影响同一输入的报告；迟到的 token 用量写回会改变 digest 并生成新报告。

### 当前原生报告口径

- `games.total` 是能从 benchmark 条目关联到数据库对局的数量；`games.completed` 仅计有明确 winner 的对局。阵营胜率分母是 `completed`，不是计划局数；平均轮数/有效耗时各自只使用存在且非负的值。座位/角色胜率的分母是完成对局中该座位或角色具有可判定阵营的出现次数。
- `requests.total` 是全部持久化逻辑模型请求。成功、显式弃权和失败互斥分类，三种 rate 都以 `requests.total` 为分母；`requests.unknown` 单独给出进程中断后仍待恢复的请求数；没有请求时 rate 为 `null`。
- 延迟分位数只以具有非负 `elapsed_ms` 的模型尝试为分母，成功与失败尝试都可进入。`attempts.token_usage` 提供 `known_*_tokens`、已知/未知 attempt 数与 `completeness_rate`；旧的 `known_tokens`、`unknown_usage_count` 和 `usage_completeness_rate` 字段继续保留。未知用量不是 0，不能用已知合计除以全部尝试推算平均成本。
- `model_performance` 根据冻结座位模型分配和终局玩家真值，分别按模型×角色、模型×阵营给出样本数、胜局数和胜率。缺少 winner、座位模型或玩家阵营的记录不会被推断。
- `data_quality.interruptions` 汇总对局中断次数，`recovery_retries` 汇总已消费的恢复重试授权，`recovery_attempts` 统计恢复代次中实际开始的 provider attempt。
- paired regression 只纳入 baseline 与 candidate 都有可判定 score 的完整 pair。每个 pair 先求两侧平均，再计算 `candidate - baseline`；`pair_count` 是有效 pair 数，缺一侧的 pair 不进入均值或区间。
- 95% cluster bootstrap 的抽样单位是完整 pair（同一 pair 的两侧始终一起保留），对 pair difference 有放回抽样 2,000 次，固定种子 1729，并取 percentile 区间。它控制 pair 内相关性和结果可复现性；pair 为 0 时上下界为 `null`，小样本区间也不应当作显著性证明。

下列旧 `summary.json` 聚合指标用于兼容导出，分母另列如下。

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

`model_snapshot`、`model_snapshot_version` 与 `pipeline_version` 已写入每局 summary。v2 模型快照按配置列出 `count` 和升序 `seats`，可准确还原每个座位所用模型；`model_assignment_known=false` 表示旧档缺少完整座位分配，工具不会根据模型总数反推或伪造映射。所有模型快照均不包含 API Key。

## 质量门禁

- 所有 `app/` 内新增模块（`app/benchmark/`、`game_summary.py`、遥测路径）均纳入 pytest 覆盖率 100%（语句+分支）门禁。
- `scripts/` 不计入应用覆盖率分母；`tests/test_run_benchmark_cli.py` 用假的 HTTP 传输验证单配置/成对 payload、`--yes` 启动门禁、轮询和服务不可达错误，不发出真实模型请求。
- 遥测只记录元数据（计数、耗时、token 用量），不记录 prompt、模型原始输出或私有上下文，符合隐私边界约束。
