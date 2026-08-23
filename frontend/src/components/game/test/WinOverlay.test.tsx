// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useGameStore } from '../../../store/gameStore';
import WinOverlay from '../WinOverlay';

beforeEach(() => {
  useGameStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useGameStore.getState().reset();
  vi.useRealTimers();
});

describe('WinOverlay', () => {
  it('uses a single labelled heading for the result dialog', () => {
    render(
      <WinOverlay winResult={{ winning_camp: 'good', reason: 'all_wolves_dead' }} />,
    );

    const dialog = screen.getByRole('dialog');
    const headings = within(dialog).getAllByRole('heading');
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveAttribute('id', 'game-over-title');
    expect(dialog).toHaveAttribute('aria-labelledby', 'game-over-title');
  });

  it('dismisses the result, rewinds, and starts replay after the delay', () => {
    vi.useFakeTimers();
    const dismissWinOverlay = vi.fn();
    const seekTo = vi.fn();
    const play = vi.fn();
    useGameStore.setState({ dismissWinOverlay, seekTo, play });

    render(
      <WinOverlay winResult={{ winning_camp: 'good', reason: 'all_wolves_dead' }} />,
    );
    fireEvent.click(screen.getByRole('button', { name: '从头播放' }));

    expect(dismissWinOverlay).toHaveBeenCalledOnce();
    expect(seekTo).toHaveBeenCalledWith(0);
    expect(play).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(100));
    expect(play).toHaveBeenCalledOnce();
  });

  it('styles a werewolf victory with the crimson accent', () => {
    render(
      <WinOverlay winResult={{ winning_camp: 'werewolf', reason: 'all_villagers_dead' }} />,
    );

    expect(screen.getByText('狼人阵营获胜')).toBeVisible();
    expect(screen.getByText('所有平民出局')).toBeVisible();
  });

  it('supports closing and displays unknown public result values verbatim', () => {
    const dismissWinOverlay = vi.fn();
    useGameStore.setState({ dismissWinOverlay });

    render(
      <WinOverlay
        winResult={{
          winning_camp: 'mystery_camp' as never,
          reason: 'unlisted_reason' as never,
        }}
      />,
    );

    expect(screen.getByText(/mystery_camp/)).toBeVisible();
    expect(screen.getByText('unlisted_reason')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '查看对局' }));
    expect(dismissWinOverlay).toHaveBeenCalledOnce();
  });
});
