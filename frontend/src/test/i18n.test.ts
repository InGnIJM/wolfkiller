// @vitest-environment jsdom

import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import i18n from '../i18n';
import { en } from '../i18n/en';
import * as labelsEn from '../i18n/labelsEn';
import { useLabels } from '../i18n/useLabels';
import { zh } from '../i18n/zh';
import * as labelsZh from '../labels';
import {
  resolveLanguage, resolveThemeMode, useUiSettingsStore,
} from '../store/uiSettingsStore';

type Flattened = Record<string, string>;

// 资源树拍平成 `a.b.c` 键，zh/en 两份必须完全同构：
// en: typeof zh 只保证编译期，运行时少键会渲染出键名本身。
function flatten(node: object, prefix = '', out: Flattened = {}): Flattened {
  for (const [key, value] of Object.entries(node)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'object' && value !== null) flatten(value as object, path, out);
    else out[path] = String(value);
  }
  return out;
}

describe('i18n 资源树', () => {
  const zhFlat = flatten(zh);
  const enFlat = flatten(en);

  it('zh/en 键集合完全一致', () => {
    expect(Object.keys(enFlat).sort()).toEqual(Object.keys(zhFlat).sort());
  });

  it('每个 en 文案都是非空且不同于键名', () => {
    for (const [key, value] of Object.entries(enFlat)) {
      expect(value.length, key).toBeGreaterThan(0);
      expect(value, key).not.toBe(key);
    }
  });

  it('插值占位符 {{x}} 两种语言参数集一致', () => {
    const params = (value: string) =>
      [...value.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort();
    for (const key of Object.keys(zhFlat)) {
      expect(params(enFlat[key]), key).toEqual(params(zhFlat[key]));
    }
  });
});

describe('labels.ts 与 labelsEn.ts 模块对齐', () => {
  it('导出名集合一致', () => {
    const zhExports = Object.keys(labelsZh).sort();
    const enExports = Object.keys(labelsEn).sort();
    expect(enExports).toEqual(zhExports);
  });

  it('每张标签表的键集一致', () => {
    for (const [name, zhTable] of Object.entries(labelsZh)) {
      if (typeof zhTable !== 'object' || zhTable === null) continue;
      const enTable = (labelsEn as Record<string, unknown>)[name];
      expect(enTable, name).toBeDefined();
      expect(Object.keys(enTable as object).sort(), name)
        .toEqual(Object.keys(zhTable).sort());
    }
  });

  it('每个函数成员在 en 里也是函数', () => {
    for (const [name, value] of Object.entries(labelsZh)) {
      if (typeof value !== 'function') continue;
      expect(typeof (labelsEn as Record<string, unknown>)[name], name).toBe('function');
    }
  });
});

describe('useLabels', () => {
  it('zh-CN 返回中文模块', async () => {
    await i18n.changeLanguage('zh-CN');
    const { result } = renderHook(() => useLabels());
    expect(result.current).toBe(labelsZh);
  });

  it('en 返回英文模块', async () => {
    await i18n.changeLanguage('en');
    const { result } = renderHook(() => useLabels());
    expect(result.current).toBe(labelsEn);
    await i18n.changeLanguage('zh-CN');
  });
});

describe('uiSettingsStore 解析逻辑', () => {
  it('resolveLanguage：显式偏好直接生效', () => {
    expect(resolveLanguage('zh-CN')).toBe('zh-CN');
    expect(resolveLanguage('en')).toBe('en');
  });

  it('resolveLanguage：system 时 zh* → 简体，其余 → English', () => {
    expect(resolveLanguage('system', 'zh-CN')).toBe('zh-CN');
    expect(resolveLanguage('system', 'zh-TW')).toBe('zh-CN');
    expect(resolveLanguage('system', 'en-US')).toBe('en');
    expect(resolveLanguage('system', 'ja-JP')).toBe('en');
  });

  it('resolveLanguage：无 navigator 环境回落 zh-CN', () => {
    vi.stubGlobal('navigator', undefined);
    try {
      expect(resolveLanguage('system')).toBe('zh-CN');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('resolveThemeMode：system 跟 prefers-color-scheme', () => {
    expect(resolveThemeMode('dark', false)).toBe('dark');
    expect(resolveThemeMode('light', true)).toBe('light');
    expect(resolveThemeMode('system', true)).toBe('dark');
    expect(resolveThemeMode('system', false)).toBe('light');
  });

  it('setLanguage/setThemeMode 写入并持久化到 localStorage', () => {
    useUiSettingsStore.getState().setLanguage('en');
    useUiSettingsStore.getState().setThemeMode('light');
    expect(useUiSettingsStore.getState().language).toBe('en');
    expect(useUiSettingsStore.getState().themeMode).toBe('light');
    const persisted = JSON.parse(localStorage.getItem('wolfk.ui') ?? '{}');
    expect(persisted.state?.language).toBe('en');
    expect(persisted.state?.themeMode).toBe('light');
    useUiSettingsStore.getState().setLanguage('zh-CN');
    useUiSettingsStore.getState().setThemeMode('system');
  });
});
