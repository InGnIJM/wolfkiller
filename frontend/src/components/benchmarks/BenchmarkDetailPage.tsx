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
import type { BenchmarkReport, BenchmarkStatus } from '../../store/types';

const PHASE_LABELS: Record<string, string> = {
  waiting: '等待中', role_deal: '分配角色', night: '黑夜', dawn: '天亮',
  last_words: '遗言', speech: '发言', vote_casting: '投票',
  vote_resolution: '公布结果', game_over: '已结束', error: '异常终止',
};

const WINNER_LABELS: Record<string, string> = { good: '好人胜', werewolf: '狼人胜' };
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

interface PerformanceRow {
  id: string;
  model: string;
  dimension: 'role' | 'camp';
  group: string;
  samples: number;
  value: number | null;
}

function performanceRows(report: BenchmarkReport | null): PerformanceRow[] {
  if (!report) return [];
  const modelRows = record(report.metrics).model_performance;
  if (Array.isArray(modelRows)) {
    return modelRows.map((raw, index) => {
      const item = record(raw);
      return {
        id: String(item.id ?? index),
        model: String(item.model ?? item.model_name ?? '未知模型'),
        dimension: item.camp ? 'camp' : 'role',
        group: String(item.role ?? item.camp ?? '全部'),
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
        model: '全部模型',
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

  const rows = useMemo(() => performanceRows(report)
    .filter((row) => dimension === 'all' || row.dimension === dimension)
    .sort((left, right) => {
      const leftValue = sort === 'samples' ? left.samples : left.value ?? -1;
      const rightValue = sort === 'samples' ? right.samples : right.value ?? -1;
      return (leftValue - rightValue) * (descending ? -1 : 1);
    }), [descending, dimension, report, sort]);
  const deletableIds = items
    .map((item) => item.game_id)
    .filter((gameId): gameId is string => Boolean(gameId));

  const changeSort = (next: 'samples' | 'value') => {
    if (sort === next) setDescending((value) => !value);
    else { setSort(next); setDescending(true); }
  };

  if (!id) return <Alert severity="error">缺少评测任务 ID。</Alert>;
  if (loading && !current) {
    return <Box role="status" sx={{ flex: 1, display: 'grid', placeItems: 'center' }}><CircularProgress /></Box>;
  }
  if (!current) {
    return <Container sx={{ py: 4 }}><Alert severity="error">{error ?? '评测任务不存在'}</Alert></Container>;
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
  const status = current.status;

  return (
    <Container component="main" maxWidth="xl" sx={{ py: { xs: 2, sm: 4 }, px: { xs: 2, sm: 3 } }}>
      <Button startIcon={<ArrowBackIcon />} onClick={() => navigate('/benchmarks')} sx={{ mb: 2 }}>返回任务列表</Button>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <Box>
          <Typography component="h1" variant="h4">{current.name}</Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 1 }}><Chip label={status} /><Chip variant="outlined" label={current.mode === 'mixed_arena' ? '混合对抗' : 'A/B 回归'} /></Stack>
        </Box>
        <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap' }}>
          {(status === 'draft' || status === 'pending') && <Button variant="contained" startIcon={<PlayArrowIcon />} disabled={actionPending} onClick={() => void control(id, 'start')}>开始评测</Button>}
          {status === 'running' && <Button variant="outlined" startIcon={<PauseIcon />} disabled={actionPending} onClick={() => void control(id, 'pause')}>暂停</Button>}
          {status === 'paused' && <Button variant="contained" startIcon={<PlayArrowIcon />} disabled={actionPending} onClick={() => void control(id, 'resume')}>继续</Button>}
          {status === 'interrupted' && <Button variant="contained" startIcon={<PlayArrowIcon />} disabled={actionPending} onClick={() => void control(id, 'start')}>恢复执行</Button>}
          {!TERMINAL.has(status) && <Button color="error" startIcon={<CancelOutlinedIcon />} disabled={actionPending} onClick={() => void control(id, 'cancel')}>取消</Button>}
          <Button color="error" variant="outlined" startIcon={<DeleteOutlinedIcon />} disabled={actionPending} onClick={() => setDeleteRunOpen(true)}>删除本次评测</Button>
        </Stack>
      </Box>
      {error && <Alert severity="warning" sx={{ mt: 2 }}>{error}</Alert>}

      <Paper variant="outlined" sx={{ p: 2, mt: 3 }} aria-label="任务进度">
        <Box sx={{ display: 'flex', justifyContent: 'space-between', gap: 2, mb: 1 }}>
          <Typography sx={{ fontWeight: 700 }}>任务进度</Typography>
          <Typography sx={{ fontVariantNumeric: 'tabular-nums' }}>{current.terminal_count ?? 0}/{current.planned_count ?? 0}</Typography>
        </Box>
        <LinearProgress variant="determinate" value={Math.max(0, Math.min(100, progress * 100))} aria-label="评测完成进度" />
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          已完成 {current.completed_count ?? 0} · 失败 {current.failed_count ?? 0} · 状态每 3 秒刷新
        </Typography>
      </Paper>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' }, gap: 2, my: 3 }}>
        {[
          ['完成率', rate(numberAt(games, 'completed') !== null && numberAt(games, 'total') ? (numberAt(games, 'completed') ?? 0) / (numberAt(games, 'total') ?? 1) : null)],
          ['有效请求率', rate(numberAt(requests, 'success_rate'))],
          ['已知 token', numberAt(tokenUsage, 'known_total_tokens')?.toLocaleString() ?? '—'],
          ['调用延迟 p95', numberAt(latency, 'p95') === null ? '—' : `${numberAt(latency, 'p95')} ms`],
        ].map(([label, value]) => (
          <Card key={label} variant="outlined"><CardContent><Typography color="text.secondary">{label}</Typography><Typography variant="h5" sx={{ mt: 1, fontVariantNumeric: 'tabular-nums' }}>{value}</Typography></CardContent></Card>
        ))}
      </Box>

      <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, mb: 3 }}>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
          <Box><Typography component="h2" variant="h6">模型表现</Typography><Typography variant="body2" color="text.secondary">胜率按模型、角色或阵营查看；样本量与数值同时展示。</Typography></Box>
          <FormControl size="small" sx={{ minWidth: 160 }}><InputLabel id="performance-filter-label">分组筛选</InputLabel><Select labelId="performance-filter-label" label="分组筛选" value={dimension} onChange={(event) => setDimension(event.target.value as typeof dimension)}><MenuItem value="all">全部</MenuItem><MenuItem value="role">按角色</MenuItem><MenuItem value="camp">按阵营</MenuItem></Select></FormControl>
        </Box>
        {rows.length === 0 ? <Alert severity="info">报告尚无模型分组数据，任务运行后可刷新报告。</Alert> : (
          <TableContainer sx={{ overflowX: 'auto' }}>
            <Table sx={{ minWidth: 640 }} aria-label="模型表现表">
              <TableHead><TableRow><TableCell>模型</TableCell><TableCell>维度</TableCell><TableCell>分组</TableCell><TableCell sortDirection={sort === 'samples' ? (descending ? 'desc' : 'asc') : false}><Button color="inherit" aria-sort={sort === 'samples' ? (descending ? 'descending' : 'ascending') : 'none'} onClick={() => changeSort('samples')}>样本数</Button></TableCell><TableCell sortDirection={sort === 'value' ? (descending ? 'desc' : 'asc') : false}><Button color="inherit" aria-sort={sort === 'value' ? (descending ? 'descending' : 'ascending') : 'none'} onClick={() => changeSort('value')}>胜率</Button></TableCell></TableRow></TableHead>
              <TableBody>{rows.map((row) => <TableRow key={row.id}><TableCell>{row.model}</TableCell><TableCell>{row.dimension === 'role' ? '角色' : '阵营'}</TableCell><TableCell>{row.group}</TableCell><TableCell>{row.samples}</TableCell><TableCell>{rate(row.value)}</TableCell></TableRow>)}</TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 }, mb: 3 }}>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
          <Box>
            <Typography component="h2" variant="h6">本次对局</Typography>
            <Typography variant="body2" color="text.secondary">评测对局只在此页管理，可回放或删除。</Typography>
          </Box>
          {selectedIds.length > 0 && (
            <Stack direction="row" spacing={1}>
              <Typography variant="body2" sx={{ alignSelf: 'center' }}>已选 {selectedIds.length} 局</Typography>
              <Button color="error" disabled={actionPending} onClick={() => setBatchDeleteOpen(true)}>批量删除</Button>
              <Button onClick={() => setSelectedIds([])}>取消选择</Button>
            </Stack>
          )}
        </Box>
        {items.length === 0 ? <Typography color="text.secondary">暂无排程对局。</Typography> : (
          <TableContainer sx={{ overflowX: 'auto' }}>
            <Table sx={{ minWidth: 760 }} aria-label="评测对局表">
              <TableHead>
                <TableRow>
                  <TableCell padding="checkbox">
                    <Checkbox
                      slotProps={{ input: { 'aria-label': '全选对局' } }}
                      checked={deletableIds.length > 0 && deletableIds.every((gameId) => selectedIds.includes(gameId))}
                      indeterminate={selectedIds.length > 0 && selectedIds.length < deletableIds.length}
                      onChange={() => setSelectedIds((current) => (
                        current.length === deletableIds.length ? [] : [...deletableIds]
                      ))}
                    />
                  </TableCell>
                  <TableCell>对局</TableCell>
                  <TableCell>阶段</TableCell>
                  <TableCell>状态</TableCell>
                  <TableCell>胜负</TableCell>
                  <TableCell align="right">操作</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {items.map((item) => {
                  const gameId = item.game_id;
                  const selected = Boolean(gameId && selectedIds.includes(gameId));
                  const replayLabel = item.event_seq
                    ? `查看回放 · 事件 ${item.event_seq}`
                    : '查看回放';
                  return (
                    <TableRow key={item.item_index} selected={selected}>
                      <TableCell padding="checkbox">
                        {gameId && (
                          <Checkbox
                            slotProps={{ input: { 'aria-label': `选择 ${item.name ?? gameId}` } }}
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
                        <Typography sx={{ fontWeight: 700 }}>{item.name ?? `排程 #${item.item_index + 1}`}</Typography>
                        <Typography variant="body2" color="text.secondary">
                          {gameId ?? '尚未开局'}
                          {item.round_number != null ? ` · 第 ${item.round_number} 轮` : ''}
                          {item.alive_count != null && item.player_count != null ? ` · ${item.alive_count}/${item.player_count}` : ''}
                        </Typography>
                      </TableCell>
                      <TableCell>{item.phase ? (PHASE_LABELS[item.phase] ?? item.phase) : '—'}</TableCell>
                      <TableCell>{item.terminal_reason ?? item.status}{item.execution_status ? ` · ${item.execution_status}` : ''}</TableCell>
                      <TableCell>{item.winner ? (WINNER_LABELS[item.winner] ?? item.winner) : '—'}</TableCell>
                      <TableCell align="right">
                        {gameId && (
                          <Stack direction="row" spacing={1} sx={{ justifyContent: 'flex-end' }}>
                            <Button
                              component={Link}
                              to={`/game/${gameId}${item.event_seq ? `?seq=${item.event_seq}` : ''}`}
                              size="small"
                            >
                              {replayLabel}
                            </Button>
                            <Button
                              color="error"
                              size="small"
                              disabled={actionPending}
                              onClick={() => setDeleteGameId(gameId)}
                            >
                              删除
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
          <Typography component="h2" variant="h6">诊断与报告</Typography>
          <Stack spacing={1} sx={{ mt: 2 }}>
            <Typography>指标版本：{report?.metric_version ?? '等待生成'}</Typography>
            <Typography>报告状态：{report?.status === 'pending' ? '报告生成中' : report?.status === 'failed' ? '报告生成失败，可重新生成' : report?.provisional ? '运行中临时报告' : report ? '已冻结数据报告' : '待生成'}</Typography>
            <Typography>用量完整率：{rate(numberAt(tokenUsage, 'completeness_rate'))}</Typography>
            <Typography>未知请求：{numberAt(requests, 'unknown') ?? '—'}</Typography>
            <Typography>中断/排除：{numberAt(report?.data_quality, 'interruptions') ?? '—'} / {numberAt(report?.data_quality, 'excluded_pairs') ?? '—'}</Typography>
          </Stack>
          <Button startIcon={<RefreshIcon />} disabled={actionPending} onClick={() => void rebuildReport(id)} sx={{ mt: 2 }}>重新生成报告</Button>
          <Typography component="h3" variant="subtitle1" sx={{ mt: 3, mb: 1 }}>导出</Typography>
          <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap' }}>
            {(['json', 'csv', 'markdown'] as const).map((format) => <Button key={format} component="a" href={benchmarkExportUrl(id, format)} startIcon={<DownloadIcon />} variant="outlined">{format.toUpperCase()}</Button>)}
          </Stack>
        </Paper>

      <Dialog open={deleteGameId !== null} onClose={() => setDeleteGameId(null)}>
        <DialogTitle>删除评测对局</DialogTitle>
        <DialogContent>
          <Typography>确定删除该评测对局？此操作不可恢复。</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteGameId(null)}>取消</Button>
          <Button
            color="error"
            disabled={actionPending}
            onClick={() => {
              if (!deleteGameId) return;
              const gameId = deleteGameId;
              void deleteGame(id, gameId).then((ok) => { if (ok) setDeleteGameId(null); });
            }}
          >
            删除
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={batchDeleteOpen} onClose={() => setBatchDeleteOpen(false)}>
        <DialogTitle>批量删除评测对局</DialogTitle>
        <DialogContent>
          <Typography>确定删除已选的 {selectedIds.length} 局？此操作不可恢复。</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setBatchDeleteOpen(false)}>取消</Button>
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
            删除
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={deleteRunOpen} onClose={() => setDeleteRunOpen(false)}>
        <DialogTitle>删除本次评测</DialogTitle>
        <DialogContent>
          <Typography>将级联删除本次评测的全部对局与报告，且不可恢复。</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteRunOpen(false)}>取消</Button>
          <Button
            color="error"
            disabled={actionPending}
            onClick={() => {
              void deleteRun(id).then((ok) => { if (ok) navigate('/benchmarks'); });
            }}
          >
            删除本次评测
          </Button>
        </DialogActions>
      </Dialog>
    </Container>
  );
}
