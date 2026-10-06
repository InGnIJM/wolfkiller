# 最近 6 局规则执行漏洞审计（2026-10-06）

审计对象：数据库中**最新的 6 局**普通对局（截止 2026-10-06 16:05 的状态）。
数据源：`backend/data/wolfkiller.sqlite3` 的 `audience_events` / `domain_events` / `game_checkpoints` / `game_commits`，以及各局 `game.log` / `model_errors.log` / `llm_calls.log` / `llm_prompts.jsonl` / `conversation.log`。
方法：机械不变量复算（屠边、发言顺序、投票资格与加权计数、死因门禁、状态清扫）+ 源码链路核对 + 逐局人工时间轴复核 + 对关键结论做可执行复现。

| 局 | 时间 | 板子 | 结果 |
| --- | --- | --- | --- |
| `e6e55110` | 10/6 15:23 | 预言家/女巫/白痴/猎魔人/赤月使徒 + 3狼 + 4民 | 好人胜 `all_wolves_dead` |
| `29f942fc` | 10/6 04:58 | 预言家/女巫/猎人/守墓人/石像鬼 + 3狼 + 4民 | 好人胜 `all_wolves_dead` |
| `09982d7c` | 10/6 02:24 | 预言家/女巫/猎人/摄梦人/噩梦之影 + 3狼 + 4民 | 狼胜 `wolves_outnumber_good` |
| `1473dff7` | 10/6 01:43 | 预言家/女巫/白痴/舞者/假面 + 3狼 + 4民 | 好人胜 `all_wolves_dead` |
| `292fcca1` | 10/5 23:47 | 预言家/女巫/白痴/舞者/假面 + 3狼 + 4民 | 好人胜 `all_wolves_dead` |
| `ca023cb0` | 10/5 22:53 | 预言家/女巫/猎人/摄梦人/狼王 + 3狼 + 4民 | 狼胜 `all_gods_dead` |

**6 局全部为 `reveal_on_death=false`（不明牌），全部开启警长。**

---

## 一、确认的规则执行漏洞

### A1【严重】遗言兜底模板把角色名写进公开遗言，不明牌局身份直接泄露

**结论**：当玩家的遗言 LLM 调用失败而走角色层兜底模板时，模板会把 `RoleSpec.display_name` 拼进公开发言，变成「我是 12 号 **Wolf King**，我出局了。…」。本批 6 局全部配置 `reveal_on_death=false`，狼王规则也明确「发动时不翻牌」，因此这是规则被直接破坏，且泄露文本会进入其余所有存活玩家的提示词。

**证据**（`ca023cb0`，狼王 = 座位 12）
- `audience_events` seq 154（`step_key=00000164:last_words:2:12:exile`）：
  ```json
  {"phase":"last_words","player_seat":12,"round_number":2,
   "text":"我是12号Wolf King，我出局了。我怀疑的玩家是1号、2号、3号，希望好人能仔细分析，找出狼人。"}
  ```
- 该次调用走兜底而非模型输出：`llm_calls.log` 该条 `phase=vote_resolution contract_id=last_words attempt=2 retried=true parse_result=fallback`；文本与模板逐字一致。
- 终局 checkpoint `state.revealed_role == {}`，`reveal_on_death=false`（seq 1 的 `game_initialized.config`）。
- **泄露已扩散到全部玩家提示词**：`llm_prompts.jsonl` 中字符串 `我是12号Wolf King` 命中 **48 条**记录，覆盖 **10 个存活座位（1–7、9–11）**、第 3–5 回合，含 `day_speech` 16 / `exile_vote` 11 / `werewolf_discussion` 8 / `werewolf_kill` 4 等。
- 代码事实：
  - `backend/app/roles/base.py:306-323` —— `:312` 取 `specs[self.role_name].display_name`，`:320` 拼进遗言模板（白天发言分支不带角色名，只有遗言分支泄露）。
  - 同型第二处：`backend/app/core/game_engine.py:2296-2312` `_emergency_speech()`。
  - 两者在当前 HEAD（`1d7bb2a`）里**均未修复**。

**影响面（全库统计，`reveal_on_death=false` 的局）**

| 泄露的角色名 | 次数 |
| --- | --- |
| Villager | 64 |
| Werewolf | 61 |
| Hunter | 26 |
| Witch | 24 |
| Seer | 19 |
| Guard | 17 |
| Old Drunkard / Knight / Wolf King | 2 / 1 / 1 |

