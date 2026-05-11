import { create } from 'zustand';
import {
  PublicGameState, PlayerFullState, SpeechRecord, VoteRecord, DeathRecord,
  NightSubstepData, TimelineEntry, GameLogs,
} from './types';

interface GameStore {
  gameId: string | null;
  connected: boolean;
  phase: string;
  roundNumber: number;
  players: Record<number, PlayerFullState>;
  speeches: SpeechRecord[];
  votes: VoteRecord[];
  deathHistory: DeathRecord[];
  winResult: { winning_camp: string; reason: string } | null;
  nightSubstep: NightSubstepData | null;
  isPaused: boolean;
  currentSpeaker: number | null;
  highlightedSeats: number[];
  wolfKillTarget: number | null;
  // timeline model
  timeline: TimelineEntry[];
  timelineIndex: number;
  isPlaying: boolean;
  playSpeed: number;
  showWinOverlay: boolean;
  winOverlayDismissed: boolean;
  showHistory: boolean;

  // live websocket actions (kept for fallback)
  setGameState: (state: PublicGameState) => void;
  setPhase: (phase: string, roundNumber: number) => void;
  addSpeech: (speech: SpeechRecord) => void;
  addVote: (vote: VoteRecord) => void;
  addDeath: (death: DeathRecord) => void;
  setWinResult: (result: { winning_camp: string; reason: string }) => void;
  setConnected: (connected: boolean) => void;
  setNightSubstep: (data: NightSubstepData | null) => void;
  setPaused: (paused: boolean) => void;
  setCurrentSpeaker: (seat: number | null) => void;

