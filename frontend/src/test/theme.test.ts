import { describe, expect, it } from 'vitest';
import theme, { createAppTheme } from '../theme';
import {
  BACKDROP, BACKGROUNDS, CARD_BACK, MODE_TOKENS, ROLE_ART_BY_ID, roleArt,
} from '../theme/tokens';
import type { ModeTokens } from '../theme/tokens';

/**
 * 暂时没有独立立绘、按约定沿用同族母角色立绘的觉醒牌（值 = 母角色 id）。
 * 它们在立绘表里与母角色共用同一个文件名是**有意**的，不算「借图 bug」；
 * 补上独立立绘时从这里删掉，下面两条「一角色一图」断言会重新把它们算进去。
 */
const SHARED_FAMILY_ART = new Map([
  ['wolf-killer-awakened-wolf-king', 'wolf-killer-wolf-king'],
  ['wolf-killer-awakened-witch', 'wolf-killer-witch'],
]);

describe('血月剧场主题令牌', () => {
  it('使用血月剧场画布色与酒红金双色主轴', () => {
    expect(theme.palette.background.default).toBe('#1D0E13');
    expect(theme.palette.background.paper).toBe('#141417');
    expect(theme.palette.primary.main).toBe('#A62E4B');
    expect(theme.palette.primary.light).toBe('#D04564');
    expect(theme.palette.secondary.main).toBe('#D4A853');
  });

  it('文字层级采用暖墨色并保持深色模式', () => {
    expect(theme.palette.mode).toBe('dark');
    expect(theme.palette.text.primary).toBe('#F2E9DC');
    expect(theme.palette.text.secondary).toBe('#B3A89A');
    expect(theme.palette.text.disabled).toBe('#7D7468');
  });

  it('语义色映射到角色盘（猎人琥珀 / 平民苔绿 / 预言家雾蓝）', () => {
    expect(theme.palette.warning.main).toBe('#D9A441');
    expect(theme.palette.success.main).toBe('#93B58C');
    expect(theme.palette.info.main).toBe('#7FB4D9');
  });

  it('字体栈以 Cinzel 起手、Noto Serif SC 兜底中文', () => {
    const family = theme.typography.fontFamily ?? '';
    expect(family).toContain('Cinzel');
    expect(family).toContain('Noto Serif SC');
  });
});

function channelToLinear(channel: number): number {
  const s = channel / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function luminance(rgb: readonly [number, number, number]): number {
  return 0.2126 * channelToLinear(rgb[0])
    + 0.7152 * channelToLinear(rgb[1])
    + 0.0722 * channelToLinear(rgb[2]);
}

function contrastRatio(
  foreground: readonly [number, number, number],
  background: readonly [number, number, number],
): number {
  const lighter = Math.max(luminance(foreground), luminance(background));
  const darker = Math.min(luminance(foreground), luminance(background));
  return (lighter + 0.05) / (darker + 0.05);
}

function parseHex(hex: string): [number, number, number] {
  return [
    Number.parseInt(hex.slice(1, 3), 16),
    Number.parseInt(hex.slice(3, 5), 16),
    Number.parseInt(hex.slice(5, 7), 16),
  ];
}

function parseRgba(input: string): { r: number; g: number; b: number; a: number } {
  const match = input.match(/rgba\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\)/);
  if (!match) throw new Error(`unparsed overlay: ${input}`);
  return { r: Number(match[1]), g: Number(match[2]), b: Number(match[3]), a: Number(match[4]) };
}

function composite(
  source: readonly [number, number, number],
  overlay: string,
): [number, number, number] {
  const layer = parseRgba(overlay);
  const keep = 1 - layer.a;
  return [
    source[0] * keep + layer.r * layer.a,
    source[1] * keep + layer.g * layer.a,
    source[2] * keep + layer.b * layer.a,
  ];
}

/** 白像素经 brightness(dim) 与指定 scrim，再叠文字承托，得到正文实际落点。 */
function plateOver(tokens: ModeTokens, scrim: string): [number, number, number] {
  const brightened: [number, number, number] = [
    255 * tokens.backdrop.dim,
    255 * tokens.backdrop.dim,
    255 * tokens.backdrop.dim,
  ];
  return composite(composite(brightened, scrim), tokens.backdrop.textPlate);
}

