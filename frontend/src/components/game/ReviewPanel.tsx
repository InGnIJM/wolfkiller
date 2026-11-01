import { useTranslation } from 'react-i18next';
import { useCallback, useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Divider, Drawer,
  IconButton, Stack, Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import RefreshIcon from '@mui/icons-material/Refresh';
import { fetchGameReview, generateGameReview } from '../../api/client';
import type { GameReviewStatus } from '../../store/types';

interface Props {
  gameId: string;
  open: boolean;
  onClose: () => void;
}

export default function ReviewPanel({ gameId, open, onClose }: Props) {
  const { t } = useTranslation();
  const [data, setData] = useState<GameReviewStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetchGameReview(gameId);
      setData(res);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('review.loadFailed'));
    }
  }, [gameId, t]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    void (async () => {
      setLoading(true);
      await load();
      if (active) setLoading(false);
    })();
    return () => { active = false; };
  }, [load, open]);

  useEffect(() => {
    if (!open || data?.status !== 'pending') return;
    const timer = setInterval(() => {
      void load();
    }, 2500);
    return () => clearInterval(timer);
  }, [data?.status, load, open]);

  const handleGenerate = async (force: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const res = await generateGameReview(gameId, { force });
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('review.triggerFailed'));
    } finally {
      setLoading(false);
    }
  };

  const review = data?.review;

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      slotProps={{
        paper: {
          sx: {
            width: { xs: '100vw', sm: 440 },
            bgcolor: 'background.paper',
            p: 2.5,
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
          },
        },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Typography variant="h6" sx={{ fontWeight: 600, letterSpacing: 1 }}>
          {t('review.title')}
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          {data?.status === 'ready' && (
            <Button
              size="small"
              variant="outlined"
              startIcon={<RefreshIcon fontSize="small" />}
              disabled={loading}
              onClick={() => void handleGenerate(true)}
            >
              {t('review.regenerate')}
            </Button>
          )}
          <IconButton size="small" aria-label={t('review.close')} onClick={onClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>
      </Box>

      <Divider />

      {error && <Alert severity="error">{error}</Alert>}

      {loading && !data && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, py: 4 }}>
          <CircularProgress size={20} />
          <Typography variant="body2" color="text.secondary">{t('review.loading')}</Typography>
        </Box>
      )}

      {data?.status === 'none' && (
        <Stack spacing={2} sx={{ py: 3, alignItems: 'flex-start' }}>
          <Typography variant="body2" color="text.secondary">
            {t('review.notGenerated')}
          </Typography>
          <Button
            variant="contained"
            disableElevation
            disabled={loading}
            onClick={() => void handleGenerate(false)}
          >
            {t('review.generate')}
          </Button>
        </Stack>
      )}

      {data?.status === 'pending' && (
        <Stack spacing={2} sx={{ py: 4, alignItems: 'center' }}>
          <CircularProgress size={26} />
          <Typography variant="body2" color="text.secondary">
            {t('review.generating')}
          </Typography>
        </Stack>
      )}

      {data?.status === 'failed' && (
        <Stack spacing={2}>
          <Alert severity="warning">
            {t('review.failed', { failure: data.failure ? `（${data.failure}）` : '' })}
          </Alert>
          <Button
            variant="outlined"
            disabled={loading}
            onClick={() => void handleGenerate(true)}
          >
            {t('review.retry')}
          </Button>
        </Stack>
      )}

      {data?.status === 'ready' && review && (
        <Stack spacing={2.5} sx={{ overflowY: 'auto', flex: 1, pr: 0.5 }}>
          <Box>
            <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 0.8 }}>
              {t('review.overview')}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>
              {review.overview}
            </Typography>
          </Box>

          {review.mvp && (
            <Box sx={{ p: 1.5, borderRadius: 2, border: '1px solid', borderColor: 'divider' }}>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 0.5 }}>
                <Chip label={t('review.mvp')} size="small" color="secondary" />
                <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                  {t('game.seatNumber', { seat: review.mvp.seat })} · {review.mvp.role}
                </Typography>
              </Stack>
              <Typography variant="body2" color="text.secondary">
                {review.mvp.reason}
              </Typography>
            </Box>
          )}

          {review.turning_points.length > 0 && (
            <Box>
              <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
                {t('review.turningPoints')}
              </Typography>
              <Stack spacing={1.2}>
                {review.turning_points.map((tp, idx) => (
                  <Box key={idx} sx={{ pl: 1.5, borderLeft: '2px solid', borderColor: 'secondary.main' }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {t('review.roundN', { round: tp.round })} · {tp.title}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {tp.detail}
                    </Typography>
                  </Box>
                ))}
              </Stack>
            </Box>
          )}

          {review.mistakes.length > 0 && (
            <Box>
              <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
                {t('review.mistakes')}
              </Typography>
              <Stack spacing={1.2}>
                {review.mistakes.map((ms, idx) => (
                  <Box key={idx} sx={{ pl: 1.5, borderLeft: '2px solid', borderColor: 'warning.main' }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {t('game.seatNumber', { seat: ms.seat })} · {ms.title}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {ms.detail}
                    </Typography>
                  </Box>
                ))}
              </Stack>
            </Box>
          )}

          <Box>
            <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
              {t('review.camps')}
            </Typography>
            <Stack spacing={1}>
              {review.camp_assessment.good && (
                <Typography variant="body2" color="text.secondary">
                  <strong>{t('review.campGood')}</strong>{review.camp_assessment.good}
                </Typography>
              )}
              {review.camp_assessment.werewolf && (
                <Typography variant="body2" color="text.secondary">
                  <strong>{t('review.campWerewolf')}</strong>{review.camp_assessment.werewolf}
                </Typography>
              )}
              {review.camp_assessment.third_party && (
                <Typography variant="body2" color="text.secondary">
                  <strong>{t('review.campThird')}</strong>{review.camp_assessment.third_party}
                </Typography>
              )}
            </Stack>
          </Box>

          {review.closing && (
            <Typography variant="body2" sx={{ fontStyle: 'italic', color: 'text.primary' }}>
              {review.closing}
            </Typography>
          )}
        </Stack>
      )}
    </Drawer>
  );
}
