# 旁观者 REST 数据隔离设计

## 目标

在未认证的旁观者模式下，`GET /api/games/{game_id}` 与
`GET /api/games/{game_id}/logs` 只返回公开游戏事实。角色、阵营、道具、夜间行动和模型私有内容均不得返回；玩家死亡或游戏结束后也不例外。

## 已确认边界

- 当前产品是旁观者模式，不引入座位令牌、登录、玩家私有 REST 视图或狼人频道。
- 不存在的 `game_id` 在详情和日志接口均返回 404。路由先通过
  `GameService.get_game_state(game_id)` 确认该局存在，再读取日志文件。
- 详情响应是由 `GameState.get_public_state()` 的已知字段构造的封闭 DTO，不能直接序列化 `GameState`、`PlayerState`、`__dict__` 或存档字典。
- 日志响应是公开回放事件的白名单投影，而非原始 `conversation.log`、`game.log` 的转发。
- 旁观者页面不得再通过 `role_init`、`speaker_role`、`visible_to`、夜间操作或死亡/结算推断身份。

## 公开详情契约

`backend/app/models/game.py` 已提供 `GameState.get_public_state()`。本次只消费并显式建模其中的公开信息：

```json
{
  "game_id": "…",
  "phase": "speech",
  "round_number": 2,
  "players": {
    "1": {"seat_number": 1, "is_alive": true, "is_sheriff": false}
  },
  "sheriff": null,
  "speeches": [{"player_seat": 1, "text": "…", "round_number": 2}],
  "death_history": [{"player_seat": 2, "cause": "wolf_kill", "round_number": 1}],
  "win_result": null
}
```

`backend/app/api/schemas.py` 中的 `GameDetailResponse` 及其嵌套模型是 wire contract。`backend/app/api/routes/game_routes.py` 必须逐字段从 `get_public_state()` 取值构造它；未知字段默认不透传。

以下结构化信息禁止出现在该 DTO 的任意层：`role`、`camp`、`role_id`、药剂、枪、狼人队友、夜间目标、验人结果、行动参数、推理和 thought。公开发言文本本身是用户可见内容，玩家在文本中自称某个角色不构成服务端泄露；过滤器不应篡改公开发言。

## 公开回放契约

日志接口改为 `{ "game_id": "…", "events": [...] }`。每条事件均由 allowlist 映射生成，字段只包含以下公开事实：

| 原始记录 | 公开事件 | 保留字段 |
| --- | --- | --- |
| `conversation.log` 且 `scope == "public"` | `speech` | `timestamp`、`round_number`、`phase`、`speaker_seat`、`content` |
| `game.log` 的 `phase_change` | `phase` | `timestamp`、`round_number`、`phase` |
| `game.log` 的 `vote` | `vote` | `timestamp`、`round_number`、`voter_seat`、`target_seat` |
| `game.log` 的 `vote_result` | `vote_result` | `timestamp`、`round_number`、`exiled_seat` |
| `game.log` 的 `night_deaths` | `death`（每个死亡一条） | `timestamp`、`round_number`、`player_seat`、`cause` |
| `game.log` 的 `game_over` | `winner` | `timestamp`、`round_number`、`winning_camp`、`reason` |

`role_init`、`werewolf_kill`、`witch_save`、`witch_poison`、`seer_check`、`hunter_death`、`hunter_shoot` 及任何未知 operation 一律丢弃。`werewolf`、`night_intel`、`thought` 等非 public conversation 一律丢弃。投影器不从其他字段补全、猜测或递归复制数据。

## 前端迁移

实际消费链路为：

```text
frontend/src/api/client.ts
  -> frontend/src/components/game/GameBoard.tsx
  -> frontend/src/store/gameStore.ts
  -> frontend/src/store/types.ts
```

实时夜间消息还经过 `frontend/src/api/websocket.ts`。它只接受 `step` 与 `round_number`，不再读取 `highlight_seats`、`action_seat`、`action` 或 `wolf_kill_target`。

角色展示和私密回放消费位于 `frontend/src/components/game/PlayerCard.tsx`、`CenterDisplay.tsx`、`WinOverlay.tsx`，以及它们通过 `SeatMap.tsx` 接收的玩家类型。迁移后玩家卡只显示座位、存活状态、警长和公开投票；中心区只渲染上表中的公开事件；结算弹窗只显示胜负结果，不展示身份列表。

## 验收

1. 两个未知 `game_id` 请求均为 404，且不读取对应日志。
2. 详情 DTO 只含 `get_public_state()` 的公开字段，死亡和结算后仍无身份字段。
3. 日志只包含上述六类公开事件；私密与未知记录不出现。
4. 前端构建通过，且旁观者消费路径不含 `role_init`、角色、阵营、资源或私密夜间字段。
