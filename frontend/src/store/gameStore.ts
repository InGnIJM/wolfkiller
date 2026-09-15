import { create } from 'zustand';
import type {
  AudienceEvent,
  AudienceEventPage,
  AudienceSnapshot,
  DeathRecord,
  ExecutionStatus,
  GameLogs,
  GamePhase,
  NightActionRecord,
  PublicGameState,
  PublicPlayerState,
  PublicReplayEvent,
  SpeechRecord,
  VoteRecord,
  WinResult,
  ModelSnapshotEntry,
} from './types';

interface DerivedState {
  players: Record<number, PublicPlayerState>;
  speeches: SpeechRecord[];
  votes: VoteRecord[];
  deathHistory: DeathRecord[];
  nightActions: NightActionRecord[];
  phase: GamePhase;
  roundNumber: number;
  winResult: WinResult | null;
  currentSpeaker: number | null;
}

interface GameStore extends DerivedState {
  gameId: string | null;
  revealOnDeath: boolean;
  connected: boolean;
  isPaused: boolean;
  timeline: PublicReplayEvent[];
  timelineIndex: number;
  isPlaying: boolean;
  playSpeed: number;
  showWinOverlay: boolean;
  winOverlayDismissed: boolean;
  showHistory: boolean;
  initialPlayers: Record<number, PublicPlayerState>;
  currentPublicPlayers: Record<number, PublicPlayerState>;
  syncMode: 'unknown' | 'incremental' | 'legacy';
  audienceCursor: number;
  audienceHighWatermark: number;
  pendingAudienceEvents: Record<number, AudienceEvent>;
  streamError: string | null;
  isFollowingLive: boolean;
  executionStatus: ExecutionStatus | null;
  recoverable: boolean;
  recoveryBlockCode: string | null;
  modelSnapshot: ModelSnapshotEntry[];

  setGameState: (state: PublicGameState) => void;
  setPhase: (phase: GamePhase, roundNumber: number) => void;
  addSpeech: (speech: SpeechRecord) => void;
  addVote: (vote: VoteRecord) => void;
  addDeath: (death: DeathRecord) => void;
  setWinResult: (result: WinResult) => void;
  setConnected: (connected: boolean) => void;
  setNightSubstep: (data: { substep: string; roundNumber: number }) => void;
  setPaused: (paused: boolean) => void;
  setCurrentSpeaker: (seat: number | null) => void;

  initPlayersFromDetail: (
    players: Record<number, PublicPlayerState>,
    revealOnDeath?: boolean,
    modelSnapshot?: ModelSnapshotEntry[],
  ) => void;
  loadLogs: (logs: GameLogs) => void;
  mergeLogs: (logs: GameLogs) => void;
  loadAudienceSnapshot: (snapshot: AudienceSnapshot) => void;
  loadAudienceHistory: (gameId: string, events: AudienceEvent[]) => void;
  mergeAudienceEvents: (page: AudienceEventPage) => void;
  goLive: () => void;
  seekTo: (index: number) => void;
  stepForward: () => void;
  stepBack: () => void;
  play: () => void;
  pause: () => void;
  setSpeed: (speed: number) => void;
  toggleHistory: () => void;
  dismissWinOverlay: () => void;
  reset: () => void;
}

const emptyDerivedState: DerivedState = {
  players: {},
  speeches: [],
  votes: [],
  deathHistory: [],
  nightActions: [],
  phase: 'waiting',
  roundNumber: 0,
  winResult: null,
  currentSpeaker: null,
};

const initialState = {
  gameId: null as string | null,
  revealOnDeath: false,
  connected: false,
  ...emptyDerivedState,
  isPaused: false,
  timeline: [] as PublicReplayEvent[],
  timelineIndex: -1,
  isPlaying: false,
  playSpeed: 1,
  showWinOverlay: false,
  winOverlayDismissed: false,
  showHistory: false,
  initialPlayers: {} as Record<number, PublicPlayerState>,
  currentPublicPlayers: {} as Record<number, PublicPlayerState>,
  syncMode: 'unknown' as const,
  audienceCursor: 0,
  audienceHighWatermark: 0,
  pendingAudienceEvents: {} as Record<number, AudienceEvent>,
  streamError: null as string | null,
  isFollowingLive: true,
  executionStatus: null as ExecutionStatus | null,
  recoverable: false,
  recoveryBlockCode: null as string | null,
  modelSnapshot: [] as ModelSnapshotEntry[],
};

