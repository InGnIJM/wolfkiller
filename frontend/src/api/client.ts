import type {
  GameListResponse, GameLogs, GameMemories, PublicGameState,
} from '../store/types';

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

export class GameNotFoundError extends Error {
  constructor() {
    super('对局不存在或已失效');
    this.name = 'GameNotFoundError';
  }
}

function throwHttpError(res: Response, fallback: string): never {
  if (res.status === 404) throw new GameNotFoundError();
  throw new Error(`${fallback}: ${res.status}`);
}

export async function fetchGameLogs(gameId: string): Promise<GameLogs> {
  const res = await fetch(`${getApiBase()}/api/games/${gameId}/logs`);
  if (!res.ok) throwHttpError(res, 'Fetch logs failed');
  return res.json();
}

export async function fetchGameDetail(gameId: string): Promise<PublicGameState> {
  const res = await fetch(`${getApiBase()}/api/games/${gameId}`);
  if (!res.ok) throwHttpError(res, 'Fetch game detail failed');
  return res.json();
}

export async function fetchGameMemories(gameId: string): Promise<GameMemories> {
  const res = await fetch(`${getApiBase()}/api/games/${gameId}/memories`);
  if (!res.ok) throwHttpError(res, 'Fetch game memories failed');
  return res.json();
}

export function getWsUrl(gameId: string): string {
  return `${getWsBase()}/ws/game/${gameId}`;
}
