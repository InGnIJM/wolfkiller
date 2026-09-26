import { describe, expect, it } from 'vitest';

import {
  PROVIDER_PROFILE_OPTIONS, isProviderProfileId, providerProfileLabel,
} from '../providerProfiles';

describe('providerProfiles', () => {
  it('exposes the OpenCode profiles', () => {
    expect(PROVIDER_PROFILE_OPTIONS.map((option) => option.id)).toEqual([
      'auto', 'openai', 'deepseek', 'openrouter', 'opencode', 'opencode-go',
      'custom-openai', 'openai-responses', 'anthropic', 'custom-anthropic',
    ]);
    expect(isProviderProfileId('opencode')).toBe(true);
    expect(isProviderProfileId('opencode-go')).toBe(true);
    expect(providerProfileLabel('opencode')).toBe('OpenCode Zen（自动附带 x-opencode-session）');
    expect(providerProfileLabel('opencode-go')).toBe('OpenCode Go（自动附带 x-opencode-session）');
  });

  it('exposes the Responses dialect profile', () => {
    expect(isProviderProfileId('openai-responses')).toBe(true);
    expect(providerProfileLabel('openai-responses')).toBe('OpenAI Responses API（/responses）');
    expect(
      PROVIDER_PROFILE_OPTIONS.find((o) => o.id === 'openai-responses')?.baseUrlPlaceholder,
    ).toBe('https://api.openai.com/v1');
  });

  it('keeps the OpenCode Base URL placeholders', () => {
    const placeholderOf = (id: string) =>
      PROVIDER_PROFILE_OPTIONS.find((option) => option.id === id)?.baseUrlPlaceholder;
    expect(placeholderOf('opencode')).toBe('https://opencode.ai/zen/v1');
    expect(placeholderOf('opencode-go')).toBe('https://opencode.ai/zen/go/v1');
  });

  it('rejects unknown profile ids', () => {
    expect(isProviderProfileId('opencode-zen')).toBe(false);
    expect(isProviderProfileId('')).toBe(false);
    expect(providerProfileLabel('mystery')).toBe('mystery');
  });
});
