// ============================================================
// 血月剧场（Crimson Gothic）设计令牌 · 唯一视觉来源
// 风格提案：frontend/design-demos/01-crimson-gothic.html
// 规则：组件禁止硬编码色值，一律引用本文件或 MUI 主题语义槽
// 图片路径只在本文件登记
//
// 双主题：导出的常量全部是对 `var(--wk-*)` 的引用，实体色值
// 收在 `MODE_TOKENS` 里按 dark / light 各一份，由主题工厂经
// `MuiCssBaseline` 注入 `:root`。组件引用令牌即可随主题切换，
// 不需要知道当前模式。
// ============================================================

import bgGame from '../assets/bg/bg-game.webp';
import bgLobby from '../assets/bg/bg-lobby.webp';
import bgMain from '../assets/bg/bg-main.webp';
import cardBack from '../assets/roles/card-back.webp';

export type ThemeMode = 'dark' | 'light';

/** 一套模式实体色值。加字段时两份一起加，`cssVars()` 才有东西可发。 */
export interface ModeTokens {
  canvas: { bg: string; bgElevated: string; surface: string; surfaceRaised: string };
  hairline: { strong: string; soft: string };
  ink: { primary: string; dim: string; faint: string };
  bloodMoon: {
    crimson: string; crimsonDeep: string; crimsonDark: string;
    gold: string; goldLight: string; goldDark: string;
  };
  roleColors: Record<RoleColorKey, { color: string; bg: string }>;
  backdrop: {
    dim: number; saturate: number; scrimTop: string; scrimBottom: string;
    vignette: string; textPlate: string; cardScrim: string;
  };
  /** 半透明承托板（活动卡底、中央面板压底） */
  plate: { strong: string; soft: string };
  /** AppBar 的磨砂底 */
  appBar: string;
  /** 覆盖在内容上的淡纱（活动卡内嵌块等） */
  scrim: { soft: string };
  /** 墨色水洗底（编年史分组条等极淡填充） */
  inkWash: string;
  /** 投影色（座位图、悬停卡、角色浏览器） */
  shadow: { card: string; pop: string; soft: string };
  /** CssBaseline 的 body 三层渐变与文字选中色 */
  bodyGradient: string;
  selection: string;
  /** 压在饱和强调色（绯红/金/角色色）上的前景：深底下用暗墨，浅底下用白 */
  onAccent: string;
  /** 「第N天」大标题的渐变文字（backgroundClip:text），深底用亮金、浅底用暗金 */
  titleGradient: string;
  /** MUI action.* 通道 */
  action: {
    hover: string; selected: string; focus: string;
    disabled: string; disabledBackground: string;
  };
}

/** 基础画布与表面 */
export const CANVAS = {
  /** 页面最深底色 */
  bg: 'var(--wk-canvas-bg)',
  /** 渐变上层的暖黑 */
  bgElevated: 'var(--wk-canvas-bg-elevated)',
  /** 卡片 / Paper 表面 */
  surface: 'var(--wk-canvas-surface)',
  /** 更亮一级的表面（悬浮、弹层内嵌块） */
  surfaceRaised: 'var(--wk-canvas-surface-raised)',
} as const;

/** 金色发丝线（分隔、描边） */
export const HAIRLINE = {
  strong: 'var(--wk-hairline-strong)',
  soft: 'var(--wk-hairline-soft)',
} as const;

/** 墨色文字层级 */
export const INK = {
  primary: 'var(--wk-ink-primary)',
  dim: 'var(--wk-ink-dim)',
  faint: 'var(--wk-ink-faint)',
} as const;

/** 血月双色：品牌红 + 纹饰金 */
export const BLOOD_MOON = {
  crimson: 'var(--wk-moon-crimson)',
  crimsonDeep: 'var(--wk-moon-crimson-deep)',
  crimsonDark: 'var(--wk-moon-crimson-dark)',
  gold: 'var(--wk-moon-gold)',
  goldLight: 'var(--wk-moon-gold-light)',
  goldDark: 'var(--wk-moon-gold-dark)',
} as const;

