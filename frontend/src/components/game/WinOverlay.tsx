import { Dialog, DialogTitle, DialogContent, DialogActions, Typography, Box, Button } from '@mui/material';
import type { WinResult } from '../../store/types';
import { useGameStore } from '../../store/gameStore';

interface Props {
  winResult: WinResult;
}

const CAMP_LABELS: Record<string, string> = {
  good: '好人阵营',
  werewolf: '狼人阵营',
};

const REASON_LABELS: Record<string, string> = {
  all_wolves_dead: '所有狼人出局',
  all_gods_dead: '所有神职出局',
  all_villagers_dead: '所有平民出局',
};

export default function WinOverlay({ winResult }: Props) {
  const { dismissWinOverlay, seekTo, play } = useGameStore();

  const handleReplay = () => {
    dismissWinOverlay();
    seekTo(0);
    setTimeout(() => play(), 100);
  };

  return (
    <Dialog open={true} maxWidth="sm" fullWidth aria-labelledby="game-over-title">
      <DialogTitle sx={{ textAlign: 'center', pt: 4 }}>
        <Typography id="game-over-title" variant="h5" sx={{ mt: 1.5, fontWeight: 500 }}>
          游戏结束
        </Typography>
      </DialogTitle>
      <DialogContent>
        <Box sx={{ textAlign: 'center', mb: 3 }}>
          <Typography variant="h5" sx={{ fontWeight: 400, mb: 0.5 }}>
            {CAMP_LABELS[winResult.winning_camp] || winResult.winning_camp}获胜
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {REASON_LABELS[winResult.reason] || winResult.reason}
          </Typography>
        </Box>
        <Box sx={{ borderRadius: 3, px: 2, py: 1.5, bgcolor: 'action.hover' }}>
          <Typography variant="body2" color="text.secondary" align="center">
            身份不公开：旁观回放仅展示公开事件与胜负结果。
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
