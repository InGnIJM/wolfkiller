# 角色立绘盘点（2026-10-20）

> **2026-10-21 更新（已完成）**：40 个角色已全部补齐统一自制立绘，均为 **768×1024 WebP**；
> 活动目录 `frontend/src/assets/roles/` 现有 **40 张角色图 + 卡背**，`.gitignore` 已取消对
> `role-*.webp` 的忽略，`ROLE_ART_BY_ID` 已登记全部 40 个角色。`scripts/fetch-official-role-art.py`
> 不再需要运行。下文「29 官方 / 11 缺图 / 干净克隆 1 张」是 2026-10-20 的历史盘点，保留作过程记录。

盘点对象：完整角色目录 **40 个**（含愚言家、铁好人、乌鸦、丘比特、暗恋者这 5 个新角色）。
当前提交的 `frontend/src/components/shared/roleMeta.ts` / 后端 `ROLE_METADATA` 可能只列出
其中 35 个（这 5 个模块还在工作区、尚未稳定接入），所以本文同时给出两种口径：

- **当前 35 角色口径**：当前目录能看到的角色。
- **完整 40 角色目标**：5 个新角色全部并入后的目标状态。

素材目录：`frontend/src/assets/roles/`（活动立绘，`role-*.webp` 被 `.gitignore` 忽略）
与 `frontend/src/assets/roles-original/`（自制备份，入库）。

> 一句话结论：**自有立绘只有 11 张**（10 张 `roles-original` 备份 + 老酒鬼）；
> 28 个角色当前用的是官方图；**11 个角色完全没有图**（其中 6 个已在当前目录、5 个待接入）。
> 补齐当前 35 个角色最少要新生成 **24 张**；补齐完整 40 个角色最少要新生成 **29 张**。

## 一、计数与口径

| 口径 | 当前 35 角色 | 完整 40 角色 |
| --- | ---: | ---: |
| 已实现角色 | 35 | 40 |
| 当前本地有立绘 | 29 | 29 |
| 当前只有卡背 | 6 | 11 |
| 磁盘上的官方立绘文件 | 29 | 29 |
| 自有立绘库存 | 11 | 11 |
| 只有官方图、没有自有图 | 18 | 18 |
| 完全没有立绘 | 6 | 11 |
| 补齐所需最少新生成量 | 24 | 29 |
| 干净克隆实际可见立绘 | 1 | 1 |

说明：

- “当前本地有立绘 29” = 28 张官方 + 老酒鬼（自制、已入库）。
- “磁盘上的官方立绘文件 29” = 28 张对应已实现角色 + 1 张孤儿「觉醒狼王」。
- “自有立绘库存 11” = 10 张 `roles-original` 备份 + `role-old_drunkard.webp`。
- 5 个待接入角色（愚言家、铁好人、乌鸦、丘比特、暗恋者）全部没有立绘；当前目录接入后，
  只有卡背的角色从 6 变 11，最少新生成量从 24 变 29。

### 为什么干净克隆只有 1 张

`frontend/src/theme/tokens.ts` 用 `import.meta.glob('../assets/roles/role-*.webp')`
取活动目录；`roles-original/` 只用于备份，**没有任何代码引用它**。而 `.gitignore`
把 `frontend/src/assets/roles/role-*.webp` 全部忽略，只放行两张：

```text
frontend/src/assets/roles/card-back.webp
frontend/src/assets/roles/role-old_drunkard.webp
```

所以：

- **本地工作区**（跑过 `scripts/fetch-official-role-art.py`）：29 个角色有立绘。
- **干净克隆 / 发布包**：只有老酒鬼 1 张角色立绘，其余 39 个角色都是卡背。
  这不是降级 bug，是当前“官方图只本地自用 + 自有图只备份不入活动目录”的设计结果；
  但它意味着**发布版离“完整 40 个角色都有立绘”还差 39 张**，不是差 11 张。

## 二、已有自有立绘（11 个角色，可复用）

这 11 个角色已经有本项目自己的立绘，其中 10 张在 `roles-original/` 备份里，当前活动目录
里覆盖的是官方图；老酒鬼是唯一一张“自制且已入库、正在使用”的图。

