import { useMemo } from 'react';
import { Box, Typography, keyframes } from '@mui/material';
import { useGameStore } from '../../store/gameStore';
import type { GamePhase, PublicReplayEvent } from '../../store/types';
import i18n from '../../i18n';
import { useTranslation } from 'react-i18next';
import { useLabels } from '../../i18n/useLabels';
import type { LabelsModule } from '../../i18n/useLabels';
import type { PublicPlayerState, WinningCamp } from '../../store/types';
import { BACKDROP, BLOOD_MOON, PLATE, ROLE_COLORS, TITLE_GRADIENT } from '../../theme/tokens';

const fadeIn = keyframes`
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
`;

function campLabel(camp: string, L: LabelsModule): string {
  return camp in L.CAMP_LABELS ? L.CAMP_LABELS[camp as WinningCamp] : camp;
}

function deathCauseLabel(
  event: Extract<PublicReplayEvent, { event_type: 'death' }>,
  phase: string,
  events: PublicReplayEvent[],
  index: number,
  L: LabelsModule,
): string {
  if (event.payload.cause !== 'self_explode') {
    return L.DEATH_CAUSE_LABELS[event.payload.cause] ?? i18n.t('game.evUnknown');
  }
  if (phase === 'sheriff_election') return i18n.t('game.evExplodeInElection');
  const nearby = [events[index - 1], events[index + 1]];
  if (nearby.some((item) => item?.event_type === 'sheriff_elected' && item.payload.reason === 'explode')) {
    return i18n.t('game.evExplodeInElection');
  }
  return L.DEATH_CAUSE_LABELS.self_explode;
}

// 中央面板内容全部使用容器查询单位：cqi 相对面板宽度；
// cqh 相对最近的 size 容器（椭圆下是舞台，双列下是中央列），
// 舞台变小时间距与字号同步收缩，避免固有高度挤占座位通道
const panelSx = {
  textAlign: 'center',
  py: 'clamp(6px, 2.2cqh, 16px)',
  px: 'clamp(10px, 3cqi, 24px)',
  animation: `${fadeIn} 0.25s ease-out`,
  bgcolor: BACKDROP.textPlate,
  backdropFilter: 'blur(12px)',
  borderRadius: 2,
};

// —— 中文大写天数（第贰天 / 第拾天）——
const CN_UPPER = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖'] as const;
function upperDay(n: number): string {
  if (n >= 1 && n <= 9) return CN_UPPER[n];
  if (n === 10) return '拾';
  if (n >= 11 && n <= 19) return `拾${CN_UPPER[n - 10]}`;
  if (n === 20) return '贰拾';
  return String(n);
}

const EN_ORDINAL: Record<number, string> = {
  1: 'FIRST', 2: 'SECOND', 3: 'THIRD', 4: 'FOURTH', 5: 'FIFTH', 6: 'SIXTH',
  7: 'SEVENTH', 8: 'EIGHTH', 9: 'NINTH', 10: 'TENTH', 11: 'ELEVENTH', 12: 'TWELFTH',
};
function ordinalEn(n: number): string {
  return EN_ORDINAL[n] ?? String(n);
}

function nightRole(
  entry: PublicReplayEvent | null,
  players: Record<number, PublicPlayerState>,
  L: LabelsModule,
): string | null {
  if (!entry) return null;
  // 舞者/假面没有 `night_action` 行（载荷形状不同），它们各自成事件类型。
  const named = L.NIGHT_EVENT_ROLE_NAMES[entry.event_type];
  if (named) return named;
  if (entry.event_type === 'night_action') {
    return L.roleLabelForSeat(entry.payload.seat, players)
      ?? L.NIGHT_ACTION_ROLE_NAMES[entry.payload.action_type]
      ?? null;
  }
  if (entry.event_type === 'night_thought') {
    return L.thoughtActorName(entry.payload.action_type, {
      roleName: L.roleLabelForSeat(entry.payload.seat, players),
    });
  }
  if (entry.event_type === 'witch_thought' || entry.event_type === 'seer_thought') {
    return L.THOUGHT_ROLE_NAMES[entry.event_type] ?? null;
  }
  if (entry.event_type === 'wolf_chat_message' || entry.event_type === 'wolf_vote') {
    return L.roleName('wolf-killer-werewolf') ?? null;
  }
  return null;
}

