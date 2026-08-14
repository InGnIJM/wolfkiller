// @vitest-environment jsdom

import { act, cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { PublicPlayerState } from '../../../store/types';
import SeatMap from '../SeatMap';

class MockResizeObserver {
  static instances: MockResizeObserver[] = [];

  callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    MockResizeObserver.instances.push(this);
  }

  observe() {}
  unobserve() {}
  disconnect() {}
}

function fireResize(width: number, height: number) {
  const entry = { contentRect: { width, height } } as ResizeObserverEntry;
  for (const instance of MockResizeObserver.instances) {
    act(() => instance.callback([entry], instance as unknown as ResizeObserver));
  }
}

beforeEach(() => {
  MockResizeObserver.instances = [];
  vi.stubGlobal('ResizeObserver', MockResizeObserver);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('SeatMap role badges', () => {
  it('renders a role badge for known roles and none for a player without a role', () => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-werewolf', camp: 'werewolf' },
      2: { seat_number: 2, is_alive: true, is_sheriff: false, role: 'wolf-killer-villager', camp: 'good' },
      3: { seat_number: 3, is_alive: true, is_sheriff: false },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    expect(screen.getByText('狼人')).toBeInTheDocument();
    expect(screen.getByText('村民')).toBeInTheDocument();
    expect(screen.getAllByText(/狼人|女巫|预言家|猎人|村民|守卫/)).toHaveLength(2);
  });

  it('renders a badge for every known role', () => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-werewolf', camp: 'werewolf' },
      2: { seat_number: 2, is_alive: true, is_sheriff: false, role: 'wolf-killer-witch', camp: 'good' },
      3: { seat_number: 3, is_alive: true, is_sheriff: false, role: 'wolf-killer-seer', camp: 'good' },
      4: { seat_number: 4, is_alive: true, is_sheriff: false, role: 'wolf-killer-hunter', camp: 'good' },
      5: { seat_number: 5, is_alive: true, is_sheriff: false, role: 'wolf-killer-villager', camp: 'good' },
      6: { seat_number: 6, is_alive: true, is_sheriff: false, role: 'wolf-killer-guard', camp: 'good' },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    expect(screen.getByText('狼人')).toBeInTheDocument();
    expect(screen.getByText('女巫')).toBeInTheDocument();
    expect(screen.getByText('预言家')).toBeInTheDocument();
    expect(screen.getByText('猎人')).toBeInTheDocument();
    expect(screen.getByText('村民')).toBeInTheDocument();
    expect(screen.getByText('守卫')).toBeInTheDocument();
  });

  it('labels each seat card with the role for god view', () => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-witch', camp: 'good' },
      2: { seat_number: 2, is_alive: false, is_sheriff: true, role: 'wolf-killer-seer', camp: 'good' },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    expect(screen.getByLabelText('1号 女巫 存活')).toBeInTheDocument();
    expect(screen.getByLabelText('2号 预言家 出局 警长')).toBeInTheDocument();
  });

  it('labels a card without a role with seat and status only', () => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    expect(screen.getByLabelText('1号 存活')).toBeInTheDocument();
  });

  it('renders nothing for an unknown role instead of leaking the raw role string', () => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-unknown', camp: 'werewolf' },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    expect(screen.queryByText(/wolf-killer-unknown/)).not.toBeInTheDocument();
    expect(screen.queryAllByText(/狼人|女巫|预言家|猎人|村民|守卫/)).toHaveLength(0);
    expect(screen.getByLabelText('1号 存活')).toBeInTheDocument();
  });
});
