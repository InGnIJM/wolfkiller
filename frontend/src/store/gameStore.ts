import { create } from 'zustand';
import type {
  DeathRecord,
  GameLogs,
  GamePhase,
  NightActionRecord,
  PublicGameState,
  PublicPlayerState,
  PublicReplayEvent,
  SpeechRecord,
  VoteRecord,
  WinResult,
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

  initPlayersFromDetail: (players: Record<number, PublicPlayerState>) => void;
  loadLogs: (logs: GameLogs) => void;
  mergeLogs: (logs: GameLogs) => void;
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
        if (event.payload.exiled_seat !== null) {
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
      case 'narration':
        roundNumber = Math.max(roundNumber, event.payload.round_number);
        currentSpeaker = null;
        break;
      case 'wolf_chat_message':
      case 'wolf_vote':
      case 'witch_thought':
      case 'seer_thought':
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

  initPlayersFromDetail: (players) => {
    const currentPublicPlayers = copyPlayers(players);
    const initialPlayers = buildInitialPlayers(currentPublicPlayers);
    const { timeline, timelineIndex } = get();
    if (timeline.length === 0) {
      const publicPlayers = copyPlayers(initialPlayers);
      applyCurrentSheriffSnapshot(publicPlayers, currentPublicPlayers);
      set({ players: publicPlayers, initialPlayers, currentPublicPlayers });
      return;
    }
    set({
      ...deriveState(timeline, timelineIndex, initialPlayers, currentPublicPlayers),
      initialPlayers,
      currentPublicPlayers,
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

  seekTo: (index) => {
    const { timeline, initialPlayers, currentPublicPlayers, winOverlayDismissed } = get();
    if (timeline.length === 0) return;
    const timelineIndex = Math.max(0, Math.min(index, timeline.length - 1));
    const derived = deriveState(timeline, timelineIndex, initialPlayers, currentPublicPlayers);
    set({
      timelineIndex,
      ...derived,
      showWinOverlay: derived.winResult !== null && !winOverlayDismissed,
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
