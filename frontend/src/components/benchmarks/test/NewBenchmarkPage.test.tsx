// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../../api/client';
import type { GamePreset, ModelConfig } from '../../../store/types';
import NewBenchmarkPage from '../NewBenchmarkPage';

const mocks = vi.hoisted(() => ({
  configs: [] as ModelConfig[],
  loadModels: vi.fn(),
  createDraft: vi.fn(),
}));

vi.mock('../../../api/client', () => ({ fetchPresets: vi.fn() }));
vi.mock('../../../store/modelConfigStore', () => ({
  useModelConfigStore: () => ({ configs: mocks.configs, load: mocks.loadModels, loading: false }),
}));
vi.mock('../../../store/benchmarkStore', () => ({
  useBenchmarkStore: () => ({ createDraft: mocks.createDraft, actionPending: false, error: null }),
}));

const NINE_PLAYER: GamePreset = {
  id: 'nine-player-standard',
  name: '九人标准场',
  description: '3狼 3民 预女猎',
  role_counts: {
    'wolf-killer-werewolf': 3, 'wolf-killer-villager': 3, 'wolf-killer-seer': 1,
    'wolf-killer-witch': 1, 'wolf-killer-hunter': 1,
  },
  enable_sheriff: false,
  recommended_timeout_seconds: 3600,
};

const TWELVE_PLAYER: GamePreset = {
  id: 'twelve-player-wolf-beauty',
  name: '十二人狼美人',
  description: '3狼 1狼美人 4民 预女猎守',
  role_counts: {
    'wolf-killer-werewolf': 3, 'wolf-killer-wolf-beauty': 1, 'wolf-killer-villager': 4,
    'wolf-killer-seer': 1, 'wolf-killer-witch': 1, 'wolf-killer-hunter': 1, 'wolf-killer-guard': 1,
  },
  enable_sheriff: true,
  recommended_timeout_seconds: 7200,
};

const PRESETS: GamePreset[] = [NINE_PLAYER, TWELVE_PLAYER];

const BASE_MODEL = {
  base_url: 'https://api.example.com/v1',
  model_id: 'example-model',
  has_key: true,
  api_key_masked: null,
  key_invalid: false,
  temperature: null,
  strict_base_url: null,
  provider_profile: 'auto' as const,
  headers: {},
  created_at: '',
  updated_at: '',
};

function model(id: string, name: string, avgMs: number | null, samples = 1000): ModelConfig {
  return {
    ...BASE_MODEL,
    id,
    name,
    model_id: `${id}-model`,
    latency: avgMs === null ? null : { samples, avg_ms: avgMs, p50_ms: avgMs, p90_ms: avgMs * 2 },
  };
}

/** 慢模型：平均 32 秒，与 12 人板 136 次调用相乘后逼近默认超时。 */
const SLOW_MODEL = model('slow', '慢模型', 32000);
const FAST_MODEL = model('fast', '快模型', 7000);
/** 没有延迟数据（旧响应为 null）。 */
const PLAIN_MODEL = model('plain', '无数据模型', null);
/** 有延迟行但样本数为 0，按契约不参与平均。 */
const EMPTY_SAMPLE_MODEL = model('empty', '零样本模型', 1000, 0);

