// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ModelConfigDialog from '../ModelConfigDialog';
import { testModelConnection } from '../../../api/client';
import type { ModelConfig } from '../../../store/types';

vi.mock('../../../api/client', () => ({
  testModelConnection: vi.fn(),
  fetchModelCatalog: vi.fn(),
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
    fireEvent.change(screen.getByLabelText('模型 ID 1'), {
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
      reasoning_effort: '',
      headers: {},
    });
  });

  it('tests and saves the selected reasoning effort', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    const onSave = vi.fn(async () => {});
    render(
      <ModelConfigDialog open initial={stored} onClose={() => {}} onSave={onSave} />,
    );

    fireEvent.click(screen.getByText('▸ 高级选项（可选）'));
    fireEvent.change(screen.getByLabelText('思考强度'), {
      target: { value: 'none' },
    });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));

    await waitFor(() => expect(testModelConnection).toHaveBeenCalledTimes(1));
    expect(testModelConnection).toHaveBeenCalledWith(
      expect.objectContaining({ config_id: 'a1', reasoning_effort: 'none' }),
    );

    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ reasoning_effort: 'none' }),
    );
  });

  it('shrinks the label so it never overlaps the default option text', async () => {
    renderDialog(stored);

    fireEvent.click(screen.getByText('▸ 高级选项（可选）'));
    // The select's empty option carries visible text, so a floating label
    // that stays inside the field would paint on top of it.
    expect(
      screen.getByText('思考强度', { selector: 'label' }),
    ).toHaveClass('MuiInputLabel-shrink');
  });

  it('prefills a stored effort and sends an empty value when it is cleared', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    renderDialog({ ...stored, reasoning_effort: 'high' });

    fireEvent.click(screen.getByText('▸ 高级选项（可选）'));
    expect(screen.getByLabelText('思考强度')).toHaveValue('high');
    fireEvent.change(screen.getByLabelText('思考强度'), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));

    await waitFor(() => expect(testModelConnection).toHaveBeenCalledTimes(1));
    expect(testModelConnection).toHaveBeenCalledWith(
      expect.objectContaining({ config_id: 'a1', reasoning_effort: '' }),
    );
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
    fireEvent.change(screen.getByLabelText('模型 ID 1'), {
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
      reasoning_effort: '',
      headers: {},
    });
  });
});


describe('model list editor', () => {
  it('saves the model rows as model_ids with the first as default', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    const onSave = vi.fn(async () => {});
    render(
      <ModelConfigDialog open initial={null} onClose={() => {}} onSave={onSave} />,
    );

    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'Go' } });
    fireEvent.change(screen.getByLabelText('Base URL'), {
      target: { value: 'https://opencode.ai/zen/go/v1' },
    });
    fireEvent.change(screen.getByLabelText('模型 ID 1'), {
      target: { value: 'deepseek-v4.1-flash' },
    });
    fireEvent.click(screen.getByRole('button', { name: '添加模型' }));
    fireEvent.change(screen.getByLabelText('模型 ID 2'), {
      target: { value: 'mimo-v2.6-flash' },
    });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(testModelConnection).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      model_id: 'deepseek-v4.1-flash',
      model_ids: ['deepseek-v4.1-flash', 'mimo-v2.6-flash'],
    })));
    expect(testModelConnection).toHaveBeenCalledWith(expect.objectContaining({
      model_id: 'deepseek-v4.1-flash',
    }));
  });

  it('prefills rows from a stored config model list', () => {
    renderDialog({ ...stored, model_ids: ['m-a', 'm-b', 'm-c'] });

    expect(screen.getByLabelText('模型 ID 1')).toHaveValue('m-a');
    expect(screen.getByLabelText('模型 ID 2')).toHaveValue('m-b');
    expect(screen.getByLabelText('模型 ID 3')).toHaveValue('m-c');
  });

  it('merges fetched upstream models into the rows', async () => {
    const { fetchModelCatalog } = await import('../../../api/client');
    vi.mocked(fetchModelCatalog).mockResolvedValue({
      model_ids: ['deepseek-v4.1-flash', 'mimo-v2.6-flash'], error: null,
    });
    renderDialog(stored);

    fireEvent.click(screen.getByRole('button', { name: '从供应商拉取' }));

    await waitFor(() => expect(fetchModelCatalog).toHaveBeenCalledWith(
      expect.objectContaining({
        config_id: 'a1', base_url: 'http://127.0.0.1:8045/v1',
      }),
    ));
    await waitFor(() => {
      expect(screen.getByLabelText('模型 ID 2')).toHaveValue('deepseek-v4.1-flash');
      expect(screen.getByLabelText('模型 ID 3')).toHaveValue('mimo-v2.6-flash');
    });
  });

  it('blocks save when every model row is blank', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    const onSave = vi.fn(async () => {});
    render(
      <ModelConfigDialog open initial={null} onClose={() => {}} onSave={onSave} />,
    );
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'Go' } });
    fireEvent.change(screen.getByLabelText('Base URL'), {
      target: { value: 'https://x/v1' },
    });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));

    await waitFor(() => expect(testModelConnection).not.toHaveBeenCalled());
    expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
  });
});
