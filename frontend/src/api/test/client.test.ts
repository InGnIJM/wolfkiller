import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  assignGameFolder, batchDeleteBenchmarkGames, batchDeleteGames, batchMoveGames,
  createFolder, deleteBenchmark, deleteBenchmarkGame, deleteFolder,
  fetchBenchmarkGames, fetchBenchmarkReport, createBenchmark, createGame, createModel, deleteGame, deleteModel,
  fetchAudienceEvents, fetchAudienceSnapshot, fetchConstraints, fetchPresets,
  fetchRoleCatalog, getWsUrl, listBenchmarks, listFolders, listModels, renameFolder, renameGame,
  testModelConnection, updateModel,
} from '../client';

const BASE = 'http://localhost:8000';

function mockFetch(body: unknown, ok = true, status = 200) {
  const fn = vi.fn();
  fn.mockImplementation(async () => ({ ok, status, json: async () => body }));
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('model config api', () => {
  it('lists models and unwraps the configs envelope', async () => {
    const fetchFn = mockFetch({ configs: [{ id: 'a', name: 'n' }] });
    const result = await listModels();
    expect(fetchFn).toHaveBeenCalledWith(`${BASE}/api/models`);
    expect(result).toEqual([{ id: 'a', name: 'n' }]);
  });

  it('creates a model with JSON body', async () => {
    const fetchFn = mockFetch({ id: 'a', name: 'n' });
    const result = await createModel({
      name: 'n', base_url: 'https://x', model_id: 'm', api_key: 'sk-k',
    });
    expect(result).toEqual({ id: 'a', name: 'n' });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe(`${BASE}/api/models`);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({
      name: 'n', base_url: 'https://x', model_id: 'm', api_key: 'sk-k',
    });
  });

  it('updates and deletes a model', async () => {
    const fetchFn = mockFetch({ id: 'a' });
    await updateModel('a', { name: 'n', base_url: 'https://x', model_id: 'm' });
    expect(fetchFn.mock.calls[0][0]).toBe(`${BASE}/api/models/a`);
    expect(fetchFn.mock.calls[0][1].method).toBe('PUT');

    await deleteModel('a');
    expect(fetchFn.mock.calls[1][0]).toBe(`${BASE}/api/models/a`);
    expect(fetchFn.mock.calls[1][1].method).toBe('DELETE');
  });

  it('tests model connection', async () => {
    const fetchFn = mockFetch({ ok: true, latency_ms: 42, error: null });
    const result = await testModelConnection({ config_id: 'a' });
    expect(result).toEqual({ ok: true, latency_ms: 42, error: null });
    expect(fetchFn.mock.calls[0][0]).toBe(`${BASE}/api/models/test`);
  });

  it('fetches role catalog and unwraps the envelope', async () => {
    mockFetch({ roles: [{ role_id: 'wolf-killer-werewolf' }] });
    const result = await fetchRoleCatalog();
    expect(result).toEqual([{ role_id: 'wolf-killer-werewolf' }]);
  });

  it('fetches presets and constraints', async () => {
    const fetchFn = mockFetch({ presets: [{ id: 'p' }] });
    expect(await fetchPresets()).toEqual([{ id: 'p' }]);
    expect(fetchFn.mock.calls[0][0]).toBe(`${BASE}/api/catalog/presets`);

    const fetchFn2 = mockFetch({ min_players: 4, max_players: 12, min_werewolves: 1, min_good: 1 });
    expect(await fetchConstraints()).toEqual({
      min_players: 4, max_players: 12, min_werewolves: 1, min_good: 1,
    });
    expect(fetchFn2.mock.calls[0][0]).toBe(`${BASE}/api/catalog/constraints`);
  });

  it('createGame posts role_counts and model_assignments', async () => {
    const fetchFn = mockFetch({ game_id: 'g', model_snapshot: [] });
    const result = await createGame({
      role_counts: { 'wolf-killer-werewolf': 1 },
      model_assignments: [{ config_id: null, count: 1 }],
    });
    expect(result).toEqual({ game_id: 'g', model_snapshot: [] });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe(`${BASE}/api/games`);
    expect(JSON.parse(init.body)).toEqual({
      role_counts: { 'wolf-killer-werewolf': 1 },
      model_assignments: [{ config_id: null, count: 1 }],
    });
  });

  it('renames a game with PATCH', async () => {
    const fetchFn = mockFetch({ game_id: 'g1', name: '新名字' });
    const result = await renameGame('g1', '新名字');
    expect(result).toEqual({ game_id: 'g1', name: '新名字' });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe(`${BASE}/api/games/g1`);
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body)).toEqual({ name: '新名字' });
  });

  it('deletes a game', async () => {
    const fetchFn = mockFetch({}, true, 204);
    await deleteGame('g1');
    expect(fetchFn.mock.calls[0][0]).toBe(`${BASE}/api/games/g1`);
    expect(fetchFn.mock.calls[0][1].method).toBe('DELETE');
  });

  it('rejects failed rename and delete', async () => {
    mockFetch({}, false, 400);
    await expect(renameGame('g1', 'x')).rejects.toThrow('Rename game failed: 400');
    mockFetch({}, false, 500);
    await expect(deleteGame('g1')).rejects.toThrow('Delete game failed: 500');
  });

  it('explains benchmark-owned delete conflicts in Chinese', async () => {
    mockFetch({ detail: { code: 'game_referenced_by_benchmark' } }, false, 409);
    await expect(deleteGame('g1')).rejects.toThrow('这是评测对局，请到评测页删除');
    mockFetch({ detail: { code: 'other' } }, false, 409);
    await expect(deleteGame('g1')).rejects.toThrow('Delete game failed: 409');
    mockFetch({ detail: 'nope' }, false, 409);
    await expect(deleteGame('g1')).rejects.toThrow('Delete game failed: 409');
    const broken = vi.fn();
    broken.mockImplementation(async () => ({
      ok: false, status: 409, json: async () => { throw new Error('bad json'); },
    }));
    vi.stubGlobal('fetch', broken);
    await expect(deleteGame('g1')).rejects.toThrow('Delete game failed: 409');
  });

  it('manages folders and batch lobby operations', async () => {
    const listed = mockFetch({ folders: [{ folder_id: 'f1', name: '九月' }] });
    expect(await listFolders()).toEqual({ folders: [{ folder_id: 'f1', name: '九月' }] });
    expect(listed).toHaveBeenCalledWith(`${BASE}/api/folders`);

    const created = mockFetch({ folder_id: 'f2', name: '归档' });
    await createFolder('归档');
    expect(created.mock.calls[0][1].method).toBe('POST');

    const renamed = mockFetch({ folder_id: 'f2', name: '新名' });
    await renameFolder('f2', '新名');
    expect(renamed.mock.calls[0][0]).toBe(`${BASE}/api/folders/f2`);

    const removed = mockFetch({}, true, 204);
    await deleteFolder('f2');
    expect(removed.mock.calls[0][1].method).toBe('DELETE');

    const assigned = mockFetch({}, true, 204);
    await assignGameFolder('g1', null);
    expect(JSON.parse(assigned.mock.calls[0][1].body)).toEqual({ folder_id: null });

    mockFetch({ deleted: ['g1'], failed: [] });
    expect(await batchDeleteGames(['g1'])).toEqual({ deleted: ['g1'], failed: [] });
    const moved = mockFetch({ moved: ['g1'], failed: [] });
    expect(await batchMoveGames(['g1'], 'f1')).toEqual({ moved: ['g1'], failed: [] });
    expect(JSON.parse(moved.mock.calls[0][1].body)).toEqual({ game_ids: ['g1'], folder_id: 'f1' });

    mockFetch({}, false, 500);
    await expect(listFolders()).rejects.toThrow('List folders failed: 500');
    mockFetch({}, false, 500);
    await expect(createFolder('x')).rejects.toThrow('Create folder failed: 500');
    mockFetch({}, false, 500);
    await expect(renameFolder('f', 'x')).rejects.toThrow('Rename folder failed: 500');
    mockFetch({}, false, 500);
    await expect(deleteFolder('f')).rejects.toThrow('Delete folder failed: 500');
    mockFetch({}, false, 500);
    await expect(assignGameFolder('g', 'f')).rejects.toThrow('Assign folder failed: 500');
    mockFetch({}, false, 500);
    await expect(batchDeleteGames(['g'])).rejects.toThrow('Batch delete failed: 500');
    mockFetch({}, false, 500);
    await expect(batchMoveGames(['g'], null)).rejects.toThrow('Batch move failed: 500');
  });

  it('deletes benchmarks and their games', async () => {
    const run = mockFetch({}, true, 204);
    await deleteBenchmark('r1');
    expect(run.mock.calls[0][0]).toBe(`${BASE}/api/benchmarks/r1`);
    const game = mockFetch({}, true, 204);
    await deleteBenchmarkGame('r1', 'g1');
    expect(game.mock.calls[0][0]).toBe(`${BASE}/api/benchmarks/r1/games/g1`);
    mockFetch({ deleted: ['g1'], failed: [] });
    expect(await batchDeleteBenchmarkGames('r1', ['g1'])).toEqual({ deleted: ['g1'], failed: [] });
    mockFetch({}, false, 500);
    await expect(deleteBenchmark('r1')).rejects.toThrow('Delete benchmark failed: 500');
    mockFetch({}, false, 500);
    await expect(deleteBenchmarkGame('r1', 'g1')).rejects.toThrow('Delete benchmark game failed: 500');
    mockFetch({}, false, 500);
    await expect(batchDeleteBenchmarkGames('r1', ['g1'])).rejects.toThrow('Batch delete benchmark games failed: 500');
  });

  it('rejects non-ok responses', async () => {
    mockFetch({}, false, 500);
    await expect(listModels()).rejects.toThrow('List models failed: 500');
  });
});

