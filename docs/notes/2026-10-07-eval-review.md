# 最近两次评测复盘：结果口径与逻辑/流程问题清单（2026-10-07）

## 摘要（现在的问题是什么）

**一句话：代码把「模型答不出来」处理成了「玩家主动放弃」，而三个吸收层各自为政——同一场故障在游戏里丢掉一个决策、在耐久账本里是成功、在评测报表里变成另一个数字，事后还查不出原因。**

真正要修的不是“重试有没有做”（做了，3 轮都跑满了），而是下面四件事：

| # | 问题 | 现状证据（本批 30 局） | 影响 |
| --- | --- | --- | --- |
| 1 | ~~**重试只有“原样重发”那一半生效**~~ **已修（2026-10-07，见 [`plans/2026-10-07-unified-model-invocation.md`](../plans/2026-10-07-unified-model-invocation.md) P1）**：解析失败改抛 `UnusableModelOutput`，repair 分支从此可达；实测四套策略均归 `REPAIR` | 修复前：每次失败问了 3 轮同一句话，耗时 53–375s（成功 p95 32s） | 曾把可恢复的答复失败变成永久损失 |
| 2 | **失败 = 放弃**：异常被吞成 `pass` / `None` / 弃票；`per_game_limit=1` 的动作照记“已用” | 37 次丢决策（60% 的对局至少一次）：6 个整夜无狼刀、1 支猎枪作废、8 次女巫不动药、7 张警长票、1 张放逐票 | 直接改变对局结果（`ec41c649`、`d5afa6d1` 各为一例） |
| 3 | **警长票提示词与 schema 自相矛盾**：`pass` 也要求 `target_seat`，照着提示写就少一个字段 | 4/7 张警长票死于“JSON 少必填字段”，3 次成功的弃票都恰好写了 `"target_seat": null` | 一张警长票的生死取决于模型有没有多写一个 `null` |
| 4 | **评测口径读不出来**：判阵营/神职用角色名字符串、技术弃票按请求 status 统计、token 三路不回传 | 放逐命中狼 38.4%→**54.6%**；技术弃票报 7 次（真实 1 次）；token 覆盖率 42%；`retry_rate` 恒 0 | 报告的数字系统性错，且错的方向一致（低估能力、错配故障） |

**不是问题的部分**（避免过度悲观）：核心规则引擎是对的（40 次 `verify_kill` 全部合法、屠边/狼刀在先/发言顺序/加权票复投逐局复算一致）；10/6 那批修复有效（兜底不再泄露身份 0/1041、旁白死者门禁生效、石像鬼带刀有端到端测试）；触发故障的是单一模型（`gemini-3.8-flash-tiered` 37 次，`gemini-3.7-flash-high` 0 次），不是全局性故障。

**如果只做三件事**：①~~让解析失败归入 `repair_on`~~（**已完成**，见实施计划 P1）；②失败不再等于放弃 + 降级动作不计入 `action_counts` + 兜底文案中性化；③指标修正（`RoleSpec.tags` 判阵营/神职、按票种认 receipt 统计、三条路回传 usage）。验收门禁建议：**任何“模型失败”必须同时出现在 `model_errors.log`、请求状态、观众流三处，且不得被计成成功使用。**

---

复盘对象：数据库中**最新两次 benchmark 运行**。

| run | 时间 | 板子 | 局数 | 结果 |
| --- | --- | --- | --- | --- |
| `2fd3c69f` | 10/6 19:42 → 10/7 11:00 | 十二人纯白夜影（守卫/猎人/纯白之女/女巫/狼巫 + 3狼 + 4民，开警长，`witch_self_save=never`） | 30/30 完成 | 好人 53.3% / 狼 46.7% |
| `3dace6a2` | 10/6 16:33 → 19:50 | 十二人假面舞会（舞者/假面/白痴/预言家/女巫 + 3狼 + 4民） | 9 局有胜负，1 局 `benchmark_game_timeout` | 狼 6 / 好人 3（缓存报告仍写 8 局） |

两次运行**分别落在 10/6 21:04 那批修复的前后**：`3dace6a2` 全程在修复前（其中两局各有 102 / 120 次模型错误）；`2fd3c69f` 的 30 局全部在修复后（首局本地时间 10/7 07:44 建局，晚于 10/6 21:07 的最后一个修复提交），因此它**就是当前代码口径下的现行结论**，下文数据默认指这一批。文内引用的每一处代码位置都已按**当前工作区**（含尚未提交的改动）复核。

再往前是 10/2–10/3 的 `10c2974e`（十二人狼美人板，10 局，运行状态 `interrupted`，缓存报告只有 1 局有胜负，不可比）与 9/29 的两批（骑士板 `92e2f6f9` 9 局：好人 44.4%；狼王板 `eea407ba` 10 局：好人 80%）。这几批都是 10 局量级，只能当趋势参考，且板型与模型组合各不相同。

评测源数据：`backend/data/wolfkiller.sqlite3`（`benchmark_reports` / `model_requests` / `model_attempts` / `audience_events` / `game_checkpoints`）与各局 `backend/data/games/<id>/`。所有数字都可用文末附录的脚本复算。

---

## 一、最新一次评测的台面结果

| 指标 | 值 |
| --- | --- |
| 完成率 / 中断率 | 30/30，0 中断，报告 `provisional=false` |
| 平均轮数 / 平均时长 | 3.77 轮 / 26.7 分钟每局 |
| 阵营胜率 | 好人 16 胜（53.3%）/ 狼 14 胜（46.7%） |
| 胜因分布 | `all_wolves_dead` 16、`all_gods_dead` 6、`wolves_outnumber_good` 6、`all_villagers_dead` 2 |
| 逻辑请求 | 3859 次（约 129 次/局），success 90.7% / abstain 8.6% / failed 0.65% |
| 延迟 | p50 8.8s，p95 34.5s，p99 95.1s |
| token | 已知 6.99M（**仅覆盖 42.3% 的调用**，见 §2.3） |
| 决策质量（报告原生口径） | 放逐命中狼 38.4%、误杀神职 30.3%、好人票命中 46.7%、狼刀杀神 43.9%、毒药命中 50% |

