import { afterEach, describe, expect, it } from 'vitest';

import { useGameStore } from '../gameStore';
import type { AudienceEvent, AudienceSnapshot, PublicGameState } from '../types';

const state = (gameId: string): PublicGameState => ({
  game_id: gameId,
  phase: 'waiting',
  round_number: 0,
  players: { 1: { seat_number: 1, is_alive: true, is_sheriff: false } },
  sheriff: null,
  speeches: [],
  death_history: [],
  win_result: null,
  execution_status: 'running',
});

const snapshot = (gameId: string, seq = 0): AudienceSnapshot => ({
  game_id: gameId,
  seq,
  projection_version: 1,
  state: state(gameId),
});

function event(seq: number, eventType: AudienceEvent['event_type'], payload: Record<string, unknown>): AudienceEvent {
  return {
    seq,
    event_id: `event-${seq}`,
    event_type: eventType,
    payload,
    schema_version: 1,
    timestamp: `2026-09-06T00:00:0${seq}Z`,
  };
}

function page(gameId: string, events: AudienceEvent[]) {
  return {
    game_id: gameId,
    after_seq: 0,
    last_seq: Math.max(0, ...events.map((item) => item.seq)),
    events,
    caught_up: true,
  };
}

afterEach(() => useGameStore.getState().reset());

