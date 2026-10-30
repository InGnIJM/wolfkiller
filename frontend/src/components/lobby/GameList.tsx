import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Container, Dialog, DialogActions, DialogContent,
  DialogTitle, MenuItem, Pagination, Stack, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import GameCard from './GameCard';
import PageBackground from '../shared/PageBackground';
import { BACKDROP } from '../../theme/tokens';
import FolderRail, { type FolderFilter } from './FolderRail';
import BatchActionBar from './BatchActionBar';
import {
  assignGameFolder, batchDeleteGames, batchMoveGames, controlGame, createFolder,
  deleteGame, GameControlError, listFolders, listGames, renameGame,
} from '../../api/client';
import type { GameFolder, GameListItem } from '../../store/types';
import { useTranslation } from 'react-i18next';
import { useLabels } from '../../i18n/useLabels';

interface Props {
  onJoinGame: (gameId: string) => void;
  onCreateClick: () => void;
}

const PAGE_SIZE = 10;

function matchesFolder(game: GameListItem, filter: FolderFilter): boolean {
  if (filter === 'all') return true;
  if (filter === 'unfiled') return !game.folder_id;
  return game.folder_id === filter;
}

export default function GameList({ onJoinGame, onCreateClick }: Props) {
  const { t } = useTranslation();
  const L = useLabels();
  const [games, setGames] = useState<GameListItem[]>([]);
  const [folders, setFolders] = useState<GameFolder[]>([]);
  const [folderFilter, setFolderFilter] = useState<FolderFilter>('all');
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [folderDraft, setFolderDraft] = useState('');
  const [folderError, setFolderError] = useState('');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [renameTarget, setRenameTarget] = useState<GameListItem | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameError, setRenameError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<GameListItem | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false);
  const [batchDeleteError, setBatchDeleteError] = useState('');
  const [moveIds, setMoveIds] = useState<string[]>([]);
  const [moveFolderId, setMoveFolderId] = useState('');
  const [moveError, setMoveError] = useState('');
  const [controlError, setControlError] = useState('');
  const [controlBusyId, setControlBusyId] = useState<string | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<
    { gameId: string; action: 'pause' | 'resume' | 'recover'; code: string } | null
  >(null);

  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  const refresh = useCallback(async () => {
    const [gamesRes, foldersRes] = await Promise.all([
      listGames({ page, pageSize: PAGE_SIZE, folderId: folderFilter }),
      listFolders(),
    ]);
    setGames(gamesRes.games);
    const nextTotal = gamesRes.total ?? gamesRes.games.length;
    setTotal(nextTotal);
    setFolders(foldersRes.folders);
    const maxPages = Math.max(1, Math.ceil(nextTotal / PAGE_SIZE));
    if (page > maxPages) {
      setPage(maxPages);
    }
  }, [page, folderFilter]);

  useEffect(() => {
    let active = true;
    const refreshWhileMounted = async () => {
      try {
        const [gamesRes, foldersRes] = await Promise.all([
          listGames({ page, pageSize: PAGE_SIZE, folderId: folderFilter }),
          listFolders(),
        ]);
        if (active) {
          setGames(gamesRes.games);
          const nextTotal = gamesRes.total ?? gamesRes.games.length;
          setTotal(nextTotal);
          setFolders(foldersRes.folders);
          const maxPages = Math.max(1, Math.ceil(nextTotal / PAGE_SIZE));
          if (page > maxPages) {
            setPage(maxPages);
          }
        }
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
  }, [page, folderFilter]);

  const visibleGames = useMemo(
    () => games.filter((game) => matchesFolder(game, folderFilter)),
    [folderFilter, games],
  );

  const totalCount = total > 0 ? total : visibleGames.length;
  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);

  const runControl = async (
    gameId: string,
    action: 'pause' | 'resume' | 'recover',
    options: { force?: boolean } = {},
  ) => {
    setControlBusyId(gameId);
    setControlError('');
    try {
      const updated = await controlGame(gameId, action, options);
      setConfirmTarget(null);
      if (Object.keys(updated).length > 0) {
        setGames((current) => current.map((game) => (
          game.game_id === gameId ? { ...game, ...updated } : game
        )));
      } else {
        await refresh();
      }
    } catch (error) {
      // A game that was interrupted under older role declarations is resumable,
      // but continuing changes the rules it plays by — so ask, never assume.
      if (error instanceof GameControlError && error.confirmationRequired) {
        setConfirmTarget({ gameId, action, code: error.code });
      } else {
        setControlError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      setControlBusyId(null);
    }
  };

  const submitConfirm = async () => {
    if (confirmTarget === null) return;
    await runControl(confirmTarget.gameId, confirmTarget.action, { force: true });
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
      setRenameError(t('lobby.nameRequired'));
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
      setTotal((prev) => Math.max(0, prev - 1));
      setDeleteTarget(null);
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : String(error));
    }
  };

  const submitCreateFolder = async () => {
    const name = folderDraft.trim();
    if (!name) {
      setFolderError(t('lobby.nameRequired'));
      return;
    }
    try {
      const folder = await createFolder(name);
      setFolders((current) => [...current, folder]);
      setCreatingFolder(false);
      setFolderDraft('');
      setFolderError('');
    } catch (error) {
      setFolderError(error instanceof Error ? error.message : String(error));
    }
  };

  const toggleSelected = (gameId: string) => {
    setSelectedIds((current) => (
      current.includes(gameId)
        ? current.filter((id) => id !== gameId)
        : [...current, gameId]
    ));
  };

  const submitMove = async () => {
    if (moveIds.length === 0) return;
    const folderId = moveFolderId === '' ? null : moveFolderId;
    try {
      if (moveIds.length === 1) {
        await assignGameFolder(moveIds[0], folderId);
        setGames((current) => current.map((game) => (
          game.game_id === moveIds[0] ? { ...game, folder_id: folderId } : game
        )));
      } else {
        const result = await batchMoveGames(moveIds, folderId);
        const moved = new Set(result.moved);
        setGames((current) => current.map((game) => (
          moved.has(game.game_id) ? { ...game, folder_id: folderId } : game
        )));
        if (result.failed.length > 0) {
          setMoveError(result.failed.map((item) => item.message).join(t('game.listJoin')));
          return;
        }
      }
      setMoveIds([]);
      setSelectedIds([]);
      await refresh();
    } catch (error) {
      setMoveError(error instanceof Error ? error.message : String(error));
    }
  };

  const submitBatchDelete = async () => {
    if (selectedIds.length === 0) return;
    try {
      const result = await batchDeleteGames(selectedIds);
      const deleted = new Set(result.deleted);
      setGames((current) => current.filter((game) => !deleted.has(game.game_id)));
      setSelectedIds((current) => current.filter((id) => !deleted.has(id)));
      setTotal((prev) => Math.max(0, prev - deleted.size));
      if (result.failed.length > 0) {
        setBatchDeleteError(result.failed.map((item) => item.message).join(t('game.listJoin')));
        return;
      }
      setBatchDeleteOpen(false);
    } catch (error) {
      setBatchDeleteError(error instanceof Error ? error.message : String(error));
    }
  };

  const inProgress = deleteTarget !== null
    && deleteTarget.phase !== 'game_over'
    && deleteTarget.phase !== 'error';

  return (
    <Box sx={{ position: 'relative', flex: 1, minHeight: 0 }}>
    <PageBackground variant="lobby" placement="contained" />
    <Container maxWidth="sm" sx={{
      position: 'relative', zIndex: 1,
      py: { xs: 2, sm: 4 }, px: { xs: 2, sm: 3 },
      bgcolor: BACKDROP.textPlate, backdropFilter: 'blur(12px)', borderRadius: 2,
    }}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, justifyContent: 'space-between', alignItems: 'center', mb: 4 }}>
        <Box sx={{
          bgcolor: BACKDROP.textPlate,
          backdropFilter: 'blur(12px)',
          borderRadius: 2,
          px: 1.5,
          py: 1,
        }}>
          <Typography
            variant="caption"
            sx={{ display: 'block', mb: 0.3, fontWeight: 600, fontSize: '0.65rem', letterSpacing: 5, color: 'secondary.dark' }}
          >
            {t('lobby.titleLine')}
          </Typography>
          <Typography variant="h4" sx={{ fontWeight: 400, fontSize: '2rem', letterSpacing: '-0.5px' }}>
            {t('lobby.subtitle')}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {t('lobby.subtitleHint')}
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={onCreateClick}
          disableElevation
        >
          {t('lobby.createGame')}
        </Button>
      </Box>

      <FolderRail
        folders={folders}
        filter={folderFilter}
        creating={creatingFolder}
        draftName={folderDraft}
        onFilter={(filter) => {
          setFolderFilter(filter);
          setPage(1);
        }}
        onStartCreate={() => { setCreatingFolder(true); setFolderError(''); }}
        onDraftName={setFolderDraft}
        onCreate={() => void submitCreateFolder()}
        onCancelCreate={() => { setCreatingFolder(false); setFolderDraft(''); setFolderError(''); }}
      />
      {folderError && <Alert severity="error" sx={{ mb: 2 }}>{folderError}</Alert>}

      {controlError && (
        <Alert severity="error" onClose={() => setControlError('')} sx={{ mb: 2 }}>
          {controlError}
        </Alert>
      )}

      {visibleGames.length === 0 && (
        <Box sx={{
          textAlign: 'center', py: 8,
          bgcolor: BACKDROP.textPlate,
          backdropFilter: 'blur(12px)',
          borderRadius: 2,
        }}>
          <Typography variant="h6" color="text.disabled" sx={{ fontWeight: 400, mb: 1 }}>
            {t('lobby.emptyGames')}
          </Typography>
          <Typography variant="body2" color="text.disabled">
            {t('lobby.emptyGamesHint')}
          </Typography>
        </Box>
      )}

      <Stack spacing={1.5}>
        {visibleGames.map((g) => (
          <GameCard
            key={g.game_id}
            gameId={g.game_id}
            name={g.name}
            phase={L.PHASE_LABELS[g.phase] || g.phase}
            roundNumber={g.round_number}
            playerCount={g.player_count}
            aliveCount={g.alive_count}
            winner={g.winner}
            executionStatus={g.execution_status}
            recoverable={g.recoverable}
            recoveryBlockCode={g.recovery_block_code}
            controlBusy={controlBusyId === g.game_id}
            selected={selectedIds.includes(g.game_id)}
            onToggleSelect={() => toggleSelected(g.game_id)}
            onClick={() => onJoinGame(g.game_id)}
            onRename={() => openRename(g)}
            onMove={() => { setMoveIds([g.game_id]); setMoveFolderId(g.folder_id ?? ''); setMoveError(''); }}
            onDelete={() => { setDeleteTarget(g); setDeleteError(''); }}
            onPause={() => void runControl(g.game_id, 'pause')}
            onResume={() => void runControl(g.game_id, 'resume')}
            onRecover={() => void runControl(g.game_id, 'recover')}
          />
        ))}
      </Stack>

      {totalPages > 1 && (
        <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3, mb: 1 }}>
          <Pagination
            count={totalPages}
            page={currentPage}
            onChange={(_, newPage) => setPage(newPage)}
            color="secondary"
            shape="rounded"
            size="medium"
          />
        </Box>
      )}

      <BatchActionBar
        selectedCount={selectedIds.length}
        onMove={() => { setMoveIds(selectedIds); setMoveFolderId(''); setMoveError(''); }}
        onDelete={() => { setBatchDeleteOpen(true); setBatchDeleteError(''); }}
        onClear={() => setSelectedIds([])}
      />

      <Dialog open={renameTarget !== null} onClose={() => setRenameTarget(null)}>
        <DialogTitle>{t('lobby.renameTitle')}</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label={t('lobby.gameName')}
            fullWidth
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
          />
          {renameError && <Alert severity="error" sx={{ mt: 1 }}>{renameError}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameTarget(null)}>{t('common.cancel')}</Button>
          <Button onClick={() => void submitRename()}>{t('common.confirm')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={deleteTarget !== null} onClose={() => setDeleteTarget(null)}>
        <DialogTitle>{t('lobby.deleteTitle')}</DialogTitle>
        <DialogContent>
          <Typography>
            {t('lobby.deleteConfirm', { name: deleteTarget?.name })}
          </Typography>
          {inProgress && (
            <Typography color="warning.main" sx={{ mt: 1 }}>
              {t('lobby.deleteRunningHint')}
            </Typography>
          )}
          {deleteError && <Alert severity="error" sx={{ mt: 1 }}>{deleteError}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>{t('common.cancel')}</Button>
          <Button color="error" onClick={() => void submitDelete()}>{t('common.delete')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={batchDeleteOpen} onClose={() => setBatchDeleteOpen(false)}>
        <DialogTitle>{t('lobby.batchDeleteTitle')}</DialogTitle>
        <DialogContent>
          <Typography>{t('lobby.batchDeleteConfirm', { count: selectedIds.length })}</Typography>
          {batchDeleteError && <Alert severity="error" sx={{ mt: 1 }}>{batchDeleteError}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setBatchDeleteOpen(false)}>{t('common.cancel')}</Button>
          <Button color="error" onClick={() => void submitBatchDelete()}>{t('common.delete')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={confirmTarget !== null} onClose={() => setConfirmTarget(null)}>
        <DialogTitle>{t('game.roleDriftTitle')}</DialogTitle>
        <DialogContent>
          <Typography>
            {t('game.roleDriftBody')}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {t('game.reasonCode', { code: confirmTarget?.code })}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmTarget(null)}>{t('common.cancel')}</Button>
          <Button onClick={() => void submitConfirm()}>{t('game.continueGame')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={moveIds.length > 0} onClose={() => setMoveIds([])}>
        <DialogTitle>{t('lobby.moveTitle')}</DialogTitle>
        <DialogContent>
          <TextField
            select
            fullWidth
            margin="dense"
            label={t('lobby.folder')}
            value={moveFolderId}
            onChange={(event) => setMoveFolderId(event.target.value)}
          >
            <MenuItem value="">{t('lobby.uncategorized')}</MenuItem>
            {folders.map((folder) => (
              <MenuItem key={folder.folder_id} value={folder.folder_id}>{folder.name}</MenuItem>
            ))}
          </TextField>
          {moveError && <Alert severity="error" sx={{ mt: 1 }}>{moveError}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMoveIds([])}>{t('common.cancel')}</Button>
          <Button onClick={() => void submitMove()}>{t('common.confirm')}</Button>
        </DialogActions>
      </Dialog>
    </Container>
    </Box>
  );
}
