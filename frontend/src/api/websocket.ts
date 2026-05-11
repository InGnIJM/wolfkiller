import { useEffect, useRef, useCallback } from 'react';
import { useGameStore } from '../store/gameStore';
import { getWsUrl } from '../api/client';
import { WSMessage } from '../store/types';

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const {
    setGameState, setPhase, addSpeech, addVote,
    setWinResult, setConnected, setNightSubstep, setPaused, reset,
  } = useGameStore();

  const connect = useCallback((gameId: string) => {
    const url = getWsUrl(gameId);
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);

    ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data);
        switch (msg.type) {
          case 'game_state':
          case 'phase_change':
            if (msg.state) {
              setGameState(msg.state);
            }
            if (msg.phase) {
              setPhase(msg.phase, msg.round_number || 0);
            }
            break;
          case 'speech':
            if (msg.speech) addSpeech(msg.speech);
            break;
          case 'vote_cast':
            if (msg.vote) addVote(msg.vote);
            break;
          case 'game_over':
            if (msg.win_result) setWinResult(msg.win_result);
            break;
          case 'night_substep':
            setNightSubstep({
              step: msg.step || '',
              highlightSeats: msg.highlight_seats || [],
              actionSeat: msg.action_seat ?? null,
              action: msg.action || null,
              wolfKillTarget: msg.wolf_kill_target ?? null,
              roundNumber: msg.round_number || 0,
            });
            break;
          case 'player_died':
            if (msg.death) {
              useGameStore.getState().addDeath(msg.death);
            }
            break;
          case 'paused_state':
            setPaused(msg.paused ?? false);
            break;
        }
      } catch (e) {
        console.error('WS message parse error:', e);
      }
    };

    ws.onerror = (err) => console.error('WebSocket error:', err);
  }, [setGameState, setPhase, addSpeech, addVote, setWinResult, setConnected]);

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    reset();
  }, [reset]);

  const sendSpeed = useCallback((delaySeconds: number) => {
    wsRef.current?.send(JSON.stringify({
      type: 'set_speed',
      delay_seconds: delaySeconds,
    }));
  }, []);

  const sendPause = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'pause' }));
  }, []);

  const sendResume = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'resume' }));
  }, []);

  useEffect(() => {
    return () => { wsRef.current?.close(); };
  }, []);

  return { connect, disconnect, sendSpeed, sendPause, sendResume };
}