共 **77 局**出现公开泄露；其中 **49 局泄露的是神职身份**（预言家/女巫/猎人/守卫/骑士），例如 `049bac91 seq=102 → Seer`、`a8ade626 seq=72 → Seer`、`9d31f5d9 seq=130 → Witch`。任何一局里，狼队都能直接从公开历史读到真预言家的底牌。

**建议**：兜底遗言只写座位号（与白天发言模板一致），角色名一律不进公开文本；`_emergency_speech` 同理。

---

### A2【严重，很可能改变了胜负】石像鬼「狼窝全灭后独自带刀」永远不发动

**结论**：`hidden_wolf_kill` 契约的调度点与狼刀同为 `NIGHT_WOLF_VOTE`，而命令提供方对**任何**该调度点的契约都短路去取狼队投票字典；石像鬼不属于狼窝，字典里永远没有它的票 → 每次都拿到兜底 `pass`，**决策从未发给模型**。文档承诺的「狼窝全灭后靠 `hidden_wolf_kill` 独自带刀」是死代码。

**源码链路**
1. `backend/app/roles/hidden_wolf.py:117-127`：`contract_id="hidden_wolf_kill", schedule_point=SchedulePoint.NIGHT_WOLF_VOTE`。
2. `backend/app/services/game_service.py:2545-2551`：
   ```python
   def provider(request, context, attempt, *, prompt_sink=None):
       if request.contract.schedule_point is SchedulePoint.NIGHT_WOLF_VOTE:
           command = director.collected_vote(request.actor_seat)
           return command if command is not None else PipelineActionCommand(
               action_type=request.contract.fallback_action_type,   # "pass"
               target_seat=None, reasoning="系统异常，本轮未行动")
   ```
   （`:2657-2658` 的 `durable_provider` 对同一调度点二次早退，连 prompt 捕获与落库都跳过。）
3. `backend/app/core/game_engine.py:677-696`：`director.record_votes(...)` 只会收到 `wolves = [in_wolf_pack 且存活]` 的票；狼窝为空时 `record_votes(())`。
4. `backend/app/core/wolf_pack.py:29-38`：`in_wolf_pack` 对有 `no_wolf_pack` 标记的石像鬼恒为 `False`。

**可执行复现**（用真实契约 + 真实 `NightDirector` + 真实 `GameService._command_provider`，`client_provider` 被替换为「一旦被调用就抛异常」以证明模型未被调用）：
```
issued at NIGHT_WOLF_VOTE: [(2, 'hidden_wolf_kill')]
hidden_wolf_kill.schedule_point is SchedulePoint.NIGHT_WOLF_VOTE
provider(request, context, 0) -> action_type='pass' reasoning='系统异常，本轮未行动'
=> the lone kill is forced to pass without a single model call.
```

**真局证据（`29f942fc`）**
- `action_counts.game` 含 `2\0hidden_wolf_kill\0 5` 与 `\0 6`（第 4 夜没有 → 适用性门禁本身正常）。
- 该局 `llm_prompts.jsonl` 中座位 2 的夜间提示词只有 `gargoyle_check`（第 1–6 夜共 6 条），**`hidden_wolf_kill` 0 条**；14 个 pipeline 契约窗口里 12 个在 `model_requests` 有记录，唯二两个 `hidden_wolf_kill` 窗口 0 命中。
- 第 5、6 夜无 `WEREWOLF_KILL` 事件、无死亡，旁白为「昨晚风平浪静，无人被袭击」；石像鬼（全场最后一狼）连续两夜零出手，第 6 天被放逐。

**影响**：第 5 夜存活的是 2（石像鬼）、7（民）、11（猎人）。按 `rule_engine.check_win` 的「狼刀在先」（`backend/app/core/rule_engine.py:29-34` 先判 `alive_gods == 0`），石像鬼只要在夜间杀掉最后的存活神职 11 号，即使猎人开枪反杀也仍判**狼胜**。因此本局「好人胜」很可能是这个 bug 造成的（机制层=确认；胜负因果=高度疑似，因为石像鬼具体会选谁不可复现）。

**测试盲区**：`tests/test_hidden_wolf_extension.py:335` 用桩 provider 只验证「请求被发出」，从不经过 `GameService._command_provider`。与项目自己的教训（摄梦人 `source="dream"`：hook 级测试全绿、端到端没人跑过）同型。

