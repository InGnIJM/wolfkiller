import { Box, Typography, keyframes } from '@mui/material';
import Avatar from '../shared/Avatar';
import RoleIcon from '../shared/RoleIcon';
import { PlayerFullState } from '../../store/types';

const highlightPulse = keyframes`
  0%, 100% { boxShadow: '0 0 0 0 rgba(168,199,250,0.4)'; }
  50% { boxShadow: '0 0 0 6px rgba(168,199,250,0.15)'; }
`;

const speakerPulse = keyframes`
  0%, 100% { boxShadow: '0 0 0 2px rgba(168,199,250,0.6), 0 0 8px rgba(168,199,250,0.3)'; }
  50% { boxShadow: '0 0 0 6px rgba(168,199,250,0.3), 0 0 16px rgba(168,199,250,0.15)'; }
`;

const clawPulse = keyframes`
  0%, 100% { opacity: 0.6; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.15); }
`;

interface Props {
  seat: number;
  player: PlayerFullState;
  isCurrentSpeaker?: boolean;
  isHighlighted?: boolean;
  hasWolfClaw?: boolean;
  cardSize?: number;
  voteTarget?: number | null;
}

export default function PlayerCard({
  seat, player, isCurrentSpeaker, isHighlighted, hasWolfClaw,
  cardSize = 52, voteTarget,
}: Props) {
  const campBg = player.camp === 'werewolf'
    ? 'rgba(242, 184, 181, 0.08)'
    : player.camp === 'good'
      ? 'rgba(168, 199, 250, 0.08)'
      : 'transparent';

  const campBorder = player.camp === 'werewolf'
    ? 'rgba(242, 184, 181, 0.3)'
    : player.camp === 'good'
      ? 'rgba(168, 199, 250, 0.3)'
      : 'transparent';

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 0.4,
        p: 0.8,
        borderRadius: 2,
        bgcolor: campBg,
        border: '1px solid',
        borderColor: isCurrentSpeaker ? 'primary.main' : campBorder,
        transition: 'all 0.25s ease',
        ...(isCurrentSpeaker
          ? { animation: `${speakerPulse} 1s ease-in-out infinite` }
          : isHighlighted
            ? { animation: `${highlightPulse} 2s ease-in-out infinite` }
            : {}),
      }}
    >
      <Box sx={{ position: 'relative' }}>
        <Avatar seat={seat} size={cardSize} isAlive={player.is_alive} />
        {!player.is_alive && (
          <Box
            sx={{
              position: 'absolute',
              inset: 0,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            <Typography sx={{ fontSize: cardSize * 0.5, color: 'error.main', fontWeight: 300 }}>
              ✕
            </Typography>
          </Box>
        )}
        {hasWolfClaw && player.is_alive && (
          <Box
            sx={{
              position: 'absolute',
              top: -4,
              right: -4,
              fontSize: '1.2rem',
              animation: `${clawPulse} 1s ease-in-out infinite`,
              pointerEvents: 'none',
            }}
          >
            🐺
          </Box>
        )}
      </Box>
      <Typography variant="caption" sx={{ fontWeight: 500, lineHeight: 1 }}>
        {seat}号
      </Typography>
      <RoleIcon role={player.role || player.revealed_role || ''} size={20} />
      {voteTarget !== undefined && (
        <Typography
          variant="caption"
          sx={{
            mt: 0.1,
            px: 0.6,
            py: 0.1,
            borderRadius: 1,
            bgcolor: voteTarget ? 'rgba(255,217,104,0.12)' : 'rgba(255,255,255,0.04)',
            color: voteTarget ? 'warning.light' : 'text.disabled',
            fontSize: '0.6rem',
            fontWeight: 500,
          }}
        >
          {voteTarget ? `→ ${voteTarget}号` : '弃权'}
        </Typography>
      )}
    </Box>
  );
}
