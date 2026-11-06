# 2026-11-03 寻香识命 10 局复盘与修复

## 0. 结论

运行 **`39c91309-8dad-4938-a3af-66ba7f9fbeae`**（`mixed_arena`，seed 42，10 局，concurrency 1，12 座全 `Gemini-3.8-flash`，配置 `06211189491646feba35dfe1881a81a3`）以 **7 狼胜 / 3 好人胜** 结束。

70% 不是单一原因，按证据排序：

1. **模型好人侧对跳预的归票判断弱**：D1 提示词完整，仍出现 10:1 / 10:2 放逐真预；
2. **板子信息结构放大狼队优势**：觉醒预言家只报“两人中是否有狼”，假预可直接报单座查杀；寻香魅影是隐藏狼且带双死；警徽进一步放大；
3. **警长选举提示词/流程有确定性缺口**（本次修复的主目标）：警长投票没有白天那套角色规则/决策门/完整历史，私有事实缺 `pair_checks`/`wolf_kill_target`，竞选发言被截到 160 字；
4. **React 死亡未进入公开死亡账**（本次修复的另一处）：`bond_death`/`love_death` 只置 `is_alive=False`，没有 `death_history`，也不进清晨批次，10 局里 6 局漏记。

## 1. 逐局

| 局 | game_id | 狼座 | 警长 | D1 放逐 | 结局 |
| --- | --- | --- | --- | --- | --- |
| G1 | `f1d69aae` | 1,4,8,9 | 4（狼） | 真预 5 | 狼胜 |
| G2 | `979c5b89` | 4,5,9,12 | 4（狼） | 真预 1 | 狼胜 |
| G3 | `46a88263` | 3,4,8,11 | 3（狼） | 真预 12 | 狼胜 |
| G4 | `9e6eec51` | 1,2,6,9 | 1（狼） | 真预 10 | 狼胜 |
| G5 | `7dcb6bee` | 1,5,6,10 | 无 | 狼 5 | 狼胜 |
| G6 | `b5271f7e` | — | 真预 11 | — | 好人胜 |
| G7 | `798d0681` | — | 真预 4 | — | 好人胜 |
| G8 | `15dbc95f` | — | 狼 6 | 真预 3 | 狼胜（all_gods_dead） |
| G9 | `4d807b43` | — | 无 | 狼（共 2 狼出局） | 狼胜 |
| G10 | `534187d4` | — | 狼 6 | 狼 6 | 好人胜 |

- **真预 D1 被放逐的 5 局全狼胜**；好人胜的 3 局共放逐 10 狼；狼胜的 7 局共放逐 4 狼。
- 警长是狼的 6 局：5 狼胜；警长是真预的 2 局：2 好人胜；无警长的 2 局：2 狼胜。
- v7 报告：`exile_wolf_rate 0.3125`（10/32）、`good_vote_hit_rate 0.422`（73/173）、`witch_poison 0.571`、`hunter_shot 0.333`、`wolf_kill_god_rate 0.606`、`average_rounds 3.7`。

## 2. 直接证据

- G1 `llm_prompts.jsonl:18`：觉醒预言家上警 run 提示词写“暂无夜间私密信息”；同一座位的竞选发言提示词却已有 `pair_checks: [{"has_wolf": true, "seats": [1, 2]}]`。
- G1 `:34`：女巫警长投票提示词只有“解药仍在手；毒药仍在手”，没有“昨晚狼袭击 2 号”；遗言提示词才出现。她投了狼 4。
- G2 `:39` / `:80` / `:93`：同一模式；`:28` 假预竞选发言完整，但警长投票提示词没有角色规则/决策门/完整历史。
- G1 `game.log:159-160`：`bond_death` 没有“玩家出局”的公开公告；`summary.json` 的 `rounds[2].deaths` 只有 `[3,10]`，缺 bond 死者。

## 3. 本次修复

### 3.1 警长提示词统一（P0-A）