let playTimer: ReturnType<typeof setInterval> | null = null;

function stopTimer() {
  if (playTimer !== null) {
    clearInterval(playTimer);
    playTimer = null;
  }
}

export function isAtTimelineEnd(timeline: PublicReplayEvent[], index: number): boolean {
  return index >= timeline.length - 1;
}

function copyPlayers(players: Record<number, PublicPlayerState>) {
  return Object.fromEntries(
    Object.entries(players).map(([seat, player]) => [Number(seat), { ...player }]),
  ) as Record<number, PublicPlayerState>;
}

function buildInitialPlayers(players: Record<number, PublicPlayerState>) {
  return Object.fromEntries(
    Object.entries(players).map(([seat, player]) => [
      Number(seat),
      { seat_number: player.seat_number, is_alive: true, is_sheriff: false, role: player.role, camp: player.camp },
    ]),
  ) as Record<number, PublicPlayerState>;
}

function applyCurrentSheriffSnapshot(
  players: Record<number, PublicPlayerState>,
  currentPublicPlayers: Record<number, PublicPlayerState>,
) {
  for (const [seat, player] of Object.entries(players)) {
    players[Number(seat)] = {
      ...player,
      is_sheriff: currentPublicPlayers[Number(seat)].is_sheriff,
    };
  }
}

function buildTimeline(logs: GameLogs): PublicReplayEvent[] {
  return logs.events.map((event) => ({ ...event }));
}

function toReplayEvent(event: AudienceEvent): PublicReplayEvent {
  return {
    timestamp: (event.created_at ?? event.timestamp ?? new Date(0).toISOString()) as PublicReplayEvent['timestamp'],
    event_type: event.event_type,
    payload: event.payload,
    seq: event.seq,
    event_id: event.event_id,
    schema_version: event.schema_version,
  } as PublicReplayEvent;
}

function latestExecutionPayload(events: AudienceEvent[]): Record<string, unknown> | undefined {
  return [...events].reverse().find((event) => (
    event.event_type === 'execution_state'
    && typeof event.payload.execution_status === 'string'
    && typeof event.payload.recoverable === 'boolean'
    && (event.payload.recovery_block_code === null
      || typeof event.payload.recovery_block_code === 'string')
  ))?.payload;
}

function eventPlayers(value: unknown): Record<number, PublicPlayerState> {
  if (Array.isArray(value)) {
    return Object.fromEntries(value
      .filter((player): player is PublicPlayerState => (
        typeof player === 'object' && player !== null
        && typeof (player as PublicPlayerState).seat_number === 'number'
      ))
      .map((player) => [player.seat_number, { ...player }]));
  }
  if (typeof value !== 'object' || value === null) return {};
  return Object.fromEntries(Object.entries(value)
    .filter(([, player]) => typeof player === 'object' && player !== null)
    .map(([seat, player]) => [Number(seat), { ...(player as PublicPlayerState) }]));
}

function eventKey(event: PublicReplayEvent): string {
  return JSON.stringify(event);
}

function timelinesEqual(
  left: PublicReplayEvent[],
  right: PublicReplayEvent[],
): boolean {
  return left.length === right.length
    && left.every((event, index) => eventKey(event) === eventKey(right[index]));
}

