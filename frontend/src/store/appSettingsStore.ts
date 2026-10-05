import { create } from 'zustand';

import * as api from '../api/client';
import type { PromptCaptureSetting } from './types';

interface AppSettingsState {
  setting: PromptCaptureSetting | null;
  loading: boolean;
  error: string | null;
  load: () => Promise<void>;
  /** 写界面设置；`null` = 恢复为跟随环境变量。成功返回写回后的生效值，失败返回 `null`（错误只落 `error`）。 */
  setEnabled: (enabled: boolean | null) => Promise<PromptCaptureSetting | null>;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export const useAppSettingsStore = create<AppSettingsState>((set) => ({
  setting: null,
  loading: false,
  error: null,

  load: async () => {
    set({ loading: true, error: null });
    try {
      const setting = await api.fetchPromptCaptureSetting();
      set({ setting, loading: false });
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
}));
