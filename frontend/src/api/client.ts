import type {
  AudienceEventPage, AudienceSnapshot, BenchmarkCreateInput, BenchmarkItem,
  BenchmarkItemsPage, BenchmarkReport, BenchmarkRun, BenchmarkRunList,
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

export class AudienceApiError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`Audience API unavailable: ${status}`);
    this.name = 'AudienceApiError';
    this.status = status;
  }
}

export async function fetchAudienceSnapshot(gameId: string): Promise<AudienceSnapshot> {
  const res = await fetch(`${getApiBase()}/api/games/${gameId}/snapshot`);
  if (!res.ok) throw new AudienceApiError(res.status);
  const data = await res.json() as AudienceSnapshot & { last_seq?: number };
  return { ...data, seq: data.seq ?? data.last_seq ?? 0 };
}

export async function fetchAudienceEvents(
  gameId: string,
  afterSeq: number,
  options: { limit?: number; throughSeq?: number } = {},
): Promise<AudienceEventPage> {
  const params = new URLSearchParams({
    after_seq: String(afterSeq),
    limit: String(options.limit ?? 100),
  });
  if (options.throughSeq !== undefined) {
    params.set('through_seq', String(options.throughSeq));
  }
  const res = await fetch(`${getApiBase()}/api/games/${gameId}/events?${params.toString()}`);
  if (!res.ok) throw new AudienceApiError(res.status);
  const data = await res.json() as AudienceEventPage & {
    next_seq?: number;
    high_watermark?: number;
    has_more?: boolean;
  };
  return {
    ...data,
    after_seq: data.after_seq ?? afterSeq,
    last_seq: data.last_seq ?? data.next_seq ?? afterSeq,
    caught_up: data.caught_up ?? !data.has_more,
  };
}

export async function controlGame(
  gameId: string,
  action: 'pause' | 'resume' | 'recover',
): Promise<Partial<GameListItem>> {
  const res = await fetch(`${getApiBase()}/api/games/${gameId}/${action}`, { method: 'POST' });
  if (!res.ok) throwHttpError(res, `${action} game failed`);
  if (res.status === 204) return {};
  return res.json();
}

export function getWsUrl(gameId: string, afterSeq = 0): string {
  const params = new URLSearchParams({ protocol: '2', after_seq: String(Math.max(0, afterSeq)) });
  return `${getWsBase()}/ws/game/${gameId}?${params.toString()}`;
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

export async function listBenchmarks(offset = 0, limit = 50): Promise<BenchmarkRunList> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  const res = await fetch(`${getApiBase()}/api/benchmarks?${params.toString()}`);
  if (!res.ok) throw new Error(`List benchmarks failed: ${res.status}`);
  const data = await res.json() as BenchmarkRunList | BenchmarkRun[] | { benchmarks: BenchmarkRun[] };
  if (Array.isArray(data)) return { runs: data };
  if ('benchmarks' in data) return { runs: data.benchmarks };
  return data;
}

export async function createBenchmark(input: BenchmarkCreateInput): Promise<BenchmarkRun> {
  const res = await fetch(`${getApiBase()}/api/benchmarks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Create benchmark failed: ${res.status}`);
  return res.json();
}

export async function fetchBenchmark(runId: string): Promise<BenchmarkRun> {
  const res = await fetch(`${getApiBase()}/api/benchmarks/${runId}`);
  if (!res.ok) throw new Error(`Fetch benchmark failed: ${res.status}`);
  return res.json();
}

export async function controlBenchmark(
  runId: string,
  action: 'start' | 'pause' | 'resume' | 'cancel',
): Promise<BenchmarkRun> {
  const res = await fetch(`${getApiBase()}/api/benchmarks/${runId}/${action}`, { method: 'POST' });
  if (!res.ok) throw new Error(`${action} benchmark failed: ${res.status}`);
  return res.json();
}

export async function fetchBenchmarkGames(
  runId: string,
  offset = 0,
  limit = 50,
  status?: string,
): Promise<BenchmarkItemsPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  if (status) params.set('status', status);
  const res = await fetch(`${getApiBase()}/api/benchmarks/${runId}/games?${params.toString()}`);
  if (!res.ok) throw new Error(`Fetch benchmark games failed: ${res.status}`);
  const data = await res.json() as BenchmarkItemsPage | BenchmarkItem[] | { items: BenchmarkItem[]; total?: number };
  if (Array.isArray(data)) return { games: data };
  if ('items' in data) return { games: data.items, total: data.total };
  return data;
}

export async function fetchBenchmarkReport(runId: string): Promise<BenchmarkReport> {
  const res = await fetch(`${getApiBase()}/api/benchmarks/${runId}/report`);
  if (!res.ok && res.status !== 503) throw new Error(`Fetch benchmark report failed: ${res.status}`);
  const data = await res.json() as BenchmarkReport | { report: BenchmarkReport };
  if (!res.ok && (!('status' in data) || data.status !== 'failed')) throw new Error(`Fetch benchmark report failed: ${res.status}`);
  return 'report' in data && data.report && typeof data.report === 'object'
    ? data.report as BenchmarkReport
    : data;
}

export async function rebuildBenchmarkReport(runId: string): Promise<BenchmarkReport> {
  const res = await fetch(`${getApiBase()}/api/benchmarks/${runId}/report/rebuild`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`Rebuild benchmark report failed: ${res.status}`);
  const data = await res.json() as BenchmarkReport | { report: BenchmarkReport };
  return 'report' in data && data.report && typeof data.report === 'object'
    ? data.report as BenchmarkReport
    : data;
}

export function benchmarkExportUrl(
  runId: string,
  format: 'json' | 'csv' | 'markdown',
): string {
  return `${getApiBase()}/api/benchmarks/${runId}/export?format=${format}`;
}
