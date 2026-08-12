import { useEffect, useRef, useCallback } from 'react';
import { useGameStore } from '../store/gameStore';
import { getWsUrl } from '../api/client';

const PUBLIC_NIGHT_SUBSTEPS = new Set([
  'werewolf_open',
  'werewolf_vote',
  'werewolf_target',
  'werewolf_close',
  'witch_open',
  'witch_action',
  'witch_close',
  'seer_open',
  'seer_check',
  'seer_close',
]);

function hasExactKeys(value: unknown, keys: string[]): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const actualKeys = Object.keys(value);
  return actualKeys.length === keys.length && keys.every((key) => actualKeys.includes(key));
}

function isPausedState(value: unknown): value is { type: 'paused_state'; paused: boolean } {
  return hasExactKeys(value, ['type', 'paused'])
    && value.type === 'paused_state'
    && typeof value.paused === 'boolean';
}

function isNightSubstep(value: unknown): value is {
  type: 'night_substep';
  phase: 'night';
  substep: string;
  round_number: number;
} {
  return hasExactKeys(value, ['type', 'phase', 'substep', 'round_number'])
    && value.type === 'night_substep'
    && value.phase === 'night'
    && typeof value.substep === 'string'
    && PUBLIC_NIGHT_SUBSTEPS.has(value.substep)
    && typeof value.round_number === 'number'
    && Number.isInteger(value.round_number)
    && value.round_number > 0;
}

function detachSocket(socket: WebSocket | null): void {
  if (socket === null) return;
  socket.onopen = null;
  socket.onclose = null;
  socket.onmessage = null;
  socket.onerror = null;
  socket.close();
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);

  const connect = useCallback((gameId: string) => {
    detachSocket(wsRef.current);
    useGameStore.getState().setConnected(false);

    const url = getWsUrl(gameId);
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (wsRef.current === ws) useGameStore.getState().setConnected(true);
    };
    ws.onclose = () => {
      if (wsRef.current === ws) {
        wsRef.current = null;
        useGameStore.getState().setConnected(false);
      }
    };

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return;
      try {
        const msg: unknown = JSON.parse(event.data);
        if (isPausedState(msg)) {
          useGameStore.getState().setPaused(msg.paused);
        } else if (isNightSubstep(msg)) {
          useGameStore.getState().setNightSubstep({
            substep: msg.substep,
            roundNumber: msg.round_number,
          });
        }
      } catch (e) {
        console.error('WS message parse error:', e);
      }
    };

    ws.onerror = (err) => {
      if (wsRef.current === ws) console.error('WebSocket error:', err);
    };
  }, []);

  const disconnect = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    detachSocket(ws);
    useGameStore.getState().setConnected(false);
  }, []);

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
    return disconnect;
  }, [disconnect]);

  return { connect, disconnect, sendSpeed, sendPause, sendResume };
}
