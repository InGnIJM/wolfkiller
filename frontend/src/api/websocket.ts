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
            setGameState(msg.state);
            break;
          case 'phase_change':
            setGameState(msg.state);
            setPhase(msg.phase, msg.round_number);
            break;
          case 'speech':
            addSpeech(msg.speech);
            break;
          case 'vote_cast':
            addVote(msg.vote);
            break;
          case 'game_over':
            setGameState(msg.state);
            setWinResult(msg.win_result);
            break;
          case 'night_substep':
            setNightSubstep({
              substep: msg.substep,
              roundNumber: msg.round_number,
            });
            break;
          case 'player_died':
            useGameStore.getState().addDeath(msg.death);
            break;
          case 'paused_state':
            setPaused(msg.paused);
            break;
        }
      } catch (e) {
        console.error('WS message parse error:', e);
      }
    };

    ws.onerror = (err) => console.error('WebSocket error:', err);
  }, [
    setGameState, setPhase, addSpeech, addVote,
    setWinResult, setConnected, setNightSubstep, setPaused,
  ]);

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
