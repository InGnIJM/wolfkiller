# 复盘修复：摘要死亡并集、警长提示词、夜幕节拍、延迟死亡公告

日期：2026-10-10 · 范围：`backend/app/services/game_summary.py`、`backend/app/core/{sheriff_flow,game_engine,night_flow}.py`

起因是复盘 `d956d1fb`（2026-10-09 21:28 的 12 人赤月猎魔人局）时，把 `game.log` / `summary.json`
/ SQLite（观众事件 + 终局检查点）/ 94 条提示词采集四条线对齐核对。**这一局的规则执行本身没有
问题**（7 个死亡的死因与时序、警长竞选先于死讯、发言顺序、票型、胜负判定逐条对得上），但查出
4 条真实缺陷，都是长期存在、且现有门禁结构上抓不到的。下面按"症状 / 根因 / 修法 / 门禁"记录。

---

## 1. `summary.json` 每轮 `deaths` 被后一批死亡覆盖

**症状**：最新一局 r1 只剩自爆的 5 号、丢掉夜刀的 6 号（女巫）；r3 只剩被狩猎的 2 号、丢掉夜刀的
8 号（猎魔人）。全库扫描：**13 局命中**，最早 2026-09-19（`25bd57c7`），最近是本局。

**根因**：`log_deaths` 在一轮里会被调用多次（夜死批次、白天中断、延迟死亡），而
`game_summary._summarize` 对 `night_deaths` 用的是**赋值**：

```python
_round_bucket(rounds, rnd)["deaths"] = [death for death in ...]
```

后一批把前一批整批挤掉；空批（平安夜）还会把前面已有内容清空。`speeches` / `votes` /
`night_actions` 一直是追加，只有 `deaths` 是赋值。

**修法**：抽出 `_merge_deaths(bucket, deaths)`：按 `(player_seat, cause, round_number)` 去重后
按时间追加。去重是防御性的 —— `_resume_pipeline_night` 的 stage 0（写 `log_deaths`）与 stage 前进
之间没有独立提交，崩溃恢复会把同一批再写一次；正常路径每批只写一次，所以现存数据的结果不变。

**门禁**：`tests/test_game_summary.py::test_every_death_batch_of_a_round_survives_the_summary`
（一轮两条批次 + 空批）、`::test_a_replayed_batch_is_not_counted_twice`、
`::test_malformed_death_rows_never_break_a_batch`。旧 fixture 每轮只喂一条 `night_deaths`，
所以覆盖率和断言全绿；新用例在修复前是红的（实测第一条批次连同空批一起消失）。

---

## 2. 警长上警提示词写着「当前候选人：无」

**症状**：`d956d1fb` 里 5–12 号的上警提示词全部是

```
已上警座位：4号、5号、9号；当前候选人：无。
```

同一行自相矛盾。有提示词采集的近 200 局里 **72 局命中**（含 2026-10-02 之前的局）。

**根因**：`_context_block` 用 `office.active` 渲染这个字段，而引擎只在**上警循环结束之后**
（`game_engine.py:1282`）与**退水循环结束之后**（`:1321`）才写 `active`；上警阶段（`step == "run"`）
它必然是空集。退水/投票阶段读 `active` 是对的，所以只有上警阶段错。

**修法**：上警阶段渲染 `office.candidates`（"到目前为止已经上警、且还没人退水"），其余阶段照旧读
`active`。

**门禁**：`tests/test_sheriff_flow.py::test_the_run_prompt_lists_the_seats_that_already_ran`、
`::test_the_withdraw_stage_keeps_the_full_candidate_list`，以及**类级**堵漏用例
`tests/test_sheriff_engine.py::test_the_run_prompts_carry_the_candidates_the_engine_really_has`
—— 它用**真实的 `SheriffDirector` + 捕获式 `invoke` 驱动真实竞选循环**，断言每条上警提示词的候选人
字段等于当时 `state.sheriff_office.candidates`。

旧门禁为何放过：提示词测试的 fixture 直接手写 `office.active`（`test_sheriff_flow.py:459-460`、
`:489-490`、`:685-686`、`:697-698`、`:745`），把退水/投票阶段的形状套在上警阶段的提示词上；引擎级
测试又全部注入 stub director（`ScriptedSheriff`），从不渲染提示词。**fixture 与真实状态不同形**
是这几条提示词缺陷的共同盲区。

---

## 3. 警长选发言方向（死左/死右）的提示词没有上下文

**症状**：警长收到的完整提示词是

```
你是警长9号。请选择今天的发言方向 side，只能是：death_left、death_right。
```

他不知道**谁死了**（锚点）、不知道存活名单，也没有任何地方说明 left/right 对应座号方向 —— 系统
提示反而强调"座位号只是标识，不含空间含义"。有采集的 **65 局**一直是这一句。

**根因**：`_context_block` 是每个提示词各自拼的，`side_prompt` 从未调用它；`tests/test_sheriff_flow.py`
对它的唯一断言是 `assert "sheriff_left" in ...`（枚举名在不在），所以"这个决定能不能做"没人管。
上一轮修交/撕徽时所写的注释（"交/撕徽提示词过去是**唯一**不带上下的警长提示词"）也是错的结论，
它把 side prompt 排除在视野之外 —— 本次一并改掉。

**修法**：`sheriff_flow.day_speech_order(state, side)` 成为顺序的唯一来源（`game_engine._rotate_speech_order`
改为调用它），`side_prompt` 拼 `_context_block` + 昨夜死者 + 存活名单 + 每个选项的真实顺序预览
（含 `_SIDE_HINTS`：左=升序、右=降序的座号定义）。