**这批数字里，决策质量一节基本不可用**（§2.1），失败率与 token 一节系统性偏低（§2.2、§2.3）。可信的是阵营胜率、轮数、以及 37 次模型故障本身。

---

## 二、评测口径与埋点问题

### 2.1【严重】决策质量的阵营/神职分类与 `RoleSpec.tags` 不一致

`app/benchmark/metrics.py` 用**角色名字符串**判阵营与神职：

```python
_GOD_ROLE_TOKENS = ("seer", "witch", "hunter", "guard", "idiot")   # 子串匹配
_WEREWOLF_ROLES = frozenset({_WEREWOLF_ROLE, "wolf-killer-werewolf-king"})
```

权威来源是 `RoleSpec.tags`（`wolf-killer-pure-white` = `god`，`wolf-killer-wolf-witch` = `wolf`，见 `app/roles/pure_white.py:155`、`app/roles/wolf_witch.py:149`）。两者的实际差异：

| 角色 | tags | 报告 `_is_god` | 报告 `_is_wolf_role` |
| --- | --- | --- | --- |
| `wolf-killer-wolf-witch` 狼巫 | wolf | **True**（"witch" 子串） | **False** |
| `wolf-killer-pure-white` 纯白之女 | god | **False** | False |
| `wolf-killer-dancer` 舞者 / `gravekeeper` 守墓人 / `knight` 骑士 | god | **False** | False |
| `mask` 假面 / `gargoyle` 石像鬼 / `wolf-beauty` / `wolf-king` / `evil-knight` / `crimson-envoy` / `nightmare` | wolf | False | **False** |

恰好这次评测选中的板子（纯白夜影）两处都踩中：**狼巫被算成神职兼好人，纯白之女被算成平民**。用权威 tags 重算同一批 `summary.json`：

| 指标 | 报告值 | 用 tags 重算 | 偏差 |
| --- | --- | --- | --- |
| `exile_wolf_rate` 放逐命中狼 | 0.3838 | **0.5455** | 低估 16pp |
| `exile_god_misfire_rate` 误杀神职 | 0.3030 | **0.1919** | 高估 11pp |
| `good_vote_hit_rate` 好人票命中 | 0.4673 | **0.6404** | 低估 17pp |
| `wolf_kill_god_rate` 狼刀杀神 | 0.4393 | **0.6075** | 低估 17pp |
| `witch_poison_hit_rate` | 0.5 | **0.625** | 低估 |
| `good_votes` 样本 | 657 | 584（狼巫的 73 张票改判狼票） | — |

结论：**这块板子上的“决策质量”表把狼巫的每一次被放逐都记成“好人误杀神职”，把狼巫投出的每一票都记成“好人票”，同时看不见纯白之女**。同一段代码还会影响任何含舞者/守墓人/骑士/假面/石像鬼/狼美人的板型。修法是改成读 `RoleSpec.tags`（或 `summary.roles[seat].camp`），并给 `test_decision_metrics` 补一条“非预言家板型”的用例。

### 2.2【严重】37 次模型失败，只有 25 次被记为失败

本批 30 局共 37 条 `model_errors.log` 记录，**全部来自 `gemini-3.8-flash-tiered`（6 个座位），`gemini-3.7-flash-high` 0 次**；18/30 局至少中一次，12 局干净。

按契约：`werewolf_discussion` 12、`witch_action` 8、`sheriff_vote` 7、`werewolf_kill` 6、`hunter_shoot` 1、`pure_white_check` 1、`guard_action` 1、`wolf_witch_check` 1。

两类记录路径不一致：

- **25 条**（讨论/狼刀/警长票）走 `NightDirector` → `model_requests.status='failed'`，计进报告的 `failure_rate 0.65%`；
- **12 条**（女巫/守卫/纯白/狼巫，走流水线 `_command_provider`）被 `_command_provider` 的兜底返回（`game_service.py:2811`；同一文案的另一处在 `:2688`）兜底成 `pass`，请求落 `status='consumed'`，**报告、`summary.json`、API 全都不认为它失败过**。

证据（`ae5c3b02`）：`model_errors.log` 有 5 条（含女巫第 2、3 夜各一次），但该局 `summary.json` 的 `llm.errors=1`、`llm.fallbacks=0`；DB 里这两条请求 `status=consumed`、`normalized_result.action_type=pass`、`reasoning="系统异常，本轮未行动"`。

后果：评测的“模型可靠性”看不出真正的 37/3859 = 0.96%，而只有 0.65%；而**每次失败都吞掉一个玩家决策**（见 §3.1）。

### 2.3【严重】57.7% 的模型调用没有 token 记账

`model_attempts` 按调用路径分组：

| 路径 | 有 usage | 无 usage |
| --- | --- | --- |
| 夜间导演（`round:*:night:*`，`_night_turn`） | 1634 | 25（失败） |
| 白天发言（`durable_speak`，`game_service.py:2338-2340` 返回 `(..., None)`） | 0 | **1033** |
| 白天投票（`cast_vote`，同上壳层） | 0 | **835** |
| 夜间流水线动作（`_command_provider.durable_provider` 返回 `command.model_dump(), None`） | 0 | **332** |

只有 `_night_turn` 一路把 `_usage_mapping(turn.usage)` 交给账本。也就是说：**发言与投票——token 消耗最大的两条路——一次都没记过账**，报告的 `known_tokens 6.99M` 与 `completeness_rate 42.3%` 只是这一条路径的量。凡是以“已知 token 合计”做的成本对比或模型性价比结论都不成立。

