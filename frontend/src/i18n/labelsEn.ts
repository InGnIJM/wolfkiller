/**
 * `labels.ts` 的英文镜像模块。
 *
 * 与 zh 源同形：每张表用同一个封闭联合键类型（`Record<DeathCause, string>`
 * 等），tsc 强制英文覆盖全部取值；函数签名逐字对齐。`useLabels()` 按当前
 * 语言在两个模块间切换，组件因此不需要知道自己在渲染哪种语言。
 *
 * 语言无关的导出（`ThoughtActionType`、`voteRoundWasRewound`）直接
 * re-export，保证两个模块的形状逐字节一致。
 */
import type {
  AwakenedWitchAssistVote,
  AwakenedWitchPoisonResult,
  DeathCause,
  ExecutionStatus,
  GamePhase,
  NightActionType,
  PublicEventType,
  SheriffSpeechSide,
  WinReason,
  WinningCamp,
} from '../store/types';
import type { ThoughtActionType } from '../labels';

export type { ThoughtActionType } from '../labels';
export { voteRoundWasRewound } from '../labels';

// ── Death ─────────────────────────────────────────────────────

export const DEATH_CAUSE_LABELS: Record<DeathCause, string> = {
  wolf_kill: 'Killed at night',
  poison: 'Poisoned',
  exile: 'Exiled',
  hunter_shot: 'Taken by the Hunter',
  self_explode: 'White Wolf King self-destruct',
  knight_duel: "Knight's duel",
  charm: 'Bound by love',
  wolf_king_shot: 'Taken by the Wolf King',
  reflect: 'Reflected damage',
  dream_death: 'Dreamt away',
  verify_kill: 'Killed by verdict',
  hunt: 'Hunted down',
  dance: 'Dance pool elimination',
  awakened_hunt: 'Awakened hunt',
  stand_in: 'Died as a stand-in',
  bond_death: 'Bound companion death',
  miracle_backlash: 'Miracle backlash',
  radiance_backlash: 'Radiance backlash',
  love_death: 'Followed a beloved',
};

// ── Phase ─────────────────────────────────────────────────────

export const PHASE_LABELS: Record<GamePhase, string> = {
  waiting: 'Waiting',
  role_deal: 'Dealing roles',
  night: 'Night',
  dawn: 'Dawn',
  last_words: 'Last words',
  sheriff_election: 'Sheriff election',
  speech: 'Speeches',
  vote_casting: 'Voting',
  vote_resolution: 'Revealing votes',
  game_over: 'Game over',
  error: 'Aborted',
};

export const CENTER_PHASE_LABELS: Record<GamePhase, string> = {
  waiting: 'The feast awaits',
  role_deal: 'Roles dealt',
  night: 'Night',
  dawn: 'Day breaks',
  last_words: 'Last words',
  sheriff_election: 'Sheriff election',
  speech: 'Day · Speeches',
  vote_casting: 'Day · Voting',
  vote_resolution: 'Day · Reveal',
  game_over: 'Dust settled',
  error: 'Game error',
};

// ── Night actions & thoughts ──────────────────────────────────

export const NIGHT_ACTION_LABELS: Record<NightActionType, string> = {
  werewolf_kill: 'Werewolf attack',
  witch_save: 'Witch saves',
  witch_poison: 'Witch poisons',
  seer_check: 'Seer checks',
  hunter_shot: 'Hunter shoots',
  wolf_king_shot: 'Wolf King shoots',
  guard_protect: 'Guard protects',
  awakened_hunt: 'Awakened Hunter patrols',
  fool_seer_check: 'Fool Seer checks',
};

/** Past-tense verbs: "Seat 5 Witch · saved · target Seat 3". */
export const NIGHT_ACTION_VERBS: Record<NightActionType, string> = {
  werewolf_kill: 'attacked',
  witch_save: 'saved',
  witch_poison: 'poisoned',
  seer_check: 'checked',
  hunter_shot: 'shot',
  wolf_king_shot: 'shot',
  guard_protect: 'protected',
  awakened_hunt: 'patrolled',
  fool_seer_check: 'checked',
};

