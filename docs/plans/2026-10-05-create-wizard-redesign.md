# 创建向导选人界面重设计

> 日期：2026-10-05
> 状态：**已实施** —— 阶段 A–D 落地，其中 D 的 `SeatMap` 有一处偏差，见下方「实施记录」。
> 范围：创建对局向导的「选择身份」步骤 + 对局中角色呈现 + 规则介绍页 `/rules`

---

## 实施记录（收尾时补）

| 阶段 | 结果 | 落点 |
| --- | --- | --- |
| A 数据模型 | ✅ | `ROLE_ART_BY_ID` + `roleArt()`（`theme/tokens.ts`）、`ROLE_ORDER` 上移到 `shared/roleMeta.ts`、`RolePortrait`（`1deabc0`、`9c36743`） |
| B 创建向导 | ✅ | `PresetList`（搜索 + 按人数折叠）、`RoleStep` 主从布局、`RoleRow`、`AddRoleDialog`；`custom` 态以 `selectedPresetId` 为单一真源（`7c7973a`、`03c2bdd`） |
| C 规则页 | ✅ | `RulesPage` + `RoleBrowser` 左索引右详情（`9924397`） |
| D 对局中 | 🟡 | `DeathAnnouncement` 改用 `RolePortrait`（`57c7b09`）；**`SeatMap` 仍自绘 `<img>`**（`badge?.art ?? CARD_BACK`），未换成组件 |

**已知残留**：

1. `SeatMap` 是本计划里唯一没换成 `RolePortrait` 的消费方。取图来源已经统一在 `roleArt()`
   （`SeatMap` 经 `ROLE_META.art` 取同一张图、同一套卡背回退），所以「补一张图只改
   `ROLE_ART_BY_ID` 一行」这条目标仍然成立，差异只在组件复用与卡背水印。
2. `ROLE_ART_BY_ID` 已为阶段 3–6 的 14 个角色**预先登记**（`pure-white` / `wolf-witch` /
   `demon-hunter` / `crimson-envoy` / `nightmare` / `time-wolf` / `order-prince` /
   `miracle-merchant` / `lonely-girl` / `mirror-girl` / `awakened-hidden-wolf` /
   `awakened-wolf-king` / `eclipse-maid` / `radiance-earl`），角色落地时不用再动取图代码。
   **仍无图**：觉醒预言家、寻香魅影、觉醒狼美人、觉醒猎人（2023 年后新牌，官方卡牌表未收录）。
3. Vite 侧门禁在收尾时跑过并全绿（ext4 镜像，做法见 `MEMORY.md` 的「WSL 下的前端验证」）：
   `npx vitest run` **38 文件 / 589 用例 passed**、`npx vitest run --coverage` 十个受门禁文件 **100%**、
   `npm run lint` 无告警、`npm run build` 成功。
4. 375 / 768 / 1440 三宽度的人工目视验收本文档未留记录；需要时跑 `npm run dev` 按 §十二 逐条核对。

> 立绘是本地资产：`frontend/src/assets/roles/role-*.webp` 在 `.gitignore` 里；
> `card-back.webp` 与 `role-old_drunkard.webp` 是自有素材、随仓库发布，
> 另 10 张自有的备份在 `frontend/src/assets/roles-original/`（入库）。

---

## 一、为什么要改

角色从 11 个涨到 16 个（后续到 35 个），板子从 6 个涨到 11 个（后续到 17+）。现有界面按小规模设计，两个区域都已经撑不住：

| 区域 | 现状 | 问题 |
| --- | --- | --- |
| 板子选择 | 11 张卡横向 `flex: 1` 平分 | 每张约 110px，名字被截断、阵容描述读不到；无分组、无搜索 |
| 角色配置 | 16 行竖排全宽，每行 56px | 约 900px 高，加到 35 个角色后约 2000px；每行摊开一整句描述，信息密度极低；「共 N 人」只有总数，看不出阵营是否平衡 |

还有一个**数据模型缺陷**：立绘按「配色键」取，不是按角色 id。

```
现在：角色 id → RoleColorKey → ROLE_ART[key]
```

多个角色共用一个配色键就必然共用一张图。实测后果：

