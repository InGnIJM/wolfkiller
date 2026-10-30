import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  Alert, Box, Button, Card, CardContent, Checkbox, Chip, CircularProgress, Container,
  Dialog, DialogActions, DialogContent, DialogTitle, FormControl, InputLabel,
  LinearProgress, MenuItem, Paper, Select, Stack,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import PauseIcon from '@mui/icons-material/Pause';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import CancelOutlinedIcon from '@mui/icons-material/CancelOutlined';
import DeleteOutlinedIcon from '@mui/icons-material/DeleteOutlined';
import RefreshIcon from '@mui/icons-material/Refresh';
import DownloadIcon from '@mui/icons-material/Download';

import { benchmarkExportUrl } from '../../api/client';
import { useBenchmarkStore } from '../../store/benchmarkStore';
import type { BenchmarkReport, BenchmarkStatus, GamePhase, WinningCamp } from '../../store/types';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useLabels } from '../../i18n/useLabels';
import { BACKDROP } from '../../theme/tokens';

const TERMINAL = new Set<BenchmarkStatus>(['completed', 'failed', 'cancelled']);

function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function numberAt(source: unknown, ...path: string[]): number | null {
  let value = source;
  for (const key of path) value = record(value)[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function rate(value: number | null): string {
  return value === null ? '—' : `${(value * 100).toFixed(1)}%`;
}

// 决策质量由后端本轮新增，旧报告没有该键：取不到就返回 null，由页面降级为提示。
function decisionSummary(report: BenchmarkReport | null): Record<string, unknown> | null {
  const value = record(report?.summary).decision;
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

interface DecisionRow {
  id: string;
  label: string;
  value: number | null;
  sample: number | null;
  count: boolean;
}

interface PerformanceRow {
  id: string;
  model: string;
  dimension: 'role' | 'camp';
  group: string;
  samples: number;
  value: number | null;
}

function boardCoverageText(coverage: Record<string, unknown>, t: TFunction): string | null {
  const boards = Array.isArray(coverage.boards) ? coverage.boards : [];
  if (boards.length === 0) return null;
  const parts = boards.map((raw) => {
    const item = record(raw);
    const counts = record(item.role_counts);
    const seats = Object.values(counts).reduce<number>(
      (total, value) => total + (typeof value === 'number' ? value : 0), 0,
    );
    const games = typeof item.games === 'number' ? item.games : 0;
    return t('benchmark.seatsTimesGames', { seats, games });
  });
  const seer = coverage.seer_present === false ? t('benchmark.noSeerNote') : '';
  return t('benchmark.coverage', { parts: parts.join('；'), note: seer });
}

function performanceRows(report: BenchmarkReport | null, t: TFunction): PerformanceRow[] {
  if (!report) return [];
  const modelRows = record(report.metrics).model_performance;
  if (Array.isArray(modelRows)) {
    return modelRows.map((raw, index) => {
      const item = record(raw);
      return {
        id: String(item.id ?? index),
        model: String(item.model ?? item.model_name ?? t('benchmark.unknownModel')),
        dimension: item.camp ? 'camp' : 'role',
        group: String(item.role ?? item.camp ?? t('benchmark.all')),
        samples: typeof item.samples === 'number' ? item.samples : 0,
        value: typeof item.win_rate === 'number' ? item.win_rate : null,
      };
    });
  }
  const games = record(record(report.summary).games);
  const rows: PerformanceRow[] = [];
  for (const [dimension, key] of [['camp', 'win_rate_by_camp'], ['role', 'win_rate_by_role']] as const) {
    for (const [group, raw] of Object.entries(record(games[key]))) {
      const item = record(raw);
      rows.push({
        id: `${dimension}-${group}`,
        model: t('benchmark.allModels'),
        dimension,
        group,
        samples: typeof item.games === 'number' ? item.games : 0,
        value: typeof raw === 'number' ? raw : typeof item.win_rate === 'number' ? item.win_rate : null,
      });
    }
  }
  return rows;
}

export default function BenchmarkDetailPage() {
  const { t } = useTranslation();
  const L = useLabels();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const {
    current, items, report, loading, actionPending, error,
    loadDetail, control, rebuildReport, deleteRun, deleteGame, batchDeleteGames, clear,
  } = useBenchmarkStore();
  const [dimension, setDimension] = useState<'all' | 'role' | 'camp'>('all');
  const [sort, setSort] = useState<'samples' | 'value'>('value');
  const [descending, setDescending] = useState(true);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [deleteGameId, setDeleteGameId] = useState<string | null>(null);
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false);
  const [deleteRunOpen, setDeleteRunOpen] = useState(false);

  useEffect(() => {
    if (!id) return undefined;
    void loadDetail(id);
    return clear;
  }, [clear, id, loadDetail]);

  useEffect(() => {
    if (!id || !current || current.run_id !== id) return undefined;
    if (TERMINAL.has(current.status) && report && report.status !== 'pending' && !report.provisional) return undefined;
    const timer = window.setInterval(() => {
      if (!useBenchmarkStore.getState().loading) void loadDetail(id);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [current, id, loadDetail, report]);

  const rows = useMemo(() => performanceRows(report, t)
    .filter((row) => dimension === 'all' || row.dimension === dimension)
    .sort((left, right) => {
      const leftValue = sort === 'samples' ? left.samples : left.value ?? -1;
      const rightValue = sort === 'samples' ? right.samples : right.value ?? -1;
      return (leftValue - rightValue) * (descending ? -1 : 1);
    }), [descending, dimension, report, sort, t]);
  const deletableIds = items
    .map((item) => item.game_id)
    .filter((gameId): gameId is string => Boolean(gameId));

  const changeSort = (next: 'samples' | 'value') => {
    if (sort === next) setDescending((value) => !value);
    else { setSort(next); setDescending(true); }
  };

  if (!id) return <Alert severity="error">{t('benchmark.missingId')}</Alert>;
  if (loading && !current) {
    return <Box role="status" sx={{ flex: 1, display: 'grid', placeItems: 'center' }}><CircularProgress /></Box>;
  }
  if (!current) {
    return <Container sx={{ py: 4 }}><Alert severity="error">{error ?? t('benchmark.runNotFound')}</Alert></Container>;
  }

  const progress = typeof current.progress === 'number'
    ? current.progress
    : (current.planned_count ?? 0) > 0 ? (current.terminal_count ?? 0) / (current.planned_count ?? 1) : 0;
  const reportSummary = record(report?.summary);
  const games = record(reportSummary.games);
  const requests = record(reportSummary.requests);
  const attempts = record(reportSummary.attempts);
  const tokenUsage = record(attempts.token_usage);
  const latency = record(attempts.latency_ms);
  const averageRounds = numberAt(games, 'average_rounds');
  const interruptionRate = numberAt(games, 'interruption_rate');
  const decision = decisionSummary(report);
  const decisionSamples = record(decision?.samples);
  const decisionGames = numberAt(decision, 'samples', 'games');
  const coverage = record(reportSummary.coverage);
  const boardSummary = boardCoverageText(coverage, t);
  const status = current.status;

  return (
    <Container component="main" maxWidth="xl" sx={{
      position: 'relative', zIndex: 1,
      py: { xs: 2, sm: 4 }, px: { xs: 2, sm: 3 },
      bgcolor: BACKDROP.textPlate, backdropFilter: 'blur(12px)', borderRadius: 2,
    }}>
      <Button startIcon={<ArrowBackIcon />} onClick={() => navigate('/benchmarks')} sx={{ mb: 2 }}>{t('benchmark.backToList')}</Button>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <Box>
          <Typography component="h1" variant="h4">{current.name}</Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 1 }}><Chip label={status} /><Chip variant="outlined" label={t(current.mode === 'mixed_arena' ? 'benchmark.modeMixed' : 'benchmark.modeRegression')} /></Stack>
        </Box>
        <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap' }}>
          {(status === 'draft' || status === 'pending') && <Button variant="contained" startIcon={<PlayArrowIcon />} disabled={actionPending} onClick={() => void control(id, 'start')}>{t('benchmark.start')}</Button>}
          {status === 'running' && <Button variant="outlined" startIcon={<PauseIcon />} disabled={actionPending} onClick={() => void control(id, 'pause')}>{t('benchmark.pause')}</Button>}
          {status === 'paused' && <Button variant="contained" startIcon={<PlayArrowIcon />} disabled={actionPending} onClick={() => void control(id, 'resume')}>{t('benchmark.resume')}</Button>}
          {status === 'interrupted' && <Button variant="contained" startIcon={<PlayArrowIcon />} disabled={actionPending} onClick={() => void control(id, 'start')}>{t('benchmark.resumeRun')}</Button>}
          {!TERMINAL.has(status) && <Button color="error" startIcon={<CancelOutlinedIcon />} disabled={actionPending} onClick={() => void control(id, 'cancel')}>{t('benchmark.cancel')}</Button>}
          <Button color="error" variant="outlined" startIcon={<DeleteOutlinedIcon />} disabled={actionPending} onClick={() => setDeleteRunOpen(true)}>{t('benchmark.deleteRun')}</Button>
        </Stack>
      </Box>
      {error && <Alert severity="warning" sx={{ mt: 2 }}>{error}</Alert>}

      <Paper variant="outlined" sx={{ p: 2, mt: 3 }} aria-label={t('benchmark.progressAria')}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', gap: 2, mb: 1 }}>
          <Typography sx={{ fontWeight: 700 }}>{t('benchmark.progressAria')}</Typography>
          <Typography sx={{ fontVariantNumeric: 'tabular-nums' }}>{current.terminal_count ?? 0}/{current.planned_count ?? 0}</Typography>
        </Box>
        <LinearProgress variant="determinate" value={Math.max(0, Math.min(100, progress * 100))} aria-label={t('benchmark.progressBarAria')} />
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          {t('benchmark.progressLine', { done: current.completed_count ?? 0, failed: current.failed_count ?? 0 })}
        </Typography>
      </Paper>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' }, gap: 2, my: 3 }}>
        {[
          [t('benchmark.metricCompletion'), rate(numberAt(games, 'completed') !== null && numberAt(games, 'total') ? (numberAt(games, 'completed') ?? 0) / (numberAt(games, 'total') ?? 1) : null)],
          [t('benchmark.metricReqSuccess'), rate(numberAt(requests, 'success_rate'))],
          [t('benchmark.metricTokens'), numberAt(tokenUsage, 'known_total_tokens')?.toLocaleString() ?? '—'],
          [t('benchmark.metricLatency'), numberAt(latency, 'p95') === null ? '—' : `${numberAt(latency, 'p95')} ms`],
          [t('benchmark.metricRounds'), averageRounds === null ? '—' : averageRounds.toFixed(1)],
          [t('benchmark.metricInterruption'), rate(interruptionRate)],
        ].map(([label, value]) => (
          <Card key={label} variant="outlined"><CardContent><Typography color="text.secondary">{label}</Typography><Typography variant="h5" sx={{ mt: 1, fontVariantNumeric: 'tabular-nums' }}>{value}</Typography></CardContent></Card>
        ))}
      </Box>

      <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, mb: 3 }}>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
          <Box><Typography component="h2" variant="h6">{t('benchmark.perfTitle')}</Typography><Typography variant="body2" color="text.secondary">{t('benchmark.perfDesc1')}</Typography><Typography variant="body2" color="text.secondary">{t('benchmark.perfDesc2')}</Typography></Box>
          <FormControl size="small" sx={{ minWidth: 160 }}><InputLabel id="performance-filter-label">{t('benchmark.groupBy')}</InputLabel><Select labelId="performance-filter-label" label={t('benchmark.groupBy')} value={dimension} onChange={(event) => setDimension(event.target.value as typeof dimension)}><MenuItem value="all">{t('benchmark.all')}</MenuItem><MenuItem value="role">{t('benchmark.byRole')}</MenuItem><MenuItem value="camp">{t('benchmark.byCamp')}</MenuItem></Select></FormControl>
        </Box>
        {rows.length === 0 ? <Alert severity="info">{t('benchmark.perfEmpty')}</Alert> : (
          <TableContainer sx={{ overflowX: 'auto' }}>
            <Table sx={{ minWidth: 640 }} aria-label={t('benchmark.perfTitle')}>
              <TableHead><TableRow><TableCell>{t('benchmark.colModel')}</TableCell><TableCell>{t('benchmark.colDimension')}</TableCell><TableCell>{t('benchmark.colGroup')}</TableCell><TableCell sortDirection={sort === 'samples' ? (descending ? 'desc' : 'asc') : false}><Button color="inherit" aria-sort={sort === 'samples' ? (descending ? 'descending' : 'ascending') : 'none'} onClick={() => changeSort('samples')}>{t('benchmark.colSamples')}</Button></TableCell><TableCell sortDirection={sort === 'value' ? (descending ? 'desc' : 'asc') : false}><Button color="inherit" aria-sort={sort === 'value' ? (descending ? 'descending' : 'ascending') : 'none'} onClick={() => changeSort('value')}>{t('benchmark.colWinRate')}</Button></TableCell></TableRow></TableHead>
              <TableBody>{rows.map((row) => <TableRow key={row.id}><TableCell>{row.model}</TableCell><TableCell>{t(row.dimension === 'role' ? 'benchmark.dimRole' : 'benchmark.dimCamp')}</TableCell><TableCell>{row.group}</TableCell><TableCell>{row.samples}</TableCell><TableCell>{rate(row.value)}</TableCell></TableRow>)}</TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, mb: 3 }}>
        <Box sx={{ mb: 2 }}>
          <Typography component="h2" variant="h6">{t('benchmark.decisionTitle')}</Typography>
          <Typography variant="body2" color="text.secondary">
            {t('benchmark.decisionDesc', { games: decisionGames === null ? '' : t('benchmark.decisionGames', { count: decisionGames }) })}
          </Typography>
          {boardSummary === null ? null : (
            <Typography variant="body2" color="text.secondary">{boardSummary}</Typography>
          )}
        </Box>
        {decision === null ? (
          <Alert severity="info">{t('benchmark.decisionEmpty')}</Alert>
        ) : (
          <TableContainer sx={{ overflowX: 'auto' }}>
            <Table sx={{ minWidth: 520 }} aria-label={t('benchmark.decisionTitle')}>
              <TableHead>
                <TableRow><TableCell>{t('benchmark.colMetric')}</TableCell><TableCell>{t('benchmark.colValue')}</TableCell><TableCell>{t('benchmark.colSample')}</TableCell></TableRow>
              </TableHead>
              <TableBody>
                {([
                  { id: 'seer-check', label: t('benchmark.metric.seerCheck'), value: numberAt(decision, 'seer_check_hit_rate'), sample: numberAt(decisionSamples, 'seer_checks'), count: false },
                  { id: 'witch-poison', label: t('benchmark.metric.witchPoison'), value: numberAt(decision, 'witch_poison_hit_rate'), sample: numberAt(decisionSamples, 'witch_poisons'), count: false },
                  { id: 'hunter-shot', label: t('benchmark.metric.hunterShot'), value: numberAt(decision, 'hunter_shot_hit_rate'), sample: numberAt(decisionSamples, 'hunter_shots'), count: false },
                  { id: 'wolf-kill', label: t('benchmark.metric.wolfKill'), value: numberAt(decision, 'wolf_kill_god_rate'), sample: numberAt(decisionSamples, 'wolf_kills'), count: false },
                  { id: 'pure-white', label: t('benchmark.metric.pureWhite'), value: numberAt(decision, 'pure_white_check_hit_rate'), sample: numberAt(decisionSamples, 'pure_white_checks'), count: false },
                  { id: 'wolf-witch', label: t('benchmark.metric.wolfWitch'), value: numberAt(decision, 'wolf_witch_check_hit_rate'), sample: numberAt(decisionSamples, 'wolf_witch_checks'), count: false },
                  { id: 'gargoyle', label: t('benchmark.metric.gargoyle'), value: numberAt(decision, 'gargoyle_check_hit_rate'), sample: numberAt(decisionSamples, 'gargoyle_checks'), count: false },
                  { id: 'demon-hunter', label: t('benchmark.metric.demonHunter'), value: numberAt(decision, 'demon_hunter_hunt_hit_rate'), sample: numberAt(decisionSamples, 'demon_hunter_hunts'), count: false },
                  { id: 'dance-pool', label: t('benchmark.metric.dancePool'), value: numberAt(decision, 'dance_pool_wolf_rate'), sample: numberAt(decisionSamples, 'dance_pools'), count: false },
                  { id: 'mask-in-pool', label: t('benchmark.metric.maskInPool'), value: numberAt(decision, 'mask_in_pool_rate'), sample: numberAt(decisionSamples, 'mask_grants'), count: false },
                  { id: 'good-vote', label: t('benchmark.metric.goodVote'), value: numberAt(decision, 'good_vote_hit_rate'), sample: numberAt(decisionSamples, 'good_votes'), count: false },
                  { id: 'good-abstain', label: t('benchmark.metric.goodAbstain'), value: numberAt(decision, 'good_vote_abstain_rate'), sample: numberAt(decisionSamples, 'good_votes'), count: false },
                  { id: 'exile-wolf', label: t('benchmark.metric.exileWolf'), value: numberAt(decision, 'exile_wolf_rate'), sample: numberAt(decisionSamples, 'exiles'), count: false },
                  { id: 'exile-misfire', label: t('benchmark.metric.exileMisfire'), value: numberAt(decision, 'exile_god_misfire_rate'), sample: numberAt(decisionSamples, 'exiles'), count: false },
                  { id: 'witch-save-uses', label: t('benchmark.metric.witchSaveUses'), value: numberAt(decision, 'witch_save_uses'), sample: null, count: true },
                  { id: 'witch-poison-uses', label: t('benchmark.metric.witchPoisonUses'), value: numberAt(decision, 'witch_poison_uses'), sample: null, count: true },
                ] as DecisionRow[]).map((row) => (
                  <TableRow key={row.id}>
                    <TableCell>{row.label}</TableCell>
                    <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>
                      {row.count ? (row.value === null ? '—' : row.value.toLocaleString()) : rate(row.value)}
                    </TableCell>
                    <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{row.sample === null ? '—' : row.sample.toLocaleString()}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, mb: 3 }}>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
          <Box>
            <Typography component="h2" variant="h6">{t('benchmark.gamesTitle')}</Typography>
            <Typography variant="body2" color="text.secondary">{t('benchmark.gamesHint')}</Typography>
          </Box>
          {selectedIds.length > 0 && (
            <Stack direction="row" spacing={1}>
              <Typography variant="body2" sx={{ alignSelf: 'center' }}>{t('benchmark.selectedGames', { count: selectedIds.length })}</Typography>
              <Button color="error" disabled={actionPending} onClick={() => setBatchDeleteOpen(true)}>{t('lobby.batchDelete')}</Button>
              <Button onClick={() => setSelectedIds([])}>{t('lobby.clearSelection')}</Button>
            </Stack>
          )}
        </Box>
        {items.length === 0 ? <Typography color="text.secondary">{t('benchmark.noGames')}</Typography> : (
          <TableContainer sx={{ overflowX: 'auto' }}>
            <Table sx={{ minWidth: 760 }} aria-label={t('benchmark.gamesTableAria')}>
              <TableHead>
                <TableRow>
                  <TableCell padding="checkbox">
                    <Checkbox
                      slotProps={{ input: { 'aria-label': t('benchmark.selectAllAria') } }}
                      checked={deletableIds.length > 0 && deletableIds.every((gameId) => selectedIds.includes(gameId))}
                      indeterminate={selectedIds.length > 0 && selectedIds.length < deletableIds.length}
                      onChange={() => setSelectedIds((current) => (
                        current.length === deletableIds.length ? [] : [...deletableIds]
                      ))}
                    />
                  </TableCell>
                  <TableCell>{t('benchmark.gameCol')}</TableCell>
                  <TableCell>{t('benchmark.phaseCol')}</TableCell>
                  <TableCell>{t('benchmark.statusCol')}</TableCell>
                  <TableCell>{t('benchmark.winnerCol')}</TableCell>
                  <TableCell align="right">{t('benchmark.colActions')}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {items.map((item) => {
                  const gameId = item.game_id;
                  const selected = Boolean(gameId && selectedIds.includes(gameId));
                  return (
                    <TableRow key={item.item_index} selected={selected}>
                      <TableCell padding="checkbox">
                        {gameId && (
                          <Checkbox
                            slotProps={{ input: { 'aria-label': t('benchmark.selectGameAria', { name: item.name ?? gameId }) } }}
                            checked={selected}
                            onChange={() => setSelectedIds((current) => (
                              current.includes(gameId)
                                ? current.filter((id) => id !== gameId)
                                : [...current, gameId]
                            ))}
                          />
                        )}
                      </TableCell>
                      <TableCell>
                        <Typography sx={{ fontWeight: 700 }}>{item.name ?? t('benchmark.scheduledItem', { index: item.item_index + 1 })}</Typography>
                        <Typography variant="body2" color="text.secondary">
                          {gameId ?? t('benchmark.notStarted')}
                          {item.round_number != null ? ` · ${t('benchmark.roundN', { round: item.round_number })}` : ''}
                          {item.alive_count != null && item.player_count != null ? ` · ${item.alive_count}/${item.player_count}` : ''}
                        </Typography>
                      </TableCell>
                      <TableCell>{item.phase ? (L.PHASE_LABELS[item.phase as GamePhase] ?? item.phase) : '—'}</TableCell>
                      <TableCell>{item.terminal_reason ?? item.status}{item.execution_status ? ` · ${item.execution_status}` : ''}</TableCell>
                      <TableCell>{item.winner ? (L.WINNER_LABELS[item.winner as WinningCamp] ?? item.winner) : '—'}</TableCell>
                      <TableCell align="right">
                        {gameId && (
                          <Stack direction="row" spacing={1} sx={{ justifyContent: 'flex-end' }}>
                            <Button
                              component={Link}
                              to={`/game/${gameId}`}
                              size="small"
                            >
                              {t('benchmark.viewReplay')}
                            </Button>
                            <Button
                              color="error"
                              size="small"
                              disabled={actionPending}
                              onClick={() => setDeleteGameId(gameId)}
                            >
                              {t('common.delete')}
                            </Button>
                          </Stack>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
          <Typography component="h2" variant="h6">{t('benchmark.diagTitle')}</Typography>
          <Stack spacing={1} sx={{ mt: 2 }}>
            <Typography>{t('benchmark.metricVersion', { version: report?.metric_version ?? t('benchmark.pendingGeneration') })}</Typography>
            <Typography>{t('benchmark.reportStatus', { status: report?.status === 'pending' ? t('benchmark.reportPending') : report?.status === 'failed' ? t('benchmark.reportFailed') : report?.provisional ? t('benchmark.reportProvisional') : report ? t('benchmark.reportFrozen') : t('benchmark.reportToGenerate') })}</Typography>
            <Typography>{t('benchmark.completeness', { rate: rate(numberAt(tokenUsage, 'completeness_rate')) })}</Typography>
            <Typography>{t('benchmark.unknownRequests', { count: numberAt(requests, 'unknown') ?? '—' })}</Typography>
            <Typography>{t('benchmark.interruptions', { interruptions: numberAt(report?.data_quality, 'interruptions') ?? '—', excluded: numberAt(report?.data_quality, 'excluded_pairs') ?? '—' })}</Typography>
          </Stack>
          <Button startIcon={<RefreshIcon />} disabled={actionPending} onClick={() => void rebuildReport(id)} sx={{ mt: 2 }}>{t('benchmark.rebuildReport')}</Button>
          <Typography component="h3" variant="subtitle1" sx={{ mt: 3, mb: 1 }}>{t('benchmark.exportTitle')}</Typography>
          <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap' }}>
            {(['json', 'csv', 'markdown'] as const).map((format) => <Button key={format} component="a" href={benchmarkExportUrl(id, format)} startIcon={<DownloadIcon />} variant="outlined">{format.toUpperCase()}</Button>)}
          </Stack>
        </Paper>

      <Dialog open={deleteGameId !== null} onClose={() => setDeleteGameId(null)}>
        <DialogTitle>{t('benchmark.deleteGameTitle')}</DialogTitle>
        <DialogContent>
          <Typography>{t('benchmark.deleteGameConfirm')}</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteGameId(null)}>{t('benchmark.cancel')}</Button>
          <Button
            color="error"
            disabled={actionPending}
            onClick={() => {
              if (!deleteGameId) return;
              const gameId = deleteGameId;
              void deleteGame(id, gameId).then((ok) => { if (ok) setDeleteGameId(null); });
            }}
          >
            {t('common.delete')}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={batchDeleteOpen} onClose={() => setBatchDeleteOpen(false)}>
        <DialogTitle>{t('benchmark.batchDeleteTitle')}</DialogTitle>
        <DialogContent>
          <Typography>{t('benchmark.batchDeleteConfirm', { count: selectedIds.length })}</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setBatchDeleteOpen(false)}>{t('benchmark.cancel')}</Button>
          <Button
            color="error"
            disabled={actionPending}
            onClick={() => {
              void batchDeleteGames(id, selectedIds).then((result) => {
                if (result && result.failed.length === 0) {
                  setBatchDeleteOpen(false);
                  setSelectedIds([]);
                }
              });
            }}
          >
            {t('common.delete')}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={deleteRunOpen} onClose={() => setDeleteRunOpen(false)}>
        <DialogTitle>{t('benchmark.deleteRun')}</DialogTitle>
        <DialogContent>
          <Typography>{t('benchmark.deleteRunConfirm2')}</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteRunOpen(false)}>{t('benchmark.cancel')}</Button>
          <Button
            color="error"
            disabled={actionPending}
            onClick={() => {
              void deleteRun(id).then((ok) => { if (ok) navigate('/benchmarks'); });
            }}
          >
            {t('benchmark.deleteRun')}
          </Button>
        </DialogActions>
      </Dialog>
    </Container>
  );
}
