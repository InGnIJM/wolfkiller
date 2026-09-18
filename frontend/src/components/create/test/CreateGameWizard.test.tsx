// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import CreateGameWizard from '../CreateGameWizard';
import {
  createGame, createModel, fetchConstraints, fetchPresets, fetchRoleCatalog,
  listModels, testModelConnection,
} from '../../../api/client';
import type { GamePreset } from '../../../store/types';
import { useModelConfigStore } from '../../../store/modelConfigStore';

vi.mock('../../../api/client', () => ({
  createGame: vi.fn(),
  listModels: vi.fn(),
  createModel: vi.fn(),
  updateModel: vi.fn(),
  deleteModel: vi.fn(),
  testModelConnection: vi.fn(),
  fetchRoleCatalog: vi.fn(),
  fetchPresets: vi.fn(),
  fetchConstraints: vi.fn(),
}));

const navigateMock = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigateMock };
});

const ROLES = [
  { role_id: 'wolf-killer-werewolf', display_name: 'Werewolf', name_zh: '狼人', camp: 'werewolf', icon: 'wolf', description: '', min_count: 0, max_count: null, dependencies: [], exclusions: [] },
  { role_id: 'wolf-killer-villager', display_name: 'Villager', name_zh: '平民', camp: 'good', icon: 'villager', description: '', min_count: 0, max_count: null, dependencies: [], exclusions: [] },
  { role_id: 'wolf-killer-seer', display_name: 'Seer', name_zh: '预言家', camp: 'good', icon: 'seer', description: '', min_count: 0, max_count: null, dependencies: [], exclusions: [] },
  { role_id: 'wolf-killer-witch', display_name: 'Witch', name_zh: '女巫', camp: 'good', icon: 'witch', description: '', min_count: 0, max_count: null, dependencies: [], exclusions: [] },
  { role_id: 'wolf-killer-hunter', display_name: 'Hunter', name_zh: '猎人', camp: 'good', icon: 'hunter', description: '', min_count: 0, max_count: null, dependencies: [], exclusions: [] },
  { role_id: 'wolf-killer-guard', display_name: 'Guard', name_zh: '守卫', camp: 'good', icon: 'guard', description: '', min_count: 0, max_count: null, dependencies: [], exclusions: [] },
  { role_id: 'wolf-killer-idiot', display_name: 'Idiot', name_zh: '白痴', camp: 'good', icon: 'idiot', description: '', min_count: 0, max_count: 1, dependencies: [], exclusions: [] },
  { role_id: 'wolf-killer-werewolf-king', display_name: 'Werewolf King', name_zh: '白狼王', camp: 'werewolf', icon: 'wolf_king', description: '', min_count: 0, max_count: 1, dependencies: [], exclusions: [] },
];

const PRESETS: GamePreset[] = [
  { id: 'nine-player-standard', name: '九人标准场', description: '3狼 3民 1预言家 1女巫 1猎人', role_counts: { 'wolf-killer-werewolf': 3, 'wolf-killer-villager': 3, 'wolf-killer-seer': 1, 'wolf-killer-witch': 1, 'wolf-killer-hunter': 1 }, enable_sheriff: false },
  { id: 'ten-player-standard', name: '十人标准场', description: '含守卫', role_counts: { 'wolf-killer-werewolf': 3, 'wolf-killer-villager': 3, 'wolf-killer-seer': 1, 'wolf-killer-witch': 1, 'wolf-killer-hunter': 1, 'wolf-killer-guard': 1 }, enable_sheriff: false },
  { id: 'twelve-player-idiot', name: '十二人预女猎白', description: '4狼 4民 预女猎白痴', role_counts: { 'wolf-killer-werewolf': 4, 'wolf-killer-villager': 4, 'wolf-killer-seer': 1, 'wolf-killer-witch': 1, 'wolf-killer-hunter': 1, 'wolf-killer-idiot': 1 }, enable_sheriff: true },
  { id: 'twelve-player-wolf-king', name: '十二人白狼王', description: '3狼 1白狼王 4民 预女猎守', role_counts: { 'wolf-killer-werewolf': 3, 'wolf-killer-werewolf-king': 1, 'wolf-killer-villager': 4, 'wolf-killer-seer': 1, 'wolf-killer-witch': 1, 'wolf-killer-hunter': 1, 'wolf-killer-guard': 1 }, enable_sheriff: true },
];

const CONSTRAINTS = { min_players: 4, max_players: 12, min_werewolves: 1, min_good: 1 };

