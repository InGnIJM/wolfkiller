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
  headers: {},
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

/** Header-editor rows carry `aria-label` so they do not collide with the config 名称 field. */
function headerNames(): HTMLInputElement[] {
  return screen.queryAllByLabelText('请求头名称') as HTMLInputElement[];
}

function headerValues(): HTMLInputElement[] {
  return screen.queryAllByLabelText('请求头值') as HTMLInputElement[];
}

/** The helper text under a header row; `名称必填` also exists for the config name field. */
function headerHelperText(index: number): string {
  const rows = headerNames();
  const row = rows[index]?.closest('.MuiStack-root');
  return row?.querySelector('.MuiFormHelperText-root')?.textContent ?? '';
}

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
        config_id: 'a1', base_url: 'https://api.deepseek.com/v1',
        model_id: 'deepseek-v4-pro', strict_base_url: '',
        api_key: 'sk-typed', provider_profile: 'auto', headers: {},
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
        base_url: 'https://x/v1', model_id: 'm', strict_base_url: '',
        api_key: 'sk-f', provider_profile: 'auto', headers: {},
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
        base_url: 'https://api.anthropic.com', model_id: 'claude-sonnet-4-5',
        strict_base_url: '', api_key: 'sk-ant', provider_profile: 'anthropic', headers: {},
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
        config_id: 'a1', base_url: 'https://api.deepseek.com/v1',
        model_id: 'deepseek-v4-pro', strict_base_url: '',
        api_key: '', provider_profile: 'custom-anthropic', headers: {},
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

  it('renders header rows loaded from the stored config', async () => {
    vi.mocked(listModels).mockResolvedValue([
      { ...sample, name: 'With Headers', headers: { 'X-Beta': '2', 'X-Alpha': '1' } },
    ]);
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('With Headers')).toBeInTheDocument());
    expect(screen.getByText(/2 个自定义请求头/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    fireEvent.click(screen.getByRole('button', { name: /高级选项/ }));

    expect(headerNames().map((input) => input.value)).toEqual(['X-Alpha', 'X-Beta']);
    expect(headerValues().map((input) => input.value)).toEqual(['1', '2']);
  });

  it('adds and removes header rows', async () => {
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.click(screen.getByRole('button', { name: /高级选项/ }));
    expect(headerNames()).toHaveLength(0);

    fireEvent.click(screen.getByRole('button', { name: '添加请求头' }));
    fireEvent.change(headerNames()[0], { target: { value: 'X-One' } });
    fireEvent.click(screen.getByRole('button', { name: '添加请求头' }));
    expect(headerNames()).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: '删除请求头 1' }));
    expect(headerNames()).toHaveLength(1);
    expect(headerNames()[0].value).toBe('');
  });

  it('blocks save on an invalid header name and clears it when fixed', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'n' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.click(screen.getByRole('button', { name: /高级选项/ }));
    fireEvent.click(screen.getByRole('button', { name: '添加请求头' }));
    fireEvent.change(headerNames()[0], { target: { value: 'X Bad Name' } });
    fireEvent.blur(headerNames()[0]);

    expect(screen.getByText('名称含非法字符')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();

    fireEvent.change(headerNames()[0], { target: { value: 'X-Good' } });
    expect(screen.queryByText('名称含非法字符')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).toBeEnabled());
  });

  it('blocks save on a reserved header name and on an empty header name', async () => {
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.click(screen.getByRole('button', { name: /高级选项/ }));
    fireEvent.click(screen.getByRole('button', { name: '添加请求头' }));
    fireEvent.blur(headerNames()[0]);
    expect(headerHelperText(0)).toBe('名称必填');

    fireEvent.change(headerNames()[0], { target: { value: 'Authorization' } });
    fireEvent.blur(headerNames()[0]);
    expect(headerHelperText(0)).toBe('该请求头由系统管理，不能自定义');
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  });

  it('flags duplicate header names', async () => {
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.click(screen.getByRole('button', { name: /高级选项/ }));
    fireEvent.click(screen.getByRole('button', { name: '添加请求头' }));
    fireEvent.click(screen.getByRole('button', { name: '添加请求头' }));

    fireEvent.change(headerNames()[0], { target: { value: 'X-Dup' } });
    fireEvent.change(headerNames()[1], { target: { value: 'x-dup' } });
    fireEvent.blur(headerNames()[1]);
    expect(headerHelperText(1)).toBe('名称重复');
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  });

  // A single-line input strips typed CR/LF, so the guard is exercised through a
  // stored value that already carries a newline.
  it('flags a header value containing a newline', async () => {
    vi.mocked(listModels).mockResolvedValue([
      { ...sample, name: 'NL', headers: { 'X-Bad': 'bad\nvalue' } },
    ]);
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('NL')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    fireEvent.click(screen.getByRole('button', { name: /高级选项/ }));
    fireEvent.blur(headerNames()[0]);

    expect(headerHelperText(0)).toBe('值不能包含换行');
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '测试连接' })).toBeDisabled();
  });

  it('sends headers in both the connection-test and save payloads', async () => {
    vi.mocked(createModel).mockResolvedValue({ ...sample, id: 'h1', name: 'Heads' });
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'Heads' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.click(screen.getByRole('button', { name: /高级选项/ }));
    fireEvent.click(screen.getByRole('button', { name: '添加请求头' }));
    fireEvent.change(headerNames()[0], { target: { value: ' X-Extra ' } });
    fireEvent.change(headerValues()[0], { target: { value: ' v ' } });

    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() =>
      expect(testModelConnection).toHaveBeenCalledWith({
        base_url: 'https://x/v1', model_id: 'm', strict_base_url: '',
        api_key: '', provider_profile: 'auto', headers: { 'X-Extra': 'v' },
      }),
    );
    await waitFor(() => expect(screen.getByText(/连接成功/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(createModel).toHaveBeenCalledWith(expect.objectContaining({
        name: 'Heads', headers: { 'X-Extra': 'v' },
      })),
    );
  });

  it('sends the stored headers with the edit-mode connection test', async () => {
    vi.mocked(listModels).mockResolvedValue([
      { ...sample, name: 'With Headers', headers: { 'X-One': '1' } },
    ]);
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 7, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('With Headers')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));

    await waitFor(() =>
      expect(testModelConnection).toHaveBeenCalledWith({
        config_id: 'a1', base_url: 'https://api.deepseek.com/v1',
        model_id: 'deepseek-v4-pro', strict_base_url: '',
        api_key: '', provider_profile: 'auto', headers: { 'X-One': '1' },
      }),
    );
  });

  it('re-disables save when the headers change after a successful test', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'n' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.click(screen.getByRole('button', { name: /高级选项/ }));
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '保存' })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: '添加请求头' }));

    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  });

  it('rejects more than 32 header rows', async () => {
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.click(screen.getByRole('button', { name: /高级选项/ }));
    for (let i = 0; i < 33; i += 1) {
      fireEvent.click(screen.getByRole('button', { name: '添加请求头' }));
    }

    expect(screen.getByText('最多 32 个请求头')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  }, 20000);

  it('exposes the OpenCode profile with its Base URL placeholder', async () => {
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('接口协议'), { target: { value: 'opencode' } });

    expect(screen.getByLabelText('Base URL'))
      .toHaveAttribute('placeholder', 'https://opencode.ai/zen/v1');
  });
});
