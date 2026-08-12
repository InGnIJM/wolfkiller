import { create } from 'zustand';
import type {
  DeathRecord,
  GameLogs,
  GamePhase,
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

function buildTimeline(logs: GameLogs): PublicReplayEvent[] {
  return logs.events.map((event) => ({ ...event }));
}

function deriveState(
  timeline: PublicReplayEvent[],
  upToIndex: number,
  initialPlayers: Record<number, PublicPlayerState>,
): DerivedState {
  const players = copyPlayers(initialPlayers);
  const speeches: SpeechRecord[] = [];
  const votes: VoteRecord[] = [];
  const deathHistory: DeathRecord[] = [];
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

  return { players, speeches, votes, deathHistory, phase, roundNumber, winResult, currentSpeaker };
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

  setGameState: (state) => set({
    gameId: state.game_id,
    phase: state.phase,
    roundNumber: state.round_number,
    players: copyPlayers(state.players),
    initialPlayers: copyPlayers(state.players),
    speeches: state.speeches,
    deathHistory: state.death_history,
    winResult: state.win_result,
    currentSpeaker: null,
  }),

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
    const publicPlayers = copyPlayers(players);
    set({ players: publicPlayers, initialPlayers: copyPlayers(publicPlayers) });
  },

  loadLogs: (logs) => {
    const timeline = buildTimeline(logs);
    const initialPlayers = get().initialPlayers;
    set({
      gameId: logs.game_id,
      timeline,
      timelineIndex: -1,
      ...deriveState(timeline, -1, initialPlayers),
      isPlaying: false,
      winOverlayDismissed: false,
      showWinOverlay: false,
    });
  },

  mergeLogs: (logs) => {
    const timeline = buildTimeline(logs);
    const { timeline: oldTimeline, timelineIndex, initialPlayers } = get();
    if (timeline.length <= oldTimeline.length) return;

    const wasAtEnd = isAtTimelineEnd(oldTimeline, timelineIndex);
    set({ timeline });
    if (!wasAtEnd) return;

    const nextIndex = Math.min(timelineIndex + 1, timeline.length - 1);
    const derived = deriveState(timeline, nextIndex, initialPlayers);
    set({
      timelineIndex: nextIndex,
      ...derived,
      showWinOverlay: derived.winResult !== null && !get().winOverlayDismissed,
    });
  },

  seekTo: (index) => {
    const { timeline, initialPlayers, winOverlayDismissed } = get();
    if (timeline.length === 0) return;
    const timelineIndex = Math.max(0, Math.min(index, timeline.length - 1));
    const derived = deriveState(timeline, timelineIndex, initialPlayers);
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
