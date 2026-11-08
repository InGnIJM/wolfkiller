import type { TFunction } from 'i18next';
import { MenuItem } from '@mui/material';

import type { ModelConfig } from '../../store/types';

export const EFFORT_VALUES = ['none', 'low', 'medium', 'high', 'max'] as const;

export function effortMenuItems(t: TFunction) {
  return [
    <MenuItem key="" value="">{t('create.effortInherit')}</MenuItem>,
    ...EFFORT_VALUES.map((value) => (
      <MenuItem key={value} value={value}>
        {t(`models.reasoningEffortOption.${value}`)}
      </MenuItem>
    )),
  ];
}

/** 该配置可提供的全部模型 id；``model_ids[0]`` 即默认。 */
export function modelListFor(config: ModelConfig | undefined): string[] | null {
  if (!config) return null;
  return config.model_ids?.length ? config.model_ids : [config.model_id];
}

export function modelDefaultFor(config: ModelConfig | undefined): string {
  return config?.model_ids?.[0] ?? config?.model_id ?? '';
}
