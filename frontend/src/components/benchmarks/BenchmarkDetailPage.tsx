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
import type { BenchmarkReport, BenchmarkStatus, GamePhase, WinReason, WinningCamp } from '../../store/types';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useLabels } from '../../i18n/useLabels';
import { BACKDROP } from '../../theme/tokens';

const TERMINAL = new Set<BenchmarkStatus>(['completed', 'failed', 'cancelled']);
const ITEM_STATUSES = ['pending', 'running', 'completed', 'failed', 'cancelled'] as const;

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
  /** Non-numeric sample text (e.g. "12 / 3534 次调用") overrides ``sample``. */
  sampleText?: string;
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
  // 信息位角色是池化查验指标的样本来源：一个都没有时要明说，否则
  // 「无样本」会被读成「都 miss 了」。旧报告没有该键时退回只看预言家。
  const infoRoles = Array.isArray(coverage.info_roles_present)
    ? coverage.info_roles_present
    : null;
  const info = infoRoles !== null && infoRoles.length === 0
    ? t('benchmark.noInfoRoleNote')
    : '';
  return t('benchmark.coverage', {
    parts: parts.join(t('game.listJoin')), note: `${info}${seer}`,
  });
}

function performanceRows(report: BenchmarkReport | null, t: TFunction): PerformanceRow[] {
  if (!report) return [];
  const modelRows = record(report.metrics).model_performance;
  if (Array.isArray(modelRows)) {
    return modelRows.map((raw, index) => {
      const item = record(raw);
      return {
        id: String(item.id ?? index),
        model: String(item.model_label ?? item.model ?? item.model_name ?? t('benchmark.unknownModel')),
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

interface UncertaintyRow {
  id: string;
  model: string;
  dimension: 'role' | 'camp';
  group: string;
  games: number | null;
  validSeats: number | null;
  winRate: number | null;
  low: number | null;
  high: number | null;
  method: string | null;
  insufficientBlocks: boolean;
}

// 混座不确定度行（summary.mixed_uncertainty）：多块 bootstrap、单块 Wilson。
// 旧报告没有该键或不是数组时返回空，由页面降级为不渲染。
function uncertaintyRows(report: BenchmarkReport | null, t: TFunction): UncertaintyRow[] {
  const candidate = record(report?.uncertainty).mixed_arena
    ?? record(report?.summary).mixed_uncertainty;
  const rows = Array.isArray(candidate) ? candidate : [];
  return rows.map((raw, index) => {
    const item = record(raw);
    const field = (key: string): number | null => {
      const value = item[key];
      return typeof value === 'number' && Number.isFinite(value) ? value : null;
    };
    return {
      id: String(item.id ?? index),
      model: String(item.model_label ?? item.model ?? t('benchmark.unknownModel')),
      dimension: item.camp ? 'camp' : 'role',
      group: String(item.role ?? item.camp ?? t('benchmark.all')),
      games: field('games'),
      validSeats: field('valid_seats'),
      winRate: field('win_rate'),
      low: field('low'),
      high: field('high'),
      method: typeof item.interval_method === 'string' ? item.interval_method : null,
      insufficientBlocks: item.insufficient_blocks === true,
    };
  });
}

interface PairRow {
  pairId: string;
  baseline: number | null;
  candidate: number | null;
  difference: number | null;
}

// 配对差异行：summary.paired_regression / paired_latency_ms 共用同一形状。
function pairRows(source: unknown): PairRow[] {
  const differences = record(source).differences;
  if (!Array.isArray(differences)) return [];
  return differences.map((raw) => {
    const item = record(raw);
    return {
      pairId: String(item.pair_id ?? ''),
      baseline: typeof item.baseline === 'number' ? item.baseline : null,
      candidate: typeof item.candidate === 'number' ? item.candidate : null,
      difference: typeof item.difference === 'number' ? item.difference : null,
    };
  });
}

function intervalText(low: number | null, high: number | null): string {
  return low === null || high === null
    ? '—'
    : `[${(low * 100).toFixed(1)}% – ${(high * 100).toFixed(1)}%]`;
}

// 配对区间的数值不是胜率：得分差用小数、延迟差用 ms，不能套百分比格式。
function fmtInterval(
  low: number | null, high: number | null,
  format: (value: number | null) => string,
): string {
  return low === null || high === null ? '—' : `[${format(low)} – ${format(high)}]`;
}

function decimalText(value: number | null): string {
  return value === null ? '—' : value.toFixed(4);
}

function msText(value: number | null): string {
  return value === null ? '—' : `${Math.round(value).toLocaleString()} ms`;
}

function MetricRowsTable({ rows, aria, t }: { rows: DecisionRow[]; aria: string; t: TFunction }) {
  return (
    <TableContainer sx={{ overflowX: 'auto' }}>
      <Table sx={{ minWidth: 520 }} aria-label={aria}>
        <TableHead>
          <TableRow><TableCell>{t('benchmark.colMetric')}</TableCell><TableCell>{t('benchmark.colValue')}</TableCell><TableCell>{t('benchmark.colSample')}</TableCell></TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id}>
              <TableCell>{row.label}</TableCell>
              <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>
                {row.count ? (row.value === null ? '—' : row.value.toLocaleString()) : rate(row.value)}
              </TableCell>
              <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>
                {row.sampleText ?? (row.sample === null ? '—' : row.sample.toLocaleString())}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

function PairTable({ rows, format, aria, t }: {
  rows: PairRow[];
  format: (value: number | null) => string;
  aria: string;
  t: TFunction;
}) {
  return (
    <TableContainer sx={{ overflowX: 'auto' }}>
      <Table sx={{ minWidth: 520 }} aria-label={aria}>
        <TableHead>
          <TableRow><TableCell>{t('benchmark.colPair')}</TableCell><TableCell>{t('benchmark.colBaseline')}</TableCell><TableCell>{t('benchmark.colCandidate')}</TableCell><TableCell>{t('benchmark.colDifference')}</TableCell></TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.pairId}>
              <TableCell>{row.pairId}</TableCell>
              <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{format(row.baseline)}</TableCell>
              <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{format(row.candidate)}</TableCell>
              <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{format(row.difference)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
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
  const [statusFilter, setStatusFilter] = useState('');

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
  const filteredItems = useMemo(
    () => (statusFilter ? items.filter((item) => item.status === statusFilter) : items),
    [items, statusFilter],
  );
  const deletableIds = filteredItems
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

  const campEntries = Object.entries(record(games.win_rate_by_camp))
    .map(([camp, value]): [string, number | null] => [camp, typeof value === 'number' ? value : null]);
  const reasonEntries = Object.entries(record(games.win_reason_distribution))
    .map(([reason, value]): [string, number] => [reason, typeof value === 'number' ? value : 0]);
  const seatEntries = Object.entries(record(games.win_rate_by_seat))
    .map(([seat, raw]) => {
      const item = record(raw);
      return [seat, {
        value: typeof item.win_rate === 'number' ? item.win_rate : null,
        wins: typeof item.wins === 'number' ? item.wins : null,
        games: typeof item.games === 'number' ? item.games : null,
      }] as const;
    });
  const hasOutcome = campEntries.length > 0 || reasonEntries.length > 0 || seatEntries.length > 0;

  const uncRows = uncertaintyRows(report, t);
  const pairedBlock = record(reportSummary.paired_regression);
  const pairedBoot = record(pairedBlock.bootstrap);
  const pairedPairs = pairRows(reportSummary.paired_regression);
  const pairedLatencyBlock = record(reportSummary.paired_latency_ms);
  const latencyBoot = record(pairedLatencyBlock.bootstrap);
  const latencyPairs = pairRows(reportSummary.paired_latency_ms);

  const eligibleTerminal = numberAt(requests, 'eligible_terminal');
  const terminalVotes = numberAt(requests, 'terminal_votes');
  const requestRows: DecisionRow[] = [
    { id: 'valid', label: t('benchmark.reqValidRate'), value: numberAt(requests, 'valid_rate'), sample: eligibleTerminal, count: false },
    { id: 'success', label: t('benchmark.reqSuccessRate'), value: numberAt(requests, 'success_rate'), sample: eligibleTerminal, count: false },
    { id: 'voluntary', label: t('benchmark.reqVoluntaryAbstain'), value: numberAt(requests, 'voluntary_abstain_rate'), sample: eligibleTerminal, count: false },
    { id: 'failure', label: t('benchmark.reqFailureRate'), value: numberAt(requests, 'failure_rate'), sample: eligibleTerminal, count: false },
    { id: 'fallback', label: t('benchmark.reqFallbackRate'), value: numberAt(requests, 'fallback_rate'), sample: eligibleTerminal, count: false },
    { id: 'tech-abstain', label: t('benchmark.reqTechAbstainRate'), value: numberAt(requests, 'technical_abstain_rate'), sample: terminalVotes, count: false },
  ];
  const passBreakdown = record(requests.pass_breakdown);
  const passRows: DecisionRow[] = [
    { id: 'campaign', label: t('benchmark.passSheriffCampaign'), value: numberAt(passBreakdown, 'sheriff_campaign'), sample: null, count: true },
    { id: 'vote', label: t('benchmark.passVoteAbstain'), value: numberAt(passBreakdown, 'vote_abstain'), sample: null, count: true },
    { id: 'night', label: t('benchmark.passNightOptional'), value: numberAt(passBreakdown, 'night_optional'), sample: null, count: true },
  ];
  const votesBlock = record(requests.votes);
  const voteRows = (['exile', 'sheriff'] as const)
    .map((kind) => {
      const bucket = record(votesBlock[kind]);
      return Object.keys(bucket).length === 0 ? null : {
        kind,
        terminal: numberAt(bucket, 'terminal'),
        technical: numberAt(bucket, 'technical'),
        voluntary: numberAt(bucket, 'voluntary'),
        failed: numberAt(bucket, 'failed'),
        techRate: numberAt(bucket, 'technical_abstain_rate'),
        volRate: numberAt(bucket, 'voluntary_abstain_rate'),
      };
    })
    .filter((row): row is NonNullable<typeof row> => row !== null);
  const logicalLatency = record(requests.logical_latency_ms);
  const llmCalls = numberAt(attempts, 'llm_calls');
  const retriedCalls = numberAt(attempts, 'retried_llm_calls');
  const usageRows: DecisionRow[] = [
    {
      id: 'retry', label: t('benchmark.usageRetryRate'),
      value: numberAt(attempts, 'retry_rate'), sample: null, count: false,
      sampleText: t('benchmark.usageRetrySample', { retried: retriedCalls ?? '—', calls: llmCalls ?? '—' }),
    },
    { id: 'tokens', label: t('benchmark.usageTokensPerAttempt'), value: numberAt(attempts, 'tokens_per_attempt'), sample: numberAt(tokenUsage, 'known_attempts'), count: true },
    { id: 'executed', label: t('benchmark.usageExecuted'), value: numberAt(attempts, 'executed_requests'), sample: numberAt(attempts, 'total'), count: true },
    { id: 'redispatches', label: t('benchmark.usageRedispatches'), value: numberAt(attempts, 'recovery_redispatches'), sample: null, count: true },
    { id: 'known-seats', label: t('benchmark.usageKnownSeats'), value: numberAt(tokenUsage, 'cache_known_seats'), sample: null, count: true },
    { id: 'cold-seats', label: t('benchmark.usageColdSeats'), value: numberAt(tokenUsage, 'cache_cold_seats'), sample: null, count: true },
    { id: 'cache-ex-cold', label: t('benchmark.usageCacheHitExCold'), value: numberAt(tokenUsage, 'cache_hit_rate_excluding_cold_seats'), sample: null, count: false },
  ];
  const decisionRows: DecisionRow[] = [
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
  ];
  const reliability = record(reportSummary.reliability);
  const ruleRejected = numberAt(reliability, 'rule_rejected');
  const ruleRejectedPerGame = numberAt(reliability, 'per_game');
  const ruleRejectedPerRequest = numberAt(reliability, 'per_request');
  const rejectionRows = Object.entries(record(reliability.by_contract)).map(([contract, raw]) => {
    const item = record(raw);
    const violations = Object.entries(record(item.violations))
      .map(([code, count]) => `${code}×${typeof count === 'number' ? count : 0}`)
      .join(t('game.listJoin'));
    return { contract, rejected: typeof item.rejected === 'number' ? item.rejected : 0, violations };
  });
  const hasRequestDetail = Object.keys(requests).length > 0 || Object.keys(attempts).length > 0;
  const inclusion = record(report?.inclusion);
  const exclusion = record(report?.exclusion);
  const dataQuality = record(report?.data_quality);

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
          <Typography sx={{ fontWeight: 600 }}>{t('benchmark.progressAria')}</Typography>
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
          [t('benchmark.metricReqSuccess'), rate(numberAt(requests, 'valid_rate'))],
          [t('benchmark.metricActionRate'), rate(numberAt(requests, 'success_rate'))],
          [t('benchmark.metricTokens'), numberAt(tokenUsage, 'known_total_tokens')?.toLocaleString() ?? '—'],
          [t('benchmark.metricCacheHit'), rate(numberAt(tokenUsage, 'cache_hit_rate'))],
          [t('benchmark.metricLatency'), numberAt(latency, 'p95') === null ? '—' : `${numberAt(latency, 'p95')} ms`],
          [t('benchmark.metricRounds'), averageRounds === null ? '—' : averageRounds.toFixed(1)],
          [t('benchmark.metricInterruption'), rate(interruptionRate)],
        ].map(([label, value]) => (
          <Card key={label} variant="outlined"><CardContent><Typography color="text.secondary">{label}</Typography><Typography variant="h5" sx={{ mt: 1, fontVariantNumeric: 'tabular-nums' }}>{value}</Typography></CardContent></Card>
        ))}
      </Box>

      {hasOutcome && (
        <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, mb: 3 }}>
          <Typography component="h2" variant="h6">{t('benchmark.outcomeTitle')}</Typography>
          <Typography variant="body2" color="text.secondary">{t('benchmark.outcomeDesc')}</Typography>
          {campEntries.length > 0 && (
            <Box sx={{ mt: 1.5 }}>
              <Typography variant="subtitle2">{t('benchmark.campWinsLabel')}</Typography>
              <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap', mt: 0.5 }}>
                {campEntries.map(([camp, value]) => (
                  <Chip key={camp} variant="outlined" label={`${L.WINNER_LABELS[camp as WinningCamp] ?? camp} ${rate(value)}`} />
                ))}
              </Stack>
            </Box>
          )}
          {reasonEntries.length > 0 && (
            <Box sx={{ mt: 1.5 }}>
              <Typography variant="subtitle2">{t('benchmark.winReasonsLabel')}</Typography>
              <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap', mt: 0.5 }}>
                {reasonEntries.map(([reason, count]) => (
                  <Chip key={reason} variant="outlined" label={`${L.WIN_REASON_LABELS[reason as WinReason] ?? reason} ×${count}`} />
                ))}
              </Stack>
            </Box>
          )}
          {seatEntries.length > 0 && (
            <Box sx={{ mt: 1.5 }}>
              <Typography variant="subtitle2">{t('benchmark.seatWinRatesLabel')}</Typography>
              <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap', mt: 0.5 }}>
                {seatEntries.map(([seat, stats]) => (
                  <Chip
                    key={seat}
                    size="small"
                    variant="outlined"
                    label={`${t('benchmark.seatN', { seat })} ${rate(stats.value)}${stats.wins !== null && stats.games !== null ? ` (${stats.wins}/${stats.games})` : ''}`}
                  />
                ))}
              </Stack>
            </Box>
          )}
        </Paper>
      )}

      <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, mb: 3 }}>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
          <Box><Typography component="h2" variant="h6">{t('benchmark.perfTitle')}</Typography><Typography variant="body2" color="text.secondary">{t('benchmark.perfDesc1')}</Typography><Typography variant="body2" color="text.secondary">{t('benchmark.perfDesc2')}</Typography></Box>
          <FormControl size="small" sx={{ minWidth: 160 }}><InputLabel id="performance-filter-label">{t('benchmark.groupBy')}</InputLabel><Select labelId="performance-filter-label" label={t('benchmark.groupBy')} value={dimension} onChange={(event) => setDimension(event.target.value as typeof dimension)}><MenuItem value="all">{t('benchmark.all')}</MenuItem><MenuItem value="role">{t('benchmark.byRole')}</MenuItem><MenuItem value="camp">{t('benchmark.byCamp')}</MenuItem></Select></FormControl>
        </Box>
        {rows.length === 0 ? <Alert severity="info">{t('benchmark.perfEmpty')}</Alert> : (
          <TableContainer sx={{ overflowX: 'auto' }}>
            <Table sx={{ minWidth: 640 }} aria-label={t('benchmark.perfTableAria')}>
              <TableHead><TableRow><TableCell>{t('benchmark.colModel')}</TableCell><TableCell>{t('benchmark.colDimension')}</TableCell><TableCell>{t('benchmark.colGroup')}</TableCell><TableCell sortDirection={sort === 'samples' ? (descending ? 'desc' : 'asc') : false}><Button color="inherit" aria-sort={sort === 'samples' ? (descending ? 'descending' : 'ascending') : 'none'} onClick={() => changeSort('samples')}>{t('benchmark.colSamples')}</Button></TableCell><TableCell sortDirection={sort === 'value' ? (descending ? 'desc' : 'asc') : false}><Button color="inherit" aria-sort={sort === 'value' ? (descending ? 'descending' : 'ascending') : 'none'} onClick={() => changeSort('value')}>{t('benchmark.colWinRate')}</Button></TableCell></TableRow></TableHead>
              <TableBody>{rows.map((row) => <TableRow key={row.id}><TableCell>{row.model}</TableCell><TableCell>{t(row.dimension === 'role' ? 'benchmark.dimRole' : 'benchmark.dimCamp')}</TableCell><TableCell>{row.group}</TableCell><TableCell>{row.samples}</TableCell><TableCell>{rate(row.value)}</TableCell></TableRow>)}</TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      {current.mode === 'paired_regression' ? (
        <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, mb: 3 }}>
          <Typography component="h2" variant="h6">{t('benchmark.uncertaintyTitle')}</Typography>
          <Typography variant="body2" color="text.secondary">{t('benchmark.uncertaintyDescPaired')}</Typography>
          <Stack direction="row" useFlexGap spacing={3} sx={{ flexWrap: 'wrap', mt: 1.5 }}>
            <Typography>{t('benchmark.meanDiffLabel')}: <strong>{decimalText(numberAt(pairedBlock, 'mean_difference'))}</strong></Typography>
            <Typography>{t('benchmark.colInterval')}: <strong>{fmtInterval(numberAt(pairedBoot, 'low'), numberAt(pairedBoot, 'high'), decimalText)}</strong></Typography>
            <Typography>{t('benchmark.pairCountLabel')}: <strong>{numberAt(pairedBlock, 'pair_count') ?? '—'}</strong></Typography>
          </Stack>
          {pairedBoot.insufficient_pairs === true && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>{t('benchmark.insufficientPairsNote')}</Typography>
          )}
          {pairedPairs.length === 0 ? (
            <Typography color="text.secondary" sx={{ mt: 1 }}>{t('benchmark.noPairs')}</Typography>
          ) : (
            <Box sx={{ mt: 1 }}><PairTable rows={pairedPairs} format={decimalText} aria={t('benchmark.uncertaintyTitle')} t={t} /></Box>
          )}
          <Typography component="h3" variant="subtitle1" sx={{ mt: 2, mb: 0.5 }}>{t('benchmark.pairedLatencyTitle')}</Typography>
          <Stack direction="row" useFlexGap spacing={3} sx={{ flexWrap: 'wrap' }}>
            <Typography>{t('benchmark.latencyMeanDiffLabel')}: <strong>{msText(numberAt(pairedLatencyBlock, 'mean_difference'))}</strong></Typography>
            <Typography>{t('benchmark.colInterval')}: <strong>{fmtInterval(numberAt(latencyBoot, 'low'), numberAt(latencyBoot, 'high'), msText)}</strong></Typography>
            <Typography>{t('benchmark.excludedInterruptedLabel')}: <strong>{numberAt(pairedLatencyBlock, 'excluded_interrupted_pairs') ?? '—'}</strong></Typography>
            <Typography>{t('benchmark.excludedMissingLabel')}: <strong>{numberAt(pairedLatencyBlock, 'excluded_incomplete_pairs') ?? '—'}</strong></Typography>
          </Stack>
          {latencyPairs.length > 0 && (
            <Box sx={{ mt: 1 }}><PairTable rows={latencyPairs} format={msText} aria={t('benchmark.pairedLatencyTitle')} t={t} /></Box>
          )}
        </Paper>
      ) : (
        <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, mb: 3 }}>
          <Typography component="h2" variant="h6">{t('benchmark.uncertaintyTitle')}</Typography>
          <Typography variant="body2" color="text.secondary">{t('benchmark.uncertaintyDescMixed')}</Typography>
          {uncRows.length === 0 ? (
            <Typography color="text.secondary" sx={{ mt: 1 }}>{t('benchmark.uncertaintyEmpty')}</Typography>
          ) : (
          <TableContainer sx={{ overflowX: 'auto', mt: 1 }}>
            <Table sx={{ minWidth: 760 }} aria-label={t('benchmark.uncertaintyTitle')}>
              <TableHead>
                <TableRow><TableCell>{t('benchmark.colModel')}</TableCell><TableCell>{t('benchmark.colDimension')}</TableCell><TableCell>{t('benchmark.colGroup')}</TableCell><TableCell>{t('benchmark.colGames')}</TableCell><TableCell>{t('benchmark.colValidSeats')}</TableCell><TableCell>{t('benchmark.colWinRate')}</TableCell><TableCell>{t('benchmark.colInterval')}</TableCell><TableCell>{t('benchmark.colMethod')}</TableCell></TableRow>
              </TableHead>
              <TableBody>
                {uncRows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell>{row.model}</TableCell>
                    <TableCell>{t(row.dimension === 'role' ? 'benchmark.dimRole' : 'benchmark.dimCamp')}</TableCell>
                    <TableCell>{row.group}</TableCell>
                    <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{row.games ?? '—'}</TableCell>
                    <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{row.validSeats ?? '—'}</TableCell>
                    <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{rate(row.winRate)}</TableCell>
                    <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{intervalText(row.low, row.high)}</TableCell>
                    <TableCell>{row.method ?? '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          )}
          {uncRows.some((row) => row.insufficientBlocks) && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>{t('benchmark.insufficientBlocksNote')}</Typography>
          )}
        </Paper>
      )}

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
          <MetricRowsTable rows={decisionRows} aria={t('benchmark.decisionTableAria')} t={t} />
        )}
      </Paper>

      {hasRequestDetail && (
        <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, mb: 3 }}>
          <Typography component="h2" variant="h6">{t('benchmark.requestsTitle')}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('benchmark.requestsDesc')}</Typography>
          <MetricRowsTable rows={requestRows} aria={t('benchmark.requestsTitle')} t={t} />
          {voteRows.length > 0 && (
            <>
              <Typography component="h3" variant="subtitle1" sx={{ mt: 2, mb: 0.5 }}>{t('benchmark.votesTitle')}</Typography>
              <TableContainer sx={{ overflowX: 'auto' }}>
                <Table sx={{ minWidth: 640 }} aria-label={t('benchmark.votesTitle')}>
                  <TableHead>
                    <TableRow>
                      <TableCell>{t('benchmark.colBallot')}</TableCell>
                      <TableCell>{t('benchmark.colTerminal')}</TableCell>
                      <TableCell>{t('benchmark.colTechnical')}</TableCell>
                      <TableCell>{t('benchmark.colTechRate')}</TableCell>
                      <TableCell>{t('benchmark.colVoluntary')}</TableCell>
                      <TableCell>{t('benchmark.colVolRate')}</TableCell>
                      <TableCell>{t('benchmark.colFailedCount')}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {voteRows.map((row) => (
                      <TableRow key={row.kind}>
                        <TableCell>{t(row.kind === 'exile' ? 'benchmark.ballotExile' : 'benchmark.ballotSheriff')}</TableCell>
                        <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{row.terminal ?? '—'}</TableCell>
                        <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{row.technical ?? '—'}</TableCell>
                        <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{rate(row.techRate)}</TableCell>
                        <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{row.voluntary ?? '—'}</TableCell>
                        <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{rate(row.volRate)}</TableCell>
                        <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{row.failed ?? '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </>
          )}
          {passRows.some((row) => row.value !== null) && (
            <>
              <Typography component="h3" variant="subtitle1" sx={{ mt: 2, mb: 0.5 }}>{t('benchmark.passBreakdownTitle')}</Typography>
              <MetricRowsTable rows={passRows} aria={t('benchmark.passBreakdownTitle')} t={t} />
            </>
          )}
          {(Object.keys(logicalLatency).length > 0 || Object.keys(attempts).length > 0) && (
            <>
              <Typography component="h3" variant="subtitle1" sx={{ mt: 2, mb: 0.5 }}>{t('benchmark.usageTitle')}</Typography>
              {Object.keys(logicalLatency).length > 0 && (
                <Typography variant="body2" sx={{ mb: 1 }}>
                  {t('benchmark.logicalLatencyTitle')}: <strong>{msText(numberAt(logicalLatency, 'p50'))}</strong>
                  {' / p95: '}<strong>{msText(numberAt(logicalLatency, 'p95'))}</strong>
                  {' / p99: '}<strong>{msText(numberAt(logicalLatency, 'p99'))}</strong>
                </Typography>
              )}
              <MetricRowsTable rows={usageRows} aria={t('benchmark.usageTitle')} t={t} />
            </>
          )}
          {rejectionRows.length > 0 && (
            <>
              <Typography component="h3" variant="subtitle1" sx={{ mt: 2, mb: 0.5 }}>{t('benchmark.reliabilityTitle')}</Typography>
              <Typography variant="body2" color="text.secondary">{t('benchmark.reliabilityDesc')}</Typography>
              {ruleRejected !== null && (
                <Typography variant="body2" sx={{ mb: 1 }}>
                  {t('benchmark.reliabilityLine', {
                    count: ruleRejected,
                    perGame: ruleRejectedPerGame === null ? '—' : ruleRejectedPerGame.toFixed(2),
                    perRequest: ruleRejectedPerRequest === null ? '—' : rate(ruleRejectedPerRequest),
                  })}
                </Typography>
              )}
              <TableContainer sx={{ overflowX: 'auto' }}>
                <Table sx={{ minWidth: 480 }} aria-label={t('benchmark.reliabilityTitle')}>
                  <TableHead>
                    <TableRow>
                      <TableCell>{t('benchmark.colContract')}</TableCell>
                      <TableCell>{t('benchmark.colRejected')}</TableCell>
                      <TableCell>{t('benchmark.colViolations')}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {rejectionRows.map((row) => (
                      <TableRow key={row.contract}>
                        <TableCell>{row.contract}</TableCell>
                        <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{row.rejected}</TableCell>
                        <TableCell>{row.violations || '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </>
          )}
        </Paper>
      )}

      <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, mb: 3 }}>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
          <Box>
            <Typography component="h2" variant="h6">{t('benchmark.gamesTitle')}</Typography>
            <Typography variant="body2" color="text.secondary">{t('benchmark.gamesHint')}</Typography>
          </Box>
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel id="benchmark-status-filter-label">{t('benchmark.filterStatus')}</InputLabel>
            <Select
              labelId="benchmark-status-filter-label"
              value={statusFilter}
              label={t('benchmark.filterStatus')}
              onChange={(event) => setStatusFilter(event.target.value)}
            >
              <MenuItem value="">{t('benchmark.statusAll')}</MenuItem>
              {ITEM_STATUSES.map((itemStatus) => (
                <MenuItem key={itemStatus} value={itemStatus}>{itemStatus}</MenuItem>
              ))}
            </Select>
          </FormControl>
          {selectedIds.length > 0 && (
            <Stack direction="row" spacing={1}>
              <Typography variant="body2" sx={{ alignSelf: 'center' }}>{t('benchmark.selectedGames', { count: selectedIds.length })}</Typography>
              <Button color="error" disabled={actionPending} onClick={() => setBatchDeleteOpen(true)}>{t('lobby.batchDelete')}</Button>
              <Button onClick={() => setSelectedIds([])}>{t('lobby.clearSelection')}</Button>
            </Stack>
          )}
        </Box>
        {filteredItems.length === 0 ? <Typography color="text.secondary">{t('benchmark.noGames')}</Typography> : (
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
                {filteredItems.map((item) => {
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
                        <Typography sx={{ fontWeight: 600 }}>{item.name ?? t('benchmark.scheduledItem', { index: item.item_index + 1 })}</Typography>
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
            <Typography>{t('benchmark.diagInclusion', { with: numberAt(inclusion, 'games_with_results') ?? '—', without: numberAt(inclusion, 'games_without_results') ?? '—' })}</Typography>
            <Typography>{t('benchmark.diagExclusion', { unstarted: numberAt(exclusion, 'unstarted_items') ?? '—', failed: numberAt(exclusion, 'failed_items') ?? '—', cancelled: numberAt(exclusion, 'cancelled_items') ?? '—' })}</Typography>
            <Typography>{t('benchmark.diagSummaries', { available: numberAt(dataQuality, 'summaries_available') ?? '—', missing: numberAt(dataQuality, 'summaries_missing') ?? '—' })}</Typography>
            <Typography>{t('benchmark.diagRecovery', { retries: numberAt(dataQuality, 'recovery_retries') ?? '—', attempts: numberAt(dataQuality, 'recovery_attempts') ?? '—' })}</Typography>
            {current.mode === 'paired_regression' && (
              <Typography>{t('benchmark.diagLatencyPairs', { interrupted: numberAt(dataQuality, 'latency_pairs_excluded_for_interruptions') ?? '—', missing: numberAt(dataQuality, 'latency_pairs_excluded_for_missing_data') ?? '—' })}</Typography>
            )}
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