beforeEach(() => {
  mocks.configs = [];
  vi.mocked(api.fetchPresets).mockResolvedValue(PRESETS);
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

let view: ReturnType<typeof render>;

function renderPage() {
  view = render(<MemoryRouter><NewBenchmarkPage /></MemoryRouter>);
}

/**
 * 表单控件的查询限定在页面容器内：MUI 的下拉菜单挂在 body 的 portal 上并复用同一个
 * labelId（`<ul role="listbox" aria-labelledby>`），菜单一旦打开过就会常驻，
 * 全局按 label 查会同时命中弹层。
 */
function inPage() {
  return within(view.container);
}

async function waitForPresets() {
  await waitFor(() => expect(inPage().getByLabelText('角色场景')).toHaveTextContent('九人标准场'));
}

function timeoutInput(): HTMLInputElement {
  return inPage().getByLabelText(/单局活动超时/) as HTMLInputElement;
}

function setTimeoutSeconds(seconds: number) {
  fireEvent.change(timeoutInput(), { target: { value: String(seconds) } });
}

async function selectPreset(name: string) {
  fireEvent.mouseDown(inPage().getByLabelText('角色场景'));
  fireEvent.click(await screen.findByRole('option', { name }));
}

async function selectModels(...names: RegExp[]) {
  fireEvent.mouseDown(inPage().getByLabelText('参赛模型'));
  for (const name of names) {
    fireEvent.click(await screen.findByRole('option', { name }));
  }
}

/** 预估提示 Alert 的根节点；用文案定位以免依赖无障碍树（打开弹层后页面会被标记 aria-hidden）。 */
function estimateAlert(text: RegExp): HTMLElement {
  const node = screen.getByText(text).closest('.MuiAlert-root');
  if (!node) throw new Error('未找到预估时长的 Alert');
  return node as HTMLElement;
}

describe('单局超时默认值与预设联动', () => {
  it('默认渲染 7200 秒', async () => {
    renderPage();
    expect(timeoutInput()).toHaveValue(7200);
    await waitForPresets();
    // 九人板推荐 3600 低于默认值，首次加载不应把它调小
    expect(timeoutInput()).toHaveValue(7200);
  });

  it('切换到 12 人预设后采用推荐值 7200 秒', async () => {
    renderPage();
    await waitForPresets();
    await selectPreset(TWELVE_PLAYER.name);
    expect(inPage().getByLabelText('角色场景')).toHaveTextContent('十二人狼美人');
    expect(timeoutInput()).toHaveValue(7200);
  });

  it('切换预设只升不降：手动调小后被抬回推荐值，换回低推荐预设也不回落', async () => {
    renderPage();
    await waitForPresets();
    setTimeoutSeconds(1800);
    expect(timeoutInput()).toHaveValue(1800);

    await selectPreset(TWELVE_PLAYER.name);
    expect(timeoutInput()).toHaveValue(7200);

    // 九人板推荐 3600，低于当前值，不应被调小
    await selectPreset(NINE_PLAYER.name);
    expect(timeoutInput()).toHaveValue(7200);
  });

  it('推荐值缺失、非正或非整数时保持用户填写的超时', async () => {
    vi.mocked(api.fetchPresets).mockResolvedValue([
      { ...NINE_PLAYER, recommended_timeout_seconds: undefined },
      { ...TWELVE_PLAYER, recommended_timeout_seconds: 0 },
      { ...TWELVE_PLAYER, id: 'twelve-fractional', name: '十二人小数', recommended_timeout_seconds: 3600.5 },
    ]);
    renderPage();
    await waitFor(() => expect(inPage().getByLabelText('角色场景')).toHaveTextContent('九人标准场'));

    await selectPreset('十二人狼美人');
    expect(timeoutInput()).toHaveValue(7200);
    await selectPreset('十二人小数');
    expect(timeoutInput()).toHaveValue(7200);
  });
});

describe('预估单局时长预警', () => {
  it('按人数与警长拟合调用次数，并在超过超时 75% 时升级为 warning', async () => {
    // 平均 40 秒：九人无警长 90 次调用约 78 分钟（info），十二人开警长 136 次约 118 分钟（接近 120）
    mocks.configs = [model('m40', '中速模型', 40000)];
    renderPage();
    await waitForPresets();
    await selectModels(/中速模型/);
    expect(estimateAlert(/预计单局约 78 分钟/))
      .toHaveTextContent('预计单局约 78 分钟（单局超时 120 分钟）');

    await selectPreset(TWELVE_PLAYER.name);
    const alert = estimateAlert(/预计单局约 118 分钟/);
    expect(alert).toHaveClass('MuiAlert-colorWarning');
    expect(alert).toHaveTextContent('预计单局约 118 分钟，接近单局超时 120 分钟，建议调大超时');
  });

  it('慢模型配 12 人开警长板：默认超时下提示接近超时', async () => {
    mocks.configs = [SLOW_MODEL];
    renderPage();
    await waitForPresets();
    await selectPreset(TWELVE_PLAYER.name);
    await selectModels(/慢模型/);

    // 136 次调用 × 32 秒 × 1.3 ≈ 94 分钟，超过 120 分钟的 75%（90 分钟）
    const alert = estimateAlert(/预计单局约 94 分钟/);
    expect(alert).toHaveClass('MuiAlert-colorWarning');
    expect(alert).toHaveTextContent('预计单局约 94 分钟，接近单局超时 120 分钟，建议调大超时');
  });

  it('把超时调回 3600 秒后同一模型判定为几乎必定超时', async () => {
    mocks.configs = [SLOW_MODEL];
    renderPage();
    await waitForPresets();
    await selectPreset(TWELVE_PLAYER.name);
    setTimeoutSeconds(3600);
    await selectModels(/慢模型/);

    const alert = estimateAlert(/已超过单局超时 60 分钟/);
    expect(alert).toHaveClass('MuiAlert-colorError');
    expect(alert).toHaveTextContent('预计单局约 94 分钟，已超过单局超时 60 分钟，几乎必定超时');
  });

  it('快模型只显示 info 级预估', async () => {
    mocks.configs = [FAST_MODEL];
    renderPage();
    await waitForPresets();
    await selectPreset(TWELVE_PLAYER.name);
    await selectModels(/快模型/);

    // 136 次调用 × 7 秒 × 1.3 ≈ 21 分钟，远低于 120 分钟
    const alert = estimateAlert(/预计单局约 21 分钟/);
    expect(alert).toHaveClass('MuiAlert-colorInfo');
    expect(alert).toHaveTextContent('预计单局约 21 分钟（单局超时 120 分钟）');
  });

  it('多模型取算术平均而不是最大值', async () => {
    mocks.configs = [SLOW_MODEL, FAST_MODEL];
    renderPage();
    await waitForPresets();
    await selectModels(/慢模型/, /快模型/);

    // 九人无警长 90 次调用 × 平均 19.5 秒 × 1.3 ≈ 38 分钟（取最大值会是 62 分钟）
    expect(estimateAlert(/预计单局约 38 分钟/))
      .toHaveTextContent('预计单局约 38 分钟（单局超时 120 分钟）');
  });

  it('样本数为 0 的模型不参与平均', async () => {
    mocks.configs = [SLOW_MODEL, EMPTY_SAMPLE_MODEL];
    renderPage();
    await waitForPresets();
    await selectModels(/慢模型/, /零样本模型/);

    // 只统计慢模型：90 次调用 × 32 秒 × 1.3 = 62 分钟
    expect(estimateAlert(/预计单局约 62 分钟/))
      .toHaveTextContent('预计单局约 62 分钟（单局超时 120 分钟）');
  });

  it('未选模型或模型没有延迟数据时不给预估数字', async () => {
    mocks.configs = [PLAIN_MODEL];
    renderPage();
    await waitForPresets();
    expect(estimateAlert(/暂无该模型的历史延迟数据，无法预估单局时长/))
      .toHaveClass('MuiAlert-colorInfo');

    await selectModels(/无数据模型/);
    expect(estimateAlert(/暂无该模型的历史延迟数据，无法预估单局时长/))
      .toHaveClass('MuiAlert-colorInfo');
  });

  it('超时 75% 是开区间：低于阈值 174 毫秒仍是 info，高 2.4 毫秒即 warning', async () => {
    // 十二人开警长 136 次调用，7200 秒的 75% 恰好是 5_400_000 毫秒
    mocks.configs = [model('edge-low', '临界下模型', 30542), model('edge-high', '临界上模型', 30543)];

    renderPage();
    await waitForPresets();
    await selectPreset(TWELVE_PLAYER.name);
    await selectModels(/临界下模型/);
    expect(estimateAlert(/预计单局约 90 分钟/))
      .toHaveTextContent('预计单局约 90 分钟（单局超时 120 分钟）');

    cleanup();
    renderPage();
    await waitForPresets();
    await selectPreset(TWELVE_PLAYER.name);
    await selectModels(/临界上模型/);
    expect(estimateAlert(/预计单局约 90 分钟/))
      .toHaveTextContent('预计单局约 90 分钟，接近单局超时 120 分钟，建议调大超时');
  });

  it('超时判定的两侧：8_999_120 毫秒仍是 warning，9_000_888 毫秒即 error', async () => {
    mocks.configs = [model('near-low', '近超时下模型', 50900), model('near-high', '近超时上模型', 50910)];

    renderPage();
    await waitForPresets();
    await selectPreset(TWELVE_PLAYER.name);
    setTimeoutSeconds(9000);
    await selectModels(/近超时下模型/);
    expect(estimateAlert(/接近单局超时 150 分钟/))
      .toHaveTextContent('预计单局约 150 分钟，接近单局超时 150 分钟，建议调大超时');

    cleanup();
    renderPage();
    await waitForPresets();
    await selectPreset(TWELVE_PLAYER.name);
    setTimeoutSeconds(9000);
    await selectModels(/近超时上模型/);
    expect(estimateAlert(/已超过单局超时 150 分钟/))
      .toHaveTextContent('预计单局约 150 分钟，已超过单局超时 150 分钟，几乎必定超时');
  });
});