| 角色 | 现在显示的 | 应该有 |
| --- | --- | --- |
| 狼王 | 白狼王的立绘 | 待补 |
| 恶夜骑士 / 石像鬼 | 狼人的立绘 | 待补 |
| 摄梦人 / 守墓人 | 守卫的立绘 | 待补 |

加上后续 19 个角色，**一共 24 个角色没有自己的立绘**。立绘会陆续补，界面必须在「没有图」时优雅降级。

**用户的真实使用习惯**（已确认）：绝大多数时候直接点一个官方板子开打，自定义是少数情况。所以**板子是主角，角色配置是次要但必须好用**。

---

## 二、目标与非目标

**目标**

1. 17+ 个板子能一屏或两屏内找到并看懂「这个板子里有什么角色」
2. 选中板子后立刻看到并调节它的阵容，一屏看完
3. 没有立绘的角色显示**卡背**，补图后只改一处数据、不动组件
4. 创建向导、对局中角色展示、规则页**共用同一套角色呈现数据与组件**
5. 视觉上沿用「血月剧场」现有风格并做改良，不引入新色系/新字体

**非目标（本次不做）**

- 不改后端任何接口（`/api/catalog/{roles,presets,constraints}` 已够用）
- 不做板子的收藏/排序/自定义板子保存
- 不做拖拽排序角色
- 不改 ModelStep（第二步）
- 不补立绘本身（只做占位与替换点）

---

## 三、数据模型改造（前置，三处共用）

### 3.1 立绘按角色 id 取，带卡背回退

```ts
// theme/tokens.ts —— 新增，键是角色 id
export const ROLE_ART_BY_ID: Record<string, string> = {
  'wolf-killer-werewolf': artWerewolf,
  'wolf-killer-villager': artVillager,
  'wolf-killer-seer': artSeer,
  'wolf-killer-witch': artWitch,
  'wolf-killer-hunter': artHunter,
  'wolf-killer-guard': artGuard,
  'wolf-killer-idiot': artIdiot,
  'wolf-killer-werewolf-king': artWerewolfKing,
  'wolf-killer-knight': artKnight,
  'wolf-killer-wolf-beauty': artWolfBeauty,
  'wolf-killer-old-drunkard': artOldDrunkard,
};

/** 角色立绘；没有立绘的角色回退到卡背，而不是借用别人的图。 */
export function roleArt(roleId: string | null | undefined): string {
  if (!roleId) return CARD_BACK;
  return ROLE_ART_BY_ID[roleId] ?? CARD_BACK;
}
```

`ROLE_ART`（按配色键）**保留不动** —— 它还被 `theme.test.ts` 断言，且是「配色键 → 立绘」的完整映射，删掉会连带改测试。新增的 `ROLE_ART_BY_ID` 是**呈现层唯一的取图入口**。

### 3.2 roleMeta 改为按 id 全覆盖 + 卡背回退

`components/shared/roleMeta.ts` 现在只登记 11 个角色，`roleMetaFor` 对未登记角色返回 `undefined`，4 个消费方各自写 `?.art ?? CARD_BACK`、`?.color ?? 'text.primary'` 这类兜底。

改造：

- `ROLE_ROWS` 补齐到全部 16 个角色（后续新增角色时同步）
- 新增角色的 `key` 用**语义最接近的配色键**（如石像鬼用 `werewolf`、摄梦人用 `guard`），但 `art` 一律走 `roleArt(roleId)` → 没图就是卡背
- `roleMetaFor` 对未登记角色仍返回 `undefined`（保持现有契约，`SeatMap` 依赖它判断「未知身份」）

**同时把 `ROLE_ORDER` 从 `RulesPage.tsx` 提到 `roleMeta.ts`** 并导出。现在它是 `RulesPage` 的私有常量，但新的创建向导右栏排序、规则页左栏排序、对话框列表排序都要用同一套顺序；留在页面里会出现三份各自维护的副本。

**关键点**：`RoleMetaEntry.art` 从「配色键对应的图」变成「这个角色自己的图，没有就是卡背」。消费方不再需要各自兜底。

### 3.3 一个共用的角色呈现组件

新增 `components/shared/RolePortrait.tsx`：

```tsx
interface Props {
  roleId: string | null | undefined;
  /** 立绘条宽度，创建向导 64、座位卡 40、规则页 56 */
  width?: number;
  /** 高度；不传则撑满父容器 */
  height?: number | string;
  /** 圆角，默认随父容器裁切 */
  rounded?: boolean;
  className?: string;
}
```

