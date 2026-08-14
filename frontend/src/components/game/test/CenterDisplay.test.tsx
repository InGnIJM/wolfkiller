// @vitest-environment jsdom

import { act, cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useGameStore } from '../../../store/gameStore';
import type { GameLogs } from '../../../store/types';
import CenterDisplay from '../CenterDisplay';

const replayEventMeta = { timestamp: '2026-08-13T00:00:00Z' } as const;

function renderTimelineAt(logs: GameLogs, index: number) {
  useGameStore.getState().loadLogs(logs);
  act(() => useGameStore.getState().seekTo(index));
  return render(<CenterDisplay />);
}

beforeEach(() => {
  useGameStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useGameStore.getState().reset();
  vi.useRealTimers();
});

describe('CenterDisplay staged night events', () => {
  it('renders narrator page for narration events', () => {
    const logs: GameLogs = {
      game_id: 'game-1',
      events: [
        {
          ...replayEventMeta,
          event_type: 'narration',
          payload: {
            round_number: 1,
            title: '天黑请闭眼',
            text: '狼人请睁眼，开始讨论今晚的行动。',
          },
        },
      ],
    };

    renderTimelineAt(logs, 0);

    expect(screen.getByText('天黑请闭眼')).toBeInTheDocument();
    expect(screen.getByText('狼人请睁眼，开始讨论今晚的行动。')).toBeInTheDocument();
  });

  it('renders wolf discussion as chat bubbles', () => {
    const logs: GameLogs = {
      game_id: 'game-1',
      events: [
        {
          ...replayEventMeta,
          event_type: 'wolf_chat_message',
          payload: { round_number: 1, seat: 3, text: '今晚刀谁？' },
        },
        {
          ...replayEventMeta,
          event_type: 'wolf_chat_message',
          payload: { round_number: 1, seat: 1, text: '我怀疑2号' },
        },
      ],
    };

    renderTimelineAt(logs, 1);

    expect(screen.getByText(/1号：我怀疑2号/)).toBeInTheDocument();
  });

  it('renders wolf vote and thoughts', () => {
    const wolfVoteLogs: GameLogs = {
      game_id: 'game-1',
      events: [
        {
          ...replayEventMeta,
          event_type: 'wolf_vote',
          payload: { round_number: 1, seat: 1, target_seat: 2, reasoning: '我怀疑2号是神' },
        },
      ],
    };
    renderTimelineAt(wolfVoteLogs, 0);
    expect(screen.getByText(/出票/)).toBeInTheDocument();
    expect(screen.getByText(/2号/)).toBeInTheDocument();
    cleanup();

    useGameStore.getState().reset();
    const witchThoughtLogs: GameLogs = {
      game_id: 'game-1',
      events: [
        {
          ...replayEventMeta,
          event_type: 'witch_thought',
          payload: { round_number: 1, seat: 5, text: '昨晚2号被刀，我决定救人' },
        },
      ],
    };
    renderTimelineAt(witchThoughtLogs, 0);
    expect(screen.getByText(/女巫思考/)).toBeInTheDocument();
    expect(screen.getByText('昨晚2号被刀，我决定救人')).toBeInTheDocument();
    cleanup();

    useGameStore.getState().reset();
    const seerThoughtLogs: GameLogs = {
      game_id: 'game-1',
      events: [
        {
          ...replayEventMeta,
          event_type: 'seer_thought',
          payload: { round_number: 1, seat: 7, text: '查验3号，是好人' },
        },
      ],
    };
    renderTimelineAt(seerThoughtLogs, 0);
    expect(screen.getByText(/预言家思考/)).toBeInTheDocument();
    expect(screen.getByText('查验3号，是好人')).toBeInTheDocument();
  });
});
