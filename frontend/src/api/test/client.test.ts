import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  fetchBenchmarkGames, fetchBenchmarkReport, createBenchmark, createGame, createModel, deleteGame, deleteModel,
  fetchAudienceEvents, fetchAudienceSnapshot, fetchConstraints, fetchPresets,
  fetchRoleCatalog, getWsUrl, listBenchmarks, listModels, renameGame,
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
