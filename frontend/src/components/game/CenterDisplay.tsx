import { Box, Typography, keyframes } from '@mui/material';
import { useGameStore } from '../../store/gameStore';
import type { PublicReplayEvent } from '../../store/types';

const fadeIn = keyframes`
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
`;

// —— 阶段文案：带白天/黑夜的叙事化表达，增强中央张力 ——
const PHASE_LABELS: Record<string, string> = {
  waiting: '夜宴将启',
  role_deal: '身份落定',
  night: '黑夜 · 群狼苏醒',
  dawn: '白昼降临',
  last_words: '遗言时刻',
  sheriff_election: '警长竞选',
  speech: '白天 · 议论纷纷',
  vote_casting: '白天 · 投票表决',
  vote_resolution: '白天 · 揭票时刻',
  game_over: '尘埃落定',
  error: '对局异常',
};

const CAUSE_LABELS: Record<string, string> = {
  wolf_kill: '狼人袭击',
  poison: '毒杀',
  exile: '被放逐',
  hunter_shot: '猎人带走',
};

const THOUGHT_LABELS: Record<string, string> = {
  witch_reasoning: '女巫',
  seer_reasoning: '预言家',
  hunter_reasoning: '猎人',
  guard_reasoning: '守卫',
  witch_thought: '女巫',
  seer_thought: '预言家',
};

const NIGHT_ACTION_LABELS: Record<string, string> = {
  werewolf_kill: '狼人行动',
  witch_save: '女巫救人',
  witch_poison: '女巫毒人',
  seer_check: '预言家查验',
  hunter_shot: '猎人开枪',
  guard_protect: '守卫守护',
};

const panelSx = {
  textAlign: 'center',
  py: 2,
  px: 3,
  animation: `${fadeIn} 0.25s ease-out`,
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

// 战报：从已播放时间线中回溯最近的关键事件，生成叙事性一句战报
function buildReport(phase: string, events: PublicReplayEvent[]): string {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.event_type === 'death') {
      return `${e.payload.player_seat}号在夜色中出局，死因：${CAUSE_LABELS[e.payload.cause] ?? '未明'}`;
    }
    if (e.event_type === 'vote_result') {
      return e.payload.exiled_seat === null
        ? '公投平票，无人被放逐'
        : `${e.payload.exiled_seat}号被公投放逐，尘土落定`;
    }
    if (e.event_type === 'night_action' && e.payload.action_type === 'werewolf_kill') {
      return `狼人的刀锋昨夜指向 ${e.payload.target_seat}号`;
    }
  }
  if (phase === 'night' || phase === 'dawn') return '长夜未尽，狼人已在暗中谋划……';
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

  return (
    <Box sx={{ ...panelSx, py: 1.5 }}>
      <Typography
        sx={{
          fontSize: '0.62rem',
          fontWeight: 700,
          letterSpacing: 6,
          color: 'secondary.dark',
          fontFamily: '"Cinzel","Noto Serif SC",serif',
        }}
      >
        THE {ordinalEn(roundNumber)} DAY
      </Typography>
      <Typography
        sx={{
          mt: 0.5,
          fontSize: 46,
          fontWeight: 900,
          lineHeight: 1.2,
          letterSpacing: 8,
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
          fontSize: '0.95rem',
          fontWeight: 700,
          letterSpacing: 5,
          color: 'text.primary',
          pb: 1,
          borderBottom: '1px solid',
          borderColor: 'secondary.main',
        }}
      >
        {PHASE_LABELS[phase] || phase}
      </Typography>

      <Box
        sx={{
          mt: 1.5,
          mx: 'auto',
          maxWidth: 236,
          px: 1.5,
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
            fontSize: '0.56rem',
            mb: 0.1,
          }}
        >
          战报
        </Typography>
        <Typography variant="caption" sx={{ color: 'text.secondary', lineHeight: 1.8, fontSize: '0.7rem' }}>
          {report}
        </Typography>
      </Box>

      <Typography
        sx={{
          mt: 1,
          fontSize: '0.68rem',
          letterSpacing: 3,
          color: 'text.disabled',
        }}
      >
        存活 {alive}/{total} · 余狼 {wolvesRemain}
      </Typography>
    </Box>
  );
}

// 阶段页下方的一句话事件摘要（完整内容见底部活动栏 / 右侧编年史）
function EventSummary({ entry }: { entry: PublicReplayEvent }) {
  let text: string;
  let tone = '#E8C887';

  switch (entry.event_type) {
    case 'speech':
      text = entry.payload.phase === 'last_words'
        ? `${entry.payload.player_seat}号遗言`
        : `${entry.payload.player_seat}号正在发言`;
      break;
    case 'witch_thought':
    case 'seer_thought':
    case 'night_thought': {
      const who = THOUGHT_LABELS[
        'action_type' in entry.payload ? entry.payload.action_type : entry.event_type
      ] ?? '角色';
      text = `${who}思考中`;
      tone = '#C4B5FD';
      break;
    }
    case 'wolf_chat_message':
      text = '狼群密谋中';
      tone = '#F4B3B6';
      break;
    case 'night_action':
      text = `${NIGHT_ACTION_LABELS[entry.payload.action_type] ?? '夜晚行动'} · 目标 ${entry.payload.target_seat}号`;
      tone = '#9DC8E8';
      break;
    case 'death':
      text = `${entry.payload.player_seat}号 ${CAUSE_LABELS[entry.payload.cause] ?? '出局'}`;
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
        sx={{ color: tone, fontWeight: 700, letterSpacing: 1.5, fontSize: '0.72rem' }}
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
      <Box sx={{ textAlign: 'center', py: 2 }}>
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
      {entry && <EventSummary key={`summary-${timelineIndex}`} entry={entry} />}
    </Box>
  );
}