describe('血月剧场素材令牌', () => {
  it('登记三张背景与卡背', () => {
    expect(Object.keys(BACKGROUNDS).sort()).toEqual(['game', 'lobby', 'main']);
    for (const src of Object.values(BACKGROUNDS)) expect(src).toEqual(expect.any(String));
    expect(CARD_BACK).toEqual(expect.any(String));
    // 背景与卡背是自有素材、必须入库，所以四张图的地址必须互不相同。
    expect(new Set([CARD_BACK, ...Object.values(BACKGROUNDS)]).size).toBe(4);
  });

  it('立绘表登记的是文件名，不是 URL', () => {
    // 立绘随仓库分发，但表里仍只记文件名 —— 记 URL 等于把 Vite 的
    // 构建产物路径写死，换 hash 或本地删图都会直接构建失败。
    for (const [roleId, name] of Object.entries(ROLE_ART_BY_ID)) {
      expect(name, roleId).toMatch(/^role-[a-z0-9_-]+$/);
    }
    // 每个角色一张自己的图：共用文件名就等于共用图，正是这次要消灭的问题。
    // 例外：觉醒狼王 / 觉醒女巫暂时沿用同族母角色的立绘（见上方常量）。
    const names = Object.entries(ROLE_ART_BY_ID)
      .filter(([roleId]) => !SHARED_FAMILY_ART.has(roleId))
      .map(([, name]) => name);
    expect(new Set(names).size).toBe(names.length);
    for (const [awakened, base] of SHARED_FAMILY_ART) {
      expect(ROLE_ART_BY_ID[awakened], awakened).toBe(ROLE_ART_BY_ID[base]);
    }
  });

  it('深色遮罩参数落在可读范围内，暗角从 40% 开始', () => {
    const backdrop = MODE_TOKENS.dark.backdrop;
    expect(backdrop.dim).toBeGreaterThanOrEqual(0.5);
    expect(backdrop.dim).toBeLessThanOrEqual(0.62);
    expect(backdrop.saturate).toBe(0.8);
    expect(backdrop.scrimTop).toBe('rgba(26, 10, 15, 0.25)');
    expect(backdrop.scrimBottom).toBe('rgba(26, 10, 15, 0.7)');
    expect(backdrop.vignette).toContain('transparent 40%');
    expect(backdrop.textPlate).toBe('rgba(14, 14, 18, 0.72)');
    expect(backdrop.cardScrim).toBe('linear-gradient(transparent, rgba(0, 0, 0, 0.85))');
    expect(BACKDROP.fadeMs).toBe(300);
  });

  it.each(['dark', 'light'] as const)('%s 模式正文在最亮与最暗承托上的对比度都不低于 4.5:1', (mode) => {
    const tokens = MODE_TOKENS[mode];
    for (const scrim of [tokens.backdrop.scrimTop, tokens.backdrop.scrimBottom]) {
      const plate = plateOver(tokens, scrim);
      expect(contrastRatio(parseHex(tokens.ink.primary), plate)).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(parseHex(tokens.ink.dim), plate)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('两种模式的令牌结构同形，且浅色模式输出浅色画布', () => {
    const light = createAppTheme('light');
    expect(light.palette.mode).toBe('light');
    expect(light.palette.background.default).toBe(MODE_TOKENS.light.canvas.bg);
    expect(light.palette.text.primary).toBe(MODE_TOKENS.light.ink.primary);
    expect(Object.keys(MODE_TOKENS.dark)).toEqual(Object.keys(MODE_TOKENS.light));
    expect(Object.keys(MODE_TOKENS.dark.roleColors)).toEqual(
      Object.keys(MODE_TOKENS.light.roleColors),
    );
  });
});

describe('roleArt', () => {
  /**
   * 立绘现在随仓库分发，所以预言家探针正常情况下恒为真。保留它是为了在本地
   * 手动删掉某张图时，仍能跳过「每张都必须存在」的两条断言，而不是误报。
   */
  const officialArtPresent = roleArt('wolf-killer-seer') !== CARD_BACK;

  it('未登记的角色回退卡背，绝不借用别人的图', () => {
    expect(roleArt('wolf-killer-not-a-role')).toBe(CARD_BACK);
    expect(roleArt('wolf-killer-not-a-real-role')).toBe(CARD_BACK);
  });

  it('没有角色 id 时回退卡背', () => {
    expect(roleArt(null)).toBe(CARD_BACK);
    expect(roleArt(undefined)).toBe(CARD_BACK);
    expect(roleArt('')).toBe(CARD_BACK);
  });

  it('登记过的角色要么是真图、要么是卡背，不会是第三种东西', () => {
    // 磁盘上没图时必须回退卡背，而不是给出一个 404 的地址。
    for (const roleId of Object.keys(ROLE_ART_BY_ID)) {
      const art = roleArt(roleId);
      if (art !== CARD_BACK) expect(art, roleId).toMatch(/\.webp$/);
    }
  });

  it.runIf(officialArtPresent)('磁盘上有立绘时，登记过的角色都能取到自己的图', () => {
    for (const roleId of Object.keys(ROLE_ART_BY_ID)) {
      expect(roleArt(roleId), roleId).not.toBe(CARD_BACK);
    }
  });

  it.runIf(officialArtPresent)('每个角色取到的地址互不相同（沿用同族立绘的觉醒牌除外）', () => {
    const urls = Object.keys(ROLE_ART_BY_ID)
      .filter((roleId) => !SHARED_FAMILY_ART.has(roleId))
      .map((roleId) => roleArt(roleId));
    expect(new Set(urls).size).toBe(urls.length);
  });
});
