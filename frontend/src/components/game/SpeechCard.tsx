import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import { keyframes } from '@mui/material';
import { useGameStore } from '../../store/gameStore';
import type { PublicReplayEvent } from '../../store/types';
import { AVATAR_PALETTE, INK } from '../../theme/tokens';

type SpeechEntry = Extract<PublicReplayEvent, { event_type: 'speech' }>;

function asSpeech(entry: PublicReplayEvent | null): entry is SpeechEntry {
  return entry?.event_type === 'speech';
}

const livePulse = keyframes`
  0%, 100% { opacity: 0.4; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1.15); }
`;

// 舞台下方的发言卡：展示当前讲话者的完整陈词（风格稿：血月剧场 speech-card）
export default function SpeechCard() {
  const { timeline, timelineIndex } = useGameStore();
  const entry = timelineIndex >= 0 && timelineIndex < timeline.length
    ? timeline[timelineIndex]
    : null;

  if (!asSpeech(entry)) return null;

  const seat = entry.payload.player_seat;
  const isLastWords = entry.payload.phase === 'last_words';
  const avatarColor = AVATAR_PALETTE[(seat - 1) % AVATAR_PALETTE.length];

  return (
    <Box
      sx={{
        mx: 2.5,
        mb: 2,
        borderRadius: 2.5,
        border: '1px solid',
        borderColor: 'divider',
        borderTop: '2px solid',
        borderTopColor: 'secondary.main',
        bgcolor: 'rgba(23,18,33,0.82)',
        px: 2.5,
        py: 1.8,
        position: 'relative',
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1 }}>
        <Box
          aria-hidden="true"
          sx={{
            width: 40,
            height: 40,
            borderRadius: '50%',
            display: 'grid',
            placeItems: 'center',
            color: INK.primary,
            fontWeight: 800,
            fontSize: 16,
            bgcolor: avatarColor,
            border: '1px solid rgba(212,168,83,0.35)',
            boxShadow: '0 0 14px rgba(229,72,77,0.25)',
          }}
        >
          {seat}
        </Box>
        <Box>
          <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
            {seat}号
          </Typography>
          <Typography variant="caption" sx={{ color: 'secondary.main', letterSpacing: 3, fontWeight: 600 }}>
            {isLastWords ? '遗言' : '正在发言'}
          </Typography>
        </Box>
        <Box
          sx={{
            ml: 'auto',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 0.8,
            color: 'error.light',
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
              bgcolor: 'error.main',
              boxShadow: '0 0 10px rgba(229,72,77,0.9)',
              animation: `${livePulse} 1.4s ease-in-out infinite`,
            }}
          />
          LIVE · 第{entry.payload.round_number}轮
        </Box>
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
        {entry.payload.text}
      </Typography>
    </Box>
  );
}
