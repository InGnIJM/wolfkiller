import { Dialog, DialogTitle, DialogContent, DialogActions, Typography, Box, Button } from '@mui/material';
import { PlayerFullState } from '../../store/types';
import RoleIcon from '../shared/RoleIcon';
import { useGameStore } from '../../store/gameStore';

interface Props {
  winResult: { winning_camp: string; reason: string };
  players: Record<number, PlayerFullState>;
}

const CAMP_LABELS: Record<string, string> = {
  good: '好人阵营',
  werewolf: '狼人阵营',
};

const REASON_LABELS: Record<string, string> = {
  all_wolves_dead: '所有狼人被消灭',
  all_gods_dead: '所有神职被消灭',
  all_villagers_dead: '所有平民被消灭',
};

const WIN_ICON: Record<string, string> = {
  good: '🛡',
  werewolf: '🐺',
};

export default function WinOverlay({ winResult, players }: Props) {
  const { dismissWinOverlay, seekTo, play } = useGameStore();

  const handleReplay = () => {
    dismissWinOverlay();
    seekTo(0);
    setTimeout(() => play(), 100);
  };

  const icon = WIN_ICON[winResult.winning_camp] || '🏆';

  return (
    <Dialog open={true} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ textAlign: 'center', pt: 4 }}>
        <Typography sx={{ fontSize: '3rem', lineHeight: 1 }}>{icon}</Typography>
        <Typography variant="h5" sx={{ mt: 1.5, fontWeight: 500 }}>
          游戏结束
        </Typography>
      </DialogTitle>
      <DialogContent>
        <Box sx={{ textAlign: 'center', mb: 3 }}>
          <Typography variant="h5" sx={{ fontWeight: 400, mb: 0.5 }}>
            {CAMP_LABELS[winResult.winning_camp] || winResult.winning_camp} 获胜
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {REASON_LABELS[winResult.reason] || winResult.reason}
          </Typography>
        </Box>

        <Typography variant="subtitle2" sx={{ mb: 1.5, fontWeight: 500, color: 'text.secondary' }}>
          身份揭晓
        </Typography>
        <Box sx={{ borderRadius: 3, overflow: 'hidden', border: '1px solid', borderColor: 'divider' }}>
          {Object.entries(players).map(([seatStr, p], i) => {
            const seat = parseInt(seatStr);
            return (
              <Box
                key={seat}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1.5,
                  px: 2, py: 1.2,
                  bgcolor: i % 2 === 0 ? 'rgba(255,255,255,0.02)' : 'transparent',
                  borderBottom: '1px solid',
                  borderColor: 'divider',
                  '&:last-child': { borderBottom: 'none' },
                }}
              >
                <Typography variant="body2" fontWeight={500} sx={{ minWidth: 40 }}>
                  {seat}号
                </Typography>
                <RoleIcon role={p.role || p.revealed_role || ''} size={22} />
                <Typography variant="body2" color="text.primary" sx={{ flex: 1 }}>
                  {p.role || p.revealed_role || '?'}
                </Typography>
                <Typography
                  variant="caption"
                  sx={{
                    fontWeight: 500,
                    color: p.is_alive ? 'success.light' : 'error.light',
                  }}
                >
                  {p.is_alive ? '存活' : '出局'}
                </Typography>
              </Box>
            );
          })}
        </Box>
      </DialogContent>
      <DialogActions sx={{ justifyContent: 'center', pb: 3, gap: 1.5 }}>
        <Button variant="outlined" onClick={handleReplay}>
          从头播放
        </Button>
        <Button variant="contained" onClick={dismissWinOverlay} disableElevation>
          查看对局
        </Button>
      </DialogActions>
    </Dialog>
  );
}