/** 角色语义色（座位徽章、日志分类共用） */
export const ROLE_COLORS = {
  werewolf: { color: 'var(--wk-role-werewolf)', bg: 'var(--wk-role-werewolf-bg)' },
  witch: { color: 'var(--wk-role-witch)', bg: 'var(--wk-role-witch-bg)' },
  seer: { color: 'var(--wk-role-seer)', bg: 'var(--wk-role-seer-bg)' },
  hunter: { color: 'var(--wk-role-hunter)', bg: 'var(--wk-role-hunter-bg)' },
  villager: { color: 'var(--wk-role-villager)', bg: 'var(--wk-role-villager-bg)' },
  guard: { color: 'var(--wk-role-guard)', bg: 'var(--wk-role-guard-bg)' },
  idiot: { color: 'var(--wk-role-idiot)', bg: 'var(--wk-role-idiot-bg)' },
  werewolf_king: { color: 'var(--wk-role-werewolf-king)', bg: 'var(--wk-role-werewolf-king-bg)' },
  knight: { color: 'var(--wk-role-knight)', bg: 'var(--wk-role-knight-bg)' },
  wolf_beauty: { color: 'var(--wk-role-wolf-beauty)', bg: 'var(--wk-role-wolf-beauty-bg)' },
  old_drunkard: { color: 'var(--wk-role-old-drunkard)', bg: 'var(--wk-role-old-drunkard-bg)' },
} as const;

export type RoleColorKey = keyof typeof ROLE_COLORS;

/** 半透明承托板（活动卡底、中央面板压底），随模式反转深浅。 */
export const PLATE = {
  strong: 'var(--wk-plate-strong)',
  soft: 'var(--wk-plate-soft)',
} as const;

/** 内容上的淡纱。 */
export const SCRIM = {
  soft: 'var(--wk-scrim-soft)',
} as const;

/** 墨色水洗底。 */
export const INK_WASH = 'var(--wk-ink-wash)';

/** 投影色。 */
export const SHADOW = {
  card: 'var(--wk-shadow-card)',
  pop: 'var(--wk-shadow-pop)',
  soft: 'var(--wk-shadow-soft)',
} as const;

/** 压在饱和强调色上的前景（徽章图标、圆形标记里的字）。 */
export const ON_ACCENT = 'var(--wk-on-accent)';

/** 「第N天」大标题的渐变文字。 */
export const TITLE_GRADIENT = 'var(--wk-title-gradient)';

/** 全站页面背景。组件按 variant 取用，不直接写图片路径。 */
export const BACKGROUNDS = {
  main: bgMain,
  lobby: bgLobby,
  game: bgGame,
} as const;

export type BackgroundVariant = keyof typeof BACKGROUNDS;

/**
 * 背景遮罩与文字承托。`fadeMs` 两种模式相同且要进 `setTimeout`，
 * 所以保留数字、不变量化；其余成员是 CSS 变量引用。
 */
export const BACKDROP = {
  dim: 'var(--wk-backdrop-dim)',
  saturate: 'var(--wk-backdrop-saturate)',
  scrimTop: 'var(--wk-backdrop-scrim-top)',
  scrimBottom: 'var(--wk-backdrop-scrim-bottom)',
  vignette: 'var(--wk-backdrop-vignette)',
  textPlate: 'var(--wk-backdrop-text-plate)',
  cardScrim: 'var(--wk-backdrop-card-scrim)',
  fadeMs: 300,
} as const;

const DARK_TOKENS: ModeTokens = {
  canvas: {
    bg: '#1D0E13',
    bgElevated: '#29151C',
    surface: '#141417',
    surfaceRaised: '#1D1D23',
  },
  hairline: {
    strong: 'rgba(212, 168, 83, 0.22)',
    soft: 'rgba(212, 168, 83, 0.12)',
  },
  ink: {
    primary: '#F2E9DC',
    dim: '#B3A89A',
    faint: '#7D7468',
  },
  bloodMoon: {
    crimson: '#D04564',
    crimsonDeep: '#A62E4B',
    crimsonDark: '#6B1F36',
    gold: '#D4A853',
    goldLight: '#E8C887',
    goldDark: '#A87F33',
  },
  roleColors: {
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
  },
  backdrop: {
    dim: 0.55,
    saturate: 0.8,
    scrimTop: 'rgba(26, 10, 15, 0.25)',
    scrimBottom: 'rgba(26, 10, 15, 0.7)',
    vignette: 'radial-gradient(ellipse at center, transparent 40%, rgba(26, 10, 15, 0.72) 100%)',
    textPlate: 'rgba(14, 14, 18, 0.72)',
    cardScrim: 'linear-gradient(transparent, rgba(0, 0, 0, 0.85))',
  },
  plate: {
    strong: 'rgba(16, 16, 20, 0.82)',
    soft: 'rgba(16, 16, 20, 0.6)',
  },
  appBar: 'rgba(0, 0, 0, 1)',
  scrim: { soft: 'rgba(0, 0, 0, 0.25)' },
  inkWash: 'rgba(242, 233, 220, 0.03)',
  shadow: {
    card: 'rgba(0, 0, 0, 0.45)',
    pop: 'rgba(0, 0, 0, 0.55)',
    soft: 'rgba(0, 0, 0, 0.35)',
  },
  bodyGradient: [
    'radial-gradient(1100px 520px at 50% -8%, rgba(166,46,75,0.30), transparent 62%)',
    'radial-gradient(900px 700px at 50% 118%, rgba(212,168,83,0.11), transparent 60%)',
    'linear-gradient(180deg, #29151C, #1D0E13 42%)',
  ].join(', '),
  selection: 'rgba(208, 69, 100, 0.35)',
  onAccent: '#1B0F13',
  titleGradient: 'linear-gradient(180deg, #F7EFE2, #CAA96A)',
  action: {
    hover: 'rgba(212, 168, 83, 0.09)',
    selected: 'rgba(212, 168, 83, 0.14)',
    focus: 'rgba(212, 168, 83, 0.18)',
    disabled: 'rgba(242, 233, 220, 0.12)',
    disabledBackground: 'rgba(242, 233, 220, 0.08)',
  },
};