### 2.4【中】`retry_rate` 结构性恒为 0，是一条死埋点

`_attempt_metrics` 用 `(game_id, request_id)` 分组数 attempts 行（`app/services/benchmark_metrics.py:401-407`），但所有重试都在**同一次逻辑请求内部**发生：`_night_turn` / `_command_provider` 的重试写在 `provider_call` 里（`game_service.py:364-410, 2745-2779`），重试次数只体现在 `_NightTurn.attempts`。本批 3859 个请求 0 个有第二行 attempts，`retried_requests=0`、`retry_rate=0.0` **在可预见的将来恒为 0**，而 10/6 刚上线的退避重试梯子（`app/agents/retry.py`）在这张报表上完全不可见。要么把内层尝试次数记进 attempt 行，要么把这个字段下线。

### 2.5【严重】“技术性弃票”有三种形态，报告数的恰好是没留痕的那一种

一个空票位上其实有三条互不相同的来路，本批 30 局的实际分布：

| 形态 | 代码路径 | 落库 | 观众流 | 本批次数 |
| --- | --- | --- | --- | --- |
| **放逐票技术弃票** | `BaseRole` 三段梯子（tool → JSON → 强格式）全部失败 | receipt `status=technical_abstain` + `technical_failure_code` | `TECHNICAL_ABSTAIN` 事件 ✓ | **1** |
| **警长票引擎故障** | 先走夜间 JSON 梯子（`_night_turn`，3 次**原样重发**），耗尽后 `SheriffDirector.vote_turn` 的 `except Exception: return None` 吞掉（`app/core/sheriff_flow.py:601-603`）——没有投票梯子的换问法重试 | 只有 `model_requests.status='failed'` | `SHERIFF_VOTE{target_seat:null}` | **7** |
| **警长票模型主动弃票** | 提示词与 schema 都提供 `pass`（`sheriff_flow.py:511-513`、`:585` 的 `_PASS`） | `consumed` + `action_type=pass` | 与上一行**完全一样** | **3** |

放逐票那条路本身设计得不错：类型化 `failure_code`、写入口强制「技术弃票必须带失败元数据」（`app/core/effect_applier.py:277,288`）、引擎据此发 `vote_technical_abstain:` 标签与 `TECHNICAL_ABSTAIN` 事件（`app/core/game_engine.py:2401-2430`）、票窗关闭时汇总 `accepted_votes / voluntary_abstains / technical_abstains / missing_voters`。唯一那次实例（`80620ede` 座位 8）在 `llm_calls.log` 里是三次 `action_payload_not_json`（34.5s + 36.6s + 37.7s），`summary.json` 与观众事件都记成 1 ✓。

问题全在警长票与报告口径：

- **警长票没有“投票梯子”的重试与留痕**：它走的是夜间 JSON 梯子（`_night_turn` + `_NIGHT_RETRY_POLICY`，3 次**原样重发**，本批 7 次失败的栈帧里都能看到 `retry.py:174 call_with_retry`），梯子耗尽后 `vote_turn` 把异常吞成 `None` —— 没有 receipt、没有 failure code、没有 `TECHNICAL_ABSTAIN`，三种形态在回放里长得一模一样（`SHERIFF_VOTE` 的载荷白名单只有 `voter_seat/target_seat/kind/round_number`，见 `app/services/audience_projector.py:34`）。
- **报告口径恰好只认最后一种留痕方式**：`technical_abstained = (action_type == 'technical_abstain') or (status=='failed' and 位置含 "vote")`。放逐票那条请求的载荷是 `{"command": {...}, "technical_failure_code": ...}`，而 `_result()` 取的是**外层**（`app/services/benchmark_metrics.py:257-267`），于是 `action_type` 永远读不到、`status='consumed'` 还被归类为 successful —— 真正的那 1 次不计；分子只剩警长票失败 = **7**。
- **分母混了两种票**：`terminal_votes = 1092`（835 放逐 + 257 警长票）。若按放逐票单独算，本批技术弃票率是 1/835 = **0.12%**，而报告写 0.64%。
- **历史对照更能说明问题**：上一批 `3dace6a2` 真实技术弃票 **251 次**（全部 `provider_connection_error`，其中 `775f0e4f` 一局 177 次），报告只写 **4 次** —— 低估 60 倍；那批的 `summary.json` 与观众事件数（40 / 34）是一致的，唯独报告对不上。

**根因分解：触发在模型，落地在代码。** 本批 37 次硬失败全部落在 `gemini-3.8-flash-tiered`（`gemini-3.7-flash-high` 0 次），而同一套代码与提示词下 3.8 另有 64 次“内容里捡回 JSON”、6 次直接不认工具 —— 说明这个模型习惯**不调工具、把答案写在 content 里**，能不能救回来取决于代码，而不是取决于它答得对不对。按 `model_errors.log` 的最内层栈帧分类：

| 失败形态 | 次数 | 含义 |
| --- | --- | --- |
| 最内层 `_structured_response` | 33 | 答复里既没有原生 tool call，content 里也没有可解析的 JSON |
| 最内层 `_require_schema_fields` | 4 | **JSON 解析成功了，但少一个必填字段**（`1b25cde7` 座位 5/7/10、`2ed0bbfe` 座位 11，全是警长票） |

