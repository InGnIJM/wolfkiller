# 首夜死讯改到警长竞选之后（官方顺序对齐）

日期：2026-10-09 · 范围：`backend/app/core/{game_engine,sheriff_flow,scheduler,role_pipeline,context_projector}.py`、
`backend/app/agents/prompt_builder.py`、`backend/app/roles/base.py`、`backend/app/services/game_service.py`

## 问题

开警长的对局里，引擎把三件事都排在竞选**之前**：天亮旁白、`PLAYER_DIED`/`night_death:` 发布与
`log_deaths`、以及夜死座位的反应窗口（猎枪等）。同时竞选名单按 `alive_players()` 过滤，首夜被刀的
座位既不能上警、也不能投警长票，提示词里还会直接把它写进「已出局玩家」。

官方/主流标准规则是反过来的：法官先喊「天亮了，现在开始警长竞选」，警徽落地后才念昨晚的死讯；
**死讯公布前首夜死者不算出局**，照常上警、发言、投警长票（这正是"预言家首夜被刀仍要上警报验人、
留警徽流"的前提），其遗言排在竞选之后。依据（多处互相印证）：

- [标准规则全文（含法官台词）](https://www.langrensha.net/strategy/2021050801.html)：「第一天未宣布死者之前就进行警长竞选」
- [警长怎么玩](https://www.langrensha.net/strategy/2020122401.html)：「警长的竞选将在法官宣布死亡之前举行，场上所有玩家都可以参加」
- [预言家遗言](https://www.langrensha.net/strategy/2021081602.html)：首夜被刀的预言家「在法官未公布死讯之前…依旧要和平常一样选择上警并去争夺警徽」
- [12 人标准局](http://www.youmindao.com/langrensha/755.html)：「首个夜晚死亡的遗言放在警长竞选之后轮次发言之前进行」
- [中文维基百科·狼人殺](https://zh.wikipedia.org/wiki/zh:%E7%8B%BC%E4%BA%BA%E6%AE%BA) / [百度百科·警长](https://baike.baidu.com/item/%E8%AD%A6%E9%95%BF/22166957)：警長在「首夜公布前一夜死訊前」競選

项目文档 [docs/gameplay.md](../gameplay.md) 写的正是这个顺序 —— 是引擎实现反了。

## 改动

1. **公告延迟（引擎）**：`_resume_pipeline_night` 在「开警长 + 第 1 轮」时跳过死亡发布与
   `log_deaths`，把 `_PendingNightCompletion` 停在 `stage 10` 并保留槽位（不新增字段，只新增
   stage 数值，旧检查点仍可解码）；竞选结束（`_finish_election` / `_election_explode` / 恢复早退分支）
   调 `_announce_pending_night_deaths()`，按 stage 10→13 依次补旁白、`night_death:` 发布、
   `log_deaths`、夜死座位的反应窗口，然后才转入 `DAWN`。每个 stage 落一个
   `night_announce:{round}:{stage}` 检查点，中断后从同一 stage 续跑。
   夜里就判出胜负时不再竞选，但会在 GAME_OVER 之前当场公告（`_resume_pipeline_night` stage 2）。
2. **竞选名单**：`election_seats(state) = alive_players() ∪ unannounced_death_seats(state)`，
   引擎的上警/退水/发言/投票/当选与 `badge_loss_message` 全部改用它；`_still_on_the_table(seat)`
   区分「未公布死亡」（仍参赛）与「竞选途中死亡」（立刻退出）。`_validate_sheriff_campaign`
   同样放行未公布死者。
3. **公开死亡投影**：`public_alive_seats()/public_dead_seats()` 成为提示词与
   `ContextProjector._public_facts` 的存活/出局唯一来源；`off_badge_seats()` 不再按死亡过滤
   （竞选先于死讯，首夜被刀者当时就是警下）。
4. **死因清单翻转**：`_ANNOUNCED_IMMEDIATELY = {exile, self_explode, knight_duel, hunter_shot,
   wolf_king_shot, charm}`，其余一律算夜死批次 —— 顺带修掉旧白名单漏 `verify_kill`/`hunt`/`dance`
   （这三种夜死座位曾被写进「警下、有投票权」名单）。`tests/test_values.py` 钉住这份名单。
5. **响应窗口延后**：`Scheduler.run_point(..., defer_event_types=("PLAYER_DIED",))` 把夜死批次的
   反应窗口挡在 `NIGHT_COMMIT` 之外；`_run_deferred_night_reactions()` 在死讯公布后用
   `_run_response_point(DAWN_REACTION, ...)` + `_settle_and_publish()` 重新开窗，观众事件走新的
   `night_reaction:{round}:{seats}` 步骤（`GameService._checkpoint_domain_events` 新增分支）。
   于是夜死座位是在听完竞选发言后才决定开枪。

## 观众时间线

```
夜晚结算（后台，不公布） → 竞选（上警/发言/退水/投票/PK，含首夜被刀者）
→ 警长产生 → 天亮了 / 昨晚死讯（narration + night_death 发布 + log_deaths）
→ 夜死座位的响应窗口（night_reaction） → 天亮阶段 → 首夜遗言 → 白天发言 → 放逐投票
```

## 门禁

- `tests/test_sheriff_engine.py`：死讯等竞选（stage 10 / 无发布）、关警长局行为不变、观众顺序
  `SHERIFF_ELECTED < narration < PLAYER_DIED < log_deaths`、判胜即公告、自爆仍公告、
  stage 11 恢复不重播旁白、首夜死者可上警/可当选/可投票、反应窗口在公告之后且带夜死批次。
- `tests/test_sheriff_flow.py`：`unannounced_death_seats`/`election_seats`/`night_batch_deaths`/
  `public_*` 的相位与死因语义、`off_badge_seats` 保留首夜死者。
- `tests/test_prompt_builder.py`：竞选提示词不泄死讯（存活/出局名单），白天恢复显示。
- `tests/test_scheduler.py`：`defer_event_types` 不开窗、不问模型、不结算伤害，事件本身照常返回。
- `tests/test_game_service_lifecycle.py`：`night_reaction:` 分支（空窗口退化成 STEP_COMMITTED）。
- `tests/test_game_engine.py`：天亮播报只念夜死批次（反应死者不算「昨夜」）。

## 兼容

- `_PendingNightCompletion` / `_PendingNightBatch` / `GameState` 字段集不变，`night_death:`、
  `narration:`、`night_complete:` 等 label 形状不变，`PointKey` 不变 → 旧检查点可续跑。
- 升级后恢复的老局：竞选已完成且槽位为 `None` 时公告助手是空操作（不会重复发布）；停在竞选前的
  老局按新顺序继续。
- 已知未做：观众快照（`GET /api/games/{id}/snapshot`）在竞选窗口内仍按真实 state 显示首夜死者已死
  （事件时间轴是正确的，回放由事件驱动）。