| 角色 | 角色 ID | 当前素材 | 自有素材 | 建议文件 | 视觉母题 |
| --- | --- | --- | --- | --- | --- |
| 狼人 | `wolf-killer-werewolf` | 官方 `role-werewolf.webp` | `role-werewolf.webp` | `role-werewolf.webp` | 狼耳兜帽、血月、利爪 |
| 女巫 | `wolf-killer-witch` | 官方 `role-witch.webp` | `role-witch.webp` | `role-witch.webp` | 药瓶、紫焰、黑纱 |
| 预言家 | `wolf-killer-seer` | 官方 `role-seer.webp` | `role-seer.webp` | `role-seer.webp` | 水晶球、星月、兜帽 |
| 猎人 | `wolf-killer-hunter` | 官方 `role-hunter.webp` | `role-hunter.webp` | `role-hunter.webp` | 猎枪、皮甲、火药 |
| 村民 | `wolf-killer-villager` | 官方 `role-villager.webp` | `role-villager.webp` | `role-villager.webp` | 粗布、提灯、草叉 |
| 守卫 | `wolf-killer-guard` | 官方 `role-guard.webp` | `role-guard.webp` | `role-guard.webp` | 重盾、青焰、重甲 |
| 白痴 | `wolf-killer-idiot` | 官方 `role-idiot.webp` | `role-idiot.webp` | `role-idiot.webp` | 破冠、铃铛、戏谑面具 |
| 白狼王 | `wolf-killer-werewolf-king` | 官方 `role-werewolf-king.webp` | `role-werewolf_king.webp`（下划线） | `role-werewolf-king.webp` | 白狼首、骨冠、血月 |
| 骑士 | `wolf-killer-knight` | 官方 `role-knight.webp` | `role-knight.webp` | `role-knight.webp` | 长剑、银甲、披风 |
| 狼美人 | `wolf-killer-wolf-beauty` | 官方 `role-wolf-beauty.webp` | `role-wolf_beauty.webp`（下划线） | `role-wolf-beauty.webp` | 红裙、狼影、蔷薇 |
| 老酒鬼 | `wolf-killer-old-drunkard` | 自制（已入库） | `role-old_drunkard.webp` | `role-old_drunkard.webp` | 酒壶、破帽、醉眼 |

**注意两个文件名陷阱**：白狼王与狼美人的备份用**下划线**
（`role-werewolf_king.webp`、`role-wolf_beauty.webp`），活动图与 `ROLE_ART_BY_ID`
用**连字符**。`roles-original/README.md` 里的 `cp .../*.webp ../roles/` 对这两张
**不会覆盖官方图**，只会多出两个没人引用的文件。恢复这两张时必须改名。

## 三、只有官方立绘、没有自有图（18 个角色，需替换）

这 18 个角色当前能显示官方图，但官方图是网易美术资产，公开发布/分发时不能使用；
它们没有 `roles-original/` 备份，因此需要新生成。

| 角色 | 角色 ID | 当前素材 | 自有素材 | 建议文件 | 视觉母题 |
| --- | --- | --- | --- | --- | --- |
| 狼王 | `wolf-killer-wolf-king` | 官方 `role-wolf-king.webp` | — | `role-wolf-king.webp` | 狼首王冠、巨爪、黑金 |
| 恶夜骑士 | `wolf-killer-evil-knight` | 官方 `role-evil-knight.webp` | — | `role-evil-knight.webp` | 黑甲、裂盔、暗红披风 |
| 摄梦人 | `wolf-killer-dream-weaver` | 官方 `role-dream-weaver.webp` | — | `role-dream-weaver.webp` | 梦蝶、纱帐、月光 |
| 石像鬼 | `wolf-killer-gargoyle` | 官方 `role-gargoyle.webp` | — | `role-gargoyle.webp` | 石翼、裂纹、教堂 |
| 守墓人 | `wolf-killer-gravekeeper` | 官方 `role-gravekeeper.webp` | — | `role-gravekeeper.webp` | 铁锹、墓碑、提灯 |
| 孤独少女 | `wolf-killer-lonely-girl` | 官方 `role-lonely-girl.webp` | — | `role-lonely-girl.webp` | 孤灯、玩偶、雨夜 |
| 纯白之女 | `wolf-killer-pure-white` | 官方 `role-pure-white.webp` | — | `role-pure-white.webp` | 白纱、圣光、银剑 |
| 狼巫 | `wolf-killer-wolf-witch` | 官方 `role-wolf-witch.webp` | — | `role-wolf-witch.webp` | 骨杖、图腾、狼血 |
| 赤月使徒 | `wolf-killer-crimson-envoy` | 官方 `role-crimson-envoy.webp` | — | `role-crimson-envoy.webp` | 红月、祭袍、锁链 |
| 噩梦之影 | `wolf-killer-nightmare` | 官方 `role-nightmare.webp` | — | `role-nightmare.webp` | 黑影、噩梦触手、碎镜 |
| 猎魔人 | `wolf-killer-demon-hunter` | 官方 `role-demon-hunter.webp` | — | `role-demon-hunter.webp` | 十字弩、银弹、风衣 |
| 奇迹商人 | `wolf-killer-miracle-merchant` | 官方 `role-miracle-merchant.webp` | — | `role-miracle-merchant.webp` | 钱袋、货箱、金骰 |
| 魔镜少女 | `wolf-killer-mirror-girl` | 官方 `role-mirror-girl.webp` | — | `role-mirror-girl.webp` | 手镜、倒影、蓝焰 |
| 觉醒隐狼 | `wolf-killer-awakened-hidden-wolf` | 官方 `role-awakened-hidden-wolf.webp` | — | `role-awakened-hidden-wolf.webp` | 狼影、面具、暗巷 |
| 流光伯爵 | `wolf-killer-radiance-earl` | 官方 `role-radiance-earl.webp` | — | `role-radiance-earl.webp` | 金纹礼服、光盾、酒杯 |
| 蚀日侍女 | `wolf-killer-eclipse-maid` | 官方 `role-eclipse-maid.webp` | — | `role-eclipse-maid.webp` | 日蚀、黑纱、毒刃 |
| 蚀时狼妃 | `wolf-killer-time-wolf` | 官方 `role-time-wolf.webp` | — | `role-time-wolf.webp` | 时钟、狼尾、沙漏 |
| 定序王子 | `wolf-killer-order-prince` | 官方 `role-order-prince.webp` | — | `role-order-prince.webp` | 王冠、卷轴、逆转沙漏 |