第二种尤其值得注意：警长票的 schema 是 `required = ["action_type", "reasoning", "target_seat"]`（`sheriff_flow.py:300-314` + `:594-597`），**连 `action_type=pass` 也要求写 `target_seat`**；而给模型的人话提示写的是「输出 action_type=vote 且 target_seat 为候选座位，**或 action_type=pass**」（`sheriff_flow.py:513`）——照着提示词写就会漏掉 `target_seat`。旁证：本批 3 次成功的主动弃票载荷都老老实实带 `"target_seat": null`（250 张有效警长票的键集完全一致），也就是说**一张警长票是生是死，取决于模型有没有多写一个 `null`**。到底缺的是哪个字段已经无法证实——`_require_schema_fields` 的异常原文（“omitted required fields: …”）被 §2.10 的诊断格式化丢掉了，这是那条缺陷的直接代价。

修法：报告按**票种**分开统计，并以 receipt 状态 / `technical_failure_code` 判定（`_result()` 需能读 `command` 内层，或让票类请求把 `action_type` 提到外层）；警长票要么补上与放逐票同等的重试与留痕，要么明确「警长票允许弃票」并把它作为自愿弃票发布——现在是既没有投票梯子的换问法重试与留痕、也不做区分。另外两条独立的代码侧修法：提示词与 schema 对齐（`pass` 不应要求 `target_seat`，或提示词明写「pass 时 target_seat 写 null」），以及让这类解析失败走 repair 重试（§2.11）。

### 2.6【中】`abstain_rate 8.63%` 主要是正常游戏行为

333 条 `pass` 的构成：`sheriff_campaign` 不上警 257 条（模型自主决定，符合规则）、流水线 `pass` 61 条（角色合法放弃）、**引擎兜底 12 条**、警长票 pass 3 条。把它命名为“弃权率”并与失败率并列展示，会让读者以为模型大量拒答。

### 2.7【中】单 block 的 mixed_arena 报告里所有置信区间都是 `null`

`_bootstrap_block_ratio`（`benchmark_metrics.py:602-606`）在 `len(blocks) < 2` 时直接返回 `(None, None)`。本次运行 `block_count=1`，于是 `uncertainty.mixed_arena` 的每一条都是 `win_rate` 有值、`low/high` 为 `null`，但同一行仍写着 `samples: 2000`、`confidence: 0.95`。对读者而言“30 局、p=0.533”被呈现成一个带 95% 区间的点估计，实际上**没有任何区间**（Wilson 区间下 30 局的半数胜率约 ±18pp）。要么对单 block 回退到二项区间，要么把 `samples/confidence` 留空。

### 2.8【中】`model_performance` 不是模型 A/B

同一局里两个模型各坐 6 席，但 `_model_performance` 把「模型 × 阵营/角色」的每个座位当独立样本（`06211189` 的好人样本 118 来自同一批 30 局）。表里 55.9% vs 50.8% 的差距几乎是同一批对局的两个切片，不能读成模型强弱；真正的模型对比需要 paired/mixed 设计（run 里已有 `mixed_uncertainty` 的 block 概念，但这次只有 1 个 block）。

### 2.9【中】评测报告缓存无界增长，且旧行会被误读成现状

`benchmark_reports` 共 **9398 行 / 149 MB**，占 376 MB 数据库的约 41%；单是 `2fd3c69f` 就有 **3404 行**（报告按 `(run_id, metric_version, input_digest)` 缓存，事实每变一次就写一行新的，无人清理）。由此产生一个真实误导：`3dace6a2` 的最新缓存行写的是「完成 8 局、好人 37.5%」，而库内事实是「9 局有胜负、好人 33.3%」——最后一次缓存生成于 19:46:47，而末局 19:50 才结束。服务在读报告时会按新 digest 重算，所以页面上是对的，但**任何按 `generated_at DESC` 取行的消费者（含本次复盘的第一版）都会读到过期结论**。建议保留每 run 最新 1–2 行或加 TTL。

### 2.10【低】诊断信息把异常原文丢了

`_exception_diagnostics`（`game_service.py:208-232`）在错误没有 OpenAI 风格 `.body` 时写死 `f"{type(error).__name__}: model invocation failed"`。本批 37 条失败**全部**是解析层抛的 `ValueError`（33 条停在 `_structured_response`＝“没有 tool call 也没有 JSON / tool 数不对 / 参数不是对象”，4 条停在 `_require_schema_fields`＝少必填字段），但日志里 37 条写的是同一句泛化文案 —— 事后**无法区分**是网关返回空、模型回错工具还是 schema 不符。至少应回落到 `str(error)`。这类异常还被重试梯子当成 transport 处理（§2.11）。**（2026-10-07 已修：诊断回落到 `str(error)`，`_model_failure_code` 优先取解析层 token。）**

### 2.11【中】“答非所问”拿不到 repair 提示，重试梯子对最常见的失败形态等于原样重发

`app/agents/retry.py:210-223` 把重试分成两档：`KIND_TRANSPORT`（“没拿到答案”，原样重发）与 `KIND_REPAIR`（“答了但不可用”，附格式提醒重问）。分档只看异常类型：`RETRYABLE_OUTPUT_ERRORS = (UnusableModelOutput, ValidationError)`。

但解析失败抛的是**裸 `ValueError`**：`_structured_response`（`app/agents/llm_client.py:499-545`）的「既没有 tool call 也没有 JSON」「tool 调用数不为 1」「tool 名不对」「参数不是对象」，以及 `_require_schema_fields`（同文件 548-562）的「少必填字段」。实测四套策略的分类：

| 异常 | `_NIGHT` | `_PIPELINE` | `_SPEECH` | `_VOTE` |
| --- | --- | --- | --- | --- |
| 裸 `ValueError`（上面四种） | transport | transport | transport | STOP（该策略本身不重试） |
| `UnusableModelOutput` | repair | repair | repair | repair |
| pydantic `ValidationError` | repair | repair | repair | repair |

