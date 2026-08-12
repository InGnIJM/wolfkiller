// @vitest-environment jsdom

import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getWsUrl } from '../client';
import { useWebSocket } from '../websocket';
import { useGameStore } from '../../store/gameStore';
import type { PublicGameState, WSMessage } from '../../store/types';

vi.mock('../client', () => ({
  getWsUrl: vi.fn(),
}));

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  readonly url: string;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: ((error: unknown) => void) | null = null;
  send = vi.fn();
  close = vi.fn();

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  emit(message: WSMessage | { type: string }) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

const originalSetNightSubstep = useGameStore.getState().setNightSubstep;
const originalSetPaused = useGameStore.getState().setPaused;

const publicState: PublicGameState = {
  game_id: 'game-1',
  phase: 'speech',
  round_number: 1,
  players: {
    1: { seat_number: 1, is_alive: true, is_sheriff: false },
    2: { seat_number: 2, is_alive: true, is_sheriff: false },
  },
  sheriff: null,
  speeches: [],
  death_history: [],
  win_result: null,
};

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.mocked(getWsUrl).mockReturnValue('ws://example.test/ws/game/game-1');
  useGameStore.setState({
    setNightSubstep: originalSetNightSubstep,
    setPaused: originalSetPaused,
  });
  useGameStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useGameStore.setState({
    setNightSubstep: originalSetNightSubstep,
    setPaused: originalSetPaused,
  });
  useGameStore.getState().reset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('useWebSocket', () => {
  it('constructs the connection and tracks open, error, close, and cleanup', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { result, unmount } = renderHook(() => useWebSocket());

    act(() => result.current.connect('game-1'));
    const socket = MockWebSocket.instances[0];
    expect(getWsUrl).toHaveBeenCalledWith('game-1');
    expect(socket.url).toBe('ws://example.test/ws/game/game-1');

    act(() => socket.onopen?.());
    expect(useGameStore.getState().connected).toBe(true);
    const error = new Event('error');
    act(() => socket.onerror?.(error));
    expect(errorSpy).toHaveBeenCalledWith('WebSocket error:', error);
    act(() => socket.onclose?.());
    expect(useGameStore.getState().connected).toBe(false);

    unmount();
    expect(socket.close).toHaveBeenCalledOnce();
  });

  it('uses the latest store actions for public night and paused messages', () => {
    const staleNight = vi.fn();
    const stalePaused = vi.fn();
    useGameStore.setState({ setNightSubstep: staleNight, setPaused: stalePaused });
    const { result } = renderHook(() => useWebSocket());
    const currentNight = vi.fn();
    const currentPaused = vi.fn();

    act(() => {
      useGameStore.setState({ setNightSubstep: currentNight, setPaused: currentPaused });
    });
    act(() => result.current.connect('game-1'));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.emit({
        type: 'night_substep',
        phase: 'night',
        substep: 'resolve',
        round_number: 3,
      });
      socket.emit({ type: 'paused_state', paused: true });
    });

    expect(currentNight).toHaveBeenCalledWith({ substep: 'resolve', roundNumber: 3 });
    expect(currentPaused).toHaveBeenCalledWith(true);
    expect(staleNight).not.toHaveBeenCalled();
    expect(stalePaused).not.toHaveBeenCalled();
  });

  it('routes every public game message without private identity data', () => {
    const { result } = renderHook(() => useWebSocket());
    act(() => result.current.connect('game-1'));
    const socket = MockWebSocket.instances[0];

    act(() => socket.emit({ type: 'game_state', state: publicState }));
    expect(useGameStore.getState().gameId).toBe('game-1');

    act(() =>
      socket.emit({
        type: 'phase_change',
        phase: 'vote_casting',
        round_number: 2,
        state: { ...publicState, phase: 'vote_casting', round_number: 2 },
      }),
    );
    expect(useGameStore.getState().phase).toBe('vote_casting');

    act(() =>
      socket.emit({
        type: 'speech',
        speech: { player_seat: 1, text: 'public speech', round_number: 2 },
      }),
    );
    act(() =>
      socket.emit({
        type: 'vote_cast',
        vote: { voter_seat: 1, target_seat: 2, round_number: 2 },
      }),
    );
    act(() =>
      socket.emit({
        type: 'player_died',
        death: { player_seat: 2, cause: 'exile', round_number: 2 },
      }),
    );
    expect(useGameStore.getState().speeches).toHaveLength(1);
    expect(useGameStore.getState().votes).toHaveLength(1);
    expect(useGameStore.getState().players[2].is_alive).toBe(false);

    act(() =>
      socket.emit({
        type: 'game_over',
        win_result: { winning_camp: 'good', reason: 'all_wolves_dead' },
        state: { ...publicState, phase: 'game_over' },
      }),
    );
    expect(useGameStore.getState().winResult).toEqual({
      winning_camp: 'good',
      reason: 'all_wolves_dead',
    });
  });

  it('ignores malformed JSON and unknown message types', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { result } = renderHook(() => useWebSocket());
    act(() => result.current.connect('game-1'));
    const socket = MockWebSocket.instances[0];

    expect(() => {
      act(() => socket.onmessage?.({ data: '{not-json' }));
      act(() => socket.emit({ type: 'future_public_event' }));
    }).not.toThrow();
    expect(errorSpy).toHaveBeenCalledOnce();
    expect(useGameStore.getState().gameId).toBeNull();
  });

  it('sends controls, disconnects with reset, and tolerates controls without a socket', () => {
    const first = renderHook(() => useWebSocket());
    expect(() => {
      act(() => {
        first.result.current.sendSpeed(2);
        first.result.current.sendPause();
        first.result.current.sendResume();
      });
      first.unmount();
    }).not.toThrow();

    const second = renderHook(() => useWebSocket());
    act(() => second.result.current.connect('game-1'));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.onopen?.();
      second.result.current.sendSpeed(1.5);
      second.result.current.sendPause();
      second.result.current.sendResume();
      second.result.current.disconnect();
    });

    expect(socket.send.mock.calls.map(([payload]) => payload)).toEqual([
      JSON.stringify({ type: 'set_speed', delay_seconds: 1.5 }),
      JSON.stringify({ type: 'pause' }),
      JSON.stringify({ type: 'resume' }),
    ]);
    expect(socket.close).toHaveBeenCalledOnce();
    expect(useGameStore.getState().connected).toBe(false);
  });
});
