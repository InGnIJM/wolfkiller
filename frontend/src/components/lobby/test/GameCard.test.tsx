// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';

import GameCard from '../GameCard';

afterEach(() => cleanup());

function renderCard(overrides: Partial<Parameters<typeof GameCard>[0]> = {}) {
  const onClick = vi.fn();
  const onRename = vi.fn();
  const onDelete = vi.fn();
  render(
    <GameCard
      gameId="abcd1234"
      name="8人局 · 8月22日 21:50"
      phase="黑夜"
      roundNumber={2}
      playerCount={8}
      aliveCount={7}
      winner={null}
      onClick={onClick}
      onRename={onRename}
      onDelete={onDelete}
      {...overrides}
    />,
  );
  return { onClick, onRename, onDelete };
}

describe('GameCard', () => {
  it('shows the display name instead of the short id', () => {
    renderCard();
    expect(screen.getByText('8人局 · 8月22日 21:50')).toBeInTheDocument();
    expect(screen.queryByText('abcd1234')).not.toBeInTheDocument();
  });

  it('enters the game when the card body is clicked', () => {
    const { onClick } = renderCard();
    fireEvent.click(screen.getByText('8人局 · 8月22日 21:50'));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('does not enter the game when rename or delete is chosen', () => {
    const { onClick, onRename, onDelete } = renderCard();
    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    fireEvent.click(screen.getByRole('menuitem', { name: '重命名' }));
    expect(onRename).toHaveBeenCalledOnce();
    expect(onClick).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    fireEvent.click(screen.getByRole('menuitem', { name: '删除' }));
    expect(onDelete).toHaveBeenCalledOnce();
    expect(onClick).not.toHaveBeenCalled();
  });

  it('shows winner chips and the error phase label', () => {
    const { unmount } = render(
      <GameCard
        gameId="g1"
        name="好人局"
        phase="error"
        roundNumber={1}
        playerCount={8}
        aliveCount={4}
        winner="good"
        onClick={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByText('好人胜')).toBeInTheDocument();
    expect(screen.getByText(/异常终止/)).toBeInTheDocument();
    unmount();
    renderCard({ winner: 'werewolf', name: '狼局' });
    expect(screen.getByText('狼人胜')).toBeInTheDocument();
  });

  it('closes the overflow menu without entering the game', () => {
    const { onClick } = renderCard();
    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    expect(onClick).not.toHaveBeenCalled();
  });
});
