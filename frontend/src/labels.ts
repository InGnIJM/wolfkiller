/**
 * 后端机器值 → 中文文案的唯一来源。
 *
 * 这些表原先散落在各个组件里，同一套词汇被抄了多份，缺键时兜底会把英文机器值
 * （`wolves_outnumber_good`、`sheriff_election`）直接显示给用户。收敛到这里的
 * 规矩：
 *
 * - 每个命名空间按**渲染语境**划分。同一个机器值在不同语境下文案不同时，各自
 *   保留一份（例如 `PHASE_LABELS.night` 是「黑夜」，`CENTER_PHASE_LABELS.night`
 *   也是「黑夜」但 `waiting` 在中央面板是氛围化的「夜宴将启」）。
 * - 键类型尽量收紧到 `store/types` 里的联合类型：联合缺值时先补类型定义（`tsc`
 *   会在缺键时报错，这正是门禁），确实开放的机器值（存档里的记忆、
 *   `player_status` 等未知值要原样显示）才退回 `string`。
 * - 只放文案。样式映射（`CAMP_ACCENT`）、图标表（`CAUSE_ICONS`）、判定集合
 *   （`TERMINAL`）留在组件里。
 */
import type {
  DeathCause,
  ExecutionStatus,
  GamePhase,
  NightActionType,
  NightThoughtPayload,
  PublicEventType,
  SheriffSpeechSide,
  WinReason,
  WinningCamp,
} from './store/types';

/** 夜间思考事件的键：新档 `night_thought` 的 7 个 reasoning + 旧档两类 thought。 */
export type ThoughtActionType =
  | NightThoughtPayload['action_type']
  | 'witch_thought'
  | 'seer_thought';

// ── 死亡 ──────────────────────────────────────────────────────

/** 四个渲染面（活动卡 / 中央面板 / 死亡公告 / 编年史）共用的死因文案。 */
export const DEATH_CAUSE_LABELS: Record<DeathCause, string> = {
  wolf_kill: '夜间死亡',
  poison: '毒杀',
  exile: '被放逐',
  hunter_shot: '猎人带走',
  self_explode: '白狼王自爆',
  knight_duel: '骑士决斗',
  charm: '殉情',
  wolf_king_shot: '狼王带走',
  reflect: '反伤',
  dream_death: '摄梦',
  verify_kill: '验杀',
  hunt: '狩猎出局',
  dance: '舞池出局',
};

// ── 阶段 ──────────────────────────────────────────────────────

/** 大厅列表 / 时间轴 / 评测详情的阶段文案。 */
export const PHASE_LABELS: Record<GamePhase, string> = {
  waiting: '等待中',
  role_deal: '分配角色',
  night: '黑夜',
  dawn: '天亮',
  last_words: '遗言',
  sheriff_election: '警长竞选',
  speech: '发言',
  vote_casting: '投票',
  vote_resolution: '公布结果',
  game_over: '已结束',
  error: '异常终止',
};

/** 中央舞台的叙事化阶段文案（「夜宴将启」「白昼降临」…），与列表文案刻意不同。 */
export const CENTER_PHASE_LABELS: Record<GamePhase, string> = {
  waiting: '夜宴将启',
  role_deal: '身份落定',
  night: '黑夜',
  dawn: '白昼降临',
  last_words: '遗言时刻',
  sheriff_election: '警长竞选',
  speech: '白天 · 发言',
  vote_casting: '白天 · 投票',
  vote_resolution: '白天 · 揭票',
  game_over: '尘埃落定',
  error: '对局异常',
};

// ── 夜间动作与思考 ────────────────────────────────────────────

/** 夜间动作长文案（中央面板 / 编年史共用）。 */
export const NIGHT_ACTION_LABELS: Record<NightActionType, string> = {
  werewolf_kill: '狼人行动',
  witch_save: '女巫救人',
  witch_poison: '女巫毒人',
  seer_check: '预言家查验',
  hunter_shot: '猎人开枪',
  wolf_king_shot: '狼王开枪',
  guard_protect: '守卫守护',
};

