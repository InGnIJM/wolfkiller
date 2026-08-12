// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchGameDetail, fetchGameLogs } from '../../../api/client';
import { useGameStore } from '../../../store/gameStore';
import type { GameLogs, PublicGameState } from '../../../store/types';
import GameBoard from '../GameBoard';

const replayEventMeta = { timestamp: '2026-08-13T00:00:00Z' } as const;

const { connect, disconnect } = vi.hoisted(() => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
}));

vi.mock('../../../api/client', () => ({
  fetchGameDetail: vi.fn(),
  fetchGameLogs: vi.fn(),
}));

vi.mock('../../../api/websocket', () => ({
  useWebSocket: () => ({ connect, disconnect, isConnected: false }),
}));

vi.mock('../TimelineController', () => ({
  default: () => <div data-testid="timeline-controller" />,
}));

vi.mock('../SeatMap', () => ({
  default: ({
    children,
    voteTargets,
  }: {
    children?: React.ReactNode;
    voteTargets: Record<number, number | null>;
  }) => <div data-testid="seat-map" data-vote-targets={JSON.stringify(voteTargets)}>{children}</div>,
}));

vi.mock('../CenterDisplay', () => ({
  default: () => <div data-testid="center-display" />,
}));

vi.mock('../HistoryPanel', () => ({
  default: () => <div data-testid="history-panel" />,
}));

const detail: PublicGameState = {
  game_id: 'game-1',
  phase: 'game_over',
  round_number: 1,
  players: {
    1: { seat_number: 1, is_alive: true, is_sheriff: false },
    2: { seat_number: 2, is_alive: false, is_sheriff: false },
  },
  sheriff: null,
  speeches: [],
  death_history: [],
  win_result: { winning_camp: 'good', reason: 'all_wolves_dead' },
};

const completedLogs: GameLogs = {
  game_id: 'game-1',
  events: [
    { ...replayEventMeta, event_type: 'phase', payload: { phase: 'game_over', round_number: 1 } },
    {
      ...replayEventMeta,
      event_type: 'winner',
      payload: { winning_camp: 'good', reason: 'all_wolves_dead' },
    },
  ],
};

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
  vi.mocked(fetchGameDetail).mockResolvedValue(detail);
  vi.mocked(fetchGameLogs).mockResolvedValue(completedLogs);
});