---

### A3【高】模型故障被当成玩家的「主动放弃」：一次性技能、警徽、强制技能被静默作废

**结论**：`_command_provider` 在模型调用抛错时，只要错误不属于「可重试」类别，就**不重试**，直接返回契约兜底动作，并把内部故障文案 `系统异常，本轮未行动` 当作玩家自己的理由写进公开流与私有思考记录。对「一局一次」和「每晚必须」的技能，这等于无声地废掉能力，且公开记录读起来完全像是玩家的决策。

**机制**：`app/services/game_service.py:2602-2651`（异常 → `_is_retryable_model_error` `:203-214` 只认 429/500/502/503/504、`provider_timeout`、类名含 connection；否则单次失败即 `command=None` → fallback）；`app/core/scheduler.py:372-406`（两次尝试的循环只对**校验违规**重试，对 provider 返回的合法 fallback 不再追问）；`app/core/sheriff_flow.py:605-632` 等警长入口的 `except Exception: return <默认值>`（无任何重试）。

**本批 6 局的实测损失（共 17 处兜底记录）**

| 局 | 被吞掉的能力 | 证据 |
| --- | --- | --- |
| `ca023cb0` | **狼王爪**（第 2 天被放逐，`wolf_king_shot` 503）与 **猎枪**（第 3 天被放逐，`hunter_shoot` ValueError）双双作废 | `role_resources` 终局 `12:{wolf_claw:1}`、`10:{gun:1}` 均**未消耗**；`death_history` 无 `wolf_king_shot`/`hunter_shot`；`action_counts` 却记 `12\0wolf_king_shot:1`、`10\0hunter_shoot:1`（兜底动作也走 ACCEPT_ACTION 计数，`per_game_limit=1` 被烧掉）→ **benchmark 会把「能力丢失」读成「成功使用」** |
| `ca023cb0` | 警长交徽失败 → 静默撕徽 | `model_errors.log` `night:4:sheriff_badge:9`；`model_requests` 该 request `status='failed', recovery_retry_count=0` |
| `e6e55110` | 警长（真预言家）第 2 夜死亡交徽失败 → 静默撕徽，公开记录 `sheriff_badge:2:1:tear` **无 reasoning** | `model_errors.log` `night:2:sheriff_badge:1`；CLAUDE.md 明确「预言家可用交徽方向传递查验结果」，这条规则因此落不了地 |
| `e6e55110` | 第 4 夜最后一狼的狼刀失败（另一侧也受损） | `model_errors.log` `night:4:werewolf_kill:7` |
| `1473dff7` | 舞者第 3 夜「必须起舞（不可放弃）」被吞 | `docs/gameplay.md` 舞者条「每晚**必须**邀请恰好三名存活玩家共舞（**不可放弃**）」；`DANCE_POOL` 全场仅 1 次 |
| `292fcca1` | 舞者第 2、4 夜被吞（能够起舞的 3 夜里丢 2 夜），两夜狼刀均命中 | `model_errors.log` ×2；该板无守卫，舞池是唯一挡刀手段 |
| `09982d7c` | 噩梦之影第 2、3 夜完全没有恐惧；**女巫在她被狼刀的第 2 夜握着毒药没出手，带着毒药出局** | `model_errors.log` ×3；终局 `role_resources {"8":{"poison":1}}`；全场遗言都在喊「毒 3 号」 |
| `29f942fc` / `09982d7c` / `ca023cb0` | 警长票静默降级为弃权 / 发言方向静默默认为 `sides[0]` | `TECHNICAL_ABSTAIN` 5 次（`ca023cb0`）；`sheriff_speech_side` failed |

**公开记录被污染**：`reasoning="系统异常，本轮未行动"` 会作为 `*_REASONING` 公开事件进入上帝视角（如 `09982d7c` seq 106/122/167、`ca023cb0` seq 151/205），并写进玩家自己的 `conversation.log`（`scope=thought`）——即玩家的「内心思考」里出现引擎故障文案。

**建议**：(a) 一次性/强制类契约（`per_game_limit=1`、`actions_requiring_target` 非空的必选动作、死亡响应窗口）在 provider 异常时不应降级为 `pass`，应重试或把整局标为可恢复的 `paused`；(b) 兜底理由不要用「系统异常」这类内部文案冒充玩家理由，改用中性占位并单独记录故障；(c) `action_counts` 不应把降级动作计成成功使用。

