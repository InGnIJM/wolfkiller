import type { GameListResponse, GameLogs, PublicGameState } from '../store/types';

function getApiBase(): string {
  return import.meta.env.VITE_API_URL || 'http://localhost:8000';
}

function getWsBase(): string {
  return getApiBase().replace(/^http/, 'ws');
}

export async function createGame(config?: {
  num_werewolves?: number;
  num_villagers?: number;
  num_seers?: number;
  num_witches?: number;
  num_hunters?: number;
}): Promise<{ game_id: string }> {
  const res = await fetch(`${getApiBase()}/api/games`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config || {}),
  });
  if (!res.ok) throw new Error(`Create game failed: ${res.status}`);
  return res.json();
}

export async function listGames(): Promise<GameListResponse> {
  const res = await fetch(`${getApiBase()}/api/games`);
  if (!res.ok) throw new Error(`List games failed: ${res.status}`);
  return res.json();
}

export async function fetchGameLogs(gameId: string): Promise<GameLogs> {
  const res = await fetch(`${getApiBase()}/api/games/${gameId}/logs`);
  if (!res.ok) throw new Error(`Fetch logs failed: ${res.status}`);
  return res.json();
}

export async function fetchGameDetail(gameId: string): Promise<PublicGameState> {
  const res = await fetch(`${getApiBase()}/api/games/${gameId}`);
  if (!res.ok) throw new Error(`Fetch game detail failed: ${res.status}`);
  return res.json();
}

export function getWsUrl(gameId: string): string {
  return `${getWsBase()}/ws/game/${gameId}`;
}
