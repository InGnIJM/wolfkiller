// ============================================================
// 血月剧场（Crimson Gothic）设计令牌 · 唯一视觉来源
// 风格提案：frontend/design-demos/01-crimson-gothic.html
// 规则：组件禁止硬编码色值，一律引用本文件或 MUI 主题语义槽
// ============================================================

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
} as const;

export type RoleColorKey = keyof typeof ROLE_COLORS;

/** 头像底色的深色宝石盘（保证浅墨文字 ≥4.5:1 对比度，按座位取色） */
export const AVATAR_PALETTE = [
  '#7A1E2B', '#7A5A1E', '#2E5670', '#553D78', '#3E5A39',
  '#2F5F58', '#70432F', '#454A63', '#5E3550',
] as const;
