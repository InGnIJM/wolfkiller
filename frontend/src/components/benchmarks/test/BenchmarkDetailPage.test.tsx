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

const DECISION_MISSING_ALERT = '本次报告没有决策质量数据（旧版本报告，或这些对局缺少 summary.json 明细）。';

const decisionSamples = {
  games: 12, seer_checks: 20, witch_poisons: 4, hunter_shots: 3,
  wolf_kills: 18, good_votes: 96, exiles: 12,
  pure_white_checks: 6, wolf_witch_checks: 5, gargoyle_checks: 4,
  demon_hunter_hunts: 2, dance_pools: 3, mask_grants: 3,
};

// 后端本轮新增的形状；集中度两项刻意保留在 fixture 里，确保页面绝不把它们渲染出来。
const decisionBlock = {
  seer_check_hit_rate: 0.5,
  witch_save_uses: 3,
  witch_poison_uses: 2,
  witch_poison_hit_rate: 1.0,
  hunter_shot_hit_rate: 0.3333,
  wolf_kill_god_rate: 0.25,
  good_vote_hit_rate: 0.4,
  good_vote_abstain_rate: 0.05,
  exile_wolf_rate: 0.6,
  exile_god_misfire_rate: 0.1,
  wolf_vote_concentration: 0.9,
  wolf_vote_distinct_targets: 7,
  pure_white_check_hit_rate: 0.6667,
  wolf_witch_check_hit_rate: 0.4,
  gargoyle_check_hit_rate: 0.25,
  demon_hunter_hunt_hit_rate: 0.5,
  dance_pool_wolf_rate: 0.3333,
  mask_in_pool_rate: 1.0,
  samples: decisionSamples,
};

function rowCells(table: HTMLElement, label: string): string[] {
  const row = within(table).getByText(label).closest('tr') as HTMLElement;
  return Array.from(row.querySelectorAll('td'), (cell) => cell.textContent ?? '');
}

function decisionTable(): HTMLElement {
  return screen.getByRole('table', { name: '决策质量表' });
}