注：白天投票的重试**不由 `retry_plan` 决定**，`_VOTE_RETRY_POLICY` 只提供退避时长（`_backoff_policy` → `delay_for`）；投票梯子的重试与否写在 `_request_action_with_transport` 的各个 `except` 分支里，且 `ToolCallError` 是 `ActionValidationError` 的子类（`app/agents/output_parser.py:26`），所以放逐票的解析失败会走“换问法重试”那一支。

于是「模型答了但格式不可用」——本批 37 次失败全部是这一类（33 次停在 `_structured_response`、4 次停在 `_require_schema_fields`，37/37 `exception_type=ValueError`）——每一次都按“没拿到答案”原样重发，**附格式提醒的 repair 分支一次都没跑**。10/6 那条“空/畸形答复也有第二次机会”的修复，对最常见的畸形形态实际是死代码；投票路径更是直接 STOP（`_VOTE_RETRY_POLICY` 未开 `retry_unknown_errors`）。修法：让 `_structured_response`/`_require_schema_fields` 抛 `UnusableModelOutput`，或把裸 `ValueError` 归入 `repair_on`。**（2026-10-07 已按前者落地：解析失败带 `response_no_tool_call` / `response_missing_fields` 等 token，`classify_failure` 归 `REPAIR`；见实施计划 P1。）**

**先澄清一个容易被误读的点：重试是做了的。** 37 次失败的栈帧显示它们各自走完了所在路径的整条梯子才放弃：

| 路径 | 梯子 | 覆盖的失败 | 栈帧特征 | 重试语义 |
| --- | --- | --- | --- | --- |
| 夜间 JSON（狼队讨论/狼刀/警长票/交徽…） | `_night_turn` + `call_with_retry(_NIGHT_RETRY_POLICY)`，attempts=3 | 25 次（discussion 12 / kill 6 / sheriff_vote 7） | `retry.py:174 call_with_retry` → `game_service.py:373 _night_turn` → `llm_client.py:517 _structured_response` | 3 次**原样重发** |
| 夜间流水线（女巫/守卫/纯白/狼巫/猎枪） | `_command_provider` 内联 `while` + `retry_plan(_PIPELINE_RETRY_POLICY)`，attempts=3 | 12 次 | `game_service.py:2709 provider` → `llm_client.py:567 invoke_action` | 3 次**原样重发** |
| 白天投票（放逐票） | `_request_action_with_transport` 手写 3 段（tool → JSON → 强格式） | 1 次（`80620ede` 座位 8） | 逐次记进 `llm_calls.log`（`attempt 1/2/3`） | 第 2、3 次**换问法**（`build_vote_retry_messages`） |
| 白天发言 | `_SPEECH_RETRY_POLICY`，attempts=4 | 1 次落模板 | `llm_calls.log` 可见 | 带 repair 提示 |

另有 SDK 自身 `LLM_PROVIDER_MAX_RETRIES=4` 的 429/5xx 重试垫在最底层。也就是说：**每次失败实际问了 3 轮（每轮内部还可能 tool→JSON 各一次），耗时 53s–375s（成功调用的 p95 是 32s）——不是没重试，而是三轮都在问同一句话。** 唯一真的换了问法的那条（放逐票）试了三次仍然拿不到 JSON，那一次确实属于模型侧。

顺带一个埋点后果：夜间与流水线这两条路只在**终局**写一行 `llm_calls.log`，`attempt` 恒为 1、`retried` 恒为 false（`_log_night_llm_telemetry` 不传这两个字段），所以从日志上看**像是完全没重试**——本批全量 3534 条调用里只有 12 条 `attempt≥2`，全部来自白天发言/投票。这既是我前一轮判断失误的来源，也是 §2.4 那条“重试不可见”的实证。

### 2.12【低】评测板型单一、且没有预言家

30 局全部是同一块板、同一对模型、同一 `seed=42`，`scenario` 里也没有换板。该板的神职是纯白之女与狼巫，于是报告的 `seer_check_hit_rate` 是 `null`（样本 0）——**这块板的两大信息角色一个指标都没有**（`compute_decision_samples` 只统计 `SEER_CHECK` / `WITCH_POISON` / `HUNTER_SHOT` / `WEREWOLF_KILL`）。石像鬼、守墓人、舞者/假面、噩梦之影同样没有任何决策指标。

---

## 三、对局内逻辑/流程问题（本批 30 局实证）

### 3.1【严重】模型故障被当成玩家“主动放弃”，一次性技能被静默作废

10/6 审计的 A3 只修了一半：兜底文案仍在，且**降级动作照旧计成“成功使用”**。

- `ec41c649` 座位 4（猎人）第 2 夜被狼刀出局（死因在 `_SHOOT_REASONS` 内，本可开枪），`hunter_shoot` 模型调用失败 →
  - 终局 `pipeline_runtime.role_resources == {"4": {"gun": 1}}`：**枪根本没消耗**；
  - 但 `action_counts["4\0hunter_shoot"] == 1`：审计上记成“已使用一次”（对 `per_game_limit=1` 的契约就是静默作废）；
  - 观众流公开事件 `HUNTER_REASONING`：`{"action_type":"pass","reasoning":"系统异常，本轮未行动","thought":"决定不开枪：系统异常，本轮未行动"}` —— 读起来完全像猎人的自主决定；
  - 该局 1 轮后以 `all_gods_dead` 狼胜结束。
- **6 个夜晚整夜没有狼刀**，与 6 次 `werewolf_kill` 失败一一对应（`35b0e778`、`d5afa6d1`×2、`1b25cde7`、`a101c8ad`、`66d7f9ce`）。`d5afa6d1` 第 3、4 夜连丢两刀，最终第 5 轮被好人 `all_wolves_dead` 清盘 —— 这是**极可能改变胜负**的一类故障。
- 同类损失还有：女巫 8 次（`cefbd2ea`/`b5ad1050`/`ae5c3b02`/`43dc3bcb`/`5cf2de15`：药在手上却整夜不动）、守卫 1 次、纯白/狼巫查验各 1 次、警长票 7 次（静默弃权，直接改变警徽归属）。

