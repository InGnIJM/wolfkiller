import { describe, expect, it } from 'vitest';

import * as labels from '../labels';
import {
  DEATH_CAUSE_LABELS, EVENT_TAGS, EXECUTION_LABELS, NIGHT_EVENT_ROLE_NAMES,
  PHASE_LABELS, WIN_REASON_LABELS,
} from '../labels';

// Vite 原生 glob，不需要 node 类型（前端 tsconfig 未引入 @types/node）。
const componentSources = import.meta.glob('../components/**/*.{ts,tsx}', {
  query: '?raw', import: 'default', eager: true,
}) as Record<string, string>;

describe('文案表本身', () => {
  it('20 个命名空间都在，且没有被清空的表', () => {
    // 命名空间被误删或被清空都会让界面退回显示机器值，而类型系统只保证「键齐全」。
    const tables = Object.entries(labels).filter(
      (entry): entry is [string, Record<string, string>] => typeof entry[1] === 'object',
    );

    expect(tables).toHaveLength(20);
    expect(tables.filter(([, table]) => Object.keys(table).length === 0)).toEqual([]);
  });
});

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

  it('舞池与面具也能查到行动者短名', () => {
    // 这两个事件没有 `night_action` 行，夜间眉标只能按事件类型认人。
    expect(NIGHT_EVENT_ROLE_NAMES.dance_pool).toBe('舞者');
    expect(NIGHT_EVENT_ROLE_NAMES.mask_granted).toBe('假面');
    expect(NIGHT_EVENT_ROLE_NAMES.night_action).toBeUndefined();
  });

  it('补齐了后端已有、界面曾直接显示英文机器值的取值', () => {
    expect(PHASE_LABELS.sheriff_election).toBe('警长竞选');
    expect(EXECUTION_LABELS.cancelled).toBe('已取消');
    expect(WIN_REASON_LABELS.wolves_outnumber_good).toBeTruthy();
    expect(Object.keys(EVENT_TAGS)).toContain('technical_abstain');
    expect(Object.keys(DEATH_CAUSE_LABELS)).toContain('charm');
  });
});