describe('benchmark decision quality', () => {
  it('renders every decision ratio with sample sizes and both witch use counts', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false,
      summary: { decision: decisionBlock, coverage: {
        boards: [{ role_counts: { 'wolf-killer-seer': 1, 'wolf-killer-villager': 3 }, games: 4 }],
        seer_present: true,
      } },
    });
    await showDetail();
    const table = decisionTable();
    expect(rowCells(table, '预言家验中狼率')).toEqual(['预言家验中狼率', '50.0%', '20']);
    expect(rowCells(table, '女巫毒中狼率')).toEqual(['女巫毒中狼率', '100.0%', '4']);
    expect(rowCells(table, '猎人带狼率')).toEqual(['猎人带狼率', '33.3%', '3']);
    expect(rowCells(table, '狼刀命中神职率')).toEqual(['狼刀命中神职率', '25.0%', '18']);
    expect(rowCells(table, '好人投票命中狼率')).toEqual(['好人投票命中狼率', '40.0%', '96']);
    expect(rowCells(table, '好人弃票率')).toEqual(['好人弃票率', '5.0%', '96']);
    expect(rowCells(table, '放逐命中狼率')).toEqual(['放逐命中狼率', '60.0%', '12']);
    expect(rowCells(table, '放逐误伤神职率')).toEqual(['放逐误伤神职率', '10.0%', '12']);
    expect(rowCells(table, '女巫用解药')).toEqual(['女巫用解药', '3', '—']);
    expect(rowCells(table, '女巫用毒')).toEqual(['女巫用毒', '2', '—']);
    // 没有专属结果事件的新角色：命中率同样带样本量。
    expect(rowCells(table, '纯白之女验中狼率')).toEqual(['纯白之女验中狼率', '66.7%', '6']);
    expect(rowCells(table, '狼巫验中纯白率')).toEqual(['狼巫验中纯白率', '40.0%', '5']);
    expect(rowCells(table, '石像鬼验中狼率')).toEqual(['石像鬼验中狼率', '25.0%', '4']);
    expect(rowCells(table, '猎魔人狩猎中狼率')).toEqual(['猎魔人狩猎中狼率', '50.0%', '2']);
    expect(rowCells(table, '舞池含狼率')).toEqual(['舞池含狼率', '33.3%', '3']);
    expect(rowCells(table, '假面入池率')).toEqual(['假面入池率', '100.0%', '3']);
    // 单板型必须看得见：读者要知道池化数字出自哪种配置。
    expect(screen.getByText('板型覆盖：4 人局 × 4')).toBeInTheDocument();
  });

  it('falls back to an info alert for older reports without a decision block', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false, summary: { games: { total: 2, completed: 2 } },
    });
    await showDetail();
    expect(screen.getByText(DECISION_MISSING_ALERT)).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: '决策质量表' })).not.toBeInTheDocument();
    expect(screen.getByText('模型表现')).toBeInTheDocument();
    // 这张表按座位切片统计，不是模型 A/B：口径必须写在表头，别让读者读成模型强弱。
    expect(screen.getByText(/这不是模型 A\/B 对比/)).toBeInTheDocument();
  });

  it('treats a non-object decision block as missing instead of crashing', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false, summary: { decision: 'oops' },
    });
    await showDetail();
    expect(screen.getByText(DECISION_MISSING_ALERT)).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: '决策质量表' })).not.toBeInTheDocument();
  });

  it('names the games with decision detail and why concentration stays hidden', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false, summary: { decision: decisionBlock },
    });
    await showDetail();
    const subtitle = screen.getByText(/跨局池化口径，见 docs\/benchmark\.md/);
    expect(subtitle).toHaveTextContent('基于 12 局有决策明细的对局。');
    expect(subtitle).toHaveTextContent('狼票集中度不在此展示：跨局池化后不可解读（需按轮次重算）。');
  });

  it('omits the game-count clause when samples.games is missing', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false,
      summary: { decision: { ...decisionBlock, samples: { seer_checks: 20 } } },
    });
    await showDetail();
    expect(screen.queryByText(/基于 \d+ 局有决策明细的对局/)).not.toBeInTheDocument();
    expect(screen.getByText(/狼票集中度不在此展示/)).toBeInTheDocument();
    expect(rowCells(decisionTable(), '预言家验中狼率')).toEqual(['预言家验中狼率', '50.0%', '20']);
  });

  it('never renders the pooled wolf vote concentration or its distinct target count', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false, summary: { decision: decisionBlock },
    });
    await showDetail();
    expect(within(decisionTable()).queryByText('狼票集中度')).not.toBeInTheDocument();
    expect(screen.queryByText('90.0%')).not.toBeInTheDocument();
    expect(screen.queryByText('0.9')).not.toBeInTheDocument();
    const section = screen.getByText('决策质量').closest('section') as HTMLElement;
    expect(within(section).queryByText('7')).not.toBeInTheDocument();
    expect(screen.queryByText('wolf_vote_concentration')).not.toBeInTheDocument();
    expect(screen.queryByText('wolf_vote_distinct_targets')).not.toBeInTheDocument();
  });

  it('adds average rounds and interruption rate KPI cards', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false,
      summary: { games: { total: 4, completed: 3, average_rounds: 5.25, interruption_rate: 0.25 } },
    });
    await showDetail();
    expect(screen.getByText('平均轮次').parentElement).toHaveTextContent('平均轮次5.3');
    expect(screen.getByText('中断率').parentElement).toHaveTextContent('中断率25.0%');
  });

  it('shows a dash for both new KPI cards when the report omits them', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false, summary: { games: { total: 1 } },
    });
    await showDetail();
    expect(screen.getByText('平均轮次').parentElement).toHaveTextContent('平均轮次—');
    expect(screen.getByText('中断率').parentElement).toHaveTextContent('中断率—');
  });
});

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
      name: '评测局', phase: 'night', round_number: 1,
      player_count: 8, alive_count: 6, winner: 'good', execution_status: 'failed',
    }, {
      run_id: 'r', item_index: 1, scenario_id: 's', pair_id: null, block_index: 0,
      assignment: {}, game_id: null, status: 'pending', terminal_reason: null, phase: 'custom',
      winner: 'other',
    }], { fakeTimers: false });
    expect(screen.getByText('评测局')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看回放' })).toHaveAttribute(
      'href', '/game/failed-game',
    );
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

  it('warns when the run played a single board without a seer', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false,
      summary: {
        decision: decisionBlock,
        coverage: {
          boards: [{ role_counts: { 'wolf-killer-villager': 12 }, games: 30 }],
          seer_present: false,
        },
      },
    });
    await showDetail();

    expect(screen.getByText(/12 人局 × 30/)).toBeInTheDocument();
    expect(screen.getByText(/本批不含预言家/)).toBeInTheDocument();
  });
