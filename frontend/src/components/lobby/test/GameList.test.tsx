// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createGame, listGames } from '../../../api/client';
import GameList from '../GameList';

vi.mock('../../../api/client', () => ({
  createGame: vi.fn(),
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

vi.mock('../CreateGame', () => ({
  default: ({
    open,
    onClose,
    onCreate,
  }: {
    open: boolean;
    onClose: () => void;
    onCreate: (config: Record<string, number>) => Promise<void>;
  }) =>
    open ? (
      <div data-testid="create-game">
        <button onClick={onClose}>close-create</button>
        <button onClick={() => void onCreate({ werewolf: 1 })}>submit-create</button>
      </div>
    ) : null,
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

beforeEach(() => {
  vi.mocked(listGames).mockResolvedValue({ games: [] });
  vi.mocked(createGame).mockResolvedValue({ game_id: 'created-game' });
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
      .mockResolvedValueOnce({
        games: [
          {
            game_id: 'game-1',
            phase: 'night',
            round_number: 1,
            player_count: 6,
            alive_count: 6,
            winner: null,
          },
        ],
      })
      .mockResolvedValueOnce({
        games: [
          {
            game_id: 'game-2',
            phase: 'custom-phase' as never,
            round_number: 2,
            player_count: 6,
            alive_count: 5,
            winner: null,
          },
        ],
      });
    const onJoinGame = vi.fn();

    render(<GameList onJoinGame={onJoinGame} />);

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

    const { unmount } = render(<GameList onJoinGame={vi.fn()} />);
    await act(async () => Promise.resolve());
    expect(listGames).toHaveBeenCalledOnce();

    unmount();
    await act(async () => pending.reject(new Error('late failure')));
    await act(async () => vi.advanceTimersByTimeAsync(3000));

    expect(errorSpy).not.toHaveBeenCalled();
    expect(listGames).toHaveBeenCalledOnce();
  });

  it('ignores an in-flight successful refresh after unmount', async () => {
    const pending = deferred<{
      games: Array<{
        game_id: string;
        phase: 'night';
        round_number: number;
        player_count: number;
        alive_count: number;
        winner: null;
      }>;
    }>();
    vi.mocked(listGames).mockReturnValueOnce(pending.promise);

    const { unmount } = render(<GameList onJoinGame={vi.fn()} />);
    await act(async () => Promise.resolve());
    unmount();
    await act(async () =>
      pending.resolve({
        games: [
          {
            game_id: 'late-game',
            phase: 'night',
            round_number: 1,
            player_count: 6,
            alive_count: 6,
            winner: null,
          },
        ],
      }),
    );

    expect(screen.queryByTestId('game-late-game')).not.toBeInTheDocument();
  });

  it('reports a refresh error while mounted and keeps polling', async () => {
    vi.useFakeTimers();
    const error = new Error('temporary failure');
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(listGames).mockRejectedValueOnce(error).mockResolvedValueOnce({ games: [] });

    render(<GameList onJoinGame={vi.fn()} />);
    await act(async () => Promise.resolve());

    expect(errorSpy).toHaveBeenCalledWith('Failed to list games:', error);
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(listGames).toHaveBeenCalledTimes(2);
  });

  it('creates a game, closes the dialog, and joins the created game', async () => {
    const onJoinGame = vi.fn();
    render(<GameList onJoinGame={onJoinGame} />);
    await act(async () => Promise.resolve());

    fireEvent.click(screen.getByRole('button', { name: /创建游戏/ }));
    expect(screen.getByTestId('create-game')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'close-create' }));
    expect(screen.queryByTestId('create-game')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /创建游戏/ }));
    fireEvent.click(screen.getByRole('button', { name: 'submit-create' }));

    await waitFor(() => expect(createGame).toHaveBeenCalledWith({ werewolf: 1 }));
    expect(onJoinGame).toHaveBeenCalledWith('created-game');
    expect(screen.queryByTestId('create-game')).not.toBeInTheDocument();
  });

  it('reports create failures and restores the create button', async () => {
    const error = new Error('create failed');
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(createGame).mockRejectedValueOnce(error);

    render(<GameList onJoinGame={vi.fn()} />);
    await act(async () => Promise.resolve());
    const createButton = screen.getByRole('button', { name: /创建游戏/ });
    fireEvent.click(createButton);
    fireEvent.click(screen.getByRole('button', { name: 'submit-create' }));

    await waitFor(() => expect(errorSpy).toHaveBeenCalledWith('Failed to create game:', error));
    expect(createButton).toBeEnabled();
  });
});