- `PromptBuilder.build_sheriff_decision_prompt()`：警长上警/退水/投票/选方向/交徽全部走与白天相同的 `_build_base`，带 `<public_role_rules>`、`<decision_gate>`、`<untrusted_public_history>`、完整私有事实与 300 字公开记录窗口。
- `_private_facts_block()` 结构化渲染 `private_checks`（“查验X号：好人/狼人”）、`pair_checks`（“查验A、B号：有狼/无狼”）、`role_checks`、`wolf_kill_target`（“昨夜狼人袭击目标：X号”）及解药/毒药/猎枪状态。
- 警长投票任务新增对跳预提示：觉醒预言家一次只知道两人中是否有狼，直接断定具体某号必须给出额外依据；被点名的好人不能只凭“查验里没被验出”站边。
- 保留狼队成员/本夜频道（按 `actor_sees_pack`，隐狼不泄露）与 `no_self_explode` 角色不能自爆/吞警徽的约束；`_context_block` 仅作为异常/未知角色的兜底。
- 修复按 `vote_round` 当天的投票轮次标签，并给投票示例加“座位号仅示范格式”的说明。

### 3.2 React 死亡进入公开账（P0-B）

- `EffectApplier._apply_one` 对 `MARK_DEATH` 同时写 `state.death_history`，`PLAYER_DIED` 载荷补 `round_number`。
- `ResponseQueue._open_new` 对缺少 `target_seat` 的 `PLAYER_DIED` 回退读 `seat`，为死者本人开响应窗。
- `GameEngine._pipeline_night_deaths` 把本轮尚未出现在结果事件里的 react 死亡（`bond_death`/`love_death`/`charm`/`stand_in`）并入清晨批次；`night_deaths`、`summary`、旁白、公开死亡列表同步。
- **清晨反应窗口之后**（`_run_deferred_night_reactions`，猎人开枪/殉情等窗口跑完）再扫一次 ledger：把本轮尚未发布、未被 settlement 覆盖的纯 react 死亡 `_publish_deaths` + 公开公告，并把它们追加进 `_PendingNightCompletion.deaths`（同时推进 `event_cursor`），所以晚到的 bond/love 死亡也不会漏记，续跑不会重复发布。

### 3.3 测试

- `tests/test_sheriff_flow.py`：警长投票/上警提示词含公共角色规则、决策门、不可执行历史、完整长发言、`pair_checks`、死而未公布女巫的 `wolf_kill_target`；狼队/隐狼约束回归。
- `tests/test_fragrance_extension.py`：bond 死亡写 ledger、带 `round_number`、`seat` 事件能开死者窗口、清晨批次合并、结果事件不重复。
- `tests/test_prompt_builder.py`：`sheriff_vote` 任务、`role_checks`、异常私有事实、重新投票标签。
- 目标：`cd backend && python -m pytest tests app --cov=app --cov-branch -q`，100%。

## 4. 控制实验（待跑）

修复后先不重做板子，用同一 seed/模型跑 30 局筛查：

| 组 | 配置 | 目的 |
| --- | --- | --- |
| A | 原 `twelve-player-fragrance` | 修后基线 |
| B | 觉醒预言家 → 普通预言家（`fragrance-normal-seer`） | 隔离双人查验信息弱 |
| C | 去掉寻香魅影（4 普通狼，`fragrance-no-hidden`） | 隔离隐藏狼/双死 |
| D | 原板但 `enable_sheriff=false` | 隔离警长选举 |
| E | 原板换 Gemini-3.7-Flash（`aeb2b719336640d0929e16a3fb7cee90`） | 隔离模型 |

`POST /api/benchmarks`，`mode=mixed_arena`，`games=max_games=30`，`seed=42`，`concurrency=4`，`scenario` 传对应 `role_counts`/`enable_sheriff`/`victory`/`witch_self_save`。先各跑 1 局 smoke。

决策树：A ≤55% → 提示词/流程是主因；A 仍 ≥65% 时看 B/C/D/E 哪组回到 ~50%，再决定是否另出板子重平衡方案。30 局只做筛查，稳定估计要 50+ 局。

## 5. 历史对照

- 同模型 12 人特殊板：fragrance 70%（n=10）、masquerade 67%、wolf-beauty 75%、cupid 92%、mirror 100%；ten-player-standard 54%（n=61）、pure-white 47%（n=30）。
- 按模型：Gemini-3.8-flash 29/40 = 72.5%；3.7/3.8 混座 49%；MiMo 54%。

结论：70% 与“模型好人侧偏弱 + 板子信息结构 + 两处提示词/流程缺口”一致；本次先修可确定的缺口，再用控制实验把板子/模型份额量化，避免用 10 局直接改板。
