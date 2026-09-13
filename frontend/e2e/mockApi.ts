import type { Page, Route } from '@playwright/test';

export const API_ORIGIN = process.env.WOLFKILLER_E2E_API_ORIGIN
  ?? 'http://127.0.0.1:18080';

type ExecutionStatus = 'running' | 'paused' | 'interrupted' | 'completed';
type BenchmarkStatus = 'draft' | 'running' | 'paused' | 'completed';

interface MockEvent {
  seq: number;
  event_id: string;
  schema_version: number;
  event_type: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

interface MockGame {
  game_id: string;
  name: string;
  phase: string;
  round_number: number;
  player_count: number;
  alive_count: number;
  winner: string | null;
  execution_status: ExecutionStatus;
  recoverable: boolean;
  recovery_block_code: string | null;
  oldArchive?: boolean;
  events: MockEvent[];
}

interface MockBenchmark {
  run_id: string;
  client_request_id: string;
  name: string;
  mode: 'mixed_arena' | 'paired_regression';
  status: BenchmarkStatus;
  config: Record<string, unknown>;
  schedule_digest: string;
  planned_count: number;
  started_count: number;
  terminal_count: number;
  completed_count: number;
  failed_count: number;
  progress: number;
  created_at: string;
  updated_at: string;
}

const timestamp = '2026-09-06T08:00:00Z';

export function audienceEvent(
  seq: number,
  eventType: string,
  payload: Record<string, unknown>,
): MockEvent {
  return {
    seq,
    event_id: `event-${seq}`,
    schema_version: 1,
    event_type: eventType,
    timestamp,
    payload,
  };
}

export function createLiveGame(gameId = 'mock-live'): MockGame {
  return {
    game_id: gameId,
    name: gameId === 'mock-live' ? '浏览器验收对局' : `对局 ${gameId}`,
    phase: 'speech',
    round_number: 1,
    player_count: 4,
    alive_count: 4,
    winner: null,
    execution_status: 'running',
    recoverable: false,
    recovery_block_code: null,
    events: [
      audienceEvent(1, 'phase', { phase: 'speech', round_number: 1 }),
      audienceEvent(2, 'speech', {
        player_seat: 1,
        text: '第一条公开发言',
        round_number: 1,
        phase: 'speech',
      }),
    ],
  };
}

export interface MockApiState {
  apiAvailable: boolean;
  games: MockGame[];
  benchmark: MockBenchmark | null;
  benchmarkItems: Array<Record<string, unknown>>;
  lastBenchmarkRequest: Record<string, unknown> | null;
}

export function createMockState(): MockApiState {
  return {
    apiAvailable: true,
    games: [],
    benchmark: null,
    benchmarkItems: [],
    lastBenchmarkRequest: null,
  };
}

function players() {
  return Object.fromEntries([1, 2, 3, 4].map((seat) => [seat, {
    seat_number: seat,
    is_alive: true,
    is_sheriff: false,
    role: seat === 1 ? 'wolf-killer-werewolf' : 'wolf-killer-villager',
    camp: seat === 1 ? 'werewolf' : 'good',
  }]));
}

function publicState(game: MockGame) {
  return {
    game_id: game.game_id,
    phase: game.phase,
    round_number: game.round_number,
    reveal_on_death: false,
    players: players(),
    sheriff: null,
    speeches: [],
    death_history: [],
    win_result: game.winner ? {
      winning_camp: game.winner,
      reason: game.winner === 'good' ? 'all_wolves_dead' : 'all_villagers_dead',
    } : null,
    execution_status: game.execution_status,
    recoverable: game.recoverable,
    recovery_block_code: game.recovery_block_code,
  };
}

function runWithStatus(run: MockBenchmark, status: BenchmarkStatus): MockBenchmark {
  const terminal = status === 'completed' ? run.planned_count : run.terminal_count;
  return {
    ...run,
    status,
    started_count: status === 'draft' ? 0 : run.planned_count,
    terminal_count: terminal,
    completed_count: terminal,
    progress: run.planned_count === 0 ? 0 : terminal / run.planned_count,
    updated_at: timestamp,
  };
}

function report() {
  return {
    metric_version: '1.0.0',
    input_digest: 'sha256:e2e-fixture',
    generated_at: timestamp,
    provisional: true,
    summary: {
      games: { total: 2, completed: 1 },
      requests: { success_rate: 0.9, unknown: 1 },
      attempts: {
        token_usage: { known_total_tokens: 1200, completeness_rate: 0.8 },
        latency_ms: { p95: 240 },
      },
    },
    metrics: {
      model_performance: [
        { id: 'mock-role', model: '测试模型', role: 'villager', samples: 8, win_rate: 0.625 },
      ],
    },
    data_quality: { interruptions: 0, excluded_pairs: 1 },
  };
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

export async function installMockApi(page: Page, state: MockApiState): Promise<void> {
  await page.route(`${API_ORIGIN}/api/**`, async (route) => {
    if (!state.apiAvailable) {
      await route.abort('connectionrefused');
      return;
    }

    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === '/api/health') {
      await json(route, { status: 'ok', service: 'wolf-killer' });
      return;
    }
    if (path === '/api/catalog/presets') {
      await json(route, { presets: [{
        id: 'four-player-e2e',
        name: '四人验收场',
        description: '1 狼 3 民',
        role_counts: { 'wolf-killer-werewolf': 1, 'wolf-killer-villager': 3 },
      }] });
      return;
    }
    if (path === '/api/catalog/roles') {
      await json(route, { roles: [
        { role_id: 'wolf-killer-werewolf', display_name: 'Werewolf', name_zh: '狼人', camp: 'werewolf', icon: 'wolf', description: '夜间行动', min_count: 1, max_count: null, dependencies: [], exclusions: [] },
        { role_id: 'wolf-killer-villager', display_name: 'Villager', name_zh: '平民', camp: 'good', icon: 'villager', description: '白天发言', min_count: 1, max_count: null, dependencies: [], exclusions: [] },
      ] });
      return;
    }
    if (path === '/api/catalog/constraints') {
      await json(route, { min_players: 4, max_players: 12, min_werewolves: 1, min_good: 1 });
      return;
    }
    if (path === '/api/models' && method === 'GET') {
      await json(route, { configs: [{
        id: 'model-e2e', name: '测试模型', base_url: 'http://model.invalid/v1',
        model_id: 'deterministic-e2e', has_key: false, api_key_masked: null,
        key_invalid: false, temperature: 0, strict_base_url: null,
        created_at: timestamp, updated_at: timestamp,
      }] });
      return;
    }
    if (path === '/api/games' && method === 'POST') {
      const game = createLiveGame();
      state.games = [game, ...state.games.filter((item) => item.game_id !== game.game_id)];
      await json(route, { game_id: game.game_id, player_count: 4, config: {} }, 201);
      return;
    }
    if (path === '/api/games' && method === 'GET') {
      await json(route, { games: state.games.map((game) => ({
        game_id: game.game_id,
        name: game.name,
        phase: game.phase,
        round_number: game.round_number,
        player_count: game.player_count,
        alive_count: game.alive_count,
        winner: game.winner,
        execution_status: game.execution_status,
        recoverable: game.recoverable,
        recovery_block_code: game.recovery_block_code,
      })) });
      return;
    }

    const gameMatch = path.match(/^\/api\/games\/([^/]+)(?:\/(snapshot|events|logs|memories|pause|resume|recover))?$/);
    if (gameMatch) {
      const game = state.games.find((item) => item.game_id === decodeURIComponent(gameMatch[1]));
      if (!game) {
        await json(route, { detail: 'game not found' }, 404);
        return;
      }
      const action = gameMatch[2];
      if (action === 'snapshot') {
        if (game.oldArchive) {
          await json(route, { detail: { code: 'snapshot_unavailable' } }, 409);
          return;
        }
        await json(route, {
          game_id: game.game_id,
          schema_version: 1,
          projection_version: 1,
          last_seq: game.events.length,
          state: publicState(game),
        });
        return;
      }
      if (action === 'events') {
        const after = Number(url.searchParams.get('after_seq') ?? 0);
        const throughValue = url.searchParams.get('through_seq');
        const through = throughValue === null ? Number.POSITIVE_INFINITY : Number(throughValue);
        const events = game.events.filter((event) => event.seq > after && event.seq <= through);
        const last = events.at(-1)?.seq ?? after;
        await json(route, {
          game_id: game.game_id,
          events,
          next_seq: last,
          high_watermark: game.events.length,
          has_more: last < Math.min(game.events.length, through),
        });
        return;
      }
      if (action === 'logs') {
        await json(route, { game_id: game.game_id, events: game.events.map((event) => ({
          timestamp: event.timestamp,
          event_type: event.event_type,
          payload: event.payload,
        })) });
        return;
      }
      if (action === 'memories') {
        await json(route, { game_id: game.game_id, memories: [] });
        return;
      }
      if (action && ['pause', 'resume', 'recover'].includes(action)) {
        game.execution_status = action === 'pause' ? 'paused' : 'running';
        game.recoverable = false;
        game.recovery_block_code = null;
        await json(route, {
          game_id: game.game_id,
          execution_status: game.execution_status,
          recoverable: game.recoverable,
          recovery_block_code: game.recovery_block_code,
          interruption_count: action === 'recover' ? 1 : 0,
          benchmark_run_id: null,
        });
        return;
      }
      await json(route, publicState(game));
      return;
    }

    if (path === '/api/benchmarks' && method === 'POST') {
      const input = request.postDataJSON() as Record<string, unknown>;
      state.lastBenchmarkRequest = input;
      const planned = Number(input.games ?? (Number(input.repetitions ?? 1) * 2));
      state.benchmark = {
        run_id: 'benchmark-e2e',
        client_request_id: String(input.client_request_id),
        name: String(input.name),
        mode: input.mode as MockBenchmark['mode'],
        status: 'draft',
        config: input,
        schedule_digest: 'sha256:schedule-e2e',
        planned_count: planned,
        started_count: 0,
        terminal_count: 0,
        completed_count: 0,
        failed_count: 0,
        progress: 0,
        created_at: timestamp,
        updated_at: timestamp,
      };
      await json(route, state.benchmark, 201);
      return;
    }
    if (path === '/api/benchmarks' && method === 'GET') {
      await json(route, { benchmarks: state.benchmark ? [state.benchmark] : [], total: state.benchmark ? 1 : 0, offset: 0, limit: 50 });
      return;
    }

    const benchmarkMatch = path.match(/^\/api\/benchmarks\/([^/]+)(?:\/(games|report|start|pause|resume|cancel))?(?:\/(rebuild))?$/);
    if (benchmarkMatch && state.benchmark) {
      const action = benchmarkMatch[2];
      if (action === 'games') {
        await json(route, { games: state.benchmarkItems, total: state.benchmarkItems.length, offset: 0, limit: 50 });
        return;
      }
      if (action === 'report') {
        await json(route, report());
        return;
      }
      if (action === 'start' || action === 'resume') state.benchmark = runWithStatus(state.benchmark, 'running');
      if (action === 'pause') state.benchmark = runWithStatus(state.benchmark, 'paused');
      if (action === 'cancel') state.benchmark = runWithStatus(state.benchmark, 'completed');
      await json(route, state.benchmark);
      return;
    }

    await json(route, { detail: `unhandled mock route: ${method} ${path}` }, 501);
  });
}

export function completedBenchmark(name = '完成的评测'): MockBenchmark {
  return {
    run_id: 'benchmark-e2e', client_request_id: 'e2e-existing', name,
    mode: 'mixed_arena', status: 'completed', config: {},
    schedule_digest: 'sha256:existing', planned_count: 2, started_count: 2,
    terminal_count: 2, completed_count: 1, failed_count: 1, progress: 1,
    created_at: timestamp, updated_at: timestamp,
  };
}
