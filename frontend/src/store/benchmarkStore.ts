import { create } from 'zustand';

import * as api from '../api/client';
import type {
  BatchDeleteResult, BenchmarkCreateInput, BenchmarkItem, BenchmarkReport, BenchmarkRun,
} from './types';

interface BenchmarkStore {
  runs: BenchmarkRun[];
  current: BenchmarkRun | null;
  items: BenchmarkItem[];
  report: BenchmarkReport | null;
  loading: boolean;
  actionPending: boolean;
  error: string | null;
  loadRuns: () => Promise<void>;
  loadDetail: (runId: string) => Promise<void>;
  createDraft: (input: BenchmarkCreateInput) => Promise<BenchmarkRun | null>;
  control: (
    runId: string,
    action: 'start' | 'pause' | 'resume' | 'cancel',
  ) => Promise<BenchmarkRun | null>;
  rebuildReport: (runId: string) => Promise<void>;
  deleteRun: (runId: string) => Promise<boolean>;
  deleteGame: (runId: string, gameId: string) => Promise<boolean>;
  batchDeleteGames: (runId: string, gameIds: string[]) => Promise<BatchDeleteResult | null>;
  clear: () => void;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function loadAllGames(runId: string): Promise<BenchmarkItem[]> {
  const games: BenchmarkItem[] = [];
  const limit = 50;
  while (true) {
    const page = await api.fetchBenchmarkGames(runId, games.length, limit);
    games.push(...page.games);
    if (page.total !== undefined ? games.length >= page.total : page.games.length < limit) return games;
    if (page.games.length === 0) throw new Error('评测对局分页不完整，请重试。');
  }
}

let selectedRunId: string | null = null;
let viewVersion = 0;
let detailVersion = 0;
let actionVersion = 0;

function isCurrentAction(view: number, version: number): boolean {
  return view === viewVersion && version === actionVersion;
}

export const useBenchmarkStore = create<BenchmarkStore>((set, get) => ({
  runs: [],
  current: null,
  items: [],
  report: null,
  loading: false,
  actionPending: false,
  error: null,

  loadRuns: async () => {
    set({ loading: true, error: null });
    try {
      const result = await api.listBenchmarks();
      set({ runs: result.runs, loading: false });
    } catch (error) {
      set({ error: messageOf(error), loading: false });
    }
  },

  loadDetail: async (runId) => {
    if (selectedRunId !== runId) {
      selectedRunId = runId;
      viewVersion += 1;
      actionVersion += 1;
      set({ current: null, items: [], report: null, actionPending: false });
    } else if (get().actionPending) return;
    const version = ++detailVersion;
    const view = viewVersion;
    set({ loading: true, error: null });
    const [runResult, itemsResult, reportResult] = await Promise.allSettled([
      api.fetchBenchmark(runId),
      loadAllGames(runId),
      api.fetchBenchmarkReport(runId),
    ]);
    if (view !== viewVersion || version !== detailVersion) return;
    if (runResult.status === 'rejected') {
      set({ error: messageOf(runResult.reason), loading: false });
      return;
    }
    set({
      current: runResult.value,
      items: itemsResult.status === 'fulfilled' ? itemsResult.value : [],
      report: reportResult.status === 'fulfilled' ? reportResult.value : null,
      loading: false,
      error: itemsResult.status === 'rejected' ? messageOf(itemsResult.reason) : null,
    });
  },

  createDraft: async (input) => {
    const view = viewVersion;
    const action = ++actionVersion;
    set({ actionPending: true, error: null });
    try {
      const run = await api.createBenchmark(input);
      if (!isCurrentAction(view, action)) return run;
      selectedRunId = run.run_id;
      detailVersion += 1;
      set({
        current: run,
        runs: [run, ...get().runs.filter((item) => item.run_id !== run.run_id)],
        actionPending: false,
      });
      return run;
    } catch (error) {
      if (!isCurrentAction(view, action)) return null;
      set({ error: messageOf(error), actionPending: false });
      return null;
    }
  },

  control: async (runId, action) => {
    const view = viewVersion;
    const version = ++actionVersion;
    detailVersion += 1;
    set({ actionPending: true, error: null });
    try {
      const run = await api.controlBenchmark(runId, action);
      if (!isCurrentAction(view, version)) return run;
      set({
        current: run,
        runs: get().runs.map((item) => item.run_id === runId ? run : item),
        actionPending: false,
        loading: false,
      });
      return run;
    } catch (error) {
      if (!isCurrentAction(view, version)) return null;
      set({ error: messageOf(error), actionPending: false, loading: false });
      return null;
    }
  },

  rebuildReport: async (runId) => {
    const view = viewVersion;
    const version = ++actionVersion;
    detailVersion += 1;
    set({ actionPending: true, error: null });
    try {
      const report = await api.rebuildBenchmarkReport(runId);
      if (!isCurrentAction(view, version)) return;
      set({ report, actionPending: false, loading: false });
    } catch (error) {
      if (!isCurrentAction(view, version)) return;
      set({ error: messageOf(error), actionPending: false, loading: false });
    }
  },

  deleteRun: async (runId) => {
    const view = viewVersion;
    const version = ++actionVersion;
    detailVersion += 1;
    set({ actionPending: true, error: null });
    try {
      await api.deleteBenchmark(runId);
      if (!isCurrentAction(view, version)) return true;
      const current = get().current;
      set({
        runs: get().runs.filter((item) => item.run_id !== runId),
        current: current?.run_id === runId ? null : current,
        items: current?.run_id === runId ? [] : get().items,
        report: current?.run_id === runId ? null : get().report,
        actionPending: false,
        loading: false,
      });
      return true;
    } catch (error) {
      if (!isCurrentAction(view, version)) return false;
      set({ error: messageOf(error), actionPending: false, loading: false });
      return false;
    }
  },

  deleteGame: async (runId, gameId) => {
    const view = viewVersion;
    const version = ++actionVersion;
    detailVersion += 1;
    set({ actionPending: true, error: null });
    try {
      await api.deleteBenchmarkGame(runId, gameId);
      if (!isCurrentAction(view, version)) return true;
      set({ actionPending: false });
      await get().loadDetail(runId);
      return true;
    } catch (error) {
      if (!isCurrentAction(view, version)) return false;
      set({ error: messageOf(error), actionPending: false, loading: false });
      return false;
    }
  },

  batchDeleteGames: async (runId, gameIds) => {
    const view = viewVersion;
    const version = ++actionVersion;
    detailVersion += 1;
    set({ actionPending: true, error: null });
    try {
      const result = await api.batchDeleteBenchmarkGames(runId, gameIds);
      if (!isCurrentAction(view, version)) return result;
      set({ actionPending: false });
      await get().loadDetail(runId);
      set({
        error: result.failed.length > 0
          ? result.failed.map((item) => item.message).join('；')
          : get().error,
      });
      return result;
    } catch (error) {
      if (!isCurrentAction(view, version)) return null;
      set({ error: messageOf(error), actionPending: false, loading: false });
      return null;
    }
  },

  clear: () => {
    selectedRunId = null;
    viewVersion += 1;
    detailVersion += 1;
    actionVersion += 1;
    set({
      current: null,
      items: [],
      report: null,
      loading: false,
      actionPending: false,
      error: null,
    });
  },
}));
