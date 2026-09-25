// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ModelConfigDialog from '../ModelConfigDialog';
import { testModelConnection } from '../../../api/client';
import type { ModelConfig } from '../../../store/types';

vi.mock('../../../api/client', () => ({
  testModelConnection: vi.fn(),
}));

const stored: ModelConfig = {
  id: 'a1',
  name: 'Gemini-3.7-Flash',
  base_url: 'http://127.0.0.1:8045/v1',
  model_id: 'gemini-3.7-flash',
  has_key: true,
  api_key_masked: 'sk-***1234',
  key_invalid: false,
  temperature: null,
  strict_base_url: null,
  provider_profile: 'auto',
  headers: {},
  created_at: '2026-08-16T00:00:00',
  updated_at: '2026-08-16T00:00:00',
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderDialog(initial: ModelConfig | null) {
  render(
    <ModelConfigDialog
      open
      initial={initial}
      onClose={() => {}}
      onSave={vi.fn(async () => {})}
    />,
  );
}

describe('ModelConfigDialog connection test payload', () => {
  it('tests the edited fields of a stored config instead of the saved ones', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    renderDialog(stored);

    fireEvent.change(screen.getByLabelText('Base URL'), {
      target: { value: 'https://edited.test/v1' },
    });
    fireEvent.change(screen.getByLabelText('模型 ID'), {
      target: { value: 'gemini-3.7-flash-tiered' },
    });
    fireEvent.click(screen.getByText('▸ 高级选项（可选）'));
    fireEvent.change(screen.getByLabelText('严格模式地址（可选，留空按厂商自动推导）'), {
      target: { value: 'https://edited-strict.test/beta' },
    });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));

    await waitFor(() => expect(testModelConnection).toHaveBeenCalledTimes(1));
    expect(testModelConnection).toHaveBeenCalledWith({
      config_id: 'a1',
      base_url: 'https://edited.test/v1',
      model_id: 'gemini-3.7-flash-tiered',
      strict_base_url: 'https://edited-strict.test/beta',
      api_key: '',
      provider_profile: 'auto',
      headers: {},
    });
  });

  it('sends an emptied strict address so a cleared field can be tested', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    renderDialog({ ...stored, strict_base_url: 'https://stored-strict.test/beta' });

    fireEvent.click(screen.getByText('▸ 高级选项（可选）'));
    fireEvent.change(screen.getByLabelText('严格模式地址（可选，留空按厂商自动推导）'), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));

    await waitFor(() => expect(testModelConnection).toHaveBeenCalledTimes(1));
    expect(testModelConnection).toHaveBeenCalledWith(
      expect.objectContaining({ config_id: 'a1', strict_base_url: '' }),
    );
  });

  it('keeps testing the form values for a brand new config', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    renderDialog(null);

    fireEvent.change(screen.getByLabelText('名称'), {
      target: { value: 'Fresh' },
    });
    fireEvent.change(screen.getByLabelText('Base URL'), {
      target: { value: 'https://fresh.test/v1' },
    });
    fireEvent.change(screen.getByLabelText('模型 ID'), {
      target: { value: 'fresh-model' },
    });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));

    await waitFor(() => expect(testModelConnection).toHaveBeenCalledTimes(1));
    expect(testModelConnection).toHaveBeenCalledWith({
      base_url: 'https://fresh.test/v1',
      model_id: 'fresh-model',
      strict_base_url: '',
      api_key: '',
      provider_profile: 'auto',
      headers: {},
    });
  });
});
