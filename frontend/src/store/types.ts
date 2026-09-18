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

export type DeathCause = 'wolf_kill' | 'poison' | 'hunter_shot' | 'exile' | 'self_explode';
export type WinningCamp = 'good' | 'werewolf';
export type WinReason = 'all_gods_dead' | 'all_villagers_dead' | 'all_wolves_dead';
export type UtcTimestamp = `${string}Z`;

export interface PublicPlayerState {
  seat_number: number;
  is_alive: boolean;
  is_sheriff: boolean;
  role?: string;
  camp?: string;
  revealed_role?: string | null;
  /** False once a flipped card stripped this seat of its ballot. */
  can_vote?: boolean;
}

export type ExecutionStatus =
  | 'running'
  | 'paused'
  | 'interrupted'
  | 'recovery_blocked'
  | 'completed'
  | 'failed';

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

export interface TechnicalAbstainPayload {
  voter_seat: number;
  round_number: number;
  failure_code: string;
}

/** The vote picked a seat that flipped its card and stayed in the game. */
export interface ExileCancelledPayload {
  round_number: number;
  target_seat: number;
}

/** A daytime self-destruct that took another seat along. */
export interface SelfExplodePayload {
  round_number: number;
  seat: number;
  target_seat: number;
}

export interface SheriffElectedPayload {
  round_number: number;
  seat: number | null;
  reason: string;
}

export interface SheriffBadgePayload {
  round_number: number;
  from_seat: number;
  to_seat: number | null;
}

export type SheriffRunChoice = 'run' | 'pass';
export type SheriffWithdrawChoice = 'stay' | 'withdraw';
export type SheriffVoteKind = 'vote' | 'pk';
export type SheriffSpeechSide =
  | 'sheriff_left'
  | 'sheriff_right'
  | 'death_left'
  | 'death_right';

export interface SheriffRunPayload {
  round_number: number;
  seat: number;
  choice: SheriffRunChoice;
}

export interface SheriffWithdrawPayload {
  round_number: number;
  seat: number;
  choice: SheriffWithdrawChoice;
}

export interface SheriffVotePayload {
  round_number: number;
  voter_seat: number;
  target_seat: number | null;
  kind: SheriffVoteKind;
}

export interface SheriffSidePayload {
  round_number: number;
  seat: number;
  side: SheriffSpeechSide;
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
  action_type:
    | 'hunter_reasoning'
    | 'witch_reasoning'
    | 'seer_reasoning'
    | 'guard_reasoning'
    | 'werewolf_king_reasoning';
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
  reveal_on_death?: boolean;
  enable_sheriff?: boolean;
  players: Record<number, PublicPlayerState>;
  sheriff: number | null;
  speeches: SpeechRecord[];
  death_history: DeathRecord[];
  win_result: WinResult | null;
  execution_status?: ExecutionStatus;
  recoverable?: boolean;
  recovery_block_code?: string | null;
  model_snapshot?: ModelSnapshotEntry[];
}

type PublicReplayEnvelope<TType extends string, TPayload> = {
  timestamp: UtcTimestamp;
  event_type: TType;
  payload: TPayload;
  seq?: number;
  event_id?: string;
  schema_version?: number;
};

export type PublicReplayEvent =
  | PublicReplayEnvelope<'speech', SpeechRecord>
  | PublicReplayEnvelope<'death', DeathRecord>
  | PublicReplayEnvelope<'vote', VoteRecord>
  | PublicReplayEnvelope<'vote_result', VoteResult>
  | PublicReplayEnvelope<'technical_abstain', TechnicalAbstainPayload>
  | PublicReplayEnvelope<'exile_cancelled', ExileCancelledPayload>
  | PublicReplayEnvelope<'self_explode', SelfExplodePayload>
  | PublicReplayEnvelope<'sheriff_elected', SheriffElectedPayload>
  | PublicReplayEnvelope<'sheriff_badge', SheriffBadgePayload>
  | PublicReplayEnvelope<'sheriff_run', SheriffRunPayload>
  | PublicReplayEnvelope<'sheriff_withdraw', SheriffWithdrawPayload>
  | PublicReplayEnvelope<'sheriff_vote', SheriffVotePayload>
  | PublicReplayEnvelope<'sheriff_side', SheriffSidePayload>
  | PublicReplayEnvelope<'night_action', NightActionRecord>
  | PublicReplayEnvelope<'narration', NarrationPayload>
  | PublicReplayEnvelope<'wolf_chat_message', WolfChatMessagePayload>
  | PublicReplayEnvelope<'wolf_vote', WolfVotePayload>
  | PublicReplayEnvelope<'witch_thought' | 'seer_thought', ThoughtPayload>
  | PublicReplayEnvelope<'night_thought', NightThoughtPayload>
  | PublicReplayEnvelope<'phase', { phase: GamePhase; round_number: number }>
  | PublicReplayEnvelope<'winner', WinResult>
  | PublicReplayEnvelope<'game_initialized', {
      players?: Record<string, PublicPlayerState> | PublicPlayerState[];
      config?: { reveal_on_death?: boolean; enable_sheriff?: boolean };
    }>
  | PublicReplayEnvelope<'player_revealed', {
      seat_number: number;
      role: string;
      camp: string;
    }>
  | PublicReplayEnvelope<'execution_state', {
      execution_status: ExecutionStatus;
      recoverable: boolean;
      recovery_block_code: string | null;
    }>;

