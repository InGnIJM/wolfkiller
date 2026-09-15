import { afterEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api/client';
import { useBenchmarkStore } from '../benchmarkStore';
import type { BenchmarkReport as apiReport, BenchmarkRun } from '../types';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

vi.mock('../../api/client', () => ({
  listBenchmarks: vi.fn(),
  fetchBenchmark: vi.fn(),
  fetchBenchmarkGames: vi.fn(),
  fetchBenchmarkReport: vi.fn(),
  createBenchmark: vi.fn(),
  controlBenchmark: vi.fn(),
  rebuildBenchmarkReport: vi.fn(),
  deleteBenchmark: vi.fn(),
  deleteBenchmarkGame: vi.fn(),
  batchDeleteBenchmarkGames: vi.fn(),
}));

const run: BenchmarkRun = {
  run_id: 'run-1', client_request_id: 'request-1', name: '回归',
  mode: 'paired_regression', status: 'pending', config: {},
  created_at: '2026-09-06T00:00:00Z', updated_at: '2026-09-06T00:00:00Z',
};

afterEach(() => {
  useBenchmarkStore.getState().clear();
  useBenchmarkStore.setState({
    runs: [], current: null, items: [], report: null,
    loading: false, actionPending: false, error: null,
  });
  vi.resetAllMocks();
});

describe('benchmark store', () => {
  it('keeps the latest refresh and skips polling while a control is pending', async () => {
    const older = deferred<BenchmarkRun>();
    vi.mocked(api.fetchBenchmark).mockReturnValueOnce(older.promise).mockResolvedValue(run);
    vi.mocked(api.fetchBenchmarkGames).mockResolvedValue({ games: [] });
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({ status: 'pending' });
    const request = useBenchmarkStore.getState().loadDetail(run.run_id);
    await useBenchmarkStore.getState().loadDetail(run.run_id);
    older.resolve({ ...run, name: 'old' });
    await request;
    expect(useBenchmarkStore.getState().current?.name).toBe(run.name);
    const control = deferred<BenchmarkRun>();
    vi.mocked(api.controlBenchmark).mockReturnValueOnce(control.promise);
    const action = useBenchmarkStore.getState().control(run.run_id, 'start');
    await useBenchmarkStore.getState().loadDetail(run.run_id);
    expect(api.fetchBenchmark).toHaveBeenCalledTimes(2);
    control.resolve({ ...run, status: 'running' });
    await action;
    expect(useBenchmarkStore.getState().current?.status).toBe('running');
  });

  it('reports an incomplete pagination response instead of hiding later failures', async () => {
    vi.mocked(api.fetchBenchmark).mockResolvedValue(run);
    vi.mocked(api.fetchBenchmarkGames).mockResolvedValue({ games: [], total: 1 });
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({ status: 'pending' });
    await useBenchmarkStore.getState().loadDetail(run.run_id);
    expect(useBenchmarkStore.getState().error).toContain('分页不完整');
  });

  it.each(['create', 'control', 'rebuild'] as const)('ignores a stale %s success after clearing the view', async (operation) => {
    const pending = deferred<BenchmarkRun>();
    const report = deferred<apiReport>();
    vi.mocked(api.createBenchmark).mockReturnValueOnce(pending.promise);
    vi.mocked(api.controlBenchmark).mockReturnValueOnce(pending.promise);
    vi.mocked(api.rebuildBenchmarkReport).mockReturnValueOnce(report.promise);
    const input = { client_request_id: 'c', name: 'n', mode: 'mixed_arena' as const, seed: 1,
      scenario: { scenario_id: 's', role_counts: { villager: 1 } }, games: 1, models: [] };
    const request = operation === 'create' ? useBenchmarkStore.getState().createDraft(input)
      : operation === 'control' ? useBenchmarkStore.getState().control(run.run_id, 'start')
        : useBenchmarkStore.getState().rebuildReport(run.run_id);
    useBenchmarkStore.getState().clear();
    if (operation === 'rebuild') report.resolve({ status: 'ready' }); else pending.resolve(run);
    await request;
    expect(useBenchmarkStore.getState()).toMatchObject({ current: null, report: null, actionPending: false });
  });

  it.each(['create', 'control', 'rebuild'] as const)('ignores a stale %s failure after clearing the view', async (operation) => {
    const pending = deferred<BenchmarkRun>();
    const report = deferred<apiReport>();
    vi.mocked(api.createBenchmark).mockReturnValueOnce(pending.promise);
    vi.mocked(api.controlBenchmark).mockReturnValueOnce(pending.promise);
    vi.mocked(api.rebuildBenchmarkReport).mockReturnValueOnce(report.promise);
    const input = { client_request_id: 'c', name: 'n', mode: 'mixed_arena' as const, seed: 1,
      scenario: { scenario_id: 's', role_counts: { villager: 1 } }, games: 1, models: [] };
    const request = operation === 'create' ? useBenchmarkStore.getState().createDraft(input)
      : operation === 'control' ? useBenchmarkStore.getState().control(run.run_id, 'start')
        : useBenchmarkStore.getState().rebuildReport(run.run_id);
    useBenchmarkStore.getState().clear();
    if (operation === 'rebuild') report.reject(new Error('old')); else pending.reject(new Error('old'));
    await request;
    expect(useBenchmarkStore.getState()).toMatchObject({ error: null, actionPending: false });
  });

  it('ignores old detail, control, and report responses after switching tasks', async () => {
    const oldDetail = deferred<BenchmarkRun>();
    const oldControl = deferred<BenchmarkRun>();
    const oldReport = deferred<apiReport>();
    vi.mocked(api.fetchBenchmark).mockReturnValueOnce(oldDetail.promise);
    vi.mocked(api.fetchBenchmarkGames).mockResolvedValue({ games: [] });
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({ status: 'pending' });
    vi.mocked(api.controlBenchmark).mockReturnValueOnce(oldControl.promise);
    vi.mocked(api.rebuildBenchmarkReport).mockReturnValueOnce(oldReport.promise);
    const detailRequest = useBenchmarkStore.getState().loadDetail('run-1');
    const controlRequest = useBenchmarkStore.getState().control('run-1', 'start');
    const reportRequest = useBenchmarkStore.getState().rebuildReport('run-1');
    const other = { ...run, run_id: 'run-2' };
    vi.mocked(api.fetchBenchmark).mockResolvedValueOnce(other);
    await useBenchmarkStore.getState().loadDetail('run-2');
    oldDetail.resolve(run);
    oldControl.resolve({ ...run, status: 'running' });
    oldReport.resolve({ metric_version: 'old' });
    await Promise.all([detailRequest, controlRequest, reportRequest]);
    expect(useBenchmarkStore.getState().current).toEqual(other);
    expect(useBenchmarkStore.getState().report).toEqual({ status: 'pending' });
  });

  it('invalidates pending detail when cleared', async () => {
    const pending = deferred<BenchmarkRun>();
    vi.mocked(api.fetchBenchmark).mockReturnValueOnce(pending.promise);
    vi.mocked(api.fetchBenchmarkGames).mockResolvedValue({ games: [] });
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({ status: 'pending' });
    const request = useBenchmarkStore.getState().loadDetail('run-1');
    useBenchmarkStore.getState().clear();
    pending.resolve(run);
    await request;
    expect(useBenchmarkStore.getState().current).toBeNull();
    expect(useBenchmarkStore.getState().report).toBeNull();
  });

  it('loads failures beyond the first 50 scheduled games', async () => {
    vi.mocked(api.fetchBenchmark).mockResolvedValue(run);
    const item = { run_id: run.run_id, item_index: 0, scenario_id: 's', pair_id: null,
      block_index: 0, assignment: {}, game_id: null, status: 'completed', terminal_reason: null };
    vi.mocked(api.fetchBenchmarkGames)
      .mockResolvedValueOnce({ games: Array.from({ length: 50 }, (_, index) => ({ ...item, item_index: index })), total: 51 })
      .mockResolvedValueOnce({ games: [{ ...item, item_index: 50, status: 'failed' }], total: 51 });
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({ status: 'pending' });
    await useBenchmarkStore.getState().loadDetail(run.run_id);
    expect(useBenchmarkStore.getState().items).toHaveLength(51);
    expect(useBenchmarkStore.getState().items[50].status).toBe('failed');
  });
  it('loads the run list and reports a non-Error failure', async () => {
    vi.mocked(api.listBenchmarks).mockResolvedValue({ runs: [run] });

    await useBenchmarkStore.getState().loadRuns();
    expect(useBenchmarkStore.getState().runs).toEqual([run]);
    expect(useBenchmarkStore.getState().loading).toBe(false);

    vi.mocked(api.listBenchmarks).mockRejectedValue('offline');
    await useBenchmarkStore.getState().loadRuns();
    expect(useBenchmarkStore.getState().error).toBe('offline');
  });

  it('keeps usable detail and marks optional report data as partial', async () => {
    vi.mocked(api.fetchBenchmark).mockResolvedValue(run);
    vi.mocked(api.fetchBenchmarkGames).mockResolvedValue({ games: [{
      run_id: 'run-1', item_index: 0, scenario_id: 'standard', pair_id: 'p1',
      block_index: 0, assignment: {}, game_id: null, status: 'pending', terminal_reason: null,
    }] });
    vi.mocked(api.fetchBenchmarkReport).mockRejectedValue(new Error('report pending'));

    await useBenchmarkStore.getState().loadDetail('run-1');

    expect(useBenchmarkStore.getState().current).toEqual(run);
    expect(useBenchmarkStore.getState().items).toHaveLength(1);
    expect(useBenchmarkStore.getState().report).toBeNull();
    expect(useBenchmarkStore.getState().error).toBeNull();
  });

  it('stops detail loading when the run fails and surfaces an item failure', async () => {
    vi.mocked(api.fetchBenchmark).mockRejectedValueOnce(new Error('run missing'));
    vi.mocked(api.fetchBenchmarkGames).mockResolvedValueOnce({ games: [] });
    vi.mocked(api.fetchBenchmarkReport).mockRejectedValueOnce(new Error('pending'));

    await useBenchmarkStore.getState().loadDetail('missing');
    expect(useBenchmarkStore.getState().error).toBe('run missing');
    expect(useBenchmarkStore.getState().loading).toBe(false);

    vi.mocked(api.fetchBenchmark).mockResolvedValueOnce(run);
    vi.mocked(api.fetchBenchmarkGames).mockRejectedValueOnce(new Error('items unavailable'));
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValueOnce({
      metric_version: 'v2', input_digest: 'digest', generated_at: '2026-09-06T00:00:00Z',
      provisional: true, summary: {}, metrics: {}, data_quality: {},
    });
    await useBenchmarkStore.getState().loadDetail('run-1');
    expect(useBenchmarkStore.getState().items).toEqual([]);
    expect(useBenchmarkStore.getState().report?.metric_version).toBe('v2');
    expect(useBenchmarkStore.getState().error).toBe('items unavailable');
  });

  it('creates a draft without starting it and applies explicit controls separately', async () => {
    vi.mocked(api.createBenchmark).mockResolvedValue(run);
    vi.mocked(api.controlBenchmark).mockResolvedValue({ ...run, status: 'running' });
    const input = {
      client_request_id: 'request-1', name: '回归', mode: 'paired_regression' as const, seed: 42,
      scenario: { scenario_id: 'standard', role_counts: { villager: 2 } },
      repetitions: 1, baseline: { model_config_id: 'a' }, candidate: { model_config_id: 'b' },
    };

    await useBenchmarkStore.getState().createDraft(input);
    expect(api.createBenchmark).toHaveBeenCalledWith(input);
    expect(api.controlBenchmark).not.toHaveBeenCalled();

    await useBenchmarkStore.getState().control('run-1', 'start');
    expect(api.controlBenchmark).toHaveBeenCalledWith('run-1', 'start');
    expect(useBenchmarkStore.getState().current?.status).toBe('running');
  });

  it('replaces a duplicate draft, retains other runs, and handles create/control failures', async () => {
    const other = { ...run, run_id: 'run-2', client_request_id: 'request-2' };
    useBenchmarkStore.setState({ runs: [run, other] });
    vi.mocked(api.createBenchmark).mockResolvedValue({ ...run, name: '更新的草稿' });

    await useBenchmarkStore.getState().createDraft({
      client_request_id: 'request-1', name: '更新的草稿', mode: 'mixed_arena', seed: 1,
      scenario: { scenario_id: 's', role_counts: { villager: 1 } }, games: 1, models: [],
    });
    expect(useBenchmarkStore.getState().runs.map((item) => item.run_id)).toEqual(['run-1', 'run-2']);

    vi.mocked(api.createBenchmark).mockRejectedValueOnce(new Error('create failed'));
    expect(await useBenchmarkStore.getState().createDraft({
      client_request_id: 'request-3', name: '失败', mode: 'mixed_arena', seed: 1,
      scenario: { scenario_id: 's', role_counts: { villager: 1 } }, games: 1, models: [],
    })).toBeNull();
    expect(useBenchmarkStore.getState().error).toBe('create failed');

    vi.mocked(api.controlBenchmark).mockResolvedValueOnce({ ...run, status: 'paused' });
    await useBenchmarkStore.getState().control('run-1', 'pause');
    expect(useBenchmarkStore.getState().runs[1]).toEqual(other);
    vi.mocked(api.controlBenchmark).mockRejectedValueOnce(new Error('control failed'));
    expect(await useBenchmarkStore.getState().control('run-1', 'resume')).toBeNull();
    expect(useBenchmarkStore.getState().error).toBe('control failed');
  });

  it('rebuilds reports, reports rebuild failures, and clears transient detail state', async () => {
    const report = {
      metric_version: 'v2', input_digest: 'digest', generated_at: '2026-09-06T00:00:00Z',
      provisional: false, summary: {}, metrics: {}, data_quality: {},
    };
    vi.mocked(api.rebuildBenchmarkReport).mockResolvedValueOnce(report);
    await useBenchmarkStore.getState().rebuildReport('run-1');
    expect(useBenchmarkStore.getState().report).toEqual(report);

    vi.mocked(api.rebuildBenchmarkReport).mockRejectedValueOnce(new Error('rebuild failed'));
    await useBenchmarkStore.getState().rebuildReport('run-1');
    expect(useBenchmarkStore.getState().error).toBe('rebuild failed');
    expect(useBenchmarkStore.getState().actionPending).toBe(false);

    useBenchmarkStore.getState().clear();
    expect(useBenchmarkStore.getState()).toMatchObject({
      current: null, items: [], report: null, loading: false,
      actionPending: false, error: null,
    });
  });

  it('deletes runs and games then reloads remaining detail', async () => {
    const other = { ...run, run_id: 'run-2' };
    useBenchmarkStore.setState({
      current: other, runs: [run, other], items: [{
        run_id: 'run-2', item_index: 0, scenario_id: 's', pair_id: null,
        block_index: 0, assignment: {}, game_id: null, status: 'pending', terminal_reason: null,
      }], report: { status: 'ready' },
    });
    vi.mocked(api.deleteBenchmark).mockResolvedValue(undefined);
    expect(await useBenchmarkStore.getState().deleteRun(run.run_id)).toBe(true);
    expect(useBenchmarkStore.getState().current).toEqual(other);
    expect(useBenchmarkStore.getState().report).toEqual({ status: 'ready' });
    expect(useBenchmarkStore.getState().runs).toEqual([other]);

    expect(await useBenchmarkStore.getState().deleteRun(other.run_id)).toBe(true);
    expect(useBenchmarkStore.getState().current).toBeNull();
    expect(useBenchmarkStore.getState().runs).toEqual([]);

    vi.mocked(api.deleteBenchmark).mockRejectedValueOnce(new Error('run busy'));
    expect(await useBenchmarkStore.getState().deleteRun(run.run_id)).toBe(false);
    expect(useBenchmarkStore.getState().error).toBe('run busy');

    vi.mocked(api.fetchBenchmark).mockResolvedValue(run);
    vi.mocked(api.fetchBenchmarkGames).mockResolvedValue({ games: [] });
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({ status: 'ready' });
    vi.mocked(api.deleteBenchmarkGame).mockResolvedValue(undefined);
    expect(await useBenchmarkStore.getState().deleteGame(run.run_id, 'g1')).toBe(true);
    expect(api.fetchBenchmark).toHaveBeenCalled();

    vi.mocked(api.deleteBenchmarkGame).mockRejectedValueOnce(new Error('game busy'));
    expect(await useBenchmarkStore.getState().deleteGame(run.run_id, 'g1')).toBe(false);
    expect(useBenchmarkStore.getState().error).toBe('game busy');

    vi.mocked(api.batchDeleteBenchmarkGames).mockResolvedValueOnce({
      deleted: ['g1'], failed: [{ game_id: 'g2', code: 'not_found', message: 'missing' }],
    });
    const partial = await useBenchmarkStore.getState().batchDeleteGames(run.run_id, ['g1', 'g2']);
    expect(partial?.failed).toHaveLength(1);
    expect(useBenchmarkStore.getState().error).toBe('missing');

    vi.mocked(api.batchDeleteBenchmarkGames).mockResolvedValueOnce({ deleted: ['g1'], failed: [] });
    const cleared = await useBenchmarkStore.getState().batchDeleteGames(run.run_id, ['g1']);
    expect(cleared?.failed).toEqual([]);
    expect(useBenchmarkStore.getState().error).toBeNull();

    vi.mocked(api.batchDeleteBenchmarkGames).mockRejectedValueOnce('offline');
    expect(await useBenchmarkStore.getState().batchDeleteGames(run.run_id, ['g1'])).toBeNull();
    expect(useBenchmarkStore.getState().error).toBe('offline');
  });

  it.each(['deleteRun', 'deleteGame', 'batchDelete'] as const)(
    'ignores a stale %s success after clearing the view',
    async (operation) => {
      const pending = deferred<void>();
      const batch = deferred<{ deleted: string[]; failed: [] }>();
      vi.mocked(api.deleteBenchmark).mockReturnValueOnce(pending.promise);
      vi.mocked(api.deleteBenchmarkGame).mockReturnValueOnce(pending.promise);
      vi.mocked(api.batchDeleteBenchmarkGames).mockReturnValueOnce(batch.promise);
      const request = operation === 'deleteRun'
        ? useBenchmarkStore.getState().deleteRun('run-1')
        : operation === 'deleteGame'
          ? useBenchmarkStore.getState().deleteGame('run-1', 'g1')
          : useBenchmarkStore.getState().batchDeleteGames('run-1', ['g1']);
      useBenchmarkStore.getState().clear();
      if (operation === 'batchDelete') batch.resolve({ deleted: ['g1'], failed: [] });
      else pending.resolve(undefined);
      await request;
      expect(useBenchmarkStore.getState()).toMatchObject({ current: null, actionPending: false, error: null });
    },
  );

  it.each(['deleteRun', 'deleteGame', 'batchDelete'] as const)(
    'ignores a stale %s failure after clearing the view',
    async (operation) => {
      const pending = deferred<void>();
      const batch = deferred<{ deleted: string[]; failed: [] }>();
      vi.mocked(api.deleteBenchmark).mockReturnValueOnce(pending.promise);
      vi.mocked(api.deleteBenchmarkGame).mockReturnValueOnce(pending.promise);
      vi.mocked(api.batchDeleteBenchmarkGames).mockReturnValueOnce(batch.promise);
      const request = operation === 'deleteRun'
        ? useBenchmarkStore.getState().deleteRun('run-1')
        : operation === 'deleteGame'
          ? useBenchmarkStore.getState().deleteGame('run-1', 'g1')
          : useBenchmarkStore.getState().batchDeleteGames('run-1', ['g1']);
      useBenchmarkStore.getState().clear();
      if (operation === 'batchDelete') batch.reject(new Error('old'));
      else pending.reject(new Error('old'));
      await request;
      expect(useBenchmarkStore.getState()).toMatchObject({ error: null, actionPending: false });
    },
  );
});
