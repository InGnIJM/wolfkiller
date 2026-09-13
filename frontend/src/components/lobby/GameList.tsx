import { useCallback, useEffect, useState } from 'react';
import {
  Alert, Box, Button, Container, Dialog, DialogActions, DialogContent,
  DialogTitle, Stack, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import GameCard from './GameCard';
import { controlGame, deleteGame, listGames, renameGame } from '../../api/client';
import type { GameListItem } from '../../store/types';

interface Props {
  onJoinGame: (gameId: string) => void;
  onCreateClick: () => void;
}

const PHASE_LABELS: Record<string, string> = {
  waiting: '等待中',
  role_deal: '分配角色',
  night: '黑夜',
  dawn: '天亮',
  last_words: '遗言',
  speech: '发言',
  vote_casting: '投票',
  vote_resolution: '公布结果',
  game_over: '已结束',
};

export default function GameList({ onJoinGame, onCreateClick }: Props) {
  const [games, setGames] = useState<GameListItem[]>([]);
  const [renameTarget, setRenameTarget] = useState<GameListItem | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameError, setRenameError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<GameListItem | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [controlError, setControlError] = useState('');
  const [controlBusyId, setControlBusyId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const res = await listGames();
    setGames(res.games);
  }, []);

  useEffect(() => {
    let active = true;
    const refreshWhileMounted = async () => {
      try {
        const res = await listGames();
        if (active) setGames(res.games);
      } catch (e) {
        if (active) console.error('Failed to list games:', e);
      }
    };

    void Promise.resolve().then(refreshWhileMounted);
    const t = setInterval(() => void refreshWhileMounted(), 3000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, []);

  const runControl = async (
    gameId: string,
    action: 'pause' | 'resume' | 'recover',
  ) => {
    setControlBusyId(gameId);
    setControlError('');
    try {
      const updated = await controlGame(gameId, action);
      if (Object.keys(updated).length > 0) {
        setGames((current) => current.map((game) => (
          game.game_id === gameId ? { ...game, ...updated } : game
        )));
      } else {
        await refresh();
      }
    } catch (error) {
      setControlError(error instanceof Error ? error.message : String(error));
    } finally {
      setControlBusyId(null);
    }
  };

  const openRename = (g: GameListItem) => {
    setRenameTarget(g);
    setRenameValue(g.name);
    setRenameError('');
  };

  const submitRename = async () => {
    if (renameTarget === null) return;
    const name = renameValue.trim();
    if (!name) {
      setRenameError('名称不能为空');
      return;
    }
    try {
      const updated = await renameGame(renameTarget.game_id, name);
      setGames((prev) => prev.map((item) => (
        item.game_id === updated.game_id ? { ...item, ...updated } : item
      )));
      setRenameTarget(null);
    } catch (error) {
      setRenameError(error instanceof Error ? error.message : String(error));
    }
  };

  const submitDelete = async () => {
    if (deleteTarget === null) return;
    try {
      await deleteGame(deleteTarget.game_id);
      setGames((prev) => prev.filter((item) => item.game_id !== deleteTarget.game_id));
      setDeleteTarget(null);
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : String(error));
    }
  };

  const inProgress = deleteTarget !== null
    && deleteTarget.phase !== 'game_over'
    && deleteTarget.phase !== 'error';

  return (
    <Container maxWidth="sm" sx={{ py: { xs: 2, sm: 4 }, px: { xs: 2, sm: 3 } }}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, justifyContent: 'space-between', alignItems: 'center', mb: 4 }}>
        <Box>
          <Typography
            variant="caption"
            sx={{ display: 'block', mb: 0.3, fontWeight: 600, fontSize: '0.65rem', letterSpacing: 5, color: 'secondary.dark' }}
          >
            WOLF KILLER · 血月剧场
          </Typography>
          <Typography variant="h4" sx={{ fontWeight: 400, fontSize: '2rem', letterSpacing: '-0.5px' }}>
            狼人杀
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            选择一个对局加入，或创建新游戏
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={onCreateClick}
          disableElevation
        >
          创建游戏
        </Button>
      </Box>

      {controlError && (
        <Alert severity="error" onClose={() => setControlError('')} sx={{ mb: 2 }}>
          {controlError}
        </Alert>
      )}

      {games.length === 0 && (
        <Box sx={{ textAlign: 'center', py: 8 }}>
          <Typography variant="h6" color="text.disabled" sx={{ fontWeight: 400, mb: 1 }}>
            暂无对局
          </Typography>
          <Typography variant="body2" color="text.disabled">
            点击「创建游戏」开始一局新的狼人杀
          </Typography>
        </Box>
      )}

      <Stack spacing={1.5}>
        {games.map((g) => (
          <GameCard
            key={g.game_id}
            gameId={g.game_id}
            name={g.name}
            phase={PHASE_LABELS[g.phase] || g.phase}
            roundNumber={g.round_number}
            playerCount={g.player_count}
            aliveCount={g.alive_count}
            winner={g.winner}
            executionStatus={g.execution_status}
            recoverable={g.recoverable}
            recoveryBlockCode={g.recovery_block_code}
            controlBusy={controlBusyId === g.game_id}
            onClick={() => onJoinGame(g.game_id)}
            onRename={() => openRename(g)}
            onDelete={() => { setDeleteTarget(g); setDeleteError(''); }}
            onPause={() => void runControl(g.game_id, 'pause')}
            onResume={() => void runControl(g.game_id, 'resume')}
            onRecover={() => void runControl(g.game_id, 'recover')}
          />
        ))}
      </Stack>

      <Dialog open={renameTarget !== null} onClose={() => setRenameTarget(null)}>
        <DialogTitle>重命名对局</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="对局名称"
            fullWidth
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
          />
          {renameError && <Alert severity="error" sx={{ mt: 1 }}>{renameError}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameTarget(null)}>取消</Button>
          <Button onClick={() => void submitRename()}>确定</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={deleteTarget !== null} onClose={() => setDeleteTarget(null)}>
        <DialogTitle>删除对局</DialogTitle>
        <DialogContent>
          <Typography>
            确定删除「{deleteTarget?.name}」？此操作不可恢复。
          </Typography>
          {inProgress && (
            <Typography color="warning.main" sx={{ mt: 1 }}>
              对局正在进行，删除将立即中断。
            </Typography>
          )}
          {deleteError && <Alert severity="error" sx={{ mt: 1 }}>{deleteError}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>取消</Button>
          <Button color="error" onClick={() => void submitDelete()}>删除</Button>
        </DialogActions>
      </Dialog>
    </Container>
  );
}
