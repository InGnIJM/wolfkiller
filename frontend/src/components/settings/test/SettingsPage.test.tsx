// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import SettingsPage from '../SettingsPage';
import { fetchPromptCaptureSetting, updatePromptCaptureSetting } from '../../../api/client';
import { useAppSettingsStore } from '../../../store/appSettingsStore';

vi.mock('../../../api/client', () => ({
  fetchPromptCaptureSetting: vi.fn(),
  updatePromptCaptureSetting: vi.fn(),
}));

const SWITCH_NAME = '采集 LLM Prompt 原文';
const FOLLOW_ENV = '恢复为跟随环境变量';

beforeEach(() => {
  useAppSettingsStore.setState({ setting: null, loading: false, error: null });
  vi.mocked(fetchPromptCaptureSetting).mockResolvedValue({ enabled: true, source: 'default' });
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe('SettingsPage', () => {
  it('shows a progress indicator while the setting is loading', () => {
    vi.mocked(fetchPromptCaptureSetting).mockReturnValue(new Promise(() => {}));

    render(<SettingsPage />);

    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('renders the card title and the single switch', async () => {
    render(<SettingsPage />);

    expect(await screen.findByText('训练语料采集（LLM Prompt 原文）')).toBeInTheDocument();
    const toggle = screen.getByRole('switch', { name: SWITCH_NAME });
    expect(toggle).toBeChecked();
    expect(screen.getByText('状态：开')).toBeInTheDocument();
  });

  it.each([
    ['ui', '由本页开关控制（已忽略环境变量 PROMPT_CAPTURE_ENABLED）'],
    ['env', '由环境变量 PROMPT_CAPTURE_ENABLED 控制（后端启动时读取，改动需重启后端）'],
    ['default', '默认开启（未做任何界面设置，环境变量也未设置）'],
  ] as const)('renders the %s source subtitle', async (source, subtitle) => {
    vi.mocked(fetchPromptCaptureSetting).mockResolvedValue({ enabled: true, source });

    render(<SettingsPage />);

    expect(await screen.findByText(subtitle)).toBeInTheDocument();
  });

  it('documents all seven notes of 这个开关做什么', async () => {
    render(<SettingsPage />);

    expect(await screen.findByText('这个开关做什么')).toBeInTheDocument();
    for (const lead of [
      '1. 用途',
      '2. 写到哪',
      '3. 占多大磁盘',
      '4. 生效时机',
      '5. 关闭后有什么影响',
      '6. 会不会写进密钥',
      '7. 想立刻停掉某一局',
    ]) {
      expect(screen.getByText(lead)).toBeInTheDocument();
    }
  });

  it('states that the switch only applies to new games', async () => {
    render(<SettingsPage />);

    expect(await screen.findByText(/只对新建的对局生效。/)).toBeInTheDocument();
    expect(screen.getByText(/关掉开关后，正在进行的对局仍会继续写到本局结束。/)).toBeInTheDocument();
    expect(screen.getByText(/一个字节都不写/)).toBeInTheDocument();
  });

  it('turns capture off through the API when the switch is on', async () => {
    vi.mocked(fetchPromptCaptureSetting).mockResolvedValue({ enabled: true, source: 'ui' });
    vi.mocked(updatePromptCaptureSetting).mockResolvedValue({ enabled: false, source: 'ui' });

    render(<SettingsPage />);

    fireEvent.click(await screen.findByRole('switch', { name: SWITCH_NAME }));

    expect(updatePromptCaptureSetting).toHaveBeenCalledWith(false);
    expect(await screen.findByText('已关闭：新建的对局不再采集；正在进行的对局会在本局结束后停止。'))
      .toBeInTheDocument();
    expect(screen.getByRole('switch', { name: SWITCH_NAME })).not.toBeChecked();
    expect(screen.getByText('状态：关')).toBeInTheDocument();
  });

  it('turns capture on through the API when the switch is off', async () => {
    vi.mocked(fetchPromptCaptureSetting).mockResolvedValue({ enabled: false, source: 'env' });
    vi.mocked(updatePromptCaptureSetting).mockResolvedValue({ enabled: true, source: 'ui' });

    render(<SettingsPage />);

    fireEvent.click(await screen.findByRole('switch', { name: SWITCH_NAME }));

    expect(updatePromptCaptureSetting).toHaveBeenCalledWith(true);
    expect(await screen.findByText('已开启：从下一个新建的对局开始采集。')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: SWITCH_NAME })).toBeChecked();
  });

  it('hides the follow-env reset unless the source is ui', async () => {
    vi.mocked(fetchPromptCaptureSetting).mockResolvedValue({ enabled: true, source: 'env' });

    render(<SettingsPage />);

    expect(await screen.findByText(/由环境变量 PROMPT_CAPTURE_ENABLED 控制/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: FOLLOW_ENV })).toBeNull();
  });

  it('clears the UI override with null when following the environment again', async () => {
    vi.mocked(fetchPromptCaptureSetting).mockResolvedValue({ enabled: true, source: 'ui' });
    vi.mocked(updatePromptCaptureSetting).mockResolvedValue({ enabled: false, source: 'env' });

    render(<SettingsPage />);

    fireEvent.click(await screen.findByRole('button', { name: FOLLOW_ENV }));

    expect(updatePromptCaptureSetting).toHaveBeenCalledWith(null);
    expect(await screen.findByText('已恢复为跟随环境变量 PROMPT_CAPTURE_ENABLED（当前：关）。'))
      .toBeInTheDocument();
    expect(screen.queryByRole('button', { name: FOLLOW_ENV })).toBeNull();
  });

  it('renders an error alert when loading fails', async () => {
    vi.mocked(fetchPromptCaptureSetting).mockRejectedValue(
      new Error('Fetch prompt capture setting failed: 500'),
    );

    render(<SettingsPage />);

    expect(await screen.findByText('Fetch prompt capture setting failed: 500')).toBeInTheDocument();
    expect(screen.queryByRole('switch', { name: SWITCH_NAME })).toBeNull();
  });

  it('reports a failed write without claiming success', async () => {
    vi.mocked(fetchPromptCaptureSetting).mockResolvedValue({ enabled: true, source: 'ui' });
    vi.mocked(updatePromptCaptureSetting).mockRejectedValue(
      new Error('Update prompt capture setting failed: 422'),
    );

    render(<SettingsPage />);

    fireEvent.click(await screen.findByRole('switch', { name: SWITCH_NAME }));

    expect(await screen.findByText('Update prompt capture setting failed: 422')).toBeInTheDocument();
    expect(screen.queryByText(/已关闭：/)).toBeNull();
  });
});