建议：`per_game_limit=1`、`actions_requiring_target` 非空、死亡响应窗口这类契约在 provider 异常时**不要降级为 fallback**，改为可恢复暂停或重新排队；兜底理由改成中性占位（不再写“系统异常”）；`action_counts` 只在动作真正执行时自增。

### 3.2【严重】引擎内部文案进入公开流与后续提示词

`系统异常，本轮未行动` / `系统异常，代投弃权` 这两个字符串（`game_service.py:2688,2811`、`night_flow.py:532`、`scheduler.py:395`、`roles/base.py:889,922`）在本批产生：

- **19 条观众事件**（13 条 `night_thought` + 6 条 `wolf_vote`）直接把引擎故障当成玩家的“内心理由”播给上帝视角，例如 `{"action_type":"witch_reasoning","reasoning":"系统异常，本轮未行动","seat":8}`；
- **15 条进入玩家提示词**：5 局里失败座位自己的历史思考被写成 `<past_thought>决定今晚不使用药水：系统异常，本轮未行动</past_thought>`，模型下一轮读到的“自己的记忆”是引擎报错；
- 投票兜底还带 `{"command":{"action_type":"abstain","reasoning":"系统异常，代投弃权"}}`。

### 3.3【高】开眼旁白按“调度点”命名，而不是按角色

本批 113 个夜晚里播了 **93 次「预言家请睁眼」**——而这块板**没有预言家**，在 `NIGHT_SEER_ACTION` 上行动的是纯白之女与狼巫，两者都没有 `opening_narration`（`app/roles/pure_white.py`、`wolf_witch.py` 均未声明）。

机制：`_night_openers(NIGHT_SEER_ACTION)`（`game_engine.py:771`）只判「注册表里在该点有契约 且 存活 且 未被 silenced」，通过后就取 `night_flow._NARRATIONS["seer_open"]`（`night_flow.py:161`，硬编码「预言家请睁眼」）。门禁是对的（守卫 96 / 女巫 81 次＜113 说明死者不再被叫醒，10/6 的 B1 已修），**错的是“谁在这个点开眼”被写死成了预言家的台词**。任何未来把契约挂到 `NIGHT_SEER_ACTION` 的角色都会继承这句错台词。

### 3.4【中】加权票型进不了观众流，公开票型与放逐结果可自相矛盾

`VOTE_RESULT` 的有效载荷已含 `weighted_counts`（`game_service.py:1872`，并有 `tests/test_vote_result_projection.py` 把关），但投影器白名单没有放行它：

```python
# app/services/audience_projector.py:24
"VOTE_RESULT": ("vote_result", frozenset({"round_number", "exiled_seat", "counts"})),
```

`project_events` 按白名单裁字段（`audience_projector.py:124-126`），所以观众事件、回放 DTO、前端都只拿到人头数。本批 101 次放逐里有 **2 次人头数显示为平票却已产生放逐**：

- `ae5c3b02` 第 1 轮 `counts {2:6, 3:6}`，放逐 2 号 —— 警长（3 号）的票按 3:2 计权后是 13:12；
- `ec41c649` 第 3 轮 `counts {3:3, 8:3}`，放逐 8 号 —— 警徽已移交 2 号，加权后 7:6。

测试只断言了领域事件（`_checkpoint_domain_events`）里的字段，投影层漏改不会被门禁抓住。

### 3.5【中】警长票失败在观众流不可见

`TECHNICAL_ABSTAIN` 观众事件只由 `vote_technical_abstain:` 标签产生（`game_service.py:1985`），而这条提交只出现在放逐投票路径（`roles/base.py` 的投票兜底）；警长票走 `sheriff_flow`，失败只写 `model_errors.log`。于是同一类“模型没答上来的票”，放逐票会被播报、警长票不会，而报告口径又把警长票算成技术弃票（§2.5）。

### 3.6【观察·平衡】纯白之女在技能生效的第一夜就阵亡（22/30）

40 次 `verify_kill` 全部合法（受害者要么是狼、要么是纯白之女；0 违规），机制实现与 `docs/reference/netease-role-skills.md` 一致。但本批的分布很极端：

- **22/30 局**：纯白之女在第 2 夜（技能解锁的第一夜）被狼巫点杀；
- **16/30 局**：同一夜也有狼被纯白之女处决（其中 15 局与上一条重叠，即标准的同夜一换一）；
- 狼巫能稳定认出她，是因为纯白之女的查验结果是**具体身份**，白天报出“某号是猎人/村民”就等于自曝；而狼巫掌握狼队名单，只需在全场找那个能报底牌的人。

这不是 bug，但意味着这块板的好人信息引擎几乎必然在第二夜熄火，是个值得产品/板型层面决策的平衡点（例如让纯白之女可报“好人/狼人”之外的粒度，或调整狼巫处决的第二夜门槛）。

### 3.7 上一批修复的复核结论（正面）

- **A1 兜底泄露角色名**：已修。1041 条公开发言里只 1 条是兜底模板（`roles/base.py:411-447` 已不带 `display_name`），全库 30 局无角色名泄露。
- **A2 石像鬼独自带刀**：已修且有走真实 `_command_provider` 的回归测试（`tests/test_hidden_wolf_kill_path.py:133,188`）；本批板子无石像鬼，未取得真局证据。
- **平票复投收窄为并列候选人**、**「连续两晚」按回合比较**、**开眼旁白的死者门禁**都在代码与数据上成立。

---

## 四、跨层不统一清单（同一件事，每一层给出不同答案）