function reconcileHistoricalIndex(
  oldTimeline: PublicReplayEvent[],
  oldIndex: number,
  newTimeline: PublicReplayEvent[],
): number {
  if (oldIndex < 0 || newTimeline.length === 0) return -1;

  const clampedOldIndex = Math.min(oldIndex, oldTimeline.length - 1);
  const anchorKey = eventKey(oldTimeline[clampedOldIndex]);
  let anchorOccurrence = 0;

  for (let index = 0; index <= clampedOldIndex; index += 1) {
    if (eventKey(oldTimeline[index]) === anchorKey) anchorOccurrence += 1;
  }

  for (let index = 0; index < newTimeline.length; index += 1) {
    if (eventKey(newTimeline[index]) !== anchorKey) continue;
    anchorOccurrence -= 1;
    if (anchorOccurrence === 0) return index;
  }

  return Math.min(oldIndex, newTimeline.length - 1);
}

function deriveState(
  timeline: PublicReplayEvent[],
  upToIndex: number,
  initialPlayers: Record<number, PublicPlayerState>,
  currentPublicPlayers: Record<number, PublicPlayerState>,
): DerivedState {
  const players = copyPlayers(initialPlayers);
  const speeches: SpeechRecord[] = [];
  const votes: VoteRecord[] = [];
  const deathHistory: DeathRecord[] = [];
  const nightActions: NightActionRecord[] = [];
  let phase: GamePhase = 'waiting';
  let roundNumber = 0;
  let winResult: WinResult | null = null;
  let currentSpeaker: number | null = null;

  for (let index = 0; index <= upToIndex && index < timeline.length; index += 1) {
    const event = timeline[index];

    switch (event.event_type) {
      case 'speech':
        speeches.push(event.payload);
        currentSpeaker = event.payload.player_seat;
        roundNumber = Math.max(roundNumber, event.payload.round_number);
        break;
      case 'vote':
        votes.push(event.payload);
        currentSpeaker = event.payload.voter_seat;
        roundNumber = Math.max(roundNumber, event.payload.round_number);
        break;
      case 'vote_result':
        roundNumber = Math.max(roundNumber, event.payload.round_number);
        if (event.seq === undefined && event.payload.exiled_seat !== null) {
          const player = players[event.payload.exiled_seat];
          if (player) {
            players[event.payload.exiled_seat] = { ...player, is_alive: false };
          }
          deathHistory.push({
            player_seat: event.payload.exiled_seat,
            cause: 'exile',
            round_number: event.payload.round_number,
          });
        }
        break;
      case 'night_action':
        nightActions.push(event.payload);
        roundNumber = Math.max(roundNumber, event.payload.round_number);
        break;
      case 'technical_abstain':
        roundNumber = Math.max(roundNumber, event.payload.round_number);
        break;
      case 'narration':
        roundNumber = Math.max(roundNumber, event.payload.round_number);
        currentSpeaker = null;
        break;
      case 'wolf_chat_message':
      case 'wolf_vote':
      case 'witch_thought':
      case 'seer_thought':
      case 'night_thought':
        roundNumber = Math.max(roundNumber, event.payload.round_number);
        break;
      case 'death': {
        const player = players[event.payload.player_seat];
        if (player) {
          players[event.payload.player_seat] = { ...player, is_alive: false };
        }
        deathHistory.push(event.payload);
        roundNumber = Math.max(roundNumber, event.payload.round_number);
        break;
      }
      case 'phase':
        phase = event.payload.phase;
        roundNumber = event.payload.round_number;
        currentSpeaker = null;
        break;
      case 'winner':
        winResult = event.payload;
        phase = 'game_over';
        currentSpeaker = null;
        break;
      case 'game_initialized': {
        const initialized = eventPlayers(event.payload.players);
        for (const [seat, player] of Object.entries(initialized)) {
          players[Number(seat)] = { ...player };
        }
        break;
      }
      case 'player_revealed': {
        const player = players[event.payload.seat_number];
        if (player) {
          players[event.payload.seat_number] = {
            ...player,
            role: event.payload.role,
            camp: event.payload.camp,
            revealed_role: event.payload.role,
          };
        }
        break;
      }
      case 'execution_state':
        break;
    }
  }

  if (timeline.length === 0 || upToIndex === timeline.length - 1) {
    applyCurrentSheriffSnapshot(players, currentPublicPlayers);
  }

  return { players, speeches, votes, deathHistory, nightActions, phase, roundNumber, winResult, currentSpeaker };
}

