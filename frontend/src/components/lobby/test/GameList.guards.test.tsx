// @vitest-environment jsdom

import type { ReactNode } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { deleteGame, listFolders, listGames, renameGame } from '../../../api/client';
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

vi.mock('@mui/material', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@mui/material')>();
  return {
    ...actual,
    Dialog: ({ children, onClose }: { children?: ReactNode; onClose?: () => void }) => (
      <section>
        <button aria-label="force-dialog-close" onClick={onClose}>close</button>
        {children}
      </section>
    ),
  };
});

beforeEach(() => {
  vi.mocked(listGames).mockResolvedValue({ games: [] });
  vi.mocked(listFolders).mockResolvedValue({ folders: [] });
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe('GameList dormant dialog guards', () => {
  it('keeps hidden dialog actions inert and allows either dialog to close safely', async () => {
    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());

    screen.getAllByRole('button', { name: '确定' }).forEach((button) => fireEvent.click(button));
    screen.getAllByRole('button', { name: '删除' }).forEach((button) => fireEvent.click(button));
    screen.getAllByRole('button', { name: 'force-dialog-close' }).forEach((button) => {
      fireEvent.click(button);
    });

    expect(renameGame).not.toHaveBeenCalled();
    expect(deleteGame).not.toHaveBeenCalled();
  });
});
