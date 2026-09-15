import { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Typography, Button, Chip, CircularProgress } from '@mui/material';
import VisibilityIcon from '@mui/icons-material/Visibility';
import { useGameStore } from '../../store/gameStore';
import {
  AudienceApiError, fetchAudienceEvents, fetchAudienceSnapshot,
  fetchGameLogs, fetchGameDetail, GameNotFoundError,
} from '../../api/client';
import { useWebSocket } from '../../api/websocket';
import TimelineController from './TimelineController';
import SeatMap from './SeatMap';
import CenterDisplay from './CenterDisplay';
import HistoryPanel from './HistoryPanel';
import WinOverlay from './WinOverlay';
import ActivityCard from './ActivityCard';

interface Props {
  onBack: () => void;
  gameId: string;
}

async function fetchAudienceHistory(gameId: string, throughSeq: number) {
  if (throughSeq <= 0) return [];
  const events = [] as Awaited<ReturnType<typeof fetchAudienceEvents>>['events'];
  let cursor = 0;
  while (cursor < throughSeq) {
    const page = await fetchAudienceEvents(gameId, cursor, { limit: 100, throughSeq });
    events.push(...page.events);
    if (page.last_seq <= cursor) break;
    cursor = page.last_seq;
    if (page.caught_up) break;
  }
  return events;
}

