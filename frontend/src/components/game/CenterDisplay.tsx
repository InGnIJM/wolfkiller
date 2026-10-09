import { Box, Typography, keyframes } from '@mui/material';
import { useGameStore } from '../../store/gameStore';
import type { GamePhase, PublicReplayEvent } from '../../store/types';
import {
  CENTER_PHASE_LABELS, DEATH_CAUSE_LABELS, DEGRADED_ACTION_LABEL, NIGHT_ACTION_LABELS,
  NIGHT_ACTION_ROLE_NAMES, NIGHT_EVENT_ROLE_NAMES, PLAYER_STATUS_LABELS,
  SHERIFF_SIDE_LABELS, THOUGHT_ROLE_NAMES,
} from '../../labels';
import { BACKDROP } from '../../theme/tokens';

const fadeIn = keyframes`
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
`;

function campLabel(camp: string): string {
  return camp === 'werewolf' ? '狼人' : camp === 'good' ? '好人' : camp;
}

function deathCauseLabel(
  event: Extract<PublicReplayEvent, { event_type: 'death' }>,
  phase: string,
  events: PublicReplayEvent[],
  index: number,
): string {
  if (event.payload.cause !== 'self_explode') {
    return DEATH_CAUSE_LABELS[event.payload.cause] ?? '未明';
  }
  if (phase === 'sheriff_election') return '竞选自爆';
  const nearby = [events[index - 1], events[index + 1]];
  if (nearby.some((item) => item?.event_type === 'sheriff_elected' && item.payload.reason === 'explode')) {
    return '竞选自爆';
  }
  return DEATH_CAUSE_LABELS.self_explode;
}

// 中央面板内容全部使用容器查询单位（cqh 相对舞台高度 / cqi 相对面板宽度），
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

function nightRole(entry: PublicReplayEvent | null): string | null {
  if (!entry) return null;
  // 舞者/假面没有 `night_action` 行（载荷形状不同），它们各自成事件类型。
  const named = NIGHT_EVENT_ROLE_NAMES[entry.event_type];
  if (named) return named;
  if (entry.event_type === 'night_action') {
    return NIGHT_ACTION_ROLE_NAMES[entry.payload.action_type] ?? null;
  }
  if (entry.event_type === 'night_thought') {
    return THOUGHT_ROLE_NAMES[entry.payload.action_type] ?? null;
  }
  if (entry.event_type === 'witch_thought' || entry.event_type === 'seer_thought') {
    return THOUGHT_ROLE_NAMES[entry.event_type] ?? null;
  }
  if (entry.event_type === 'wolf_chat_message' || entry.event_type === 'wolf_vote') {
    return '狼人';
  }
  return null;
}

// 战报：从已播放时间线中回溯最近的关键事件，生成叙事性一句战报
function buildReport(phase: string, events: PublicReplayEvent[]): string {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.event_type === 'death') {
      return `${e.payload.player_seat}号在夜色中出局，死因：${deathCauseLabel(e, phase, events, i)}`;
    }
    if (e.event_type === 'vote_result') {
      return e.payload.exiled_seat === null
        ? '公投平票，无人被放逐'
        : `${e.payload.exiled_seat}号被公投放逐，尘土落定`;
    }
    if (e.event_type === 'sheriff_elected') {
      return e.payload.seat == null
        ? '警长竞选结束，警徽流失'
        : `${e.payload.seat}号当选警长`;
    }
    if (e.event_type === 'sheriff_badge') {
      return e.payload.to_seat == null
        ? `${e.payload.from_seat}号撕毁警徽`
        : `${e.payload.from_seat}号将警徽移交给${e.payload.to_seat}号`;
    }
    if (e.event_type === 'sheriff_run') {
      return e.payload.choice === 'run'
        ? `${e.payload.seat}号上警竞选`
        : `${e.payload.seat}号选择不上警`;
    }
    if (e.event_type === 'night_action' && e.payload.action_type === 'werewolf_kill') {
      return `狼人的刀锋昨夜指向 ${e.payload.target_seat}号`;
    }
  }
  if (phase === 'night' || phase === 'dawn') return '长夜未尽，狼人已在暗中谋划……';
  if (phase === 'sheriff_election') return '警长竞选进行中……';
  if (phase === 'speech') return '众人各执一词，真伪难辨……';
  if (phase === 'vote_casting' || phase === 'vote_resolution') return '公投在即，人心浮动……';
  return '村中灯火未熄，只待天明';
}

