import { create } from 'zustand';

import i18n from '../i18n';
import type {
  AudienceEvent,
  AudienceEventPage,
  AudienceSnapshot,
  ExecutionStatus,
  GameLogs,
  GamePhase,
  NightActionRecord,
  PublicPlayerState,
  PublicReplayEvent,
  SpeechRecord,
  VoteRecord,
  WinResult,
  ModelSnapshotEntry,
  PauseInfo,
  StreamingSpeech,
} from './types';

interface DerivedState {
  players: Record<number, PublicPlayerState>;
  speeches: SpeechRecord[];
  votes: VoteRecord[];
  nightActions: NightActionRecord[];
  phase: GamePhase;
  roundNumber: number;
  winResult: WinResult | null;
  currentSpeaker: number | null;
  /** 竞选期「警上」：已上警且仍存活的座位（含后续退水者，与后端 ran 集合一致） */
  badgeCandidates: number[];
  /** 竞选期「警下」：上警环节走完后，开选时存活、从未上警且当前仍存活的座位 */
  offBadgeSeats: number[];
}

interface GameStore extends DerivedState {
  gameId: string | null;
  revealOnDeath: boolean;
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
  currentPublicPlayers: Record<number, PublicPlayerState>;
  syncMode: 'unknown' | 'incremental' | 'legacy';
  audienceCursor: number;
  audienceHighWatermark: number;
  pendingAudienceEvents: Record<number, AudienceEvent>;
  streamError: string | null;
  isFollowingLive: boolean;
  executionStatus: ExecutionStatus | null;
  recoverable: boolean;
  recoveryBlockCode: string | null;
  pauseInfo: PauseInfo | null;
  modelSnapshot: ModelSnapshotEntry[];
  streamingSpeech: StreamingSpeech | null;

  setConnected: (connected: boolean) => void;
  setCurrentSpeaker: (seat: number | null) => void;
  setStreamingSpeech: (seat: number, text: string) => void;
  setStreamingThinking: (seat: number, thinking: string) => void;

  initPlayersFromDetail: (
    players: Record<number, PublicPlayerState>,
    revealOnDeath?: boolean,
    modelSnapshot?: ModelSnapshotEntry[],
  ) => void;
  loadLogs: (logs: GameLogs) => void;
  mergeLogs: (logs: GameLogs) => void;
  loadAudienceSnapshot: (snapshot: AudienceSnapshot) => void;
  loadAudienceHistory: (gameId: string, events: AudienceEvent[]) => void;
  mergeAudienceEvents: (page: AudienceEventPage) => void;
  goLive: () => void;
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
  nightActions: [],
  phase: 'waiting',
  roundNumber: 0,
  winResult: null,
  currentSpeaker: null,
  badgeCandidates: [],
  offBadgeSeats: [],
};

