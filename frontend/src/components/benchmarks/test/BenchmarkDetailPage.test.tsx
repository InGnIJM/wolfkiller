// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../../api/client';
import { useBenchmarkStore } from '../../../store/benchmarkStore';
import type { BenchmarkMode } from '../../../store/types';
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
  { fakeTimers = true, mode = 'mixed_arena' }: { fakeTimers?: boolean; mode?: BenchmarkMode } = {},
) {
  if (fakeTimers) vi.useFakeTimers();
  vi.mocked(api.fetchBenchmark).mockResolvedValue({
    run_id: 'r', client_request_id: 'c', name: 'finished run', mode,
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
    expect(screen.getByText('缓存命中率').parentElement).toHaveTextContent('缓存命中率—');
  });

  it('shows the prefix-cache hit rate from token usage', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false,
      summary: { attempts: { token_usage: { known_total_tokens: 1000, cache_hit_rate: 0.625 } } },
    });
    await showDetail();
    expect(screen.getByText('缓存命中率').parentElement).toHaveTextContent('缓存命中率62.5%');
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

describe('benchmark outcome composition', () => {
  it('renders camp win rates, win reasons, and seat win rates as chips', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false,
      summary: {
        games: {
          win_rate_by_camp: { good: 0.6, werewolf: 0.4 },
          win_reason_distribution: { all_gods_dead: 3, wolves_outnumber_good: 2 },
          win_rate_by_seat: { '1': { games: 5, wins: 2, win_rate: 0.4 } },
        },
      },
    });
    await showDetail();

    const section = screen.getByText('结果构成').closest('section') as HTMLElement;
    expect(within(section).getByText('好人胜 60.0%')).toBeInTheDocument();
    expect(within(section).getByText('狼人胜 40.0%')).toBeInTheDocument();
    expect(within(section).getByText('所有神职出局 ×3')).toBeInTheDocument();
    expect(within(section).getByText('狼人人数过半 ×2')).toBeInTheDocument();
    expect(within(section).getByText('1 号 40.0% (2/5)')).toBeInTheDocument();
  });

  it('hides the whole section when no outcome data exists', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false, summary: { games: { total: 1 } },
    });
    await showDetail();
    expect(screen.queryByText('结果构成')).not.toBeInTheDocument();
  });
});

describe('benchmark uncertainty', () => {
  const mixedRow = {
    id: 'm1:camp:werewolf', model: 'm1', model_label: '模型甲',
    camp: 'werewolf', games: 8, valid_seats: 40, wins: 14,
    win_rate: 0.35, low: 0.21, high: 0.51,
    interval_method: 'wilson', insufficient_blocks: true,
  };

  it('renders mixed-arena intervals with method labels and the Wilson note', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false,
      uncertainty: { mixed_arena: [mixedRow, {
        id: 'm1:role:wolf-killer-seer', model: 'm1',
        role: 'wolf-killer-seer', games: 8, valid_seats: 8, wins: 4,
        win_rate: 0.5, low: 0.3, high: 0.7, interval_method: 'bootstrap',
      }] },
    });
    await showDetail();

    const table = screen.getByRole('table', { name: '不确定度' });
    expect(rowCells(table, '模型甲')).toEqual([
      '模型甲', '阵营', 'werewolf', '8', '40', '35.0%', '[21.0% – 51.0%]', 'wilson',
    ]);
    expect(screen.getByText('单区组无法重采样，区间为 Wilson 近似。')).toBeInTheDocument();
    expect(within(table).getByText('bootstrap')).toBeInTheDocument();
  });

  it('falls back to summary.mixed_uncertainty for reports without the uncertainty key', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false,
      summary: { mixed_uncertainty: [mixedRow] },
    });
    await showDetail();
    const table = screen.getByRole('table', { name: '不确定度' });
    expect(within(table).getByText('模型甲')).toBeInTheDocument();
  });

  it('shows the empty note for legacy reports with no interval data', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false, summary: { games: { total: 2 } },
    });
    await showDetail();
    expect(screen.getByText(/本次报告没有不确定度数据/)).toBeInTheDocument();
  });

  it('renders paired regression score and latency conclusions', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false,
      summary: {
        paired_regression: {
          pair_count: 2, mean_difference: 0.125,
          differences: [
            { pair_id: 'p1', baseline: 0.5, candidate: 0.75, difference: 0.25 },
            { pair_id: 'p2', baseline: 0.4, candidate: 0.4, difference: 0.0 },
          ],
          bootstrap: { low: 0.01, high: 0.24, samples: 2000 },
        },
        paired_latency_ms: {
          pair_count: 2, excluded_interrupted_pairs: 1, excluded_incomplete_pairs: 1,
          mean_difference: -1200,
          differences: [{ pair_id: 'p1', baseline: 9000, candidate: 7800, difference: -1200 }],
          bootstrap: { low: -2000, high: -500, samples: 2000 },
        },
      },
    });
    await showDetail([], { mode: 'paired_regression' });

    expect(screen.getByText(/平均得分差/)).toHaveTextContent('平均得分差: 0.1250');
    const intervalLines = screen.getAllByText(/95% 区间/);
    expect(intervalLines[0]).toHaveTextContent('95% 区间: [0.0100 – 0.2400]');
    expect(intervalLines[1]).toHaveTextContent('95% 区间: [-2,000 ms – -500 ms]');
    expect(screen.getByText(/配对数/)).toHaveTextContent('配对数: 2');
    const scoreTable = screen.getByRole('table', { name: '不确定度' });
    expect(rowCells(scoreTable, 'p1')).toEqual(['p1', '0.5000', '0.7500', '0.2500']);
    expect(screen.getByText('配对延迟')).toBeInTheDocument();
    expect(screen.getByText(/平均延迟差/)).toHaveTextContent('平均延迟差: -1,200 ms');
    expect(screen.getByText(/中断排除/)).toHaveTextContent('中断排除: 1');
    expect(screen.getByText(/缺数据排除/)).toHaveTextContent('缺数据排除: 1');
  });

  it('says there are no complete pairs when differences is empty', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false,
      summary: {
        paired_regression: { pair_count: 0, mean_difference: null, differences: [], bootstrap: { insufficient_pairs: true, low: null, high: null } },
      },
    });
    await showDetail([], { mode: 'paired_regression' });
    expect(screen.getByText('没有完整配对。')).toBeInTheDocument();
    expect(screen.getByText('没有可重采样的配对，无法给出区间。')).toBeInTheDocument();
  });
});