// 战报：从已播放时间线中回溯最近的关键事件，生成叙事性一句战报
function buildReport(phase: string, events: PublicReplayEvent[], L: LabelsModule): string {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.event_type === 'death') {
      return i18n.t('game.reportDeath', { seat: e.payload.player_seat, cause: deathCauseLabel(e, phase, events, i, L) });
    }
    if (e.event_type === 'vote_result') {
      if (e.payload.exiled_seat === null) {
        return L.voteRoundWasRewound(e.payload.round_number, events)
          ? L.VOTE_REWOUND_RESULT
          : i18n.t('game.reportTie');
      }
      return i18n.t('game.reportExiled', { seat: e.payload.exiled_seat });
    }
    if (e.event_type === 'vote_rewound') {
      return i18n.t('game.reportRewind', { seat: e.payload.seat });
    }
    if (e.event_type === 'sheriff_elected') {
      return e.payload.seat == null
        ? i18n.t('game.evSheriffLost')
        : i18n.t('game.evSheriffElected', { seat: e.payload.seat });
    }
    if (e.event_type === 'sheriff_badge') {
      return e.payload.to_seat == null
        ? i18n.t('game.evBadgeTear', { seat: e.payload.from_seat })
        : i18n.t('game.evBadgePass', { from: e.payload.from_seat, to: e.payload.to_seat });
    }
    if (e.event_type === 'sheriff_run') {
      return e.payload.choice === 'run'
        ? i18n.t('game.reportRun', { seat: e.payload.seat })
        : i18n.t('game.reportPass', { seat: e.payload.seat });
    }
    if (e.event_type === 'night_action' && e.payload.action_type === 'werewolf_kill') {
      return i18n.t('game.reportWolfKill', { target: e.payload.target_seat });
    }
  }
  if (phase === 'night' || phase === 'dawn') return i18n.t('game.reportNight');
  if (phase === 'sheriff_election') return i18n.t('game.reportElection');
  if (phase === 'speech') return i18n.t('game.reportSpeech');
  if (phase === 'vote_casting' || phase === 'vote_resolution') return i18n.t('game.reportVote');
  return i18n.t('game.reportDefault');
}