---

### A4【高，代码级确认，本批对局未触发】「不能连续两晚同一人」读的是上一次**实际**选择，被跳过的夜晚不清除状态

**结论**：守卫 / 噩梦之影 / 假面 / 狼美人 / 摄梦人的「连续两晚」判断都比较「上一次实际生效的目标」（`last_guarded` / `last_feared` / `last_masked` / `last_charmed` / `last_dreamed`），而这些键只在动作**成功解析**时才写入，`pass`、模型失败、被封印的夜晚都**不会清除**它（全仓库没有任何一处从外部清除这些键）。

**后果分两类**
- **禁令型（守卫/噩梦/假面/狼美人）**：第 N 夜守 X、第 N+1 夜被跳过、第 N+2 夜想再守 X —— 按规则（两夜不连续）合法，但校验会判 `consecutive_guard` 等违规 → `scheduler._command` 盲重试一次 → 再被拒 → 降级 `pass`，**再丢一夜**。
  - `app/roles/guard.py:18`、`app/roles/nightmare.py`（`FEARED_KEY`）、`app/roles/mask.py:44`、`app/roles/wolf_beauty.py:50`
- **摄梦人（反向致命）**：`app/roles/dream_weaver.py:91-109` 第 N 夜摄 X、第 N+1 夜被跳过、第 N+2 夜再摄 X → `context.facts.get("last_dreamed") == target` 成立 → 提交 `SUBMIT_DAMAGE(cause=dream_death)`，把一个**本该被保护**的座位**杀死**。规则原文是「连续两晚同一人则该人第二夜出局」。

**本批未触发的原因**（逐局核对）：`09982d7c` 摄梦人第 1 夜被噩梦之影封印（`last_dreamed` 仍为 `None`），第 2/3 夜目标 3、1 不同；`ca023cb0` 摄梦人 5 夜目标 9→6→9→4→11，无连摄；其余 4 局无这些角色。属**潜伏缺陷**，任何一次「跳夜 + 重复目标」就会踩中。

---

## 二、流程 / 记录一致性缺陷

### B1【中】夜间开眼旁白与角色行动错位，且死者仍被「请睁眼」

`app/core/game_engine.py:711-728` 的阶段编排是：stage 6 播 `witch_narration`（女巫请睁眼）→ stage 7 跑 `NIGHT_SEER_ACTION`（预言家行动）→ stage 8 播 `seer_open`（预言家请睁眼）→ stage 9 跑 `NIGHT_WITCH_ACTION`（女巫行动）。
于是观众先听到「女巫请睁眼」，再看到预言家的查验结果，**之后**才听到「预言家请睁眼」，最后才是女巫用药。两处旁白相对各自的行动点都错了一格。
另外 `director.narration(...)` 是引擎节拍，**不看存活**：`e6e55110` 第 2 夜女巫已死于第 1 夜、第 3 夜预言家已死于第 2 夜，仍照播「女巫请睁眼」「预言家请睁眼」；`29f942fc` 第 6 夜（audience seq 261-263）同样：女巫与预言家均已阵亡，仍播「女巫请睁眼」「预言家请睁眼」。角色自带 `opening_narration` 的路径（`_narrate_role_openings`，`:530-556`）反而是正确的（石像鬼被封印的第 2 夜确实没有「猎魔人请睁眼」）。
影响：仅观感与回放可信度，不改变对局；但会让「死了的人还在开眼」出现在上帝视角里。

### B2【中】`VOTE_RESULT.counts` 是未加权人头数，警长 1.5 票在展示票型里不可见

`app/services/game_service.py:1747` 直接 `counts[target] += 1`，而放逐判定用 `app/core/sheriff_flow.py:127 ballot_weight`（警长 3 / 其他人 2 的半票单位）。本批 6 局的实际放逐与加权票**逐局复算一致**，但存在「展示票型看起来平票、实际有一方多 0.5 票而胜出」的现实风险。
（另注：本批 `e6e55110` 第 2 回合 `counts {"2":1,"3":8}` 这类展示值即为未加权人头数。）

### B3【中】舞池可选名单与 `TARGET_SELECTION_RULE` 自相矛盾（`292fcca1`）

