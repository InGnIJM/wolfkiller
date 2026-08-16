import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useModelConfigStore } from '../modelConfigStore';
import { createModel, deleteModel, listModels, updateModel } from '../../api/client';

vi.mock('../../api/client', () => ({
  listModels: vi.fn(),
  createModel: vi.fn(),
  updateModel: vi.fn(),
  deleteModel: vi.fn(),
}));

const sample = {
  id: 'a',
  name: 'n',
  base_url: 'https://x',
  model_id: 'm',
  has_key: false,
  api_key_masked: null,
  key_invalid: false,
  temperature: null,
  strict_base_url: null,
  created_at: '',
  updated_at: '',
};

const input = { name: 'n', base_url: 'https://x', model_id: 'm' };

beforeEach(() => {
  useModelConfigStore.setState({ configs: [], loading: false, error: null });
});

describe('modelConfigStore', () => {
  it('loads configs and clears loading', async () => {
    vi.mocked(listModels).mockResolvedValue([sample]);
    await useModelConfigStore.getState().load();
    expect(useModelConfigStore.getState().configs).toEqual([sample]);
    expect(useModelConfigStore.getState().loading).toBe(false);
  });

  it('stores load errors', async () => {
    vi.mocked(listModels).mockRejectedValue(new Error('boom'));
    await useModelConfigStore.getState().load();
    expect(useModelConfigStore.getState().error).toBe('boom');
    expect(useModelConfigStore.getState().loading).toBe(false);
  });

  it('appends created configs and clears error', async () => {
    useModelConfigStore.setState({ error: 'stale' });
    vi.mocked(createModel).mockResolvedValue(sample);
    const result = await useModelConfigStore.getState().create(input);
    expect(result).toEqual(sample);
    expect(useModelConfigStore.getState().configs).toEqual([sample]);
    expect(useModelConfigStore.getState().error).toBeNull();
  });

  it('returns null and stores error on create failure', async () => {
    vi.mocked(createModel).mockRejectedValue(new Error('dup'));
    const result = await useModelConfigStore.getState().create(input);
    expect(result).toBeNull();
    expect(useModelConfigStore.getState().error).toBe('dup');
  });

  it('replaces updated configs', async () => {
    useModelConfigStore.setState({ configs: [sample] });
    const updated = { ...sample, name: 'n2' };
    vi.mocked(updateModel).mockResolvedValue(updated);
    await useModelConfigStore.getState().update('a', input);
    expect(useModelConfigStore.getState().configs[0].name).toBe('n2');
  });

  it('returns null and stores error on update failure', async () => {
    useModelConfigStore.setState({ configs: [sample] });
    vi.mocked(updateModel).mockRejectedValue(new Error('x'));
    expect(await useModelConfigStore.getState().update('a', input)).toBeNull();
    expect(useModelConfigStore.getState().error).toBe('x');
  });

  it('removes deleted configs', async () => {
    useModelConfigStore.setState({ configs: [sample] });
    vi.mocked(deleteModel).mockResolvedValue(undefined);
    expect(await useModelConfigStore.getState().remove('a')).toBe(true);
    expect(useModelConfigStore.getState().configs).toEqual([]);
  });

  it('returns false and stores error on delete failure', async () => {
    useModelConfigStore.setState({ configs: [sample] });
    vi.mocked(deleteModel).mockRejectedValue(new Error('x'));
    expect(await useModelConfigStore.getState().remove('a')).toBe(false);
    expect(useModelConfigStore.getState().error).toBe('x');
  });
});
