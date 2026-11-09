import { Dialog, DialogTitle, DialogContent, DialogActions, Typography, Box, Button } from '@mui/material';
import type { WinResult, WinningCamp } from '../../store/types';
import { useGameStore } from '../../store/gameStore';
import { useTranslation } from 'react-i18next';
import { useLabels } from '../../i18n/useLabels';
import { CARD_BACK, HAIRLINE } from '../../theme/tokens';

interface Props {
  winResult: WinResult;
  revealOnDeath: boolean;
}

// 阵营 → 胜利标题的强调色（狼人血红 / 好人鎏金）
const CAMP_ACCENT: Record<WinningCamp, string> = {
  werewolf: 'error.light',
  good: 'secondary.light',
  third_party: 'info.light',
};

export default function WinOverlay({ winResult, revealOnDeath }: Props) {
  const { t } = useTranslation();
  const L = useLabels();
  const dismissWinOverlay = useGameStore((s) => s.dismissWinOverlay);
  const seekTo = useGameStore((s) => s.seekTo);
  const play = useGameStore((s) => s.play);

  const handleReplay = () => {
    dismissWinOverlay();
    seekTo(0);
    setTimeout(() => play(), 100);
  };

  return (
    <Dialog open={true} maxWidth="sm" fullWidth aria-labelledby="game-over-title">
      <DialogTitle sx={{ textAlign: 'center', pt: 4 }}>
        <Box
          aria-hidden="true"
          sx={{
            width: 72, mx: 'auto', mb: 1.5, borderRadius: 1, overflow: 'hidden',
            border: `1px solid ${HAIRLINE.strong}`,
          }}
        >
          <Box
            component="img"
            alt=""
            src={CARD_BACK}
            sx={{ display: 'block', width: '100%', height: 96, objectFit: 'cover' }}
          />
        </Box>
        <Typography component="span" variant="h6" sx={{ mt: 1.5, fontWeight: 600, letterSpacing: 8 }}>
          {t('game.gameOver')}
        </Typography>
      </DialogTitle>
      <DialogContent>
        <Box sx={{ textAlign: 'center', mb: 3 }}>
          <Typography
            component="span"
            variant="h5"
            sx={{
              display: 'block',
              fontWeight: 700,
              mb: 0.5,
              letterSpacing: 4,
              color: CAMP_ACCENT[winResult.winning_camp],
            }}
          >
            {t('game.campWins', { camp: L.WINNING_CAMP_LABELS[winResult.winning_camp] || winResult.winning_camp })}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {L.WIN_REASON_LABELS[winResult.reason] || winResult.reason}
          </Typography>
          {winResult.winners != null && winResult.winners.length > 0 && (
            // 孤独少女这类「胜负跟随别人」的牌会让胜者不等于整个阵营，所以按座位
            // 点名（阵营标题仍是结算阵营，两者可以不一致）。
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
              {t('game.winnerSeats', { seats: winResult.winners.join('、') })}
            </Typography>
          )}
        </Box>
        <Box sx={{ borderRadius: 3, px: 2, py: 1.5, bgcolor: 'action.hover' }}>
          <Typography variant="body2" color="text.secondary" align="center">
            {revealOnDeath
              ? t('game.revealOn')
              : t('game.revealOff')}
          </Typography>
        </Box>
      </DialogContent>
      <DialogActions sx={{ justifyContent: 'center', pb: 3, gap: 1.5 }}>
        <Button variant="outlined" onClick={handleReplay}>
          {t('game.replayFromStart')}
        </Button>
        <Button variant="contained" onClick={dismissWinOverlay} disableElevation>
          {t('game.viewGame')}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