职责：渲染立绘或卡背，`objectFit: cover`、`objectPosition: center 16%`（沿用现有取景），无立绘时叠一层极淡的角色名水印，让卡背不至于完全空白。

创建向导、`SeatMap`、`DeathAnnouncement`、`RulesPage` 四处都改用它，**去掉各自的 `?.art ?? CARD_BACK`**。

---

## 四、创建向导「选择身份」新布局

### 4.1 整体结构

`CreateGameWizard` 的 `Container` 从 `maxWidth="md"`（900px）放宽到 `maxWidth="lg"`（1200px），否则两栏放不下。

```
┌──────────────────────────────────────────────────────────────────┐
│  创建游戏                                                         │
│  ① 人数身份配置 ── ② Agent 模型配置                                │
├────────────────┬─────────────────────────────────────────────────┤
│  左栏 290px     │  右栏 flex                                       │
│  ┌───────────┐ │  ┌────────────────────────────────────────────┐ │
│  │ ⌕ 搜索…   │ │  │ 十二人石像鬼守墓人  [网易官方赛制]           │ │
│  └───────────┘ │  │ 3狼 1石像鬼 4民 1预言家 1女巫 1猎人 1守墓人  │ │
│  ▾ 12 人局  9  │  │ 共12人 狼4 好人8 警长开 女巫自救禁止  [+添加]│ │
│    ▪ 十二人…   │  ├────────────────────────────────────────────┤ │
│    ▸ 十人局  1 │  │  [图] 狼人    夜晚睁眼…  狼人阵营 − 3 +     │ │
│    ▸ 九人局  1 │  │  [图] 石像鬼  狼人，不属于狼窝… 狼人阵营 −1+ │ │
│                │  │  [卡背] 守墓人 好人神职…  好人阵营 − 1 +     │ │
│                │  │  ⋯ 共 7 行                                  │ │
├────────────────┴─────────────────────────────────────────────────┤
│  ☐ 明牌局   ☐ 警长  女巫自救：禁止                                 │
│                                          [下一步]                 │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 左栏：板子列表

**搜索框**（顶部固定）

- 受控输入，`debounce` 200ms 后再过滤（角色数多时避免每次按键重算）
- 匹配范围：板子名（`name`）+ 描述（`description`）+ **阵容里的角色中文名**（这样搜「摄梦」能找到狼王摄梦人板）
- 有输入时右侧出现清除按钮（`IconButton` + `aria-label="清空搜索"`）
- 无结果时显示空状态：`没有匹配的板子` + `试试「狼王」或「摄梦」，或清空搜索`，**不是空白**

**分类折叠组**

- 按**人数**分组（9 / 10 / 12 人），组标题显示组内数量
- 默认只展开「12 人局」（数量最多），其余折叠
- 折叠状态用 `Collapse`，箭头 `ExpandMore` 旋转 150ms
- 组标题是 `<button>`，带 `aria-expanded`
- **搜索时自动展开所有命中分组**，并隐藏空分组

**板子条目**

- 第一行：板子名 + `官方` 金标（`enable_sheriff && witch_self_save === 'never'`）
- 第二行：阵容色块（角色简称 + ×N），沿用现有的 `chips` 渲染思路
- 选中态：左侧 2px 血红竖条 + `CANVAS.surfaceRaised` 背景 + 名字加粗
- 悬停：背景提亮一档，150ms 过渡，**不改布局尺寸**

### 4.3 右栏：板子头部

- 标题：板子名（未自定义）或 `自定义阵容`（已自定义）
- 副标题：板子 `description`
- 标签行：`网易官方赛制` 金标 / 自定义时改为虚线 `基于 <原板子名>` + 「重置为原板子」文字按钮
- 统计条：`共 N 人` / `狼 N` / `好人 N` / `警长 开|关` / `女巫自救 禁止|仅首夜`
  - 人数超出上限时 `共 N 人 · 超出 M` 用血红警示
- 右上角：`+ 添加角色` 按钮（`outlined`，金色描边）

### 4.4 右栏：角色行列表（沿用现有样式）

一行一个角色，**完全沿用现有 `RoleStep` 的行结构**：

- 左侧 64px 立绘条（`RolePortrait`），无图走卡背
- 中间：角色名（`body2` 700）+ 描述（`caption`，单行省略号）
- 右侧：阵营标签 + `−` / 数量 / `+`
- 行容器：`1px solid HAIRLINE.soft`、`borderRadius: 2`（8px）、`bgcolor: background.paper`
- **新增角色**的行：金色描边 + 金色「新增」角标
- 数量为 0 的行：整体 `opacity: 0.55`，减号禁用

**初始只渲染 `roleCounts[roleId] > 0` 的角色**，按阵营排序（狼人阵营在前，组内按 `ROLE_ORDER`）。

### 4.5 「+ 添加角色」对话框

- `Dialog`，标题「添加角色」，副标题 `已选中 N 个 · 当前板子已有 M 个角色，不再列出`
- 顶部搜索框（同样 debounce）
- 列表：**只列当前 `roleCounts` 里为 0 的角色**，按阵营分组（狼人阵营 / 好人阵营），每组显示数量
- 每项：复选框 + 角色名 + 立绘小缩略图
- 多选；底部主按钮 `添加 N 个角色`（N=0 时禁用），次按钮 `取消`
- 确认后：这些角色 `roleCounts[id] = 1`，进入自定义态
- `Esc` / 点遮罩关闭；关闭不保留已选（避免「关掉了但以为加了」）

### 4.6 自定义态

- 任意 `−` / `+` / 添加角色后，`selectedPresetId` 置为 `'custom'`
- 标题变 `自定义阵容`，出现 `基于 <原板子名>` 与 `重置为原板子`
- 左栏对应板子仍高亮（因为基于它），但加虚线边框区分「已改」
- 「重置为原板子」直接恢复该板子的 `role_counts` 与三个开关，不弹确认（可再改回来）

### 4.7 三个开关的位置

`明牌局` / `警长` / `女巫自救` 从现在的独立大盒子挪到**右栏底部的一条紧凑设置行**，`女巫自救` 只读展示（它随预设自动设定，用户不能改）。

---

## 五、视觉改良（在现有风格上升级）

沿用「血月剧场」全部令牌，**不新增色值、不换字体**。

### 5.1 背景与层次

现在整页是 `Container` 套一层 `BACKDROP.textPlate + blur(12px)`，两栏设计下会显得糊成一片。改为**分层**：

| 层 | 做法 |
| --- | --- |
| 页面背景 | `PageBackground variant="main"` 不变 |
| 向导容器 | 去掉整块 `textPlate`（现在是 `Container` 上的 `bgcolor: BACKDROP.textPlate + blur(12px)`），改为**无底色**，只保留 `position: relative; zIndex: 1` |
| 页面标题「创建游戏」 | **保留**它自己的 `textPlate + blur` 小药丸（`Typography` 上那份，与容器那份是两处），它是全页唯一还压磨砂的地方，正好做视觉锚点 |
| 左栏 | `CANVAS.surface` + `1px solid HAIRLINE.soft` 右边框，自身可滚动 |
| 右栏 | 无底色（透出背景），只有角色行是 `background.paper` 卡片 |

这样背景图在两栏之间和右栏空白处透出来，层次比现在「一整块磨砂玻璃」更清晰，也更符合「血月剧场」的剧场感。

### 5.2 字体

- 标题（`创建游戏`、板子名）：`h4` / `h6`，Cinzel + Noto Serif SC 衬线栈（主题已设，不额外指定）
- 正文/描述：`body2` / `caption`，同一衬线栈
- **数字用 `font-variant-numeric: tabular-nums`**：统计条的「共 12 人」「狼 4」、角色行的数量、折叠组计数 —— 避免加减时数字宽度跳动
- 板子名与角色名**不加 letter-spacing**（中文衬线加字距会散）

### 5.3 金色发丝线

沿用 `HAIRLINE.strong`（区块分隔）与 `HAIRLINE.soft`（卡片描边）。新增用法：

- 左栏与右栏之间：`HAIRLINE.soft` 竖线
- 头部与角色列表之间：`HAIRLINE.soft` 横线
- 折叠组标题下方：`HAIRLINE.soft`

### 5.4 间距节奏

统一 4/8px 基线：容器内边距 16/18px，角色行间距 8px，统计条内边距 8px，卡片内边距 12/14px。折叠组标题 8px 上下。

### 5.5 动效

| 交互 | 时长 | 属性 |
| --- | --- | --- |
| 板子条目悬停 | 150ms | `background-color` |
| 折叠展开/收起 | 200ms | MUI `Collapse`（高度） |
| 角色行新增出现 | 200ms | `opacity` + `transform: translateY(4px)` |
| 对话框 | MUI 默认 | — |

全部走 `theme.transitions`，并包在 `@media (prefers-reduced-motion: reduce)` 的降级里（MUI 的 `CssBaseline` + 主题 `transitions` 已处理，只需不写裸 `transition`）。

**禁止**：动画 `width` / `height` / `top` / `left`；按压时改变布局尺寸。

---

## 六、对局中角色展示（范围第二部分）

四处消费方统一改走 `RolePortrait` + `roleMetaFor`：

| 位置 | 现在 | 改为 |
| --- | --- | --- |
| `SeatMap.tsx:83` | `roleMetaFor(role)` 取 badge 配色 | 不变（配色仍按配色键），但**已揭晓身份时用 `RolePortrait` 显示立绘/卡背** |
| `DeathAnnouncement.tsx:46` | `roleMetaFor(revealedRole)` 取 art | 改用 `RolePortrait`，无图走卡背 |
| `RoleStep.tsx:146` | `roleMetaFor(...)?.art ?? CARD_BACK` | 改用 `RolePortrait` |
| `RulesPage.tsx:243` | 只取 color | 加 `RolePortrait` 小图 |

**不改**：`SeatMap` 的座位椭圆、颜色映射、悬停卡结构。

---

## 七、规则介绍页 `/rules`（范围第三部分）

现在是一列长列表（`maxWidth="lg"`），角色到 35 个后会同样过长。

改造为**左侧角色索引 + 右侧详情**，与创建向导同一套交互语言：

- 左栏：搜索 + 按阵营折叠分组（狼人阵营 / 好人阵营 / 神职 / 平民），每项显示立绘小图 + 名字
- 右栏：选中角色的立绘大图 + `name_zh` + `description` + `rules` 条目列表
- 顶部保留现有的规则叙述文案（`ruleSections.ts`）作为一个默认页

**不改**：`ruleSections.ts` 的文案内容、只读三条公开目录接口的做法。

---

## 八、无障碍与 UX 门禁

按 ui-ux-pro-max 的 CRITICAL/HIGH 清单落到本项目：

| 项 | 做法 |
| --- | --- |
| 对比度 | 正文 `INK.primary #F2E9DC` on `CANVAS.surface #171221` ≈ 13:1；次要文字 `INK.dim #B3A89A` ≈ 6.4:1；都过 AA。`INK.faint #7D7468` 只用于非关键装饰，不承载信息 |
| 触控目标 | `−` / `+` 视觉 30×30，**hit area 补到 44×44**（`sx` 上用 `p` 或 `::before` 扩展）；左栏条目整行可点，高度 ≥44px |
| 键盘 | Tab 顺序 = 视觉顺序；折叠组标题是 `<button aria-expanded>`；加减号 `IconButton` 带 `aria-label="减少摄梦人"` / `"增加摄梦人"` |
| 焦点环 | 2px `BLOOD_MOON.gold` 外环，`:focus-visible` 才显示 |
| 搜索 | debounce 200ms；无结果给引导文案；清除按钮有 `aria-label` |
| 空状态 | 左栏无结果、对话框无可选角色，都有文案而不是空白 |
| 加载 | 三条目录接口加载中显示骨架，不是空白（`content-jumping`） |
| 图片 | 立绘 `<img>` 带 `width`/`height` 或 `aspect-ratio`，避免 CLS；`loading="lazy"`（列表内非首屏图） |
| 动效 | 全部 150–300ms，只动 `transform`/`opacity`/`background-color` |
| 不靠颜色 | 新增角色除金色描边外还有「新增」文字角标；人数超出除红字外还有「超出 N」文字 |