export const VOTE_REWOUND_RESULT = 'Exile vote voided; voting again';

export function awakenedWitchVoteLabel(vote: AwakenedWitchAssistVote): string {
  switch (vote) {
    case 'agree': return 'Agree';
    case 'veto': return 'Veto';
    case 'pass': return 'Abstain';
    default: return vote;
  }
}

export function awakenedWitchPoisonResultLabel(result: AwakenedWitchPoisonResult): string {
  switch (result) {
    case 'passed': return 'Effective';
    case 'vetoed': return 'Vetoed';
    case 'incomplete': return 'Incomplete';
    case 'aborted': return 'Aborted';
    default: return result;
  }
}

export function nightActionCaption(
  actionType: NightActionType,
  options: {
    seat?: number | null;
    targetSeat?: number | null;
    intendedSeat?: number | null;
    roleName?: string | null;
  } = {},
): string {
  const verb = NIGHT_ACTION_VERBS[actionType];
  const fallback = NIGHT_ACTION_LABELS[actionType] ?? 'Night action';
  const who = options.seat != null
    ? (options.roleName ? `Seat ${options.seat} ${options.roleName}` : `Seat ${options.seat}`)
    : null;
  const skill = who && verb ? `${who} · ${verb}` : fallback;
  const target = options.targetSeat != null ? ` · target Seat ${options.targetSeat}` : '';
  const redirect = (
    options.intendedSeat != null
    && options.targetSeat != null
    && options.intendedSeat !== options.targetSeat
  ) ? ` (intended Seat ${options.intendedSeat}, redirected by time lock)` : '';
  return `${skill}${target}${redirect}`;
}

export function thoughtActorName(
  actionType: ThoughtActionType,
  options: { roleName?: string | null } = {},
): string {
  if (actionType === 'lucky_reasoning') return 'Lucky One';
  return options.roleName || THOUGHT_ROLE_NAMES[actionType] || 'Role';
}

export const THOUGHT_LABELS: Record<ThoughtActionType, string> = {
  witch_reasoning: 'Witch thinking',
  seer_reasoning: 'Seer thinking',
  hunter_reasoning: 'Hunter thinking',
  wolf_king_reasoning: 'Wolf King thinking',
  dream_weaver_reasoning: 'Dream Weaver thinking',
  gargoyle_reasoning: 'Gargoyle thinking',
  guard_reasoning: 'Guard thinking',
  werewolf_king_reasoning: 'White Wolf King thinking',
  knight_reasoning: 'Knight thinking',
  wolf_beauty_reasoning: 'Wolf Beauty thinking',
  pure_white_reasoning: 'Pure White thinking',
  wolf_witch_reasoning: 'Wolf Witch thinking',
  demon_hunter_reasoning: 'Demon Hunter thinking',
  crimson_envoy_reasoning: 'Crimson Envoy thinking',
  nightmare_reasoning: 'Nightmare thinking',
  dancer_reasoning: 'Dancer thinking',
  mask_reasoning: 'Mask thinking',
  awakened_hunter_reasoning: 'Awakened Hunter thinking',
  awakened_beauty_reasoning: 'Awakened Wolf Beauty thinking',
  awakened_seer_reasoning: 'Awakened Seer thinking',
  fragrance_phantom_reasoning: 'Fragrance Phantom thinking',
  miracle_merchant_reasoning: 'Miracle Merchant thinking',
  lucky_reasoning: 'Lucky One thinking',
  mirror_girl_reasoning: 'Mirror Girl thinking',
  awakened_hidden_wolf_reasoning: 'Awakened Hidden Wolf thinking',
  radiance_earl_reasoning: 'Radiance Earl thinking',
  eclipse_maid_reasoning: 'Eclipse Maid thinking',
  time_wolf_reasoning: 'Time Wolf thinking',
  order_prince_reasoning: 'Order Prince thinking',
  fool_seer_reasoning: 'Fool Seer thinking',
  fool_seer_reveal_reasoning: 'Fool Seer reveals',
  iron_villager_reasoning: 'Iron Villager reveals',
  crow_reasoning: 'Crow thinking',
  cupid_reasoning: 'Cupid thinking',
  admirer_reasoning: 'Admirer thinking',
  witch_thought: 'Witch thinking',
  seer_thought: 'Seer thinking',
};

