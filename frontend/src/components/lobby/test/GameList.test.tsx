// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  assignGameFolder, batchDeleteGames, batchMoveGames, controlGame, createFolder,
  deleteGame, listFolders, listGames, renameGame,
} from '../../../api/client';
import type { GameListItem } from '../../../store/types';
import GameList from '../GameList';

vi.mock('../../../api/client', () => ({
  listGames: vi.fn(),
  renameGame: vi.fn(),
  deleteGame: vi.fn(),
  controlGame: vi.fn(),
  listFolders: vi.fn(),
  createFolder: vi.fn(),
  assignGameFolder: vi.fn(),
  batchDeleteGames: vi.fn(),
  batchMoveGames: vi.fn(),
}));

vi.mock('../GameCard', () => ({
  default: ({
    gameId,
    name,
    phase,
    onClick,
    onRename,
    onDelete,
    onPause,
    onResume,
    onRecover,
    onMove,
    onToggleSelect,
  }: {
    gameId: string;
    name: string;
    phase: string;
    onClick: () => void;
    onRename: () => void;
    onDelete: () => void;
    onPause: () => void;
    onResume: () => void;
    onRecover: () => void;
    onMove?: () => void;
    onToggleSelect?: () => void;
  }) => (
    <div>
      <button data-testid={`game-${gameId}`} data-phase={phase} onClick={onClick}>
        {name}
      </button>
      <button data-testid={`rename-${gameId}`} onClick={onRename}>rename</button>
      <button data-testid={`delete-${gameId}`} onClick={onDelete}>delete</button>
      <button data-testid={`pause-${gameId}`} onClick={onPause}>pause</button>
      <button data-testid={`resume-${gameId}`} onClick={onResume}>resume</button>
      <button data-testid={`recover-${gameId}`} onClick={onRecover}>recover</button>
      {onMove && <button data-testid={`move-${gameId}`} onClick={onMove}>move</button>}
      {onToggleSelect && <button data-testid={`select-${gameId}`} onClick={onToggleSelect}>select</button>}
    </div>
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
  vi.mocked(listFolders).mockResolvedValue({ folders: [] });
  vi.mocked(controlGame).mockResolvedValue({});
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

describe('GameList management', () => {
  it('applies lifecycle response fields to only the controlled game', async () => {
    vi.mocked(listGames).mockResolvedValueOnce({ games: [game('game-1'), game('game-2')] });
    vi.mocked(controlGame)
      .mockResolvedValueOnce({ ...game('game-1'), execution_status: 'paused' })
      .mockResolvedValueOnce({ ...game('game-2'), execution_status: 'running' });
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());

    fireEvent.click(screen.getByTestId('pause-game-1'));
    await waitFor(() => expect(controlGame).toHaveBeenCalledWith('game-1', 'pause'));
    fireEvent.click(screen.getByTestId('resume-game-2'));
    await waitFor(() => expect(controlGame).toHaveBeenCalledWith('game-2', 'resume'));

    expect(listGames).toHaveBeenCalledOnce();
    expect(screen.getByTestId('game-game-1')).toBeInTheDocument();
    expect(screen.getByTestId('game-game-2')).toBeInTheDocument();
  });

  it.each([
    [new Error('control failed'), 'control failed'],
    ['control denied', 'control denied'],
  ])('shows and dismisses lifecycle control failures', async (failure, message) => {
    vi.mocked(listGames).mockResolvedValueOnce({ games: [game('game-1')] });
    vi.mocked(controlGame).mockRejectedValueOnce(failure);
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());

    fireEvent.click(screen.getByTestId('pause-game-1'));
    expect(await screen.findByText(message)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByText(message)).not.toBeInTheDocument();
  });

  it('sends lifecycle controls and refreshes when the response has no list fields', async () => {
    vi.mocked(listGames)
      .mockResolvedValueOnce({ games: [{ ...game('game-1'), execution_status: 'interrupted', recoverable: true }] })
      .mockResolvedValueOnce({ games: [{ ...game('game-1'), execution_status: 'running' }] });
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());

    fireEvent.click(screen.getByTestId('recover-game-1'));
    await waitFor(() => expect(controlGame).toHaveBeenCalledWith('game-1', 'recover'));
    await waitFor(() => expect(listGames).toHaveBeenCalledTimes(2));
  });

  it('renames a game from the dialog and updates the card title', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1'), game('game-2')] });
    vi.mocked(renameGame).mockResolvedValue({ ...game('game-1'), name: '新名字' });
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());

    fireEvent.click(screen.getByTestId('rename-game-1'));
    const input = screen.getByLabelText('对局名称');
    fireEvent.change(input, { target: { value: '新名字' } });
    fireEvent.click(screen.getByRole('button', { name: '确定' }));

    await waitFor(() => expect(renameGame).toHaveBeenCalledWith('game-1', '新名字'));
    // 等待重命名后的状态更新完成渲染，避免与 mock 调用检测产生竞态
    await waitFor(() => expect(screen.getByTestId('game-game-1')).toHaveTextContent('新名字'));
    expect(screen.getByTestId('game-game-2')).toHaveTextContent('game-2-name');
  });

  it('cancels rename without calling the API', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1')] });
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('rename-game-1'));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(renameGame).not.toHaveBeenCalled();
    expect(screen.getByTestId('game-game-1')).toHaveTextContent('game-1-name');
  });

  it('keeps the rename dialog open when the name is blank', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1')] });
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('rename-game-1'));
    fireEvent.change(screen.getByLabelText('对局名称'), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    expect(renameGame).not.toHaveBeenCalled();
    expect(screen.getByLabelText('对局名称')).toBeInTheDocument();
  });

  it('shows rename errors in the dialog', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1')] });
    vi.mocked(renameGame).mockRejectedValue(new Error('Rename game failed: 400'));
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('rename-game-1'));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    await waitFor(() => expect(screen.getByText(/Rename game failed: 400/)).toBeInTheDocument());
    expect(screen.getByLabelText('对局名称')).toBeInTheDocument();
  });

  it('shows non-error rename failures', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1')] });
    vi.mocked(renameGame).mockRejectedValue('boom');
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('rename-game-1'));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
  });

  it('warns when deleting an in-progress game and removes it after confirm', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1', 'night')] });
    vi.mocked(deleteGame).mockResolvedValue(undefined);
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('delete-game-1'));
    expect(screen.getByText(/对局正在进行，删除将立即中断/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(deleteGame).toHaveBeenCalledWith('game-1'));
    await waitFor(() => expect(screen.queryByTestId('game-game-1')).not.toBeInTheDocument());
  });

  it('does not warn when deleting a finished or error game', async () => {
    vi.mocked(listGames).mockResolvedValue({
      games: [game('done', 'game_over'), game('bad', 'error')],
    });
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('delete-done'));
    expect(screen.queryByText(/对局正在进行/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    fireEvent.click(screen.getByTestId('delete-bad'));
    expect(screen.queryByText(/对局正在进行/)).not.toBeInTheDocument();
  });

  it('keeps the delete dialog open when delete fails', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1', 'game_over')] });
    vi.mocked(deleteGame).mockRejectedValue(new Error('Delete game failed: 500'));
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('delete-game-1'));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(screen.getByText(/Delete game failed: 500/)).toBeInTheDocument());
    expect(screen.getByTestId('game-game-1')).toBeInTheDocument();
  });

  it('shows non-error delete failures', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1', 'game_over')] });
    vi.mocked(deleteGame).mockRejectedValue('busy');
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTestId('delete-game-1'));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(screen.getByText('busy')).toBeInTheDocument());
  });

  it('creates folders, filters them, and supports batch move/delete', async () => {
    vi.mocked(listGames).mockResolvedValue({
      games: [game('game-1'), { ...game('game-2'), folder_id: 'f1' }],
    });
    vi.mocked(listFolders).mockResolvedValue({
      folders: [{ folder_id: 'f1', name: '九月', game_count: 1, created_at: '', updated_at: '' }],
    });
    vi.mocked(createFolder).mockResolvedValue({
      folder_id: 'f2', name: '归档', game_count: 0, created_at: '', updated_at: '',
    });
    vi.mocked(assignGameFolder).mockResolvedValue(undefined);
    vi.mocked(batchMoveGames).mockResolvedValue({
      moved: ['game-1', 'game-2'], failed: [],
    });
    vi.mocked(batchDeleteGames).mockResolvedValue({ deleted: ['game-1'], failed: [] });
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());

    fireEvent.click(screen.getByText('未分类'));
    expect(screen.getByTestId('game-game-1')).toBeInTheDocument();
    expect(screen.queryByTestId('game-game-2')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('九月 (1)'));
    expect(screen.getByTestId('game-game-2')).toBeInTheDocument();
    fireEvent.click(screen.getByText('全部'));

    fireEvent.click(screen.getByRole('button', { name: '新建文件夹' }));
    fireEvent.click(screen.getByRole('button', { name: '创建' }));
    await waitFor(() => expect(screen.getByText('名称不能为空')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    fireEvent.click(screen.getByRole('button', { name: '新建文件夹' }));
    fireEvent.change(screen.getByLabelText('文件夹名称'), { target: { value: '归档' } });
    fireEvent.click(screen.getByRole('button', { name: '创建' }));
    await waitFor(() => expect(createFolder).toHaveBeenCalledWith('归档'));

    fireEvent.click(screen.getByTestId('move-game-1'));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    await waitFor(() => expect(assignGameFolder).toHaveBeenCalledWith('game-1', null));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    fireEvent.click(screen.getByTestId('select-game-1'));
    fireEvent.click(screen.getByTestId('select-game-2'));
    fireEvent.click(screen.getByRole('button', { name: '批量移动' }));
    fireEvent.mouseDown(screen.getByLabelText('文件夹'));
    fireEvent.click(await screen.findByRole('option', { name: '九月' }));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    await waitFor(() => expect(batchMoveGames).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    fireEvent.click(screen.getByTestId('select-game-1'));
    fireEvent.click(screen.getByTestId('select-game-1'));
    fireEvent.click(screen.getByTestId('select-game-1'));
    fireEvent.click(screen.getByRole('button', { name: '取消选择' }));
    expect(screen.queryByRole('button', { name: '批量删除' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('select-game-1'));
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(batchDeleteGames).toHaveBeenCalled());
  });

  it('keeps batch dialogs open on partial or thrown failures', async () => {
    vi.mocked(listGames).mockResolvedValue({ games: [game('game-1'), game('game-2')] });
    vi.mocked(batchDeleteGames)
      .mockResolvedValueOnce({
        deleted: [], failed: [{ game_id: 'game-1', code: 'not_found', message: 'missing' }],
      })
      .mockRejectedValueOnce(new Error('delete boom'))
      .mockRejectedValueOnce('delete string');
    vi.mocked(batchMoveGames)
      .mockRejectedValueOnce('move string')
      .mockRejectedValueOnce(new Error('move boom'))
      .mockResolvedValueOnce({
        moved: [], failed: [{ game_id: 'game-1', code: 'busy', message: 'nope' }],
      });
    vi.mocked(createFolder)
      .mockRejectedValueOnce('exists')
      .mockRejectedValueOnce(new Error('folder boom'));
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole('button', { name: '新建文件夹' }));
    fireEvent.change(screen.getByLabelText('文件夹名称'), { target: { value: '重复' } });
    fireEvent.click(screen.getByRole('button', { name: '创建' }));
    await waitFor(() => expect(screen.getByText('exists')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '创建' }));
    await waitFor(() => expect(screen.getByText('folder boom')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('select-game-1'));
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(screen.getByText('missing')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(screen.getByText('delete boom')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(screen.getByText('delete string')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    fireEvent.click(screen.getByTestId('select-game-2'));
    fireEvent.click(screen.getByRole('button', { name: '批量移动' }));
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    await waitFor(() => expect(screen.getByText('move string')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    await waitFor(() => expect(screen.getByText('move boom')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    await waitFor(() => expect(screen.getByText('nope')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});
