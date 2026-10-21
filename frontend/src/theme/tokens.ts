// ============================================================
// 血月剧场（Crimson Gothic）设计令牌 · 唯一视觉来源
// 风格提案：frontend/design-demos/01-crimson-gothic.html
// 规则：组件禁止硬编码色值，一律引用本文件或 MUI 主题语义槽
// 图片路径只在本文件登记
// ============================================================

import bgGame from '../assets/bg/bg-game.webp';
import bgLobby from '../assets/bg/bg-lobby.webp';
import bgMain from '../assets/bg/bg-main.webp';
import cardBack from '../assets/roles/card-back.webp';

/** 基础画布与表面 */
export const CANVAS = {
  /** 页面最深底色 */
  bg: '#0B0A0F',
  /** 渐变上层的暖黑 */
  bgElevated: '#120E18',
  /** 卡片 / Paper 表面 */
  surface: '#171221',
  /** 更亮一级的表面（悬浮、弹层内嵌块） */
  surfaceRaised: '#1D1729',
} as const;

/** 金色发丝线（分隔、描边） */
export const HAIRLINE = {
  strong: 'rgba(212, 168, 83, 0.22)',
  soft: 'rgba(212, 168, 83, 0.12)',
} as const;

/** 墨色文字层级 */
export const INK = {
  primary: '#F2E9DC',
  dim: '#B3A89A',
  faint: '#7D7468',
} as const;

/** 血月双色：品牌红 + 纹饰金 */
export const BLOOD_MOON = {
  crimson: '#E5484D',
  crimsonDeep: '#C22E42',
  crimsonDark: '#8F1F30',
  gold: '#D4A853',
  goldLight: '#E8C887',
  goldDark: '#A87F33',
} as const;

/** 角色语义色（座位徽章、日志分类共用） */
export const ROLE_COLORS = {
  werewolf: { color: '#E5484D', bg: 'rgba(229, 72, 77, 0.14)' },
  witch: { color: '#B08BE0', bg: 'rgba(176, 139, 224, 0.14)' },
  seer: { color: '#7FB4D9', bg: 'rgba(127, 180, 217, 0.14)' },
  hunter: { color: '#D9A441', bg: 'rgba(217, 164, 65, 0.14)' },
  villager: { color: '#93B58C', bg: 'rgba(147, 181, 140, 0.14)' },
  guard: { color: '#74ABA3', bg: 'rgba(116, 171, 163, 0.14)' },
  idiot: { color: '#D9C27A', bg: 'rgba(217, 194, 122, 0.14)' },
  werewolf_king: { color: '#F0F0F0', bg: 'rgba(240, 240, 240, 0.12)' },
  knight: { color: '#8FA8D9', bg: 'rgba(143, 168, 217, 0.14)' },
  wolf_beauty: { color: '#D96A9A', bg: 'rgba(217, 106, 154, 0.14)' },
  old_drunkard: { color: '#B99A6B', bg: 'rgba(185, 154, 107, 0.14)' },
} as const;

export type RoleColorKey = keyof typeof ROLE_COLORS;

/** 全站页面背景。组件按 variant 取用，不直接写图片路径。 */
export const BACKGROUNDS = {
  main: bgMain,
  lobby: bgLobby,
  game: bgGame,
} as const;

export type BackgroundVariant = keyof typeof BACKGROUNDS;

/** 背景压暗、渐变遮罩与文字承托。对比度不够时只调这里。 */
export const BACKDROP = {
  dim: 0.55,
  saturate: 0.8,
  scrimTop: 'rgba(11, 10, 15, 0.25)',
  scrimBottom: 'rgba(11, 10, 15, 0.7)',
  vignette: 'radial-gradient(ellipse at center, transparent 40%, rgba(11, 10, 15, 0.72) 100%)',
  textPlate: 'rgba(23, 18, 33, 0.72)',
  cardScrim: 'linear-gradient(transparent, rgba(11, 10, 15, 0.85))',
  fadeMs: 300,
} as const;

/**
 * 立绘文件表：`role-*.webp` 的路径 → Vite 处理后的资源 URL。
 *
 * 用 `import.meta.glob` 而不是一条条静态 `import`：立绘是自制素材、现在随仓库
 * 分发，但 glob 仍让「某张图临时缺失」只表现为少一个键，`roleArt()` 照常回退
 * 卡背，**空仓库也能构建、也能跑**。
 *
 * 顺带的好处：换成自制立绘时只要文件名不变，**不用改任何代码**。
 */
const ROLE_ART_FILES = import.meta.glob('../assets/roles/role-*.webp', {
  eager: true, query: '?url', import: 'default',
}) as Record<string, string>;

