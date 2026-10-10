# 回放对齐：狼聊长度契约与缺失的阶段事件

日期：2026-10-09 · 范围：`backend/app/api/schemas.py`、`backend/app/api/routes/game_routes.py`、
`backend/app/services/game_service.py`

首夜死讯改到竞选之后的那次排查里顺带发现的两条，单独修在这里（与主修复无耦合）。

## 1. 文件回放按 200 字静默丢弃狼聊

**症状**：`GET /api/games/{id}/logs` 返回的狼聊比真实内容少一截，且没有告警。

**根因**：两条公开回放路径对同一事件给了不同的契约。

| 路径 | 文本长度 | 表现 |
| --- | --- | --- |
| 观众投影（`/snapshot` + `/events`，前端主路径） | 无上限（`AudienceEventResponse.payload` 是开放 dict） | 原样返回，DB 里最长 382 字 |
| 文件回放（`/logs`） | `game_routes.py` 投影时 `len(text) > 200` 丢行，`PublicWolfChatMessageResponse.text` 也写 `max_length=200` | 静默丢弃 |

实测：近 40 局 477 条狼聊里 **188 条（39%）超过 200 字**，单局最多丢 12/15。库里 209 局全部有观众
投影，所以前端当前不会走文件回放 —— 但 `/logs` 是公开接口，任何消费方（外部工具、无投影的档案）
拿到的都是残缺狼聊。

**修法**：两端一起放开（`PublicWolfChatMessageResponse` / `PublicWitchThoughtResponse` /
`PublicSeerThoughtResponse` 的 `text` 去掉 `max_length`，投影器去掉 `len(text) > 200`），保留
「座位为正整数 + 非空字符串」。**只改一端会把静默丢弃变成 500**，所以两端必须同步。

**门禁**：`test_replay_keeps_a_long_wolf_chat_message`（400 字狼聊穿过投影器并进入 `GameLogsResponse`）；
`tests/test_schemas.py` 改为断言 400 字被接受。实测复核：最新一局 `/logs` 的狼聊条数 10 → **20/20**，
与观众流一致。

## 2. 观众流缺「第一夜」与「每夜天亮」的阶段事件

**症状**：`game.log` 每局有 21~28 条 `phase_change`，观众流只有 15~21 条 `phase`；第一夜整段阶段
标签停在「等待中」，每轮报死讯那一拍仍显示「黑夜」。

**根因**：`audience` 表只由 `GameService._checkpoint_domain_events` 的 label 分支写入，而两类阶段转换
没有对应的 label：

| 缺的行 | 为什么 |
| --- | --- |
| `(0, role_deal)`、`(0, night)` | 开局两次转换用 `_broadcast_phase_change(checkpoint=False)`（身份还没发，故意不落检查点） |
| `(1, sheriff_election)`、`(2..N, dawn)`、`(末轮, game_over)` | 转换发生在夜晚收尾路径，耐久标签是 `night_complete:`，而该分支只映射胜局的 `GAME_OVER`，其余落成非公开的 `STEP_COMMITTED` |

**修法**：

1. `game_initialized` 步骤返回两行（`GAME_INITIALIZED` + `PHASE_CHANGED(phase=night)`），事件 id 变成
   `domain:{id}:0/1`（与其它多事件步骤同款；audience 表按 `(game_id, event_id)` 去重，新局无冲突）；
2. `night_complete:` 分支在非胜局时产出 `PHASE_CHANGED`（胜局仍走 `GAME_OVER`）。

**门禁**：`tests/test_game_service_lifecycle.py` 的 label 矩阵（开局两行 + `night_complete` 非胜局的
阶段行）。投影复核（`AudienceProjector.project_events`）：`game_initialized → phase(night)`、
`night_complete:1:sheriff_election → phase(sheriff_election)`、`night_complete:1:dawn → phase(dawn)`。

**影响**：前端无需改动 —— 竞选窗口的补偿（`gameStore` 在选举事件上开窗）继续有效，只是现在有了真正的
阶段行；`round_number` 在开局那一行为 0（与今天一致，narration 到达后显示第 1 轮）。
