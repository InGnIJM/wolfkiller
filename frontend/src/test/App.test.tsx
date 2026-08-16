// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AppShell } from '../App';

vi.mock('../components/lobby/GameList', () => ({
  default: ({
    onJoinGame,
    onCreateClick,
  }: {
    onJoinGame: (id: string) => void;
    onCreateClick: () => void;
  }) => (
    <div>
      <button onClick={() => onJoinGame('g1')}>join-g1</button>
      <button onClick={onCreateClick}>open-create</button>
    </div>
  ),
}));

vi.mock('../components/game/GameBoard', () => ({
  default: ({ gameId }: { gameId: string }) => <div data-testid={`board-${gameId}`} />,
}));

vi.mock('../components/models/ModelConfigPage', () => ({
  default: () => <div data-testid="models-page" />,
}));

vi.mock('../components/create/CreateGameWizard', () => ({
  default: () => <div data-testid="wizard-page" />,
}));

const navigateMock = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigateMock };
});

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppShell />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('AppShell routing', () => {
  it('renders lobby at / and joins a game', () => {
    renderAt('/');
    expect(screen.getByRole('button', { name: 'join-g1' })).toBeInTheDocument();
  });

  it('opens the create wizard from the lobby', () => {
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'open-create' }));
    expect(navigateMock).toHaveBeenCalledWith('/create');
  });

  it('renders models page at /models', () => {
    renderAt('/models');
    expect(screen.getByTestId('models-page')).toBeInTheDocument();
  });

  it('renders wizard at /create', () => {
    renderAt('/create');
    expect(screen.getByTestId('wizard-page')).toBeInTheDocument();
  });

  it('renders game board at /game/:gameId', () => {
    renderAt('/game/abc');
    expect(screen.getByTestId('board-abc')).toBeInTheDocument();
  });

  it('renders the 模型管理 nav button', () => {
    renderAt('/');
    expect(screen.getByRole('button', { name: '模型管理' })).toBeInTheDocument();
  });
});