// 中央阶段页：英文眉标 + 第X天金字 + 阶段名 + 战报 + 存活与余狼情报
function PhaseContent({ phase, roundNumber }: { phase: string; roundNumber: number }) {
  const { t } = useTranslation();
  const L = useLabels();
  const players = useGameStore((s) => s.players);
  const timeline = useGameStore((s) => s.timeline);
  const timelineIndex = useGameStore((s) => s.timelineIndex);

  const alive = Object.values(players).filter((p) => p.is_alive).length;
  const total = Object.keys(players).length;
  const wolvesRemain = Object.values(players).filter((p) => p.camp === 'werewolf' && p.is_alive).length;
  const report = useMemo(
    () => buildReport(phase, timeline.slice(0, timelineIndex + 1), L),
    [phase, timeline, timelineIndex, L],
  );

  const currentEntry = timelineIndex >= 0 && timelineIndex < timeline.length
    ? timeline[timelineIndex]
    : null;
  // 组件签名的 phase 是 string（未知取值原样显示），查表时按联合收窄。
  let phaseLabel = L.CENTER_PHASE_LABELS[phase as GamePhase] || phase;
  if (phase === 'night') {
    const role = nightRole(currentEntry, players, L);
    if (role) phaseLabel = t('game.nightRolePrefix', { role });
  }

  return (
    <Box sx={{ ...panelSx, py: 'clamp(5px, 1.8cqh, 12px)' }}>
      <Typography
        sx={{
          fontSize: 'clamp(8px, 1.15cqh, 10px)',
          fontWeight: 600,
          letterSpacing: 'clamp(1.5px, 0.9cqi, 4px)',
          color: 'secondary.dark',
          fontFamily: '"Cinzel","Noto Serif SC",serif',
        }}
      >
        THE {ordinalEn(roundNumber)} DAY
      </Typography>
      <Typography
        sx={{
          mt: 0.5,
          fontSize: 'clamp(20px, min(7.4cqh, 13cqi), 46px)',
          fontWeight: 700,
          lineHeight: 1.2,
          letterSpacing: 'clamp(2px, 2.4cqi, 8px)',
          fontFamily: '"Cinzel","Noto Serif SC",serif',
          background: TITLE_GRADIENT,
          WebkitBackgroundClip: 'text',
          backgroundClip: 'text',
          color: 'transparent',
        }}
      >
        {t('game.dayTitle', { day: i18n.language.startsWith('zh') ? upperDay(roundNumber) : ordinalEn(roundNumber) })}
      </Typography>
      <Typography
        sx={{
          display: 'inline-block',
          mt: 0.4,
          fontSize: 'clamp(11px, 1.9cqh, 15.2px)',
          fontWeight: 600,
          letterSpacing: 'clamp(1.5px, 1.2cqi, 3.5px)',
          color: 'text.primary',
          pb: 1,
          borderBottom: '1px solid',
          borderColor: 'secondary.main',
        }}
      >
        {phaseLabel}
      </Typography>

      <Box
        sx={{
          mt: 1.5,
          mx: 'auto',
          width: 'min(176px, 92cqi)',
          px: 1.4,
          py: 0.7,
          border: '1px dashed',
          borderColor: 'rgba(201,86,107,0.4)',
          borderRadius: 1.5,
          bgcolor: 'rgba(201,86,107,0.05)',
          textAlign: 'left',
        }}
      >
        <Typography
          variant="caption"
          sx={{
            display: 'block',
            color: ROLE_COLORS.werewolf.color,
            fontWeight: 600,
            letterSpacing: 1.5,
            fontSize: 'clamp(9px, 1.15cqh, 10px)',
            mb: 0.1,
          }}
        >
          {t('game.reportTitle')}
        </Typography>
        <Typography variant="caption" sx={{ color: 'text.secondary', lineHeight: 1.8, fontSize: 'clamp(9px, 1.3cqh, 11.2px)' }}>
          {report}
        </Typography>
      </Box>

      <Typography
        sx={{
          mt: 1,
          fontSize: 'clamp(9px, 1.25cqh, 10.9px)',
          letterSpacing: 'clamp(0.5px, 0.7cqi, 2px)',
          color: 'text.disabled',
        }}
      >
        {t('game.aliveLine', { alive, total, wolves: wolvesRemain })}
      </Typography>
    </Box>
  );
}

