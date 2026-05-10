// ── Player / Game state (shared with backend) ──────────────

export interface PlayerState {
  seat_number: number;
  is_alive: boolean;
  revealed_role: string | null;
}

export interface SpeechRecord {
  player_seat: number;
  text: string;
  round_number: number;
}

export interface VoteRecord {
  voter_seat: number;
  target_seat: number | null;
  reasoning: string;
}

export interface DeathRecord {
  player_seat: number;
  cause: string;
  round_number: number;
}

export interface PublicGameState {
  game_id: string;
  phase: string;
  round_number: number;
  players: Record<number, PlayerState>;
  speeches: SpeechRecord[];
  death_history: DeathRecord[];
  win_result: { winning_camp: string; reason: string } | null;
}

export type GamePhase =
  | 'waiting' | 'role_deal' | 'night' | 'dawn' | 'last_words'
  | 'speech' | 'vote_casting' | 'vote_resolution'
  | 'game_over';

// ── WebSocket messages ─────────────────────────────────────

export interface NightActionInfo {
  action_type: string;
  target_seat: number | null;
  reasoning: string;
  seer_result?: string | null;
}

export interface NightSubstepData {
  step: string;
  highlightSeats: number[];
  actionSeat: number | null;
  action: NightActionInfo | null;
  wolfKillTarget: number | null;
  roundNumber: number;
}

export interface WSMessage {
  type: string;
  state?: PublicGameState;
  phase?: string;
  round_number?: number;
  speech?: SpeechRecord;
  vote?: VoteRecord;
  death?: DeathRecord;
  win_result?: { winning_camp: string; reason: string };
  step?: string;
  highlight_seats?: number[];
  action_seat?: number | null;
  action?: NightActionInfo | null;
  wolf_kill_target?: number | null;
  paused?: boolean;
}

// ── Log-driven types (audience / replay mode) ──────────────

export interface ConversationEntry {
  scope: 'public' | 'werewolf' | 'night_intel';
  content: string;
  round_number: number;
  speaker_seat: number | null;
  speaker_role: string | null;
  phase: string;
  visible_to: number[] | null;
  timestamp: string;
}

export interface OperationEntry {
  timestamp: string;
  round: number;
  phase: string;
  operation: string;
  seat: number | null;
  data: Record<string, any>;
}

export interface TimelineEntry {
  type: 'conversation' | 'operation';
  // conversation fields
  scope?: string;
  content?: string;
  round_number?: number;
  speaker_seat?: number | null;
  speaker_role?: string | null;
  visible_to?: number[] | null;
  // operation fields
  round?: number;
  operation?: string;
  seat?: number | null;
  data?: Record<string, any>;
  // common
  phase: string;
  timestamp: string;
}

export interface GameLogs {
  game_id: string;
  conversations: ConversationEntry[];
  operations: OperationEntry[];
}

// ── Extended player state (audience view — roles visible) ──

export interface PlayerFullState {
  seat_number: number;
  role: string;
  camp: string;
  is_alive: boolean;
  has_antidote: boolean;
  has_poison: boolean;
  has_gun: boolean;
  revealed_role: string | null;
}