上面各节是症状。把它们按“层”重排，会发现共同结构：**每一层都在自己那一层就地兜底，但没有任何一份跨层的“失败契约”**——谁负责重试、谁负责记录、谁负责公开、谁负责计数，每处各写一遍。本项目在别的地方已经做过这种统一（`classify_provider_error` 是唯一的失败码词表、`PROTECTION_SOURCES` 是唯一的保护来源清单、`values.py` 是唯一的取值目录），唯独“失败处理”没有。

### 4.1 一次失败的落库形状，取决于谁接住它

同一场“模型答不出来”，在本批 30 局里产生了**五种互不相等的计数**：

| 消费者 | 数据源 | 口径 | 本批总数 |
| --- | --- | --- | --- |
| `model_errors.log` | 各调用点手写 | 最终失败 | **37** |
| `summary.json` 的 `llm.errors` | `llm_calls.log` 里 `parse_result=="error"` 或 `failure_code` 非空 | 只有白天路径 + 夜间导演终局行 | **27** |
| `model_requests.status='failed'` | 耐久账本（只有**异常逃逸**的路径） | 25（讨论 12 + 狼刀 6 + 警长票 7） | **25** |
| 报告 `technical_abstained` | failed 且位置含 "vote" | 只剩警长票 | **7** |
| `audience_events.TECHNICAL_ABSTAIN` | 只由 `contract_id == "exile_vote"` 触发 | 只剩放逐票 | **1** |

四个吸收层各把一个例外变成不同东西：`roles/base.py` 的投票梯子→**技术弃票**（有类型化原因、有 receipt）；`roles/base.py` 的发言梯子→**模板发言**（请求仍算成功）；`game_service._command_provider`→**`pass` + 「系统异常，本轮未行动」**（请求算 consumed）；`_night_turn` 与各 director→**异常逃逸**（请求才落 `failed`）。`model_requests.status` 这一列因此语义漂移：在账本里它表示“结果是否被游戏消费”，在报表里被判成 success，而在夜间路径里它恰好表示“失败”。

### 4.2 失败时“玩家做什么”，六处各写一个默认值

| 场景 | 默认行为 | 位置 | 观众可见性 |
| --- | --- | --- | --- |
| 放逐票失败 | 弃票（带 `failure_code`） | `roles/base.py:830-930` | `TECHNICAL_ABSTAIN` ✓ |
| 警长票失败 | **沉默弃票** | `sheriff_flow.py:601-603` | 与“模型主动 pass”同形 |
| 警长发言方向失败 | 取 `sides[0]` | `sheriff_flow.py:614-616` | 无 |
| 交徽失败 | **直接撕徽**（1.5 票资产作废） | `sheriff_flow.py:642-644` | 只有 `sheriff_badge` 无 reasoning |
| 夜间流水线失败 | `pass` + 引擎文案 | `game_service.py:2811` | 上帝视角能看到假理由 |
| 夜间导演失败（讨论/狼刀） | 该行动**整个消失** | 请求 `failed` + 兜底票 | 无（狼刀消失只表现为“平安夜”） |
| 白天发言失败 | 模板发言 | `roles/base.py:411-447` | 看不出是模板 |

规则上“玩家失败后应该怎样”本身就没有定义，于是每处凭感觉写了一个——其中“撕徽”与“整刀消失”的代价远大于其他。

### 4.3 重试的判定依据在“异常类型”与“调用方”之间分裂

`retry_plan` 按**异常类型**分档（`repair_on` 只认 `UnusableModelOutput`/`ValidationError`），但真正需要换问法的解析失败抛的是裸 `ValueError` → 落到 transport（原样重发）。而各调用点又各有自己的判定：

| 路径 | 谁决定重试 | 对“答了但不可用”的处理 |
| --- | --- | --- |
| 夜间 JSON | `call_with_retry` + `retry_plan` | 3 次原样重发 |
| 夜间流水线 | `_command_provider` 内联循环 + `retry_plan` | 3 次原样重发 |
| 白天发言 | `_SPEECH_RETRY_POLICY` + 自己的 `_speech_failure` 判定 | 4 次，带 repair 提示 |
| 白天放逐票 | 手写三段 `except` 分支（`_VOTE_RETRY_POLICY` 只管退避） | 第 2/3 次**换问法** |
| 警长票 | 复用夜间梯子 | 3 次原样重发，且不留痕 |

同一个错误在白天投票会换问法、在夜间不会——差异不在错误，而在调用点。

### 4.4 同一事实的“记录”与“投影”不一致

| 事实 | 是否在领域事件 | 是否在旧档白名单 | 是否在观众投影 | 结果 |
| --- | --- | --- | --- | --- |
| `weighted_counts` | ✓ | ✓（`game_routes.py:112`） | **✗**（`audience_projector.py:24`） | 前端只看到人头票 |
| 警长票的失败原因 | ✗（无此事件） | ✗ | ✗ | 三种空票同形 |
| 技术弃票 | ✓ | ✓ | ✓ | 只有放逐票有 |

投影器按白名单裁字段（`audience_projector.py:124-126`），却没有任何“投影字段 ⊆ 领域字段”的门禁；测试只断言了领域事件（`tests/test_vote_result_projection.py`），所以漏改不会被发现——这正是 CLAUDE.md 里“改 `audience_projector._EVENTS` 要同步四处”那条规矩没有对应的自动门禁。

### 4.5 同一个概念两处存储，其中一处是死的

- `runtime.role_resources[seat]["gun"]` 是真实资源；`PlayerState.has_gun` 是镜像——**`app/` 里没有任何一处把它写成 `True`**（只有编解码器往返和测试手写）。真实读取走 `role_resource_view()`，只有查不到时才回落到这个恒为 `False` 的字段（`context_projector.py:528-543`）、`memory_service.py:71` 也拿它兜底。本批两者恰好一致（`runtime` 一直有值），属**潜伏**缺陷。
- `action_counts` 记“合同被触发”（降级也算用掉），`role_resources` 记真实消耗——同一个动作在两个字段里给出互相矛盾的答案（§3.1 的猎人枪）。

