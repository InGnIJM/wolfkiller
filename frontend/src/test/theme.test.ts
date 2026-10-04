import { describe, expect, it } from 'vitest';
import theme from '../theme';
import { BACKDROP, BACKGROUNDS, CARD_BACK, INK, ROLE_ART_BY_ID, roleArt } from '../theme/tokens';

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
  it('登记三张背景与卡背', () => {
    expect(Object.keys(BACKGROUNDS).sort()).toEqual(['game', 'lobby', 'main']);
    for (const src of Object.values(BACKGROUNDS)) expect(src).toEqual(expect.any(String));
    expect(CARD_BACK).toEqual(expect.any(String));
    // 背景与卡背是自有素材、必须入库，所以四张图的地址必须互不相同。
    expect(new Set([CARD_BACK, ...Object.values(BACKGROUNDS)]).size).toBe(4);
  });

  it('立绘表登记的是文件名，不是 URL', () => {
    // 立绘不入库（role-*.webp 在 .gitignore 里），所以这张表只能记文件名 ——
    // 记 URL 就等于把「图一定在」写死，新克隆会直接构建失败。
    for (const [roleId, name] of Object.entries(ROLE_ART_BY_ID)) {
      expect(name, roleId).toMatch(/^role-[a-z0-9_-]+$/);
    }
    // 每个角色一张自己的图：共用文件名就等于共用图，正是这次要消灭的问题。
    const names = Object.values(ROLE_ART_BY_ID);
    expect(new Set(names).size).toBe(names.length);
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

describe('roleArt', () => {
  /**
   * 「抓图脚本跑过没有」的探针。不能用「任意角色有图」来判断 —— 老酒鬼的立绘
   * 是自有素材、始终入库，那个判断会永远为真。预言家是纯官方立绘，只在跑过
   * `scripts/fetch-official-role-art.py` 之后才有图。
   */
  const officialArtPresent = roleArt('wolf-killer-seer') !== CARD_BACK;

  it('未登记的角色回退卡背，绝不借用别人的图', () => {
    expect(roleArt('wolf-killer-not-a-role')).toBe(CARD_BACK);
    expect(roleArt('wolf-killer-awakened-seer')).toBe(CARD_BACK);
  });

  it('没有角色 id 时回退卡背', () => {
    expect(roleArt(null)).toBe(CARD_BACK);
    expect(roleArt(undefined)).toBe(CARD_BACK);
    expect(roleArt('')).toBe(CARD_BACK);
  });

  it('登记过的角色要么是真图、要么是卡背，不会是第三种东西', () => {
    // 立绘不入库：磁盘上没图时必须回退卡背，而不是给出一个 404 的地址。
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

  it.runIf(officialArtPresent)('每个角色取到的地址互不相同', () => {
    const urls = Object.keys(ROLE_ART_BY_ID).map((roleId) => roleArt(roleId));
    expect(new Set(urls).size).toBe(urls.length);
  });
});
