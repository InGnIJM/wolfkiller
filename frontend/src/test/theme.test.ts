import { describe, expect, it } from 'vitest';
import theme from '../theme';
import {
  BACKDROP, BACKGROUNDS, CARD_BACK, INK, ROLE_ART, ROLE_COLORS,
} from '../theme/tokens';
import type { RoleColorKey } from '../theme/tokens';

describe('血月剧场主题令牌', () => {
  it('使用血月剧场画布色与双色主轴', () => {
    expect(theme.palette.background.default).toBe('#0B0A0F');
    expect(theme.palette.background.paper).toBe('#171221');
    expect(theme.palette.primary.main).toBe('#C22E42');
    expect(theme.palette.primary.light).toBe('#E5484D');
    expect(theme.palette.secondary.main).toBe('#D4A853');
  });

  it('文字层级采用暖墨色并保持深色模式', () => {
    expect(theme.palette.mode).toBe('dark');
    expect(theme.palette.text.primary).toBe('#F2E9DC');
    expect(theme.palette.text.secondary).toBe('#B3A89A');
    expect(theme.palette.text.disabled).toBe('#7D7468');
  });

  it('语义色映射到角色盘（猎人琥珀 / 村民苔绿 / 预言家雾蓝）', () => {
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
function plateOver(scrim: string): [number, number, number] {
  const brightened: [number, number, number] = [
    255 * BACKDROP.dim,
    255 * BACKDROP.dim,
    255 * BACKDROP.dim,
  ];
  return composite(composite(brightened, scrim), BACKDROP.textPlate);
}

describe('血月剧场素材令牌', () => {
  it('登记三张背景、十一张立绘与卡背', () => {
    expect(Object.keys(BACKGROUNDS).sort()).toEqual(['game', 'lobby', 'main']);
    for (const src of Object.values(BACKGROUNDS)) expect(src).toEqual(expect.any(String));
    const keys = Object.keys(ROLE_COLORS) as RoleColorKey[];
    expect(Object.keys(ROLE_ART).sort()).toEqual([...keys].sort());
    for (const key of keys) expect(ROLE_ART[key]).toEqual(expect.any(String));
    expect(CARD_BACK).toEqual(expect.any(String));
    expect(new Set([CARD_BACK, ...Object.values(ROLE_ART), ...Object.values(BACKGROUNDS)]).size)
      .toBe(keys.length + 4);
  });

  it('遮罩参数落在可读范围内，暗角从 40% 开始', () => {
    expect(BACKDROP.dim).toBeGreaterThanOrEqual(0.5);
    expect(BACKDROP.dim).toBeLessThanOrEqual(0.62);
    expect(BACKDROP.saturate).toBe(0.8);
    expect(BACKDROP.scrimTop).toBe('rgba(11, 10, 15, 0.25)');
    expect(BACKDROP.scrimBottom).toBe('rgba(11, 10, 15, 0.7)');
    expect(BACKDROP.vignette).toContain('transparent 40%');
    expect(BACKDROP.textPlate).toBe('rgba(23, 18, 33, 0.72)');
    expect(BACKDROP.cardScrim).toBe('linear-gradient(transparent, rgba(11, 10, 15, 0.85))');
    expect(BACKDROP.fadeMs).toBe(300);
  });

  it('正文在最亮与最暗承托上的对比度都不低于 4.5:1', () => {
    for (const scrim of [BACKDROP.scrimTop, BACKDROP.scrimBottom]) {
      const plate = plateOver(scrim);
      expect(contrastRatio(parseHex(INK.primary), plate)).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(parseHex(INK.dim), plate)).toBeGreaterThanOrEqual(4.5);
    }
  });
});