`dancer.py:46-58` 的 `_eligible_seats`（排除已入池者）只用于 `is_applicable`，**没有投影进 `PROJECTED_CONTEXT`**：第 4 夜的上下文仍给 `alive_seats=[1,4,6,7,8,9]` 而 `pooled_seats=[4,8,11]`。模型若照规则从名单里选到已入池座位，会被 `pool_repeat_forbidden` 连拒两次 → 同样降级 `pass`。本局因该夜调用先失败而未触发。

### B4【中】角色 `instructions` 与自身实现/文档直接矛盾（文本经 `<public_role_rules>` 送达模型）

| 位置 | 提示词写的 | 实现/文档 |
| --- | --- | --- |
| `app/roles/wolf_king.py:152` | 「被带走的猎人仍然可以开枪反击」 | `app/roles/hunter.py:25` `_SHOOT_REASONS={"wolf_kill","exile"}` 明确排除 `wolf_king_shot`；CLAUDE.md「被带走的猎人不能开枪」 |
| `app/roles/hunter.py:124-131` | 「被猎人或被白狼王自爆带走时触发开枪权」「即使被自爆带走也能开枪反击」 | 同一文件 `:25` 的 `_SHOOT_REASONS` 只认 `wolf_kill` / `exile`；`09982d7c` 中该句进入全部 12 名玩家的 73 条提示词 |
| `app/roles/witch.py:159-169` | 「saving yourself is allowed only on the first night」 | 本批 6 局中 4 局配置 `witch_self_save="never"`（首夜也不可自救）。权威值确实注入了 `PROJECTED_CONTEXT.witch_self_save`，因此是**自相矛盾**而非缺失，但违反「只允许提示里写明的规则」 |

### B5【低】警长提示词对隐狼说「可自爆吞警徽」

`app/core/sheriff_flow.py:419` 用阵营门禁（`if wolf:`）追加「也可自爆吞警徽（只死自己）」，而 `:462-465 _may_explode` 用狼窝门禁 `in_wolf_pack`。石像鬼（`no_wolf_pack`）因此在自己的竞选/警长投票提示词里看到与 `ROLE_CONTRACT`「你不能自爆，也不能吞警徽」相反的句子（`29f942fc` 实证）。可执行面未破（schema 枚举里没有 `explode`），属提示词层规则误述。

### B6【低】平票复投不限制为并列候选人

`app/core/vote_service.py:74-75` 的目标集合永远是「全体存活 − `exile_immune`」，从不读 `tiebreak_candidates`；`app/agents/prompt_builder.py:529-531` 明写「复投可投任意存活座位」。与本项目 `docs/gameplay.md`「平票处理」一致，但与通行规则（PK 复投只投并列候选人）不同——对照警长 PK 反而做了限制（`game_engine.py:1112` `legal=set(candidates)`）。本批 6 局均未触发复投。

### B7【低】其它观测

- 舞者「不可放弃」无任何机器强制：`pass` 是契约合法动作，`validate_dance_pool` 放行；模型主动 `pass` 与失败降级 `pass` 只能靠 reasoning 文案区分。
- 首夜引擎会向噩梦之影 / 狼队 / 预言家注入 `RANDOM_HINT=<随机合法目标>`（`game_service.py:2511-2534, 2579-2582`）。`09982d7c` 中三项首夜决定**全部**等于注入值（恐惧 5 / 刀 4 / 验 9，狼队发言原文「参考提示我建议首夜刀4号」）。此机制未见于 `docs/gameplay.md`。
- `witch_self_save` 被写进 `GAME_INITIALIZED` 领域事件，却被 `audience_projector.py` 的 `_PUBLIC_CONFIG_FIELDS`（仅 3 键）丢弃 → 上帝视角/回放看不到该规则设置（只在 PROMPT 里可见）。
- `agents/game_rules.py:10-18` 的中文 `TARGET_SELECTION_RULE` 被 `:22-32` 英文版覆盖成死代码，且旧版把 `RANDOM_HINT` 说成「从 0 开始的索引」，与实现（座位号）相反。

---

## 三、明确核对无误（正面结论）

以下项目在 6 局中逐条复算/核对，**未发现违规**：

