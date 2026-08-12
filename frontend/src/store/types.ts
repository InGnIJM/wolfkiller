// Public observer contracts. These types intentionally contain no role,
// camp, resource, or private night-action information.

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
  | 'game_over';

export type DeathCause = 'wolf_kill' | 'poison' | 'hunter_shot' | 'exile';
export type WinningCamp = 'good' | 'werewolf';
export type WinReason = 'all_gods_dead' | 'all_villagers_dead' | 'all_wolves_dead';

export interface PublicPlayerState {
  seat_number: number;
  is_alive: boolean;
  is_sheriff: boolean;
}

export interface SpeechRecord {
  player_seat: number;
  text: string;
  round_number: number;
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

export type PublicReplayEvent =
  | { event_type: 'speech'; payload: SpeechRecord }
  | { event_type: 'death'; payload: DeathRecord }
  | { event_type: 'vote'; payload: VoteRecord }
  | { event_type: 'vote_result'; payload: VoteResult }
  | { event_type: 'phase'; payload: { phase: GamePhase; round_number: number } }
  | { event_type: 'winner'; payload: WinResult };

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
