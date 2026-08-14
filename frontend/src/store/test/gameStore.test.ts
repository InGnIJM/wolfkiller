import { afterEach, describe, expect, it, vi } from 'vitest';

import { isAtTimelineEnd, useGameStore } from '../gameStore';
import type {
  GameLogs,
  PublicGameState,
  PublicPlayerState,
} from '../types';

const replayEventMeta = { timestamp: '2026-08-13T00:00:00Z' } as const;

const currentPlayers: Record<number, PublicPlayerState> = {
  1: { seat_number: 1, is_alive: true, is_sheriff: false },
  2: { seat_number: 2, is_alive: false, is_sheriff: true },
};

afterEach(() => {
  useGameStore.getState().reset();
  vi.useRealTimers();
});

describe('public replay state', () => {
  it('shows the current sheriff snapshot when no replay timeline exists', () => {
    useGameStore.getState().initPlayersFromDetail(currentPlayers);

    expect(useGameStore.getState().players).toEqual({
      1: { seat_number: 1, is_alive: true, is_sheriff: false },
      2: { seat_number: 2, is_alive: true, is_sheriff: true },
    });
    expect(useGameStore.getState().initialPlayers).toEqual({
      1: { seat_number: 1, is_alive: true, is_sheriff: false },
      2: { seat_number: 2, is_alive: true, is_sheriff: false },
    });
  });

  it('shows the sheriff snapshot only at the current replay tail', () => {
    const logs: GameLogs = {
      game_id: 'game-1',
      events: [
        { ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
        {
          ...replayEventMeta,
          event_type: 'speech',
          payload: { player_seat: 1, text: 'tail', round_number: 1 },
        },
      ],
    };

    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs(logs);
    useGameStore.getState().seekTo(1);
    expect(useGameStore.getState().players[2].is_sheriff).toBe(true);

    useGameStore.getState().seekTo(0);
    expect(useGameStore.getState().players[2].is_sheriff).toBe(false);

    useGameStore.getState().seekTo(1);
    expect(useGameStore.getState().players[2].is_sheriff).toBe(true);
  });

  it('keeps snapshot updates out of history and applies them at the tail', () => {
    const logs: GameLogs = {
      game_id: 'game-1',
      events: [
        { ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
        {
          ...replayEventMeta,
          event_type: 'speech',
          payload: { player_seat: 1, text: 'tail', round_number: 1 },
        },
      ],
    };
    const updatedPlayers: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: true },
      3: { seat_number: 3, is_alive: true, is_sheriff: false },
    };

    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs(logs);
    useGameStore.getState().seekTo(0);
    useGameStore.getState().initPlayersFromDetail(updatedPlayers);

    expect(useGameStore.getState().players).toEqual({
      1: { seat_number: 1, is_alive: true, is_sheriff: false },
      3: { seat_number: 3, is_alive: true, is_sheriff: false },
    });

    useGameStore.getState().seekTo(1);

    expect(useGameStore.getState().players).toEqual({
      1: { seat_number: 1, is_alive: true, is_sheriff: true },
      3: { seat_number: 3, is_alive: true, is_sheriff: false },
    });
  });

  it('shows the current sheriff snapshot at a game-over tail', () => {
    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs({
      game_id: 'game-1',
      events: [
        {
          ...replayEventMeta,
          event_type: 'winner',
          payload: { winning_camp: 'good', reason: 'all_wolves_dead' },
        },
      ],
    });
    useGameStore.getState().seekTo(0);

    expect(useGameStore.getState().phase).toBe('game_over');
    expect(useGameStore.getState().players[2].is_sheriff).toBe(true);
  });

  it('builds a neutral baseline from detail seats and applies deaths only when replayed', () => {
    const logs: GameLogs = {
      game_id: 'game-1',
      events: [
        { ...replayEventMeta, event_type: 'phase', payload: { phase: 'night', round_number: 1 } },
        {
          ...replayEventMeta,
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
      is_sheriff: true,
    });
  });

  it('follows all newly appended events when live playback was at the tail', () => {
    const originalLogs: GameLogs = {
      game_id: 'game-1',
      events: [
        { ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
        {
          ...replayEventMeta,
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
          ...replayEventMeta,
          event_type: 'death',
          payload: { player_seat: 2, cause: 'exile', round_number: 1 },
        },
        {
          ...replayEventMeta,
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
        { ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
        {
          ...replayEventMeta,
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
          ...replayEventMeta,
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

  it('replays the complete reordered timeline when following the live tail', () => {
    const phaseEvent = {
      ...replayEventMeta,
      event_type: 'phase' as const,
      payload: { phase: 'speech' as const, round_number: 1 },
    };
    const deathEvent = {
      ...replayEventMeta,
      event_type: 'death' as const,
      payload: { player_seat: 2, cause: 'exile' as const, round_number: 1 },
    };
    const winnerEvent = {
      ...replayEventMeta,
      event_type: 'winner' as const,
      payload: { winning_camp: 'good' as const, reason: 'all_wolves_dead' as const },
    };

    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs({ game_id: 'game-1', events: [phaseEvent, winnerEvent] });
    useGameStore.getState().seekTo(1);
    useGameStore.getState().setPaused(false);

    useGameStore.getState().mergeLogs({
      game_id: 'game-1',
      events: [phaseEvent, deathEvent, winnerEvent],
    });

    const state = useGameStore.getState();
    expect(state.timelineIndex).toBe(2);
    expect(state.timeline).toEqual([phaseEvent, deathEvent, winnerEvent]);
    expect(state.players[2].is_alive).toBe(false);
    expect(state.deathHistory).toEqual([deathEvent.payload]);
    expect(state.phase).toBe('game_over');
  });

  it('keeps the current event as a semantic anchor when earlier logs are inserted', () => {
    const phaseEvent = {
      ...replayEventMeta,
      event_type: 'phase' as const,
      payload: { phase: 'speech' as const, round_number: 1 },
    };
    const insertedSpeech = {
      ...replayEventMeta,
      event_type: 'speech' as const,
      payload: { player_seat: 2, text: 'inserted', round_number: 1 },
    };
    const anchoredSpeech = {
      ...replayEventMeta,
      event_type: 'speech' as const,
      payload: { player_seat: 1, text: 'anchor', round_number: 1 },
    };
    const deathEvent = {
      ...replayEventMeta,
      event_type: 'death' as const,
      payload: { player_seat: 2, cause: 'exile' as const, round_number: 1 },
    };

    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs({
      game_id: 'game-1',
      events: [phaseEvent, anchoredSpeech, deathEvent],
    });
    useGameStore.getState().seekTo(1);
    useGameStore.getState().setPaused(true);

    useGameStore.getState().mergeLogs({
      game_id: 'game-1',
      events: [phaseEvent, insertedSpeech, anchoredSpeech, deathEvent],
    });

    const state = useGameStore.getState();
    expect(state.timelineIndex).toBe(2);
    expect(state.timeline[state.timelineIndex]).toEqual(anchoredSpeech);
    expect(state.speeches).toEqual([insertedSpeech.payload, anchoredSpeech.payload]);
    expect(state.currentSpeaker).toBe(1);
    expect(state.players[2].is_alive).toBe(true);
  });

  it('tracks the same duplicate occurrence when reconciling a historical anchor', () => {
    const phaseEvent = {
      ...replayEventMeta,
      event_type: 'phase' as const,
      payload: { phase: 'speech' as const, round_number: 1 },
    };
    const repeatedSpeech = {
      ...replayEventMeta,
      event_type: 'speech' as const,
      payload: { player_seat: 1, text: 'repeat', round_number: 1 },
    };
    const insertedSpeech = {
      ...replayEventMeta,
      event_type: 'speech' as const,
      payload: { player_seat: 2, text: 'inserted', round_number: 1 },
    };

    useGameStore.getState().loadLogs({
      game_id: 'game-1',
      events: [phaseEvent, repeatedSpeech, repeatedSpeech],
    });
    useGameStore.getState().seekTo(2);
    useGameStore.getState().setPaused(true);

    useGameStore.getState().mergeLogs({
      game_id: 'game-1',
      events: [phaseEvent, insertedSpeech, repeatedSpeech, repeatedSpeech],
    });

    expect(useGameStore.getState().timelineIndex).toBe(3);
    expect(useGameStore.getState().speeches).toEqual([
      insertedSpeech.payload,
      repeatedSpeech.payload,
      repeatedSpeech.payload,
    ]);
  });

  it('keeps equal payloads at different timestamps as distinct replay events', () => {
    const earlierSpeech = {
      timestamp: '2026-08-13T00:00:01Z' as const,
      event_type: 'speech' as const,
      payload: { player_seat: 1, text: 'same payload', round_number: 1 },
    };
    const anchoredSpeech = {
      timestamp: '2026-08-13T00:00:02Z' as const,
      event_type: 'speech' as const,
      payload: { player_seat: 1, text: 'same payload', round_number: 1 },
    };

    useGameStore.getState().loadLogs({ game_id: 'game-1', events: [anchoredSpeech] });
    useGameStore.getState().seekTo(0);
    useGameStore.getState().setPaused(true);
    useGameStore.getState().mergeLogs({
      game_id: 'game-1',
      events: [earlierSpeech, anchoredSpeech],
    });

    expect(useGameStore.getState().timeline).toEqual([earlierSpeech, anchoredSpeech]);
    expect(useGameStore.getState().timelineIndex).toBe(1);
  });

  it('clamps a missing historical anchor and supports replacement with an empty log', () => {
    const phaseEvent = {
      ...replayEventMeta,
      event_type: 'phase' as const,
      payload: { phase: 'speech' as const, round_number: 1 },
    };
    const speechEvent = {
      ...replayEventMeta,
      event_type: 'speech' as const,
      payload: { player_seat: 1, text: 'removed', round_number: 1 },
    };

    useGameStore.getState().loadLogs({
      game_id: 'game-1',
      events: [phaseEvent, speechEvent],
    });
    useGameStore.getState().seekTo(1);
    useGameStore.getState().setPaused(true);
    useGameStore.getState().mergeLogs({ game_id: 'game-1', events: [phaseEvent] });

    expect(useGameStore.getState().timelineIndex).toBe(0);
    expect(useGameStore.getState().speeches).toEqual([]);
    expect(useGameStore.getState().phase).toBe('speech');

    useGameStore.getState().mergeLogs({ game_id: 'game-1', events: [] });

    expect(useGameStore.getState().timeline).toEqual([]);
    expect(useGameStore.getState().timelineIndex).toBe(-1);
    expect(useGameStore.getState().phase).toBe('waiting');
  });

  it('reconciles same-length replacements and recomputes derived state', () => {
    const phaseEvent = {
      ...replayEventMeta,
      event_type: 'phase' as const,
      payload: { phase: 'speech' as const, round_number: 1 },
    };
    const oldSpeech = {
      ...replayEventMeta,
      event_type: 'speech' as const,
      payload: { player_seat: 1, text: 'old', round_number: 1 },
    };
    const replacementSpeech = {
      ...replayEventMeta,
      event_type: 'speech' as const,
      payload: { player_seat: 2, text: 'replacement', round_number: 2 },
    };

    useGameStore.getState().loadLogs({
      game_id: 'game-1',
      events: [phaseEvent, oldSpeech],
    });
    useGameStore.getState().seekTo(1);
    useGameStore.getState().setPaused(false);

    useGameStore.getState().mergeLogs({
      game_id: 'game-1',
      events: [phaseEvent, replacementSpeech],
    });

    const state = useGameStore.getState();
    expect(state.timeline).toEqual([phaseEvent, replacementSpeech]);
    expect(state.timelineIndex).toBe(1);
    expect(state.speeches).toEqual([replacementSpeech.payload]);
    expect(state.currentSpeaker).toBe(2);
    expect(state.roundNumber).toBe(2);
  });

  it('keeps state and timeline references stable for identical logs', () => {
    const logs: GameLogs = {
      game_id: 'game-1',
      events: [{ ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } }],
    };

    useGameStore.getState().loadLogs(logs);
    useGameStore.getState().seekTo(0);
    const stateBefore = useGameStore.getState();
    const timelineBefore = stateBefore.timeline;

    useGameStore.getState().mergeLogs(logs);

    expect(useGameStore.getState()).toBe(stateBefore);
    expect(useGameStore.getState().timeline).toBe(timelineBefore);
  });

  it('derives every public event type without requiring a matching player', () => {
    const logs: GameLogs = {
      game_id: 'game-1',
      events: [
        { ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
        {
          ...replayEventMeta,
          event_type: 'speech',
          payload: { player_seat: 1, text: '发言', round_number: 2 },
        },
        {
          ...replayEventMeta,
          event_type: 'vote',
          payload: { voter_seat: 1, target_seat: 2, round_number: 3 },
        },
        {
          ...replayEventMeta,
          event_type: 'vote_result',
          payload: { exiled_seat: null, round_number: 4 },
        },
        {
          ...replayEventMeta,
          event_type: 'vote_result',
          payload: { exiled_seat: 2, round_number: 5 },
        },
        {
          ...replayEventMeta,
          event_type: 'vote_result',
          payload: { exiled_seat: 99, round_number: 6 },
        },
        {
          ...replayEventMeta,
          event_type: 'death',
          payload: { player_seat: 99, cause: 'poison', round_number: 7 },
        },
        {
          ...replayEventMeta,
          event_type: 'night_action',
          payload: { action_type: 'seer_check', target_seat: 1, round_number: 8, result: 'good' },
        },
        {
          ...replayEventMeta,
          event_type: 'winner',
          payload: { winning_camp: 'werewolf', reason: 'all_gods_dead' },
        },
      ],
    };

    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs(logs);
    useGameStore.getState().seekTo(99);

    const state = useGameStore.getState();
    expect(state.timelineIndex).toBe(8);
    expect(state.speeches).toHaveLength(1);
    expect(state.votes).toHaveLength(1);
    expect(state.deathHistory).toHaveLength(3);
    expect(state.nightActions).toEqual([{
      action_type: 'seer_check', target_seat: 1, round_number: 8, result: 'good',
    }]);
    expect(state.players[2].is_alive).toBe(false);
    expect(state.roundNumber).toBe(8);
    expect(state.phase).toBe('game_over');
    expect(state.currentSpeaker).toBeNull();
  });

  it('advances the round for staged night thought and wolf chat events', () => {
    const logs: GameLogs = {
      game_id: 'game-1',
      events: [
        { ...replayEventMeta, event_type: 'phase', payload: { phase: 'night', round_number: 1 } },
        {
          ...replayEventMeta,
          event_type: 'seer_thought',
          payload: { round_number: 2, seat: 3, text: '查验1号' },
        },
        {
          ...replayEventMeta,
          event_type: 'wolf_chat_message',
          payload: { round_number: 2, seat: 1, text: '我觉得4号是神' },
        },
      ],
    };

    useGameStore.getState().initPlayersFromDetail(currentPlayers);
    useGameStore.getState().loadLogs(logs);
    useGameStore.getState().seekTo(2);

    expect(useGameStore.getState().roundNumber).toBe(2);
    expect(useGameStore.getState().phase).toBe('night');
  });

  it('keeps night phase and round across staged night events', () => {
    const logs: GameLogs = {
      game_id: 'g',
      events: [
        { ...replayEventMeta, event_type: 'phase', payload: { phase: 'night', round_number: 1 } },
        { ...replayEventMeta, event_type: 'narration', payload: { round_number: 1, title: '天黑请闭眼', text: '狼人请睁眼' } },
        { ...replayEventMeta, event_type: 'wolf_chat_message', payload: { round_number: 1, seat: 1, text: '我怀疑2号' } },
        { ...replayEventMeta, event_type: 'wolf_vote', payload: { round_number: 1, seat: 1, target_seat: 2, reasoning: '像神' } },
        { ...replayEventMeta, event_type: 'witch_thought', payload: { round_number: 1, seat: 5, text: '考虑救人' } },
        { ...replayEventMeta, event_type: 'seer_thought', payload: { round_number: 1, seat: 6, text: '查验2号' } },
      ],
    };
    useGameStore.getState().loadLogs(logs);
    useGameStore.getState().seekTo(logs.events.length - 1);
    expect(useGameStore.getState().phase).toBe('night');
    expect(useGameStore.getState().roundNumber).toBe(1);
    expect(useGameStore.getState().currentSpeaker).toBeNull();
  });

  it('keeps viewer role and camp on initial players', () => {
    useGameStore.getState().initPlayersFromDetail({
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-werewolf', camp: 'werewolf' },
    });
    expect(useGameStore.getState().initialPlayers[1].role).toBe('wolf-killer-werewolf');
    expect(useGameStore.getState().initialPlayers[1].camp).toBe('werewolf');
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
        { ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
        {
          ...replayEventMeta,
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
        { ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
        {
          ...replayEventMeta,
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
      events: [{ ...replayEventMeta, event_type: 'phase', payload: { phase: 'speech', round_number: 1 } }],
    };
    const extendedLogs: GameLogs = {
      ...originalLogs,
      events: [
        ...originalLogs.events,
        {
          ...replayEventMeta,
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
