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
  poison: '夜间死亡',
  hunter_shot: '出局',
  exile: '被放逐',
};

const NIGHT_ACTION_LABELS: Record<string, string> = {
  werewolf_kill: '狼人行动',
  witch_save: '女巫救人',
  witch_poison: '女巫毒人',
  seer_check: '预言家查验',
  hunter_shot: '猎人开枪',
  guard_protect: '守卫守护',
};

const THOUGHT_LABELS: Record<string, string> = {
  witch_reasoning: '女巫思考',
  seer_reasoning: '预言家思考',
  hunter_reasoning: '猎人思考',
  guard_reasoning: '守卫思考',
  witch_thought: '女巫思考',
  seer_thought: '预言家思考',
};

const CAMP_LABELS: Record<string, string> = {
  good: '好人',
  werewolf: '狼人',
};

const panelSx = {
  textAlign: 'center',
  py: 2,
  px: 3,
  animation: `${fadeIn} 0.25s ease-out`,
};

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

function PublicEventContent({ entry }: { entry: PublicReplayEvent }) {
  switch (entry.event_type) {
    case 'speech':
      return (
        <Box sx={panelSx}>
          <Typography variant="subtitle2" color="secondary.main" gutterBottom sx={{ fontWeight: 700, letterSpacing: 2 }}>
            {entry.payload.phase === 'last_words'
              ? `${entry.payload.player_seat}号玩家遗言`
              : `${entry.payload.player_seat}号玩家发言`}
          </Typography>
          <Typography
            variant="body2"
            color="text.primary"
            sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 260, overflowY: 'auto', lineHeight: 1.9, fontSize: '0.875rem' }}
          >
            {entry.payload.text}
          </Typography>
        </Box>
      );
    case 'vote':
      return (
        <Box sx={panelSx}>
          <Typography variant="subtitle2" color="warning.light" gutterBottom>
            {entry.payload.voter_seat}号玩家投票
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {entry.payload.target_seat === null ? '弃权' : `投给 ${entry.payload.target_seat}号`}
          </Typography>
        </Box>
      );
    case 'vote_result':
      return (
        <Box sx={{ ...panelSx, py: 1.8 }}>
          <Typography variant="subtitle1" color="warning.light" gutterBottom>
            投票结果
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {entry.payload.exiled_seat === null
              ? '平票，无人被放逐'
              : `${entry.payload.exiled_seat}号玩家被放逐出局`}
          </Typography>
        </Box>
      );
    case 'night_action': {
      const action = entry.payload;
      const counts = action.vote_counts
        ? Object.entries(action.vote_counts).map(([target, count]) => `${target}号×${count}`).join('，')
        : null;
      const detail = action.action_type === 'seer_check'
        ? `查验了 ${action.target_seat}号，身份为${CAMP_LABELS[action.result ?? ''] ?? '未知'}`
        : counts
          ? `刀向 ${action.target_seat}号（票型：${counts}）`
          : `目标 ${action.target_seat}号`;
      return (
        <Box sx={{ ...panelSx, py: 1.8 }}>
          <Typography variant="subtitle1" color="info.light" gutterBottom>
            {NIGHT_ACTION_LABELS[action.action_type] ?? '夜晚行动'}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {detail}
          </Typography>
        </Box>
      );
    }
    case 'narration':
      return (
        <Box sx={{ ...panelSx, py: 3 }}>
          <Typography variant="h5" color="secondary.light" gutterBottom sx={{ fontWeight: 900, letterSpacing: 4, textShadow: '0 0 26px rgba(212,168,83,0.25)' }}>
            {entry.payload.title}
          </Typography>
          <Typography variant="body1" color="text.secondary" sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>
            {entry.payload.text}
          </Typography>
        </Box>
      );
    case 'wolf_chat_message':
      return (
        <Box
          sx={{
            ...panelSx,
            textAlign: 'left',
            py: 1.5,
            borderLeft: '3px solid',
            borderColor: 'error.main',
          }}
        >
          <Typography variant="body1" color="text.secondary" sx={{ wordBreak: 'break-word' }}>
            {entry.payload.seat}号：{entry.payload.text}
          </Typography>
        </Box>
      );
    case 'wolf_vote':
      return (
        <Box sx={panelSx}>
          <Typography variant="subtitle2" color="warning.light" gutterBottom>
            {entry.payload.seat}号 出票
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {entry.payload.target_seat === null
              ? `弃权（${entry.payload.reasoning}）`
              : `→ ${entry.payload.target_seat}号（${entry.payload.reasoning}）`}
          </Typography>
        </Box>
      );
    case 'witch_thought':
    case 'seer_thought':
      return (
        <Box sx={{ ...panelSx, py: 1.8 }}>
          <Typography variant="subtitle1" color="info.light" gutterBottom>
            {THOUGHT_LABELS[entry.event_type]} · {entry.payload.seat}号
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: 'pre-wrap' }}>
            {entry.payload.text}
          </Typography>
        </Box>
      );
    case 'night_thought': {
      const { action_type, seat, target_seat, reasoning } = entry.payload;
      const body = target_seat === null
        ? `不行动：${reasoning || '（无理由）'}`
        : `目标 ${target_seat}号：${reasoning || '（无理由）'}`;
      return (
        <Box sx={{ ...panelSx, py: 1.8 }}>
          <Typography variant="subtitle1" color="info.light" gutterBottom>
            {THOUGHT_LABELS[action_type] ?? '夜间思考'} · {seat}号
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: 'pre-wrap' }}>
            {body}
          </Typography>
        </Box>
      );
    }
    case 'death':
      return (
        <Box sx={{ ...panelSx, py: 1.8 }}>
          <Typography variant="subtitle1" color="error.light" gutterBottom>
            死亡公告
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {entry.payload.player_seat}号玩家{CAUSE_LABELS[entry.payload.cause] || '死亡'}
          </Typography>
        </Box>
      );
    case 'phase':
      return <PhaseContent phase={entry.payload.phase} roundNumber={entry.payload.round_number} />;
    case 'winner':
      return (
        <Box sx={{ ...panelSx, py: 1.8 }}>
          <Typography variant="h6" color="secondary.light" sx={{ fontWeight: 700, letterSpacing: 2 }}>
            游戏结束
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {entry.payload.winning_camp === 'werewolf' ? '狼人阵营获胜' : '好人阵营获胜'}
          </Typography>
        </Box>
      );
  }
}

export default function CenterDisplay() {
  const { timeline, timelineIndex, isPaused, phase, roundNumber } = useGameStore();
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

  if (!entry) return <PhaseContent phase={phase} roundNumber={roundNumber} />;

  return <PublicEventContent key={`entry-${timelineIndex}`} entry={entry} />;
}