describe('incremental audience synchronization', () => {
  it('waits for the durable death event instead of applying an incremental vote result twice', () => {
    const store = useGameStore.getState();
    const initial = snapshot('game-1');
    initial.state.players[2] = { seat_number: 2, is_alive: true, is_sheriff: false };
    store.loadAudienceSnapshot(initial);
    store.loadAudienceHistory('game-1', [
      event(1, 'vote_result', { round_number: 1, exiled_seat: 2, counts: { 2: 1 } }),
    ]);

    expect(useGameStore.getState().players[2].is_alive).toBe(true);
    expect(useGameStore.getState().deathHistory).toHaveLength(0);

    store.mergeAudienceEvents(page('game-1', [
      event(2, 'death', { player_seat: 2, cause: 'exile', round_number: 1 }),
    ]));
    expect(useGameStore.getState().players[2].is_alive).toBe(false);
    expect(useGameStore.getState().deathHistory).toEqual([
      { player_seat: 2, cause: 'exile', round_number: 1 },
    ]);
  });

  it('deduplicates events and holds out-of-order events until a gap is filled', () => {
    const store = useGameStore.getState();
    store.loadAudienceSnapshot(snapshot('game-1'));
    store.mergeAudienceEvents(page('game-1', [
      event(2, 'speech', { player_seat: 1, text: 'second', round_number: 1 }),
    ]));
    expect(useGameStore.getState().audienceCursor).toBe(0);
    expect(useGameStore.getState().timeline).toHaveLength(0);

    store.mergeAudienceEvents(page('game-1', [
      event(1, 'phase', { phase: 'speech', round_number: 1 }),
      event(2, 'speech', { player_seat: 1, text: 'second', round_number: 1 }),
    ]));
    expect(useGameStore.getState().audienceCursor).toBe(2);
    expect(useGameStore.getState().timeline.map((item) => item.seq)).toEqual([1, 2]);
    expect(useGameStore.getState().speeches).toHaveLength(1);

    store.mergeAudienceEvents(page('game-1', [
      event(2, 'speech', { player_seat: 1, text: 'second', round_number: 1 }),
    ]));
    expect(useGameStore.getState().timeline).toHaveLength(2);
  });

  it('keeps the historical view stable while new contiguous events advance the stream cursor', () => {
    const store = useGameStore.getState();
    store.loadAudienceSnapshot(snapshot('game-1', 2));
    store.loadAudienceHistory('game-1', [
      event(1, 'phase', { phase: 'speech', round_number: 1 }),
      event(2, 'speech', { player_seat: 1, text: 'old', round_number: 1 }),
    ]);
    store.seekTo(0);
    store.mergeAudienceEvents(page('game-1', [
      event(4, 'speech', { player_seat: 1, text: 'fourth', round_number: 1 }),
      event(3, 'speech', { player_seat: 1, text: 'third', round_number: 1 }),
    ]));

    expect(useGameStore.getState().audienceCursor).toBe(4);
    expect(useGameStore.getState().timelineIndex).toBe(0);
    expect(useGameStore.getState().speeches).toHaveLength(0);
    store.goLive();
    expect(useGameStore.getState().timelineIndex).toBe(3);
    expect(useGameStore.getState().speeches.map((item) => item.text)).toEqual(['old', 'third', 'fourth']);
  });

  it('ignores late pages from the previous game and applies execution state explicitly', () => {
    const store = useGameStore.getState();
    store.loadAudienceSnapshot(snapshot('game-1'));
    store.loadAudienceSnapshot(snapshot('game-2'));
    store.mergeAudienceEvents(page('game-1', [
      event(1, 'phase', { phase: 'night', round_number: 1 }),
    ]));
    expect(useGameStore.getState().gameId).toBe('game-2');
    expect(useGameStore.getState().audienceCursor).toBe(0);

    store.mergeAudienceEvents(page('game-2', [
      event(1, 'execution_state', {
        execution_status: 'interrupted', recoverable: true, recovery_block_code: null,
      }),
    ]));
    expect(useGameStore.getState().executionStatus).toBe('interrupted');
    expect(useGameStore.getState().recoverable).toBe(true);
  });

  it('validates stream sequence conflicts and keeps a missing history prefix pending', () => {
    const store = useGameStore.getState();
    store.loadAudienceSnapshot(snapshot('game-1'));
    store.loadAudienceHistory('wrong-game', []);
    store.loadAudienceHistory('game-1', [
      event(0, 'phase', { phase: 'night', round_number: 1 }),
      event(2, 'speech', { player_seat: 1, text: 'gap', round_number: 1 }),
    ]);
    expect(useGameStore.getState().audienceCursor).toBe(0);
    expect(useGameStore.getState().pendingAudienceEvents[2].event_id).toBe('event-2');

    store.mergeAudienceEvents({
      ...page('game-1', [{
        ...event(2, 'speech', { player_seat: 1, text: 'conflict', round_number: 1 }),
        event_id: 'conflicting-event',
      }]),
      high_watermark: 5,
    });
    expect(useGameStore.getState().streamError).toContain('存在冲突');
    expect(useGameStore.getState().audienceHighWatermark).toBe(5);

    store.mergeAudienceEvents(page('game-1', [
      event(0, 'phase', { phase: 'night', round_number: 1 }),
      event(1, 'phase', { phase: 'speech', round_number: 1 }),
    ]));
    expect(useGameStore.getState().audienceCursor).toBe(2);
    expect(useGameStore.getState().streamError).toContain('无效');

    store.mergeAudienceEvents(page('game-1', [event(1, 'phase', {
      phase: 'night', round_number: 1,
    })]));
    expect(useGameStore.getState().audienceCursor).toBe(2);

    store.reset();
    store.goLive();
    expect(useGameStore.getState().isFollowingLive).toBe(true);
  });

  it('derives initialization, reveal, diagnostic and execution events from a full prefix', () => {
    const store = useGameStore.getState();
    const initial = snapshot('game-1');
    initial.state.speeches = undefined as never;
    initial.state.death_history = undefined as never;
    initial.state.execution_status = undefined;
    initial.state.recoverable = undefined;
    initial.state.recovery_block_code = undefined;
    store.loadAudienceSnapshot(initial);
    const events: AudienceEvent[] = [
      {
        ...event(1, 'game_initialized', {
          players: [
            { seat_number: 1, is_alive: true, is_sheriff: false },
            null,
            { seat_number: 'bad' },
          ],
        }),
        created_at: '2026-09-06T00:00:01Z',
        timestamp: undefined,
      },
      event(2, 'game_initialized', {
        players: {
          1: { seat_number: 1, is_alive: true, is_sheriff: false },
          bad: null,
        },
      }),
      event(3, 'player_revealed', {
        seat_number: 1, role: 'wolf-killer-villager', camp: 'good',
      }),
      event(4, 'player_revealed', {
        seat_number: 99, role: 'wolf-killer-werewolf', camp: 'werewolf',
      }),
      event(5, 'technical_abstain', {
        voter_seat: 1, round_number: 2, failure_code: 'timeout',
      }),
      event(6, 'narration', { round_number: 2, title: '天亮', text: '继续' }),
      event(7, 'night_action', {
        action_type: 'seer_check', target_seat: 1, round_number: 2, result: 'good',
      }),
      event(8, 'execution_state', {
        execution_status: 'paused', recoverable: true, recovery_block_code: null,
      }),
      {
        ...event(9, 'execution_state', {
          execution_status: 1, recoverable: 'yes', recovery_block_code: 2,
        }),
        timestamp: undefined,
      },
    ];
    store.loadAudienceHistory('game-1', events);

    const result = useGameStore.getState();
    expect(result.players[1]).toMatchObject({
      role: 'wolf-killer-villager', camp: 'good', revealed_role: 'wolf-killer-villager',
    });
    expect(result.roundNumber).toBe(2);
    expect(result.nightActions).toHaveLength(1);
    expect(result.executionStatus).toBe('paused');
    expect(result.recoverable).toBe(true);
    expect(result.recoveryBlockCode).toBeNull();
    expect(result.timeline[0].timestamp).toBe('2026-09-06T00:00:01Z');
    expect(result.timeline[8].timestamp).toBe('1970-01-01T00:00:00.000Z');
  });

  it('handles empty initialization payloads, recovery block codes and winner overlays', () => {
    const store = useGameStore.getState();
    store.loadAudienceSnapshot(snapshot('game-1'));
    store.loadAudienceHistory('game-1', [
      event(1, 'game_initialized', { players: 42 }),
      event(2, 'game_initialized', { players: null }),
      event(3, 'execution_state', {
        execution_status: 'recovery_blocked',
        recoverable: false,
        recovery_block_code: 'model_config_missing',
      }),
      event(4, 'winner', { winning_camp: 'good', reason: 'all_wolves_dead' }),
    ]);
    expect(useGameStore.getState().recoveryBlockCode).toBe('model_config_missing');
    expect(useGameStore.getState().showWinOverlay).toBe(true);

    store.dismissWinOverlay();
    store.mergeAudienceEvents(page('game-1', [
      event(5, 'execution_state', {
        execution_status: 'recovery_blocked',
        recoverable: false,
        recovery_block_code: 'model_config_changed',
      }),
      event(6, 'winner', { winning_camp: 'good', reason: 'all_wolves_dead' }),
    ]));
    expect(useGameStore.getState().recoveryBlockCode).toBe('model_config_changed');
    expect(useGameStore.getState().showWinOverlay).toBe(false);
  });
});
