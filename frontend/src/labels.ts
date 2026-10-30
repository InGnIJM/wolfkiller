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
  AwakenedWitchAssistVote,
  AwakenedWitchPoisonResult,
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
  awakened_hunt: '巡猎出局',
  stand_in: '替身代出局',
  bond_death: '缠绕连带出局',
  miracle_backlash: '奇迹反噬出局',
  radiance_backlash: '流光反噬出局',
  love_death: '挚友跟随出局',
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
  awakened_hunt: '觉醒猎人巡猎',
  fool_seer_check: '愚言家查验',
};

/** 夜间技能动词：有施法座位时按真实身份拼接，不再写死「女巫/猎人」。 */
export const NIGHT_ACTION_VERBS: Record<NightActionType, string> = {
  werewolf_kill: '袭击',
  witch_save: '救人',
  witch_poison: '毒人',
  seer_check: '查验',
  hunter_shot: '开枪',
  wolf_king_shot: '开枪',
  guard_protect: '守护',
  awakened_hunt: '巡猎',
  fool_seer_check: '查验',
};

/** 定序王子作废当轮放逐票后，同轮 ``exiled_seat === null`` 的票型文案。 */
export const VOTE_REWOUND_RESULT: string = '放逐投票作废，重新投票';

/**
 * 觉醒女巫协助者表态（`awakened_witch_assist_vote.vote`）。
 * 投票取值是后端闭集，三个面都走这里，避免各写一份把 `veto` 显示成机器值。
 */
export function awakenedWitchVoteLabel(vote: AwakenedWitchAssistVote): string {
  switch (vote) {
    case 'agree': return '同意';
    case 'veto': return '反对';
    case 'pass': return '弃权';
    default: return vote;
  }
}

/** 觉醒女巫调配毒药的最终结果（`awakened_witch_poison_result.result`）。 */
export function awakenedWitchPoisonResultLabel(result: AwakenedWitchPoisonResult): string {
  switch (result) {
    case 'passed': return '生效';
    case 'vetoed': return '被否决';
    case 'incomplete': return '未完成';
    case 'aborted': return '中止';
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
  const fallback = NIGHT_ACTION_LABELS[actionType] ?? '夜晚行动';
  const who = options.seat != null
    ? (options.roleName ? `${options.seat}号${options.roleName}` : `${options.seat}号`)
    : null;
  const skill = who && verb ? `${who} · ${verb}` : fallback;
  const target = options.targetSeat != null ? ` · 目标${options.targetSeat}号` : '';
  const redirect = (
    options.intendedSeat != null
    && options.targetSeat != null
    && options.intendedSeat !== options.targetSeat
  ) ? `（原目标${options.intendedSeat}号，被时间封锁改向）` : '';
  return `${skill}${target}${redirect}`;
}

export function thoughtActorName(
  actionType: ThoughtActionType,
  options: { roleName?: string | null } = {},
): string {
  if (actionType === 'lucky_reasoning') return '幸运儿';
  return options.roleName || THOUGHT_ROLE_NAMES[actionType] || '角色';
}

export function voteRoundWasRewound(
  round: number,
  events: ReadonlyArray<{ event_type: string; payload?: object }>,
): boolean {
  return events.some(
    (event) => (
      event.event_type === 'vote_rewound'
      && (event.payload as { round_number?: unknown } | undefined)?.round_number === round
    ),
  );
}

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
  demon_hunter_reasoning: '猎魔人思考',
  crimson_envoy_reasoning: '赤月使徒思考',
  nightmare_reasoning: '噩梦之影思考',
  dancer_reasoning: '舞者思考',
  mask_reasoning: '假面思考',
  awakened_hunter_reasoning: '觉醒猎人思考',
  awakened_beauty_reasoning: '觉醒狼美人思考',
  awakened_seer_reasoning: '觉醒预言家思考',
  fragrance_phantom_reasoning: '寻香魅影思考',
  miracle_merchant_reasoning: '奇迹商人思考',
  lucky_reasoning: '幸运儿思考',
  mirror_girl_reasoning: '魔镜少女思考',
  awakened_hidden_wolf_reasoning: '觉醒隐狼思考',
  radiance_earl_reasoning: '流光伯爵思考',
  eclipse_maid_reasoning: '蚀日侍女思考',
  time_wolf_reasoning: '蚀时狼妃思考',
  order_prince_reasoning: '定序王子思考',
  fool_seer_reasoning: '愚言家思考',
  fool_seer_reveal_reasoning: '愚言家自曝',
  iron_villager_reasoning: '铁好人自曝',
  crow_reasoning: '乌鸦思考',
  cupid_reasoning: '丘比特思考',
  admirer_reasoning: '暗恋者思考',
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
  awakened_hunt: '觉醒猎人',
  fool_seer_check: '愚言家',
};

