# 首页对局列表管理（重命名 / 删除）设计

日期：2026-08-22
状态：已确认（分节确认完毕，待用户审阅书面规格）

## 1. 背景与目标

首页对局列表目前只能点进对局，卡片主标题是截断后的 `game_id`。对局多了之后无法辨认、也无法清掉失败或不要的存档。本期只做两件事：

1. **重命名**：给对局一个可改的显示名，ID / 目录 / `/game/:id` 不变。
2. **删除**：已结束和进行中都可以删；进行中先停引擎再清存档。

入口只在首页列表卡片上，不另开管理页。

## 2. 需求澄清结论（决策记录）

| # | 问题 | 决策 |
|---|---|---|
| 1 | 操作范围 | 只做重命名 + 删除 |
| 2 | 入口 | 首页对局列表，不另开管理页 |
| 3 | 进行中能否删 | 能；先停引擎再清存档 |
| 4 | 重命名语义 | 只改显示名；对局 ID、目录、回放 URL 不变 |
| 5 | 默认显示名 | 创建时自动生成，例如 `8人局 · 8月22日 21:50` |
| 6 | 删除确认 | 必须弹确认框；进行中额外提示会中断对局 |
| 7 | 实施方案 | 方案 A：在现有 `GameManifest` / `GameService` / 列表卡片上扩展 |

## 3. 非目标

- 停止但不删除、暂停、回收站 / 软删除
- 修改对局 ID 或移动目录
- 独立对局管理页
- 对局进行页 / 回放页标题改名（首页列表以外不改展示）
- 批量删除、搜索、排序、置顶、导出

## 4. 架构总览

```
后端
├── app/services/game_manifest.py   # 条目增加 name；update name；remove_game
├── app/services/game_service.py    # 创建时生成默认名；rename_game；delete_game
├── app/api/schemas.py              # GameListItem.name；RenameGameRequest
├── app/api/routes/game_routes.py   # PATCH /api/games/{id}；DELETE /api/games/{id}
└── app/api/websocket/ws_handler.py # WSManager.close_game(game_id)

前端
├── src/store/types.ts              # GameListItem.name
├── src/api/client.ts               # renameGame / deleteGame
├── src/components/lobby/GameCard.tsx
└── src/components/lobby/GameList.tsx
```

边界：显示名只活在 manifest（`data/games/index.json`），不写 `game.log`，不进入公开对局状态 DTO。引擎、角色流水线、五个核心 blob 模块零改动。

## 5. 数据模型

`index.json` 每条对局增加：

| 字段 | 类型 | 说明 |
|---|---|---|
| name | str | 显示名。去首尾空格后非空，最长 50 字，不要求唯一 |

其余字段不变。`game_id` 仍是目录名和路由主键。

**默认名**：创建时由后端生成

```
{player_count}人局 · {M}月{D}日 {HH}:{mm}
```

时区固定 `Asia/Shanghai`。月、日不补零，时分补零。例：8 人、2026-08-22 21:50 → `8人局 · 8月22日 21:50`。生成一次后当普通字符串存，不随时间再生。

**旧档**：没有 `name` 或为空时，`GET /api/games` 回退为 `game_id` 前 8 位。回退值只出现在列表响应里，不回写磁盘，直到用户主动重命名。

## 6. 接口

### 列表

`GET /api/games` 的 `GameListItem` 增加必填 `name: str`（已含旧档回退值）。

### 重命名

```
PATCH /api/games/{game_id}
Content-Type: application/json
{ "name": "自定义名字" }
```

- 校验与模型配置名相同：strip 后非空、`min_length=1`、`max_length=50`
- 成功 200，返回更新后的 `GameListItem`（含最新 `name`）
- 找不到 404
- 校验失败 400
- 进行中、已结束、异常终止均可改名
- 重名允许；并发以后写为准

### 删除

```
DELETE /api/games/{game_id}
```

- 成功 204
- 找不到 404

**删除顺序（`GameService.delete_game`）**

1. 对局不在内存中 → 视为不存在，404。
2. 若有引擎：调用已有 `GameEngine.stop()`，取消对应 `asyncio.Task`，最多等 5 秒。超时只记日志，继续拆。
3. `WSManager.close_game(game_id)`：关闭该局全部 WebSocket 并从连接表移除。
4. 删除 `data/games/{game_id}/` 目录。目录删除失败 → **不**动内存和 manifest，返回 500，列表保持原样，可重试。
5. 从 `_games` / `_engines` / `_tasks` / `_model_snapshots` 移除，并 `GameManifest.remove_game`。
6. 返回 204。

正在观看该局的人：WebSocket 断开后，现有 `GameNotFoundError`（「对局不存在或已失效」）继续负责。

`GameManifest.remove_game`：从 `_entries` 去掉该 id 并 `_persist()`。磁盘目录已不存在时，现有 `load_or_rebuild` 会丢掉无目录的陈旧条目，作为重启后的兜底。

## 7. 前端交互

只改首页列表。

- 卡片主标题改为 `name`，不再用短 ID 当主标题。
- 卡片右侧 ⋮ 菜单两项：重命名、删除。点击 ⋮ / 菜单**不得**触发进入对局（`CardActionArea` 需 `stopPropagation`，必要时拦 `onMouseDown`）。
- **重命名**：对话框预填当前名；确认后 `PATCH`；失败留在对话框并展示原因。
- **删除**：确认文案「确定删除「{name}」？此操作不可恢复。」`phase` 不是 `game_over` 且不是 `error` 时额外一句「对局正在进行，删除将立即中断。」确认后 `DELETE`，成功后从本地列表移除。
- 3 秒轮询保留；改名 / 删除成功后立刻更新本地列表，不等下一轮。
- 列表拿到 404（该项已被别处删掉）时刷新列表即可。

不新增 Zustand store：列表仍由 `GameList` 本地 state 持有。

## 8. 错误处理

| 场景 | 行为 |
|---|---|
| 名为空 / 全空格 / 超长 | 400，对话框不关，展示原因 |
| 对局已不存在 | 404；列表刷新去掉该项；回放页走现有失效提示 |
| 删目录失败 | 500，内存与 manifest 不动，列表不变 |
| 引擎取消超时 | 记日志，继续关 WS、删目录、清内存 |
| 显示名重复 | 允许 |

不做回收站。删除不可恢复。

## 9. 测试

不改 `game_engine.py` / `action_validator.py` / `action_resolver.py` / `prompt_builder.py` / `state_filter.py`，守卫 blob 门禁不动。

**后端**

- `GameManifest`：写入 / 更新 `name`；`remove_game`；缺名字不回写
- `GameService`：创建带默认名（上海时区格式）；改名；删已结束（目录与索引都没了）；删进行中（任务被取消、目录没了）；删不存在报错；目录删除失败时内存仍在
- 路由：`PATCH` 200/400/404；`DELETE` 204/404；`GET` 列表含 `name`；旧档回退短 ID

**前端**

- 卡片展示 `name`
- ⋮ 不触发 `onJoinGame`
- 重命名成功后标题更新；校验失败不关对话框
- 删除确认：进行中有额外提示；成功后该项从列表消失
- `client.ts` 覆盖 `PATCH` / `DELETE`

## 10. 实现顺序

1. Manifest `name` + `remove_game` 及测试
2. `GameService` 默认名 / `rename_game` / `delete_game` 及测试
3. `WSManager.close_game` + 路由 + schema
4. 前端类型、client、GameCard / GameList 交互与测试
