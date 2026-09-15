// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import ModelConfigPage from '../ModelConfigPage';
import {
  createModel, deleteModel, listModels, testModelConnection, updateModel,
} from '../../../api/client';

vi.mock('../../../api/client', () => ({
  listModels: vi.fn(),
  createModel: vi.fn(),
  updateModel: vi.fn(),
  deleteModel: vi.fn(),
  testModelConnection: vi.fn(),
}));

const sample = {
  id: 'a1',
  name: 'DeepSeek Pro',
  base_url: 'https://api.deepseek.com/v1',
  model_id: 'deepseek-v4-pro',
  has_key: true,
  api_key_masked: 'sk-***1234',
  key_invalid: false,
  temperature: 1.2,
  strict_base_url: null,
  provider_profile: 'auto' as const,
  created_at: '2026-08-16T00:00:00',
  updated_at: '2026-08-16T00:00:00',
};

beforeEach(() => {
  vi.mocked(listModels).mockResolvedValue([sample]);
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe('ModelConfigPage', () => {
  it('loads and renders configs on mount', async () => {
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());
    expect(screen.getByText(/sk-\*\*\*1234/)).toBeInTheDocument();
  });

  it('renders the empty state when there are no configs', async () => {
    vi.mocked(listModels).mockResolvedValue([]);
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('暂无模型配置')).toBeInTheDocument());
  });

  it('creates a new config through the dialog', async () => {
    vi.mocked(createModel).mockResolvedValue({ ...sample, id: 'b2', name: 'New Model' });
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'New Model' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'sk-new' } });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByText(/连接成功/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(createModel).toHaveBeenCalledWith(expect.objectContaining({
        name: 'New Model', api_key: 'sk-new',
      })),
    );
  });

  it('rejects save when name is blank', async () => {
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.blur(screen.getByLabelText('名称'));

    expect(screen.getByText('名称必填')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
    expect(createModel).not.toHaveBeenCalled();
    expect(testModelConnection).not.toHaveBeenCalled();
  });

  it('rejects save when base url has no http scheme', async () => {
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'n' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'ftp://x' } });
    fireEvent.blur(screen.getByLabelText('Base URL'));

    expect(screen.getByText(/必须以 http/)).toBeInTheDocument();
    expect(createModel).not.toHaveBeenCalled();
  });

  it('edits an existing config with prefilled values', async () => {
    vi.mocked(updateModel).mockResolvedValue({ ...sample, name: 'Renamed' });
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    const nameInput = screen.getByLabelText('名称') as HTMLInputElement;
    expect(nameInput.value).toBe('DeepSeek Pro');

    fireEvent.change(nameInput, { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByText(/连接成功/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(updateModel).toHaveBeenCalledWith('a1', expect.objectContaining({
        name: 'Renamed', api_key: '',
      })),
    );
  });

  it('deletes a config', async () => {
    vi.mocked(deleteModel).mockResolvedValue(undefined);
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '删除' }));

    await waitFor(() => expect(deleteModel).toHaveBeenCalledWith('a1'));
  });

  it('tests connection for a stored config and shows the result', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 42, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '测试' }));

    await waitFor(() => expect(screen.getByText(/连接成功（42ms）/)).toBeInTheDocument());
    expect(testModelConnection).toHaveBeenCalledWith({ config_id: 'a1' });
  });

  it('shows failure result for a failed connection test', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: false, latency_ms: null, error: 'TimeoutError' });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '测试' }));

    await waitFor(() => expect(screen.getByText(/连接失败：TimeoutError/)).toBeInTheDocument());
  });

  it('dialog tests connection in edit mode with config_id and entered key', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 7, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'sk-typed' } });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));

    await waitFor(() =>
      expect(testModelConnection).toHaveBeenCalledWith({
        config_id: 'a1', api_key: 'sk-typed', provider_profile: 'auto',
      }),
    );
  });

  it('dialog tests connection in create mode with form fields', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 7, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'n' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'sk-f' } });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));

    await waitFor(() =>
      expect(testModelConnection).toHaveBeenCalledWith({
        base_url: 'https://x/v1', api_key: 'sk-f', model_id: 'm', provider_profile: 'auto',
      }),
    );
  });

  it('dialog tests and saves an explicitly selected Anthropic protocol', async () => {
    vi.mocked(createModel).mockResolvedValue({
      ...sample, id: 'c1', name: 'Claude', provider_profile: 'anthropic',
    });
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 9, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('接口协议'), { target: { value: 'anthropic' } });
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'Claude' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://api.anthropic.com' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'claude-sonnet-4-5' } });
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'sk-ant' } });

    expect(screen.getByText(/Anthropic Messages API：Base URL 不含/)).toBeInTheDocument();
    expect(screen.getByLabelText('Base URL')).toHaveAttribute('placeholder', 'https://api.anthropic.com');

    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() =>
      expect(testModelConnection).toHaveBeenCalledWith({
        base_url: 'https://api.anthropic.com', api_key: 'sk-ant',
        model_id: 'claude-sonnet-4-5', provider_profile: 'anthropic',
      }),
    );
    await waitFor(() => expect(screen.getByText(/连接成功/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(createModel).toHaveBeenCalledWith(expect.objectContaining({
        name: 'Claude', provider_profile: 'anthropic',
      })),
    );
  });

  it('dialog re-disables save when the protocol changes after a successful test', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'n' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).toBeEnabled());

    fireEvent.change(screen.getByLabelText('接口协议'), { target: { value: 'custom-anthropic' } });

    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  });

  it('edit dialog prefills the stored protocol and sends it with the config test', async () => {
    vi.mocked(listModels).mockResolvedValue([
      { ...sample, provider_profile: 'custom-anthropic', name: 'Relay Claude' },
    ]);
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 7, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('Relay Claude')).toBeInTheDocument());
    expect(screen.getByText(/自定义 · Anthropic 兼容/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    expect((screen.getByLabelText('接口协议') as HTMLSelectElement).value).toBe('custom-anthropic');

    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() =>
      expect(testModelConnection).toHaveBeenCalledWith({
        config_id: 'a1', api_key: '', provider_profile: 'custom-anthropic',
      }),
    );
  });

  it('dialog saves advanced options as number and string', async () => {
    vi.mocked(createModel).mockResolvedValue({ ...sample, id: 'b3', name: 'Adv' });
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'Adv' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.click(screen.getByRole('button', { name: /高级选项/ }));
    fireEvent.change(screen.getByLabelText(/Temperature/), { target: { value: '0.5' } });
    fireEvent.change(screen.getByLabelText(/严格模式地址/), { target: { value: 'https://x/beta' } });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByText(/连接成功/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(createModel).toHaveBeenCalledWith(expect.objectContaining({
        temperature: 0.5, strict_base_url: 'https://x/beta',
      })),
    );
  });

  it('dialog saves blank advanced options as nulls', async () => {
    vi.mocked(createModel).mockResolvedValue({ ...sample, id: 'b4', name: 'NoAdv' });
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'NoAdv' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.click(screen.getByRole('button', { name: /高级选项/ }));
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByText(/连接成功/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(createModel).toHaveBeenCalledWith(expect.objectContaining({
        temperature: null, strict_base_url: null,
      })),
    );
  });

  it('disables save until the connection test passes', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'n' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });

    expect(screen.getByText('保存前需通过连接测试')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByText(/连接成功/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled();
  });

  it('re-disables save when a connectivity field changes after a successful test', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'n' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).toBeEnabled());

    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm2' } });

    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  });
});