/** 思考事件长文案（活动卡 / 编年史共用）。 */
export const THOUGHT_LABELS: Record<ThoughtActionType, string> = {
  witch_reasoning: '女巫思考',
  seer_reasoning: '预言家思考',
  hunter_reasoning: '猎人思考',
  wolf_king_reasoning: '狼王思考',
  dream_weaver_reasoning: '摄梦人思考',
  gargoyle_reasoning: '石像鬼思考',
  guard_reasoning: '守卫思考',
  werewolf_king_reasoning: '白狼王思考',
  knight_reasoning: '骑士思考',
  wolf_beauty_reasoning: '狼美人思考',
  pure_white_reasoning: '纯白之女思考',
  wolf_witch_reasoning: '狼巫思考',
  witch_thought: '女巫思考',
  seer_thought: '预言家思考',
};

/** 夜间动作 → 角色短名（中央面板的「黑夜 · 狼人」眉标）。 */
export const NIGHT_ACTION_ROLE_NAMES: Record<NightActionType, string> = {
  werewolf_kill: '狼人',
  seer_check: '预言家',
  witch_save: '女巫',
  witch_poison: '女巫',
  guard_protect: '守卫',
  hunter_shot: '猎人',
  wolf_king_shot: '狼王',
};

/** 思考事件 → 角色短名，和上表同为「正在行动的是谁」，共用同一套短名。 */
export const THOUGHT_ROLE_NAMES: Record<ThoughtActionType, string> = {
  witch_thought: '女巫',
  witch_reasoning: '女巫',
  seer_thought: '预言家',
  seer_reasoning: '预言家',
  hunter_reasoning: '猎人',
  wolf_king_reasoning: '狼王',
  dream_weaver_reasoning: '摄梦人',
  gargoyle_reasoning: '石像鬼',
  guard_reasoning: '守卫',
  werewolf_king_reasoning: '白狼王',
  knight_reasoning: '骑士',
  wolf_beauty_reasoning: '狼美人',
  pure_white_reasoning: '纯白之女',
  wolf_witch_reasoning: '狼巫',
};

// ── 编年史 ────────────────────────────────────────────────────

/**
 * 编年史条目的短标签。**独立于死因表**：同一个事件类型（例如 `self_explode`）
 * 在死因列里是「白狼王自爆」，在标签列里是「自爆」。四个只驱动 store 的类型
 * （开局座位、翻牌、执行状态、系统代投）也要在这里表态，否则 `tsc` 报缺键。
 */
export const EVENT_TAGS: Record<PublicEventType, string> = {
  speech: '发言',
  vote: '投票',
  vote_result: '放逐',
  technical_abstain: '系统代投',
  exile_cancelled: '翻牌',
  self_explode: '自爆',
  knight_duel: '决斗',
  wolf_beauty_charm: '魅惑',
  wolf_beauty_revenge: '殉情',
  sheriff_elected: '警长',
  sheriff_badge: '警徽',
  sheriff_run: '上警',
  sheriff_withdraw: '退水',
  sheriff_vote: '警票',
  sheriff_side: '警序',
  speaking: '开口',
  night_action: '夜间',
  narration: '旁白',
  player_status: '状态',
  wolf_chat_message: '狼聊',
  wolf_vote: '狼票',
  witch_thought: '思考',
  seer_thought: '思考',
  night_thought: '思考',
  death: '死亡',
  phase: '阶段',
  winner: '结局',
  game_initialized: '开局',
  player_revealed: '公开身份',
  execution_state: '执行状态',
};

// ── 执行状态 ──────────────────────────────────────────────────

/** 大厅卡片 / 对局页的执行状态（`cancelled` 是后端已有、前端联合曾缺的取值）。 */
export const EXECUTION_LABELS: Record<ExecutionStatus, string> = {
  running: '运行中',
  paused: '已暂停',
  interrupted: '已中断',
  recovery_blocked: '恢复受阻',
  completed: '已完成',
  failed: '执行失败',
  cancelled: '已取消',
};