// 阶段页下方的一句话事件摘要（完整内容见底部活动栏 / 右侧编年史）
function EventSummary({
  entry,
  phase,
  events,
  index,
}: {
  entry: PublicReplayEvent;
  phase: string;
  events: PublicReplayEvent[];
  index: number;
}) {
  const { t } = useTranslation();
  const L = useLabels();
  const players = useGameStore((s) => s.players);
  const voteRewound = useMemo(
    () => entry.event_type === 'vote_result'
      && L.voteRoundWasRewound(entry.payload.round_number, events),
    [entry, events, L],
  );
  let text: string;
  let tone: string = BLOOD_MOON.goldLight;

  switch (entry.event_type) {
    case 'speaking':
      text = t('game.evSpeaking', { seat: entry.payload.seat });
      tone = BLOOD_MOON.crimson;
      break;
    case 'speech':
      text = entry.payload.phase === 'last_words'
        ? t('game.evLastWords', { seat: entry.payload.player_seat })
        : entry.payload.phase === 'sheriff_election'
          ? t('game.evCampaign', { seat: entry.payload.player_seat })
          : t('game.evSpeaking', { seat: entry.payload.player_seat });
      break;
    case 'witch_thought':
    case 'seer_thought': {
      // 旧档事件类型本身就是「谁在想」，新档要看 payload.action_type，分开查表才能收窄键。
      const who = L.THOUGHT_ROLE_NAMES[entry.event_type] ?? t('game.evRoleFallback');
      text = t('game.evThinking', { who });
      tone = ROLE_COLORS.witch.color;
      break;
    }
    case 'night_thought': {
      // 引擎代行时「某角色思考中」是错的：它没有思考，是引擎替它提交的。
      const who = L.thoughtActorName(entry.payload.action_type, {
        roleName: L.roleLabelForSeat(entry.payload.seat, players),
      });
      text = entry.payload.degraded
        ? t('game.evSysAct', { seat: entry.payload.seat, label: L.DEGRADED_ACTION_LABEL })
        : t('game.evThinking', { who: `${t('game.seatNumber', { seat: entry.payload.seat })}${who}` });
      tone = ROLE_COLORS.witch.color;
      break;
    }
    case 'wolf_chat_message':
      text = t('game.evWolfChat');
      tone = ROLE_COLORS.werewolf.color;
      break;
    case 'wolf_vote':
      // 狼票走狼队频道的粉色，与白天投票/警票的金色摘要区分昼夜
      text = entry.payload.degraded
        ? t('game.evSysAct', { seat: entry.payload.seat, label: L.DEGRADED_ACTION_LABEL })
        : entry.payload.target_seat === null
          ? t('game.evAbstain', { seat: entry.payload.seat })
          : t('game.evVoted', { seat: entry.payload.seat, target: entry.payload.target_seat });
      tone = ROLE_COLORS.werewolf.color;
      break;
    case 'night_action':
      text = L.nightActionCaption(entry.payload.action_type, {
        seat: entry.payload.seat,
        targetSeat: entry.payload.target_seat,
        intendedSeat: entry.payload.intended_seat,
        roleName: L.roleLabelForSeat(entry.payload.seat, players),
      });
      tone = ROLE_COLORS.seer.color;
      break;
    case 'death':
      text = t('game.evDeath', { seat: entry.payload.player_seat, cause: deathCauseLabel(entry, phase, events, index, L) });
      tone = ROLE_COLORS.werewolf.color;
      break;
    case 'vote':
      text = entry.payload.target_seat === null
        ? t('game.evVoteAbstain', { seat: entry.payload.voter_seat })
        : t('game.evVoteFor', { seat: entry.payload.voter_seat, target: entry.payload.target_seat });
      break;
    case 'vote_result':
      text = entry.payload.exiled_seat === null
        ? (voteRewound ? L.VOTE_REWOUND_RESULT : t('game.evTie'))
        : t('game.evExiled', { seat: entry.payload.exiled_seat });
      break;
    case 'vote_rewound':
      text = t('game.reportRewind', { seat: entry.payload.seat });
      break;
    case 'technical_abstain':
      text = t('game.evSysAbstain', { seat: entry.payload.voter_seat });
      tone = ROLE_COLORS.werewolf.color;
      break;
    case 'player_status':
      // 结算给一个座位留下的标记：中毒/中枪但被延迟结算，或延迟死亡本身。
      text = t('game.evDeath', {
        seat: entry.payload.player_seat,
        cause: L.PLAYER_STATUS_LABELS[entry.payload.status] ?? entry.payload.status,
      });
      tone = BLOOD_MOON.goldLight;
      break;
    case 'exile_cancelled':
      text = t('game.evFlip', { seat: entry.payload.target_seat });
      break;
    case 'knight_duel':
      text = t('game.evDuel', { seat: entry.payload.seat, target: entry.payload.target_seat, camp: campLabel(entry.payload.camp, L) });
      tone = BLOOD_MOON.crimson;
      break;
    case 'wolf_beauty_charm':
      // God view: name the charmed seat when the event carries it (archives
      // recorded before the field existed do not).
      text = entry.payload.target_seat == null
        ? t('game.evCharmStart', { seat: entry.payload.seat })
        : t('game.evCharm', { seat: entry.payload.seat, target: entry.payload.target_seat });
      tone = ROLE_COLORS.werewolf.color;
      break;
    case 'fragrance_bond':
      // God view: the bound players are never told, and the pair is the whole
      // point of the beat.
      text = t('game.evEntangle', { seat: entry.payload.seat, targets: entry.payload.target_seats.join(t('game.listJoin')) });
      tone = ROLE_COLORS.witch.color;
      break;
    case 'wraith_illusion':
      // God view: name the stand-in when the event carries it (archives
      // recorded before the field existed do not).
      text = entry.payload.target_seat == null
        ? t('game.evIllusionStart', { seat: entry.payload.seat })
        : t('game.evIllusion', { seat: entry.payload.seat, target: entry.payload.target_seat });
      tone = ROLE_COLORS.werewolf.color;
      break;
    case 'wolf_beauty_revenge':
      text = t('game.evRevenge', { seat: entry.payload.seat, target: entry.payload.target_seat });
      tone = ROLE_COLORS.werewolf.color;
      break;
    case 'self_explode':
      text = t('game.evExplode', { seat: entry.payload.seat, target: entry.payload.target_seat });
      tone = ROLE_COLORS.werewolf.color;
      break;
    case 'dance_pool':
      text = t('game.evDance', { seat: entry.payload.seat, seats: entry.payload.pool_seats.join(t('game.listJoin')) });
      tone = ROLE_COLORS.witch.color;
      break;
    case 'mask_granted':
      text = t('game.evMask', { seat: entry.payload.seat, target: entry.payload.target_seat, inPool: t(entry.payload.in_pool ? 'game.evInPool' : 'game.evNotInPool') });
      tone = ROLE_COLORS.witch.color;
      break;
    case 'awakened_witch_assist_pick':
      text = t('game.evPoisonTarget', { target: entry.payload.target_seat, assistants: entry.payload.assistant_seats.join(t('game.listJoin')) });
      tone = ROLE_COLORS.witch.color;
      break;
    case 'awakened_witch_assist_chat': {
      const brief = entry.payload.text.length > 18
        ? `…${entry.payload.text.slice(-18)}`
        : entry.payload.text;
      text = t('game.evAssistChat', { seat: entry.payload.seat, brief });
      tone = ROLE_COLORS.witch.color;
      break;
    }
    case 'awakened_witch_assist_vote':
      text = t('game.evAssistVote', { seat: entry.payload.seat, vote: L.awakenedWitchVoteLabel(entry.payload.vote), target: entry.payload.target_seat });
      tone = ROLE_COLORS.witch.color;
      break;
    case 'awakened_witch_poison_result':
      text = t('game.evPoisonResult', { target: entry.payload.target_seat, result: L.awakenedWitchPoisonResultLabel(entry.payload.result) });
      tone = ROLE_COLORS.witch.color;
      break;
    case 'crimson_explode':
      text = t('game.evSilence', { seat: entry.payload.seat, count: entry.payload.silenced_seats.length });
      tone = ROLE_COLORS.werewolf.color;
      break;
    case 'sheriff_elected':
      text = entry.payload.seat == null
        ? t('game.evSheriffLost')
        : t('game.evSheriffElected', { seat: entry.payload.seat });
      break;
    case 'sheriff_badge':
      text = entry.payload.to_seat == null
        ? t('game.evBadgeTear', { seat: entry.payload.from_seat })
        : t('game.evBadgePass', { from: entry.payload.from_seat, to: entry.payload.to_seat });
      break;
    case 'sheriff_run':
      text = entry.payload.choice === 'run'
        ? t('game.evRun', { seat: entry.payload.seat })
        : t('game.evPassSeat', { seat: entry.payload.seat });
      break;
    case 'sheriff_withdraw':
      text = entry.payload.choice === 'withdraw'
        ? t('game.evWithdraw', { seat: entry.payload.seat })
        : t('game.evStay', { seat: entry.payload.seat });
      break;
    case 'sheriff_vote':
      text = entry.payload.target_seat == null
        ? t('game.evVoteAbstain', { seat: entry.payload.voter_seat })
        : t('game.evVoteFor', { seat: entry.payload.voter_seat, target: entry.payload.target_seat });
      break;
    case 'sheriff_side':
      text = t('game.evSheriffSide', { seat: entry.payload.seat, side: L.SHERIFF_SIDE_LABELS[entry.payload.side] ?? entry.payload.side });
      break;
    case 'narration':
      text = entry.payload.title;
      break;
    case 'commentary':
      text = entry.payload.title;
      tone = ROLE_COLORS.guard.color;
      break;
    case 'winner':
      text = t('game.evWin', { camp: L.WINNING_CAMP_LABELS[entry.payload.winning_camp] ?? entry.payload.winning_camp });
      tone = ROLE_COLORS.werewolf.color;
      break;
    default:
      return null;
  }

  return (
    <Box
      className="wk-event-summary"
      sx={{
        mt: 1,
        display: 'inline-flex',
        alignItems: 'center',
        gap: 1,
        px: 1.8,
        py: 0.5,
        borderRadius: 99,
        border: '1px solid',
        borderColor: 'divider',
        bgcolor: PLATE.soft,
      }}
    >
      <Box
        aria-hidden="true"
        sx={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          bgcolor: tone,
        }}
      />
      <Typography
        variant="caption"
        sx={{ color: tone, fontWeight: 600, letterSpacing: 0.75, fontSize: 'clamp(9px, 1.3cqh, 11.5px)' }}
      >
        {text}
      </Typography>
    </Box>
  );
}