/** 夜间独立事件 → 角色短名。
 *
 * 舞者与假面的载荷（三人舞池 / 目标是否在舞池里）塞不进封闭的 `night_action`，
 * 所以它们走自己的公开事件类型。中央面板的「黑夜 · X」眉标与编年史的夜间分组
 * 都要按事件类型认人，否则这两张牌行动时界面只剩一个「黑夜」。
 */
export const NIGHT_EVENT_ROLE_NAMES: Partial<Record<PublicEventType, string>> = {
  dance_pool: '舞者',
  mask_granted: '假面',
  wraith_illusion: '觉醒狼美人',
  fragrance_bond: '寻香魅影',
  // 觉醒女巫的毒药调配分四拍公开：选人、协助者密谈、协助者表态、结果。
  awakened_witch_assist_pick: '觉醒女巫',
  awakened_witch_assist_chat: '觉醒女巫',
  awakened_witch_assist_vote: '觉醒女巫',
  awakened_witch_poison_result: '觉醒女巫',
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
  demon_hunter_reasoning: '猎魔人',
  crimson_envoy_reasoning: '赤月使徒',
  nightmare_reasoning: '噩梦之影',
  dancer_reasoning: '舞者',
  mask_reasoning: '假面',
  awakened_hunter_reasoning: '觉醒猎人',
  awakened_beauty_reasoning: '觉醒狼美人',
  awakened_seer_reasoning: '觉醒预言家',
  fragrance_phantom_reasoning: '寻香魅影',
  miracle_merchant_reasoning: '奇迹商人',
  lucky_reasoning: '幸运儿',
  mirror_girl_reasoning: '魔镜少女',
  awakened_hidden_wolf_reasoning: '觉醒隐狼',
  radiance_earl_reasoning: '流光伯爵',
  eclipse_maid_reasoning: '蚀日侍女',
  time_wolf_reasoning: '蚀时狼妃',
  order_prince_reasoning: '定序王子',
  fool_seer_reasoning: '愚言家',
  fool_seer_reveal_reasoning: '愚言家',
  iron_villager_reasoning: '铁好人',
  crow_reasoning: '乌鸦',
  cupid_reasoning: '丘比特',
  admirer_reasoning: '暗恋者',
};

// ── 编年史 ────────────────────────────────────────────────────

/**
 * 引擎代行的动作在上帝视角里的眉标。
 *
 * 模型调用失败或答复连续不可用时，引擎会替该座位提交一个兜底动作。它曾经带一句
 * 「系统异常，本轮未行动」，而下游没有任何字段能把它和玩家的真实决定区分开 ——
 * 于是上帝视角把引擎的报错读成了玩家的内心独白。现在这条动作由 `degraded` 标记，
 * 眉标换成中性说法，读者一眼能看出「这轮不是他决定的」。
 */
export const DEGRADED_ACTION_LABEL: string = '系统代行';

/**
 * 编年史条目的短标签。**独立于死因表**：同一个事件类型（例如 `self_explode`）
 * 在死因列里是「白狼王自爆」，在标签列里是「自爆」。四个只驱动 store 的类型
 * （开局座位、翻牌、执行状态、系统代投）也要在这里表态，否则 `tsc` 报缺键。
 */
export const EVENT_TAGS: Record<PublicEventType, string> = {
  speech: '发言',
  vote: '投票',
  vote_result: '放逐',
  vote_rewound: '回溯',
  technical_abstain: '系统代投',
  exile_cancelled: '翻牌',
  self_explode: '自爆',
  crimson_explode: '自爆',
  dance_pool: '舞池',
  mask_granted: '假面',
  awakened_witch_assist_pick: '毒药协助选人',
  awakened_witch_assist_chat: '协助者密谈',
  awakened_witch_assist_vote: '协助者表态',
  awakened_witch_poison_result: '毒药结果',
  knight_duel: '决斗',
  wolf_beauty_charm: '魅惑',
  wraith_illusion: '幻象',
  fragrance_bond: '缠绕',
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
  commentary: '解说',
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
/** 评测任务状态 → 中文（BenchmarkStatus 的展示层，机器码契约在后端）。 */
export const BENCHMARK_STATUS_LABELS: Record<string, string> = {
  draft: '草稿', pending: '待开始', running: '运行中', pausing: '暂停中',
  paused: '已暂停', interrupted: '已中断', blocked: '已阻塞',
  completed: '已完成', failed: '失败', cancelled: '已取消',
};

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
  all_good_dead: '所有好人出局',
  all_others_dead: '其余玩家出局',
};

