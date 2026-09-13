// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useGameStore } from '../../../store/gameStore';
import type { PublicReplayEvent } from '../../../store/types';
import { fetchGameMemories } from '../../../api/client';
import HistoryPanel from '../HistoryPanel';

vi.mock('../../../api/client', () => ({
  fetchGameMemories: vi.fn(),
}));

const replayEventMeta = { timestamp: '2026-08-13T00:00:00Z' } as const;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  useGameStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useGameStore.getState().reset();
  vi.restoreAllMocks();
});

describe('HistoryPanel staged night event cards', () => {
  it('renders a centered narration card with title and body text', () => {
    const timeline: PublicReplayEvent[] = [
      {
        ...replayEventMeta,
        event_type: 'narration',
        payload: { round_number: 1, title: '天黑请闭眼', text: '狼人请睁眼，开始讨论今晚的行动。' },
      },
    ];
    useGameStore.setState({ timeline });
    render(<HistoryPanel onClose={vi.fn()} />);

    expect(screen.getByText('天黑请闭眼')).toBeVisible();
    expect(screen.getByText('狼人请睁眼，开始讨论今晚的行动。')).toBeVisible();
  });

  it('renders wolf chat messages as chat bubbles', () => {
    const timeline: PublicReplayEvent[] = [
      {
        ...replayEventMeta,
        event_type: 'wolf_chat_message',
        payload: { round_number: 1, seat: 1, text: '我怀疑2号' },
      },
    ];
    useGameStore.setState({ timeline });
    render(<HistoryPanel onClose={vi.fn()} />);

    expect(screen.getByText(/1号：我怀疑2号/)).toBeVisible();
  });

  it('renders wolf votes with target and reasoning', () => {
    const timeline: PublicReplayEvent[] = [
      {
        ...replayEventMeta,
        event_type: 'wolf_vote',
        payload: { round_number: 1, seat: 1, target_seat: 2, reasoning: '我怀疑2号是神' },
      },
    ];
    useGameStore.setState({ timeline });
    render(<HistoryPanel onClose={vi.fn()} />);

    expect(screen.getByText(/1号 → 2号/)).toBeVisible();
    expect(screen.getByText(/我怀疑2号是神/)).toBeVisible();
  });

  it('renders witch thoughts with seat label and body text', () => {
    const timeline: PublicReplayEvent[] = [
      {
        ...replayEventMeta,
        event_type: 'witch_thought',
        payload: { round_number: 1, seat: 5, text: '昨晚2号被刀，我决定救人' },
      },
    ];
    useGameStore.setState({ timeline });
    render(<HistoryPanel onClose={vi.fn()} />);

    expect(screen.getByText(/女巫思考 · 5号/)).toBeVisible();
    expect(screen.getByText('昨晚2号被刀，我决定救人')).toBeVisible();
  });

  it('renders hunter reasoning night_thought cards and groups them into 思考', () => {
    const timeline: PublicReplayEvent[] = [
      {
        ...replayEventMeta,
        event_type: 'night_thought',
        payload: { round_number: 1, seat: 4, action_type: 'hunter_reasoning', target_seat: 6, reasoning: '我开枪带走6号' },
      },
    ];
    useGameStore.setState({ timeline });
    render(<HistoryPanel onClose={vi.fn()} />);

    expect(screen.getByText(/猎人思考 · 4号/)).toBeVisible();
    expect(screen.getByText(/目标6号：我开枪带走6号/)).toBeVisible();

    fireEvent.click(screen.getByRole('tab', { name: /思考/ }));
    expect(screen.getByText(/猎人思考 · 4号/)).toBeVisible();
    expect(screen.getByText(/目标6号：我开枪带走6号/)).toBeVisible();
  });

  it('groups daytime speeches into 第N天 chronicle marks', () => {
    const timeline: PublicReplayEvent[] = [
      {
        ...replayEventMeta,
        event_type: 'speech',
        payload: { player_seat: 5, text: '我怀疑2号', round_number: 1, phase: 'speech' },
      },
    ];
    useGameStore.setState({ timeline });
    render(<HistoryPanel onClose={vi.fn()} />);

    expect(screen.getAllByText('第1天').length).toBeGreaterThan(0);
    expect(screen.getByText(/我怀疑2号/)).toBeVisible();
  });

  it('groups night narration into 第N夜 chronicle marks', () => {
    const timeline: PublicReplayEvent[] = [
      {
        ...replayEventMeta,
        event_type: 'narration',
        payload: { round_number: 2, title: '夜幕降临', text: '请狼人睁眼' },
      },
    ];
    useGameStore.setState({ timeline });
    render(<HistoryPanel onClose={vi.fn()} />);

    expect(screen.getByText('第2夜')).toBeVisible();
    expect(screen.getByText('夜幕降临')).toBeVisible();
  });

  it('groups chat messages into 思考 and narration plus wolf votes into 夜晚', () => {
    const timeline: PublicReplayEvent[] = [
      {
        ...replayEventMeta,
        event_type: 'narration',
        payload: { round_number: 1, title: '天黑请闭眼', text: '狼人请睁眼' },
      },
      {
        ...replayEventMeta,
        event_type: 'wolf_chat_message',
        payload: { round_number: 1, seat: 1, text: '我怀疑2号' },
      },
      {
        ...replayEventMeta,
        event_type: 'wolf_vote',
        payload: { round_number: 1, seat: 1, target_seat: 2, reasoning: '怀疑2号' },
      },
    ];
    useGameStore.setState({ timeline });
    render(<HistoryPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('tab', { name: /思考/ }));
    expect(screen.getByText(/1号：我怀疑2号/)).toBeVisible();
    expect(screen.queryByText('天黑请闭眼')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /夜晚/ }));
    expect(screen.getByText('天黑请闭眼')).toBeVisible();
    expect(screen.getByText(/1号 → 2号/)).toBeVisible();
  });

  it('renders every public event fallback without hiding unfamiliar public data', () => {
    const timeline = [
      { ...replayEventMeta, event_type: 'speech', payload: { player_seat: 1, text: '同一天', round_number: 1 } },
      { ...replayEventMeta, event_type: 'vote', payload: { voter_seat: 1, target_seat: 2, round_number: 1 } },
      { ...replayEventMeta, event_type: 'technical_abstain', payload: { voter_seat: 3, round_number: 1 } },
      { ...replayEventMeta, event_type: 'night_action', payload: { action_type: 'future_action', target_seat: 4, round_number: 1 } },
      { ...replayEventMeta, event_type: 'wolf_vote', payload: { seat: 2, target_seat: null, reasoning: '没有合适目标', round_number: 1 } },
      { ...replayEventMeta, event_type: 'night_thought', payload: { action_type: 'future_thought', seat: 4, target_seat: null, reasoning: '暂不行动', round_number: 1 } },
      { ...replayEventMeta, event_type: 'death', payload: { player_seat: 5, cause: 'future_cause', round_number: 1 } },
      { ...replayEventMeta, event_type: 'phase', payload: { phase: 'future_phase', round_number: 2 } },
      { ...replayEventMeta, event_type: 'winner', payload: { winning_camp: 'good', reason: 'done' } },
      { ...replayEventMeta, event_type: 'future_public_event', payload: {} },
    ] as unknown as PublicReplayEvent[];
    useGameStore.setState({ timeline });

    render(<HistoryPanel onClose={vi.fn()} />);

    expect(screen.getByText(/系统代投弃权/)).toBeVisible();
    expect(screen.getByText(/夜晚行动/)).toBeVisible();
    expect(screen.getByText(/2号 → 弃权/)).toBeVisible();
    expect(screen.getByText(/夜间思考 · 4号/)).toBeVisible();
    expect(screen.getByText(/不行动：暂不行动/)).toBeVisible();
    expect(screen.getByText('游戏结束')).toBeVisible();
    expect(screen.getAllByText('事件').length).toBeGreaterThan(0);
    expect(screen.getAllByText('第1天').length).toBeGreaterThan(0);
    expect(screen.getByText('第?天')).toBeVisible();
  });
});