export interface AudienceEvent {
  seq: number;
  event_id: string;
  event_type: PublicReplayEvent['event_type'];
  payload: Record<string, unknown>;
  schema_version: number;
  created_at?: string;
  timestamp?: string;
}

export interface AudienceSnapshot {
  game_id: string;
  seq: number;
  state: PublicGameState;
  projection_version: number;
  state_digest?: string;
}

export interface AudienceEventPage {
  game_id: string;
  after_seq: number;
  through_seq?: number | null;
  last_seq: number;
  high_watermark?: number;
  events: AudienceEvent[];
  caught_up: boolean;
}

export interface GameLogs {
  game_id: string;
  events: PublicReplayEvent[];
}

export interface GameListItem {
  game_id: string;
  name: string;
  phase: GamePhase;
  round_number: number;
  player_count: number;
  alive_count: number;
  winner: WinningCamp | null;
  execution_status?: ExecutionStatus;
  recoverable?: boolean;
  recovery_block_code?: string | null;
  source?: 'native' | 'legacy' | 'benchmark' | string;
  folder_id?: string | null;
}

export interface GameListResponse {
  games: GameListItem[];
}

export interface GameFolder {
  folder_id: string;
  name: string;
  game_count: number;
  created_at: string;
  updated_at: string;
}

export interface BatchFailure {
  game_id: string;
  code: string;
  message: string;
}

export interface BatchDeleteResult {
  deleted: string[];
  failed: BatchFailure[];
}

export interface BatchMoveResult {
  moved: string[];
  failed: BatchFailure[];
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

/** Backend provider profile ids; `auto` infers one from the Base URL host. */
export type ProviderProfileId =
  | 'auto'
  | 'openai'
  | 'deepseek'
  | 'openrouter'
  | 'custom-openai'
  | 'anthropic'
  | 'custom-anthropic';

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
  provider_profile: ProviderProfileId;
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
  provider_profile?: ProviderProfileId;
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
  enable_sheriff?: boolean;
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
  provider_profile: string;
  count: number;
  seats: number[];
}

// Benchmark contracts intentionally accept additive server fields. Reports are
// versioned JSON and the UI renders the stable summary subset defensively.
export type BenchmarkMode = 'mixed_arena' | 'paired_regression';
export type BenchmarkStatus =
  | 'draft'
  | 'pending'
  | 'running'
  | 'pausing'
  | 'paused'
  | 'interrupted'
  | 'blocked'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface BenchmarkRun {
  run_id: string;
  client_request_id: string;
  name: string;
  mode: BenchmarkMode;
  status: BenchmarkStatus;
  config: Record<string, unknown>;
  schedule_digest?: string;
  planned_count?: number;
  terminal_count?: number;
  completed_count?: number;
  failed_count?: number;
  created_at: string;
  updated_at: string;
  [key: string]: unknown;
}

export interface BenchmarkRunList {
  runs: BenchmarkRun[];
  total?: number;
}

export interface BenchmarkItem {
  run_id: string;
  item_index: number;
  scenario_id: string;
  pair_id: string | null;
  block_index: number;
  assignment: Record<string, unknown>;
  game_id: string | null;
  status: string;
  terminal_reason: string | null;
  created_at?: string;
  updated_at?: string;
  event_seq?: number | null;
  name?: string | null;
  phase?: string | null;
  round_number?: number | null;
  player_count?: number | null;
  alive_count?: number | null;
  winner?: string | null;
  execution_status?: string | null;
  [key: string]: unknown;
}

export interface BenchmarkItemsPage {
  games: BenchmarkItem[];
  total?: number;
}

export interface BenchmarkReport {
  status?: 'pending' | 'ready' | string;
  metric_version?: string;
  input_digest?: string;
  generated_at?: string;
  provisional?: boolean;
  summary?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  uncertainty?: Record<string, unknown>;
  data_quality?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface BenchmarkCreateInput {
  client_request_id: string;
  name: string;
  mode: BenchmarkMode;
  seed: number;
  scenario: {
    scenario_id: string;
    role_counts: Record<string, number>;
  };
  games?: number;
  repetitions?: number;
  models?: Array<{ model_config_id: string }>;
  baseline?: { model_config_id: string; temperature?: number };
  candidate?: { model_config_id: string; temperature?: number };
  concurrency?: number;
  game_timeout_seconds?: number;
  max_games?: number;
  block_count?: number;
  max_attempts_per_game?: number;
}