const LIGHT_TOKENS: ModeTokens = {
  canvas: {
    bg: '#F3EDE3',
    bgElevated: '#FAF6EC',
    surface: '#FFFCF4',
    surfaceRaised: '#FFFFFF',
  },
  hairline: {
    strong: 'rgba(146, 112, 44, 0.32)',
    soft: 'rgba(146, 112, 44, 0.16)',
  },
  ink: {
    primary: '#241E2B',
    dim: '#5C5347',
    faint: '#7D7264',
  },
  bloodMoon: {
    crimson: '#D63340',
    crimsonDeep: '#B02838',
    crimsonDark: '#7C1B2B',
    gold: '#8A6B22',
    goldLight: '#A3802F',
    goldDark: '#66501B',
  },
  roleColors: {
    werewolf: { color: '#C43640', bg: 'rgba(198, 54, 64, 0.12)' },
    witch: { color: '#7A57B8', bg: 'rgba(122, 87, 184, 0.12)' },
    seer: { color: '#35739F', bg: 'rgba(53, 115, 159, 0.12)' },
    hunter: { color: '#8F6A13', bg: 'rgba(143, 106, 19, 0.14)' },
    villager: { color: '#4A7A43', bg: 'rgba(74, 122, 67, 0.12)' },
    guard: { color: '#3A7A70', bg: 'rgba(58, 122, 112, 0.12)' },
    idiot: { color: '#8A762E', bg: 'rgba(138, 118, 46, 0.14)' },
    werewolf_king: { color: '#4A4457', bg: 'rgba(74, 68, 87, 0.12)' },
    knight: { color: '#4B6AAE', bg: 'rgba(75, 106, 174, 0.12)' },
    wolf_beauty: { color: '#B0416F', bg: 'rgba(176, 65, 111, 0.12)' },
    old_drunkard: { color: '#7A5D36', bg: 'rgba(122, 93, 54, 0.14)' },
  },
  backdrop: {
    dim: 0.9,
    saturate: 0.85,
    scrimTop: 'rgba(243, 237, 227, 0.55)',
    scrimBottom: 'rgba(243, 237, 227, 0.85)',
    vignette: 'radial-gradient(ellipse at center, rgba(243, 237, 227, 0.3) 0%, rgba(243, 237, 227, 0.82) 100%)',
    textPlate: 'rgba(255, 252, 244, 0.85)',
    cardScrim: 'linear-gradient(transparent, rgba(243, 237, 227, 0.9))',
  },
  plate: {
    strong: 'rgba(255, 252, 244, 0.85)',
    soft: 'rgba(255, 252, 244, 0.68)',
  },
  appBar: 'rgba(255, 252, 244, 0.86)',
  scrim: { soft: 'rgba(43, 34, 25, 0.08)' },
  inkWash: 'rgba(36, 30, 43, 0.04)',
  shadow: {
    card: 'rgba(43, 34, 25, 0.22)',
    pop: 'rgba(43, 34, 25, 0.3)',
    soft: 'rgba(43, 34, 25, 0.18)',
  },
  bodyGradient: [
    'radial-gradient(1100px 520px at 50% -8%, rgba(214,51,64,0.10), transparent 62%)',
    'radial-gradient(900px 700px at 50% 118%, rgba(138,107,34,0.12), transparent 60%)',
    'linear-gradient(180deg, #FAF6EC, #F3EDE3 42%)',
  ].join(', '),
  selection: 'rgba(214, 51, 64, 0.25)',
  onAccent: '#FFFFFF',
  titleGradient: 'linear-gradient(180deg, #8A6B22, #5E4A1C)',
  action: {
    hover: 'rgba(138, 107, 34, 0.1)',
    selected: 'rgba(138, 107, 34, 0.16)',
    focus: 'rgba(138, 107, 34, 0.2)',
    disabled: 'rgba(36, 30, 43, 0.12)',
    disabledBackground: 'rgba(36, 30, 43, 0.07)',
  },
};