describe('HistoryPanel memory detail', () => {
  it('renders action history and witnessed events entries', async () => {
    vi.mocked(fetchGameMemories).mockResolvedValue({
      game_id: 'game-1',
      memories: [{
        seat_number: 5,
        role: 'wolf-killer-witch',
        camp: 'good',
        is_alive: true,
        private_knowledge: {},
        action_history: [
          { round: 1, phase: 'night', action: { player_seat: 5, action_type: 'save', target_seat: 2, reasoning: '', thinking: '' } },
          { round: 1, phase: 'speech', type: 'speech', text: '我是预言家' },
        ],
        witnessed_events: [
          { round: 1, event: 'death', details: { player_seat: 2, cause: 'wolf_kill', round_number: 1 } },
        ],
        last_updated: '2026-01-01T00:00:00Z',
      }],
    });
    useGameStore.setState({ gameId: 'game-1', timeline: [] });
    render(<HistoryPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('tab', { name: '记忆' }));

    expect(await screen.findByText(/行动 1轮：救人 → 2号/)).toBeVisible();
    expect(screen.getByText(/发言 1轮：我是预言家/)).toBeVisible();
    expect(screen.getByText(/见证 1轮：死亡/)).toBeVisible();
  });

  it('shows 无 for empty action history and witnessed events', async () => {
    vi.mocked(fetchGameMemories).mockResolvedValue({
      game_id: 'game-1',
      memories: [{
        seat_number: 5,
        role: 'wolf-killer-witch',
        camp: 'good',
        is_alive: true,
        private_knowledge: {},
        action_history: [],
        witnessed_events: [],
        last_updated: '2026-01-01T00:00:00Z',
      }],
    });
    useGameStore.setState({ gameId: 'game-1', timeline: [] });
    render(<HistoryPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('tab', { name: '记忆' }));

    expect(await screen.findByText(/行动记录：无/)).toBeVisible();
    expect(screen.getByText(/见证记录：无/)).toBeVisible();
  });

  it('formats complete, empty, legacy, and unfamiliar memory fields', async () => {
    vi.mocked(fetchGameMemories).mockResolvedValue({
      game_id: 'game-1',
      memories: [
        {
          seat_number: 1,
          role: 'wolf-killer-seer',
          camp: 'good',
          is_alive: true,
          private_knowledge: {
            teammates: [2, 3],
            check_results: [
              { target_seat: 2, camp: 'werewolf' },
              { target: 3, result: 'good' },
              { target_seat: 4, camp: 'neutral' },
            ],
            last_wolf_kill_target: 6,
            has_badge: true,
            note: '保留信息',
          },
          action_history: [
            { round: 1, action: { action_type: 'poison', target_seat: 6 } },
            { action: { action_type: 'future_action' } },
            { type: 'speech' },
            { round: 2, type: 'vote', vote: { target_seat: 4 } },
            { round: 3, type: 'vote', vote: null },
            { round: 4, type: 'future', phase: 'dawn' },
            { round: 5, type: 'future' },
          ],
          witnessed_events: [
            { round: 1, event: 'vote' },
            { type: 'future_event' },
            {},
          ],
          last_updated: '2026-01-01T00:00:00Z',
        },
        {
          seat_number: 2,
          role: 'future_role',
          camp: 'unknown',
          is_alive: false,
          private_knowledge: { teammates: [], check_results: [], has_badge: false },
          action_history: [],
          witnessed_events: [],
          last_updated: '2026-01-01T00:00:00Z',
        },
      ],
    });
    useGameStore.setState({ gameId: 'game-1', timeline: [] });
    render(<HistoryPanel onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('tab', { name: '记忆' }));

    expect(await screen.findByText(/狼队友：2号、3号/)).toBeVisible();
    expect(screen.getByText(/查验记录：2号→狼人、3号→好人、4号→neutral/)).toBeVisible();
    expect(screen.getByText(/昨夜狼刀目标：6号/)).toBeVisible();
    expect(screen.getByText(/note：保留信息/)).toBeVisible();
    expect(screen.getByText(/行动 1轮：毒人 → 6号/)).toBeVisible();
    expect(screen.getByText(/行动 \?轮：future_action/)).toBeVisible();
    expect(screen.getByText(/发言 \?轮：/)).toBeVisible();
    expect(screen.getByText(/投票 2轮：4号/)).toBeVisible();
    expect(screen.getByText(/投票 3轮：弃权/)).toBeVisible();
    expect(screen.getByText(/行动 4轮：dawn/)).toBeVisible();
    expect(screen.getByText(/行动 5轮：\?/)).toBeVisible();
    expect(screen.getByText(/见证 1轮：vote/)).toBeVisible();
    expect(screen.getByText(/见证 \?轮：future_event/)).toBeVisible();
    expect(screen.getByText(/见证 \?轮：\?/)).toBeVisible();
    expect(screen.getByText(/2号 · future_role/)).toBeVisible();
    expect(screen.getByText('出局')).toBeVisible();
  });

  it('ignores memory completion after unmount', async () => {
    const pending = deferred<Awaited<ReturnType<typeof fetchGameMemories>>>();
    vi.mocked(fetchGameMemories).mockReturnValueOnce(pending.promise);
    useGameStore.setState({ gameId: 'game-1', timeline: [] });
    const { unmount } = render(<HistoryPanel onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('tab', { name: '记忆' }));
    unmount();

    await act(async () => pending.resolve({ game_id: 'game-1', memories: [] }));
  });

  it('ignores memory failure after unmount', async () => {
    const pending = deferred<Awaited<ReturnType<typeof fetchGameMemories>>>();
    vi.mocked(fetchGameMemories).mockReturnValueOnce(pending.promise);
    useGameStore.setState({ gameId: 'game-1', timeline: [] });
    const { unmount } = render(<HistoryPanel onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('tab', { name: '记忆' }));
    unmount();

    await act(async () => pending.reject(new Error('late failure')));
  });
});
