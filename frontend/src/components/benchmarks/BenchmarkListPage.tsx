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

const TERMINAL = new Set<BenchmarkStatus>(['completed', 'failed', 'cancelled']);
const STATUS_LABEL: Record<BenchmarkStatus, string> = {
  draft: '草稿', pending: '待开始', running: '运行中', pausing: '暂停中',
  paused: '已暂停', interrupted: '已中断', blocked: '已阻塞',
  completed: '已完成', failed: '失败', cancelled: '已取消',
};

function progressOf(run: BenchmarkRun): number {
  if (typeof run.progress === 'number') return Math.max(0, Math.min(1, run.progress));
  const planned = run.planned_count ?? 0;
  return planned > 0 ? (run.terminal_count ?? 0) / planned : 0;
}

export default function BenchmarkListPage() {
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
    <Container component="main" maxWidth="lg" sx={{ py: { xs: 2, sm: 4 }, px: { xs: 2, sm: 3 } }}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
        <Box>
          <Typography component="h1" variant="h4">模型评测</Typography>
          <Typography color="text.secondary">冻结排程、运行对局并查看可追溯报告。</Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => navigate('/benchmarks/new')}>
          新建评测
        </Button>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading && runs.length === 0 && (
        <Box role="status" sx={{ display: 'grid', placeItems: 'center', py: 8, gap: 2 }}>
          <CircularProgress size={28} /><Typography color="text.secondary">加载评测任务…</Typography>
        </Box>
      )}
      {!loading && runs.length === 0 && !error && (
        <Paper variant="outlined" sx={{ p: 4, textAlign: 'center' }}>
          <Typography variant="h6">还没有评测任务</Typography>
          <Typography color="text.secondary" sx={{ mt: 1 }}>创建草稿只会冻结配置，不会调用模型。</Typography>
        </Paper>
      )}

      {runs.length > 0 && (
        <TableContainer component={Paper} variant="outlined" sx={{ overflowX: 'auto' }}>
          <Table sx={{ minWidth: 760 }} aria-label="评测任务列表">
            <TableHead>
              <TableRow>
                <TableCell>名称</TableCell><TableCell>模式</TableCell><TableCell>状态</TableCell>
                <TableCell>完成进度</TableCell><TableCell>创建时间</TableCell><TableCell align="right">操作</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {runs.map((run) => {
                const progress = progressOf(run);
                return (
                  <TableRow key={run.run_id} hover>
                    <TableCell><Typography sx={{ fontWeight: 700 }}>{run.name}</Typography></TableCell>
                    <TableCell>{run.mode === 'mixed_arena' ? '混合对抗' : 'A/B 回归'}</TableCell>
                    <TableCell><Chip size="small" label={STATUS_LABEL[run.status] ?? run.status} /></TableCell>
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
                        查看
                      </Button>
                      <Button
                        color="error"
                        startIcon={<DeleteOutlinedIcon />}
                        disabled={actionPending}
                        onClick={() => setDeleteTarget(run)}
                      >
                        删除
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
        <DialogTitle>删除评测任务</DialogTitle>
        <DialogContent>
          <Typography>
            确定删除「{deleteTarget?.name}」？将级联删除其全部对局与报告。
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>取消</Button>
          <Button
            color="error"
            disabled={actionPending}
            onClick={() => {
              if (!deleteTarget) return;
              void deleteRun(deleteTarget.run_id).then((ok) => { if (ok) setDeleteTarget(null); });
            }}
          >
            删除
          </Button>
        </DialogActions>
      </Dialog>
    </Container>
  );
}
