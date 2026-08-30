import type {
  FieldConstraints, GameListItem, GameListResponse, GameLogs, GameMemories, GamePreset,
  ModelAssignment, ModelConfig, ModelConfigInput, ModelSnapshotEntry,
  ModelTestResult, PublicGameState, RoleCatalogItem,
} from '../store/types';

function getApiBase(): string {
  return import.meta.env.VITE_API_URL || 'http://localhost:8000';
}

function getWsBase(): string {
  return getApiBase().replace(/^http/, 'ws');
}

export async function createGame(config?: {
  role_counts?: Record<string, number>;
  reveal_on_death?: boolean;
  model_assignments?: ModelAssignment[];
}): Promise<{ game_id: string; model_snapshot?: ModelSnapshotEntry[] }> {
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

export async function renameGame(gameId: string, name: string): Promise<GameListItem> {
  const res = await fetch(`${getApiBase()}/api/games/${gameId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`Rename game failed: ${res.status}`);
  return res.json();
}

export async function deleteGame(gameId: string): Promise<void> {
  const res = await fetch(`${getApiBase()}/api/games/${gameId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Delete game failed: ${res.status}`);
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

export async function listModels(): Promise<ModelConfig[]> {
  const res = await fetch(`${getApiBase()}/api/models`);
  if (!res.ok) throw new Error(`List models failed: ${res.status}`);
  const data = (await res.json()) as { configs: ModelConfig[] };
  return data.configs;
}

export async function createModel(input: ModelConfigInput): Promise<ModelConfig> {
  const res = await fetch(`${getApiBase()}/api/models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Create model failed: ${res.status}`);
  return res.json();
}

export async function updateModel(id: string, input: ModelConfigInput): Promise<ModelConfig> {
  const res = await fetch(`${getApiBase()}/api/models/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Update model failed: ${res.status}`);
  return res.json();
}

export async function deleteModel(id: string): Promise<void> {
  const res = await fetch(`${getApiBase()}/api/models/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Delete model failed: ${res.status}`);
}

export async function testModelConnection(input: {
  config_id?: string | null;
  base_url?: string;
  api_key?: string;
  model_id?: string;
}): Promise<ModelTestResult> {
  const res = await fetch(`${getApiBase()}/api/models/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Test model failed: ${res.status}`);
  return res.json();
}

export async function fetchRoleCatalog(): Promise<RoleCatalogItem[]> {
  const res = await fetch(`${getApiBase()}/api/catalog/roles`);
  if (!res.ok) throw new Error(`Fetch roles failed: ${res.status}`);
  const data = (await res.json()) as { roles: RoleCatalogItem[] };
  return data.roles;
}

export async function fetchPresets(): Promise<GamePreset[]> {
  const res = await fetch(`${getApiBase()}/api/catalog/presets`);
  if (!res.ok) throw new Error(`Fetch presets failed: ${res.status}`);
  const data = (await res.json()) as { presets: GamePreset[] };
  return data.presets;
}

export async function fetchConstraints(): Promise<FieldConstraints> {
  const res = await fetch(`${getApiBase()}/api/catalog/constraints`);
  if (!res.ok) throw new Error(`Fetch constraints failed: ${res.status}`);
  return res.json();
}
