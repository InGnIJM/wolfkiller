import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  createGame, createModel, deleteModel, fetchConstraints, fetchPresets,
  fetchRoleCatalog, listModels, testModelConnection, updateModel,
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

  it('rejects non-ok responses', async () => {
    mockFetch({}, false, 500);
    await expect(listModels()).rejects.toThrow('List models failed: 500');
  });
});
