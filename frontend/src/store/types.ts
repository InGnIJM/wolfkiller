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

export type DeathCause =
  | 'wolf_kill'
  | 'poison'
  | 'hunter_shot'
  | 'exile'
  | 'self_explode'
  | 'crimson_explode'
  | 'dance_pool'
  | 'mask_granted'
  | 'knight_duel'
  | 'charm'
  | 'wolf_king_shot'
  | 'reflect'
  | 'dream_death'
  | 'verify_kill'
  | 'hunt'
  | 'dance';
export type WinningCamp = 'good' | 'werewolf';
export type WinReason =
  | 'all_gods_dead'
  | 'all_villagers_dead'
  | 'all_wolves_dead'
  | 'wolves_outnumber_good';
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
  | 'failed'
  | 'cancelled';

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

/** 舞者的舞池：当晚被邀请共舞的三名玩家（裁决在结算时给出）。 */
export interface DancePoolPayload {
  round_number: number;
  seat: number;
  pool_seats: number[];
}

/** 假面给某人戴面具，并附上「对方是否在舞池中」的查验结论。 */
export interface MaskGrantedPayload {
  round_number: number;
  seat: number;
  target_seat: number;
  in_pool: boolean;
}

/** 赤月使徒自爆：不带走任何人，改为封印当晚全部好人技能。 */
export interface CrimsonExplodePayload {
  round_number: number;
  seat: number;
  silenced_seats: number[];
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
  /** 警长的交/撕徽理由（上帝视角）；旧档没有这个字段。 */
  reasoning?: string | null;
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

/** Published when a seat starts generating speech, before the text exists. */
export interface SpeakingPayload {
  round_number: number;
  seat: number;
}

export type NightActionType =
  | 'werewolf_kill'
  | 'witch_save'
  | 'witch_poison'
  | 'seer_check'
  | 'hunter_shot'
  | 'wolf_king_shot'
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

export interface PlayerStatusPayload {
  round_number: number;
  player_seat: number;
  /** 结算留下的标记：poisoned / wounded / delayed_death 等，未知值原样显示。 */
  status: string;
}

export interface WolfChatMessagePayload {
  round_number: number;
  seat: number;
  text: string;
  /** 该次狼队发言里给出的次日计划，没有计划时不带这个字段。 */
  day_plan?: string;
}

export interface WolfVotePayload {
  round_number: number;
  seat: number;
  target_seat: number | null;
  reasoning: string;
}

/** Published when the knight flips and duels one seat after the speeches. */
export interface KnightDuelPayload {
  round_number: number;
  seat: number;
  target_seat: number;
  /** Camp of the challenged seat; the judge announces the verdict publicly. */
  camp: string;
}

/**
 * Wolf Beauty's charm. The audience is the god view, so the charmed seat is
 * published; it stays optional because archives recorded before the field
 * existed carry only the caster (and the players never receive this event).
 */
export interface WolfBeautyCharmPayload {
  round_number: number;
  seat: number;
  target_seat?: number | null;
}

export interface WolfBeautyRevengePayload {
  round_number: number;
  seat: number;
  target_seat: number;
  cause: DeathCause;
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
    | 'wolf_king_reasoning'
    | 'dream_weaver_reasoning'
    | 'gargoyle_reasoning'
    | 'witch_reasoning'
    | 'seer_reasoning'
    | 'guard_reasoning'
    | 'werewolf_king_reasoning'
    | 'knight_reasoning'
    | 'wolf_beauty_reasoning'
    | 'pure_white_reasoning'
    | 'wolf_witch_reasoning'
    | 'demon_hunter_reasoning'
    | 'crimson_envoy_reasoning'
    | 'nightmare_reasoning'
    | 'dancer_reasoning'
    | 'mask_reasoning';
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
  | PublicReplayEnvelope<'crimson_explode', CrimsonExplodePayload>
  | PublicReplayEnvelope<'dance_pool', DancePoolPayload>
  | PublicReplayEnvelope<'mask_granted', MaskGrantedPayload>
  | PublicReplayEnvelope<'knight_duel', KnightDuelPayload>
  | PublicReplayEnvelope<'wolf_beauty_charm', WolfBeautyCharmPayload>
  | PublicReplayEnvelope<'wolf_beauty_revenge', WolfBeautyRevengePayload>
  | PublicReplayEnvelope<'sheriff_elected', SheriffElectedPayload>
  | PublicReplayEnvelope<'sheriff_badge', SheriffBadgePayload>
  | PublicReplayEnvelope<'sheriff_run', SheriffRunPayload>
  | PublicReplayEnvelope<'sheriff_withdraw', SheriffWithdrawPayload>
  | PublicReplayEnvelope<'sheriff_vote', SheriffVotePayload>
  | PublicReplayEnvelope<'sheriff_side', SheriffSidePayload>
  | PublicReplayEnvelope<'speaking', SpeakingPayload>
  | PublicReplayEnvelope<'night_action', NightActionRecord>
  | PublicReplayEnvelope<'narration', NarrationPayload>
  | PublicReplayEnvelope<'player_status', PlayerStatusPayload>
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

/** The closed set of audience event types; text tables key off this union. */
export type PublicEventType = PublicReplayEvent['event_type'];

export interface AudienceEvent {
  seq: number;
  event_id: string;
  event_type: PublicEventType;
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
  total?: number;
  page?: number;
  page_size?: number;
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
  | { type: 'paused_state'; paused: boolean };

// ── Model config & game creation catalog ──────────────────────

/** Backend provider profile ids; `auto` infers one from the Base URL host. */
export type ProviderProfileId =
  | 'auto'
  | 'openai'
  | 'deepseek'
  | 'openrouter'
  | 'opencode'
  | 'opencode-go'
  | 'custom-openai'
  | 'openai-responses'
  | 'anthropic'
  | 'custom-anthropic';

/** 后端按历史模型请求统计的延迟分布；没有样本时为 `null`。 */
export interface ModelLatencyStats {
  samples: number;
  avg_ms: number;
  p50_ms: number;
  p90_ms: number;
}

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
  headers: Record<string, string>;
  /** 旧响应或测试 mock 可能没有这个字段。 */
  latency?: ModelLatencyStats | null;
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
  headers?: Record<string, string>;
}

export interface ModelTestResult {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
}

/**
 * 「训练语料采集」开关的生效值与来源。
 * `ui` = 本页开关覆盖；`env` = 环境变量 `PROMPT_CAPTURE_ENABLED`；`default` = 内建默认（开）。
 */
export interface PromptCaptureSetting {
  enabled: boolean;
  source: 'default' | 'env' | 'ui';
}

export interface RoleCatalogItem {
  role_id: string;
  display_name: string;
  name_zh: string;
  camp: string;
  icon: string;
  description: string;
  /** 玩家向的规则条目；旧响应或测试 mock 可能没有这个字段。 */
  rules?: string[];
  min_count: number;
  max_count: number | null;
  dependencies: string[];
  exclusions: string[];
}

/** 女巫自救规则：假面官方板子首夜可自救，网易官方赛制板子全程禁止。 */
export type WitchSelfSavePolicy = 'first_night_only' | 'never';

export interface GamePreset {
  id: string;
  name: string;
  description: string;
  role_counts: Record<string, number>;
  enable_sheriff?: boolean;
  /** 女巫自救规则：假面板子首夜可自救，网易官方赛制板子全程禁止。 */
  witch_self_save?: WitchSelfSavePolicy;
  /** 该板子建议的单局超时（秒）：9/10 人板 3600，12 人板 7200；旧响应可能没有这个字段。 */
  recommended_timeout_seconds?: number;
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