// 中央舞台只放一行流式摘要，取末尾这么多字，完整正文看底部活动栏
const LIVE_LINE_CHARS = 40;

function LiveSpeechLine({ seat }: { seat: number }) {
  const { t } = useTranslation();
  const streaming = useGameStore((s) => s.streamingSpeech);
  const isFollowingLive = useGameStore((s) => s.isFollowingLive);
  if (!isFollowingLive || streaming === null || streaming.seat !== seat) return null;
  const { text, thinking } = streaming;
  if (!text && !thinking) return null;
  const body = text || thinking;
  const tail = body.length > LIVE_LINE_CHARS ? `…${body.slice(-LIVE_LINE_CHARS)}` : body;
  const line = text ? tail : t('game.evThinkingPrefix', { text: tail });
  return (
    <Typography
      data-testid="center-live-speech"
      variant="body2"
      noWrap
      sx={{
        mt: 0.8,
        mx: 'auto',
        maxWidth: '90cqi',
        color: text ? 'text.primary' : 'text.secondary',
        fontSize: 'clamp(10px, 1.6cqh, 13px)',
      }}
    >
      {line}
    </Typography>
  );
}

export default function CenterDisplay() {
  const { t } = useTranslation();
  const phase = useGameStore((s) => s.phase);
  const roundNumber = useGameStore((s) => s.roundNumber);
  const timeline = useGameStore((s) => s.timeline);
  const timelineIndex = useGameStore((s) => s.timelineIndex);
  const isPaused = useGameStore((s) => s.isPaused);
  const pauseInfo = useGameStore((s) => s.pauseInfo);
  const entry = timelineIndex >= 0 && timelineIndex < timeline.length
    ? timeline[timelineIndex]
    : null;

  if (isPaused && !entry) {
    return (
      <Box sx={{ textAlign: 'center', py: 2, bgcolor: BACKDROP.textPlate, backdropFilter: 'blur(12px)', borderRadius: 2 }}>
        <Typography variant="subtitle1" color="text.secondary">
          {pauseInfo?.reason ?? t('game.paused')}
        </Typography>
        <Typography variant="caption" color="text.disabled" sx={{ mt: 0.5, display: 'block' }}>
          {pauseInfo?.code ? t('game.pauseReason', { code: pauseInfo.code }) : t('game.pauseHint')}
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ width: '100%', textAlign: 'center' }}>
      <PhaseContent phase={phase} roundNumber={roundNumber} />
      {entry && (
        <EventSummary
          key={`summary-${timelineIndex}`}
          entry={entry}
          phase={phase}
          events={timeline}
          index={timelineIndex}
        />
      )}
      {entry?.event_type === 'speaking' && <LiveSpeechLine seat={entry.payload.seat} />}
    </Box>
  );
}
