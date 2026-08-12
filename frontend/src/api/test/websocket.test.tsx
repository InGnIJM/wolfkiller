// @vitest-environment jsdom

import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getWsUrl } from '../client';
import { useWebSocket } from '../websocket';
import { useGameStore } from '../../store/gameStore';

vi.mock('../client', () => ({
  getWsUrl: vi.fn(),
}));

type MessageHandler = (event: { data: string }) => void;

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  readonly url: string;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: MessageHandler | null = null;
  onerror: ((error: unknown) => void) | null = null;
  send = vi.fn();
  close = vi.fn();

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  emit(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

const PUBLIC_NIGHT_SUBSTEPS = [
  'werewolf_open',
  'werewolf_vote',
  'werewolf_target',
  'werewolf_close',
  'witch_open',
  'witch_action',
  'witch_close',
  'seer_open',
  'seer_check',
  'seer_close',
] as const;

const originalSetConnected = useGameStore.getState().setConnected;
const originalSetNightSubstep = useGameStore.getState().setNightSubstep;
const originalSetPaused = useGameStore.getState().setPaused;

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.mocked(getWsUrl).mockImplementation(
    (gameId: string) => `ws://example.test/ws/game/${gameId}`,
  );
  useGameStore.setState({
    setConnected: originalSetConnected,
    setNightSubstep: originalSetNightSubstep,
    setPaused: originalSetPaused,
  });
  useGameStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useGameStore.setState({
    setConnected: originalSetConnected,
    setNightSubstep: originalSetNightSubstep,
    setPaused: originalSetPaused,
  });
  useGameStore.getState().reset();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('useWebSocket', () => {
  it('replaces the prior socket and ignores every late callback from it', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const setNightSubstep = vi.fn();
    const setPaused = vi.fn();
    useGameStore.setState({ setNightSubstep, setPaused });
    const { result } = renderHook(() => useWebSocket());

    act(() => result.current.connect('game-1'));
    const staleSocket = MockWebSocket.instances[0];
    const staleHandlers = {
      open: staleSocket.onopen,
      close: staleSocket.onclose,
      error: staleSocket.onerror,
      message: staleSocket.onmessage,
    };
    act(() => staleHandlers.open?.());
    expect(useGameStore.getState().connected).toBe(true);

    act(() => result.current.connect('game-2'));
    const currentSocket = MockWebSocket.instances[1];
    expect(getWsUrl).toHaveBeenNthCalledWith(1, 'game-1');
    expect(getWsUrl).toHaveBeenNthCalledWith(2, 'game-2');
    expect(currentSocket.url).toBe('ws://example.test/ws/game/game-2');
    expect(staleSocket.close).toHaveBeenCalledOnce();
    expect(staleSocket.onopen).toBeNull();
    expect(staleSocket.onclose).toBeNull();
    expect(staleSocket.onerror).toBeNull();
    expect(staleSocket.onmessage).toBeNull();
    expect(useGameStore.getState().connected).toBe(false);

    act(() => currentSocket.onopen?.());
    expect(useGameStore.getState().connected).toBe(true);
    act(() => {
      staleHandlers.open?.();
      staleHandlers.message?.({
        data: JSON.stringify({ type: 'paused_state', paused: true }),
      });
      staleHandlers.message?.({
        data: JSON.stringify({
          type: 'night_substep',
          phase: 'night',
          substep: 'witch_open',
          round_number: 2,
        }),
      });
      staleHandlers.error?.(new Event('error'));
      staleHandlers.close?.();
    });
    expect(useGameStore.getState().connected).toBe(true);
    expect(setPaused).not.toHaveBeenCalled();
    expect(setNightSubstep).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();

    const currentError = new Event('error');
    act(() => currentSocket.onerror?.(currentError));
    expect(errorSpy).toHaveBeenCalledWith('WebSocket error:', currentError);
    act(() => currentSocket.onclose?.());
    expect(useGameStore.getState().connected).toBe(false);
  });

  it('accepts only exact paused and public night-substep messages at runtime', () => {
    const setNightSubstep = vi.fn();
    const setPaused = vi.fn();
    useGameStore.setState({ setNightSubstep, setPaused });
    const { result } = renderHook(() => useWebSocket());
    act(() => result.current.connect('game-1'));
    const socket = MockWebSocket.instances[0];

    act(() => {
      socket.emit({ type: 'paused_state', paused: true });
      socket.emit({ type: 'paused_state', paused: false });
      PUBLIC_NIGHT_SUBSTEPS.forEach((substep, index) => {
        socket.emit({
          type: 'night_substep',
          phase: 'night',
          substep,
          round_number: index + 1,
        });
      });
    });

    expect(setPaused.mock.calls).toEqual([[true], [false]]);
    expect(setNightSubstep.mock.calls.map(([value]) => value)).toEqual(
      PUBLIC_NIGHT_SUBSTEPS.map((substep, index) => ({
        substep,
        roundNumber: index + 1,
      })),
    );

    setPaused.mockClear();
    setNightSubstep.mockClear();
    const invalidMessages: unknown[] = [
      null,
      [],
      'paused_state',
      { type: 'paused_state' },
      { type: 'paused_state', paused: 1 },
      { type: 'paused_state', paused: true, role: 'werewolf' },
      { type: 'night_substep', phase: 'day', substep: 'witch_open', round_number: 1 },
      { type: 'night_substep', phase: 'night', substep: 'resolve', round_number: 1 },
      { type: 'night_substep', phase: 'night', substep: 'witch_open', round_number: 0 },
      { type: 'night_substep', phase: 'night', substep: 'witch_open', round_number: -1 },
      { type: 'night_substep', phase: 'night', substep: 'witch_open', round_number: 1.5 },
      { type: 'night_substep', phase: 'night', substep: 'witch_open', round_number: '1' },
      { type: 'night_substep', phase: 'night', substep: 'witch_open', round_number: 1, thought: 'secret' },
    ];
    act(() => invalidMessages.forEach((message) => socket.emit(message)));
    expect(setPaused).not.toHaveBeenCalled();
    expect(setNightSubstep).not.toHaveBeenCalled();
  });

  it('ignores replay, state, malformed, unknown, and private websocket events', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { result } = renderHook(() => useWebSocket());
    act(() => result.current.connect('game-1'));
    const socket = MockWebSocket.instances[0];
    useGameStore.setState({ gameId: 'rest-game', isPaused: false });
    const before = useGameStore.getState();

    const ignored = [
      { type: 'game_state', state: { game_id: 'leaked-game' } },
      { type: 'phase_change', phase: 'vote_casting', round_number: 2, state: {} },
      { type: 'speech', speech: { player_seat: 1, text: 'duplicate' } },
      { type: 'vote_cast', vote: { voter_seat: 1, target_seat: 2 } },
      { type: 'player_died', death: { player_seat: 2, cause: 'werewolf' } },
      { type: 'game_over', win_result: { winning_camp: 'werewolf' }, state: {} },
      { type: 'role_init', role: 'seer', camp: 'good' },
      { type: 'future_event', payload: { thought: 'secret' } },
    ];
    act(() => {
      ignored.forEach((message) => socket.emit(message));
      socket.onmessage?.({ data: '{not-json' });
    });

    const after = useGameStore.getState();
    expect(after.gameId).toBe(before.gameId);
    expect(after.phase).toBe(before.phase);
    expect(after.players).toEqual(before.players);
    expect(after.speeches).toEqual(before.speeches);
    expect(after.votes).toEqual(before.votes);
    expect(after.deathHistory).toEqual(before.deathHistory);
    expect(after.winResult).toEqual(before.winResult);
    expect(after.isPaused).toBe(false);
    expect(errorSpy).toHaveBeenCalledOnce();
  });

  it('clears handlers, closes, marks disconnected, and sends controls while a socket exists', () => {
    const { result, unmount } = renderHook(() => useWebSocket());
    expect(() => {
      act(() => {
        result.current.sendSpeed(2);
        result.current.sendPause();
        result.current.sendResume();
      });
    }).not.toThrow();

    act(() => result.current.connect('game-1'));
    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.onopen?.();
      result.current.sendSpeed(1.5);
      result.current.sendPause();
      result.current.sendResume();
    });
    useGameStore.setState({ gameId: 'rest-game' });
    act(() => result.current.disconnect());

    expect(socket.send.mock.calls.map(([payload]) => payload)).toEqual([
      JSON.stringify({ type: 'set_speed', delay_seconds: 1.5 }),
      JSON.stringify({ type: 'pause' }),
      JSON.stringify({ type: 'resume' }),
    ]);
    expect(socket.onopen).toBeNull();
    expect(socket.onclose).toBeNull();
    expect(socket.onerror).toBeNull();
    expect(socket.onmessage).toBeNull();
    expect(socket.close).toHaveBeenCalledOnce();
    expect(useGameStore.getState().connected).toBe(false);
    expect(useGameStore.getState().gameId).toBe('rest-game');

    act(() => result.current.connect('game-2'));
    const unmountedSocket = MockWebSocket.instances[1];
    unmount();
    expect(unmountedSocket.onopen).toBeNull();
    expect(unmountedSocket.onclose).toBeNull();
    expect(unmountedSocket.onerror).toBeNull();
    expect(unmountedSocket.onmessage).toBeNull();
    expect(unmountedSocket.close).toHaveBeenCalledOnce();
    expect(useGameStore.getState().connected).toBe(false);
  });
});