describe('benchmark requests and reliability', () => {
  const requestReport = {
    status: 'ready', provisional: false,
    summary: {
      requests: {
        total: 120, eligible_terminal: 100, terminal_votes: 60,
        valid_rate: 0.9, success_rate: 0.8, voluntary_abstain_rate: 0.1,
        failure_rate: 0.1, fallback_rate: 0.02, technical_abstain_rate: 0.05,
        pass_breakdown: { sheriff_campaign: 4, vote_abstain: 3, night_optional: 5 },
        votes: {
          exile: { terminal: 40, technical: 2, voluntary: 3, failed: 1, technical_abstain_rate: 0.05, voluntary_abstain_rate: 0.075 },
          sheriff: { terminal: 20, technical: 1, voluntary: 0, failed: 0, technical_abstain_rate: 0.05, voluntary_abstain_rate: 0 },
        },
        logical_latency_ms: { count: 100, p50: 1200, p95: 3400, p99: 5000 },
      },
      attempts: {
        total: 130, llm_calls: 3500, retried_llm_calls: 12, retry_rate: 0.0034,
        tokens_per_attempt: 812.5, executed_requests: 100, recovery_redispatches: 2,
        token_usage: { known_attempts: 110, cache_known_seats: 30, cache_cold_seats: 1, cache_hit_rate_excluding_cold_seats: 0.61 },
      },
      reliability: {
        rule_rejected: 6, games: 10, per_game: 0.6, per_request: 0.05,
        by_contract: {
          'werewolf.kill': { rejected: 4, violations: { target_dead: 3, target_forbidden: 1 } },
          'seer.check': { rejected: 2, violations: { target_self: 2 } },
        },
      },
    },
  };

  it('renders request rates, per-ballot abstentions, pass breakdown and usage', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue(requestReport);
    await showDetail();

    const section = screen.getByText('请求与可靠性').closest('section') as HTMLElement;
    const requestTable = within(section).getByRole('table', { name: '请求与可靠性' });
    expect(rowCells(requestTable, '请求成功率')).toEqual(['请求成功率', '90.0%', '100']);
    expect(rowCells(requestTable, '技术弃票率')).toEqual(['技术弃票率', '5.0%', '60']);
    expect(rowCells(requestTable, '兜底率')).toEqual(['兜底率', '2.0%', '100']);

    const voteTable = within(section).getByRole('table', { name: '投票细分' });
    expect(rowCells(voteTable, '放逐票')).toEqual(['放逐票', '40', '2', '5.0%', '3', '7.5%', '1']);
    expect(rowCells(voteTable, '警长票')).toEqual(['警长票', '20', '1', '5.0%', '0', '0.0%', '0']);

    const passTable = within(section).getByRole('table', { name: '主动 pass 构成' });
    expect(rowCells(passTable, '不上警')).toEqual(['不上警', '4', '—']);
    expect(rowCells(passTable, '投票弃票')).toEqual(['投票弃票', '3', '—']);

    expect(within(section).getByText(/逻辑请求延迟/))
      .toHaveTextContent('逻辑请求延迟: 1,200 ms / p95: 3,400 ms / p99: 5,000 ms');
    const usageTable = within(section).getByRole('table', { name: '用量与缓存' });
    expect(rowCells(usageTable, '内层重试率')).toEqual(['内层重试率', '0.3%', '12 / 3500 次调用']);
    expect(rowCells(usageTable, '每调用 token')).toEqual(['每调用 token', '812.5', '110']);
    expect(rowCells(usageTable, '恢复重派发')).toEqual(['恢复重派发', '2', '—']);
    expect(rowCells(usageTable, '缓存冷座位')).toEqual(['缓存冷座位', '1', '—']);
    expect(rowCells(usageTable, '命中率（排除冷座位）')).toEqual(['命中率（排除冷座位）', '61.0%', '—']);
  });

  it('renders the contract x violation rejection breakdown', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue(requestReport);
    await showDetail();

    const section = screen.getByText('请求与可靠性').closest('section') as HTMLElement;
    const table = within(section).getByRole('table', { name: '规则拒绝' });
    expect(rowCells(table, 'werewolf.kill')).toEqual(['werewolf.kill', '4', 'target_dead×3、target_forbidden×1']);
    expect(rowCells(table, 'seer.check')).toEqual(['seer.check', '2', 'target_self×2']);
    expect(within(section).getByText(/共 6 次 · 平均每局 0\.60 · 占请求 5\.0%/)).toBeInTheDocument();
  });

  it('hides the requests section entirely for legacy reports', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false, summary: { games: { total: 2 } },
    });
    await showDetail();
    expect(screen.queryByText('请求与可靠性')).not.toBeInTheDocument();
  });
});