**门禁**：`test_the_side_prompt_carries_the_anchor_the_alive_list_and_both_orders`、
`test_the_side_prompt_preview_matches_the_shared_order`、
`test_the_side_prompt_survives_an_empty_table`、
`test_every_sheriff_prompt_carries_identity_and_the_office_lists`（**遍历全部警长提示词构造器**，
新提示词漏拼上下文即红），以及引擎级漂移保险
`tests/test_sheriff_engine.py::test_the_side_prompt_previews_the_order_the_engine_rotates_to`
（预览顺序 == `_rotate_speech_order` 的结果）。

---

## 4. 延迟死亡没有公开公告

**症状**：`d956d1fb` 3 日 2 号在"发言结束后、投票前"被狩猎出局，公开对话记录里**一条公告都没有**：
全场发言都在"归票 2 号"，紧接着的票型里 2 号已经不在，只能从权威状态的"已出局玩家"看出他没了。
全库 `delayed_death:` 事件 5 次（`e6e55110` r4、`c32598c2` r2/r3、`8d88bc17` r3、`d956d1fb` r3），
**无一有公告**。

**根因**：夜死批次、白天中断、放逐各写一条系统消息；延迟死亡走 `_resolve_delayed_deaths` →
`_publish_deaths`（总线 + JSONL），从不写 `ConversationLog`。`GameService._on_player_died` 只广播
WebSocket，不落公开记录。观众流/回放其实都有这条死亡（`delayed_death:` 标签 + `night_deaths` 操作），
缺的是**玩家可见的叙述**。

**修法**：新增 `_announce_delayed_deaths()`，在 `_publish_deaths` 之后写一条公开系统消息
`"{seats}玩家出局。"`（多死合并一条）。**不写死因**：`hunted` / `delayed_death` 不在
`_PUBLIC_STATUSES`，写死因等于把猎魔人的身份交给全场。

**门禁**：`tests/test_old_drunkard_extension.py::test_a_delayed_death_is_announced_in_the_public_record`
（毒伤路径）、`::test_a_delayed_batch_announces_every_seat_once`、
`::test_the_hunt_path_is_announced_by_the_same_step`（hunt 路径）。修复前三个都是红的。

---

## 5. 夜幕开场节拍（原「开眼旁白早于天黑请闭眼」）

**先纠正定性**：这**不是**执行顺序 bug。`tests/test_game_engine.py` 明确锁定"角色台词在前、引擎节拍
在后"，`docs/gameplay.md` 也把 `NIGHT_ACTION` 排在狼队讨论之前；stage 号还被旧档持久化，不能重排。

**症状**：`wolf_open` 那一拍的标题是「天黑请闭眼」（正文才是狼人那句），它排在 stage 2，即第一个
调度点之后 —— 于是有 `NIGHT_ACTION` 角色的夜里，观众先听到「守卫/猎魔人请睁眼」、再听到
「天黑请闭眼」。**95 / 113 局**（有观众旁白的局）命中，最早 2026-09-28；上一局（`7aab7a3e`，守卫板）
三个夜晚全部如此。

**修法**：`_NARRATIONS` 拆成两拍 —— `night_open`（「天黑请闭眼」，stage 0 barrier 里播，先于任何
调度点）与 `wolf_open`（「狼人请睁眼」，仍是 stage 2 的狼队节拍）。stage 号、`PointKey`、
`narration:{round}` label 形状都不动。

**门禁**：`tests/test_game_engine.py::test_the_night_opens_before_any_role_is_woken`（有
`NIGHT_ACTION` 角色与无角色两种板子；修复前红）。同批更新了锁定旧节拍列表的既有断言
（`test_night_flow.py`、`test_night_script.py`、`test_game_engine.py` 两处、
`test_sheriff_engine.py` 的"第一条旁白"需改为认「天亮了」）。

---

## 回归

- 全量：`pytest tests app -q` → **4638 passed**；`--cov=app --cov-branch --cov-fail-under=100` → **100.00%**。
- 数据：只读脚本用现有 `build_game_summary` 从 `games/<id>/game.log` 重新派生，与观众流 `death`
  事件（排除放逐，放逐记在 `exiled`）逐轮对账 —— **435/438 轮一致**。最新一局现在是
  r1 `[6 wolf_kill, 5 self_explode]`、r2 `[9 wolf_kill]`、r3 `[8 wolf_kill, 2 hunt]`；
  `8d88bc17` r3 从只剩 `[1 hunt]` 变成 `[12 wolf_kill, 1 hunt]`。
- 前端：本次没有新事件类型 / label / DTO 变化，`narration` 与系统消息都由现有渲染面处理，无需改动。
- 兼容：stage 号、`PointKey`、label 形状、检查点字段均未变，旧档可续跑；提示词内容变化只会让
  `prompt_digest` 变化，恢复仅记告警（既有约定）。历史 `summary.json` 按惯例不回溯重算。

## 附：回归中发现的相邻项（未修，待定）

**竞选自爆的死亡从不写 `night_deaths`**：`5cf2de15` r1、`6dbbea17` r1、`a3ac6cb9` r1 的
3 号 / 10 号 / 6 号（狼人竞选期自爆）在观众流里有 `sheriff_explode:` 死亡事件，但 `game.log` 里
没有任何 `night_deaths` 记录，于是 `rounds.*.deaths` 与公开回放 `/logs`（`night_deaths` 操作 →
`death` 事件）都看不到这一死。这与第 1 条同属"`rounds.*.deaths` 漏死亡"，但根因不同（不是覆盖写，
而是从不落批次），且改动会同时改变 `/logs` 的回放内容，故按"相邻项"记录，不在本次范围内。