---

## 九、测试与覆盖率

前端覆盖率门禁只卡 `vite.config.ts` 列出的 10 个文件。本次新增的 `RolePortrait.tsx` **不加入门禁**（与 `RoleStep.tsx`、`SeatMap.tsx` 一致 —— 渲染型组件不在门禁内，由 `eventCoverage.test.tsx` 兜底事件覆盖），但仍按项目惯例写单元测试：

| 文件 | 测试 |
| --- | --- |
| `theme/tokens.ts` | 扩 `src/test/theme.test.ts`：`roleArt` 对有图/无图/空值三种输入的回退 |
| `shared/roleMeta.ts` | 扩 `shared/test/roleMeta.test.ts`：16 个角色全覆盖、未登记角色返回 `undefined` |
| `shared/RolePortrait.tsx` | 新建 `shared/test/RolePortrait.test.tsx`：有图渲染 `<img>`、无图渲染卡背、`alt` 文案 |
| `create/RoleStep.tsx` | 新建/扩 `create/test/RoleStep.test.tsx`：搜索过滤、分组折叠、只显示已选角色、加减改自定义态、重置、添加角色对话框 |
| `rules/RulesPage.tsx` | 扩现有测试：左栏索引、搜索、选中切换 |
| `game/SeatMap.tsx` / `DeathAnnouncement.tsx` | 扩现有测试：无立绘角色走卡背 |
| `components/game/test/eventCoverage.test.tsx` | **不改**（本次不新增观众事件类型） |