function startTimer(get: () => GameStore) {
  stopTimer();
  const speed = get().playSpeed;
  playTimer = setInterval(() => {
    const state = get();
    if (!state.isPlaying || isAtTimelineEnd(state.timeline, state.timelineIndex)) {
      state.pause();
      return;
    }
    state.stepForward();
  }, 3000 / speed);
}

export const useGameStore = create<GameStore>((set, get) => ({
  ...initialState,

  setGameState: (state) => {
    const currentPublicPlayers = copyPlayers(state.players);
    set({
      gameId: state.game_id,
      revealOnDeath: state.reveal_on_death ?? false,
      phase: state.phase,
      roundNumber: state.round_number,
      players: copyPlayers(currentPublicPlayers),
      initialPlayers: buildInitialPlayers(currentPublicPlayers),
      currentPublicPlayers,
      speeches: state.speeches,
      deathHistory: state.death_history,
      nightActions: [],
      winResult: state.win_result,
      currentSpeaker: null,
      executionStatus: state.execution_status ?? null,
      recoverable: state.recoverable ?? false,
      recoveryBlockCode: state.recovery_block_code ?? null,
    });
  },

  setPhase: (phase, roundNumber) => set({ phase, roundNumber, currentSpeaker: null }),

  addSpeech: (speech) => set((state) => ({
    speeches: [...state.speeches, speech],
    currentSpeaker: speech.player_seat,
  })),

  addVote: (vote) => set((state) => ({
    votes: [...state.votes, vote],
    currentSpeaker: vote.voter_seat,
  })),

  addDeath: (death) => set((state) => {
    const players = copyPlayers(state.players);
    const player = players[death.player_seat];
    if (player) players[death.player_seat] = { ...player, is_alive: false };
    return { players, deathHistory: [...state.deathHistory, death] };
  }),

  setWinResult: (result) => set({ winResult: result, phase: 'game_over', showWinOverlay: true }),
  setConnected: (connected) => set({ connected }),
  setNightSubstep: ({ roundNumber }) => set({
    phase: 'night',
    roundNumber,
    currentSpeaker: null,
  }),
  setPaused: (paused) => set({ isPaused: paused }),
  setCurrentSpeaker: (seat) => set({ currentSpeaker: seat }),

  initPlayersFromDetail: (players, revealOnDeath = false, modelSnapshot) => {
    const currentPublicPlayers = copyPlayers(players);
    const initialPlayers = buildInitialPlayers(currentPublicPlayers);
    const { timeline, timelineIndex } = get();
    const snapshotUpdate = modelSnapshot !== undefined ? { modelSnapshot } : {};
    if (timeline.length === 0) {
      const publicPlayers = copyPlayers(initialPlayers);
      applyCurrentSheriffSnapshot(publicPlayers, currentPublicPlayers);
      set({ players: publicPlayers, initialPlayers, currentPublicPlayers, revealOnDeath, ...snapshotUpdate });
      return;
    }
    set({
      ...deriveState(timeline, timelineIndex, initialPlayers, currentPublicPlayers),
      initialPlayers,
      currentPublicPlayers,
      revealOnDeath,
      ...snapshotUpdate,
    });
  },

  loadLogs: (logs) => {
    const timeline = buildTimeline(logs);
    const { initialPlayers, currentPublicPlayers } = get();
    set({
      gameId: logs.game_id,
      timeline,
      timelineIndex: -1,
      ...deriveState(timeline, -1, initialPlayers, currentPublicPlayers),
      isPlaying: false,
      winOverlayDismissed: false,
      showWinOverlay: false,
      syncMode: 'legacy',
      isFollowingLive: true,
    });
  },

  mergeLogs: (logs) => {
    const timeline = buildTimeline(logs);
    const {
      timeline: oldTimeline,
      timelineIndex,
      initialPlayers,
      currentPublicPlayers,
      isPaused,
      winOverlayDismissed,
    } = get();
    if (timelinesEqual(timeline, oldTimeline)) return;

    const shouldFollowTail = isAtTimelineEnd(oldTimeline, timelineIndex) && !isPaused;
    const nextIndex = shouldFollowTail
      ? timeline.length - 1
      : reconcileHistoricalIndex(oldTimeline, timelineIndex, timeline);
    const derived = deriveState(timeline, nextIndex, initialPlayers, currentPublicPlayers);
    set({
      timeline,
      timelineIndex: nextIndex,
      ...derived,
      showWinOverlay: derived.winResult !== null && !winOverlayDismissed,
    });
  },

  loadAudienceSnapshot: (snapshot) => {
    const state = snapshot.state;
    const currentPublicPlayers = copyPlayers(state.players);
    const initialPlayers = buildInitialPlayers(currentPublicPlayers);
    set({
      gameId: snapshot.game_id,
      revealOnDeath: state.reveal_on_death ?? false,
      phase: state.phase,
      roundNumber: state.round_number,
      players: copyPlayers(currentPublicPlayers),
      initialPlayers,
      currentPublicPlayers,
      speeches: state.speeches ?? [],
      votes: [],
      deathHistory: state.death_history ?? [],
      nightActions: [],
      winResult: state.win_result,
      currentSpeaker: null,
      syncMode: 'incremental',
      audienceCursor: 0,
      audienceHighWatermark: snapshot.seq,
      pendingAudienceEvents: {},
      streamError: null,
      isFollowingLive: true,
      executionStatus: state.execution_status ?? null,
      recoverable: state.recoverable ?? false,
      recoveryBlockCode: state.recovery_block_code ?? null,
      modelSnapshot: state.model_snapshot ?? [],
      timeline: [],
      timelineIndex: -1,
      isPlaying: false,
      showWinOverlay: Boolean(state.win_result),
      winOverlayDismissed: false,
    });
  },

  loadAudienceHistory: (gameId, events) => {
    if (get().gameId !== gameId || get().syncMode !== 'incremental') return;
    const ordered = [...events].sort((left, right) => left.seq - right.seq);
    const contiguous: AudienceEvent[] = [];
    const pending: Record<number, AudienceEvent> = {};
    let cursor = 0;
    for (const event of ordered) {
      if (!Number.isInteger(event.seq) || event.seq <= 0 || event.seq <= cursor) continue;
      if (event.seq === cursor + 1) {
        contiguous.push(event);
        cursor = event.seq;
      } else {
        pending[event.seq] = event;
      }
    }
    const timeline = contiguous.map(toReplayEvent);
    if (timeline.length === 0) {
      set({
        audienceCursor: cursor,
        pendingAudienceEvents: pending,
        isFollowingLive: true,
      });
      return;
    }
    const { initialPlayers, currentPublicPlayers, winOverlayDismissed } = get();
    const timelineIndex = timeline.length - 1;
    const derived = deriveState(timeline, timelineIndex, initialPlayers, currentPublicPlayers);
    const payload = latestExecutionPayload(contiguous);
    set({
      timeline,
      timelineIndex,
      ...derived,
      audienceCursor: cursor,
      pendingAudienceEvents: pending,
      isFollowingLive: true,
      showWinOverlay: derived.winResult !== null && !winOverlayDismissed,
      executionStatus: typeof payload?.execution_status === 'string'
        ? payload.execution_status as ExecutionStatus
        : get().executionStatus,
      recoverable: typeof payload?.recoverable === 'boolean'
        ? payload.recoverable
        : get().recoverable,
      recoveryBlockCode: typeof payload?.recovery_block_code === 'string'
        ? payload.recovery_block_code
        : payload?.recovery_block_code === null ? null : get().recoveryBlockCode,
    });
  },

  mergeAudienceEvents: (page) => {
    const state = get();
    if (state.gameId !== page.game_id || state.syncMode !== 'incremental') return;
    const pending = { ...state.pendingAudienceEvents };
    let streamError = state.streamError;
    for (const event of page.events) {
      if (!Number.isInteger(event.seq) || event.seq <= 0) {
        streamError = '收到无效的观众事件序号，已停止应用该事件。';
        continue;
      }
      if (event.seq <= state.audienceCursor) continue;
      const existing = pending[event.seq];
      if (existing && existing.event_id !== event.event_id) {
        streamError = `事件序号 ${event.seq} 存在冲突，等待重新同步。`;
        continue;
      }
      pending[event.seq] = event;
    }

    const consumed: AudienceEvent[] = [];
    let cursor = state.audienceCursor;
    while (pending[cursor + 1]) {
      const event = pending[cursor + 1];
      delete pending[cursor + 1];
      consumed.push(event);
      cursor = event.seq;
    }
    if (consumed.length === 0) {
      set({
        pendingAudienceEvents: pending,
        audienceHighWatermark: Math.max(
          state.audienceHighWatermark,
          page.high_watermark ?? page.last_seq,
        ),
        streamError,
      });
      return;
    }

    const oldTimeline = state.timeline;
    const timeline = [...oldTimeline, ...consumed.map(toReplayEvent)];
    const shouldFollowTail = state.isFollowingLive
      && (oldTimeline.length === 0 || state.timelineIndex >= oldTimeline.length - 1);
    const timelineIndex = shouldFollowTail ? timeline.length - 1 : state.timelineIndex;
    const derived = deriveState(
      timeline,
      timelineIndex,
      state.initialPlayers,
      state.currentPublicPlayers,
    );
    const payload = latestExecutionPayload(consumed);
    set({
      timeline,
      timelineIndex,
      ...derived,
      audienceCursor: cursor,
      audienceHighWatermark: Math.max(
        state.audienceHighWatermark,
        page.high_watermark ?? page.last_seq,
      ),
      pendingAudienceEvents: pending,
      streamError,
      showWinOverlay: derived.winResult !== null && !state.winOverlayDismissed,
      executionStatus: typeof payload?.execution_status === 'string'
        ? payload.execution_status as ExecutionStatus
        : state.executionStatus,
      recoverable: typeof payload?.recoverable === 'boolean'
        ? payload.recoverable
        : state.recoverable,
      recoveryBlockCode: typeof payload?.recovery_block_code === 'string'
        ? payload.recovery_block_code
        : payload?.recovery_block_code === null ? null : state.recoveryBlockCode,
    });
  },

  goLive: () => {
    const { timeline } = get();
    if (timeline.length === 0) {
      set({ isFollowingLive: true });
      return;
    }
    get().seekTo(timeline.length - 1);
    set({ isFollowingLive: true });
  },

  seekTo: (index) => {
    const { timeline, initialPlayers, currentPublicPlayers, winOverlayDismissed } = get();
    if (timeline.length === 0) return;
    const timelineIndex = Math.max(0, Math.min(index, timeline.length - 1));
    const derived = deriveState(timeline, timelineIndex, initialPlayers, currentPublicPlayers);
    set({
      timelineIndex,
      ...derived,
      showWinOverlay: derived.winResult !== null && !winOverlayDismissed,
      isFollowingLive: timelineIndex === timeline.length - 1,
    });
  },

  stepForward: () => {
    const { timelineIndex, timeline } = get();
    if (timelineIndex < timeline.length - 1) get().seekTo(timelineIndex + 1);
    else get().pause();
  },

  stepBack: () => {
    const { timelineIndex } = get();
    if (timelineIndex > 0) get().seekTo(timelineIndex - 1);
  },

  play: () => {
    const { timeline, timelineIndex } = get();
    if (timeline.length === 0) return;
    if (isAtTimelineEnd(timeline, timelineIndex)) get().seekTo(0);
    set({ isPlaying: true, isPaused: false });
    startTimer(get);
  },

  pause: () => {
    stopTimer();
    set({ isPlaying: false });
  },

  setSpeed: (speed) => {
    set({ playSpeed: speed });
    if (get().isPlaying) startTimer(get);
  },

  toggleHistory: () => set((state) => ({ showHistory: !state.showHistory })),
  dismissWinOverlay: () => set({ showWinOverlay: false, winOverlayDismissed: true }),

  reset: () => {
    stopTimer();
    set(initialState);
  },
}));
