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
    cleanup();
    renderCard({ winner: 'unknown' });
    expect(screen.queryByText('好人胜')).not.toBeInTheDocument();
    expect(screen.queryByText('狼人胜')).not.toBeInTheDocument();
  });

  it('closes the overflow menu without entering the game', () => {
    const { onClick } = renderCard();
    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    expect(onClick).not.toHaveBeenCalled();
  });

  it('exposes execution status and the matching recovery control', () => {
    const onRecover = vi.fn();
    renderCard({
      executionStatus: 'interrupted', recoverable: true, onRecover,
    });
    expect(screen.getByText('已中断')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /恢复对局/ }));
    expect(onRecover).toHaveBeenCalledOnce();
  });

  it('runs pause and resume controls only when their callbacks are available', () => {
    const onPause = vi.fn();
    const { unmount } = render(
      <GameCard
        gameId="running"
        name="运行局"
        phase="night"
        roundNumber={1}
        playerCount={8}
        aliveCount={8}
        winner={null}
        executionStatus="running"
        onClick={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onPause={onPause}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /暂停执行/ }));
    expect(onPause).toHaveBeenCalledOnce();

    unmount();
    const onResume = vi.fn();
    renderCard({ executionStatus: 'paused', onResume });
    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /继续执行/ }));
    expect(onResume).toHaveBeenCalledOnce();

    cleanup();
    renderCard({ executionStatus: 'running' });
    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    expect(screen.queryByRole('menuitem', { name: /暂停执行/ })).not.toBeInTheDocument();
    cleanup();
    renderCard({ executionStatus: 'paused' });
    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    expect(screen.queryByRole('menuitem', { name: /继续执行/ })).not.toBeInTheDocument();
  });

  it('explains and disables a blocked recovery', () => {
    const onRecover = vi.fn();
    renderCard({
      executionStatus: 'recovery_blocked', recoverable: false,
      recoveryBlockCode: 'checkpoint_missing', onRecover,
    });
    expect(screen.getByText('恢复受阻')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    const item = screen.getByRole('menuitem', { name: /恢复对局/ });
    expect(item).toHaveAttribute('aria-disabled', 'true');
    expect(item).toHaveAttribute('title', 'checkpoint_missing');
    fireEvent.click(item);
    expect(onRecover).not.toHaveBeenCalled();

    cleanup();
    renderCard({ executionStatus: 'interrupted', recoverable: false, onRecover });
    fireEvent.click(screen.getByRole('button', { name: '对局操作' }));
    expect(screen.getByRole('menuitem', { name: /恢复对局/ }))
      .toHaveAttribute('title', '此对局无法恢复');

    cleanup();
    renderCard({ executionStatus: 'failed' });
    expect(screen.getByText('执行失败')).toBeInTheDocument();
  });
});
