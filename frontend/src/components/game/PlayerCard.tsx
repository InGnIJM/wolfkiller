import { Box, Typography, keyframes } from '@mui/material';
import Avatar from '../shared/Avatar';
import type { PublicPlayerState } from '../../store/types';

const highlightPulse = keyframes`
  0%, 100% { boxShadow: '0 0 0 0 rgba(229,72,77,0.4)'; }
  50% { boxShadow: '0 0 0 6px rgba(229,72,77,0.15)'; }
`;

const speakerPulse = keyframes`
  0%, 100% { boxShadow: '0 0 0 2px rgba(229,72,77,0.6), 0 0 8px rgba(229,72,77,0.3)'; }
  50% { boxShadow: '0 0 0 6px rgba(229,72,77,0.3), 0 0 16px rgba(229,72,77,0.15)'; }
`;

interface Props {
  seat: number;
  player: PublicPlayerState;
  isCurrentSpeaker?: boolean;
  isHighlighted?: boolean;
  cardSize?: number;
  voteTarget?: number | null;
}

export default function PlayerCard({
  seat, player, isCurrentSpeaker, isHighlighted, cardSize = 52, voteTarget,
}: Props) {
  const status = player.is_alive ? '存活' : '出局';

  return (
    <Box
      aria-label={`${seat}号玩家，${status}${player.is_sheriff ? '，警长' : ''}`}
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 0.4,
        p: 0.8,
        borderRadius: 2,
        bgcolor: player.is_alive ? 'background.paper' : 'action.disabledBackground',
        opacity: player.is_alive ? 1 : 0.65,
        border: '1px solid',
        borderColor: isCurrentSpeaker ? 'primary.main' : 'divider',
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
            aria-hidden="true"
            sx={{
              position: 'absolute',
              inset: 0,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            <Typography sx={{ fontSize: cardSize * 0.5, color: 'error.main', fontWeight: 300 }}>
              ×
            </Typography>
          </Box>
        )}
      </Box>
      <Typography variant="caption" sx={{ fontWeight: 500, lineHeight: 1 }}>
        {seat}号
      </Typography>
      <Typography variant="caption" color={player.is_alive ? 'success.light' : 'text.disabled'}>
        {status}
      </Typography>
      {player.is_sheriff && <Typography variant="caption" color="warning.light">警长</Typography>}
      {voteTarget !== undefined && (
        <Typography
          variant="caption"
          sx={{
            mt: 0.1,
            px: 0.6,
            py: 0.1,
            borderRadius: 1,
            bgcolor: voteTarget !== null ? 'rgba(212,168,83,0.14)' : 'rgba(242,233,220,0.05)',
            color: voteTarget !== null ? 'warning.light' : 'text.disabled',
            fontSize: '0.6rem',
            fontWeight: 500,
          }}
        >
          {voteTarget !== null ? `投给 ${voteTarget}号` : '弃权'}
        </Typography>
      )}
    </Box>
  );
}