export const NIGHT_ACTION_ROLE_NAMES: Record<NightActionType, string> = {
  werewolf_kill: 'Werewolf',
  seer_check: 'Seer',
  witch_save: 'Witch',
  witch_poison: 'Witch',
  guard_protect: 'Guard',
  hunter_shot: 'Hunter',
  wolf_king_shot: 'Wolf King',
  awakened_hunt: 'Awakened Hunter',
  fool_seer_check: 'Fool Seer',
};

export const NIGHT_EVENT_ROLE_NAMES: Partial<Record<PublicEventType, string>> = {
  dance_pool: 'Dancer',
  mask_granted: 'Mask',
  wraith_illusion: 'Awakened Wolf Beauty',
  fragrance_bond: 'Fragrance Phantom',
  awakened_witch_assist_pick: 'Awakened Witch',
  awakened_witch_assist_chat: 'Awakened Witch',
  awakened_witch_assist_vote: 'Awakened Witch',
  awakened_witch_poison_result: 'Awakened Witch',
};

export const THOUGHT_ROLE_NAMES: Record<ThoughtActionType, string> = {
  witch_thought: 'Witch',
  witch_reasoning: 'Witch',
  seer_thought: 'Seer',
  seer_reasoning: 'Seer',
  hunter_reasoning: 'Hunter',
  wolf_king_reasoning: 'Wolf King',
  dream_weaver_reasoning: 'Dream Weaver',
  gargoyle_reasoning: 'Gargoyle',
  guard_reasoning: 'Guard',
  werewolf_king_reasoning: 'White Wolf King',
  knight_reasoning: 'Knight',
  wolf_beauty_reasoning: 'Wolf Beauty',
  pure_white_reasoning: 'Pure White',
  wolf_witch_reasoning: 'Wolf Witch',
  demon_hunter_reasoning: 'Demon Hunter',
  crimson_envoy_reasoning: 'Crimson Envoy',
  nightmare_reasoning: 'Nightmare',
  dancer_reasoning: 'Dancer',
  mask_reasoning: 'Mask',
  awakened_hunter_reasoning: 'Awakened Hunter',
  awakened_beauty_reasoning: 'Awakened Wolf Beauty',
  awakened_seer_reasoning: 'Awakened Seer',
  fragrance_phantom_reasoning: 'Fragrance Phantom',
  miracle_merchant_reasoning: 'Miracle Merchant',
  lucky_reasoning: 'Lucky One',
  mirror_girl_reasoning: 'Mirror Girl',
  awakened_hidden_wolf_reasoning: 'Awakened Hidden Wolf',
  radiance_earl_reasoning: 'Radiance Earl',
  eclipse_maid_reasoning: 'Eclipse Maid',
  time_wolf_reasoning: 'Time Wolf',
  order_prince_reasoning: 'Order Prince',
  fool_seer_reasoning: 'Fool Seer',
  fool_seer_reveal_reasoning: 'Fool Seer',
  iron_villager_reasoning: 'Iron Villager',
  crow_reasoning: 'Crow',
  cupid_reasoning: 'Cupid',
  admirer_reasoning: 'Admirer',
};

// ── Chronicle ─────────────────────────────────────────────────

export const DEGRADED_ACTION_LABEL = 'System fallback';

