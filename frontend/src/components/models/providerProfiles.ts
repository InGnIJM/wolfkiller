import type { ProviderProfileId } from '../../store/types';

export interface ProviderProfileOption {
  id: ProviderProfileId;
  /** i18n key under `models.*`; resolved via `providerProfileLabel(id, t)`. */
  labelKey: string;
  /** Hint shown as the Base URL placeholder when this profile is selected. */
  baseUrlPlaceholder: string;
}

/** Keep in sync with `ProviderProfileId` in backend `model_schemas.py`. */
export const PROVIDER_PROFILE_OPTIONS: readonly ProviderProfileOption[] = [
  { id: 'auto', labelKey: 'models.profile.auto', baseUrlPlaceholder: 'https://api.deepseek.com/v1' },
  { id: 'openai', labelKey: 'models.profile.openai', baseUrlPlaceholder: 'https://api.openai.com/v1' },
  { id: 'deepseek', labelKey: 'models.profile.deepseek', baseUrlPlaceholder: 'https://api.deepseek.com/v1' },
  { id: 'openrouter', labelKey: 'models.profile.openrouter', baseUrlPlaceholder: 'https://openrouter.ai/api/v1' },
  { id: 'opencode', labelKey: 'models.profile.opencode', baseUrlPlaceholder: 'https://opencode.ai/zen/v1' },
  { id: 'opencode-go', labelKey: 'models.profile.opencodeGo', baseUrlPlaceholder: 'https://opencode.ai/zen/go/v1' },
  { id: 'custom-openai', labelKey: 'models.profile.customOpenai', baseUrlPlaceholder: 'https://your-relay.example/v1' },
  { id: 'openai-responses', labelKey: 'models.profile.openaiResponses', baseUrlPlaceholder: 'https://api.openai.com/v1' },
  { id: 'anthropic', labelKey: 'models.profile.anthropic', baseUrlPlaceholder: 'https://api.anthropic.com' },
  { id: 'custom-anthropic', labelKey: 'models.profile.customAnthropic', baseUrlPlaceholder: 'https://your-relay.example' },
];

export function providerProfileLabel(id: string, t: (key: string) => string): string {
  const option = PROVIDER_PROFILE_OPTIONS.find((item) => item.id === id);
  return option ? t(option.labelKey) : id;
}

export function isProviderProfileId(value: string): value is ProviderProfileId {
  return PROVIDER_PROFILE_OPTIONS.some((option) => option.id === value);
}