describe('benchmark games filter and diagnostics', () => {
  const scheduledGames = [
    { run_id: 'r', item_index: 0, scenario_id: 's', pair_id: null, block_index: 0, assignment: {}, game_id: 'g1', status: 'completed', terminal_reason: null, name: '甲局', winner: 'good' },
    { run_id: 'r', item_index: 1, scenario_id: 's', pair_id: null, block_index: 0, assignment: {}, game_id: 'g2', status: 'failed', terminal_reason: 'model_timeout', name: '乙局', winner: null },
    { run_id: 'r', item_index: 2, scenario_id: 's', pair_id: null, block_index: 0, assignment: {}, game_id: null, status: 'pending', terminal_reason: null, name: '丙局' },
  ];

  it('filters the games table by status', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({ status: 'ready', provisional: false });
    await showDetail(scheduledGames, { fakeTimers: false });

    expect(screen.getByText('甲局')).toBeInTheDocument();
    expect(screen.getByText('乙局')).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '状态筛选' }));
    fireEvent.click(await screen.findByRole('option', { name: 'completed' }));
    expect(screen.getByText('甲局')).toBeInTheDocument();
    expect(screen.queryByText('乙局')).not.toBeInTheDocument();
    expect(screen.queryByText('丙局')).not.toBeInTheDocument();
  });

  it('renders inclusion, exclusion, summaries and recovery diagnostic rows', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false, metric_version: 'v8',
      inclusion: { games_with_results: 3, games_without_results: 1 },
      exclusion: { unstarted_items: 2, failed_items: 1, cancelled_items: 0 },
      data_quality: { summaries_available: 3, summaries_missing: 1, recovery_retries: 4, recovery_attempts: 2 },
    });
    await showDetail();

    expect(screen.getByText(/纳入统计：3 局有结果 \/ 1 局无结果/)).toBeInTheDocument();
    expect(screen.getByText(/排除明细：未开始 2 · 失败 1 · 取消 0/)).toBeInTheDocument();
    expect(screen.getByText(/决策明细：3 局可读 \/ 1 局缺失/)).toBeInTheDocument();
    expect(screen.getByText(/恢复统计：重试 4 次 · 重派发 2 次/)).toBeInTheDocument();
  });

  it('shows paired latency exclusions only for paired runs', async () => {
    vi.mocked(api.fetchBenchmarkReport).mockResolvedValue({
      status: 'ready', provisional: false,
      data_quality: { latency_pairs_excluded_for_interruptions: 2, latency_pairs_excluded_for_missing_data: 1 },
    });
    await showDetail([], { mode: 'paired_regression' });
    expect(screen.getByText(/延迟配对排除：中断 2 · 缺数据 1/)).toBeInTheDocument();

    cleanup();
    useBenchmarkStore.getState().clear();
    await showDetail();
    expect(screen.queryByText(/延迟配对排除/)).not.toBeInTheDocument();
  });
});