const initialState = {
  gameId: null as string | null,
  revealOnDeath: false,
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
  currentPublicPlayers: {} as Record<number, PublicPlayerState>,
  syncMode: 'unknown' as const,
  audienceCursor: 0,
  audienceHighWatermark: 0,
  pendingAudienceEvents: {} as Record<number, AudienceEvent>,
  streamError: null as string | null,
  isFollowingLive: true,
  executionStatus: null as ExecutionStatus | null,
  pauseInfo: null as PauseInfo | null,
  recoverable: false,
  recoveryBlockCode: null as string | null,
  modelSnapshot: [] as ModelSnapshotEntry[],
  streamingSpeech: null as StreamingSpeech | null,
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

function aliveSeatNumbers(players: Record<number, PublicPlayerState>): number[] {
  return Object.entries(players)
    .filter(([, player]) => player.is_alive)
    .map(([seat]) => Number(seat))
    .sort((left, right) => left - right);
}

function buildInitialPlayers(players: Record<number, PublicPlayerState>) {
  return Object.fromEntries(
    Object.entries(players).map(([seat, player]) => [
      Number(seat),
      { seat_number: player.seat_number, is_alive: true, is_sheriff: false, role: player.role, camp: player.camp },
    ]),
  ) as Record<number, PublicPlayerState>;
}

function applyCurrentSheriffSnapshot(
  players: Record<number, PublicPlayerState>,
  currentPublicPlayers: Record<number, PublicPlayerState>,
) {
  for (const [seat, player] of Object.entries(players)) {
    players[Number(seat)] = {
      ...player,
      is_sheriff: currentPublicPlayers[Number(seat)].is_sheriff,
    };
  }
}

// Legacy audience projections keyed the victim as `seat`; the public death
// contract is `player_seat`. Tolerate both shapes so old archives still render.
function normalizeDeathSeat<T extends PublicReplayEvent>(event: T): T {
  if (event.event_type !== 'death') return event;
  const payload = event.payload as unknown as Record<string, unknown>;
  if (payload.player_seat === undefined && typeof payload.seat === 'number') {
    return {
      ...event,
      payload: { ...payload, player_seat: payload.seat },
    } as unknown as T;
  }
  return event;
}

function buildTimeline(logs: GameLogs): PublicReplayEvent[] {
  return logs.events.map((event) => normalizeDeathSeat({ ...event }));
}

function toReplayEvent(event: AudienceEvent): PublicReplayEvent {
  return normalizeDeathSeat({
    timestamp: (event.created_at ?? event.timestamp ?? new Date(0).toISOString()) as PublicReplayEvent['timestamp'],
    event_type: event.event_type,
    payload: event.payload,
    seq: event.seq,
    event_id: event.event_id,
    schema_version: event.schema_version,
  } as PublicReplayEvent);
}

function latestExecutionPayload(events: AudienceEvent[]): Record<string, unknown> | undefined {
  return [...events].reverse().find((event) => (
    event.event_type === 'execution_state'
    && typeof event.payload.execution_status === 'string'
    && typeof event.payload.recoverable === 'boolean'
    && (event.payload.recovery_block_code === null
      || typeof event.payload.recovery_block_code === 'string')
  ))?.payload;
}

function eventPlayers(value: unknown): Record<number, PublicPlayerState> {
  if (Array.isArray(value)) {
    return Object.fromEntries(value
      .filter((player): player is PublicPlayerState => (
        typeof player === 'object' && player !== null
        && typeof (player as PublicPlayerState).seat_number === 'number'
      ))
      .map((player) => [player.seat_number, { ...player }]));
  }
  if (typeof value !== 'object' || value === null) return {};
  return Object.fromEntries(Object.entries(value)
    .filter(([, player]) => typeof player === 'object' && player !== null)
    .map(([seat, player]) => [Number(seat), { ...(player as PublicPlayerState) }]));
}

function eventKey(event: PublicReplayEvent): string {
  return JSON.stringify(event);
}

function timelinesEqual(
  left: PublicReplayEvent[],
  right: PublicReplayEvent[],
): boolean {
  return left.length === right.length
    && left.every((event, index) => eventKey(event) === eventKey(right[index]));
}

function reconcileHistoricalIndex(
  oldTimeline: PublicReplayEvent[],
  oldIndex: number,
  newTimeline: PublicReplayEvent[],
): number {
  if (oldIndex < 0 || newTimeline.length === 0) return -1;

  const clampedOldIndex = Math.min(oldIndex, oldTimeline.length - 1);
  const anchorKey = eventKey(oldTimeline[clampedOldIndex]);
  let anchorOccurrence = 0;

  for (let index = 0; index <= clampedOldIndex; index += 1) {
    if (eventKey(oldTimeline[index]) === anchorKey) anchorOccurrence += 1;
  }

  for (let index = 0; index < newTimeline.length; index += 1) {
    if (eventKey(newTimeline[index]) !== anchorKey) continue;
    anchorOccurrence -= 1;
    if (anchorOccurrence === 0) return index;
  }

  return Math.min(oldIndex, newTimeline.length - 1);
}

interface DeriveAccumulator {
  players: Record<number, PublicPlayerState>;
  speeches: SpeechRecord[];
  votes: VoteRecord[];
  nightActions: NightActionRecord[];
  phase: GamePhase;
  roundNumber: number;
  winResult: WinResult | null;
  currentSpeaker: number | null;
  /** 竞选窗口是否开着。**不能靠 phase 事件判断**：观众流里没有 sheriff_election
      阶段事件（后端把夜→竞选那次转换用 night_complete: 标签落检查点，投影成
      非公开的 STEP_COMMITTED 被丢掉），所以窗口改由竞选事件本身界定。 */
  electionActive: boolean;
  /** 竞选期「警上」名单：sheriff_run 里 choice === 'run' 的座位。
      与后端 office.candidates 一致：退水者仍算警上（不恢复警长投票权）。 */
  ranForSheriff: Set<number>;
  /** 明确表态不上警（choice === 'pass'）的座位。表态即定论，可以立刻挂「警下」；
      还没轮到的座位不属于任何一组，一个标都不挂。 */
  passedForSheriff: Set<number>;
}

function createAcc(
  initialPlayers: Record<number, PublicPlayerState>,
): DeriveAccumulator {
  return {
    players: copyPlayers(initialPlayers),
    speeches: [],
    votes: [],
    nightActions: [],
    phase: 'waiting',
    roundNumber: 0,
    winResult: null,
    currentSpeaker: null,
    electionActive: false,
    ranForSheriff: new Set(),
    passedForSheriff: new Set(),
  };
}

function applyEvent(acc: DeriveAccumulator, event: PublicReplayEvent): void {
  const openElection = () => {
    if (acc.electionActive) return;
    acc.electionActive = true;
    // 竞选期的表态不经过 acting 通告，上一夜最后那个「正在行动」必须在这里收掉，
    // 否则竞选全程都会挂着前一夜的红色高亮
    acc.currentSpeaker = null;
  };

  switch (event.event_type) {
    case 'speaking':
      acc.currentSpeaker = event.payload.seat;
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      break;
    case 'speech':
      acc.speeches.push(event.payload);
      // 竞选发言（含 PK）也属于竞选窗口；旧档可能只有发言没有上警记录
      if (event.payload.phase === 'sheriff_election') openElection();
      acc.currentSpeaker = event.payload.player_seat;
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      break;
    case 'vote':
      acc.votes.push(event.payload);
      acc.currentSpeaker = event.payload.voter_seat;
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      break;
    case 'vote_result':
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      if (event.seq === undefined && event.payload.exiled_seat !== null) {
        const player = acc.players[event.payload.exiled_seat];
        if (player) {
          acc.players[event.payload.exiled_seat] = { ...player, is_alive: false };
        }
      }
      break;
    case 'night_action':
      acc.nightActions.push(event.payload);
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      break;
    case 'technical_abstain':
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      break;
    case 'exile_cancelled': {
      // The seat survived the vote by flipping its card (identity arrives
      // through the accompanying player_revealed event) and lost its ballot.
      const player = acc.players[event.payload.target_seat];
      if (player) {
        acc.players[event.payload.target_seat] = { ...player, is_alive: true, can_vote: false };
      }
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      break;
    }
    case 'dance_pool':
    case 'mask_granted':
    case 'awakened_witch_assist_pick':
    case 'awakened_witch_assist_chat':
    case 'awakened_witch_assist_vote':
    case 'awakened_witch_poison_result':
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      break;
    case 'self_explode':
    case 'crimson_explode':
      // 赤月使徒的自爆不带乘客，但它同样终止当日流程；两者在时间轴上是同一拍。
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      acc.currentSpeaker = null;
      break;
    case 'sheriff_elected': {
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      // 竞选结束（含无人上警 / 全员退水 / 自爆中断）
      acc.electionActive = false;
      for (const [seat, player] of Object.entries(acc.players)) {
        acc.players[Number(seat)] = { ...player, is_sheriff: false };
      }
      if (event.payload.seat != null) {
        const elected = acc.players[event.payload.seat];
        if (elected) {
          acc.players[event.payload.seat] = { ...elected, is_sheriff: true };
        }
      }
      break;
    }
    case 'sheriff_badge': {
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      for (const [seat, player] of Object.entries(acc.players)) {
        acc.players[Number(seat)] = { ...player, is_sheriff: false };
      }
      if (event.payload.to_seat != null) {
        const nextSheriff = acc.players[event.payload.to_seat];
        if (nextSheriff) {
          acc.players[event.payload.to_seat] = { ...nextSheriff, is_sheriff: true };
        }
      }
      break;
    }
    case 'sheriff_run':
      openElection();
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      if (event.payload.choice === 'run') acc.ranForSheriff.add(event.payload.seat);
      else acc.passedForSheriff.add(event.payload.seat);
      break;
    case 'sheriff_withdraw':
    case 'sheriff_vote':
      // 只在竞选里出现，可以据此开窗
      openElection();
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      break;
    case 'sheriff_side':
      // 警长选发言方向，发生在竞选结束后的白天，**不能**用来开窗
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      break;
    case 'narration':
    case 'commentary':
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      acc.currentSpeaker = null;
      break;
    case 'knight_duel':
    case 'wolf_beauty_charm':
    case 'wolf_beauty_revenge':
    case 'player_status':
      // 技能事件自带轮次，但不代表某个座位「正在发言」，所以只推进轮次。
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      break;
    case 'wolf_chat_message':
    case 'wolf_vote':
    case 'witch_thought':
    case 'seer_thought':
    case 'night_thought':
      acc.currentSpeaker = event.payload.seat;
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      break;
    case 'death': {
      const player = acc.players[event.payload.player_seat];
      if (player) {
        acc.players[event.payload.player_seat] = { ...player, is_alive: false };
      }
      acc.roundNumber = Math.max(acc.roundNumber, event.payload.round_number);
      break;
    }
    case 'phase':
      acc.phase = event.payload.phase;
      acc.roundNumber = event.payload.round_number;
      acc.currentSpeaker = null;
      // 观众流通常没有竞选阶段事件，有就按它开关窗口；其它阶段一律关窗
      if (event.payload.phase === 'sheriff_election') openElection();
      else acc.electionActive = false;
      break;
    case 'winner':
      acc.winResult = event.payload;
      acc.phase = 'game_over';
      acc.currentSpeaker = null;
      break;
    case 'game_initialized': {
      const initialized = eventPlayers(event.payload.players);
      for (const [seat, player] of Object.entries(initialized)) {
        acc.players[Number(seat)] = { ...player };
      }
      break;
    }
    case 'player_revealed': {
      const player = acc.players[event.payload.seat_number];
      if (player) {
        acc.players[event.payload.seat_number] = {
          ...player,
          role: event.payload.role,
          camp: event.payload.camp,
          revealed_role: event.payload.role,
        };
      }
      break;
    }
    case 'execution_state':
      break;
  }
}

function finalizeAcc(
  acc: DeriveAccumulator,
  timelineLength: number,
  upToIndex: number,
  currentPublicPlayers: Record<number, PublicPlayerState>,
): DerivedState {
  // 输出层用浅拷贝：acc 会在后续增量应用里继续原地推进，不能把可变数组/座位表
  // 的引用交给已发布的状态，否则之后的事件会改到历史快照。
  const players = { ...acc.players };
  if (timelineLength === 0 || upToIndex === timelineLength - 1) {
    applyCurrentSheriffSnapshot(players, currentPublicPlayers);
  }

  // 竞选期的两组座位：出局座位不参与高亮（警上死于自爆、警下死于夜刀都不该继续挂标）。
  // 窗口关了（竞选结束）就不再对外提供名单，座位图此时看的是警徽。
  const aliveSeats = aliveSeatNumbers(players);
  const badgeCandidates = acc.electionActive
    ? aliveSeats.filter((seat) => acc.ranForSheriff.has(seat))
    : [];
  // 已明确不上警的座位立刻算警下；还没轮到的座位不在任何一组里，不挂标
  const offBadgeSeats = acc.electionActive
    ? aliveSeats.filter((seat) => acc.passedForSheriff.has(seat))
    : [];
  // 竞选窗口开着时按后端真实状态机给出阶段：否则观众流里这一段会一直停在上一夜的
  // 「night」（座位图的警上/警下、中央面板与活动卡都按 sheriff_election 取分支）。
  // 对局结束时以 game_over 为准，不去覆盖它。
  const phase = acc.electionActive && acc.winResult === null ? 'sheriff_election' : acc.phase;

  return {
    players,
    speeches: [...acc.speeches],
    votes: [...acc.votes],
    nightActions: [...acc.nightActions],
    phase,
    roundNumber: acc.roundNumber,
    winResult: acc.winResult,
    currentSpeaker: acc.currentSpeaker,
    badgeCandidates,
    offBadgeSeats,
  };
}

interface DeriveCache {
  acc: DeriveAccumulator;
  index: number;
  timeline: PublicReplayEvent[];
  initialPlayers: Record<number, PublicPlayerState>;
  currentPublicPlayers: Record<number, PublicPlayerState>;
}

// 增量推导缓存：观众事件只往时间轴尾部追加、不改写前缀，所以「acc 已消化到的
// 那条边界事件仍是同一对象引用」就足以证明前缀完好，可以直接续推；向后 seek、
// timeline 整体替换、玩家输入变化都会让检查失败而回落全量重建。
let deriveCache: DeriveCache | null = null;

function deriveTo(
  timeline: PublicReplayEvent[],
  upToIndex: number,
  initialPlayers: Record<number, PublicPlayerState>,
  currentPublicPlayers: Record<number, PublicPlayerState>,
): DerivedState {
  let acc: DeriveAccumulator;
  const cache = deriveCache;
  if (
    cache !== null
    && cache.index >= 0
    && cache.index <= upToIndex
    && cache.index < timeline.length
    && timeline[cache.index] === cache.timeline[cache.index]
    && cache.initialPlayers === initialPlayers
    && cache.currentPublicPlayers === currentPublicPlayers
  ) {
    acc = cache.acc;
    for (
      let index = cache.index + 1;
      index <= upToIndex && index < timeline.length;
      index += 1
    ) {
      applyEvent(acc, timeline[index]);
    }
    cache.index = Math.min(upToIndex, timeline.length - 1);
    cache.timeline = timeline;
  } else {
    acc = createAcc(initialPlayers);
    for (
      let index = 0;
      index <= upToIndex && index < timeline.length;
      index += 1
    ) {
      applyEvent(acc, timeline[index]);
    }
    deriveCache = {
      acc,
      index: Math.min(upToIndex, timeline.length - 1),
      timeline,
      initialPlayers,
      currentPublicPlayers,
    };
  }
  return finalizeAcc(acc, timeline.length, upToIndex, currentPublicPlayers);
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

  setConnected: (connected) => set({ connected }),
  setCurrentSpeaker: (seat) => set((state) => ({
    currentSpeaker: seat,
    streamingSpeech: state.streamingSpeech?.seat === seat ? state.streamingSpeech : null,
  })),
  setStreamingSpeech: (seat, text) => {
    const { isFollowingLive, streamingSpeech } = get();
    if (!isFollowingLive) return;
    if (streamingSpeech !== null && streamingSpeech.seat === seat && streamingSpeech.text === text) return;
    // 同一座位只换正文、保留思考；换了座位就整条重来
    const thinking = streamingSpeech?.seat === seat ? streamingSpeech.thinking : '';
    set({ streamingSpeech: { seat, text, thinking, updatedAt: Date.now() } });
  },
  setStreamingThinking: (seat, thinking) => {
    const { isFollowingLive, streamingSpeech } = get();
    if (!isFollowingLive) return;
    if (streamingSpeech !== null && streamingSpeech.seat === seat && streamingSpeech.thinking === thinking) return;
    const text = streamingSpeech?.seat === seat ? streamingSpeech.text : '';
    set({ streamingSpeech: { seat, text, thinking, updatedAt: Date.now() } });
  },

  initPlayersFromDetail: (players, revealOnDeath = false, modelSnapshot) => {
    const currentPublicPlayers = copyPlayers(players);
    const initialPlayers = buildInitialPlayers(currentPublicPlayers);
    const { timeline, timelineIndex } = get();
    const snapshotUpdate = modelSnapshot !== undefined ? { modelSnapshot } : {};
    if (timeline.length === 0) {
      const publicPlayers = copyPlayers(initialPlayers);
      applyCurrentSheriffSnapshot(publicPlayers, currentPublicPlayers);
      set({ players: publicPlayers, initialPlayers, currentPublicPlayers, revealOnDeath, ...snapshotUpdate });
      return;
    }
    set({
      ...deriveTo(timeline, timelineIndex, initialPlayers, currentPublicPlayers),
      initialPlayers,
      currentPublicPlayers,
      revealOnDeath,
      ...snapshotUpdate,
    });
  },

  loadLogs: (logs) => {
    const timeline = buildTimeline(logs);
    const { initialPlayers, currentPublicPlayers } = get();
    set({
      gameId: logs.game_id,
      timeline,
      timelineIndex: -1,
      ...deriveTo(timeline, -1, initialPlayers, currentPublicPlayers),
      isPlaying: false,
      winOverlayDismissed: false,
      showWinOverlay: false,
      syncMode: 'legacy',
      isFollowingLive: true,
    });
  },

  mergeLogs: (logs) => {
    const timeline = buildTimeline(logs);
    const {
      timeline: oldTimeline,
      timelineIndex,
      initialPlayers,
      currentPublicPlayers,
      isPaused,
      winOverlayDismissed,
    } = get();
    if (timelinesEqual(timeline, oldTimeline)) return;

    const shouldFollowTail = isAtTimelineEnd(oldTimeline, timelineIndex) && !isPaused;
    const nextIndex = shouldFollowTail
      ? timeline.length - 1
      : reconcileHistoricalIndex(oldTimeline, timelineIndex, timeline);
    const derived = deriveTo(timeline, nextIndex, initialPlayers, currentPublicPlayers);
    set({
      timeline,
      timelineIndex: nextIndex,
      ...derived,
      showWinOverlay: derived.winResult !== null && !winOverlayDismissed,
    });
  },

  loadAudienceSnapshot: (snapshot) => {
    const state = snapshot.state;
    const currentPublicPlayers = copyPlayers(state.players);
    const initialPlayers = buildInitialPlayers(currentPublicPlayers);
    set({
      gameId: snapshot.game_id,
      revealOnDeath: state.reveal_on_death ?? false,
      phase: state.phase,
      roundNumber: state.round_number,
      players: copyPlayers(currentPublicPlayers),
      initialPlayers,
      currentPublicPlayers,
      speeches: state.speeches ?? [],
      votes: [],
      nightActions: [],
      winResult: state.win_result,
      currentSpeaker: null,
      // 快照不带竞选名单，等观众事件按 sheriff_run 重建
      badgeCandidates: [],
      offBadgeSeats: [],
      syncMode: 'incremental',
      audienceCursor: 0,
      audienceHighWatermark: snapshot.seq,
      pendingAudienceEvents: {},
      streamError: null,
      isFollowingLive: true,
      executionStatus: state.execution_status ?? null,
      recoverable: state.recoverable ?? false,
      recoveryBlockCode: state.recovery_block_code ?? null,
      pauseInfo: state.pause_code
        ? { code: state.pause_code, reason: state.pause_reason ?? null, detail: state.pause_detail ?? null }
        : null,
      isPaused: state.execution_status === 'paused',
      modelSnapshot: state.model_snapshot ?? [],
      timeline: [],
      timelineIndex: -1,
      isPlaying: false,
      showWinOverlay: Boolean(state.win_result),
      winOverlayDismissed: false,
    });
  },

  loadAudienceHistory: (gameId, events) => {
    if (get().gameId !== gameId || get().syncMode !== 'incremental') return;
    const ordered = [...events].sort((left, right) => left.seq - right.seq);
    const contiguous: AudienceEvent[] = [];
    const pending: Record<number, AudienceEvent> = {};
    let cursor = 0;
    for (const event of ordered) {
      if (!Number.isInteger(event.seq) || event.seq <= 0 || event.seq <= cursor) continue;
      if (event.seq === cursor + 1) {
        contiguous.push(event);
        cursor = event.seq;
      } else {
        pending[event.seq] = event;
      }
    }
    const timeline = contiguous.map(toReplayEvent);
    if (timeline.length === 0) {
      set({
        audienceCursor: cursor,
        pendingAudienceEvents: pending,
        isFollowingLive: true,
      });
      return;
    }
    const { initialPlayers, currentPublicPlayers, winOverlayDismissed } = get();
    const timelineIndex = timeline.length - 1;
    const derived = deriveTo(timeline, timelineIndex, initialPlayers, currentPublicPlayers);
    const payload = latestExecutionPayload(contiguous);
    set({
      timeline,
      timelineIndex,
      ...derived,
      audienceCursor: cursor,
      pendingAudienceEvents: pending,
      isFollowingLive: true,
      showWinOverlay: derived.winResult !== null && !winOverlayDismissed,
      executionStatus: typeof payload?.execution_status === 'string'
        ? payload.execution_status as ExecutionStatus
        : get().executionStatus,
      recoverable: typeof payload?.recoverable === 'boolean'
        ? payload.recoverable
        : get().recoverable,
      recoveryBlockCode: typeof payload?.recovery_block_code === 'string'
        ? payload.recovery_block_code
        : payload?.recovery_block_code === null ? null : get().recoveryBlockCode,
    });
  },

  mergeAudienceEvents: (page) => {
    const state = get();
    if (state.gameId !== page.game_id || state.syncMode !== 'incremental') return;
    const pending = { ...state.pendingAudienceEvents };
    let streamError = state.streamError;
    for (const event of page.events) {
      if (!Number.isInteger(event.seq) || event.seq <= 0) {
        streamError = i18n.t('errors.invalidAudienceSeq');
        continue;
      }
      if (event.seq <= state.audienceCursor) continue;
      const existing = pending[event.seq];
      if (existing && existing.event_id !== event.event_id) {
        streamError = i18n.t('errors.seqConflict', { seq: event.seq });
        continue;
      }
      pending[event.seq] = event;
    }

    const consumed: AudienceEvent[] = [];
    let cursor = state.audienceCursor;
    while (pending[cursor + 1]) {
      const event = pending[cursor + 1];
      delete pending[cursor + 1];
      consumed.push(event);
      cursor = event.seq;
    }
    if (consumed.length === 0) {
      set({
        pendingAudienceEvents: pending,
        audienceHighWatermark: Math.max(
          state.audienceHighWatermark,
          page.high_watermark ?? page.last_seq,
        ),
        streamError,
      });
      return;
    }

    const oldTimeline = state.timeline;
    const timeline = [...oldTimeline, ...consumed.map(toReplayEvent)];
    const shouldFollowTail = state.isFollowingLive
      && (oldTimeline.length === 0 || state.timelineIndex >= oldTimeline.length - 1);
    const timelineIndex = shouldFollowTail ? timeline.length - 1 : state.timelineIndex;
    // 停在历史中段时新事件只往尾部追加，timelineIndex/输入都没变，
    // 派生结果与现值逐字段恒等——整段推导直接跳过（增量缓存的另一半收益）。
    const derived = shouldFollowTail
      ? deriveTo(
        timeline,
        timelineIndex,
        state.initialPlayers,
        state.currentPublicPlayers,
      )
      : null;
    const payload = latestExecutionPayload(consumed);
    const hasSpeechOrPhaseEvent = consumed.some(
      (e) => e.event_type === 'speech' || e.event_type === 'phase',
    );
    const winResult = derived?.winResult ?? state.winResult;
    const currentSpeaker = derived?.currentSpeaker ?? state.currentSpeaker;
    set({
      timeline,
      timelineIndex,
      ...(derived ?? {}),
      audienceCursor: cursor,
      audienceHighWatermark: Math.max(
        state.audienceHighWatermark,
        page.high_watermark ?? page.last_seq,
      ),
      pendingAudienceEvents: pending,
      streamError,
      showWinOverlay: winResult !== null && !state.winOverlayDismissed,
      streamingSpeech: (hasSpeechOrPhaseEvent || (state.streamingSpeech && state.streamingSpeech.seat !== currentSpeaker))
        ? null
        : state.streamingSpeech,
      executionStatus: typeof payload?.execution_status === 'string'
        ? payload.execution_status as ExecutionStatus
        : state.executionStatus,
      recoverable: typeof payload?.recoverable === 'boolean'
        ? payload.recoverable
        : state.recoverable,
      recoveryBlockCode: typeof payload?.recovery_block_code === 'string'
        ? payload.recovery_block_code
        : payload?.recovery_block_code === null ? null : state.recoveryBlockCode,
    });
  },

  goLive: () => {
    const { timeline } = get();
    if (timeline.length === 0) {
      set({ isFollowingLive: true });
      return;
    }
    get().seekTo(timeline.length - 1);
    set({ isFollowingLive: true });
  },

  seekTo: (index) => {
    const { timeline, initialPlayers, currentPublicPlayers, winOverlayDismissed } = get();
    if (timeline.length === 0) return;
    const timelineIndex = Math.max(0, Math.min(index, timeline.length - 1));
    const derived = deriveTo(timeline, timelineIndex, initialPlayers, currentPublicPlayers);
    set({
      timelineIndex,
      ...derived,
      showWinOverlay: derived.winResult !== null && !winOverlayDismissed,
      isFollowingLive: timelineIndex === timeline.length - 1,
      streamingSpeech: null,
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
    deriveCache = null;
    set(initialState);
  },
}));
