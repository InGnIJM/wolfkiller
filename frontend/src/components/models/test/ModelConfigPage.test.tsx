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
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'New Model' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'sk-new' } });
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
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(createModel).not.toHaveBeenCalled();
    expect(screen.getByText('名称必填')).toBeInTheDocument();
  });

  it('rejects save when base url has no http scheme', async () => {
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'n' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'ftp://x' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(createModel).not.toHaveBeenCalled();
    expect(screen.getByText(/必须以 http/)).toBeInTheDocument();
  });

  it('edits an existing config with prefilled values', async () => {
    vi.mocked(updateModel).mockResolvedValue({ ...sample, name: 'Renamed' });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    const nameInput = screen.getByLabelText('名称') as HTMLInputElement;
    expect(nameInput.value).toBe('DeepSeek Pro');

    fireEvent.change(nameInput, { target: { value: 'Renamed' } });
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
});
