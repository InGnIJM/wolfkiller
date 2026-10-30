import { useState } from 'react';
import { Card, CardActionArea, Checkbox, Typography, Chip, Box, IconButton, Menu, MenuItem, Button } from '@mui/material';
import GroupsIcon from '@mui/icons-material/Groups';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import DownloadIcon from '@mui/icons-material/Download';
import PauseCircleIcon from '@mui/icons-material/PauseCircle';
import PlayCircleIcon from '@mui/icons-material/PlayCircle';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import type { ExecutionStatus, GamePhase, WinningCamp } from '../../store/types';
import { useTranslation } from 'react-i18next';
import { useLabels } from '../../i18n/useLabels';
import type { LabelsModule } from '../../i18n/useLabels';
import { gameExportUrl } from '../../api/client';
import { HAIRLINE } from '../../theme/tokens';

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

// 胜负徽标只在文案之外多一个色调，色调留在组件里（与 CAMP_ACCENT 同类）。
const WINNER_COLOR: Record<WinningCamp, 'success' | 'error' | 'info'> = {
  good: 'success',
  werewolf: 'error',
  third_party: 'info',
};

// 未知阵营（旧档脏数据）不渲染徽标，与旧行为一致。
function winnerMeta(winner: string, L: LabelsModule): { label: string; color: 'success' | 'error' | 'info' } | null {
  if (!(winner in L.WINNER_LABELS)) return null;
  const camp = winner as WinningCamp;
  return { label: L.WINNER_LABELS[camp], color: WINNER_COLOR[camp] };
}

export default function GameCard({
  gameId, name, phase, roundNumber, playerCount, aliveCount, winner, onClick, onRename, onDelete,
  executionStatus, recoverable = false, recoveryBlockCode, controlBusy = false,
  onPause, onResume, onRecover, onMove, selected = false, onToggleSelect,
}: Props) {
  const { t } = useTranslation();
  const L = useLabels();
  const win = winner ? winnerMeta(winner, L) : null;
  const [menuEl, setMenuEl] = useState<null | HTMLElement>(null);

  return (
    <Card
      variant="outlined"
      sx={{
        transition: 'background-color 0.2s, border-color 0.2s',
        border: `1px solid ${HAIRLINE.strong}`,
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
              slotProps={{ input: { 'aria-label': t('lobby.selectGame', { name }) } }}
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
                  {/* phase 由调用方（GameList）先查表，未解析的机器值在这里兜底 */}
                  {t('lobby.phaseAndRound', { phase: L.PHASE_LABELS[phase as GamePhase] ?? phase, round: roundNumber })}
                </Typography>
              </Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                {executionStatus && (
                  <Chip
                    label={L.EXECUTION_LABELS[executionStatus]}
                    size="small"
                    color={
                      executionStatus === 'failed' || executionStatus === 'recovery_blocked'
                        ? 'error'
                        : executionStatus === 'interrupted' || executionStatus === 'paused'
                        ? 'warning'
                        : 'default'
                    }
                    variant="outlined"
                    title={L.recoveryBlockLabel(recoveryBlockCode) ?? undefined}
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
        <Box sx={{ display: 'flex', alignItems: 'center', pr: 1, gap: 0.5 }}>
          {(executionStatus === 'interrupted'
            || executionStatus === 'failed'
            || executionStatus === 'recovery_blocked') && onRecover && (
            <Button
              variant="outlined"
              size="small"
              color={executionStatus === 'recovery_blocked' || executionStatus === 'failed' ? 'error' : 'warning'}
              disabled={controlBusy || !recoverable}
              title={!recoverable ? L.recoveryBlockLabel(recoveryBlockCode) ?? t('game.recoverImpossible') : undefined}
              onClick={(event) => {
                event.stopPropagation();
                onRecover();
              }}
              startIcon={<RestartAltIcon fontSize="small" />}
              sx={{ py: 0.25, px: 1, minWidth: 0, fontSize: '0.75rem', whiteSpace: 'nowrap' }}
            >
              {executionStatus === 'recovery_blocked' ? t('game.retryRecover') : t('game.recoverGame')}
            </Button>
          )}
          <IconButton
            aria-label={t('lobby.gameActions')}
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
                <PauseCircleIcon fontSize="small" sx={{ mr: 1 }} />{t('lobby.pauseExecution')}
              </MenuItem>
            )}
            {executionStatus === 'paused' && onResume && (
              <MenuItem disabled={controlBusy} onClick={() => { setMenuEl(null); onResume(); }}>
                <PlayCircleIcon fontSize="small" sx={{ mr: 1 }} />{t('lobby.resumeExecution')}
              </MenuItem>
            )}
            {(executionStatus === 'interrupted'
              || executionStatus === 'failed'
              || executionStatus === 'recovery_blocked') && onRecover && (
              <MenuItem
                disabled={controlBusy || !recoverable}
                title={!recoverable ? L.recoveryBlockLabel(recoveryBlockCode) ?? t('game.recoverImpossible') : undefined}
                onClick={() => { setMenuEl(null); onRecover(); }}
              >
                <RestartAltIcon fontSize="small" sx={{ mr: 1 }} />
                {executionStatus === 'recovery_blocked' ? t('game.retryRecover') : t('game.recoverGame')}
              </MenuItem>
            )}
            <MenuItem onClick={() => { setMenuEl(null); onRename(); }}>{t('lobby.rename')}</MenuItem>
            {onMove && (
              <MenuItem onClick={() => { setMenuEl(null); onMove(); }}>{t('lobby.moveToFolder')}</MenuItem>
            )}
            {/* attachment 响应头让浏览器直接下载，不离开当前页 */}
            <MenuItem
              component="a"
              href={gameExportUrl(gameId)}
              onClick={() => setMenuEl(null)}
            >
              <DownloadIcon fontSize="small" sx={{ mr: 1 }} />{t('common.exportReplay')}
            </MenuItem>
            <MenuItem onClick={() => { setMenuEl(null); onDelete(); }}>{t('lobby.delete')}</MenuItem>
          </Menu>
        </Box>
      </Box>
    </Card>
  );
}