export default function GameBoard({ onBack, gameId }: Props) {
  const { connect, disconnect } = useWebSocket();
  const {
    players, phase, roundNumber, winResult, showWinOverlay, revealOnDeath,
    showHistory, currentSpeaker, modelSnapshot,
    initPlayersFromDetail, loadLogs, mergeLogs, toggleHistory, timeline, timelineIndex,
    loadAudienceSnapshot, loadAudienceHistory, mergeAudienceEvents, reset,
    syncMode, streamError, executionStatus,
  } = useGameStore();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [syncRevision, setSyncRevision] = useState(0);
  const resync = useCallback(() => setSyncRevision((revision) => revision + 1), []);

  useEffect(() => {
    reset();
    connect(gameId, resync);
    return disconnect;
  }, [connect, disconnect, gameId, reset, resync, syncRevision]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        setLoading(true);
        setError(null);
        try {
          const snapshot = await fetchAudienceSnapshot(gameId);
          if (cancelled) return;
          loadAudienceSnapshot(snapshot);
          const events = await fetchAudienceHistory(gameId, snapshot.seq);
          if (cancelled) return;
          loadAudienceHistory(gameId, events);
          const requestedSeq = Number(new URLSearchParams(window.location.search).get('seq'));
          if (Number.isInteger(requestedSeq) && requestedSeq > 0) {
            const index = useGameStore.getState().timeline.findIndex(
              (event) => event.seq !== undefined && event.seq >= requestedSeq,
            );
            if (index >= 0) useGameStore.getState().seekTo(index);
          }
          setLoading(false);
          return;
        } catch (error) {
          if (!(error instanceof AudienceApiError)
            || ![404, 409, 410, 501].includes(error.status)) throw error;
        }

        // Old archives do not have an audience projection. Keep their existing
        // detail + log replay path without advertising recovery.
        const detail = await fetchGameDetail(gameId);
        if (cancelled) return;
        initPlayersFromDetail(
          detail.players,
          detail.reveal_on_death ?? false,
          detail.model_snapshot ?? [],
        );
        const logs = await fetchGameLogs(gameId);
        if (cancelled) return;
        loadLogs(logs);
        const t = useGameStore.getState().timeline;
        if (t.length > 0) useGameStore.getState().seekTo(t.length - 1);
        setLoading(false);
      } catch (error: unknown) {
        if (!cancelled) {
          setError(error instanceof Error ? error.message : 'Failed to load game logs');
          setLoading(false);
        }
      }
    }
    load();
    return () => { cancelled = true; };
  }, [
    gameId, initPlayersFromDetail, loadAudienceHistory, loadAudienceSnapshot,
    loadLogs, syncRevision,
  ]);

  // Poll for new logs while game is in progress
  useEffect(() => {
    if (loading || winResult || error) return;

    let active = true;
    let inFlight = false;

    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        if (syncMode === 'incremental') {
          const cursor = useGameStore.getState().audienceCursor;
          const page = await fetchAudienceEvents(gameId, cursor, { limit: 100 });
          if (active) mergeAudienceEvents(page);
          return;
        }
        const [detailResult, logsResult] = await Promise.allSettled([
          fetchGameDetail(gameId),
          fetchGameLogs(gameId),
        ]);
        let failure: unknown = null;
        if (detailResult.status === 'rejected') {
          failure = detailResult.reason;
        } else if (logsResult.status === 'rejected') {
          failure = logsResult.reason;
        }
        if (failure !== null) {
          // A 404 means the game disappeared (e.g. server restarted);
          // other failures are transient and silently retried.
          if (active && failure instanceof GameNotFoundError) {
            setError(failure.message);
          }
          return;
        }
        if (active
          && detailResult.status === 'fulfilled'
          && logsResult.status === 'fulfilled') {
          initPlayersFromDetail(
            detailResult.value.players,
            detailResult.value.reveal_on_death ?? false,
            detailResult.value.model_snapshot ?? [],
          );
          mergeLogs(logsResult.value);
        }
      } catch {
        // Silently ignore poll errors
      } finally {
        inFlight = false;
      }
    };

    const interval = setInterval(poll, 3000);

    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [
    error, gameId, initPlayersFromDetail, loading, mergeAudienceEvents,
    mergeLogs, syncMode, winResult,
  ]);

  const aliveCount = Object.values(players).filter((p) => p.is_alive).length;
  const totalPlayers = Object.keys(players).length;

  const voteTargets: Record<number, number | null> = {};
  if (phase === 'vote_casting' || phase === 'vote_resolution') {
    for (const event of timeline.slice(0, timelineIndex + 1)) {
      if (event.event_type === 'vote' && event.payload.round_number === roundNumber) {
        voteTargets[event.payload.voter_seat] = event.payload.target_seat;
      }
    }
  }

  if (loading) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: 1, gap: 2 }}>
        <CircularProgress size={28} />
        <Typography variant="body2" color="text.secondary">加载对局记录中…</Typography>
      </Box>
    );
  }

  if (error) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: 1, gap: 2 }}>
        <Typography variant="body2" color="error">{error}</Typography>
        <Button onClick={onBack} variant="outlined" size="small">返回</Button>
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {streamError && <Alert severity="warning">{streamError}</Alert>}
      <TimelineController />

      {/* 游戏信息条：存活统计 / 对局编号 / 视角标识 + 操作 */}
      <Box sx={{
        display: 'flex', alignItems: 'center', gap: 2,
        flexWrap: { xs: 'wrap', md: 'nowrap' },
        px: 2.5, py: 0.9, flexShrink: 0,
        borderBottom: '1px solid', borderColor: 'divider',
        bgcolor: 'rgba(23,18,33,0.5)',
      }}>
        <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 0.6 }}>
          <Typography variant="caption" color="text.disabled" sx={{ letterSpacing: 2 }}>存活</Typography>
          <Typography sx={{ color: 'secondary.main', fontWeight: 800, fontSize: '0.9rem', lineHeight: 1 }}>{aliveCount}</Typography>
          <Typography variant="caption" color="text.disabled">/ {totalPlayers}</Typography>
        </Box>
        <Box aria-hidden="true" sx={{ width: '1px', height: 14, bgcolor: 'divider' }} />
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ letterSpacing: 2, fontFamily: '"Cinzel","Noto Serif SC",serif' }}
        >
          # {gameId.slice(0, 8)}
        </Typography>
        <Box sx={{ ml: { xs: 0, md: 'auto' }, width: { xs: '100%', md: 'auto' }, display: 'flex', flexWrap: 'wrap', gap: 1, alignItems: 'center' }}>
          {executionStatus && (
            <Chip
              label={`执行：${executionStatus}`}
              size="small"
              color={executionStatus === 'failed' || executionStatus === 'recovery_blocked' ? 'error' : 'default'}
              variant="outlined"
            />
          )}
          <Chip
            icon={<VisibilityIcon sx={{ fontSize: 14 }} />}
            label="上帝视角"
            size="small"
            variant="outlined"
            sx={{ '& .MuiChip-label': { letterSpacing: 1 } }}
          />
          <Button
            size="small"
            variant={showHistory ? 'contained' : 'outlined'}
            onClick={toggleHistory}
            disableElevation
          >
            {showHistory ? '隐藏记录' : '历史记录'}
          </Button>
          <Button size="small" onClick={onBack} variant="outlined">
            返回
          </Button>
        </Box>
      </Box>

      <Box sx={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
        <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}>
          <Box sx={{ flex: 1, position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center', minWidth: 0, minHeight: 160 }}>
            <SeatMap
              players={players}
              currentSpeaker={currentSpeaker}
              voteTargets={voteTargets}
              modelSnapshot={modelSnapshot}
            >
              <CenterDisplay />
            </SeatMap>
          </Box>
          <ActivityCard />
        </Box>

        {showHistory && <HistoryPanel onClose={toggleHistory} />}
      </Box>

      {showWinOverlay && winResult && (
        <WinOverlay winResult={winResult} revealOnDeath={revealOnDeath} />
      )}
    </Box>
  );
}
