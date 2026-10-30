import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/**
 * 纯前端偏好（语言 / 主题模式）。与 `appSettingsStore` 的后端开关不同：
 * 它们只影响本机呈现，落 localStorage（key `wolfk.ui`），不打扰后端
 * `data/settings.json` —— 那里面刻意不做通用 KV。
 */

export type LanguagePreference = 'system' | 'zh-CN' | 'en';
export type ThemePreference = 'system' | 'dark' | 'light';
export type ResolvedLanguage = 'zh-CN' | 'en';
export type ResolvedThemeMode = 'dark' | 'light';

/** `system` 时读浏览器语言：zh* → 简体中文，其余 → English。 */
export function resolveLanguage(
  preference: LanguagePreference,
  navigatorLanguage?: string,
): ResolvedLanguage {
  if (preference !== 'system') return preference;
  const nav = navigatorLanguage
    ?? (typeof navigator === 'undefined' ? 'zh-CN' : navigator.language);
  return nav.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en';
}

/** `system` 时跟 `prefers-color-scheme`。 */
export function resolveThemeMode(
  preference: ThemePreference,
  prefersDark: boolean,
): ResolvedThemeMode {
  if (preference !== 'system') return preference;
  return prefersDark ? 'dark' : 'light';
}

interface UiSettingsState {
  language: LanguagePreference;
  themeMode: ThemePreference;
  setLanguage: (language: LanguagePreference) => void;
  setThemeMode: (themeMode: ThemePreference) => void;
}

export const useUiSettingsStore = create<UiSettingsState>()(
  persist(
    (set) => ({
      language: 'system',
      themeMode: 'system',
      setLanguage: (language) => set({ language }),
      setThemeMode: (themeMode) => set({ themeMode }),
    }),
    { name: 'wolfk.ui' },
  ),
);
