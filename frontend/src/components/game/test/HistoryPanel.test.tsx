// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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
});
