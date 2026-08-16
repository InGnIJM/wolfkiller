// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import CreateGameWizard from '../CreateGameWizard';
import {
  createGame, createModel, fetchConstraints, fetchPresets, fetchRoleCatalog,
  listModels,
} from '../../../api/client';
import type { GamePreset } from '../../../store/types';

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
];

const PRESETS: GamePreset[] = [
  { id: 'nine-player-standard', name: '九人标准场', description: '3狼 3民 1预言家 1女巫 1猎人', role_counts: { 'wolf-killer-werewolf': 3, 'wolf-killer-villager': 3, 'wolf-killer-seer': 1, 'wolf-killer-witch': 1, 'wolf-killer-hunter': 1 } },
  { id: 'ten-player-standard', name: '十人标准场', description: '含守卫', role_counts: { 'wolf-killer-werewolf': 3, 'wolf-killer-villager': 3, 'wolf-killer-seer': 1, 'wolf-killer-witch': 1, 'wolf-killer-hunter': 1, 'wolf-killer-guard': 1 } },
];

const CONSTRAINTS = { min_players: 4, max_players: 12, min_werewolves: 1, min_good: 1 };

const MODEL = {
  id: 'm1', name: 'DeepSeek Pro', base_url: 'https://api.deepseek.com/v1',
  model_id: 'deepseek-v4-pro', has_key: true, api_key_masked: 'sk-***1234',
  key_invalid: false, temperature: null, strict_base_url: null,
  created_at: '', updated_at: '',
};

beforeEach(() => {
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
  });

  it('creates the game with the env default and navigates', async () => {
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));

    await waitFor(() =>
      expect(createGame).toHaveBeenCalledWith({
        role_counts: expect.objectContaining({ 'wolf-killer-werewolf': 3 }),
        model_assignments: [{ config_id: null, count: 9 }],
      }),
    );
    expect(navigateMock).toHaveBeenCalledWith('/game/g1');
  });

  it('selects a stored config and submits its id', async () => {
    await goToStep2();
    fireEvent.click(screen.getByText('DeepSeek Pro'));
    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));

    await waitFor(() =>
      expect(createGame).toHaveBeenCalledWith(expect.objectContaining({
        model_assignments: [{ config_id: 'm1', count: 9 }],
      })),
    );
  });

  it('creates a model config inline and selects it', async () => {
    vi.mocked(createModel).mockResolvedValue({ ...MODEL, id: 'm2', name: 'New Model' });
    await goToStep2();
    fireEvent.click(screen.getByText('当场新建模型配置'));

    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'New Model' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(createModel).toHaveBeenCalledWith(expect.objectContaining({ name: 'New Model' })),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));
    await waitFor(() =>
      expect(createGame).toHaveBeenCalledWith(expect.objectContaining({
        model_assignments: [{ config_id: 'm2', count: 9 }],
      })),
    );
  });

  it('shows an error alert when creation fails', async () => {
    vi.mocked(createGame).mockRejectedValue(new Error('boom'));
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));

    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