const MODEL = {
  id: 'm1', name: 'DeepSeek Pro', base_url: 'https://api.deepseek.com/v1',
  model_id: 'deepseek-v4-pro', has_key: true, api_key_masked: 'sk-***1234',
  key_invalid: false, temperature: null, strict_base_url: null,
  provider_profile: 'auto' as const,
  created_at: '', updated_at: '',
};

const INVALID_MODEL = {
  ...MODEL,
  id: 'invalid-model',
  name: '失效模型',
  key_invalid: true,
};

beforeEach(() => {
  useModelConfigStore.setState({
    configs: [], loading: false, error: null, loadError: null,
  });
  vi.mocked(fetchPresets).mockResolvedValue(PRESETS);
  vi.mocked(fetchRoleCatalog).mockResolvedValue(ROLES);
  vi.mocked(fetchConstraints).mockResolvedValue(CONSTRAINTS);
  vi.mocked(listModels).mockResolvedValue([MODEL]);
  vi.mocked(createGame).mockResolvedValue({ game_id: 'g1' });
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

async function renderStep1() {
  render(<CreateGameWizard />);
  await waitFor(() => expect(screen.getByText('九人标准场')).toBeInTheDocument());
}

describe('CreateGameWizard step 1', () => {
  it('loads presets, roles, and defaults to the nine-player preset', async () => {
    await renderStep1();
    expect(screen.getByText(/共 9 人/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下一步' })).toBeEnabled();
    expect(screen.getByRole('checkbox', { name: /警长/ })).not.toBeChecked();
  });

  it('turns sheriff on when selecting a twelve-player preset', async () => {
    await renderStep1();
    fireEvent.click(screen.getByText('十二人预女猎白'));
    expect(screen.getByRole('checkbox', { name: /警长/ })).toBeChecked();
  });

  it('adjusting a role switches to custom preset and updates the total', async () => {
    await renderStep1();
    fireEvent.click(screen.getByRole('button', { name: '增加狼人' }));
    expect(screen.getByText(/共 10 人/)).toBeInTheDocument();
  });

  it('stops decrementing wolves at the min_werewolves constraint', async () => {
    vi.mocked(fetchConstraints).mockResolvedValue({
      ...CONSTRAINTS, min_players: 4, min_werewolves: 3,
    });
    await renderStep1();
    const minusWolf = screen.getByRole('button', { name: '减少狼人' });
    expect(minusWolf).toBeDisabled();
  });

  it('stops decrementing good roles at the min_good constraint', async () => {
    vi.mocked(fetchConstraints).mockResolvedValue({
      ...CONSTRAINTS, min_players: 4, min_good: 9,
    });
    await renderStep1();
    expect(screen.getByRole('button', { name: '减少平民' })).toBeDisabled();
  });

  it('stops decrementing when total would drop below min_players', async () => {
    vi.mocked(fetchConstraints).mockResolvedValue({ ...CONSTRAINTS, min_players: 9 });
    await renderStep1();
    expect(screen.getByRole('button', { name: '减少平民' })).toBeDisabled();
  });

  it('selecting the ten-player preset applies its role counts', async () => {
    await renderStep1();
    fireEvent.click(screen.getByText('十人标准场'));
    expect(screen.getByText(/共 10 人/)).toBeInTheDocument();
  });

  it('counts the werewolf king toward the wolf quota so the twelve-player board is valid', async () => {
    vi.mocked(fetchConstraints).mockResolvedValue({ ...CONSTRAINTS, min_werewolves: 4 });
    await renderStep1();
    fireEvent.click(screen.getByText('十二人白狼王'));
    expect(screen.getByText(/共 12 人/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下一步' })).toBeEnabled();
    // 3 狼 + 1 白狼王刚好达到下限，任一狼阵营角色都不能再减
    expect(screen.getByRole('button', { name: '减少狼人' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '减少白狼王' })).toBeDisabled();
    // 白痴 max_count=1 达到上限后不能再加
    fireEvent.click(screen.getByText('十二人预女猎白'));
    expect(screen.getByRole('button', { name: '增加白痴' })).toBeDisabled();
  });
});

describe('CreateGameWizard step 2 and submission', () => {
  async function goToStep2() {
    await renderStep1();
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    await waitFor(() => expect(screen.getByText('环境默认 (.env)')).toBeInTheDocument());
  }

  it('shows the env default card and stored configs', async () => {
    await goToStep2();
    expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument();
    expect(screen.getByText('当场新建模型配置')).toBeInTheDocument();
    expect(screen.getByText('已分配 9 / 总人数 9')).toBeInTheDocument();
    expect(screen.getByText('分配完成')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '增加 DeepSeek Pro 人数' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '减少 DeepSeek Pro 人数' })).toBeDisabled();
  });

  it('creates the game with the env default and navigates', async () => {
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));

    await waitFor(() =>
      expect(createGame).toHaveBeenCalledWith({
        role_counts: expect.objectContaining({ 'wolf-killer-werewolf': 3 }),
        reveal_on_death: false,
        enable_sheriff: false,
        model_assignments: [{ config_id: null, count: 9 }],
      }),
    );
    expect(navigateMock).toHaveBeenCalledWith('/game/g1');
  });

  it('splits players across the env default and a stored config', async () => {
    await goToStep2();
    for (let i = 0; i < 4; i += 1) {
      fireEvent.click(screen.getByRole('button', { name: '减少环境默认模型人数' }));
      fireEvent.click(screen.getByRole('button', { name: '增加 DeepSeek Pro 人数' }));
    }
    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));

    await waitFor(() =>
      expect(createGame).toHaveBeenCalledWith(expect.objectContaining({
        model_assignments: [
          { config_id: null, count: 5 },
          { config_id: 'm1', count: 4 },
        ],
      })),
    );
  });

  it('jumps by five or ten seats and clamps to remaining players or zero', async () => {
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '将环境默认模型减少 10 人' }));
    expect(screen.getByText('还需分配 9 人')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '将 DeepSeek Pro 增加 5 人' }));
    expect(screen.getByText('还需分配 4 人')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '将 DeepSeek Pro 增加 10 人' }));
    expect(screen.getByText('分配完成')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '将 DeepSeek Pro 增加 5 人' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '将 DeepSeek Pro 增加 10 人' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '将 DeepSeek Pro 减少 5 人' }));
    expect(screen.getByText('还需分配 5 人')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '将 DeepSeek Pro 减少 10 人' }));
    expect(screen.getByText('还需分配 9 人')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '将 DeepSeek Pro 减少 5 人' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '将 DeepSeek Pro 减少 10 人' })).toBeDisabled();
  });

  it('accepts a typed quantity, clamps overflow, and reverts invalid input', async () => {
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '将环境默认模型减少 10 人' }));

    const input = screen.getByRole('textbox', { name: 'DeepSeek Pro 人数输入' });
    fireEvent.change(input, { target: { value: '4' } });
    expect(screen.getByText('还需分配 9 人')).toBeInTheDocument();
    fireEvent.blur(input);
    expect(screen.getByText('还需分配 5 人')).toBeInTheDocument();
    expect(input).toHaveValue('4');

    fireEvent.change(input, { target: { value: '99' } });
    fireEvent.blur(input);
    expect(screen.getByText('分配完成')).toBeInTheDocument();
    expect(input).toHaveValue('9');

    fireEvent.change(input, { target: { value: 'abc' } });
    fireEvent.blur(input);
    expect(input).toHaveValue('9');
    expect(screen.getByText('分配完成')).toBeInTheDocument();
  });

  it('commits a typed quantity on Enter from the input value', async () => {
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '将环境默认模型减少 10 人' }));

    const input = screen.getByRole('textbox', { name: 'DeepSeek Pro 人数输入' });
    fireEvent.change(input, { target: { value: '3' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.getByText('还需分配 6 人')).toBeInTheDocument();
    expect(input).toHaveValue('3');
  });

  it('preserves allocations when the player total changes and blocks a deficit', async () => {
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '减少环境默认模型人数' }));
    fireEvent.click(screen.getByRole('button', { name: '增加 DeepSeek Pro 人数' }));

    fireEvent.click(screen.getByRole('button', { name: '上一步' }));
    fireEvent.click(await screen.findByText('十人标准场'));
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    await waitFor(() => expect(screen.getByText('已分配 9 / 总人数 10')).toBeInTheDocument());
    expect(screen.getByText('还需分配 1 人')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '创建游戏' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '减少 DeepSeek Pro 人数' })).toBeEnabled();
  });

  it('preserves allocations when the player total shrinks and reports excess', async () => {
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '上一步' }));
    fireEvent.click(await screen.findByText('十人标准场'));
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    await waitFor(() => expect(screen.getByText('已分配 9 / 总人数 10')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '增加 DeepSeek Pro 人数' }));
    expect(screen.getByText('已分配 10 / 总人数 10')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '上一步' }));
    fireEvent.click(await screen.findByText('九人标准场'));
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    await waitFor(() => expect(screen.getByText('已分配 10 / 总人数 9')).toBeInTheDocument());
    expect(screen.getByText('已超出 1 人')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '创建游戏' })).toBeDisabled();
  });

  it('creates a model config inline with zero players, focuses it, and filters it from submit', async () => {
    vi.mocked(createModel).mockResolvedValue({ ...MODEL, id: 'm2', name: 'New Model' });
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 5, error: null });
    await goToStep2();
    fireEvent.click(screen.getByText('当场新建模型配置'));

    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'New Model' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    await waitFor(() => expect(screen.getByText(/连接成功/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(createModel).toHaveBeenCalledWith(expect.objectContaining({ name: 'New Model' })),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    const newModelRow = screen.getByRole('group', { name: 'New Model 人数分配' });
    await waitFor(() => expect(newModelRow).toHaveFocus());
    expect(screen.getByRole('button', { name: '减少 New Model 人数' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));
    await waitFor(() =>
      expect(createGame).toHaveBeenCalledWith(expect.objectContaining({
        model_assignments: [{ config_id: null, count: 9 }],
      })),
    );
  });

  it('keeps a now-invalid used config visible until its allocation is reduced to zero', async () => {
    vi.mocked(listModels)
      .mockResolvedValueOnce([MODEL])
      .mockResolvedValueOnce([{ ...MODEL, key_invalid: true }]);
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '减少环境默认模型人数' }));
    fireEvent.click(screen.getByRole('button', { name: '增加 DeepSeek Pro 人数' }));

    fireEvent.click(screen.getByRole('button', { name: '上一步' }));
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    await waitFor(() => expect(screen.getByText(/密钥失效/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '创建游戏' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '增加 DeepSeek Pro 人数' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '减少 DeepSeek Pro 人数' }));
    expect(screen.getByText('还需分配 1 人')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '增加环境默认模型人数' }));
    expect(screen.getByRole('button', { name: '创建游戏' })).toBeEnabled();
  });

  it('keeps a deleted used config as a warning row that can be reduced to zero', async () => {
    vi.mocked(listModels)
      .mockResolvedValueOnce([MODEL])
      .mockResolvedValueOnce([]);
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '减少环境默认模型人数' }));
    fireEvent.click(screen.getByRole('button', { name: '增加 DeepSeek Pro 人数' }));

    fireEvent.click(screen.getByRole('button', { name: '上一步' }));
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    await waitFor(() => expect(screen.getByText(/配置已删除或不可用/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '减少 已删除配置 m1 人数' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '增加 已删除配置 m1 人数' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '创建游戏' })).toBeDisabled();
  });

  it('allows an env-only game when loading stored configs fails', async () => {
    vi.mocked(listModels).mockRejectedValue(new Error('模型列表加载失败'));
    await goToStep2();
    await waitFor(() => expect(screen.getByText(/模型列表加载失败/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '创建游戏' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));
    await waitFor(() => expect(createGame).toHaveBeenCalledWith(expect.objectContaining({
      model_assignments: [{ config_id: null, count: 9 }],
    })));
  });

  it('blocks a cached stored assignment when refreshing the model list fails', async () => {
    vi.mocked(listModels)
      .mockResolvedValueOnce([MODEL])
      .mockRejectedValueOnce(new Error('刷新失败'));
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '减少环境默认模型人数' }));
    fireEvent.click(screen.getByRole('button', { name: '增加 DeepSeek Pro 人数' }));

    fireEvent.click(screen.getByRole('button', { name: '上一步' }));
    await screen.findByText('十人标准场');
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    await waitFor(() => expect(screen.getByText(/刷新失败/)).toBeInTheDocument());
    expect(screen.getByText(/无法确认此配置仍然可用/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '增加 DeepSeek Pro 人数' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '创建游戏' })).toBeDisabled();
  });

  it('disables quantity controls and navigation while creating, then restores them on failure', async () => {
    let rejectRequest: (error: Error) => void = () => undefined;
    vi.mocked(createGame).mockImplementation(() => new Promise((_, reject) => {
      rejectRequest = reject;
    }));
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));

    expect(screen.getByRole('button', { name: '上一步' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '创建中…' })).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', { name: '减少环境默认模型人数' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '当场新建模型配置' })).toBeDisabled();
    rejectRequest(new Error('boom'));

    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '上一步' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '减少环境默认模型人数' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '创建游戏' })).toBeEnabled();
  });

  it('does not allow assigning players to an already-invalid config', async () => {
    vi.mocked(listModels).mockResolvedValue([INVALID_MODEL]);
    await goToStep2();
    await waitFor(() => expect(screen.getByText('失效模型')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '增加 失效模型 人数' })).toBeDisabled();
  });

  it('shows an error alert when creation fails', async () => {
    vi.mocked(createGame).mockRejectedValue(new Error('boom'));
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));

    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
