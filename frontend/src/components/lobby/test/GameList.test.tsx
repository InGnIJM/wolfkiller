// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { listGames } from '../../../api/client';
import type { GameListItem } from '../../../store/types';
import GameList from '../GameList';

vi.mock('../../../api/client', () => ({
  listGames: vi.fn(),
}));

vi.mock('../GameCard', () => ({
  default: ({
    gameId,
    phase,
    onClick,
  }: {
    gameId: string;
    phase: string;
    onClick: () => void;
  }) => (
    <button data-testid={`game-${gameId}`} data-phase={phase} onClick={onClick}>
      {gameId}
    </button>
  ),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const game = (id: string, phase: GameListItem['phase'] = 'night'): GameListItem => ({
  game_id: id,
  name: `${id}-name`,
  phase,
  round_number: 1,
  player_count: 6,
  alive_count: 6,
  winner: null,
});

beforeEach(() => {
  vi.mocked(listGames).mockResolvedValue({ games: [] });
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('GameList polling', () => {
  it('loads immediately and refreshes the visible games every three seconds', async () => {
    vi.useFakeTimers();
    vi.mocked(listGames)
      .mockResolvedValueOnce({ games: [game('game-1')] })
      .mockResolvedValueOnce({ games: [game('game-2', 'custom-phase' as never)] });
    const onJoinGame = vi.fn();

    render(<GameList onJoinGame={onJoinGame} onCreateClick={vi.fn()} />);

    await act(async () => Promise.resolve());
    expect(listGames).toHaveBeenCalledOnce();
    expect(screen.getByTestId('game-game-1')).toHaveAttribute('data-phase', '黑夜');
    fireEvent.click(screen.getByTestId('game-game-1'));
    expect(onJoinGame).toHaveBeenCalledWith('game-1');

    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(listGames).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('game-game-2')).toHaveAttribute('data-phase', 'custom-phase');
  });

  it('clears polling and ignores an in-flight rejection after unmount', async () => {
    vi.useFakeTimers();
    const pending = deferred<{ games: [] }>();
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(listGames).mockReturnValueOnce(pending.promise);

    const { unmount } = render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    expect(listGames).toHaveBeenCalledOnce();

    unmount();
    await act(async () => pending.reject(new Error('late failure')));
    await act(async () => vi.advanceTimersByTimeAsync(3000));

    expect(errorSpy).not.toHaveBeenCalled();
    expect(listGames).toHaveBeenCalledOnce();
  });

  it('ignores an in-flight successful refresh after unmount', async () => {
    const pending = deferred<{ games: Array<ReturnType<typeof game>> }>();
    vi.mocked(listGames).mockReturnValueOnce(pending.promise);

    const { unmount } = render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    unmount();
    await act(async () =>
      pending.resolve({ games: [game('late-game')] }),
    );

    expect(screen.queryByTestId('game-late-game')).not.toBeInTheDocument();
  });

  it('reports a refresh error while mounted and keeps polling', async () => {
    vi.useFakeTimers();
    const error = new Error('temporary failure');
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(listGames).mockRejectedValueOnce(error).mockResolvedValueOnce({ games: [] });

    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());

    expect(errorSpy).toHaveBeenCalledWith('Failed to list games:', error);
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(listGames).toHaveBeenCalledTimes(2);
  });

  it('opens the create wizard through onCreateClick', async () => {
    const onCreateClick = vi.fn();
    render(<GameList onJoinGame={vi.fn()} onCreateClick={onCreateClick} />);
    await act(async () => Promise.resolve());

    fireEvent.click(screen.getByRole('button', { name: /创建游戏/ }));
    expect(onCreateClick).toHaveBeenCalledOnce();
  });
});
