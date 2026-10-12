import { describe, expect, it } from 'vitest';

import { computeStageLayout } from '../seatLayout';

describe('computeStageLayout', () => {
  it('keeps a 12-seat wide short stage on the ellipse even with a vote line', () => {
    const layout = computeStageLayout({
      width: 1100,
      height: 360,
      seatCount: 12,
      showVoteLine: true,
    });
    expect(layout.mode).toBe('ellipse');
    expect(layout.cardH).toBeGreaterThanOrEqual(56);
    expect(layout.panelMaxH).toBeGreaterThan(0);
  });

  it('uses compact columns on a 12-seat narrow stage', () => {
    const layout = computeStageLayout({
      width: 500,
      height: 600,
      seatCount: 12,
    });
    expect(layout.mode).toBe('compact');
    expect(layout.compactCardH).toBeGreaterThanOrEqual(44);
    expect(layout.compactCardH).toBeLessThanOrEqual(72);
    expect(layout.compactFits).toBe(true);
  });

  it('flips compactFits when a 12-seat column is shorter than the minimum cards', () => {
    const tall = computeStageLayout({ width: 500, height: 600, seatCount: 12 });
    const short = computeStageLayout({ width: 500, height: 220, seatCount: 12 });
    expect(tall.mode).toBe('compact');
    expect(short.mode).toBe('compact');
    expect(tall.compactFits).toBe(true);
    expect(short.compactFits).toBe(false);
    expect(short.compactCardH).toBe(44);
  });

  it('keeps a 9-seat 800x600 stage on the ellipse', () => {
    const layout = computeStageLayout({
      width: 800,
      height: 600,
      seatCount: 9,
    });
    expect(layout.mode).toBe('ellipse');
  });

  // 舞台尺寸在 ResizeObserver 回调之前是 0：此时必须给出可渲染的兜底尺寸，
  // 而不是让椭圆半径与面板宽度算出 NaN。
  it('falls back to fixed sizes for a zero-sized stage', () => {
    const layout = computeStageLayout({ width: 0, height: 0, seatCount: 12 });
    expect(layout.mode).toBe('compact');
    expect(layout.rx).toBeGreaterThan(0);
    expect(layout.ry).toBeGreaterThan(0);
    expect(layout.panelW).toBeGreaterThan(0);
    expect(layout.compactFits).toBe(false);
  });

  it('keeps a 12-seat zero-width but tall stage on the guarded compact path', () => {
    const layout = computeStageLayout({ width: 0, height: 700, seatCount: 12 });
    expect(layout.mode).toBe('compact');
    expect(layout.cardSize).toBe(52);
    // 护栏优先于任何高度计算：首帧（尺寸还是 0）一律按「列放不下」自居，
    // 于是只有这一帧不居中；ResizeObserver 回调后立即恢复真实判定。
    expect(layout.compactFits).toBe(false);
  });
});
