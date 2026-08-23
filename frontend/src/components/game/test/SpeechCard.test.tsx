// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useGameStore } from '../../../store/gameStore';
import type { PublicReplayEvent } from '../../../store/types';
import SpeechCard from '../SpeechCard';

const replayEventMeta = { timestamp: '2026-08-13T00:00:00Z' } as const;

function setTimeline(timeline: PublicReplayEvent[]) {
  useGameStore.getState().loadLogs({ game_id: 'game-1', events: timeline });
  useGameStore.getState().seekTo(0);
}

beforeEach(() => {
  useGameStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useGameStore.getState().reset();
});

describe('SpeechCard', () => {
  it('renders nothing when the current event is not a speech', () => {
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'death',
        payload: { player_seat: 3, cause: 'wolf_kill', round_number: 1 },
      },
    ]);
    const { container } = render(<SpeechCard />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the speaking player seat and speech body', () => {
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'speech',
        payload: { player_seat: 5, text: '我怀疑 2 号是狼', round_number: 2, phase: 'speech' },
      },
    ]);
    render(<SpeechCard />);

    expect(screen.getByText('5号')).toBeInTheDocument();
    expect(screen.getByText(/正在发言/)).toBeInTheDocument();
    expect(screen.getByText('我怀疑 2 号是狼')).toBeInTheDocument();
  });

  it('labels last-word events as 遗言', () => {
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'speech',
        payload: { player_seat: 4, text: '我是狼，你们上当了', round_number: 1, phase: 'last_words' },
      },
    ]);
    render(<SpeechCard />);

    expect(screen.getByText(/遗言/)).toBeInTheDocument();
    expect(screen.getByText('我是狼，你们上当了')).toBeInTheDocument();
  });
});