export const EVENT_TAGS: Record<PublicEventType, string> = {
  speech: 'Speech',
  vote: 'Vote',
  vote_result: 'Exile',
  vote_rewound: 'Rewind',
  technical_abstain: 'Auto-abstain',
  exile_cancelled: 'Revealed',
  self_explode: 'Self-destruct',
  crimson_explode: 'Self-destruct',
  dance_pool: 'Dance pool',
  mask_granted: 'Mask',
  awakened_witch_assist_pick: 'Poison pick',
  awakened_witch_assist_chat: 'Assist whisper',
  awakened_witch_assist_vote: 'Assist vote',
  awakened_witch_poison_result: 'Poison result',
  knight_duel: 'Duel',
  wolf_beauty_charm: 'Charm',
  wraith_illusion: 'Illusion',
  fragrance_bond: 'Bond',
  wolf_beauty_revenge: 'Revenge',
  sheriff_elected: 'Sheriff',
  sheriff_badge: 'Badge',
  sheriff_run: 'Candidacy',
  sheriff_withdraw: 'Withdraw',
  sheriff_vote: 'Ballot',
  sheriff_side: 'Speech order',
  speaking: 'Speaking',
  night_action: 'Night',
  narration: 'Narration',
  commentary: 'Commentary',
  player_status: 'Status',
  wolf_chat_message: 'Wolf chat',
  wolf_vote: 'Wolf vote',
  witch_thought: 'Thought',
  seer_thought: 'Thought',
  night_thought: 'Thought',
  death: 'Death',
  phase: 'Phase',
  winner: 'Result',
  game_initialized: 'Start',
  player_revealed: 'Revealed',
  execution_state: 'State',
};

// ── Execution ─────────────────────────────────────────────────

export const BENCHMARK_STATUS_LABELS: Record<string, string> = {
  draft: 'Draft', pending: 'Pending', running: 'Running', pausing: 'Pausing',
  paused: 'Paused', interrupted: 'Interrupted', blocked: 'Blocked',
  completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled',
};

