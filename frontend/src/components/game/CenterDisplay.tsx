import { Box, Typography, keyframes } from '@mui/material';
import { useGameStore } from '../../store/gameStore';
import type { PublicReplayEvent } from '../../store/types';

const fadeIn = keyframes`
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
`;

const PHASE_LABELS: Record<string, string> = {
  waiting: '等待游戏开始',
  role_deal: '游戏准备中',
  night: '夜晚进行中',
  dawn: '天亮了',
  last_words: '遗言阶段',
  sheriff_election: '警长竞选阶段',
  speech: '发言阶段',
  vote_casting: '投票阶段',
  vote_resolution: '公布投票结果',
  game_over: '游戏结束',
  error: '游戏异常结束',
};

const CAUSE_LABELS: Record<string, string> = {
  wolf_kill: '夜间死亡',
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

// 中央阶段页：DAY N + 大字阶段名 + 存活统计（中心只展示阶段信息）
function PhaseContent({ phase, roundNumber }: { phase: string; roundNumber: number }) {
  const players = useGameStore((s) => s.players);
  const alive = Object.values(players).filter((p) => p.is_alive).length;
  const total = Object.keys(players).length;

  return (
    <Box sx={{ ...panelSx, py: 2.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1.5 }}>
        <Box aria-hidden="true" sx={{ width: 38, height: '1px', bgcolor: 'divider' }} />
        <Typography
          variant="caption"
          color="secondary.dark"
          sx={{ fontWeight: 700, letterSpacing: 3, fontFamily: '"Cinzel","Noto Serif SC",serif' }}
        >
          DAY {roundNumber}
        </Typography>
        <Box aria-hidden="true" sx={{ width: 38, height: '1px', bgcolor: 'divider' }} />
      </Box>
      <Typography
        variant="h5"
        color="text.primary"
        sx={{
          mt: 1,
          fontWeight: 900,
          letterSpacing: 8,
          textShadow: '0 0 28px rgba(212,168,83,0.28)',
        }}
      >
        {PHASE_LABELS[phase] || phase}
      </Typography>
      <Box
        sx={{
          mt: 1.5,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 1,
          px: 1.8,
          py: 0.6,
          borderRadius: 99,
          border: '1px solid',
          borderColor: 'divider',
          bgcolor: 'rgba(23,18,33,0.6)',
        }}
      >
        <Typography variant="caption" color="text.disabled" sx={{ letterSpacing: 2 }}>存活</Typography>
        <Typography sx={{ color: 'secondary.main', fontWeight: 800, fontSize: '0.85rem' }}>
          {alive}/{total}
        </Typography>
      </Box>
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
        mt: 1.5,
        display: 'inline-flex',
        alignItems: 'center',
        gap: 1,
        px: 1.8,
        py: 0.6,
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