## 四、完全没有立绘（11 个角色，需从零生成）

这 11 个角色在 `ROLE_ART_BY_ID` 里没有登记，`roleArt()` 直接回退卡背。其中前 6 个已经在
当前目录；后 5 个是工作区里待接入的新角色（是否出现在 `ROLE_ROWS` 取决于当前检出）。

| 角色 | 角色 ID | 当前目录 | 当前素材 | 建议文件 | 视觉母题 |
| --- | --- | --- | --- | --- | --- |
| 觉醒猎人 | `wolf-killer-awakened-hunter` | 已接入 | 无，卡背 | `role-awakened-hunter.webp` | 双枪、猎装、狼血 |
| 觉醒狼美人 | `wolf-killer-awakened-wolf-beauty` | 已接入 | 无，卡背 | `role-awakened-wolf-beauty.webp` | 替身幻影、红绸、镜 |
| 觉醒预言家 | `wolf-killer-awakened-seer` | 已接入 | 无，卡背 | `role-awakened-seer.webp` | 双星、双瞳、星图 |
| 寻香魅影 | `wolf-killer-fragrance-phantom` | 已接入 | 无，卡背 | `role-fragrance-phantom.webp` | 香炉、面纱、狼影 |
| 假面 | `wolf-killer-mask` | 已接入 | 无，卡背 | `role-mask.webp` | 半脸面具、舞会、羽毛 |
| 舞者 | `wolf-killer-dancer` | 已接入 | 无，卡背 | `role-dancer.webp` | 红舞鞋、舞池、纱裙 |
| 愚言家 | `wolf-killer-fool-seer` | 待接入 | 无，卡背 | `role-fool-seer.webp` | 小丑帽、假眼、提线 |
| 铁好人 | `wolf-killer-iron-villager` | 待接入 | 无，卡背 | `role-iron-villager.webp` | 粗布衣、无字铁牌、按胸自曝 |
| 乌鸦 | `wolf-killer-crow` | 待接入 | 无，卡背 | `role-crow.webp` | 黑羽、鸦群、枯枝 |
| 丘比特 | `wolf-killer-cupid` | 待接入 | 无，卡背 | `role-cupid.webp` | 弓箭、玫瑰、双心 |
| 暗恋者 | `wolf-killer-admirer` | 待接入 | 无，卡背 | `role-admirer.webp` | 信笺、心锁、暗影 |

补充：觉醒猎人 / 觉醒狼美人 / 觉醒预言家 / 寻香魅影属于 2023 年后的觉醒系列，
官方卡牌表未收录；舞者 / 假面 / 老酒鬼来自「假面舞会」（京城大师赛原创板，非网易牌），
官方没有立绘。愚言家 / 铁好人 / 暗恋者虽属网易学院板，但也不在 45 张卡牌数据里。
其中「乌鸦」「丘比特」在官方卡牌表里有图（`scripts/fetch-official-role-art.py` 的
`PROVISIONAL_PIC` 里是 `wy` / `qbt`），但既没有下载也没有登记；既然目标是自制图，
这两张也按“从零生成”处理。

## 五、盘点时发现的四个问题

1. **孤儿素材「觉醒狼王」**：磁盘上有 `role-awakened-wolf-king.webp`（官方图），
   `ROLE_ART_BY_ID` 也登记了 `wolf-killer-awakened-wolf-king`，但后端/前端角色目录
   都没有这个角色（官方卡牌表 45 张里有「觉醒狼王」，本作尚未实现）。它不在 40 个
   角色里，当前是死条目。要么删掉图与登记，要么等角色实现时一并补自制图。

