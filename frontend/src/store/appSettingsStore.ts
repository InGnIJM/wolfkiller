import { create } from 'zustand';

import * as api from '../api/client';
import type { AutoReviewSetting, PromptCaptureSetting } from './types';

interface AppSettingsState {
  setting: PromptCaptureSetting | null;
  autoReviewSetting: AutoReviewSetting | null;
  loading: boolean;
  error: string | null;
  load: () => Promise<void>;
  /** 写界面设置；`null` = 恢复为跟随环境变量。成功返回写回后的生效值，失败返回 `null`（错误只落 `error`）。 */
  setEnabled: (enabled: boolean | null) => Promise<PromptCaptureSetting | null>;
  setAutoReviewEnabled: (enabled: boolean | null) => Promise<AutoReviewSetting | null>;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export const useAppSettingsStore = create<AppSettingsState>((set) => ({
  setting: null,
  autoReviewSetting: null,
  loading: false,
  error: null,

  load: async () => {
    set({ loading: true, error: null });
    try {
      const setting = await api.fetchPromptCaptureSetting();
      let autoReview: AutoReviewSetting | null = null;
      try {
        if ('fetchAutoReviewSetting' in api && typeof api.fetchAutoReviewSetting === 'function') {
          autoReview = (await api.fetchAutoReviewSetting()) ?? null;
        }
      } catch {
        autoReview = null;
      }
      set({ setting, autoReviewSetting: autoReview, loading: false });
    } catch (error) {
      set({ error: messageOf(error), loading: false });
    }
  },

  setEnabled: async (enabled) => {
    try {
      const setting = await api.updatePromptCaptureSetting(enabled);
      set({ setting, error: null });
      return setting;
    } catch (error) {
      set({ error: messageOf(error) });
      return null;
    }
  },

  setAutoReviewEnabled: async (enabled) => {
    try {
      const autoReviewSetting = await api.updateAutoReviewSetting(enabled);
      set({ autoReviewSetting, error: null });
      return autoReviewSetting;
    } catch (error) {
      set({ error: messageOf(error) });
      return null;
    }
  },
}));
