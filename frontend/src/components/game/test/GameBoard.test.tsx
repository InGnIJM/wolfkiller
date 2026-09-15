// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  AudienceApiError, fetchAudienceEvents, fetchAudienceSnapshot,
  fetchGameDetail, fetchGameLogs, GameNotFoundError,
} from '../../../api/client';
import { useGameStore } from '../../../store/gameStore';
import type { GameLogs, PublicGameState } from '../../../store/types';
import GameBoard from '../GameBoard';

const replayEventMeta = { timestamp: '2026-08-13T00:00:00Z' } as const;

const { connect, disconnect } = vi.hoisted(() => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
}));

vi.mock('../../../api/client', () => ({
  fetchAudienceSnapshot: vi.fn(),
  fetchAudienceEvents: vi.fn(),
  AudienceApiError: class AudienceApiError extends Error {
    status: number;
    constructor(status: number) { super(`Audience API unavailable: ${status}`); this.status = status; }
  },
  fetchGameDetail: vi.fn(),
  fetchGameLogs: vi.fn(),
  GameNotFoundError: class GameNotFoundError extends Error {
    constructor() {
      super('对局不存在或已失效');
      this.name = 'GameNotFoundError';
    }
  },
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
  model_snapshot: [{
    config_id: 'model-a',
    name: 'Model A',
    model_id: 'provider/model-a',
    base_url: 'https://models.example/v1',
    provider_profile: 'openrouter',
    count: 2,
    seats: [1, 2],
  }],
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
  vi.mocked(fetchAudienceSnapshot).mockRejectedValue(new AudienceApiError(409));
  vi.mocked(fetchAudienceEvents).mockResolvedValue({
    game_id: 'game-1', after_seq: 0, last_seq: 0, events: [], caught_up: true,
  });
});

afterEach(() => {
  cleanup();
  useGameStore.getState().reset();
  vi.resetAllMocks();
  vi.useRealTimers();
  window.history.replaceState({}, '', '/');
});

describe('GameBoard public replay', () => {
  it('reloads the snapshot and reconnects from zero when the server rejects the cursor', async () => {
    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await screen.findByTestId('seat-map');
    useGameStore.setState({ audienceCursor: 100 });
    await act(async () => { connect.mock.calls[0][1](); });
    expect(fetchAudienceSnapshot).toHaveBeenCalledTimes(2);
    expect(disconnect).toHaveBeenCalledOnce();
    expect(connect).toHaveBeenCalledTimes(2);
    expect(useGameStore.getState().audienceCursor).toBe(0);
    expect(useGameStore.getState().modelSnapshot).toEqual(detail.model_snapshot);
  });

  it('treats a public detail without model_snapshot as unknown seat models', async () => {
    const { model_snapshot: _omitted, ...withoutSnapshot } = detail;
    vi.mocked(fetchGameDetail).mockResolvedValueOnce(withoutSnapshot);

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(await screen.findByTestId('seat-map')).toBeInTheDocument();
    expect(useGameStore.getState().modelSnapshot).toEqual([]);
  });

  it('resets the previous game cursor before connecting', () => {
    useGameStore.setState({ gameId: 'old-game', audienceCursor: 200 });
    connect.mockImplementationOnce(() => {
      expect(useGameStore.getState().audienceCursor).toBe(0);
    });
    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
  });
  it('loads a new game from snapshot and incremental events without reading full logs', async () => {
    vi.mocked(fetchAudienceSnapshot).mockResolvedValueOnce({
      game_id: 'game-1', seq: 1, projection_version: 1,
      state: {
        ...detail,
        phase: 'speech',
        win_result: null,
        execution_status: 'running',
        model_snapshot: [{
          config_id: 'model-a',
          name: 'Model A',
          model_id: 'provider/model-a',
          base_url: 'https://models.example/v1',
          provider_profile: 'openrouter',
          count: 1,
          seats: [1],
        }],
      },
    });
    vi.mocked(fetchAudienceEvents).mockResolvedValueOnce({
      game_id: 'game-1', after_seq: 0, last_seq: 1, caught_up: true,
      events: [{
        seq: 1, event_id: 'event-1', schema_version: 1, event_type: 'phase',
        timestamp: '2026-08-13T00:00:00Z', payload: { phase: 'speech', round_number: 1 },
      }],
    });

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(await screen.findByTestId('seat-map')).toBeInTheDocument();
    expect(fetchAudienceSnapshot).toHaveBeenCalledWith('game-1');
    expect(fetchGameDetail).not.toHaveBeenCalled();
    expect(fetchGameLogs).not.toHaveBeenCalled();
    expect(useGameStore.getState().syncMode).toBe('incremental');
    expect(useGameStore.getState().modelSnapshot).toEqual([{
      config_id: 'model-a',
      name: 'Model A',
      model_id: 'provider/model-a',
      base_url: 'https://models.example/v1',
      provider_profile: 'openrouter',
      count: 1,
      seats: [1],
    }]);
  });

  it('loads a zero-sequence snapshot without requesting an empty history page', async () => {
    vi.mocked(fetchAudienceSnapshot).mockResolvedValueOnce({
      game_id: 'game-1', seq: 0, projection_version: 1,
      state: { ...detail, phase: 'speech', win_result: null },
    });

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(await screen.findByTestId('seat-map')).toBeInTheDocument();
    expect(fetchAudienceEvents).not.toHaveBeenCalled();
  });

  it('pages public history to the snapshot boundary and seeks to a requested sequence', async () => {
    window.history.replaceState({}, '', '/?seq=2');
    vi.mocked(fetchAudienceSnapshot).mockResolvedValueOnce({
      game_id: 'game-1', seq: 3, projection_version: 1,
      state: { ...detail, phase: 'speech', win_result: null },
    });
    vi.mocked(fetchAudienceEvents)
      .mockResolvedValueOnce({
        game_id: 'game-1', after_seq: 0, last_seq: 1, caught_up: false,
        events: [{
          seq: 1, event_id: 'event-1', schema_version: 1, event_type: 'phase',
          timestamp: replayEventMeta.timestamp, payload: { phase: 'night', round_number: 1 },
        }],
      })
      .mockResolvedValueOnce({
        game_id: 'game-1', after_seq: 1, last_seq: 3, caught_up: true,
        events: [
          {
            seq: 2, event_id: 'event-2', schema_version: 1, event_type: 'phase',
            timestamp: replayEventMeta.timestamp, payload: { phase: 'dawn', round_number: 1 },
          },
          {
            seq: 3, event_id: 'event-3', schema_version: 1, event_type: 'phase',
            timestamp: replayEventMeta.timestamp, payload: { phase: 'speech', round_number: 1 },
          },
        ],
      });

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(await screen.findByTestId('seat-map')).toBeInTheDocument();
    expect(fetchAudienceEvents).toHaveBeenNthCalledWith(1, 'game-1', 0, { limit: 100, throughSeq: 3 });
    expect(fetchAudienceEvents).toHaveBeenNthCalledWith(2, 'game-1', 1, { limit: 100, throughSeq: 3 });
    expect(useGameStore.getState().timelineIndex).toBe(1);
  });

  it('keeps the initial position when a requested sequence is beyond the archive', async () => {
    window.history.replaceState({}, '', '/?seq=99');
    vi.mocked(fetchAudienceSnapshot).mockResolvedValueOnce({
      game_id: 'game-1', seq: 1, projection_version: 1,
      state: { ...detail, phase: 'speech', win_result: null },
    });
    vi.mocked(fetchAudienceEvents).mockResolvedValueOnce({
      game_id: 'game-1', after_seq: 0, last_seq: 1, caught_up: true,
      events: [{
        seq: 1, event_id: 'event-1', schema_version: 1, event_type: 'phase',
        timestamp: replayEventMeta.timestamp, payload: { phase: 'speech', round_number: 1 },
      }],
    });

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(await screen.findByTestId('seat-map')).toBeInTheDocument();
    expect(useGameStore.getState().timelineIndex).toBe(0);
  });

  it('stops paging when an audience page makes no cursor progress', async () => {
    vi.mocked(fetchAudienceSnapshot).mockResolvedValueOnce({
      game_id: 'game-1', seq: 2, projection_version: 1,
      state: { ...detail, phase: 'speech', win_result: null },
    });
    vi.mocked(fetchAudienceEvents).mockResolvedValueOnce({
      game_id: 'game-1', after_seq: 0, last_seq: 0, caught_up: false, events: [],
    });

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(await screen.findByTestId('seat-map')).toBeInTheDocument();
    expect(fetchAudienceEvents).toHaveBeenCalledOnce();
  });

  it.each([
    [new AudienceApiError(500), 'Audience API unavailable: 500'],
    [new Error('snapshot network failure'), 'snapshot network failure'],
  ])('surfaces unexpected audience snapshot failures', async (failure, message) => {
    vi.mocked(fetchAudienceSnapshot).mockRejectedValueOnce(failure);

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(await screen.findByText(message)).toBeVisible();
    expect(fetchGameDetail).not.toHaveBeenCalled();
  });

  it('does not apply a snapshot that resolves after unmount', async () => {
    const pending = deferred<Awaited<ReturnType<typeof fetchAudienceSnapshot>>>();
    vi.mocked(fetchAudienceSnapshot).mockReturnValueOnce(pending.promise);
    const { unmount } = render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    unmount();

    await act(async () => pending.resolve({
      game_id: 'game-1', seq: 0, projection_version: 1, state: detail,
    }));
    expect(useGameStore.getState().players).toEqual({});
  });

  it('does not apply public history that resolves after unmount', async () => {
    const pending = deferred<Awaited<ReturnType<typeof fetchAudienceEvents>>>();
    vi.mocked(fetchAudienceSnapshot).mockResolvedValueOnce({
      game_id: 'game-1', seq: 1, projection_version: 1, state: detail,
    });
    vi.mocked(fetchAudienceEvents).mockReturnValueOnce(pending.promise);
    const { unmount } = render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await waitFor(() => expect(fetchAudienceEvents).toHaveBeenCalledOnce());
    unmount();

    await act(async () => pending.resolve({
      game_id: 'game-1', after_seq: 0, last_seq: 1, caught_up: true, events: [],
    }));
    expect(useGameStore.getState().timeline).toEqual([]);
  });

  it('connects to the game websocket once without waiting for REST loading', () => {
    const pendingDetail = deferred<PublicGameState>();
    vi.mocked(fetchGameDetail).mockReturnValueOnce(pendingDetail.promise);

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(connect).toHaveBeenCalledOnce();
    expect(connect).toHaveBeenCalledWith('game-1', expect.any(Function));
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
    expect(connect).toHaveBeenNthCalledWith(1, 'game-1', expect.any(Function));
    expect(connect).toHaveBeenNthCalledWith(2, 'game-2', expect.any(Function));
  });

  it('disconnects the websocket when the board unmounts', () => {
    const pendingDetail = deferred<PublicGameState>();
    vi.mocked(fetchGameDetail).mockReturnValueOnce(pendingDetail.promise);

    const { unmount } = render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    unmount();

    expect(disconnect).toHaveBeenCalledOnce();
  });

  it('shows the public winner overlay after loading a winner event', async () => {
    vi.mocked(fetchGameDetail).mockResolvedValueOnce({ ...detail, reveal_on_death: true });

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('好人阵营获胜')).toBeInTheDocument();
    expect(screen.getByText('身份公开：玩家出局时会向场上公开身份。')).toBeVisible();
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

  it('polls incremental audience events and renders stream and execution failures', async () => {
    vi.useFakeTimers();
    const mergeAudienceEvents = vi.spyOn(useGameStore.getState(), 'mergeAudienceEvents');
    vi.mocked(fetchAudienceSnapshot).mockResolvedValueOnce({
      game_id: 'game-1', seq: 0, projection_version: 1,
      state: {
        ...detail,
        phase: 'speech',
        win_result: null,
        execution_status: 'failed',
      },
    });
    vi.mocked(fetchAudienceEvents).mockResolvedValueOnce({
      game_id: 'game-1', after_seq: 0, last_seq: 1, caught_up: true,
      events: [{
        seq: 1, event_id: 'event-1', schema_version: 1, event_type: 'phase',
        timestamp: replayEventMeta.timestamp, payload: { phase: 'dawn', round_number: 1 },
      }],
    });

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    act(() => useGameStore.setState({ streamError: '事件流暂时中断' }));

    expect(screen.getByText('执行：failed')).toBeVisible();
    expect(screen.getByText('事件流暂时中断')).toBeVisible();
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(mergeAudienceEvents).toHaveBeenCalledOnce();
    expect(useGameStore.getState().phase).toBe('dawn');
  });

  it('does not merge an incremental poll that resolves after unmount', async () => {
    vi.useFakeTimers();
    const pending = deferred<Awaited<ReturnType<typeof fetchAudienceEvents>>>();
    const mergeAudienceEvents = vi.spyOn(useGameStore.getState(), 'mergeAudienceEvents');
    vi.mocked(fetchAudienceSnapshot).mockResolvedValueOnce({
      game_id: 'game-1', seq: 0, projection_version: 1,
      state: { ...detail, phase: 'speech', win_result: null },
    });
    vi.mocked(fetchAudienceEvents).mockReturnValueOnce(pending.promise);

    const { unmount } = render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    unmount();
    await act(async () => pending.resolve({
      game_id: 'game-1', after_seq: 0, last_seq: 1, caught_up: true, events: [],
    }));

    expect(mergeAudienceEvents).not.toHaveBeenCalled();
  });

  it('shows a friendly notice and stops polling when the game disappears', async () => {
    vi.useFakeTimers();
    const activeDetail = { ...detail, phase: 'speech' as const, win_result: null };
    const activeLogs: GameLogs = {
      game_id: 'game-1',
      events: [{ ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } }],
    };
    vi.mocked(fetchGameDetail)
      .mockResolvedValueOnce(activeDetail)
      .mockRejectedValueOnce(new GameNotFoundError());
    vi.mocked(fetchGameLogs)
      .mockResolvedValueOnce(activeLogs)
      .mockRejectedValueOnce(new GameNotFoundError());

    render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId('seat-map')).toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(3000));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText('对局不存在或已失效')).toBeVisible();

    const callsAfterError = vi.mocked(fetchGameDetail).mock.calls.length;
    await act(async () => vi.advanceTimersByTimeAsync(9000));
    expect(vi.mocked(fetchGameDetail).mock.calls.length).toBe(callsAfterError);
  });

  it('refreshes the public player snapshot before merging each successful poll', async () => {
    vi.useFakeTimers();
    const activeDetail = {
      ...detail,
      phase: 'speech' as const,
      win_result: null,
      reveal_on_death: false,
    };
    const refreshedDetail: PublicGameState = {
      ...activeDetail,
      model_snapshot: undefined,
      players: {
        ...activeDetail.players,
        1: { ...activeDetail.players[1], is_sheriff: true },
      },
      sheriff: 1,
      reveal_on_death: true,
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
    expect(useGameStore.getState().revealOnDeath).toBe(true);
    expect(useGameStore.getState().modelSnapshot).toEqual([]);
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

  it('does not load legacy logs that resolve after unmount', async () => {
    const pendingLogs = deferred<GameLogs>();
    vi.mocked(fetchGameLogs).mockReturnValueOnce(pendingLogs.promise);

    const { unmount } = render(<GameBoard gameId="game-1" onBack={vi.fn()} />);
    await waitFor(() => expect(fetchGameLogs).toHaveBeenCalledOnce());
    unmount();
    await act(async () => pendingLogs.resolve(completedLogs));

    expect(useGameStore.getState().timeline).toEqual([]);
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
    await waitFor(() => expect(useGameStore.getState().timelineIndex).toBe(-1));
  });
});
