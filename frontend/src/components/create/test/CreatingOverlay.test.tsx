// @vitest-environment jsdom

import { act, cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';

import CreatingOverlay from '../CreatingOverlay';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('CreatingOverlay', () => {
  it('renders the title, first stage line and elapsed counter', () => {
    render(<CreatingOverlay />);

    expect(screen.getByTestId('creating-overlay')).toBeInTheDocument();
    expect(screen.getByText('创建对局')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('分发身份牌…');
    expect(screen.getByText('已等待 0 秒')).toBeInTheDocument();
    expect(screen.getByText(/正在初始化对局/)).toBeInTheDocument();
  });

  it('rotates stage lines on the interval and wraps around', () => {
    vi.useFakeTimers();
    render(<CreatingOverlay />);

    act(() => { vi.advanceTimersByTime(3200); });
    expect(screen.getByRole('status')).toHaveTextContent('唤醒 AI 玩家…');
    act(() => { vi.advanceTimersByTime(3200); });
    expect(screen.getByRole('status')).toHaveTextContent('预热模型会话…');
    act(() => { vi.advanceTimersByTime(3200); });
    expect(screen.getByRole('status')).toHaveTextContent('分发身份牌…');
  });

  it('ticks the elapsed seconds counter once per second', () => {
    vi.useFakeTimers();
    render(<CreatingOverlay />);

    act(() => { vi.advanceTimersByTime(5000); });
    expect(screen.getByText('已等待 5 秒')).toBeInTheDocument();
  });

  it('clears its timers on unmount', () => {
    vi.useFakeTimers();
    const { unmount } = render(<CreatingOverlay />);
    unmount();

    act(() => { vi.advanceTimersByTime(10000); });
    expect(vi.getTimerCount()).toBe(0);
  });
});
