import { Card, CardActionArea, Typography, Chip, Box } from '@mui/material';
import GroupsIcon from '@mui/icons-material/Groups';
import HourglassEmptyIcon from '@mui/icons-material/HourglassEmpty';

interface Props {
  gameId: string;
  phase: string;
  roundNumber: number;
  playerCount: number;
  aliveCount: number;
  winner: string | null;
  onClick: () => void;
}

const WINNER_META: Record<string, { label: string; color: 'success' | 'error' }> = {
  good: { label: '好人胜', color: 'success' },
  werewolf: { label: '狼人胜', color: 'error' },
};

export default function GameCard({ gameId, phase, roundNumber, playerCount, aliveCount, winner, onClick }: Props) {
  const win = winner ? WINNER_META[winner] : null;

  return (
    <Card
      variant="outlined"
      sx={{
        transition: 'background-color 0.2s, border-color 0.2s',
        '&:hover': {
          bgcolor: 'action.hover',
          borderColor: 'primary.main',
        },
      }}
    >
      <CardActionArea onClick={onClick} sx={{ p: 0 }}>
        <Box sx={{ px: 2.5, py: 2 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <Box>
              <Typography
                variant="subtitle1"
                sx={{ fontWeight: 500, fontFamily: '"Google Sans Mono", monospace', fontSize: '0.9rem' }}
              >
                {gameId.slice(0, 8)}
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3 }}>
                {phase} · 第 {roundNumber} 轮
              </Typography>
            </Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                <GroupsIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
                <Typography variant="body2" color="text.secondary">
                  {aliveCount}/{playerCount}
                </Typography>
              </Box>
              {win && (
                <Chip
                  label={win.label}
                  color={win.color}
                  size="small"
                  variant="outlined"
                  sx={{ fontWeight: 500, height: 24, fontSize: '0.7rem' }}
                />
              )}
            </Box>
          </Box>
        </Box>
      </CardActionArea>
    </Card>
  );
}