/** 阵营的胜负说法（大厅徽标 / 评测表）。 */
export const WINNER_LABELS: Record<WinningCamp, string> = {
  good: '好人胜',
  werewolf: '狼人胜',
  third_party: '第三方胜',
};

/** 阵营名（结算弹窗标题）。 */
export const CAMP_LABELS: Record<WinningCamp, string> = {
  good: '好人',
  werewolf: '狼人',
  third_party: '第三方',
};

export const WINNING_CAMP_LABELS: Record<WinningCamp, string> = {
  good: '好人阵营',
  werewolf: '狼人阵营',
  third_party: '第三方阵营',
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
  time_locked: '时间封锁',
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

/** 记忆里 `private_knowledge` 的键（存档 JSON，键开放）。 */
export const KNOWLEDGE_LABELS: Record<string, string> = {
  teammates: '狼队友',
  check_results: '查验记录',
  has_antidote: '解药',
  has_poison: '毒药',
  has_gun: '枪',
  last_wolf_kill_target: '昨夜狼刀目标',
};

// ── 角色名 ────────────────────────────────────────────────────

/**
 * 角色 id → 短名的唯一来源（键开放：后端 catalog 可以新增角色）。
 *
 * 这份表接管了原先 `roleMeta.ts` 里 `ROLE_ROWS` 的 label 列 —— 文案统一
 * 回 labels 体系，多语言由 `i18n/labelsEn.ts` 的同形表镜像；roleMeta
 * 只保留配色 / 底色 / 立绘这些与语言无关的视觉元数据。
 */
export const ROLE_NAME_LABELS: Record<string, string> = {
  'wolf-killer-werewolf': '狼人',
  'wolf-killer-witch': '女巫',
  'wolf-killer-awakened-witch': '觉醒女巫',
  'wolf-killer-seer': '预言家',
  'wolf-killer-hunter': '猎人',
  'wolf-killer-awakened-hunter': '觉醒猎人',
  'wolf-killer-villager': '平民',
  'wolf-killer-guard': '守卫',
  'wolf-killer-idiot': '白痴',
  'wolf-killer-werewolf-king': '白狼王',
  'wolf-killer-wolf-king': '狼王',
  'wolf-killer-awakened-wolf-king': '觉醒狼王',
  'wolf-killer-evil-knight': '恶夜骑士',
  'wolf-killer-dream-weaver': '摄梦人',
  'wolf-killer-gargoyle': '石像鬼',
  'wolf-killer-gravekeeper': '守墓人',
  'wolf-killer-knight': '骑士',
  'wolf-killer-wolf-beauty': '狼美人',
  'wolf-killer-awakened-wolf-beauty': '觉醒狼美人',
  'wolf-killer-old-drunkard': '老酒鬼',
  'wolf-killer-lonely-girl': '孤独少女',
  'wolf-killer-awakened-seer': '觉醒预言家',
  'wolf-killer-fragrance-phantom': '寻香魅影',
  'wolf-killer-pure-white': '纯白之女',
  'wolf-killer-wolf-witch': '狼巫',
  'wolf-killer-crimson-envoy': '赤月使徒',
  'wolf-killer-nightmare': '噩梦之影',
  'wolf-killer-mask': '假面',
  'wolf-killer-demon-hunter': '猎魔人',
  'wolf-killer-dancer': '舞者',
  'wolf-killer-miracle-merchant': '奇迹商人',
  'wolf-killer-mirror-girl': '魔镜少女',
  'wolf-killer-awakened-hidden-wolf': '觉醒隐狼',
  'wolf-killer-radiance-earl': '流光伯爵',
  'wolf-killer-eclipse-maid': '蚀日侍女',
  'wolf-killer-time-wolf': '蚀时狼妃',
  'wolf-killer-order-prince': '定序王子',
  'wolf-killer-fool-seer': '愚言家',
  'wolf-killer-iron-villager': '铁好人',
  'wolf-killer-crow': '乌鸦',
  'wolf-killer-cupid': '丘比特',
  'wolf-killer-admirer': '暗恋者',
};

/** 角色短名；未登记返回 `undefined`，调用方显示「未知身份」。 */
export function roleName(roleId: string | null | undefined): string | undefined {
  if (!roleId) return undefined;
  return ROLE_NAME_LABELS[roleId];
}

/** `recovery_block_code` → 提示文案；未知码原样返回（后端码才是稳定契约）。 */
export function recoveryBlockLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return RECOVERY_BLOCK_LABELS[code] ?? code;
}

/** 上帝视角按座位读快照上的真实身份短名；座位或角色未知时不猜。 */
export function roleLabelForSeat(
  seat: number | null | undefined,
  players: Record<number, { role?: string }> | undefined,
): string | undefined {
  if (seat == null || players == null) return undefined;
  return roleName(players[seat]?.role);
}
