import { describe, expect, it } from 'vitest';

import {
  DEATH_CAUSE_LABELS, EVENT_TAGS, EXECUTION_LABELS, PHASE_LABELS, WIN_REASON_LABELS,
} from '../labels';

// Vite 原生 glob，不需要 node 类型（前端 tsconfig 未引入 @types/node）。
const componentSources = import.meta.glob('../components/**/*.{ts,tsx}', {
  query: '?raw', import: 'default', eager: true,
}) as Record<string, string>;

describe('文案统一由 src/labels.ts 管理', () => {
  it('组件里不再有本地文案表', () => {
    // 33 张表曾散在 10 个组件里，同一套词汇被抄 2-5 份；谁再写一张本地表，这里就红。
    const offenders: string[] = [];
    for (const [path, text] of Object.entries(componentSources)) {
      if (path.includes('/test/')) continue;
      for (const match of text.matchAll(/^const ([A-Z][A-Z0-9_]*(?:_LABELS|_TAGS))\b/gm)) {
        offenders.push(`${path}: ${match[1]}`);
      }
    }

    expect(offenders).toEqual([]);
  });

  it('补齐了后端已有、界面曾直接显示英文机器值的取值', () => {
    expect(PHASE_LABELS.sheriff_election).toBe('警长竞选');
    expect(EXECUTION_LABELS.cancelled).toBe('已取消');
    expect(WIN_REASON_LABELS.wolves_outnumber_good).toBeTruthy();
    expect(Object.keys(EVENT_TAGS)).toContain('technical_abstain');
    expect(Object.keys(DEATH_CAUSE_LABELS)).toContain('charm');
  });
});
