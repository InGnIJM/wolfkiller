import { describe, expect, it } from 'vitest';

import { seatModelLookup } from '../seatModels';
import type { ModelSnapshotEntry } from '../types';

const snapshot: ModelSnapshotEntry[] = [
  {
    config_id: null,
    name: '环境默认',
    model_id: 'env-model',
    base_url: 'https://env.example/v1',
    provider_profile: 'custom-openai',
    count: 1,
    seats: [2],
  },
  {
    config_id: 'model-a',
    name: 'Model A',
    model_id: 'provider/model-a',
    base_url: 'https://models.example/v1',
    provider_profile: 'openrouter',
    count: 2,
    seats: [1, 3],
  },
];

describe('seatModelLookup', () => {
  it('maps each seat to the credential-free model identity', () => {
    expect(seatModelLookup(snapshot)).toEqual({
      1: { name: 'Model A', model_id: 'provider/model-a', provider_profile: 'openrouter' },
      2: { name: '环境默认', model_id: 'env-model', provider_profile: 'custom-openai' },
      3: { name: 'Model A', model_id: 'provider/model-a', provider_profile: 'openrouter' },
    });
  });

  it('skips legacy rows without seats and empty snapshots', () => {
    expect(seatModelLookup(undefined)).toEqual({});
    expect(seatModelLookup([])).toEqual({});
    expect(seatModelLookup([
      { ...snapshot[0], seats: [] },
    ])).toEqual({});
  });
});
