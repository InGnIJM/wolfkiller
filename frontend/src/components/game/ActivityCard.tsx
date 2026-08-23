import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import { keyframes } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { useGameStore } from '../../store/gameStore';
import type { PublicReplayEvent } from '../../store/types';
import { AVATAR_PALETTE, INK } from '../../theme/tokens';

const livePulse = keyframes`
  0%, 100% { opacity: 0.4; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1.15); }
`;

const THOUGHT_LABELS: Record<string, string> = {
  witch_reasoning: '女巫思考',
  seer_reasoning: '预言家思考',
  hunter_reasoning: '猎人思考',
  guard_reasoning: '守卫思考',
  witch_thought: '女巫思考',
  seer_thought: '预言家思考',
};

const CAUSE_LABELS: Record<string, string> = {
  wolf_kill: '夜间死亡',
  poison: '毒杀',
  exile: '被放逐',
  hunter_shot: '猎人带走',
};

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function formatClock(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '--:--';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

interface CardProps {
  tone: string;
  children: React.ReactNode;
}

// 底部活动卡框架：顶部色条 + 标题行 + 正文
function ActivityFrame({ tone, children }: CardProps) {
  return (
    <Box
      sx={{
        mx: 2.5,
        mb: 2,
        borderRadius: 2.5,
        border: '1px solid',
        borderColor: 'divider',
        borderTop: '2px solid',
        borderTopColor: tone,
        bgcolor: 'rgba(23,18,33,0.82)',
        px: 2.5,
        py: 1.6,
      }}
    >
      {children}
    </Box>
  );
}

function LiveTag({ label }: { label: string }) {
  return (
    <Box
      sx={{
        ml: 'auto',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 0.8,
        color: '#F4B3B6',
        fontSize: '0.65rem',
        fontWeight: 700,
        letterSpacing: 2,
      }}
    >
      <Box
        aria-hidden="true"
        sx={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          bgcolor: '#E5484D',
          boxShadow: '0 0 10px rgba(229,72,77,0.9)',
          animation: `${livePulse} 1.4s ease-in-out infinite`,
        }}
      />
      {label}
    </Box>
  );
}

function SeatAvatar({ seat, size = 40 }: { seat: number; size?: number }) {
  const color = AVATAR_PALETTE[(seat - 1) % AVATAR_PALETTE.length];
  return (
    <Box
      aria-hidden="true"
      sx={{
        width: size,
        height: size,
        borderRadius: '50%',
        display: 'grid',
        placeItems: 'center',
        color: INK.primary,
        fontWeight: 800,
        fontSize: size * 0.4,
        bgcolor: color,
        border: '1px solid rgba(212,168,83,0.35)',
        boxShadow: '0 0 14px rgba(229,72,77,0.22)',
        flexShrink: 0,
      }}
    >
      {seat}
    </Box>
  );
}

function SpeechView({
  payload,
  timestamp,
  durationSec,
}: {
  payload: Extract<PublicReplayEvent['payload'], { player_seat: number; text: string }>;
  timestamp: string;
  durationSec: number | null;
}) {
  const isLastWords = payload.phase === 'last_words';
  return (
    <ActivityFrame tone="#E5484D">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1 }}>
        <SeatAvatar seat={payload.player_seat} />
        <Box>
          <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
            {payload.player_seat}号
          </Typography>
          <Typography variant="caption" sx={{ color: 'secondary.main', letterSpacing: 3, fontWeight: 600 }}>
            {isLastWords ? '遗言' : '正在发言'}
          </Typography>
        </Box>
        <LiveTag label={isLastWords ? 'LAST WORDS' : 'LIVE · 第' + String(payload.round_number) + '轮'} />
      </Box>
      <Typography
        variant="body2"
        sx={{
          color: 'text.primary',
          lineHeight: 2,
          fontSize: '0.875rem',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          maxHeight: 150,
          overflowY: 'auto',
          pr: 0.5,
        }}
      >
        {payload.text}
      </Typography>
      <Box
        sx={{
          mt: 1.2,
          pt: 0.8,
          borderTop: '1px dashed',
          borderColor: 'divider',
          display: 'flex',
          flexWrap: 'wrap',
          gap: 2.5,
          fontSize: '0.6rem',
          color: 'text.disabled',
          letterSpacing: 1,
        }}
      >
        <span>第{payload.round_number}轮 · {isLastWords ? '遗言' : '发言'}</span>
        <span>时点 {formatClock(timestamp)}</span>
        <span>耗时 {durationSec === null ? '--:--' : formatDuration(durationSec)}</span>
        <span>tokens --</span>
      </Box>
    </ActivityFrame>
  );
}

