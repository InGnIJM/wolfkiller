# 早期自制立绘备份（历史）

这 10 张是本项目**第一批**自制立绘（AI 生成 + 手改，2026-10），
在 2026-10-21 全量重绘之前留档，**不再被任何代码引用**。

## 现在的立绘策略

- 活动目录 `../roles/` 从 2026-10-21 起存放 **40 张自制立绘 + 卡背**，并**随仓库分发**。
- 网易官方立绘已退出仓库，`scripts/fetch-official-role-art.py` 降级为遗留参考脚本
  （`--force` 会用官方图覆盖现成自制图，不要在发布分支运行）。
- `../roles/role-*.webp` 不再是本地资产：取图唯一入口仍是
  `frontend/src/theme/tokens.ts` 的 `ROLE_ART_BY_ID`，缺图时组件回退卡背。

## 这份备份有什么用

- 立绘风格演进的过程记录，也是重绘时的风格参考；
- 若某张新图需要回退到旧版，可以把对应文件复制回 `../roles/`（会被 git 跟踪，
  记得同时确认 `ROLE_ART_BY_ID` 已登记该角色）。

文件名与活动目录**基本同名**，但 `role-werewolf_king` / `role-wolf_beauty` 用下划线，
活动目录多数用连字符（`role-werewolf-king.webp` / `role-wolf-beauty.webp`），
例外是 `role-old_drunkard.webp` 用下划线且已按原名登记进 `ROLE_ART_BY_ID`；还原时请按登记名处理。
卡背 `../roles/card-back.webp` 与老酒鬼在重绘前就是自有的，因此不在这份备份里。

```bash
# 仅作历史参考；正常开发不需要执行
cp frontend/src/assets/roles-original/role-seer.webp frontend/src/assets/roles/role-seer.webp
```

## 清单

| 文件 | 角色 |
| --- | --- |
| `role-werewolf.webp` | 狼人 |
| `role-villager.webp` | 平民 |
| `role-seer.webp` | 预言家 |
| `role-witch.webp` | 女巫 |
| `role-hunter.webp` | 猎人 |
| `role-guard.webp` | 守卫 |
| `role-idiot.webp` | 白痴（官方牌名「愚者」） |
| `role-werewolf_king.webp` | 白狼王 |
| `role-knight.webp` | 骑士 |
| `role-wolf_beauty.webp` | 狼美人 |

尺寸统一 768×1024（3:4），与卡背一致，所以 `RolePortrait` 的取景规则对两者都成立。
