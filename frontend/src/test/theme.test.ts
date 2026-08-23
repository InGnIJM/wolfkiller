import { describe, expect, it } from 'vitest';
import theme from '../theme';

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
