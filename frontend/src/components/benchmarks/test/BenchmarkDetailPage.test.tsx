// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../api/client';
import { useBenchmarkStore } from '../../../store/benchmarkStore';
import BenchmarkDetailPage from '../BenchmarkDetailPage';

vi.mock('../../../api/client', () => ({
  fetchBenchmark: vi.fn(), fetchBenchmarkGames: vi.fn(), fetchBenchmarkReport: vi.fn(),
  controlBenchmark: vi.fn(), rebuildBenchmarkReport: vi.fn(),
  deleteBenchmark: vi.fn(), deleteBenchmarkGame: vi.fn(), batchDeleteBenchmarkGames: vi.fn(),
  benchmarkExportUrl: () => '/export',
}));
afterEach(() => { cleanup(); useBenchmarkStore.getState().clear(); vi.resetAllMocks(); vi.useRealTimers(); });

async function showDetail(
  games: Array<Record<string, unknown>> = [],
  { fakeTimers = true }: { fakeTimers?: boolean } = {},
) {
  if (fakeTimers) vi.useFakeTimers();
  vi.mocked(api.fetchBenchmark).mockResolvedValue({
    run_id: 'r', client_request_id: 'c', name: 'finished run', mode: 'mixed_arena',
    status: 'completed', config: {}, created_at: '', updated_at: '',
  });
  vi.mocked(api.fetchBenchmarkGames).mockResolvedValue({ games: games as never });
  await act(async () => {
    render(<MemoryRouter initialEntries={['/benchmarks/r']}><Routes><Route path="/benchmarks/:id" element={<BenchmarkDetailPage />} /></Routes></MemoryRouter>);
  });
}

describe('benchmark report completion', () => {
  it('does not overlap slow report polling requests', async () => {
    let finish!: (value: { status: string }) => void;
    const pending = new Promise<{ status: string }>((resolve) => { finish = resolve; });
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValueOnce({ status: 'pending' }).mockReturnValue(pending);
    await showDetail();
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(api.fetchBenchmarkReport).toHaveBeenCalledTimes(2);
    await act(async () => { finish({ status: 'ready' }); });
    expect(screen.getByText(/已冻结数据报告/)).toBeInTheDocument();
  });

  it('polls a pending report after the run finishes and stops once ready', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValueOnce({ status: 'pending' })
      .mockResolvedValue({ status: 'ready', metric_version: 'v2', provisional: false });
    await showDetail();
    expect(screen.queryByText(/已冻结数据报告/)).not.toBeInTheDocument();
    expect(screen.getByText(/报告生成中/)).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.getByText(/已冻结数据报告/)).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(api.fetchBenchmarkReport).toHaveBeenCalledTimes(2);
  });

  it('shows generation failure and stops polling a terminal failure', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({ status: 'failed' });
    await showDetail();
    expect(screen.getByText(/报告生成失败/)).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(api.fetchBenchmarkReport).toHaveBeenCalledOnce();
  });

  it('lists every scheduled game and surfaces delete errors', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({ status: 'ready', provisional: false });
    vi.mocked(api.deleteBenchmarkGame).mockRejectedValue(new Error('删不掉'));
    await showDetail([{
      run_id: 'r', item_index: 0, scenario_id: 's', pair_id: null, block_index: 0,
      assignment: {}, game_id: 'failed-game', status: 'failed', terminal_reason: 'model_timeout',
      event_seq: 2, name: '评测局', phase: 'night', round_number: 1,
      player_count: 8, alive_count: 6, winner: 'good', execution_status: 'failed',
    }, {
      run_id: 'r', item_index: 1, scenario_id: 's', pair_id: null, block_index: 0,
      assignment: {}, game_id: null, status: 'pending', terminal_reason: null, phase: 'custom',
      winner: 'other',
    }], { fakeTimers: false });
    expect(screen.getByText('评测局')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看回放 · 事件 2' })).toBeInTheDocument();
    expect(screen.getByText('好人胜')).toBeInTheDocument();
    expect(screen.getByText('排程 #2')).toBeInTheDocument();
    expect(screen.getByText('custom')).toBeInTheDocument();
    expect(screen.getByText('other')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    const gameDialog = await screen.findByRole('dialog');
    fireEvent.click(within(gameDialog).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(screen.getByText('删不掉')).toBeInTheDocument());
    fireEvent.click(within(gameDialog).getByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('checkbox', { name: '选择 评测局' }));
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }));
    vi.mocked(api.batchDeleteBenchmarkGames).mockResolvedValue({
      deleted: [], failed: [{ game_id: 'failed-game', code: 'busy', message: '占用中' }],
    });
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: '删除' }));
    await waitFor(() => expect(screen.getByText('占用中')).toBeInTheDocument());
  });
});
