import type { ProviderProfileId } from '../../store/types';

export interface ProviderProfileOption {
  id: ProviderProfileId;
  label: string;
  /** Hint shown as the Base URL placeholder when this profile is selected. */
  baseUrlPlaceholder: string;
}

/** Keep in sync with `ProviderProfileId` in backend `model_schemas.py`. */
export const PROVIDER_PROFILE_OPTIONS: readonly ProviderProfileOption[] = [
  { id: 'auto', label: '自动识别（按 Base URL 域名）', baseUrlPlaceholder: 'https://api.deepseek.com/v1' },
  { id: 'openai', label: 'OpenAI', baseUrlPlaceholder: 'https://api.openai.com/v1' },
  { id: 'deepseek', label: 'DeepSeek', baseUrlPlaceholder: 'https://api.deepseek.com/v1' },
  { id: 'openrouter', label: 'OpenRouter', baseUrlPlaceholder: 'https://openrouter.ai/api/v1' },
  { id: 'custom-openai', label: '自定义 · OpenAI 兼容（Chat Completions）', baseUrlPlaceholder: 'https://your-relay.example/v1' },
  { id: 'anthropic', label: 'Anthropic（Messages API）', baseUrlPlaceholder: 'https://api.anthropic.com' },
  { id: 'custom-anthropic', label: '自定义 · Anthropic 兼容（Messages API）', baseUrlPlaceholder: 'https://your-relay.example' },
];

export function providerProfileLabel(id: string): string {
  return PROVIDER_PROFILE_OPTIONS.find((option) => option.id === id)?.label ?? id;
}

export function isProviderProfileId(value: string): value is ProviderProfileId {
  return PROVIDER_PROFILE_OPTIONS.some((option) => option.id === value);
}
