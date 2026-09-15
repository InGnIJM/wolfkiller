import { useState } from 'react';
import { Card, CardActionArea, Checkbox, Typography, Chip, Box, IconButton, Menu, MenuItem } from '@mui/material';
import GroupsIcon from '@mui/icons-material/Groups';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import PauseCircleIcon from '@mui/icons-material/PauseCircle';
import PlayCircleIcon from '@mui/icons-material/PlayCircle';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import type { ExecutionStatus } from '../../store/types';

interface Props {
  gameId: string;
  name: string;
  phase: string;
  roundNumber: number;
  playerCount: number;
  aliveCount: number;
  winner: string | null;
  onClick: () => void;
  onRename: () => void;
  onDelete: () => void;
  executionStatus?: ExecutionStatus;
  recoverable?: boolean;
  recoveryBlockCode?: string | null;
  controlBusy?: boolean;
  onPause?: () => void;
  onResume?: () => void;
  onRecover?: () => void;
  onMove?: () => void;
  selected?: boolean;
  onToggleSelect?: () => void;
}

const WINNER_META: Record<string, { label: string; color: 'success' | 'error' }> = {
  good: { label: '好人胜', color: 'success' },
  werewolf: { label: '狼人胜', color: 'error' },
};

const PHASE_LABELS: Record<string, string> = {
  error: '异常终止',
};

const EXECUTION_LABELS: Record<ExecutionStatus, string> = {
  running: '运行中',
  paused: '已暂停',
  interrupted: '已中断',
  recovery_blocked: '恢复受阻',
  completed: '已完成',
  failed: '执行失败',
};

export default function GameCard({
  name, phase, roundNumber, playerCount, aliveCount, winner, onClick, onRename, onDelete,
  executionStatus, recoverable = false, recoveryBlockCode, controlBusy = false,
  onPause, onResume, onRecover, onMove, selected = false, onToggleSelect,
}: Props) {
  const win = winner ? WINNER_META[winner] : null;
  const [menuEl, setMenuEl] = useState<null | HTMLElement>(null);

  return (
    <Card
      variant="outlined"
      sx={{
        transition: 'background-color 0.2s, border-color 0.2s',
        bgcolor: selected ? 'action.selected' : undefined,
        '&:hover': {
          bgcolor: selected ? 'action.selected' : 'action.hover',
          borderColor: 'primary.main',
        },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'stretch' }}>
        {onToggleSelect && (
          <Box sx={{ display: 'flex', alignItems: 'center', pl: 1 }}>
            <Checkbox
              checked={selected}
              onChange={onToggleSelect}
              onClick={(event) => event.stopPropagation()}
              slotProps={{ input: { 'aria-label': `选择 ${name}` } }}
            />
          </Box>
        )}
        <CardActionArea onClick={onClick} sx={{ p: 0, flex: 1 }}>
          <Box sx={{ px: 2.5, py: 2 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <Box>
                <Typography variant="subtitle1" sx={{ fontWeight: 500, fontSize: '0.9rem' }}>
                  {name}
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3 }}>
                  {PHASE_LABELS[phase] ?? phase} · 第 {roundNumber} 轮
                </Typography>
              </Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                {executionStatus && (
                  <Chip
                    label={EXECUTION_LABELS[executionStatus]}
                    size="small"
                    color={executionStatus === 'failed' || executionStatus === 'recovery_blocked' ? 'error' : 'default'}
                    variant="outlined"
                    title={recoveryBlockCode ?? undefined}
                  />
                )}
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <GroupsIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
                  <Typography variant="body2" color="text.secondary">
                    {aliveCount}/{playerCount}
                  </Typography>
                </Box>
                {win && (
                  <Chip
                    label={win.label}
                    color={win.color}
                    size="small"
                    variant="outlined"
                    sx={{ fontWeight: 500, height: 24, fontSize: '0.7rem' }}
                  />
                )}
              </Box>
            </Box>
          </Box>
        </CardActionArea>
        <Box sx={{ display: 'flex', alignItems: 'center', pr: 1 }}>
          <IconButton
            aria-label="对局操作"
            onClick={(event) => setMenuEl(event.currentTarget)}
          >
            <MoreVertIcon />
          </IconButton>
          <Menu
            anchorEl={menuEl}
            open={Boolean(menuEl)}
            onClose={() => setMenuEl(null)}
          >
            {executionStatus === 'running' && onPause && (
              <MenuItem disabled={controlBusy} onClick={() => { setMenuEl(null); onPause(); }}>
                <PauseCircleIcon fontSize="small" sx={{ mr: 1 }} />暂停执行
              </MenuItem>
            )}
            {executionStatus === 'paused' && onResume && (
              <MenuItem disabled={controlBusy} onClick={() => { setMenuEl(null); onResume(); }}>
                <PlayCircleIcon fontSize="small" sx={{ mr: 1 }} />继续执行
              </MenuItem>
            )}
            {(executionStatus === 'interrupted' || executionStatus === 'recovery_blocked') && onRecover && (
              <MenuItem
                disabled={controlBusy || !recoverable}
                title={!recoverable ? recoveryBlockCode ?? '此对局无法恢复' : undefined}
                onClick={() => { setMenuEl(null); onRecover(); }}
              >
                <RestartAltIcon fontSize="small" sx={{ mr: 1 }} />恢复对局
              </MenuItem>
            )}
            <MenuItem onClick={() => { setMenuEl(null); onRename(); }}>重命名</MenuItem>
            {onMove && (
              <MenuItem onClick={() => { setMenuEl(null); onMove(); }}>移动到文件夹</MenuItem>
            )}
            <MenuItem onClick={() => { setMenuEl(null); onDelete(); }}>删除</MenuItem>
          </Menu>
        </Box>
      </Box>
    </Card>
  );
}