/** 恢复受阻的原因码 → 提示文案；未知码原样显示（后端码才是稳定契约）。 */
export const RECOVERY_BLOCK_LABELS: Record<string, string> = {
  checkpoint_missing: '找不到检查点',
  checkpoint_corrupt: '检查点损坏',
  checkpoint_version_unsupported: '检查点版本不受支持',
  registry_incompatible: '角色或契约已不存在',
  contract_incompatible: '契约版本不兼容',
  role_declaration_drift: '角色声明已变更',
  contract_digest_drift: '契约内容已变更',
  registry_identity_unknown: '缺少角色注册表身份',
  prompt_drift: '提示词已更新',
  model_drift: '模型参数已变更',
  model_config_missing: '模型配置缺失',
  model_key_unavailable: '模型密钥不可用',
  registry_mismatch: '注册表不一致（旧代码）',
  legacy_archive: '旧档，没有耐久检查点',
};

// ── 胜负 ──────────────────────────────────────────────────────

/** 胜因（结算弹窗）。 */
export const WIN_REASON_LABELS: Record<WinReason, string> = {
  all_wolves_dead: '所有狼人出局',
  all_gods_dead: '所有神职出局',
  all_villagers_dead: '所有平民出局',
  wolves_outnumber_good: '狼人人数过半',
};

/** 阵营的胜负说法（大厅徽标 / 评测表）。 */
export const WINNER_LABELS: Record<WinningCamp, string> = {
  good: '好人胜',
  werewolf: '狼人胜',
};

/** 阵营名（结算弹窗标题）。 */
export const WINNING_CAMP_LABELS: Record<WinningCamp, string> = {
  good: '好人阵营',
  werewolf: '狼人阵营',
};

// ── 其余同源表 ────────────────────────────────────────────────

/** 警长发言顺序（警左/警右/死左/死右）。 */
export const SHERIFF_SIDE_LABELS: Record<SheriffSpeechSide, string> = {
  sheriff_left: '警左',
  sheriff_right: '警右',
  death_left: '死左',
  death_right: '死右',
};

/**
 * 结算留下的标记。公开 DTO 的 `player_status.status` 是宽字符串（后端可以新增
 * 标记），未知值按设计原样显示，所以这里不收窄键类型。
 */
export const PLAYER_STATUS_LABELS: Record<string, string> = {
  poisoned: '中毒未死',
  wounded: '中枪未死',
  delayed_death: '延迟结算',
  hunted: '被狩猎',
  silenced: '技能封印',
};

/** 记忆里 `action_history[].action.action_type`（存档 JSON，键开放）。 */
export const MEMORY_ACTION_LABELS: Record<string, string> = {
  kill: '狼刀',
  check: '查验',
  save: '救人',
  poison: '毒人',
  pass: '空过',
};

/** 记忆里 `witnessed_events[].event`（存档 JSON，键开放）。 */
export const MEMORY_EVENT_LABELS: Record<string, string> = {
  death: '死亡',
};

/** 角色 id → 角色名（记忆面板；前端没有角色 id 联合类型）。 */
export const ROLE_LABELS: Record<string, string> = {
  'wolf-killer-werewolf': '狼人',
  'wolf-killer-villager': '村民',
  'wolf-killer-seer': '预言家',
  'wolf-killer-witch': '女巫',
  'wolf-killer-hunter': '猎人',
  'wolf-killer-guard': '守卫',
  'wolf-killer-idiot': '白痴',
  'wolf-killer-werewolf-king': '白狼王',
  'wolf-killer-knight': '骑士',
  'wolf-killer-wolf-beauty': '狼美人',
  'wolf-killer-old-drunkard': '老酒鬼',
  'wolf-killer-wolf-king': '狼王',
  'wolf-killer-evil-knight': '恶夜骑士',
  'wolf-killer-dream-weaver': '摄梦人',
  'wolf-killer-gargoyle': '石像鬼',
  'wolf-killer-gravekeeper': '守墓人',
};

/** 记忆里 `private_knowledge` 的键（存档 JSON，键开放）。 */
export const KNOWLEDGE_LABELS: Record<string, string> = {
  teammates: '狼队友',
  check_results: '查验记录',
  has_antidote: '解药',
  has_poison: '毒药',
  has_gun: '枪',
  last_wolf_kill_target: '昨夜狼刀目标',
};
