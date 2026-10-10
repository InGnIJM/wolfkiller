# 修掉「已知未修」与评测口径残留（十项）

日期：2026-10-10 · 范围：`backend/app/core/{game_engine,night_flow,scheduler}.py`、
`backend/app/services/{audience_projector,memory_service,game_summary,benchmark_metrics}.py`、
`backend/app/roles/{dancer,dream_weaver,mandatory}.py`、`backend/app/benchmark/metrics.py`、
`backend/app/models/game.py`、`backend/app/core/context_projector.py`、前端 benchmark 详情页

起因是盘点「上一轮还有哪些没修」：五条对局内缺陷（`docs/notes/2026-10-10-game-review-five-fixes.md`
的相邻项与更早的复盘结论）加四项评测口径残留，另加一项口径可见化。每项按「症状 / 根因 /
修法 / 门禁」记录，门禁都是**改前为红**的。

---

## A1 竞选期自爆的死亡不进 `night_deaths`

**症状**：`5cf2de15` r1、`6dbbea17` r1、`a3ac6cb9` r1 的竞选期自爆在观众流里有
`sheriff_explode:` 死亡事件，但 `summary.json` 的 `rounds.*.deaths` 与 `/logs` 文件回放都
看不到这一死。

**根因**：`_election_explode` 只 `mark_dead` → `publish(PLAYER_DIED)` → checkpoint，从不调
`log_deaths`；白天自爆与延迟死亡走 `_publish_deaths` → `log_deaths`，只有竞选这一支漏了。

**修法**：在 `sheriff_explode:` 检查点**之前**补一条 `night_deaths` 批次（崩溃不丢；重放与
夜死批次同一容忍度 —— summary 按 `(seat, cause, round)` 去重，回放分支已认 `self_explode`
死因），并补 `_reveal_on_death`（明牌局里 `_publish_deaths` 对每一死都公布身份）。

**门禁**：`tests/test_sheriff_engine.py` 走真实竞选循环，再把写出的 `game.log` 喂给真实
`build_game_summary`；`test_an_explode_still_reads_the_night_out` 改为断言两条批次。

---

## A2 狼人那一拍承诺了一场不会发生的讨论

**症状**：独狼（例如只剩石像鬼带刀）或被封印的夜里，观众听到「狼人请睁眼 / 开始讨论今晚的
行动」，之后没有任何狼聊。

**根因**：stage 2 无条件播 `wolf_open`，而讨论只在 stage 3 的 `len(wolves) > 1` 分支里发生。

**修法**：`NightDirector.wolf_open_narration(discusses=...)`（讨论态保持原文，无讨论时说
「请选择今晚的行动」）；引擎把狼窝推导与 `_pack_sealed` 判定上移到 stage 2 之前 —— 仍在
stage 1 之后，「第一个调度点跑完才能读 `no_wolf_pack`」这条不变量不变。

**门禁**：`tests/test_night_flow.py` 两句原文；`tests/test_game_engine.py` 走真实
`_execute_staged_night` 的三态（两狼 / 独狼 / 被封印）。

---

## A3 竞选窗口内的观众快照泄露首夜死讯

**症状**：官方顺序是「先让警徽落地、再念死讯」，事件时间轴是对的，但 `GET /games/{id}/snapshot`
在竞选期间就把首夜死者标成已死、并把他的死亡行放进 `death_history`。

**根因**：`AudienceProjector.snapshot` 直接读真实 `state`。

**修法**：按 `unannounced_death_seats(state)` 投影 —— `is_alive` 用 public 口径，
`death_history` 过滤未公布行。窗口外该集合恒空，其它阶段零影响；竞选期自爆属
`_ANNOUNCED_IMMEDIATELY`，照旧如实显示。

**不改 `GameState.get_public_state()`**：它喂 `_public_state_digest`（进 `game_commits.result_digest`
与合成提交的 CAS），改它需要单独的兼容评审。

**门禁**：`tests/test_audience_projector.py` 三例（未公布隐藏 / 公布后如实 / 当场公布的死因
不被复活）。

---

## A4 `has_antidote` / `has_poison` / `has_gun` 是死镜像

