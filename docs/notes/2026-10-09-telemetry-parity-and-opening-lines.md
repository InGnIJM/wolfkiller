# 遥测对齐与开眼台词归属

日期：2026-10-09 · 范围：`backend/app/services/game_service.py`、`backend/app/core/game_engine.py`、
`backend/app/roles/{seer,pure_white,wolf_witch}.py`

上一轮"还有什么没修"清单里剩下的两条，各自独立。

## 1. `summary.json` 漏记通用流水线的模型调用

**症状**：`summary.json` 的 `llm.calls` / `tokens` / `errors` 比耐久账本少。

**根因**：`summary.json` 的 `llm.*` 只读 `games/<id>/llm_calls.log`，而**通用角色流水线的模型调用从不写这份
日志** —— 它们只落在耐久账本里（`model_requests` / `model_attempts`，`action_position` 是一串 64 位 digest，
没有可读 key）。实测最新一局：

```
耐久账本   135 次调用 / 1,045,787 tokens（usage_known 135/135）
summary    129 次调用 / 1,003,621 tokens
差         6 次（4.4%）/ 42,166 tokens（4.0%）
```

差的正好是守卫×3、预言家×1、女巫×2 的夜间技能调用；`calls_by_kind` / `errors` / `fallbacks` / `retried`
一并漏掉。失败调用**不受影响**（`_command_provider` 会写 `model_errors.log`，summary 的 `model_errors`
统计的是它），漏的是成功调用与它们的 token。

**修法**：把 `_log_night_llm_telemetry` 提升为两条路径共用的 `_log_llm_telemetry`（多两个默认参数
`call_kind` / `transport`，狼频道三处调用点形状不变），流水线的 `_command_provider` 在梯子结束后写一行：
`call_kind="action"`、`contract_id=<契约 id>`、`transport="action_tool"`、`attempts=梯子级数`、
`usage=各级求和`、失败时 `parse_result="error"` + `failure_code`（共用分类器 `_model_failure_code`）。
stub 引擎没有 `game_logger` 时跳过（`getattr`），不影响既有 provider 单测。

**门禁**：`test_provider_records_one_llm_call_row` / `test_provider_records_a_failed_llm_call_row`
（走真实 `_command_provider` + 真实 `GameLogger`，断言 `call_kind` / `contract_id` / token / 失败码 /
`retried`）。历史局已写出的 `summary.json` 不回溯重算。

## 2. 共享调度点上的台词不属于在场的角色

**症状**：纯白夜影板（没有预言家）的观众流里，开眼旁白是「预言家请睁眼，请查验一名玩家的身份」。
2026-10-07 复盘 3.3 的实测：113 个夜晚里播了 93 次。

**根因**：`NIGHT_SEER_ACTION` 与 `NIGHT_WITCH_ACTION` 都是**共享**调度点，而 stage 6 / stage 8 播的是
引擎硬编码的节拍（`_NARRATIONS["seer_open"]` / `director.witch_narration(...)`）；门禁
（`_night_openers`：在该点有契约 且 存活 且 未被封印）是对的，错的是"谁在这个点开眼"被写死成了某一个
角色的台词。

**全量审计（冻结注册表）** —— 哪些点由多个角色共用：

| 调度点 | 在场的角色 | 处理 |
| --- | --- | --- |
| `night_action` | 守卫 / 舞者 / 假面 / 噩梦之影 / 摄梦人 / 猎魔人 | 引擎本来就不播节拍，按 `PointResult.requests` 逐角色播声明台词 ✓ |
| `night_seer_action` | 预言家 / 纯白之女 / 狼巫 / **石像鬼** | 四个都补上声明台词；`seer_open` 降为兜底 |
| `night_witch_action` | 女巫 / **狼美人** | 狼美人补声明台词；女巫那句依赖当晚刀口，仍由节拍承担 |
| `night_wolf_vote` | 狼窝各角色（+ 不属于狼窝的隐狼，它们不参加讨论） | 讨论台词只属于狼窝 ✓ 与在场者一致 |
| `dawn_reaction` / `day_action` / `exile_verdict` | 猎人 / 狼美人 / 摄梦人 / 狼王 / 骑士 / 赤月使徒 / 白痴 | 这些窗口**没有**旁白节拍 ✓ 无此问题 |

**修法**（`_narrate_declared_openers` 返回「未声明者数量」）：

- 先播在该点有契约的座位各自声明的台词；
- **节拍只属于没声明台词的角色**：有未声明者在场才播（返回 `None` = 拿不到注册表 → 照旧播，绝不静音）。
  这样女巫（不声明、节拍带刀口信息）保住自己的台词，声明了台词的角色永远听不到别人的台词；
- 新增声明：`gargoyle`（石像鬼请睁眼）、`wolf_beauty`（狼美人请睁眼）；`hidden_wolf_role_spec` 增加
  `opening_narration` 参数。

**影响**：15 个预设里每一个共享点现在都由在场角色自己发声（`night_seer_action` 上「有预言家 → 预言家
台词、纯白夜影 → 纯白/狼巫台词」；`night_witch_action` 上「有女巫 → 女巫节拍（逐字不变）、狼美人额外
一句自己的台词」）。自定义板子（例如只有石像鬼或只有狼美人）不再听到别人的台词。

**门禁**：`test_every_shared_point_speaks_through_its_own_roles`（四种板子逐项断言该出现的台词出现、
引擎节拍不插话）、`test_the_identity_check_line_comes_from_whoever_acts_there`、
`test_a_role_that_declares_no_line_keeps_the_engine_beat`（兜底路径）、
`test_staged_night_logs_full_operation_order`（拿不到注册表时保留节拍）、
`tests/test_night_script.py` 的声明清单。

## 附：审计中发现的相邻项（未修，待定）

`wolf_open`（"天黑请闭眼 / 狼人请睁眼，开始讨论今晚的行动。"）在 stage 2 **无条件**播出，而讨论只在
「存活狼窝 > 1 且未被封印」时进行。近 25 局 42 个夜晚里没有一例触发（封印旁白在近 60 局里 0 次），
属**潜在**问题；真触发时观众会看到「开始讨论」但没有任何狼聊。修它需要一个文案决策（无讨论时播什么），
故先记录。

**注**：`RoleSpec` 声明变化会改变 `registry.digest`，只影响新局（旧局按建局时的冻结 digest 走既有
兼容性裁决续跑）。