/** 未揭晓身份 / 翻牌演出用的卡背。它必须入库 —— 回退总得有东西。 */
export const CARD_BACK = cardBack;

/**
 * 角色 id → 立绘文件名（不含扩展名）。
 *
 * 按**角色 id** 而不是按配色键登记：配色键只有 11 个，多个角色共用一个配色键
 * 就必然共用一张图 —— 狼王会显示成白狼王、恶夜骑士和石像鬼会显示成狼人。
 * 表里有名字但磁盘上没图时 `roleArt()` 仍回退卡背，所以这张表可以**先行**登记
 * 还没做图的角色，不必等素材。
 *
 * 自制立绘覆盖全部 40 个角色；表里有名字但磁盘上没图时 `roleArt()` 仍回退卡背。
 */
export const ROLE_ART_BY_ID: Record<string, string> = {
  'wolf-killer-werewolf': 'role-werewolf',
  'wolf-killer-villager': 'role-villager',
  'wolf-killer-seer': 'role-seer',
  'wolf-killer-witch': 'role-witch',
  'wolf-killer-hunter': 'role-hunter',
  'wolf-killer-guard': 'role-guard',
  'wolf-killer-idiot': 'role-idiot',
  'wolf-killer-werewolf-king': 'role-werewolf-king',
  'wolf-killer-knight': 'role-knight',
  'wolf-killer-wolf-beauty': 'role-wolf-beauty',
  'wolf-killer-old-drunkard': 'role-old_drunkard',
  'wolf-killer-wolf-king': 'role-wolf-king',
  'wolf-killer-evil-knight': 'role-evil-knight',
  'wolf-killer-dream-weaver': 'role-dream-weaver',
  'wolf-killer-gargoyle': 'role-gargoyle',
  'wolf-killer-gravekeeper': 'role-gravekeeper',
  'wolf-killer-dancer': 'role-dancer',
  'wolf-killer-mask': 'role-mask',
  'wolf-killer-pure-white': 'role-pure-white',
  'wolf-killer-wolf-witch': 'role-wolf-witch',
  'wolf-killer-demon-hunter': 'role-demon-hunter',
  'wolf-killer-crimson-envoy': 'role-crimson-envoy',
  'wolf-killer-nightmare': 'role-nightmare',
  'wolf-killer-time-wolf': 'role-time-wolf',
  'wolf-killer-order-prince': 'role-order-prince',
  'wolf-killer-miracle-merchant': 'role-miracle-merchant',
  'wolf-killer-lonely-girl': 'role-lonely-girl',
  'wolf-killer-mirror-girl': 'role-mirror-girl',
  'wolf-killer-awakened-hidden-wolf': 'role-awakened-hidden-wolf',
  'wolf-killer-awakened-hunter': 'role-awakened-hunter',
  'wolf-killer-awakened-wolf-beauty': 'role-awakened-wolf-beauty',
  'wolf-killer-awakened-seer': 'role-awakened-seer',
  'wolf-killer-eclipse-maid': 'role-eclipse-maid',
  'wolf-killer-radiance-earl': 'role-radiance-earl',
  'wolf-killer-fragrance-phantom': 'role-fragrance-phantom',
  'wolf-killer-fool-seer': 'role-fool-seer',
  'wolf-killer-iron-villager': 'role-iron-villager',
  'wolf-killer-crow': 'role-crow',
  'wolf-killer-cupid': 'role-cupid',
  'wolf-killer-admirer': 'role-admirer',
};

/**
 * 取一个角色的立绘地址。
 *
 * 三种情况都回退到卡背，而不是借用别人的图 —— 借图会让「这个角色长什么样」
 * 彻底失真，而卡背至少是诚实的：
 *   1. 没有角色 id（身份未揭晓）
 *   2. 角色 id 没登记
 *   3. 登记了但磁盘上没有对应文件（本地删了图，或新角色还没补图）
 */
export function roleArt(roleId: string | null | undefined): string {
  if (!roleId) return CARD_BACK;
  const name = ROLE_ART_BY_ID[roleId];
  if (!name) return CARD_BACK;
  return ROLE_ART_FILES[`../assets/roles/${name}.webp`] ?? CARD_BACK;
}

/** 头像底色的深色宝石盘（保证浅墨文字 ≥4.5:1 对比度，按座位取色） */
export const AVATAR_PALETTE = [
  '#7A1E2B', '#7A5A1E', '#2E5670', '#553D78', '#3E5A39',
  '#2F5F58', '#70432F', '#454A63', '#5E3550',
] as const;