**症状**：`PlayerState` 的三个资源位在生产代码里**只读不写**（最后一次写入在 `4cc79ac` 被删），
两处回落（`ContextProjector._actor_resources`、`MemoryService._build_private_knowledge`）会读它们。

**根因**：资源统一走 `runtime.role_resources` 之后，这三个字段只剩「编解码器往返 + 旧档兼容
回落」；一个永不更新的字段会把「还没有 runtime」悄悄读成「没有枪/没有药」。

**修法**：删掉 `_RESOURCE_ADAPTERS` 回落 —— canonical 缺键时读**声明值**（与引擎在下一个
调度点按声明建立资源的行为一致），memory 同口径。**字段本身必须留着**：`checkpoint_codec._exact`
对玩家行做**对称**精确字段集校验，删字段会让所有现存检查点 `CheckpointError`（不是可确认的
`drift`）。

**门禁**：`tests/test_player_resource_mirrors.py` 用 AST 扫描断言 `app/**` 不存在这三个字段的
属性赋值或 `setattr`（实测注入一行即红），并钉住它们仍在 `_PLAYER_FIELDS` 里；两条把回落当
契约的用例改为断言新口径，三条手工构造状态的用例改为把资源写进 canonical 视图（与生产同路径）。

---

## A5 舞者/摄梦人的「不可放弃」没有机器强制

**症状**：两个角色的规则文本写着「每晚必须…不可放弃」，但模型主动 `pass` 被当成合法决定，
与引擎兜底无从区分。

**根因**：`pass` 同时是合法 `action_type` 与 `fallback_action_type`（注册表要求 fallback 属于
动作集合、且不能要求目标，而这两个契约的真实动作是多目标/需目标），validator 层没有任何东西拦它。

**修法**：`roles/mandatory.py::engine_only_fallback_violation` —— 模型自己答的 fallback 动作判违规
（`dance_mandatory` / `dream_mandatory`），引擎兜底放行。**判据读 `command.degraded`，绝不读
`context.degraded`**：后者由 `Scheduler._fallback` 在校验**之后**才盖上（`_as_degraded`），读错会
把引擎自己的兜底也拒掉 → `PipelinePaused("fallback command invalid")` 打死整局。其余 15 个契约的
`pass` 都是规则允许的放弃（守卫空守、女巫不用药、猎人不开枪、狼人空刀…），不收口。

同时给 `Scheduler._command` 的违例分支补 `action_rejected` 故障行（contract/seat/attempt/violation）：
「回答合法但被规则拒绝」从前不记任何东西，故障的唯一痕迹是兜底命令本身，读起来与 provider 故障
相同；`game_summary` 按轮计数 `rounds.*.rejected_actions`，`slow_rules` 只留耗时行。

**门禁**：两个角色扩展测试各加「模型 pass 被拒 / 兜底 pass 放行」；舞者端到端走真实
`Scheduler.run_point`（provider 两次 pass → 提交兜底、`attempts == [0, 1]`、两条 `action_rejected`）；
summary 计数用例。

---

## B1 决策指标缺新角色的查验与技能命中率

**症状**：纯白之女 / 狼巫 / 石像鬼 / 猎魔人 / 舞者 / 假面的决策从不进入报告（近 200 局里
`WOLF_WITCH_REASONING` 92 条、`MASK` 86、`PURE_WHITE` 64、`DEMON_HUNTER` 35、`DANCER` 33 条）。

**根因**：`game_summary._TRACKED_EVENTS` 只收七个结果事件，而这些角色没有专属结果事件 ——
决策目标就在 `*_REASONING` 行（舞者/假面另在 `pool_seats` / `in_pool`），命中真值在
`summary.roles`（`RoleSpec.tags` 判阵营）。

**修法**：增补六个事件（**不加** seer/witch/guard/hunter 的 reasoning 事件，会重复计数）；
行里按需带 `pool_seats` / `in_pool`；`_DecisionPool` 新增六个比率与分母；弃权（无目标）不算一次
机会。明确不评分：守墓人（零契约、无模型动作）、摄梦人/噩梦之影/狼美人（没有单一「命中」定义）。

**门禁**：两个用例覆盖命中/未命中/弃权与「无机会为 `None` 而非 `0.0`」；样本字典的精确断言同步；
前端决策质量表补六行。