export const MODE_TOKENS: Record<ThemeMode, ModeTokens> = {
  dark: DARK_TOKENS,
  light: LIGHT_TOKENS,
};

/** 把一套实体色值摊平成 `--wk-*` CSS 变量表，由主题工厂注入 `:root`。 */
export function cssVars(mode: ThemeMode): Record<string, string> {
  const t = MODE_TOKENS[mode];
  const vars: Record<string, string> = {
    '--wk-canvas-bg': t.canvas.bg,
    '--wk-canvas-bg-elevated': t.canvas.bgElevated,
    '--wk-canvas-surface': t.canvas.surface,
    '--wk-canvas-surface-raised': t.canvas.surfaceRaised,
    '--wk-hairline-strong': t.hairline.strong,
    '--wk-hairline-soft': t.hairline.soft,
    '--wk-ink-primary': t.ink.primary,
    '--wk-ink-dim': t.ink.dim,
    '--wk-ink-faint': t.ink.faint,
    '--wk-moon-crimson': t.bloodMoon.crimson,
    '--wk-moon-crimson-deep': t.bloodMoon.crimsonDeep,
    '--wk-moon-crimson-dark': t.bloodMoon.crimsonDark,
    '--wk-moon-gold': t.bloodMoon.gold,
    '--wk-moon-gold-light': t.bloodMoon.goldLight,
    '--wk-moon-gold-dark': t.bloodMoon.goldDark,
    '--wk-backdrop-dim': String(t.backdrop.dim),
    '--wk-backdrop-saturate': String(t.backdrop.saturate),
    '--wk-backdrop-scrim-top': t.backdrop.scrimTop,
    '--wk-backdrop-scrim-bottom': t.backdrop.scrimBottom,
    '--wk-backdrop-vignette': t.backdrop.vignette,
    '--wk-backdrop-text-plate': t.backdrop.textPlate,
    '--wk-backdrop-card-scrim': t.backdrop.cardScrim,
    '--wk-plate-strong': t.plate.strong,
    '--wk-plate-soft': t.plate.soft,
    '--wk-app-bar': t.appBar,
    '--wk-scrim-soft': t.scrim.soft,
    '--wk-ink-wash': t.inkWash,
    '--wk-shadow-card': t.shadow.card,
    '--wk-shadow-pop': t.shadow.pop,
    '--wk-shadow-soft': t.shadow.soft,
    '--wk-on-accent': t.onAccent,
    '--wk-title-gradient': t.titleGradient,
  };
  for (const [key, pair] of Object.entries(t.roleColors)) {
    vars[`--wk-role-${key.replace(/_/g, '-')}`] = pair.color;
    vars[`--wk-role-${key.replace(/_/g, '-')}-bg`] = pair.bg;
  }
  return vars;
}

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
  // 觉醒女巫尚未有独立立绘，沿用女巫的现有立绘（不另造新素材）；与母角色共用
  // 文件名是有意的，theme.test 的「一角色一图」断言把它列为已知例外。
  'wolf-killer-awakened-witch': 'role-witch',
  'wolf-killer-hunter': 'role-hunter',
  'wolf-killer-guard': 'role-guard',
  'wolf-killer-idiot': 'role-idiot',
  'wolf-killer-werewolf-king': 'role-werewolf-king',
  'wolf-killer-knight': 'role-knight',
  'wolf-killer-wolf-beauty': 'role-wolf-beauty',
  'wolf-killer-old-drunkard': 'role-old_drunkard',
  'wolf-killer-wolf-king': 'role-wolf-king',
  // 觉醒狼王尚未有独立立绘，沿用狼王的现有立绘（不另造新素材）；与母角色共用
  // 文件名是有意的，theme.test 的「一角色一图」断言把它列为已知例外。
  'wolf-killer-awakened-wolf-king': 'role-wolf-king',
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

/**
 * 宝石盘上的前景文字。盘色不随主题切换，前景因此也固定为浅墨 ——
 * 不能用 `INK.primary`（浅色模式会变深，压不进深色盘）。
 */
export const AVATAR_INK = '#F2E9DC';