  // log-driven actions
  initPlayersFromDetail: (players: Record<number, any>) => void;
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

const initialState = {
  gameId: null as string | null,
  connected: false,
  phase: 'waiting',
  roundNumber: 0,
  players: {} as Record<number, PlayerFullState>,
  speeches: [] as SpeechRecord[],
  votes: [] as VoteRecord[],
  deathHistory: [] as DeathRecord[],
  winResult: null as { winning_camp: string; reason: string } | null,
  nightSubstep: null as NightSubstepData | null,
  isPaused: false,
  currentSpeaker: null as number | null,
  highlightedSeats: [] as number[],
  wolfKillTarget: null as number | null,
  timeline: [] as TimelineEntry[],
  timelineIndex: -1,
  isPlaying: false,
  playSpeed: 1.0,
  showWinOverlay: false,
  winOverlayDismissed: false,
  showHistory: false,
};

let _playTimer: ReturnType<typeof setInterval> | null = null;

function stopTimer() {
  if (_playTimer !== null) {
    clearInterval(_playTimer);
    _playTimer = null;
  }
}

function startTimer(store: any) {
  stopTimer();
  const speed = store.getState().playSpeed;
  _playTimer = setInterval(() => {
    const s = store.getState();
    if (!s.isPlaying) { stopTimer(); return; }
    if (s.timelineIndex >= s.timeline.length - 1) {
      store.getState().pause();
      return;
    }
    store.getState().stepForward();
  }, speed * 3000);
}

function buildTimeline(logs: GameLogs): TimelineEntry[] {
  const timeline: TimelineEntry[] = [];

  for (const c of logs.conversations) {
    timeline.push({
      type: 'conversation',
      scope: c.scope,
      content: c.content,
      round_number: c.round_number,
      speaker_seat: c.speaker_seat,
      speaker_role: c.speaker_role,
      phase: c.phase,
      visible_to: c.visible_to,
      timestamp: c.timestamp,
    });
  }

  for (const op of logs.operations) {
    timeline.push({
      type: 'operation',
      phase: op.phase,
      round: op.round,
      operation: op.operation,
      seat: op.seat,
      data: op.data,
      timestamp: op.timestamp,
    });
  }

  timeline.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  return timeline;
}

function ensurePlayer(players: Record<number, PlayerFullState>, seat: number, role?: string | null) {
  if (!players[seat]) {
    const resolvedRole = role || 'unknown';
    const camp = resolvedRole.includes('werewolf') ? 'werewolf' : 'good';
    players[seat] = {
      seat_number: seat,
      role: resolvedRole,
      camp,
      is_alive: true,
      has_antidote: resolvedRole.includes('witch'),
      has_poison: resolvedRole.includes('witch'),
      has_gun: resolvedRole.includes('hunter'),
      revealed_role: null,
    };
  }
}

function deriveState(timeline: TimelineEntry[], upToIndex: number, basePlayers?: Record<number, PlayerFullState>) {
  const players: Record<number, PlayerFullState> = {};
  // Copy base players (from game detail) as starting point
  if (basePlayers) {
    for (const [seatStr, p] of Object.entries(basePlayers)) {
      players[parseInt(seatStr)] = { ...p };
    }
  }
  const speeches: SpeechRecord[] = [];
  const votes: VoteRecord[] = [];
  const deathHistory: DeathRecord[] = [];
  let phase = 'waiting';
  let roundNumber = 0;
  let winResult: { winning_camp: string; reason: string } | null = null;
  let currentSpeaker: number | null = null;
  let highlightedSeats: number[] = [];
  let wolfKillTarget: number | null = null;

  for (let i = 0; i <= upToIndex && i < timeline.length; i++) {
    const entry = timeline[i];

    // Track round from any entry (round is at top level for both types)
    const entryRound = entry.round_number ?? entry.round;
    if (entryRound !== undefined && entryRound !== null && entryRound > roundNumber) {
      roundNumber = entryRound;
    }

    if (entry.type === 'conversation') {
      if (entry.speaker_seat) {
        ensurePlayer(players, entry.speaker_seat, entry.speaker_role);
      }
      // Set current speaker from public conversations
      if (entry.scope === 'public' && entry.speaker_seat) {
        currentSpeaker = entry.speaker_seat;
      }
      // Compute highlighted seats from conversation scope
      if (entry.scope === 'werewolf') {
        highlightedSeats = Object.entries(players)
          .filter(([_, p]) => p.camp === 'werewolf')
          .map(([s]) => parseInt(s));
      } else if (entry.scope === 'night_intel' && entry.visible_to) {
        highlightedSeats = entry.visible_to;
      } else if (entry.scope === 'public') {
        highlightedSeats = [];
      }
    }

    if (entry.type === 'operation') {
      switch (entry.operation) {
        case 'phase_change':
          phase = entry.data?.new_phase || entry.phase;
          highlightedSeats = [];
          wolfKillTarget = null;
          break;
        case 'speak':
          if (entry.seat) {
            ensurePlayer(players, entry.seat, entry.data?.role);
            if (entry.data?.text) {
              speeches.push({ player_seat: entry.seat, text: entry.data.text, round_number: entry.round || 0 });
              currentSpeaker = entry.seat;
            }
          }
          highlightedSeats = [];
          break;
        case 'vote':
          if (entry.seat !== null && entry.seat !== undefined) {
            ensurePlayer(players, entry.seat);
            votes.push({
              voter_seat: entry.seat,
              target_seat: entry.data?.target ?? null,
              reasoning: entry.data?.reasoning || '',
            });
            currentSpeaker = entry.seat;
          }
          highlightedSeats = [];
          break;
        case 'vote_result':
          if (entry.data?.exiled !== null && entry.data?.exiled !== undefined) {
            const exiledSeat = entry.data.exiled;
            if (players[exiledSeat]) {
              players[exiledSeat].is_alive = false;
              players[exiledSeat].revealed_role = players[exiledSeat].role;
            }
            deathHistory.push({ player_seat: exiledSeat, cause: 'exile', round_number: entry.round || 0 });
          }
          highlightedSeats = [];
          break;
        case 'night_deaths':
          if (entry.data?.deaths) {
            for (const d of entry.data.deaths) {
              const seat = d.player_seat || d.seat;
              const cause = d.cause || 'wolf_kill';
              if (seat) {
                ensurePlayer(players, seat, d.role);
                if (players[seat]) {
                  players[seat].is_alive = false;
                  players[seat].revealed_role = players[seat].role;
                }
              }
              deathHistory.push({ player_seat: seat, cause, round_number: entry.round || 0 });
            }
          }
          highlightedSeats = [];
          wolfKillTarget = null;
          break;
        case 'werewolf_kill':
          if (entry.data?.wolf_seats) {
            highlightedSeats = entry.data.wolf_seats;
            for (const s of entry.data.wolf_seats) {
              ensurePlayer(players, s, 'wolf-killer-werewolf');
            }
          }
          if (entry.data?.target) {
            wolfKillTarget = entry.data.target;
          }
          break;
        case 'witch_save':
        case 'witch_poison':
          if (entry.seat) {
            highlightedSeats = [entry.seat];
            ensurePlayer(players, entry.seat, 'wolf-killer-witch');
          }
          if (entry.operation === 'witch_poison' && entry.data?.target) {
            wolfKillTarget = entry.data.target;
          }
          break;
        case 'seer_check':
          if (entry.seat) {
            highlightedSeats = [entry.seat];
            ensurePlayer(players, entry.seat, 'wolf-killer-seer');
          }
          break;
        case 'hunter_shoot':
          if (entry.seat) {
            highlightedSeats = [entry.seat];
            ensurePlayer(players, entry.seat, 'wolf-killer-hunter');
          }
          if (entry.data?.target) {
            wolfKillTarget = entry.data.target;
            if (players[entry.data.target]) {
              players[entry.data.target].is_alive = false;
              players[entry.data.target].revealed_role = players[entry.data.target].role;
            }
            deathHistory.push({
              player_seat: entry.data.target,
              cause: 'hunter_shot',
              round_number: entry.round || 0,
            });
          }
          break;
        case 'game_over':
          winResult = { winning_camp: entry.data?.winner || '', reason: entry.data?.reason || '' };
          phase = 'game_over';
          highlightedSeats = [];
          break;
        case 'role_init':
          if (entry.data?.players) {
            for (const [seatStr, p] of Object.entries(entry.data.players)) {
              const seatNum = parseInt(seatStr);
              players[seatNum] = { ...p as PlayerFullState, seat_number: seatNum };
            }
          }
          break;
      }
    }
  }

  return { players, speeches, votes, deathHistory, phase, roundNumber, winResult, currentSpeaker, highlightedSeats, wolfKillTarget };
}

export const useGameStore = create<GameStore>((set, get) => ({
  ...initialState,

  // ── Live WebSocket actions (kept for fallback) ──────────

  setGameState: (state: PublicGameState) =>
    set({
      gameId: state.game_id,
      phase: state.phase,
      roundNumber: state.round_number,
      players: state.players as unknown as Record<number, PlayerFullState>,
      speeches: state.speeches,
      deathHistory: state.death_history,
      winResult: state.win_result,
      currentSpeaker: null,
    }),

  setPhase: (phase, roundNumber) =>
    set({ phase, roundNumber, currentSpeaker: null }),

  addSpeech: (speech) =>
    set((s) => ({
      speeches: [...s.speeches, speech],
      currentSpeaker: speech.player_seat,
    })),

  addVote: (vote) =>
    set((s) => ({
      votes: [...s.votes, vote],
      currentSpeaker: vote.voter_seat,
    })),

  addDeath: (death) =>
    set((s) => {
      const players = { ...s.players };
      if (players[death.player_seat]) {
        players[death.player_seat] = {
          ...players[death.player_seat],
          is_alive: false,
          revealed_role: players[death.player_seat].revealed_role || players[death.player_seat].role,
        };
      }
      return {
        players,
        deathHistory: [...s.deathHistory, death],
      };
    }),

  setWinResult: (result) =>
    set({ winResult: result, phase: 'game_over', showWinOverlay: true }),

  setConnected: (connected) => set({ connected }),

  setNightSubstep: (data) =>
    set({
      nightSubstep: data,
      currentSpeaker: data?.actionSeat ?? null,
    }),

  setPaused: (paused) => set({ isPaused: paused }),

  setCurrentSpeaker: (seat) => set({ currentSpeaker: seat }),

  // ── Log-driven actions ──────────────────────────────────

  initPlayersFromDetail: (detail: Record<number, any>) => {
    const players: Record<number, PlayerFullState> = {};
    for (const [seatStr, p] of Object.entries(detail)) {
      const seatNum = parseInt(seatStr);
      players[seatNum] = {
        seat_number: p.seat_number || seatNum,
        role: p.role || '',
        camp: p.camp || '',
        is_alive: p.is_alive,
        has_antidote: p.has_antidote ?? false,
        has_poison: p.has_poison ?? false,
        has_gun: p.has_gun ?? false,
        revealed_role: p.revealed_role ?? null,
      };
    }
    set({ players });
  },

  loadLogs: (logs: GameLogs) => {
    const timeline = buildTimeline(logs);
    const existingPlayers = get().players;

    // Build initial player state — used as fallback when deriveState finds no role_init
    const players: Record<number, PlayerFullState> = {};

    for (const entry of timeline) {
      if (entry.type === 'operation' && entry.operation === 'role_init' && entry.data?.players) {
        for (const [seatStr, p] of Object.entries(entry.data.players as Record<string, any>)) {
          const seatNum = parseInt(seatStr);
          players[seatNum] = {
            seat_number: seatNum,
            role: p.role || '',
            camp: p.camp || '',
            is_alive: p.is_alive ?? true,
            has_antidote: p.has_antidote ?? false,
            has_poison: p.has_poison ?? false,
            has_gun: p.has_gun ?? false,
            revealed_role: p.revealed_role ?? null,
          };
        }
        break;
      }
    }

    if (Object.keys(players).length === 0) {
      const seenRoles: Record<number, string> = {};
      for (const entry of timeline) {
        if (entry.type === 'conversation' && entry.speaker_seat && entry.speaker_role) {
          seenRoles[entry.speaker_seat] = entry.speaker_role;
        }
        if (entry.type === 'operation' && entry.operation === 'speak' && entry.seat && entry.data?.role) {
          seenRoles[entry.seat] = entry.data.role;
        }
        if (entry.type === 'operation' && entry.operation === 'night_deaths' && entry.data?.deaths) {
          for (const d of entry.data.deaths) {
            const seat = d.player_seat || d.seat;
            if (seat && d.role) seenRoles[seat] = d.role;
          }
        }
      }
      for (const [seatStr, role] of Object.entries(seenRoles)) {
        const seatNum = parseInt(seatStr);
        const camp = role.includes('werewolf') ? 'werewolf' : 'good';
        players[seatNum] = {
          seat_number: seatNum,
          role,
          camp,
          is_alive: true,
          has_antidote: role.includes('witch'),
          has_poison: role.includes('witch'),
          has_gun: role.includes('hunter'),
          revealed_role: null,
        };
      }
      // Fall back to existingPlayers for any seats we couldn't infer roles for
      for (const [seatStr, p] of Object.entries(existingPlayers)) {
        const seatNum = parseInt(seatStr);
        if (!players[seatNum]) {
          players[seatNum] = { ...p };
        }
      }
    }

    set({
      gameId: logs.game_id,
      timeline,
      timelineIndex: 0,
      ...deriveState(timeline, 0, existingPlayers),
      players,
      winOverlayDismissed: false,
    });
  },

  mergeLogs: (logs: GameLogs) => {
    const fullTimeline = buildTimeline(logs);
    const { timeline: oldTimeline, timelineIndex, players: basePlayers } = get();

    if (fullTimeline.length <= oldTimeline.length) return;

    const wasAtEnd = timelineIndex >= oldTimeline.length - 1;
    set({ timeline: fullTimeline });

    if (wasAtEnd) {
      let newIndex = fullTimeline.length - 1;
      // Skip speak operations (same logic as seekTo)
      while (newIndex > 0 && fullTimeline[newIndex].type === 'operation' && fullTimeline[newIndex].operation === 'speak') {
        newIndex--;
      }
      const derived = deriveState(fullTimeline, newIndex, basePlayers);
      set({
        timelineIndex: newIndex,
        ...derived,
        showWinOverlay: derived.winResult !== null && !get().winOverlayDismissed,
      });
    }
  },

  seekTo: (index: number) => {
    const { timeline, winOverlayDismissed, players: basePlayers } = get();
    let clamped = Math.max(0, Math.min(index, timeline.length - 1));
    if (clamped < 0) return;
    // Skip speak operation entries (they duplicate conversation entries)
    while (clamped < timeline.length - 1 && timeline[clamped].type === 'operation' && timeline[clamped].operation === 'speak') {
      clamped++;
    }
    while (clamped > 0 && timeline[clamped].type === 'operation' && timeline[clamped].operation === 'speak') {
      clamped--;
    }
    const derived = deriveState(timeline, clamped, basePlayers);
    set({
      timelineIndex: clamped,
      ...derived,
      showWinOverlay: derived.winResult !== null && !winOverlayDismissed,
    });
  },

  stepForward: () => {
    const { timelineIndex, timeline } = get();
    let next = timelineIndex + 1;
    while (next < timeline.length && timeline[next].type === 'operation' && timeline[next].operation === 'speak') {
      next++;
    }
    if (next < timeline.length) {
      get().seekTo(next);
    } else {
      get().pause();
    }
  },

  stepBack: () => {
    const { timelineIndex, timeline } = get();
    let prev = timelineIndex - 1;
    while (prev >= 0 && timeline[prev].type === 'operation' && timeline[prev].operation === 'speak') {
      prev--;
    }
    if (prev >= 0) {
      get().seekTo(prev);
    }
  },

  play: () => {
    const { timelineIndex, timeline } = get();
    if (timelineIndex >= timeline.length - 1) {
      // restart from beginning
      get().seekTo(0);
    }
    set({ isPlaying: true, isPaused: false });
    startTimer(get);
  },

  pause: () => {
    stopTimer();
    set({ isPlaying: false, isPaused: false });
  },

  setSpeed: (speed: number) => {
    set({ playSpeed: speed });
    if (get().isPlaying) {
      startTimer(get);
    }
  },

  toggleHistory: () => set((s) => ({ showHistory: !s.showHistory })),

  dismissWinOverlay: () => set({ showWinOverlay: false, winOverlayDismissed: true }),

  reset: () => {
    stopTimer();
    set(initialState);
  },
}));