2. **`ROLE_ART_BY_ID` 注释已过时**：`tokens.ts` 写“官方立绘覆盖 29 个角色”，实际
   已实现角色里只有 28 张官方；另一张是未实现的觉醒狼王。注释里“2023 年后的新牌暂时
   没有图”也已不准（觉醒隐狼、魔镜少女、流光伯爵、蚀日侍女、蚀时狼妃、定序王子都已有
   官方图）。改代码时顺手更正。

3. **文件名不统一**：白狼王/狼美人的自制备份用下划线，活动目录与登记表用连字符。
   在把自制图接入之前，先把这两个文件改成连字符，否则“覆盖即恢复”的说明是错的。

4. **发布路径还没闭环**：新生成的图如果放进 `assets/roles/`，会被 `.gitignore` 忽略，
   发布包里仍然没有；如果放回 `roles-original/`，`import.meta.glob` 又读不到。
   这是补齐立绘之前必须先定的机制（见下一节）。

## 六、补齐立绘的推荐路线

### 方案 A：最小改动（复用 11 张，新生成 24 / 29 张）

1. 把 `roles-original/` 里 10 张备份复制进 `assets/roles/`，覆盖官方图；
   其中白狼王/狼美人先改成连字符文件名（`role-werewolf-king.webp` /
   `role-wolf-beauty.webp`）。
2. 当前 35 角色口径：新生成 18 张“替换官方” + 6 张“从零补缺”，共 24 张。
   5 个待接入角色并入后，再补愚言家 / 铁好人 / 乌鸦 / 丘比特 / 暗恋者 5 张，
   合计 29 张。
3. 在 `ROLE_ART_BY_ID` 补 6 个当前缺失角色（觉醒猎人、觉醒狼美人、觉醒预言家、
   寻香魅影、假面、舞者）；5 个待接入角色并入时再补 5 条。
4. 删掉孤儿觉醒狼王的图与登记，或留到角色实现时处理。
5. 解决入库问题：要么让 `assets/roles/` 的 `role-*.webp` 入库（先删官方图，
   再取消 `.gitignore` 忽略），要么把自制图放到一个入库目录并让 `roleArt()` 优先读它。
6. 更新 `THIRD_PARTY_NOTICES.md` 4.1/4.3：自制图扩到全量；官方图免责声明与
   `scripts/fetch-official-role-art.py` 退役或明确标注“仅本地研究，不随包分发”。

### 方案 B：整套重生成（推荐，风格统一）

现有 11 张是不同批次生成的，画风、构图、光位不完全一致；官方 28 张又是另一种
“透明底人物 + 423×564”的体系。如果要给玩家一套看起来是同一套的立绘，建议 **完整 40 个
角色全部按同一套 prompt/seed/后期规范重新生成**，现有 11 张只作为风格基准，不直接上线。

### 落盘与登记

- 画布：**768×1024（3:4）**，与 `card-back.webp`、老酒鬼一致；WebP，质量 82 左右。
- 构图：胸像/半身，脸部在上半部（眼睛约在画面 30%–35% 高度），人物居中；
  不要文字、logo、边框、水印；给 `object-fit: cover` + `object-position: center 16%`
  留出头部空间。
- 风格：与「血月剧场」一致——暗黑哥特、电影感厚涂、低饱和暖黑底、血红与鎏金点缀；
  避免二次元/卡通/网易官方角色的相似脸与相似构图。
- 命名：`role-<slug>.webp`，`slug` = 角色 ID 去掉 `wolf-killer-`，**一律用连字符**。
- 补未登记角色时必须同时改两处：`frontend/src/theme/tokens.ts` 的 `ROLE_ART_BY_ID`
  与文件本身；`roleMeta.ts` 按当前目录登记，不需要为立绘改。
- 只改图不改代码时，`RolePortrait`、`SeatMap`、`DeathAnnouncement`、规则页都会自动生效。

### 验收

```bash
cd frontend
npm test                 # theme.test.ts 会检查登记角色都能取到图、地址互不相同
npm run build            # 确认所有立绘都能被 Vite 打包
```

`theme.test.ts` 的探针是 `roleArt('wolf-killer-seer') !== CARD_BACK`：只要预言家有图，
它就会要求 `ROLE_ART_BY_ID` 里每个登记角色都有图。补图时漏一张会红，这是好事。

## 七、一句话回答

- **当前 35 个角色**：29 个有立绘（28 官方 + 老酒鬼自制）；6 个只有卡背；
  补齐最少新生成 **24 张**。
- **完整 40 个角色**：29 个有立绘；11 个只有卡背（多出愚言家、铁好人、乌鸦、丘比特、
  暗恋者 5 个）；补齐最少新生成 **29 张**。
- **已有自有立绘**：11 个角色（10 张备份 + 老酒鬼）；其余 29 个需要新生成。
- 若追求统一画风：完整 40 张全部重生成，现有 11 张只作风格参考。
