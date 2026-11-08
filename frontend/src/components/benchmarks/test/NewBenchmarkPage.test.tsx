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

/** 变体列表逐行添加：每次新增一行再从该行挑配置。 */
async function selectModels(...names: RegExp[]) {
  for (const name of names) {
    fireEvent.click(inPage().getByRole('button', { name: '添加变体' }));
    const rows = inPage().getAllByRole('group', { name: /模型变体 \d+/ });
    fireEvent.mouseDown(
      within(rows[rows.length - 1]).getByRole('combobox', { name: '模型配置' }),
    );
    fireEvent.click(await screen.findByRole('option', { name }));
  }
}

function variantRow(index: number) {
  return inPage().getByRole('group', { name: `模型变体 ${index}` });
}

async function pickOption(label: string, option: RegExp | string, scope?: HTMLElement) {
  fireEvent.mouseDown(
    (scope ? within(scope) : inPage()).getByRole('combobox', { name: label }),
  );
  fireEvent.click(await screen.findByRole('option', { name: option }));
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

describe('模型变体与辅助模型', () => {
  const CONFIG_A: ModelConfig = {
    ...model('cfg-a', '模型A', 7000),
    model_ids: ['a-one', 'a-two'],
  };
  const CONFIG_B: ModelConfig = {
    ...model('cfg-b', '模型B', 9000),
    model_ids: ['b-one', 'b-two'],
  };

  async function submitDraft() {
    fireEvent.click(inPage().getByRole('button', { name: '创建草稿' }));
    await waitFor(() => expect(mocks.createDraft).toHaveBeenCalled());
  }

  it('混座提交把每行展开为带覆盖的 model_assignments', async () => {
    mocks.configs = [CONFIG_A];
    mocks.createDraft.mockResolvedValue({ run_id: 'r-1' });
    renderPage();
    await waitForPresets();
    await selectModels(/模型A/);

    const row = variantRow(1);
    fireEvent.change(
      within(row).getByRole('combobox', { name: '变体模型' }),
      { target: { value: 'a-two' } },
    );
    fireEvent.mouseDown(within(row).getByRole('combobox', { name: '变体思考强度' }));
    fireEvent.click(await screen.findByRole('option', { name: '高（high）' }));
    fireEvent.change(within(row).getByLabelText('变体温度'), { target: { value: '0.4' } });

    await submitDraft();
    expect(mocks.createDraft).toHaveBeenCalledWith(expect.objectContaining({
      model_assignments: [expect.objectContaining({
        model_config_id: 'cfg-a',
        model_id: 'a-two',
        reasoning_effort: 'high',
        temperature: 0.4,
        count: 9,
      })],
    }));
  });

  it('添加行默认坐满席位，满员时新行从最大行让一席，移除归还', async () => {
    mocks.configs = [CONFIG_A, CONFIG_B];
    renderPage();
    await waitForPresets();

    fireEvent.click(inPage().getByRole('button', { name: '添加变体' }));
    expect(inPage().getByText('已分配 9 / 9 席')).toBeInTheDocument();
    expect(
      (within(variantRow(1)).getByLabelText('模型变体 1 人数输入') as HTMLInputElement).value,
    ).toBe('9');

    fireEvent.click(inPage().getByRole('button', { name: '添加变体' }));
    expect(inPage().getByText('已分配 9 / 9 席')).toBeInTheDocument();
    expect(
      (within(variantRow(1)).getByLabelText('模型变体 1 人数输入') as HTMLInputElement).value,
    ).toBe('8');
    expect(
      (within(variantRow(2)).getByLabelText('模型变体 2 人数输入') as HTMLInputElement).value,
    ).toBe('1');

    fireEvent.click(within(variantRow(2)).getByLabelText('移除此变体'));
    expect(inPage().queryByRole('group', { name: '模型变体 2' })).not.toBeInTheDocument();
    expect(
      (within(variantRow(1)).getByLabelText('模型变体 1 人数输入') as HTMLInputElement).value,
    ).toBe('9');
  });

  it('成对回归允许同配置不同覆盖，拒绝完全相同的变体', async () => {
    mocks.configs = [CONFIG_A];
    mocks.createDraft.mockResolvedValue({ run_id: 'r-2' });
    renderPage();
    await waitForPresets();
    await pickOption('评测模式', 'A/B 配置回归');

    const base = inPage().getByRole('group', { name: '基线变体' });
    const cand = inPage().getByRole('group', { name: '候选变体' });
    await pickOption('模型配置', /模型A/, base);
    await pickOption('模型配置', /模型A/, cand);

    expect(screen.getByText('基线与候选变体完全相同，请至少调整一项覆盖。')).toBeInTheDocument();
    expect(inPage().getByRole('button', { name: '创建草稿' })).toBeDisabled();

    await pickOption('变体思考强度', '低（low）', cand);
    expect(screen.queryByText('基线与候选变体完全相同，请至少调整一项覆盖。'))
      .not.toBeInTheDocument();

    await submitDraft();
    expect(mocks.createDraft).toHaveBeenCalledWith(expect.objectContaining({
      baseline: expect.objectContaining({ model_config_id: 'cfg-a' }),
      candidate: expect.objectContaining({
        model_config_id: 'cfg-a',
        reasoning_effort: 'low',
      }),
    }));
  });

  it('解说与投票模型覆盖随请求冻结', async () => {
    mocks.configs = [CONFIG_A, CONFIG_B];
    mocks.createDraft.mockResolvedValue({ run_id: 'r-3' });
    renderPage();
    await waitForPresets();
    await selectModels(/模型A/);

    fireEvent.click(inPage().getByRole('switch', { name: '开启上帝视角实时解说' }));
    await pickOption('解说与复盘模型', /模型B/);
    await pickOption('解说模型思考强度', '中（medium）');
    await pickOption('投票模型', /模型B/);
    await pickOption('投票模型思考强度', '低（low）');

    await submitDraft();
    expect(mocks.createDraft).toHaveBeenCalledWith(expect.objectContaining({
      commentary_enabled: true,
      commentary_config_id: 'cfg-b',
      commentary_reasoning_effort: 'medium',
      vote_model_config_id: 'cfg-b',
      vote_reasoning_effort: 'low',
    }));
  });
});