// 中央阶段页：英文眉标 + 第X天金字 + 阶段名 + 战报 + 存活与余狼情报
function PhaseContent({ phase, roundNumber }: { phase: string; roundNumber: number }) {
  const players = useGameStore((s) => s.players);
  const timeline = useGameStore((s) => s.timeline);
  const timelineIndex = useGameStore((s) => s.timelineIndex);

  const alive = Object.values(players).filter((p) => p.is_alive).length;
  const total = Object.keys(players).length;
  const wolvesRemain = Object.values(players).filter((p) => p.camp === 'werewolf' && p.is_alive).length;
  const report = buildReport(phase, timeline.slice(0, timelineIndex + 1));

  const currentEntry = timelineIndex >= 0 && timelineIndex < timeline.length
    ? timeline[timelineIndex]
    : null;
  // 组件签名的 phase 是 string（未知取值原样显示），查表时按联合收窄。
  let phaseLabel = CENTER_PHASE_LABELS[phase as GamePhase] || phase;
  if (phase === 'night') {
    const role = nightRole(currentEntry);
    if (role) phaseLabel = `黑夜 · ${role}`;
  }

  return (
    <Box sx={{ ...panelSx, py: 'clamp(5px, 1.8cqh, 12px)' }}>
      <Typography
        sx={{
          fontSize: 'clamp(8px, 1.15cqh, 10px)',
          fontWeight: 700,
          letterSpacing: 'clamp(2.5px, 1.1cqi, 6px)',
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
          fontWeight: 900,
          lineHeight: 1.2,
          letterSpacing: 'clamp(2px, 2.4cqi, 8px)',
          fontFamily: '"Cinzel","Noto Serif SC",serif',
          background: 'linear-gradient(180deg, #F7EFE2, #CAA96A)',
          WebkitBackgroundClip: 'text',
          backgroundClip: 'text',
          color: 'transparent',
          textShadow: '0 18px 50px rgba(212,168,83,0.22)',
        }}
      >
        第{upperDay(roundNumber)}天
      </Typography>
      <Typography
        sx={{
          display: 'inline-block',
          mt: 0.4,
          fontSize: 'clamp(11px, 1.9cqh, 15.2px)',
          fontWeight: 700,
          letterSpacing: 'clamp(2px, 1.7cqi, 5px)',
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
          borderColor: 'rgba(229,72,77,0.4)',
          borderRadius: 1.5,
          bgcolor: 'rgba(229,72,77,0.05)',
          textAlign: 'left',
        }}
      >
        <Typography
          variant="caption"
          sx={{
            display: 'block',
            color: '#F4B3B6',
            fontWeight: 800,
            letterSpacing: 3,
            fontSize: 'clamp(8px, 1.05cqh, 9px)',
            mb: 0.1,
          }}
        >
          战报
        </Typography>
        <Typography variant="caption" sx={{ color: 'text.secondary', lineHeight: 1.8, fontSize: 'clamp(9px, 1.3cqh, 11.2px)' }}>
          {report}
        </Typography>
      </Box>

      <Typography
        sx={{
          mt: 1,
          fontSize: 'clamp(9px, 1.25cqh, 10.9px)',
          letterSpacing: 'clamp(1px, 0.9cqi, 3px)',
          color: 'text.disabled',
        }}
      >
        存活 {alive}/{total} · 余狼 {wolvesRemain}
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
  let text: string;
  let tone = '#E8C887';

  switch (entry.event_type) {
    case 'speaking':
      text = `${entry.payload.seat}号正在发言`;
      tone = '#E5484D';
      break;
    case 'speech':
      text = entry.payload.phase === 'last_words'
        ? `${entry.payload.player_seat}号遗言`
        : entry.payload.phase === 'sheriff_election'
          ? `${entry.payload.player_seat}号竞选发言`
          : `${entry.payload.player_seat}号正在发言`;
      break;
    case 'witch_thought':
    case 'seer_thought': {
      // 旧档事件类型本身就是「谁在想」，新档要看 payload.action_type，分开查表才能收窄键。
      const who = THOUGHT_ROLE_NAMES[entry.event_type] ?? '角色';
      text = `${who}思考中`;
      tone = '#C4B5FD';
      break;
    }
    case 'night_thought': {
      // 引擎代行时「某角色思考中」是错的：它没有思考，是引擎替它提交的。
      const who = THOUGHT_ROLE_NAMES[entry.payload.action_type] ?? '角色';
      text = entry.payload.degraded
        ? `${entry.payload.seat}号${DEGRADED_ACTION_LABEL}`
        : `${who}思考中`;
      tone = '#C4B5FD';
      break;
    }
    case 'wolf_chat_message':
      text = '狼群密谋中';
      tone = '#F4B3B6';
      break;
    case 'wolf_vote':
      // 狼票走狼队频道的粉色，与白天投票/警票的金色摘要区分昼夜
      text = entry.payload.degraded
        ? `${entry.payload.seat}号${DEGRADED_ACTION_LABEL}`
        : entry.payload.target_seat === null
          ? `${entry.payload.seat}号弃票`
          : `${entry.payload.seat}号票给 ${entry.payload.target_seat}号`;
      tone = '#F4B3B6';
      break;
    case 'night_action':
      text = `${NIGHT_ACTION_LABELS[entry.payload.action_type] ?? '夜晚行动'} · 目标 ${entry.payload.target_seat}号`;
      tone = '#9DC8E8';
      break;
    case 'death':
      text = `${entry.payload.player_seat}号 ${deathCauseLabel(entry, phase, events, index)}`;
      tone = '#F4B3B6';
      break;
    case 'vote':
      text = entry.payload.target_seat === null
        ? `${entry.payload.voter_seat}号弃权`
        : `${entry.payload.voter_seat}号投给 ${entry.payload.target_seat}号`;
      break;
    case 'vote_result':
      text = entry.payload.exiled_seat === null ? '平票，无人被放逐' : `${entry.payload.exiled_seat}号被放逐`;
      break;
    case 'technical_abstain':
      text = `${entry.payload.voter_seat}号 系统代投弃权`;
      tone = '#F4B3B6';
      break;
    case 'player_status':
      // 结算给一个座位留下的标记：中毒/中枪但被延迟结算，或延迟死亡本身。
      text = `${entry.payload.player_seat}号 ${
        PLAYER_STATUS_LABELS[entry.payload.status] ?? entry.payload.status
      }`;
      tone = '#E8C887';
      break;
    case 'exile_cancelled':
      text = `${entry.payload.target_seat}号翻牌免于出局`;
      break;
    case 'knight_duel':
      text = `${entry.payload.seat}号决斗${entry.payload.target_seat}号（${campLabel(entry.payload.camp)}）`;
      tone = '#E5484D';
      break;
    case 'wolf_beauty_charm':
      // God view: name the charmed seat when the event carries it (archives
      // recorded before the field existed do not).
      text = entry.payload.target_seat == null
        ? `${entry.payload.seat}号发动魅惑`
        : `${entry.payload.seat}号魅惑${entry.payload.target_seat}号`;
      tone = '#F4B3B6';
      break;
    case 'wolf_beauty_revenge':
      text = `${entry.payload.seat}号出局，${entry.payload.target_seat}号殉情`;
      tone = '#F4B3B6';
      break;
    case 'self_explode':
      text = `${entry.payload.seat}号自爆，带走 ${entry.payload.target_seat}号`;
      tone = '#F4B3B6';
      break;
    case 'dance_pool':
      text = `${entry.payload.seat}号起舞，舞池：${entry.payload.pool_seats.join('、')}号`;
      tone = '#C9A7E8';
      break;
    case 'mask_granted':
      text = `${entry.payload.seat}号给${entry.payload.target_seat}号戴面具（${entry.payload.in_pool ? '在' : '不在'}舞池）`;
      tone = '#C9A7E8';
      break;
    case 'crimson_explode':
      text = `${entry.payload.seat}号自爆，封印${entry.payload.silenced_seats.length}个好人技能`;
      tone = '#F4B3B6';
      break;
    case 'sheriff_elected':
      text = entry.payload.seat == null
        ? '警长竞选结束，警徽流失'
        : `${entry.payload.seat}号当选警长`;
      break;
    case 'sheriff_badge':
      text = entry.payload.to_seat == null
        ? `${entry.payload.from_seat}号撕毁警徽`
        : `${entry.payload.from_seat}号将警徽移交给${entry.payload.to_seat}号`;
      break;
    case 'sheriff_run':
      text = entry.payload.choice === 'run'
        ? `${entry.payload.seat}号上警`
        : `${entry.payload.seat}号过`;
      break;
    case 'sheriff_withdraw':
      text = entry.payload.choice === 'withdraw'
        ? `${entry.payload.seat}号退水`
        : `${entry.payload.seat}号留下`;
      break;
    case 'sheriff_vote':
      text = entry.payload.target_seat == null
        ? `${entry.payload.voter_seat}号弃权`
        : `${entry.payload.voter_seat}号投给 ${entry.payload.target_seat}号`;
      break;
    case 'sheriff_side':
      text = `${entry.payload.seat}号选择${SHERIFF_SIDE_LABELS[entry.payload.side] ?? entry.payload.side}发言`;
      break;
    case 'narration':
      text = entry.payload.title;
      break;
    case 'winner':
      text = entry.payload.winning_camp === 'werewolf' ? '狼人阵营获胜' : '好人阵营获胜';
      tone = '#F4B3B6';
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
        bgcolor: 'rgba(23,18,33,0.6)',
      }}
    >
      <Box
        aria-hidden="true"
        sx={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          bgcolor: tone,
          boxShadow: `0 0 8px ${tone}`,
        }}
      />
      <Typography
        variant="caption"
        sx={{ color: tone, fontWeight: 700, letterSpacing: 1.5, fontSize: 'clamp(9px, 1.3cqh, 11.5px)' }}
      >
        {text}
      </Typography>
    </Box>
  );
}

export default function CenterDisplay() {
  const { phase, roundNumber, timeline, timelineIndex, isPaused } = useGameStore();
  const entry = timelineIndex >= 0 && timelineIndex < timeline.length
    ? timeline[timelineIndex]
    : null;

  if (isPaused && !entry) {
    return (
      <Box sx={{ textAlign: 'center', py: 2, bgcolor: BACKDROP.textPlate, backdropFilter: 'blur(12px)', borderRadius: 2 }}>
        <Typography variant="subtitle1" color="text.secondary">游戏已暂停</Typography>
        <Typography variant="caption" color="text.disabled" sx={{ mt: 0.5, display: 'block' }}>
          点击播放按钮继续
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
    </Box>
  );
}
