import { create } from 'zustand';

import * as api from '../api/client';
import type { ModelConfig, ModelConfigInput } from './types';

interface ModelConfigState {
  configs: ModelConfig[];
  loading: boolean;
  error: string | null;
  load: () => Promise<void>;
  create: (input: ModelConfigInput) => Promise<ModelConfig | null>;
  update: (id: string, input: ModelConfigInput) => Promise<ModelConfig | null>;
  remove: (id: string) => Promise<boolean>;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export const useModelConfigStore = create<ModelConfigState>((set) => ({
  configs: [],
  loading: false,
  error: null,

  load: async () => {
    set({ loading: true, error: null });
    try {
      const configs = await api.listModels();
      set({ configs, loading: false });
    } catch (error) {
      set({ error: messageOf(error), loading: false });
    }
  },

  create: async (input) => {
    try {
      const config = await api.createModel(input);
      set((state) => ({ configs: [...state.configs, config], error: null }));
      return config;
    } catch (error) {
      set({ error: messageOf(error) });
      return null;
    }
  },

  update: async (id, input) => {
    try {
      const config = await api.updateModel(id, input);
      set((state) => ({
        configs: state.configs.map((c) => (c.id === id ? config : c)),
        error: null,
      }));
      return config;
    } catch (error) {
      set({ error: messageOf(error) });
      return null;
    }
  },

  remove: async (id) => {
    try {
      await api.deleteModel(id);
      set((state) => ({
        configs: state.configs.filter((c) => c.id !== id),
        error: null,
      }));
      return true;
    } catch (error) {
      set({ error: messageOf(error) });
      return false;
    }
  },
}));