- **屠边与「狼刀在先」**：6 局按 `rule_engine.check_win` 的真实判定顺序（先 `alive_gods==0` → 再 `alive_villagers==0` → 再狼数>好人数 → 最后 `alive_wolves==0`）逐步复算，首次满足的时刻与理由与记录**完全一致**，含 `ca023cb0` 同一夜「最后神职与最后狼同批死亡」时判狼胜的仲裁。
- **发言顺序**：6 局 × 每轮（含警长 `sheriff_left/right`、`death_left/right`、无警长时以 `death_history[-1]` 为锚的三种分支）与 `speech_order()` 逐字吻合；无死者发言。
- **投票**：投票人始终存活、目标始终存活、无自投；加权票型（警长 3:2 半票）与实际放逐结果 6 局全部一致；`technical_abstain` 均为模型超时。
- **死因门禁**：猎人 / 狼美人 / 狼王的可发动死因集合与实现一致（本批只在实际触发的场合产生后果，见 A3）。
- **赤月使徒封印**：`e6e55110` 第 1 天自爆后，第 2 夜预言家（存活）与猎魔人（已解锁）**确实**未行动，第 3 夜恢复；封印集合恰为当时全部存活好人（7 席）。
- **舞池裁决**：`1473dff7` 第 2 夜池 `[2,3,8]` = 1 狼 2 好 → 少数方（狼 2 号）出局；`dance_veil` 只挡狼刀（该夜狼刀目标 8 号被挡成平安夜）、不挡毒（`292fcca1` 11 号在池内仍被毒死）。
- **摄梦人**：`ca023cb0` 无连摄，11 号 `dream_death` 来自「摄梦人夜死同亡」（`_DREAM_REVENGE_REASONS`），同批去重正确、护盾未抵消；`09982d7c` 梦游者免疫档位用仓库真实 `settle()` 实跑验证（`poison+dream → 不死`、`poison+guard → 死`、`guard+antidote 对穿 → 死`）。
- **石像鬼不住狼窝**：`29f942fc` 全程不在 `WOLF_CHAT_MESSAGE` / `WOLF_VOTE`，狼队名单与狼频道不含它，它也不含狼队；`role_checks` 确实进了自己的提示词。
- **女巫**：解药只救当夜刀口、一局一次、`witch_self_save` 由校验器强制；毒药目标存活、一局一次。
- **警长流程**：上警 / 退水 / 仅未上警者可投票 / 唯一多数当选 / 1.5 票实证 / 交徽与撕徽均为合法选项；`sheriff_ballot` 记录完整；警长 reasoning 不进玩家提示词。
- **状态清扫 / 隐私边界**：终局 `runtime.statuses` 全空、`pending_damage`/`pending_protection` 全空；`silenced` 不在 `_PUBLIC_STATUSES`，未进入任何玩家提示词（被封印者不知情）；`domain → audience` 事件映射无丢失。
- **白痴 / 假面（源码核实）**：白痴翻牌路径本批未触发（3 名白痴均未被放逐）；假面 `mask_grant` 未触发（相关局中假面过早出局），仅源码层面核对通过。

---

## 四、修复优先级建议

1. **A1**（遗言兜底泄露身份）—— 改动最小、影响面最大（77 局 / 49 局泄露神职），建议立刻修。
2. **A2**（石像鬼带刀死代码）—— 把 `_command_provider` 的早退判据从「调度点」改成「契约 id（`werewolf_kill`）」，并补一条走真实 `_command_provider` 的端到端测试。
3. **A3**（故障=放弃）—— 至少让 `per_game_limit=1` 与死亡响应窗口在 provider 异常时进入可恢复暂停，并停止用「系统异常」冒充玩家理由、停止把降级动作计入 `action_counts`。
4. **A4**（连续两晚状态不清除）—— 跳过的夜晚应把 `last_*` 置空（或改为记录「上次生效的回合号」并比较 `round_number` 差 1）。
5. **B1/B2/B4/B5** —— 旁白时机与存活门禁、`counts` 改加权、纠正三处 `instructions`、竞选提示词改用狼窝门禁。

---

## 附：审计材料

- 各局完整可读时间轴：`/tmp/wk-audit2/transcripts/<前缀>.txt`
- 机械检查脚本：`/tmp/wk-audit2/{lib,check,check2,check3,check4}.py`（对 6 局均 0 findings，即上述 A 类均非机械不变量能捕获）
- 逐局详细报告：`/tmp/wk-audit2/findings/{29f942fc,09982d7c,1473dff7,292fcca1,ca023cb0,LEAD}.md`
