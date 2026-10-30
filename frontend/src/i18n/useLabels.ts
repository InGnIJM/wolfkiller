import { useTranslation } from 'react-i18next';

import * as labelsEn from './labelsEn';
import * as labelsZh from '../labels';

/**
 * 机器值 → 文案表的整模块访问器。
 *
 * `labels.ts`（zh）与 `i18n/labelsEn.ts`（en）导出同名同型的成员，
 * 本 hook 按当前语言返回其中一个模块，组件写 `L.PHASE_LABELS[phase]` /
 * `L.nightActionCaption(...)` 即可，不需要分支。`useTranslation()` 订阅
 * 语言变化，切换语言时组件自动重渲染。
 */
export type LabelsModule = typeof labelsZh;

export function useLabels(): LabelsModule {
  const { i18n } = useTranslation();
  return i18n.language.toLowerCase().startsWith('en') ? labelsEn : labelsZh;
}
