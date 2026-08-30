import { Dialog, DialogTitle, DialogContent, DialogActions, Typography, Box, Button } from '@mui/material';
import type { WinResult, WinningCamp } from '../../store/types';
import { useGameStore } from '../../store/gameStore';

interface Props {
  winResult: WinResult;
  revealOnDeath: boolean;
}

const CAMP_LABELS: Record<string, string> = {
  good: '好人阵营',
  werewolf: '狼人阵营',
};

// 阵营 → 胜利标题的强调色（狼人血红 / 好人鎏金）
const CAMP_ACCENT: Record<WinningCamp, string> = {
  werewolf: 'error.light',
  good: 'secondary.light',
};

const REASON_LABELS: Record<string, string> = {
  all_wolves_dead: '所有狼人出局',
  all_gods_dead: '所有神职出局',
  all_villagers_dead: '所有平民出局',
};

export default function WinOverlay({ winResult, revealOnDeath }: Props) {
  const { dismissWinOverlay, seekTo, play } = useGameStore();

  const handleReplay = () => {
    dismissWinOverlay();
    seekTo(0);
    setTimeout(() => play(), 100);
  };

  return (
    <Dialog open={true} maxWidth="sm" fullWidth aria-labelledby="game-over-title">
      <DialogTitle sx={{ textAlign: 'center', pt: 4 }}>
        <Typography component="span" variant="h6" sx={{ mt: 1.5, fontWeight: 700, letterSpacing: 8 }}>
          游 戏 结 束
        </Typography>
      </DialogTitle>
      <DialogContent>
        <Box sx={{ textAlign: 'center', mb: 3 }}>
          <Typography
            component="span"
            variant="h5"
            sx={{
              display: 'block',
              fontWeight: 900,
              mb: 0.5,
              letterSpacing: 4,
              color: CAMP_ACCENT[winResult.winning_camp],
              textShadow: winResult.winning_camp === 'werewolf'
                ? '0 0 30px rgba(229,72,77,0.35)'
                : '0 0 30px rgba(212,168,83,0.3)',
            }}
          >
            {CAMP_LABELS[winResult.winning_camp] || winResult.winning_camp}获胜
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {REASON_LABELS[winResult.reason] || winResult.reason}
          </Typography>
        </Box>
        <Box sx={{ borderRadius: 3, px: 2, py: 1.5, bgcolor: 'action.hover' }}>
          <Typography variant="body2" color="text.secondary" align="center">
            {revealOnDeath
              ? '身份公开：玩家出局时会向场上公开身份。'
              : '身份不公开：玩家出局时不会向场上公开身份。'}
          </Typography>
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