afterEach(() => {
  cleanup();
  useGameStore.getState().reset();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe('GameBoard public replay', () => {
  it('connects to the game websocket once without waiting for REST loading', () => {
    const pendingDetail = deferred<PublicGameState>();
    vi.mocked(fetchGameDetail).mockReturnValueOnce(pendingDetail.promise);

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(connect).toHaveBeenCalledOnce();
    expect(connect).toHaveBeenCalledWith('game-1');
  });

  it('does not reconnect when rerendered with the same game id', () => {
    const pendingDetail = deferred<PublicGameState>();
    vi.mocked(fetchGameDetail).mockReturnValueOnce(pendingDetail.promise);

    const { rerender } = render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    rerender(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(connect).toHaveBeenCalledOnce();
    expect(disconnect).not.toHaveBeenCalled();
  });

  it('disconnects the previous websocket before connecting a different game', () => {
    const pendingDetail = deferred<PublicGameState>();
    vi.mocked(fetchGameDetail).mockReturnValue(pendingDetail.promise);

    const { rerender } = render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    rerender(<GameBoard gameId="game-2" onBack={vi.fn()} />);

    expect(disconnect).toHaveBeenCalledOnce();
    expect(connect).toHaveBeenNthCalledWith(1, 'game-1');
    expect(connect).toHaveBeenNthCalledWith(2, 'game-2');
  });

  it('disconnects the websocket when the board unmounts', () => {
    const pendingDetail = deferred<PublicGameState>();
    vi.mocked(fetchGameDetail).mockReturnValueOnce(pendingDetail.promise);

    const { unmount } = render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    unmount();

    expect(disconnect).toHaveBeenCalledOnce();
  });

  it('shows the public winner overlay after loading a winner event', async () => {
    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('好人阵营获胜')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '从头播放' })).toBeVisible();
  });

  it('shows the original Error message when loading fails', async () => {
    vi.mocked(fetchGameDetail).mockRejectedValueOnce(new Error('对局不存在'));

    render(<GameBoard gameId="missing-game" onBack={vi.fn()} />);

    expect(await screen.findByText('对局不存在')).toBeVisible();
    await waitFor(() => expect(fetchGameLogs).not.toHaveBeenCalled());
  });

  it('uses a safe fallback when loading rejects with a non-Error value', async () => {
    vi.mocked(fetchGameDetail).mockRejectedValueOnce('network unavailable');

    render(<GameBoard gameId="missing-game" onBack={vi.fn()} />);

    expect(await screen.findByText('Failed to load game logs')).toBeVisible();
  });

  it('derives public vote targets and exposes the existing board controls', async () => {
    const onBack = vi.fn();
    vi.mocked(fetchGameDetail).mockResolvedValueOnce({
      ...detail,
      phase: 'vote_casting',
      win_result: null,
    });
    vi.mocked(fetchGameLogs).mockResolvedValueOnce({
      game_id: 'game-1',
      events: [
        { ...replayEventMeta, event_type: 'phase', payload: { phase: 'vote_casting', round_number: 1 } },
        { ...replayEventMeta, event_type: 'speech', payload: { player_seat: 2, text: '公开发言', round_number: 1 } },
        { ...replayEventMeta, event_type: 'vote', payload: { voter_seat: 1, target_seat: 2, round_number: 1 } },
      ],
    });

    render(<GameBoard gameId="game-1" onBack={onBack} />);

    const seatMap = await screen.findByTestId('seat-map');
    expect(seatMap).toHaveAttribute('data-vote-targets', JSON.stringify({ 1: 2 }));
    fireEvent.click(screen.getByRole('button', { name: '历史记录' }));
    expect(screen.getByTestId('history-panel')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '返回' }));
    expect(onBack).toHaveBeenCalledOnce();
  });

  it('polls public logs while the game is in progress and ignores poll failures', async () => {
    vi.useFakeTimers();
    const activeLogs: GameLogs = {
      game_id: 'game-1',
      events: [{ ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } }],
    };
    vi.mocked(fetchGameDetail).mockResolvedValueOnce({
      ...detail,
      phase: 'speech',
      win_result: null,
    });
    vi.mocked(fetchGameLogs)
      .mockResolvedValueOnce(activeLogs)
      .mockResolvedValueOnce(activeLogs)
      .mockRejectedValueOnce(new Error('temporary poll failure'));

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId('seat-map')).toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(2);
    expect(fetchGameLogs).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(3);
    expect(fetchGameLogs).toHaveBeenCalledTimes(3);
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(4);
    expect(fetchGameLogs).toHaveBeenCalledTimes(4);
    expect(screen.getByTestId('seat-map')).toBeInTheDocument();
  });

  it('refreshes the public player snapshot before merging each successful poll', async () => {
    vi.useFakeTimers();
    const activeDetail = { ...detail, phase: 'speech' as const, win_result: null };
    const refreshedDetail: PublicGameState = {
      ...activeDetail,
      players: {
        ...activeDetail.players,
        1: { ...activeDetail.players[1], is_sheriff: true },
      },
      sheriff: 1,
    };
    const activeLogs: GameLogs = {
      game_id: 'game-1',
      events: [{ ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } }],
    };
    const refreshedLogs: GameLogs = {
      game_id: 'game-1',
      events: [
        ...activeLogs.events,
        { ...replayEventMeta, event_type: 'speech', payload: { player_seat: 1, text: 'updated', round_number: 1 } },
      ],
    };
    const initPlayersFromDetail = vi.spyOn(useGameStore.getState(), 'initPlayersFromDetail');
    const mergeLogs = vi.spyOn(useGameStore.getState(), 'mergeLogs');
    vi.mocked(fetchGameDetail)
      .mockResolvedValueOnce(activeDetail)
      .mockResolvedValueOnce(refreshedDetail);
    vi.mocked(fetchGameLogs)
      .mockResolvedValueOnce(activeLogs)
      .mockResolvedValueOnce(refreshedLogs);

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => vi.advanceTimersByTimeAsync(3000));

    expect(fetchGameDetail).toHaveBeenCalledTimes(2);
    expect(fetchGameLogs).toHaveBeenCalledTimes(2);
    expect(initPlayersFromDetail).toHaveBeenCalledTimes(2);
    expect(mergeLogs).toHaveBeenCalledOnce();
    expect(initPlayersFromDetail.mock.invocationCallOrder[1])
      .toBeLessThan(mergeLogs.mock.invocationCallOrder[0]);
    expect(useGameStore.getState().players[1].is_sheriff).toBe(true);
    expect(useGameStore.getState().timeline).toEqual(refreshedLogs.events);
  });

  it('discards both poll responses when the public detail request fails and retries next tick', async () => {
    vi.useFakeTimers();
    const activeDetail = { ...detail, phase: 'speech' as const, win_result: null };
    const refreshedDetail: PublicGameState = {
      ...activeDetail,
      players: {
        ...activeDetail.players,
        1: { ...activeDetail.players[1], is_sheriff: true },
      },
      sheriff: 1,
    };
    const activeLogs: GameLogs = {
      game_id: 'game-1',
      events: [{ ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } }],
    };
    const refreshedLogs: GameLogs = {
      game_id: 'game-1',
      events: [
        ...activeLogs.events,
        { ...replayEventMeta, event_type: 'speech', payload: { player_seat: 1, text: 'new', round_number: 1 } },
      ],
    };
    vi.mocked(fetchGameDetail)
      .mockResolvedValueOnce(activeDetail)
      .mockRejectedValueOnce(new Error('temporary detail failure'))
      .mockResolvedValueOnce(refreshedDetail);
    vi.mocked(fetchGameLogs)
      .mockResolvedValueOnce(activeLogs)
      .mockResolvedValueOnce(refreshedLogs)
      .mockResolvedValueOnce(refreshedLogs);

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(useGameStore.getState().timeline).toEqual(activeLogs.events);
    expect(useGameStore.getState().players[1].is_sheriff).toBe(false);

    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(useGameStore.getState().timeline).toEqual(refreshedLogs.events);
    expect(useGameStore.getState().players[1].is_sheriff).toBe(true);
  });

  it('does not merge a pending poll after the board unmounts', async () => {
    vi.useFakeTimers();
    const activeLogs: GameLogs = {
      game_id: 'game-1',
      events: [{ ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } }],
    };
    const pendingPoll = deferred<GameLogs>();
    const mergeLogs = vi.spyOn(useGameStore.getState(), 'mergeLogs');
    vi.mocked(fetchGameDetail).mockResolvedValueOnce({
      ...detail,
      phase: 'speech',
      win_result: null,
    });
    vi.mocked(fetchGameLogs)
      .mockResolvedValueOnce(activeLogs)
      .mockReturnValueOnce(pendingPoll.promise);

    const { unmount } = render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(2);
    expect(fetchGameLogs).toHaveBeenCalledTimes(2);

    unmount();
    await act(async () => pendingPoll.resolve(activeLogs));

    expect(mergeLogs).not.toHaveBeenCalled();
  });

  it('does not merge a pending poll from the previous game', async () => {
    vi.useFakeTimers();
    const firstLogs: GameLogs = {
      game_id: 'game-1',
      events: [{ ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } }],
    };
    const secondLogs: GameLogs = {
      game_id: 'game-2',
      events: [{ ...replayEventMeta, event_type: 'phase', payload: { phase: 'dawn', round_number: 1 } }],
    };
    const pendingPoll = deferred<PublicGameState>();
    const mergeLogs = vi.spyOn(useGameStore.getState(), 'mergeLogs');
    const activeDetail = { ...detail, phase: 'speech' as const, win_result: null };
    vi.mocked(fetchGameDetail)
      .mockResolvedValueOnce(activeDetail)
      .mockReturnValueOnce(pendingPoll.promise)
      .mockResolvedValueOnce({ ...activeDetail, game_id: 'game-2' });
    vi.mocked(fetchGameLogs)
      .mockResolvedValueOnce(firstLogs)
      .mockResolvedValueOnce(firstLogs)
      .mockResolvedValueOnce(secondLogs);

    const { rerender } = render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(fetchGameLogs).toHaveBeenCalledTimes(2);

    rerender(<GameBoard gameId="game-2" onBack={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => pendingPoll.resolve(activeDetail));

    expect(mergeLogs).not.toHaveBeenCalled();
    expect(useGameStore.getState().timeline).toEqual(secondLogs.events);
  });

  it('waits for a pending poll before starting another request', async () => {
    vi.useFakeTimers();
    const activeLogs: GameLogs = {
      game_id: 'game-1',
      events: [{ ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } }],
    };
    const pendingPoll = deferred<GameLogs>();
    vi.mocked(fetchGameDetail).mockResolvedValueOnce({
      ...detail,
      phase: 'speech',
      win_result: null,
    });
    vi.mocked(fetchGameLogs)
      .mockResolvedValueOnce(activeLogs)
      .mockReturnValueOnce(pendingPoll.promise)
      .mockResolvedValueOnce(activeLogs);

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(2);
    expect(fetchGameLogs).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTimeAsync(6000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(2);
    expect(fetchGameLogs).toHaveBeenCalledTimes(2);

    await act(async () => pendingPoll.resolve(activeLogs));
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(3);
    expect(fetchGameLogs).toHaveBeenCalledTimes(3);
  });

  it('waits for a pending public detail request before starting another poll batch', async () => {
    vi.useFakeTimers();
    const activeDetail = { ...detail, phase: 'speech' as const, win_result: null };
    const activeLogs: GameLogs = {
      game_id: 'game-1',
      events: [{ ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } }],
    };
    const pendingDetail = deferred<PublicGameState>();
    vi.mocked(fetchGameDetail)
      .mockResolvedValueOnce(activeDetail)
      .mockReturnValueOnce(pendingDetail.promise)
      .mockResolvedValueOnce(activeDetail);
    vi.mocked(fetchGameLogs).mockResolvedValue(activeLogs);

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(2);
    expect(fetchGameLogs).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTimeAsync(6000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(2);
    expect(fetchGameLogs).toHaveBeenCalledTimes(2);

    await act(async () => pendingDetail.resolve(activeDetail));
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(3);
    expect(fetchGameLogs).toHaveBeenCalledTimes(3);
  });

  it('keeps a failed poll batch locked until both public requests settle', async () => {
    vi.useFakeTimers();
    const activeDetail = { ...detail, phase: 'speech' as const, win_result: null };
    const activeLogs: GameLogs = {
      game_id: 'game-1',
      events: [{ ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } }],
    };
    const pendingLogs = deferred<GameLogs>();
    vi.mocked(fetchGameDetail)
      .mockResolvedValueOnce(activeDetail)
      .mockRejectedValueOnce(new Error('fast detail failure'))
      .mockResolvedValue(activeDetail);
    vi.mocked(fetchGameLogs)
      .mockResolvedValueOnce(activeLogs)
      .mockReturnValueOnce(pendingLogs.promise)
      .mockResolvedValue(activeLogs);

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(2);
    expect(fetchGameLogs).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTimeAsync(6000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(2);
    expect(fetchGameLogs).toHaveBeenCalledTimes(2);

    await act(async () => pendingLogs.resolve(activeLogs));
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(fetchGameDetail).toHaveBeenCalledTimes(3);
    expect(fetchGameLogs).toHaveBeenCalledTimes(3);
  });

  it('does not update game state when unmounted during sequential loading', async () => {
    const pendingDetail = deferred<PublicGameState>();
    const pendingLogs = deferred<GameLogs>();
    vi.mocked(fetchGameDetail).mockReturnValueOnce(pendingDetail.promise);
    vi.mocked(fetchGameLogs).mockReturnValueOnce(pendingLogs.promise);

    const { unmount } = render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    unmount();
    await act(async () => pendingDetail.resolve(detail));
    await act(async () => pendingLogs.resolve(completedLogs));

    expect(useGameStore.getState().timeline).toEqual([]);
    expect(useGameStore.getState().players).toEqual({});
  });

  it('does not update an unmounted board when loading rejects', async () => {
    const pendingDetail = deferred<PublicGameState>();
    vi.mocked(fetchGameDetail).mockReturnValueOnce(pendingDetail.promise);

    const { unmount } = render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    unmount();
    await act(async () => pendingDetail.reject(new Error('late failure')));

    expect(useGameStore.getState().timeline).toEqual([]);
  });

  it('loads an empty public timeline without trying to seek', async () => {
    vi.mocked(fetchGameLogs).mockResolvedValueOnce({ game_id: 'game-1', events: [] });

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(await screen.findByTestId('seat-map')).toBeInTheDocument();
    expect(useGameStore.getState().timelineIndex).toBe(-1);
  });
});
