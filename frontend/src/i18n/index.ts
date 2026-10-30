import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import { resolveLanguage, useUiSettingsStore } from '../store/uiSettingsStore';
import { en } from './en';
import { zh } from './zh';

/**
 * 前端 UI 文案的 i18n 入口。
 *
 * - 资源只含 `zh-CN` / `en` 两个 locale；「跟随系统」由
 *   `resolveLanguage()` 在启动时与设置变更时解析。
 * - 组件一律走 `useTranslation()`；非 React 代码（store 错误串）用
 *   导出的 `i18n.t()`。
 * - 机器值文案表不经此模块：`labels.ts`（zh）/ `labelsEn.ts`（en）
 *   由 `useLabels()` 切换，保持「封闭联合键」的 tsc 门禁与后端
 *   `test_values.py` 的正则门禁。
 */
void i18n.use(initReactI18next).init({
  resources: {
    'zh-CN': { translation: zh },
    en: { translation: en },
  },
  lng: resolveLanguage(useUiSettingsStore.getState().language),
  fallbackLng: 'zh-CN',
  interpolation: { escapeValue: false },
  returnEmptyString: false,
});

export default i18n;
