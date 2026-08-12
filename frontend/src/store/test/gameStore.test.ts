import { afterEach, describe, expect, it, vi } from 'vitest';

import { isAtTimelineEnd, useGameStore } from '../gameStore';
import type { GameLogs, PublicGameState, PublicPlayerState } from '../types';

const currentPlayers: Record<number, PublicPlayerState> = {
  1: { seat_number: 1, is_alive: true, is_sheriff: false },
  2: { seat_number: 2, is_alive: false, is_sheriff: true },
};

afterEach(() => {
  useGameStore.getState().reset();
  vi.useRealTimers();
});

describe('public replay state', () => {
  it('builds a neutral baseline from detail seats and applies deaths only when replayed', () => {
    const logs: GameLogs = {
      game_id: 'game-1',
      events: [
        { event_type: 'phase', payload: { phase: 'night', round_number: 1 } },
        {
          event_type: 'death',
          payload: { player_seat: 2, cause: 'wolf_kill', round_number: 1 },
        },
      ],
    };

    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs(logs);
    useGameStore.getState().seekTo(0);

    expect(useGameStore.getState().players).toEqual({
      1: { seat_number: 1, is_alive: true, is_sheriff: false },
      2: { seat_number: 2, is_alive: true, is_sheriff: false },
    });

    useGameStore.getState().seekTo(1);

    expect(useGameStore.getState().players[2]).toEqual({
      seat_number: 2,
      is_alive: false,
      is_sheriff: false,
    });
  });

  it('follows all newly appended events when live playback was at the tail', () => {
    const originalLogs: GameLogs = {
      game_id: 'game-1',
      events: [
        { event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
        {
          event_type: 'speech',
          payload: { player_seat: 1, text: '发言', round_number: 1 },
        },
      ],
    };
    const extendedLogs: GameLogs = {
      ...originalLogs,
      events: [
        ...originalLogs.events,
        {
          event_type: 'death',
          payload: { player_seat: 2, cause: 'exile', round_number: 1 },
        },
        {
          event_type: 'winner',
          payload: { winning_camp: 'good', reason: 'all_wolves_dead' },
        },
      ],
    };

    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs(originalLogs);
    useGameStore.getState().seekTo(1);
    useGameStore.getState().setPaused(false);

    useGameStore.getState().mergeLogs(extendedLogs);

    expect(useGameStore.getState().timelineIndex).toBe(3);
    expect(useGameStore.getState().players[2].is_alive).toBe(false);
    expect(useGameStore.getState().phase).toBe('game_over');
    expect(useGameStore.getState().winResult).toEqual({
      winning_camp: 'good',
      reason: 'all_wolves_dead',
    });

    useGameStore.getState().mergeLogs(extendedLogs);

    expect(useGameStore.getState().timelineIndex).toBe(3);
    expect(useGameStore.getState().phase).toBe('game_over');
  });

  it('keeps a paused historical position when new events arrive', () => {
    const originalLogs: GameLogs = {
      game_id: 'game-1',
      events: [
        { event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
        {
          event_type: 'speech',
          payload: { player_seat: 1, text: '发言', round_number: 1 },
        },
      ],
    };
    const extendedLogs: GameLogs = {
      ...originalLogs,
      events: [
        ...originalLogs.events,
        {
          event_type: 'death',
          payload: { player_seat: 2, cause: 'exile', round_number: 1 },
        },
      ],
    };

    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs(originalLogs);
    useGameStore.getState().seekTo(0);
    useGameStore.getState().setPaused(true);

    useGameStore.getState().mergeLogs(extendedLogs);

    expect(useGameStore.getState().timeline).toHaveLength(3);
    expect(useGameStore.getState().timelineIndex).toBe(0);
    expect(useGameStore.getState().isPaused).toBe(true);
    expect(useGameStore.getState().players[2].is_alive).toBe(true);
  });

  it('derives every public event type without requiring a matching player', () => {
    const logs: GameLogs = {
      game_id: 'game-1',
      events: [
        { event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
        {
          event_type: 'speech',
          payload: { player_seat: 1, text: '发言', round_number: 2 },
        },
        {
          event_type: 'vote',
          payload: { voter_seat: 1, target_seat: 2, round_number: 3 },
        },
        {
          event_type: 'vote_result',
          payload: { exiled_seat: null, round_number: 4 },
        },
        {
          event_type: 'vote_result',
          payload: { exiled_seat: 2, round_number: 5 },
        },
        {
          event_type: 'vote_result',
          payload: { exiled_seat: 99, round_number: 6 },
        },
        {
          event_type: 'death',
          payload: { player_seat: 99, cause: 'poison', round_number: 7 },
        },
        {
          event_type: 'winner',
          payload: { winning_camp: 'werewolf', reason: 'all_gods_dead' },
        },
      ],
    };

    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs(logs);
    useGameStore.getState().seekTo(99);

    const state = useGameStore.getState();
    expect(state.timelineIndex).toBe(7);
    expect(state.speeches).toHaveLength(1);
    expect(state.votes).toHaveLength(1);
    expect(state.deathHistory).toHaveLength(3);
    expect(state.players[2].is_alive).toBe(false);
    expect(state.roundNumber).toBe(7);
    expect(state.phase).toBe('game_over');
    expect(state.currentSpeaker).toBeNull();
  });

  it('updates direct public websocket state and controls', () => {
    const state: PublicGameState = {
      game_id: 'game-1',
      phase: 'speech',
      round_number: 2,
      players: currentPlayers,
      sheriff: 2,
      speeches: [{ player_seat: 1, text: '已有发言', round_number: 2 }],
      death_history: [{ player_seat: 2, cause: 'exile', round_number: 2 }],
      win_result: null,
    };

    useGameStore.getState().setGameState(state);
    useGameStore.getState().setPhase('vote_casting', 3);
    useGameStore.getState().addSpeech({ player_seat: 1, text: '新发言', round_number: 3 });
    useGameStore.getState().addVote({ voter_seat: 2, target_seat: 1, round_number: 3 });
    useGameStore.getState().addDeath({ player_seat: 1, cause: 'poison', round_number: 3 });
    useGameStore.getState().addDeath({ player_seat: 99, cause: 'hunter_shot', round_number: 3 });
    useGameStore.getState().setConnected(true);
    useGameStore.getState().setNightSubstep({ substep: 'resolve', roundNumber: 4 });
    useGameStore.getState().setCurrentSpeaker(2);
    useGameStore.getState().setWinResult({
      winning_camp: 'good',
      reason: 'all_wolves_dead',
    });
    useGameStore.getState().toggleHistory();
    useGameStore.getState().dismissWinOverlay();

    const result = useGameStore.getState();
    expect(result.gameId).toBe('game-1');
    expect(result.initialPlayers).not.toBe(state.players);
    expect(result.players[1].is_alive).toBe(false);
    expect(result.speeches).toHaveLength(2);
    expect(result.votes).toHaveLength(1);
    expect(result.deathHistory).toHaveLength(3);
    expect(result.connected).toBe(true);
    expect(result.roundNumber).toBe(4);
    expect(result.currentSpeaker).toBe(2);
    expect(result.phase).toBe('game_over');
    expect(result.showHistory).toBe(true);
    expect(result.showWinOverlay).toBe(false);
    expect(result.winOverlayDismissed).toBe(true);
  });

  it('handles replay navigation, duplicate logs, and overlay dismissal', () => {
    const logs: GameLogs = {
      game_id: 'game-1',
      events: [
        { event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
        {
          event_type: 'winner',
          payload: { winning_camp: 'good', reason: 'all_wolves_dead' },
        },
      ],
    };

    expect(isAtTimelineEnd([], -1)).toBe(true);
    expect(isAtTimelineEnd(logs.events, 0)).toBe(false);
    useGameStore.getState().seekTo(0);
    useGameStore.getState().stepForward();
    useGameStore.getState().stepBack();

    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs(logs);
    useGameStore.getState().seekTo(-5);
    expect(useGameStore.getState().timelineIndex).toBe(0);
    useGameStore.getState().stepForward();
    expect(useGameStore.getState().showWinOverlay).toBe(true);
    useGameStore.getState().dismissWinOverlay();
    useGameStore.getState().seekTo(1);
    expect(useGameStore.getState().showWinOverlay).toBe(false);
    useGameStore.getState().stepForward();
    expect(useGameStore.getState().isPlaying).toBe(false);
    useGameStore.getState().stepBack();
    expect(useGameStore.getState().timelineIndex).toBe(0);
    useGameStore.getState().stepBack();
    expect(useGameStore.getState().timelineIndex).toBe(0);

    useGameStore.getState().mergeLogs(logs);
    expect(useGameStore.getState().timeline).toHaveLength(2);
  });

  it('plays, pauses, advances on timers, and restarts timers when speed changes', () => {
    vi.useFakeTimers();
    const logs: GameLogs = {
      game_id: 'game-1',
      events: [
        { event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
        {
          event_type: 'speech',
          payload: { player_seat: 1, text: '发言', round_number: 1 },
        },
      ],
    };

    useGameStore.getState().play();
    expect(useGameStore.getState().isPlaying).toBe(false);

    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs(logs);
    useGameStore.getState().setSpeed(2);
    useGameStore.getState().play();
    expect(useGameStore.getState().isPlaying).toBe(true);
    vi.advanceTimersByTime(1500);
    expect(useGameStore.getState().timelineIndex).toBe(0);

    useGameStore.getState().setSpeed(3);
    vi.advanceTimersByTime(1000);
    expect(useGameStore.getState().timelineIndex).toBe(1);
    vi.advanceTimersByTime(1000);
    expect(useGameStore.getState().isPlaying).toBe(false);

    useGameStore.getState().play();
    expect(useGameStore.getState().timelineIndex).toBe(0);
    useGameStore.getState().pause();
    expect(useGameStore.getState().isPlaying).toBe(false);
  });

  it('does not follow new events when paused at the old tail', () => {
    const originalLogs: GameLogs = {
      game_id: 'game-1',
      events: [{ event_type: 'phase', payload: { phase: 'speech', round_number: 1 } }],
    };
    const extendedLogs: GameLogs = {
      ...originalLogs,
      events: [
        ...originalLogs.events,
        {
          event_type: 'speech',
          payload: { player_seat: 1, text: '新增', round_number: 1 },
        },
      ],
    };

    useGameStore.getState().loadLogs(originalLogs);
    useGameStore.getState().seekTo(0);
    useGameStore.getState().setPaused(true);
    useGameStore.getState().mergeLogs(extendedLogs);

    expect(useGameStore.getState().timelineIndex).toBe(0);
  });
});
