// Public observer contracts. These types intentionally contain no player
// resource or private identity information; audience-visible night actions
// (who acted on whom) are exposed through the closed night_action payload.

export type GamePhase =
  | 'waiting'
  | 'role_deal'
  | 'night'
  | 'dawn'
  | 'last_words'
  | 'sheriff_election'
  | 'speech'
  | 'vote_casting'
  | 'vote_resolution'
  | 'game_over'
  | 'error';

export type DeathCause = 'wolf_kill' | 'poison' | 'hunter_shot' | 'exile';
export type WinningCamp = 'good' | 'werewolf';
export type WinReason = 'all_gods_dead' | 'all_villagers_dead' | 'all_wolves_dead';
export type UtcTimestamp = `${string}Z`;

export interface PublicPlayerState {
  seat_number: number;
  is_alive: boolean;
  is_sheriff: boolean;
  role?: string;
  camp?: string;
}

export interface SpeechRecord {
  player_seat: number;
  text: string;
  round_number: number;
  phase?: string;
}

export interface VoteRecord {
  voter_seat: number;
  target_seat: number | null;
  round_number: number;
}

export interface DeathRecord {
  player_seat: number;
  cause: DeathCause;
  round_number: number;
}

export interface VoteResult {
  round_number: number;
  exiled_seat: number | null;
}

export type NightActionType =
  | 'werewolf_kill'
  | 'witch_save'
  | 'witch_poison'
  | 'seer_check'
  | 'hunter_shot'
  | 'guard_protect';

export interface NightActionRecord {
  action_type: NightActionType;
  target_seat: number;
  round_number: number;
  vote_counts?: Record<string, number>;
  result?: WinningCamp;
}

export interface NarrationPayload {
  round_number: number;
  title: string;
  text: string;
}

export interface WolfChatMessagePayload {
  round_number: number;
  seat: number;
  text: string;
}

export interface WolfVotePayload {
  round_number: number;
  seat: number;
  target_seat: number | null;
  reasoning: string;
}

export interface ThoughtPayload {
  round_number: number;
  seat: number;
  text: string;
}

export interface NightThoughtPayload {
  round_number: number;
  seat: number;
  action_type: 'hunter_reasoning' | 'witch_reasoning' | 'seer_reasoning' | 'guard_reasoning';
  target_seat: number | null;
  reasoning: string;
}

export interface PlayerMemory {
  seat_number: number;
  role: string;
  camp: string;
  is_alive: boolean;
  private_knowledge: Record<string, unknown>;
  action_history: Array<Record<string, unknown>>;
  witnessed_events: Array<Record<string, unknown>>;
  last_updated: string;
}

export interface GameMemories {
  game_id: string;
  memories: PlayerMemory[];
}

export interface WinResult {
  winning_camp: WinningCamp;
  reason: WinReason;
}

export interface PublicGameState {
  game_id: string;
  phase: GamePhase;
  round_number: number;
  players: Record<number, PublicPlayerState>;
  sheriff: number | null;
  speeches: SpeechRecord[];
  death_history: DeathRecord[];
  win_result: WinResult | null;
}

type PublicReplayEnvelope<TType extends string, TPayload> = {
  timestamp: UtcTimestamp;
  event_type: TType;
  payload: TPayload;
};

export type PublicReplayEvent =
  | PublicReplayEnvelope<'speech', SpeechRecord>
  | PublicReplayEnvelope<'death', DeathRecord>
  | PublicReplayEnvelope<'vote', VoteRecord>
  | PublicReplayEnvelope<'vote_result', VoteResult>
  | PublicReplayEnvelope<'night_action', NightActionRecord>
  | PublicReplayEnvelope<'narration', NarrationPayload>
  | PublicReplayEnvelope<'wolf_chat_message', WolfChatMessagePayload>
  | PublicReplayEnvelope<'wolf_vote', WolfVotePayload>
  | PublicReplayEnvelope<'witch_thought' | 'seer_thought', ThoughtPayload>
  | PublicReplayEnvelope<'night_thought', NightThoughtPayload>
  | PublicReplayEnvelope<'phase', { phase: GamePhase; round_number: number }>
  | PublicReplayEnvelope<'winner', WinResult>;

export interface GameLogs {
  game_id: string;
  events: PublicReplayEvent[];
}

export interface GameListItem {
  game_id: string;
  phase: GamePhase;
  round_number: number;
  player_count: number;
  alive_count: number;
  winner: WinningCamp | null;
}

export interface GameListResponse {
  games: GameListItem[];
}

export type WSMessage =
  | { type: 'game_state'; state: PublicGameState }
  | {
      type: 'phase_change';
      phase: GamePhase;
      round_number: number;
      state: PublicGameState;
    }
  | { type: 'speech'; speech: SpeechRecord }
  | { type: 'vote_cast'; vote: VoteRecord }
  | { type: 'player_died'; death: DeathRecord }
  | { type: 'game_over'; win_result: WinResult; state: PublicGameState }
  | {
      type: 'night_substep';
      phase: 'night';
      substep: string;
      round_number: number;
    }
  | { type: 'paused_state'; paused: boolean };

// ── Model config & game creation catalog ──────────────────────

export interface ModelConfig {
  id: string;
  name: string;
  base_url: string;
  model_id: string;
  has_key: boolean;
  api_key_masked: string | null;
  key_invalid: boolean;
  temperature: number | null;
  strict_base_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface ModelConfigInput {
  name: string;
  base_url: string;
  model_id: string;
  api_key?: string;
  temperature?: number | null;
  strict_base_url?: string | null;
}

export interface ModelTestResult {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
}

export interface RoleCatalogItem {
  role_id: string;
  display_name: string;
  name_zh: string;
  camp: string;
  icon: string;
  description: string;
  min_count: number;
  max_count: number | null;
  dependencies: string[];
  exclusions: string[];
}

export interface GamePreset {
  id: string;
  name: string;
  description: string;
  role_counts: Record<string, number>;
}

export interface FieldConstraints {
  min_players: number;
  max_players: number;
  min_werewolves: number;
  min_good: number;
}

export interface ModelAssignment {
  config_id: string | null;
  count: number;
}

export interface ModelSnapshotEntry {
  config_id: string | null;
  name: string;
  model_id: string;
  base_url: string;
}