function ThoughtView({
  label,
  seat,
  text,
}: {
  label: string;
  seat: number | null;
  text: string;
}) {
  return (
    <ActivityFrame tone="#B08BE0">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1 }}>
        {seat !== null ? <SeatAvatar seat={seat} /> : <Box sx={{ width: 40 }} />}
        <Box>
          <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
            {label}
            {seat !== null ? ` · ${seat}号` : ''}
          </Typography>
          <Typography variant="caption" sx={{ color: '#B08BE0', letterSpacing: 2, fontWeight: 600 }}>
            内心思考
          </Typography>
        </Box>
        <LiveTag label="THINKING" />
      </Box>
      <Typography
        variant="body2"
        sx={{
          color: 'text.secondary',
          lineHeight: 2,
          fontSize: '0.875rem',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          maxHeight: 150,
          overflowY: 'auto',
          pr: 0.5,
        }}
      >
        {text}
      </Typography>
    </ActivityFrame>
  );
}

function ChatView({ seat, text }: { seat: number; text: string }) {
  return (
    <ActivityFrame tone="#F4B3B6">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1 }}>
        <SeatAvatar seat={seat} />
        <Box>
          <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
            {seat}号
          </Typography>
          <Typography variant="caption" sx={{ color: '#F4B3B6', letterSpacing: 2, fontWeight: 700 }}>
            狼群密谋
          </Typography>
        </Box>
        <LiveTag label="WOLF CHAT" />
      </Box>
      <Typography
        variant="body2"
        sx={{
          color: 'text.secondary',
          lineHeight: 2,
          fontSize: '0.875rem',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {text}
      </Typography>
    </ActivityFrame>
  );
}

function DeathView({ payload }: { payload: Extract<PublicReplayEvent['payload'], { player_seat: number; cause: string; round_number: number }> }) {
  return (
    <ActivityFrame tone="#F6686C">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
        <Box
          aria-hidden="true"
          sx={{
            width: 40,
            height: 40,
            borderRadius: '50%',
            display: 'grid',
            placeItems: 'center',
            color: 'background.paper',
            bgcolor: '#E5484D',
            boxShadow: '0 0 14px rgba(229,72,77,0.5)',
            flexShrink: 0,
          }}
        >
          <CloseIcon sx={{ fontSize: 20 }} />
        </Box>
        <Box>
          <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2, color: '#F4B3B6' }}>
            {payload.player_seat}号玩家出局
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ letterSpacing: 1.5 }}>
            {CAUSE_LABELS[payload.cause] ?? payload.cause} · 第{payload.round_number}轮
          </Typography>
        </Box>
        <LiveTag label="DEATH" />
      </Box>
    </ActivityFrame>
  );
}

function VoteResultView({ payload }: { payload: Extract<PublicReplayEvent['payload'], { exiled_seat: number | null }> }) {
  const exiled = payload.exiled_seat === null ? '平票，无人被放逐' : `${payload.exiled_seat}号被放逐`;
  return (
    <ActivityFrame tone="#D4A853">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
        <Box
          aria-hidden="true"
          sx={{
            width: 40,
            height: 40,
            borderRadius: '50%',
            display: 'grid',
            placeItems: 'center',
            color: 'background.paper',
            bgcolor: '#D4A853',
            boxShadow: '0 0 14px rgba(212,168,83,0.5)',
            flexShrink: 0,
            fontSize: 20,
            fontWeight: 800,
          }}
        >
          票
        </Box>
        <Box>
          <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2, color: '#E8C887' }}>
            {exiled}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ letterSpacing: 1.5 }}>
            投票结果 · 第{payload.round_number}轮
          </Typography>
        </Box>
        <LiveTag label="VOTE RESULT" />
      </Box>
    </ActivityFrame>
  );
}

export default function ActivityCard() {
  const { timeline, timelineIndex } = useGameStore();
  const entry = timelineIndex >= 0 && timelineIndex < timeline.length
    ? timeline[timelineIndex]
    : null;

  if (!entry) return null;

  // 发言耗时：距上一条事件的时间差（秒）
  let durationSec: number | null = null;
  if (timelineIndex > 0) {
    const cur = new Date(entry.timestamp).getTime();
    const prev = new Date(timeline[timelineIndex - 1].timestamp).getTime();
    if (!Number.isNaN(cur) && !Number.isNaN(prev) && cur >= prev) {
      durationSec = Math.round((cur - prev) / 1000);
    }
  }

  switch (entry.event_type) {
    case 'speech':
      return <SpeechView payload={entry.payload} timestamp={entry.timestamp} durationSec={durationSec} />;
    case 'witch_thought':
    case 'seer_thought':
      return (
        <ThoughtView
          label={THOUGHT_LABELS[entry.event_type] ?? '思考'}
          seat={entry.payload.seat}
          text={entry.payload.text}
        />
      );
    case 'night_thought':
      return (
        <ThoughtView
          label={THOUGHT_LABELS[entry.payload.action_type] ?? '夜间思考'}
          seat={entry.payload.seat}
          text={
            entry.payload.target_seat === null
              ? `不行动：${entry.payload.reasoning || '（无理由）'}`
              : `目标 ${entry.payload.target_seat}号：${entry.payload.reasoning || '（无理由）'}`
          }
        />
      );
    case 'wolf_chat_message':
      return <ChatView seat={entry.payload.seat} text={entry.payload.text} />;
    case 'death':
      return <DeathView payload={entry.payload} />;
    case 'vote_result':
      return <VoteResultView payload={entry.payload} />;
    default:
      return null;
  }
}