describe('audience and benchmark api', () => {
  it('preserves benchmark pagination totals and report generation states', async () => {
    mockFetch({ items: [], total: 51 });
    expect(await fetchBenchmarkGames('r')).toEqual({ games: [], total: 51 });
    mockFetch({ status: 'pending' }, true, 202);
    expect(await fetchBenchmarkReport('r')).toEqual({ status: 'pending' });
    mockFetch({ status: 'failed', error: 'generation failed' }, false, 503);
    expect(await fetchBenchmarkReport('r')).toEqual({ status: 'failed', error: 'generation failed' });
    mockFetch({ detail: 'unavailable' }, false, 503);
    await expect(fetchBenchmarkReport('r')).rejects.toThrow('503');
  });

  it('normalizes durable snapshot and event cursor field names', async () => {
    const fetchFn = mockFetch({
      game_id: 'g1', last_seq: 4, projection_version: 1,
      state: { game_id: 'g1' },
    });
    expect((await fetchAudienceSnapshot('g1')).seq).toBe(4);
    expect(fetchFn.mock.calls[0][0]).toBe(`${BASE}/api/games/g1/snapshot`);

    mockFetch({
      game_id: 'g1', events: [], next_seq: 4, high_watermark: 4, has_more: false,
    });
    const page = await fetchAudienceEvents('g1', 2, { limit: 25, throughSeq: 4 });
    expect(page).toMatchObject({ after_seq: 2, last_seq: 4, caught_up: true });
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe(
      `${BASE}/api/games/g1/events?after_seq=2&limit=25&through_seq=4`,
    );
    expect(getWsUrl('g1', 7)).toBe('ws://localhost:8000/ws/game/g1?protocol=2&after_seq=7');
  });

  it('unwraps benchmark lists and creates a frozen draft request', async () => {
    mockFetch({ benchmarks: [{ run_id: 'run-1' }], total: 1 });
    expect((await listBenchmarks()).runs).toEqual([{ run_id: 'run-1' }]);

    const fetchFn = mockFetch({ run_id: 'run-2', status: 'pending' });
    await createBenchmark({
      client_request_id: 'request-1', name: '回归', mode: 'paired_regression', seed: 42,
      scenario: { scenario_id: 'standard', role_counts: { villager: 2 } },
      repetitions: 2,
      baseline: { model_config_id: 'a' },
      candidate: { model_config_id: 'b' },
    });
    expect(fetchFn.mock.calls[0][0]).toBe(`${BASE}/api/benchmarks`);
    expect(fetchFn.mock.calls[0][1].method).toBe('POST');
  });
});
