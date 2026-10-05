import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useAppSettingsStore } from '../appSettingsStore';
import { fetchPromptCaptureSetting, updatePromptCaptureSetting } from '../../api/client';
import type { PromptCaptureSetting } from '../types';

vi.mock('../../api/client', () => ({
  fetchPromptCaptureSetting: vi.fn(),
  updatePromptCaptureSetting: vi.fn(),
}));

const uiOn: PromptCaptureSetting = { enabled: true, source: 'ui' };
const envOff: PromptCaptureSetting = { enabled: false, source: 'env' };

beforeEach(() => {
  useAppSettingsStore.setState({ setting: null, loading: false, error: null });
});

describe('appSettingsStore', () => {
  it('loads the effective setting and clears loading', async () => {
    vi.mocked(fetchPromptCaptureSetting).mockResolvedValue(uiOn);
    await useAppSettingsStore.getState().load();

    expect(useAppSettingsStore.getState().setting).toEqual(uiOn);
    expect(useAppSettingsStore.getState().loading).toBe(false);
    expect(useAppSettingsStore.getState().error).toBeNull();
  });

  it('marks loading while the request is in flight', async () => {
    let resolveLoad: (value: PromptCaptureSetting) => void = () => {};
    vi.mocked(fetchPromptCaptureSetting).mockReturnValue(
      new Promise((resolve) => { resolveLoad = resolve; }),
    );

    const pending = useAppSettingsStore.getState().load();
    expect(useAppSettingsStore.getState().loading).toBe(true);

    resolveLoad(envOff);
    await pending;
    expect(useAppSettingsStore.getState().loading).toBe(false);
  });

  it('stores load failures and keeps the setting untouched', async () => {
    vi.mocked(fetchPromptCaptureSetting).mockRejectedValue(new Error('Fetch prompt capture setting failed: 500'));
    await useAppSettingsStore.getState().load();

    expect(useAppSettingsStore.getState().error).toBe('Fetch prompt capture setting failed: 500');
    expect(useAppSettingsStore.getState().setting).toBeNull();
    expect(useAppSettingsStore.getState().loading).toBe(false);
  });

  it('enables capture and writes the response back', async () => {
    vi.mocked(updatePromptCaptureSetting).mockResolvedValue(uiOn);

    expect(await useAppSettingsStore.getState().setEnabled(true)).toEqual(uiOn);
    expect(updatePromptCaptureSetting).toHaveBeenCalledWith(true);
    expect(useAppSettingsStore.getState().setting).toEqual(uiOn);
    expect(useAppSettingsStore.getState().error).toBeNull();
  });

  it('disables capture and writes the response back', async () => {
    useAppSettingsStore.setState({ setting: uiOn });
    const disabled: PromptCaptureSetting = { enabled: false, source: 'ui' };
    vi.mocked(updatePromptCaptureSetting).mockResolvedValue(disabled);

    expect(await useAppSettingsStore.getState().setEnabled(false)).toEqual(disabled);
    expect(updatePromptCaptureSetting).toHaveBeenCalledWith(false);
    expect(useAppSettingsStore.getState().setting).toEqual(disabled);
  });

  it('passes null to clear the UI override and follows the environment again', async () => {
    useAppSettingsStore.setState({ setting: uiOn });
    vi.mocked(updatePromptCaptureSetting).mockResolvedValue(envOff);

    expect(await useAppSettingsStore.getState().setEnabled(null)).toEqual(envOff);
    expect(updatePromptCaptureSetting).toHaveBeenCalledWith(null);
    expect(useAppSettingsStore.getState().setting).toEqual(envOff);
  });

  it('returns null and stores the error when the write fails', async () => {
    useAppSettingsStore.setState({ setting: envOff });
    vi.mocked(updatePromptCaptureSetting).mockRejectedValue(new Error('Update prompt capture setting failed: 422'));

    expect(await useAppSettingsStore.getState().setEnabled(true)).toBeNull();
    expect(useAppSettingsStore.getState().error).toBe('Update prompt capture setting failed: 422');
    expect(useAppSettingsStore.getState().setting).toEqual(envOff);
  });

  it('stringifies non-Error rejections', async () => {
    vi.mocked(fetchPromptCaptureSetting).mockRejectedValue('offline');
    await useAppSettingsStore.getState().load();

    expect(useAppSettingsStore.getState().error).toBe('offline');
  });
});