export const EXECUTION_LABELS: Record<ExecutionStatus, string> = {
  running: 'Running',
  paused: 'Paused',
  interrupted: 'Interrupted',
  recovery_blocked: 'Recovery blocked',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export const RECOVERY_BLOCK_LABELS: Record<string, string> = {
  checkpoint_missing: 'Checkpoint missing',
  checkpoint_corrupt: 'Checkpoint corrupt',
  checkpoint_version_unsupported: 'Checkpoint version unsupported',
  registry_incompatible: 'Role or contract no longer exists',
  contract_incompatible: 'Contract version incompatible',
  role_declaration_drift: 'Role declaration changed',
  contract_digest_drift: 'Contract content changed',
  registry_identity_unknown: 'Missing registry identity',
  prompt_drift: 'Prompts updated',
  model_drift: 'Model parameters changed',
  model_config_missing: 'Model config missing',
  model_key_unavailable: 'Model key unavailable',
  registry_mismatch: 'Registry mismatch (legacy)',
  legacy_archive: 'Legacy archive without durable checkpoint',
};

// ── Victory ───────────────────────────────────────────────────

export const WIN_REASON_LABELS: Record<WinReason, string> = {
  all_wolves_dead: 'All werewolves eliminated',
  all_gods_dead: 'All gods eliminated',
  all_villagers_dead: 'All villagers eliminated',
  wolves_outnumber_good: 'Werewolves reached parity',
  all_good_dead: 'All good players eliminated',
  all_others_dead: 'All other players eliminated',
};

export const WINNER_LABELS: Record<WinningCamp, string> = {
  good: 'Good wins',
  werewolf: 'Werewolves win',
  third_party: 'Third party wins',
};

export const CAMP_LABELS: Record<WinningCamp, string> = {
  good: 'Good',
  werewolf: 'Werewolves',
  third_party: 'Third party',
};

export const WINNING_CAMP_LABELS: Record<WinningCamp, string> = {
  good: 'Good camp',
  werewolf: 'Werewolf camp',
  third_party: 'Third-party camp',
};

// ── Remaining tables ──────────────────────────────────────────

export const SHERIFF_SIDE_LABELS: Record<SheriffSpeechSide, string> = {
  sheriff_left: 'Sheriff left',
  sheriff_right: 'Sheriff right',
  death_left: 'Left of the dead',
  death_right: 'Right of the dead',
};

export const PLAYER_STATUS_LABELS: Record<string, string> = {
  poisoned: 'Poisoned (alive)',
  wounded: 'Shot (alive)',
  delayed_death: 'Delayed death',
  hunted: 'Hunted',
  silenced: 'Skill sealed',
  time_locked: 'Time-locked',
};

export const MEMORY_ACTION_LABELS: Record<string, string> = {
  kill: 'Wolf kill',
  check: 'Check',
  save: 'Save',
  poison: 'Poison',
  pass: 'Pass',
};

export const MEMORY_EVENT_LABELS: Record<string, string> = {
  death: 'Death',
};

export const KNOWLEDGE_LABELS: Record<string, string> = {
  teammates: 'Wolf teammates',
  check_results: 'Check results',
  has_antidote: 'Antidote',
  has_poison: 'Poison',
  has_gun: 'Gun',
  last_wolf_kill_target: "Last night's wolf target",
};

// ── Role names ────────────────────────────────────────────────

export const ROLE_NAME_LABELS: Record<string, string> = {
  'wolf-killer-werewolf': 'Werewolf',
  'wolf-killer-witch': 'Witch',
  'wolf-killer-awakened-witch': 'Awakened Witch',
  'wolf-killer-seer': 'Seer',
  'wolf-killer-hunter': 'Hunter',
  'wolf-killer-awakened-hunter': 'Awakened Hunter',
  'wolf-killer-villager': 'Villager',
  'wolf-killer-guard': 'Guard',
  'wolf-killer-idiot': 'Idiot',
  'wolf-killer-werewolf-king': 'White Wolf King',
  'wolf-killer-wolf-king': 'Wolf King',
  'wolf-killer-awakened-wolf-king': 'Awakened Wolf King',
  'wolf-killer-evil-knight': 'Evil Knight',
  'wolf-killer-dream-weaver': 'Dream Weaver',
  'wolf-killer-gargoyle': 'Gargoyle',
  'wolf-killer-gravekeeper': 'Gravekeeper',
  'wolf-killer-knight': 'Knight',
  'wolf-killer-wolf-beauty': 'Wolf Beauty',
  'wolf-killer-awakened-wolf-beauty': 'Awakened Wolf Beauty',
  'wolf-killer-old-drunkard': 'Old Drunkard',
  'wolf-killer-lonely-girl': 'Lonely Girl',
  'wolf-killer-awakened-seer': 'Awakened Seer',
  'wolf-killer-fragrance-phantom': 'Fragrance Phantom',
  'wolf-killer-pure-white': 'Pure White',
  'wolf-killer-wolf-witch': 'Wolf Witch',
  'wolf-killer-crimson-envoy': 'Crimson Envoy',
  'wolf-killer-nightmare': 'Nightmare',
  'wolf-killer-mask': 'Mask',
  'wolf-killer-demon-hunter': 'Demon Hunter',
  'wolf-killer-dancer': 'Dancer',
  'wolf-killer-miracle-merchant': 'Miracle Merchant',
  'wolf-killer-mirror-girl': 'Mirror Girl',
  'wolf-killer-awakened-hidden-wolf': 'Awakened Hidden Wolf',
  'wolf-killer-radiance-earl': 'Radiance Earl',
  'wolf-killer-eclipse-maid': 'Eclipse Maid',
  'wolf-killer-time-wolf': 'Time Wolf',
  'wolf-killer-order-prince': 'Order Prince',
  'wolf-killer-fool-seer': 'Fool Seer',
  'wolf-killer-iron-villager': 'Iron Villager',
  'wolf-killer-crow': 'Crow',
  'wolf-killer-cupid': 'Cupid',
  'wolf-killer-admirer': 'Admirer',
};

export function roleName(roleId: string | null | undefined): string | undefined {
  if (!roleId) return undefined;
  return ROLE_NAME_LABELS[roleId];
}

export function recoveryBlockLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return RECOVERY_BLOCK_LABELS[code] ?? code;
}

export function roleLabelForSeat(
  seat: number | null | undefined,
  players: Record<number, { role?: string }> | undefined,
): string | undefined {
  if (seat == null || players == null) return undefined;
  return roleName(players[seat]?.role);
}