后端**零改动**，`pytest` 应保持 3940 passed / 100% 不变；如果变了说明误碰了后端。

---

## 十、建议实施顺序

一次做完三处面积偏大，建议拆成四个可独立验收、可独立提交的阶段：

1. **阶段 A：数据模型**（§三）—— `ROLE_ART_BY_ID` + `roleArt()` + `ROLE_ROWS` 补齐 + `ROLE_ORDER` 上移 + `RolePortrait`。**这一阶段不碰任何布局**，四处消费方先只换取图方式，界面外观不变（除了 5 个角色从「借用别人的图」变成卡背）。做完就能验证「补一张图只改一行」。
2. **阶段 B：创建向导**（§四 + §五）—— 两栏布局、左栏搜索折叠、右栏行式列表、添加角色对话框、自定义态、视觉改良。这是主体。
3. **阶段 C：规则页**（§七）—— 左索引右详情，复用 B 的交互组件。
4. **阶段 D：对局中**（§六）—— `SeatMap` / `DeathAnnouncement` 换 `RolePortrait`。

每阶段结束都要跑 `npx vitest run` + `npm run lint` + `npm run build`，阶段 B 还要在 375px / 768px / 1440px 三个宽度各看一次。

---

## 十一、风险与取舍

| 风险 | 处理 |
| --- | --- |
| `Container maxWidth` 从 `md` 改 `lg`，窄屏会挤 | 两栏在 `md` 以下断点改为上下堆叠：左栏变成可折叠的「选择板子」抽屉，右栏全宽 |
| 左栏固定 290px，长板子名会截断 | 板子名 `text-overflow: ellipsis` + `title` 属性给全名 |
| `roleMetaFor` 语义变化影响 `SeatMap` 的「未知身份」判断 | 保持「未登记角色返回 `undefined`」这一条不变，只改已登记角色的 `art` 来源 |
| 搜索「阵容里的角色名」需要中英映射 | 用目录接口返回的 `name_zh`，不额外维护映射表 |
| 自定义态与预设态的状态同步 | `selectedPresetId` 是唯一真源：`'custom'` 表示已改；重置时写回预设 id 与三开关 |

---

## 十二、验收标准

1. 左栏在 17 个板子下能通过搜索 + 折叠在 2 次交互内定位到任一板子
2. 选中板子后右栏只显示该板子的角色，一屏（1440×900）内看完 12 人板子的全部角色行
3. 没有立绘的角色显示卡背，补一张图只需在 `ROLE_ART_BY_ID` 加一行
4. 创建向导、`SeatMap`、`DeathAnnouncement`、`RulesPage` 四处角色图走同一个 `RolePortrait`
5. `npm run lint` / `tsc -b && vite build` / `npx vitest run` 全绿，覆盖率门禁不降
6. 后端 `pytest tests app --cov=app --cov-branch` 仍是 3940 passed / 100%
7. 375px 宽度下无横向滚动，两栏正确堆叠
