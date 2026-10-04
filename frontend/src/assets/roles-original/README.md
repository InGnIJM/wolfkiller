# 自制立绘备份

这 10 张是本项目**自己的**立绘（AI 生成 + 手改），2026-10 换成官方立绘时从这里保留下来。

## 为什么留着

官方立绘是网易的美术资产，只适合**本地自用**。要公开发布或分发时，必须换回自有素材
—— 这一份就是那个退路。后续重新生成 AI 立绘时也可以拿它当风格基准（卡背
`../roles/card-back.webp` 与老酒鬼 `../roles/role-old_drunkard.webp` 一直是自有的，
从未被替换，所以不在这份备份里）。

## 怎么还原

文件名与 `../roles/` 下的**活动立绘同名**（注意 `role-werewolf_king` 与
`role-wolf_beauty` 用下划线，官方那批用连字符，还原时别改错），直接覆盖即可：

```bash
cp frontend/src/assets/roles-original/*.webp frontend/src/assets/roles/
```

`../roles/role-*.webp` 在 `.gitignore` 里，覆盖进去不会被提交 —— 这正是想要的：
官方图与自制图都不入库，仓库里只留这份备份与 `card-back` / `role-old_drunkard`
两张必须随仓库发布的图。

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
