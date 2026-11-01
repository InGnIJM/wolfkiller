import { useTranslation } from 'react-i18next';
import { useLabels } from '../../i18n/useLabels';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Box, Button, Chip, CircularProgress, Container, Dialog, DialogActions,
  DialogContent, DialogTitle, LinearProgress, Paper, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import DeleteOutlinedIcon from '@mui/icons-material/DeleteOutlined';

import { useBenchmarkStore } from '../../store/benchmarkStore';
import type { BenchmarkRun, BenchmarkStatus } from '../../store/types';
import { BACKDROP } from '../../theme/tokens';

const TERMINAL = new Set<BenchmarkStatus>(['completed', 'failed', 'cancelled']);

function progressOf(run: BenchmarkRun): number {
  if (typeof run.progress === 'number') return Math.max(0, Math.min(1, run.progress));
  const planned = run.planned_count ?? 0;
  return planned > 0 ? (run.terminal_count ?? 0) / planned : 0;
}

export default function BenchmarkListPage() {
  const { t } = useTranslation();
  const L = useLabels();
  const navigate = useNavigate();
  const { runs, loading, error, actionPending, loadRuns, deleteRun } = useBenchmarkStore();
  const [deleteTarget, setDeleteTarget] = useState<BenchmarkRun | null>(null);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  useEffect(() => {
    if (!runs.some((run) => !TERMINAL.has(run.status))) return undefined;
    const timer = window.setInterval(() => void loadRuns(), 3000);
    return () => window.clearInterval(timer);
  }, [loadRuns, runs]);

  return (
    <Container component="main" maxWidth="lg" sx={{
      position: 'relative', zIndex: 1,
      py: { xs: 2, sm: 4 }, px: { xs: 2, sm: 3 },
      bgcolor: BACKDROP.textPlate, backdropFilter: 'blur(12px)', borderRadius: 2,
    }}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
        <Box>
          <Typography component="h1" variant="h4">{t('benchmark.title')}</Typography>
          <Typography color="text.secondary">{t('benchmark.subtitle')}</Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => navigate('/benchmarks/new')}>
          {t('benchmark.create')}
        </Button>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading && runs.length === 0 && (
        <Box role="status" sx={{ display: 'grid', placeItems: 'center', py: 8, gap: 2 }}>
          <CircularProgress size={28} /><Typography color="text.secondary">{t('benchmark.loading')}</Typography>
        </Box>
      )}
      {!loading && runs.length === 0 && !error && (
        <Paper variant="outlined" sx={{ p: 4, textAlign: 'center' }}>
          <Typography variant="h6">{t('benchmark.empty')}</Typography>
          <Typography color="text.secondary" sx={{ mt: 1 }}>{t('benchmark.emptyHint')}</Typography>
        </Paper>
      )}

      {runs.length > 0 && (
        <TableContainer component={Paper} variant="outlined" sx={{ overflowX: 'auto' }}>
          <Table sx={{ minWidth: 760 }} aria-label={t('benchmark.title')}>
            <TableHead>
              <TableRow>
                <TableCell>{t('benchmark.colName')}</TableCell><TableCell>{t('benchmark.colMode')}</TableCell><TableCell>{t('benchmark.colStatus')}</TableCell>
                <TableCell>{t('benchmark.colProgress')}</TableCell><TableCell>{t('benchmark.colCreated')}</TableCell><TableCell align="right">{t('benchmark.colActions')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {runs.map((run) => {
                const progress = progressOf(run);
                return (
                  <TableRow key={run.run_id} hover>
                    <TableCell><Typography sx={{ fontWeight: 600 }}>{run.name}</Typography></TableCell>
                    <TableCell>{t(run.mode === 'mixed_arena' ? 'benchmark.modeMixed' : 'benchmark.modeRegression')}</TableCell>
                    <TableCell><Chip size="small" label={L.BENCHMARK_STATUS_LABELS[run.status] ?? run.status} /></TableCell>
                    <TableCell sx={{ minWidth: 180 }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <LinearProgress variant="determinate" value={progress * 100} sx={{ width: 96 }} />
                        <Typography variant="body2" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                          {run.terminal_count ?? 0}/{run.planned_count ?? 0}
                        </Typography>
                      </Box>
                    </TableCell>
                    <TableCell>{new Date(run.created_at).toLocaleString()}</TableCell>
                    <TableCell align="right">
                      <Button endIcon={<ChevronRightIcon />} onClick={() => navigate(`/benchmarks/${run.run_id}`)}>
                        {t('benchmark.view')}
                      </Button>
                      <Button
                        color="error"
                        startIcon={<DeleteOutlinedIcon />}
                        disabled={actionPending}
                        onClick={() => setDeleteTarget(run)}
                      >
                        {t('common.delete')}
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <Dialog open={deleteTarget !== null} onClose={() => setDeleteTarget(null)}>
        <DialogTitle>{t('benchmark.deleteRunTitle')}</DialogTitle>
        <DialogContent>
          <Typography>
            {t('benchmark.deleteRunConfirm', { name: deleteTarget?.name })}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>{t('common.cancel')}</Button>
          <Button
            color="error"
            disabled={actionPending}
            onClick={() => {
              if (!deleteTarget) return;
              void deleteRun(deleteTarget.run_id).then((ok) => { if (ok) setDeleteTarget(null); });
            }}
          >
            {t('common.delete')}
          </Button>
        </DialogActions>
      </Dialog>
    </Container>
  );
}