### 4.6 顺带的两个“同类不同写法”

- 判阵营/神职：报告用角色名子串（`benchmark/metrics.py:15-27`），权威是 `RoleSpec.tags`；同一概念两处定义且不一致（§2.1）。
- 请求载荷：同为票类请求，放逐票把命令包在 `{"command": {...}}` 里、警长票直接在顶层；`_result()` 只按外层读（`benchmark_metrics.py:257-267`），于是放逐票的 `action_type` 永远读不到（§2.5）。

---

## 五、修复优先级建议

| 级别 | 事项 | 落地位置 |
| --- | --- | --- |
| P0 | **立一份跨层失败契约**：提供方只抛四类（瞬时传输/输出不可用/能力拒绝/结构性），调用点不得自行吞异常；账本状态统一为 `succeeded` / `degraded(reason)` / `failed`，日志、观众流、报表都从它派生 | `agents/retry.py`、`roles/base.py:790-930`、`game_service.py:2811`、`sheriff_flow.py:601-603,642-644` |
| P0 | 门禁：失败必须三处同时可见（`model_errors.log` + 请求状态 + 观众流），降级动作不得计入 `action_counts` | 新增测试 + `scheduler.py`/`game_engine.py` |
| P0 | 决策指标改用 `RoleSpec.tags` / `summary.roles[].camp` 判阵营与神职，并补非预言家板型用例 | `app/benchmark/metrics.py:15-27` |
| P0 | 模型故障不再等于“主动放弃”：一次性/强制契约异常改可恢复暂停；兜底理由去内部文案；`action_counts` 只在真正执行时计数 | `game_service.py:2688,2811`、`scheduler.py:395`、`night_flow.py:532`、`roles/base.py:889,922` |
| P0 | 发言/投票/流水线三条路回传 token usage | `game_service.py:2338-2340`（发言/投票）、`2841-2843`（夜间流水线） |
| P0 | `weighted_counts` 进观众投影白名单，并加一层“投影字段集 ⊆ 领域字段集”的门禁 | `app/services/audience_projector.py:24` |
| P1 | 开眼旁白改为“按调度点找角色、用角色自己的台词”，不再写死预言家 | `game_engine.py:713`、`night_flow.py:161` |
| P1 | 失败分类统一：流水线兜底也落 `failed`（或专门的 `degraded` 状态） | `game_service.py:2805-2814` |
| P1 | 技术弃票按票种统计、认 receipt/`technical_failure_code` 而不是请求 status；警长票补重试与留痕（或明确允许弃票并单独发布） | `benchmark_metrics.py:257-267,300-341`、`sheriff_flow.py:511-513,581-603`、`game_engine.py:1140-1183` |
| P1 | 让解析失败抛 `UnusableModelOutput`（或把裸 `ValueError` 归入 `repair_on`），使 repair 分支真正生效 | `app/agents/llm_client.py:499-562`、`app/agents/retry.py:70-73,210-248` |
| P1 | 诊断保留 `str(error)`；`retry_rate` 要么记内层尝试次数要么下线；`benchmark_reports` 加保留策略 | `game_service.py:208-232`、`benchmark_metrics.py:401`、`benchmark_service.py:485-495` |
| P2 | 单 block 不再报 2000 次 bootstrap；`model_performance` 标注“非模型 A/B” | `benchmark_metrics.py:602-616`、`_model_performance` |
| P2 | 决策指标补纯白/狼巫/石像鬼/守墓人/舞者等角色的查验与技能命中率 | `app/benchmark/metrics.py` |

---

## 附：复现材料

```bash
# 事实库只读副本（原库在 WSL 上直接只读打开会 I/O error）
cp backend/data/wolfkiller.sqlite3 /tmp/wk/wk.sqlite3

# 需要的解释器（含 langchain 依赖）
/home/jm050711/.venvs/wk/bin/python
```

关键脚本（均已实际执行）：`/tmp/wk/extract.py`（逐局汇总表）、`/tmp/wk/latest_report.json`（最新 v3 报告原文）。核心复算的最小可重跑片段：

```python
# 1) 决策指标重算（§2.1）：把分类换成 RoleSpec.tags 后重跑同一批 summary.json
import sys, json, glob; sys.path.insert(0, "backend")
from app.benchmark import metrics as M
from app.roles.registry import builtin_registry
snap = builtin_registry.freeze()
M._is_god       = lambda r: r in {i for i, s in snap.specs.items() if "god"  in (s.tags or ())}
M._is_wolf_role = lambda r: r in {i for i, s in snap.specs.items() if "wolf" in (s.tags or ())}
summaries = [json.load(open(p)) for p in glob.glob("backend/data/games/*/summary.json")]  # 仅取本 run 的 30 个目录
print(M.compute_decision_metrics(summaries))   # 与报告里的 summary.decision 对比

# 2) token 记账缺口（§2.3）
#    SELECT a.usage_known, CASE WHEN r.action_position LIKE 'round:%:night:%'  THEN 'night-director'
#                               WHEN r.action_position LIKE 'round:%:speech:%' THEN 'day-speech'
#                               WHEN r.action_position LIKE '%:vote_casting:%' THEN 'day-vote'
#                               ELSE 'pipeline' END, COUNT(*)
#    FROM model_attempts a JOIN model_requests r USING (game_id, request_id) GROUP BY 1, 2;
```

`model_requests` 的 `request_id` **不是全局唯一**（跨局复用同一摘要值），任何 `JOIN ... USING (request_id)` 都必须同时带上 `game_id`，否则 attempts 会被交叉放大。