---

## B2 模型表现表被读成模型 A/B

**症状**：`_model_performance` 把「模型 × 阵营/角色」的每个座位当独立样本，而同批对局的座位、
板型与阵营都是随机分配的 —— 55.9% vs 50.8% 很容易被读成模型强弱。

**修法**：每一行加 `not_an_ab: true`（判断是每行的性质，页面与导出都按行读），前端表头补一句
口径说明。

**门禁**：`tests/test_benchmark_metrics.py` 的整行断言加该键；前端断言表头文案。

---

## B3 样本不足仍报 `samples: 2000`

**症状**：单 block 的 `mixed_arena` 行区间为 `null`，却写着 `samples: 2000`（配置值），
读起来像「跑过 2000 次重采样」。

**修法**：`_bootstrap_block_ratio` 多返回「真正抽到的次数」：单 block 一次都没抽 → 0；有块但
抽样分母全 0 时确实抽了 2000 次、只是没有可用的比例。mixed 行写实际次数并在 0 时加
`insufficient_blocks`；`_bootstrap` 空 pairs 写 `samples: 0` + `insufficient_pairs`。非空情形
逐字节不变（不删键：文档与导出已把它当契约）。

**门禁**：`tests/test_benchmark_fact_validation.py` 的三态。

---

## B4 技术弃票没按票种统计，也没认 receipt

**症状**：放逐票与警长票的 `action_position` 都含 `"vote"`，旧口径把两者混进同一个分母；
分子只认 `action == "technical_abstain"`（读 `normalized_result` 外层，而放逐票的命令嵌在
`command` 里，这一项**永远为假**）或 `status == "failed"`。全库 258 局实测：放逐 8411 张票里
技术弃票 2864、自愿 157；警长 859 张里自愿 `pass` 7、失败 45 —— 旧分子 67，**低估约 43 倍**。

**修法**：`_vote_kind`（`:vote_casting:` → 放逐、`:night:sheriff_vote:seat:` → 警长）替代子串匹配；
`_vote_receipt` 读 `command` 内层，**不动全局 `_result`**（speech 的 `{"text": …}` 与夜间流水线的
顶层命令语义保持不变）；技术弃票判定改为 receipt 的 `technical_failure_code` 非空或该请求未解决；
两个票种各有 terminal/technical/voluntary/failed 与自己的比率，扁平键保留为合计。

**门禁**：新增两票种 × 三态的用例；既有 requests 精确断言同步。口径修正后历史数字会变，这是
纠正不是回归。

---

## B5 「单一板型 / 没有预言家」不可见

**修法**：新增纯函数 `compute_board_coverage`（从每局 summary 自己的 `roles` 推导
`boards` + `seer_present`），进 `summary.coverage`；前端决策面板补一行「板型覆盖：N 人局 × M」，
`seer_present` 为假时明确写「本批不含预言家」。多板型轮换不做（那是功能）。

**门禁**：后端两例 + 前端两例。

---

## 跨项约定

- **`METRIC_VERSION` v3 → v4**：B1/B3/B4 都改了口径与键集，缓存按 `(run_id, version, digest)`
  命中，不 bump 会继续返回旧形状。旧 v3 行按保留窗口仍在（同版本内才 prune）。
- **没有动的东西**：`PointKey`、`sheriff_explode:`/`night_point:`/`narration:` 等 label 形状、
  检查点字段集、`_MODEL_PARAMETER_FIELDS`、`ROLE_PIPELINE_V2`；A5 不走「契约新增声明式字段」，
  因为字段进 `to_json()` 会让**每个**契约 digest 变、所有有 issued 行的旧局判 `drift`。
- **历史数据不回溯**：`summary.json` 与旧报告按惯例不重算（B1 的六个指标对老局无样本；
  `scripts/export_game_summary.py --all` 可重建 summary，需要时手动跑）。
- 门禁：后端 `pytest tests app --cov=app --cov-branch --cov-fail-under=100` = 100.00%；
  前端镜像 vitest / lint / build 全绿（`/mnt/e` 上 vitest 会 worker 超时，必须先 rsync 到 ext4）。
