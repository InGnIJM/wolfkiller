// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../api/client';
import { useBenchmarkStore } from '../../../store/benchmarkStore';
import BenchmarkDetailPage from '../BenchmarkDetailPage';

vi.mock('../../../api/client', () => ({
  fetchBenchmark: vi.fn(), fetchBenchmarkGames: vi.fn(), fetchBenchmarkReport: vi.fn(),
  benchmarkExportUrl: () => '/export',
}));
afterEach(() => { cleanup(); useBenchmarkStore.getState().clear(); vi.resetAllMocks(); vi.useRealTimers(); });

async function showDetail() {
  vi.useFakeTimers();
  vi.mocked(api.fetchBenchmark).mockResolvedValue({
    run_id: 'r', client_request_id: 'c', name: 'finished run', mode: 'mixed_arena',
    status: 'completed', config: {}, created_at: '', updated_at: '',
  });
  vi.mocked(api.fetchBenchmarkGames).mockResolvedValue({ games: [] });
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
});
