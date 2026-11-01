import { useEffect, useRef, useCallback } from 'react';
import { useGameStore } from '../store/gameStore';
import { getWsUrl } from '../api/client';
import type { AudienceEvent } from '../store/types';

function hasExactKeys(value: unknown, keys: string[]): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const actualKeys = Object.keys(value);
  return actualKeys.length === keys.length && keys.every((key) => actualKeys.includes(key));
}

function isPausedState(value: unknown): value is {
  type: 'paused_state'; paused: boolean; code?: string | null; reason?: string | null;
} {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const keys = Object.keys(value);
  if (!keys.every((key) => ['type', 'paused', 'code', 'reason'].includes(key))) return false;
  const candidate = value as Record<string, unknown>;
  if (candidate.type !== 'paused_state' || typeof candidate.paused !== 'boolean') return false;
  if ('code' in candidate && candidate.code !== null && typeof candidate.code !== 'string') return false;
  if ('reason' in candidate && candidate.reason !== null && typeof candidate.reason !== 'string') return false;
  return true;
}

function isActing(value: unknown): value is { type: 'acting'; seat: number } {
  return hasExactKeys(value, ['type', 'seat'])
    && value.type === 'acting'
    && typeof value.seat === 'number'
    && Number.isInteger(value.seat)
    && value.seat > 0;
}

type LiveTextType = 'speech_stream' | 'speech_thinking';

function isLiveText<T extends LiveTextType>(
  value: unknown,
  type: T,
): value is { type: T; seat: number; text: string } {
  return hasExactKeys(value, ['type', 'seat', 'text'])
    && value.type === type
    && typeof value.seat === 'number'
    && Number.isInteger(value.seat)
    && value.seat > 0
    && typeof value.text === 'string';
}

function isAudienceEvent(value: unknown): value is AudienceEvent {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const event = value as Record<string, unknown>;
  return Number.isInteger(event.seq)
    && typeof event.event_id === 'string'
    && typeof event.event_type === 'string'
    && typeof event.payload === 'object'
    && event.payload !== null
    && !Array.isArray(event.payload)
    && Number.isInteger(event.schema_version);
}

function audienceEventsFromMessage(value: unknown): AudienceEvent[] {
  if (isAudienceEvent(value)) return [value];
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return [];
  const message = value as Record<string, unknown>;
  if (isAudienceEvent(message.event)) return [message.event];
  if (Array.isArray(message.events)) return message.events.filter(isAudienceEvent);
  return [];
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

  const connect = useCallback((gameId: string, onCursorAhead?: () => void) => {
    detachSocket(wsRef.current);
    useGameStore.getState().setConnected(false);

    const url = getWsUrl(gameId, useGameStore.getState().audienceCursor);
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
        if (typeof msg === 'object' && msg !== null && (msg as { type?: unknown }).type === 'ping') {
          ws.send(JSON.stringify({ type: 'pong' }));
          return;
        }
        if (typeof msg === 'object' && msg !== null
          && (msg as { type?: unknown }).type === 'error'
          && (msg as { code?: unknown }).code === 'cursor_ahead') {
          wsRef.current = null;
          detachSocket(ws);
          useGameStore.getState().setConnected(false);
          onCursorAhead?.();
          return;
        }
        if (isPausedState(msg)) {
          useGameStore.getState().setPaused(
            msg.paused,
            msg.paused ? {
              code: msg.code ?? null,
              reason: msg.reason ?? null,
            } : null,
          );
        } else if (isLiveText(msg, 'speech_stream') && useGameStore.getState().isFollowingLive) {
          useGameStore.getState().setStreamingSpeech(msg.seat, msg.text);
        } else if (isLiveText(msg, 'speech_thinking') && useGameStore.getState().isFollowingLive) {
          useGameStore.getState().setStreamingThinking(msg.seat, msg.text);
        } else if (isActing(msg) && useGameStore.getState().isFollowingLive) {
          useGameStore.getState().setCurrentSpeaker(msg.seat);
        } else {
          const events = audienceEventsFromMessage(msg);
          if (events.length > 0) {
            const lastSeq = Math.max(...events.map((item) => item.seq));
            useGameStore.getState().mergeAudienceEvents({
              game_id: gameId,
              after_seq: useGameStore.getState().audienceCursor,
              last_seq: lastSeq,
              events,
              caught_up: true,
            });
            ws.send(JSON.stringify({
              type: 'ack',
              last_seq: useGameStore.getState().audienceCursor,
            }));
          }
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

  useEffect(() => {
    return disconnect;
  }, [disconnect]);

  return { connect, disconnect };
}
