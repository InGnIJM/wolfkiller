// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import RulesPage from '../RulesPage';
import { fetchConstraints, fetchPresets, fetchRoleCatalog } from '../../../api/client';
import type { FieldConstraints, GamePreset, RoleCatalogItem } from '../../../store/types';

vi.mock('../../../api/client', () => ({
  fetchRoleCatalog: vi.fn(),
  fetchPresets: vi.fn(),
  fetchConstraints: vi.fn(),
}));

const PRESETS: GamePreset[] = [
  {
    id: 'nine-player-standard', name: '九人标准场',
    description: '3狼 3民 1预言家 1女巫 1猎人',
    role_counts: {
      'wolf-killer-werewolf': 3, 'wolf-killer-villager': 3,
      'wolf-killer-seer': 1, 'wolf-killer-witch': 1, 'wolf-killer-hunter': 1,
    },
    enable_sheriff: false,
  },
  {
    id: 'twelve-player-idiot', name: '十二人预女猎白',
    description: '4狼 4民 预女猎白痴',
    role_counts: { 'wolf-killer-werewolf': 4, 'wolf-killer-idiot': 1 },
    enable_sheriff: true,
  },
];

/** 三张卡各测一条分支：有规则条目 / 没有条目退回简介 / 未登记的角色与阵营。 */
const ROLES: RoleCatalogItem[] = [
  {
    role_id: 'wolf-killer-werewolf', display_name: 'Werewolf', name_zh: '狼人',
    camp: 'werewolf', icon: 'wolf', description: '夜晚睁眼刀人',
    rules: ['与狼队友共用同一次狼刀。', '彼此知道身份。'],
    min_count: 0, max_count: null, dependencies: [], exclusions: [],
  },
  {
    role_id: 'wolf-killer-idiot', display_name: 'Idiot', name_zh: '白痴',
    camp: 'good', icon: 'idiot', description: '被放逐时翻牌免死',
    min_count: 0, max_count: 1, dependencies: [], exclusions: [],
  },
  {
    role_id: 'wolf-killer-mystery', display_name: 'Mystery', name_zh: '神秘人',
    camp: 'third_party', icon: 'unknown', description: '未登记阵营',
    rules: ['主程序之外的阵营也要照常渲染。'],
    min_count: 0, max_count: null, dependencies: [], exclusions: [],
  },
];

const CONSTRAINTS: FieldConstraints = {
  min_players: 4, max_players: 12, min_werewolves: 1, min_good: 1,
};

beforeEach(() => {
  vi.mocked(fetchPresets).mockResolvedValue(PRESETS);
  vi.mocked(fetchRoleCatalog).mockResolvedValue(ROLES);
  vi.mocked(fetchConstraints).mockResolvedValue(CONSTRAINTS);
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe('规则介绍页', () => {
  it('渲染板子构成、角色卡与通用规则', async () => {
    render(<RulesPage />);

    expect(screen.getByRole('heading', { level: 1, name: '规则介绍' })).toBeInTheDocument();
    // 默认选中第一套板子
    const nine = await screen.findByRole('button', { name: /九人标准场/ });
    expect(nine).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('警长：关闭')).toBeInTheDocument();
    expect(screen.getByText('9 人局')).toBeInTheDocument();
    expect(screen.getAllByText('×3')).toHaveLength(2);
    expect(screen.getByText(/自定义板子支持/)).toBeInTheDocument();

    // 角色卡：有规则条目时按条目渲染，没有条目时退回一句话简介
    expect(screen.getByTestId('role-card-wolf-killer-werewolf'))
      .toHaveTextContent('与狼队友共用同一次狼刀。');
    expect(screen.getByTestId('role-card-wolf-killer-idiot'))
      .toHaveTextContent('被放逐时翻牌免死');
    // 未登记的角色 id 与阵营照常显示
    expect(screen.getByText('神秘人')).toBeInTheDocument();
    expect(screen.getByText('third_party')).toBeInTheDocument();
    expect(screen.getByText('狼人阵营')).toBeInTheDocument();
    expect(screen.getByText('好人阵营')).toBeInTheDocument();

    // 叙述区：六个小节都在，夜晚流程按有序列表渲染
    for (const title of ['夜晚流程', '白天流程', '遗言资格', '投票与平票', '胜负条件', '可选规则与本作差异']) {
      expect(screen.getByRole('heading', { level: 2, name: title })).toBeInTheDocument();
    }
    expect(screen.getByText(/守卫先睁眼/)).toBeInTheDocument();
    expect(screen.getByText(/先判狼人/)).toBeInTheDocument();
  });

  it('切换到另一套板子后构成与警长开关随之变化', async () => {
    render(<RulesPage />);

    fireEvent.click(await screen.findByRole('button', { name: /十二人预女猎白/ }));

    expect(screen.getByText('警长：开启')).toBeInTheDocument();
    expect(screen.getByText('5 人局')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /十二人预女猎白/ }))
      .toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /九人标准场/ }))
      .toHaveAttribute('aria-pressed', 'false');
  });

  it('数据未返回时先显示加载状态', () => {
    vi.mocked(fetchPresets).mockReturnValue(new Promise<GamePreset[]>(() => {}));

    render(<RulesPage />);

    expect(screen.getByRole('status', { name: '正在加载规则数据' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '夜晚流程' })).not.toBeInTheDocument();
  });

  it('读取失败时提示原因，通用规则照常可读', async () => {
    vi.mocked(fetchRoleCatalog).mockRejectedValue(new Error('catalog 挂了'));

    render(<RulesPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent('catalog 挂了');
    expect(screen.getByText('板子数据读取失败，下方通用规则不受影响。')).toBeInTheDocument();
    expect(screen.getByText('角色数据读取失败，下方通用规则不受影响。')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '胜负条件' })).toBeInTheDocument();
  });

  it('抛出非 Error 时原样显示', async () => {
    vi.mocked(fetchPresets).mockRejectedValue('boom');

    render(<RulesPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent('boom');
  });

  it('后端返回空目录时给出空态而不是空白', async () => {
    vi.mocked(fetchPresets).mockResolvedValue([]);
    vi.mocked(fetchRoleCatalog).mockResolvedValue([]);
    vi.mocked(fetchConstraints).mockResolvedValue(undefined as unknown as FieldConstraints);

    render(<RulesPage />);

    await waitFor(() => {
      expect(screen.getByText('暂时没有可展示的板子数据。')).toBeInTheDocument();
    });
    expect(screen.getByText('暂时没有可展示的角色数据。')).toBeInTheDocument();
  });

  it('卸载后才返回的数据不再写入状态', async () => {
    let resolvePresets: (value: GamePreset[]) => void = () => {};
    vi.mocked(fetchPresets).mockReturnValue(
      new Promise<GamePreset[]>((resolve) => { resolvePresets = resolve; }),
    );
    const { unmount } = render(<RulesPage />);

    unmount();
    resolvePresets(PRESETS);

    await waitFor(() => expect(fetchPresets).toHaveBeenCalledTimes(1));
  });

  it('卸载后才到达的失败也不写入状态', async () => {
    let rejectPresets: (reason?: unknown) => void = () => {};
    vi.mocked(fetchPresets).mockReturnValue(
      new Promise<GamePreset[]>((_resolve, reject) => { rejectPresets = reject; }),
    );
    const { unmount } = render(<RulesPage />);

    unmount();
    rejectPresets(new Error('late'));

    await waitFor(() => expect(fetchPresets).toHaveBeenCalledTimes(1));
  });
});
