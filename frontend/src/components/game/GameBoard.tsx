import { useEffect, useState } from 'react';
import { Box, Typography, Button, CircularProgress } from '@mui/material';
import { useGameStore } from '../../store/gameStore';
import { fetchGameLogs, fetchGameDetail, GameNotFoundError } from '../../api/client';
import { useWebSocket } from '../../api/websocket';
import TimelineController from './TimelineController';
import SeatMap from './SeatMap';
import CenterDisplay from './CenterDisplay';
import HistoryPanel from './HistoryPanel';
import WinOverlay from './WinOverlay';

interface Props {
  onBack: () => void;
  gameId: string;
}

export default function GameBoard({ onBack, gameId }: Props) {
  const { connect, disconnect } = useWebSocket();
  const {
    players, phase, roundNumber, winResult, showWinOverlay,
    showHistory, currentSpeaker,
    initPlayersFromDetail, loadLogs, mergeLogs, toggleHistory, timeline, timelineIndex,
  } = useGameStore();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    connect(gameId);
    return disconnect;
  }, [connect, disconnect, gameId]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        setLoading(true);
        // Fetch game detail first to get all player seats
        const detail = await fetchGameDetail(gameId);
        if (!cancelled) {
          initPlayersFromDetail(detail.players);
        }
        // Then fetch logs
        const logs = await fetchGameLogs(gameId);
        if (!cancelled) {
          loadLogs(logs);
          const t = useGameStore.getState().timeline;
          if (t.length > 0) {
            useGameStore.getState().seekTo(t.length - 1);
          }
          setLoading(false);
        }
      } catch (error: unknown) {
        if (!cancelled) {
          setError(error instanceof Error ? error.message : 'Failed to load game logs');
          setLoading(false);
        }
      }
    }
    load();
    return () => { cancelled = true; };
  }, [gameId, loadLogs, initPlayersFromDetail]);

  // Poll for new logs while game is in progress
  useEffect(() => {
    if (loading || winResult || error) return;

    let active = true;
    let inFlight = false;

    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
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
          initPlayersFromDetail(detailResult.value.players);
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
  }, [gameId, loading, winResult, error, initPlayersFromDetail, mergeLogs]);

  const aliveCount = Object.values(players).filter((p) => p.is_alive).length;

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
      <TimelineController />

      <Box sx={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        px: 2, py: 0.8, flexShrink: 0,
        borderBottom: '1px solid', borderColor: 'divider',
      }}>
        <Typography variant="body2" color="text.secondary">
          存活 {aliveCount}/{Object.keys(players).length} · #{gameId.slice(0, 8)}
        </Typography>
        <Box sx={{ display: 'flex', gap: 0.8 }}>
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
        <Box sx={{ flex: 1, position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center', minWidth: 0, minHeight: 0 }}>
          <SeatMap
            players={players}
            currentSpeaker={currentSpeaker}
            voteTargets={voteTargets}
          >
            <CenterDisplay />
          </SeatMap>
        </Box>

        {showHistory && <HistoryPanel onClose={toggleHistory} />}
      </Box>

      {showWinOverlay && winResult && <WinOverlay winResult={winResult} />}
    </Box>
  );
}
