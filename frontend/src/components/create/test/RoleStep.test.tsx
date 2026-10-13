// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import RoleStep from '../RoleStep';
import { fetchConstraints, fetchPresets, fetchRoleCatalog } from '../../../api/client';
import type { GamePreset } from '../../../store/types';

vi.mock('../../../api/client', () => ({
  fetchRoleCatalog: vi.fn(),
  fetchPresets: vi.fn(),
  fetchConstraints: vi.fn(),
}));

const role = (
  role_id: string, name_zh: string, camp: string, description = '',
  max_count: number | null = null,
) => ({
  role_id, display_name: name_zh, name_zh, camp, icon: role_id,
  description, min_count: 0, max_count, dependencies: [], exclusions: [],
});

const ROLES = [
  role('wolf-killer-werewolf', '狼人', 'werewolf', '夜晚睁眼，与狼队友商议刀人'),
  role('wolf-killer-villager', '平民', 'good', '白天发言投票，找出狼人'),
  role('wolf-killer-seer', '预言家', 'good', '每晚查验一名玩家的阵营'),
  role('wolf-killer-witch', '女巫', 'good', '拥有一瓶解药和一瓶毒药'),
  role('wolf-killer-hunter', '猎人', 'good', '死亡时可以开枪带走一名玩家'),
  role('wolf-killer-guard', '守卫', 'good', '每晚守护一名玩家免于狼刀'),
  role('wolf-killer-knight', '骑士', 'good', '发言结束后投票前翻牌决斗', 1),
  role('wolf-killer-wolf-beauty', '狼美人', 'werewolf', '每晚可魅惑一名好人', 1),
  role('wolf-killer-dream-weaver', '摄梦人', 'good', '每晚让一名玩家梦游', 1),
];

const NINE: GamePreset = {
  id: 'nine-player-standard', name: '九人标准场',
  description: '3狼 3民 1预言家 1女巫 1猎人',
  role_counts: {
    'wolf-killer-werewolf': 3, 'wolf-killer-villager': 3,
    'wolf-killer-seer': 1, 'wolf-killer-witch': 1, 'wolf-killer-hunter': 1,
  },
  enable_sheriff: false,
};

const BEAUTY_KNIGHT: GamePreset = {
  id: 'twelve-player-wolf-beauty-knight', name: '十二人狼美人骑士',
  description: '3狼 1狼美人 4民 预女守骑',
  role_counts: {
    'wolf-killer-werewolf': 3, 'wolf-killer-wolf-beauty': 1,
    'wolf-killer-villager': 4, 'wolf-killer-seer': 1, 'wolf-killer-witch': 1,
    'wolf-killer-guard': 1, 'wolf-killer-knight': 1,
  },
  enable_sheriff: true, witch_self_save: 'never',
};

const PRESETS: GamePreset[] = [NINE, BEAUTY_KNIGHT];
const CONSTRAINTS = { min_players: 4, max_players: 12, min_werewolves: 1, min_good: 1 };

/**
 * RoleStep 是受控组件，自己不改 roleCounts —— 由父级（向导）持有。
 * 测试必须照做，否则默认预设只会被「通知」出去而界面永远停在空阵容。
 */
function renderStep(initialCounts: Record<string, number> = {}) {
  const onRoleCountsChange = vi.fn();
  const onEnableSheriffChange = vi.fn();
  const onWitchSelfSaveChange = vi.fn();

  function Harness() {
    const [roleCounts, setRoleCounts] = useState(initialCounts);
    const [enableSheriff, setEnableSheriff] = useState(false);
    const [witchSelfSave, setWitchSelfSave] = useState<'first_night_only' | 'never'>(
      'first_night_only',
    );
    return (
      <RoleStep
        roleCounts={roleCounts}
        onRoleCountsChange={(next) => { onRoleCountsChange(next); setRoleCounts(next); }}
        onConstraintsChange={vi.fn()}
        onRolesChange={vi.fn()}
        revealOnDeath={false}
        onRevealOnDeathChange={vi.fn()}
        enableSheriff={enableSheriff}
        onEnableSheriffChange={(value) => { onEnableSheriffChange(value); setEnableSheriff(value); }}
        witchSelfSave={witchSelfSave}
        onWitchSelfSaveChange={(value) => { onWitchSelfSaveChange(value); setWitchSelfSave(value); }}
      />
    );
  }

  const view = render(<Harness />);
  return { ...view, onRoleCountsChange, onEnableSheriffChange, onWitchSelfSaveChange };
}

/** 板子条目是带 aria-label 的 button；右栏标题里也有同名文字，所以按 role 查。 */
const presetButton = (name: string) => screen.getByRole('button', { name });

async function search(text: string) {
  fireEvent.change(screen.getByLabelText('搜索板子'), { target: { value: text } });
  // 搜索有 200ms 防抖
  await waitFor(() => expect(screen.getByLabelText('搜索板子')).toHaveValue(text));
}

beforeEach(() => {
  vi.mocked(fetchPresets).mockResolvedValue(PRESETS);
  vi.mocked(fetchRoleCatalog).mockResolvedValue(ROLES);
  vi.mocked(fetchConstraints).mockResolvedValue(CONSTRAINTS);
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe('RoleStep 右栏', () => {
  it('only lists the roles the selected board actually contains', async () => {
    renderStep();
    await waitFor(() => expect(presetButton('九人标准场')).toBeInTheDocument());
    for (const name of ['狼人', '平民', '预言家', '女巫', '猎人']) {
      expect(screen.getByRole('button', { name: `增加${name}` })).toBeInTheDocument();
    }
    // 板子里没有的角色不该出现 —— 否则会误以为这个板子有 35 个角色。
    for (const name of ['守卫', '骑士', '狼美人', '摄梦人']) {
      expect(screen.queryByRole('button', { name: `增加${name}` })).not.toBeInTheDocument();
    }
  });

  it('shows the total and the camp split', async () => {
    renderStep();
    await waitFor(() => expect(screen.getByTestId('roster-total')).toHaveTextContent('共 9 人'));
  });

  it('switches to a custom roster once a count is adjusted', async () => {
    const { onRoleCountsChange } = renderStep();
    await waitFor(() => expect(presetButton('九人标准场')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '增加狼人' }));
    expect(onRoleCountsChange).toHaveBeenCalledWith(
      expect.objectContaining({ 'wolf-killer-werewolf': 4 }),
    );
  });

  it('marks the roster custom and offers a reset after adjusting', async () => {
    renderStep({
      'wolf-killer-werewolf': 4, 'wolf-killer-villager': 3,
      'wolf-killer-seer': 1, 'wolf-killer-witch': 1, 'wolf-killer-hunter': 1,
    });
    await waitFor(() => expect(screen.getByText('自定义阵容')).toBeInTheDocument());
    expect(screen.getByText('基于 九人标准场')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重置为原板子' }));
    // 回到板子态：标题不再是「自定义阵容」，也没有「基于 …」与重置入口。
    await waitFor(() => expect(screen.queryByText('自定义阵容')).not.toBeInTheDocument());
    expect(screen.queryByRole('button', { name: '重置为原板子' })).not.toBeInTheDocument();
  });
});

describe('RoleStep 左栏', () => {
  it('filters by board name', async () => {
    renderStep();
    await waitFor(() => expect(presetButton('十二人狼美人骑士')).toBeInTheDocument());
    await search('狼美人');
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '九人标准场' })).not.toBeInTheDocument();
    });
    expect(presetButton('十二人狼美人骑士')).toBeInTheDocument();
  });

  it('filters by a role name inside the board lineup', async () => {
    renderStep();
    await waitFor(() => expect(presetButton('十二人狼美人骑士')).toBeInTheDocument());
    // 「骑士」不在任何板子的名字或描述里，只在阵容里。
    await search('骑士');
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '九人标准场' })).not.toBeInTheDocument();
    });
    expect(presetButton('十二人狼美人骑士')).toBeInTheDocument();
  });

  it('offers guidance instead of a blank column when nothing matches', async () => {
    renderStep();
    await waitFor(() => expect(presetButton('九人标准场')).toBeInTheDocument());
    await search('不存在的板子');
    await waitFor(() => expect(screen.getByText('没有匹配的板子')).toBeInTheDocument());
    expect(screen.getByText(/试试「狼王」或「摄梦」/)).toBeInTheDocument();
  });

  it('clears the search', async () => {
    renderStep();
    await waitFor(() => expect(presetButton('九人标准场')).toBeInTheDocument());
    await search('骑士');
    await waitFor(() => expect(screen.getByLabelText('清空搜索')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('清空搜索'));
    await waitFor(() => expect(presetButton('九人标准场')).toBeInTheDocument());
  });

  it('caps the board list so a long catalog scrolls instead of stretching the wizard', async () => {
    renderStep();
    await waitFor(() => expect(presetButton('九人标准场')).toBeInTheDocument());
    const scroller = screen.getByTestId('preset-list-scroll');
    expect(scroller).toHaveAttribute('data-max-height', '520');
    fireEvent.click(presetButton('十二人狼美人骑士'));
    await waitFor(() => expect(screen.getByRole('heading', { name: '十二人狼美人骑士' })).toBeInTheDocument());
  });

  it('collapses a player-count group', async () => {
    renderStep();
    await waitFor(() => expect(presetButton('九人标准场')).toBeInTheDocument());
    const group = screen.getByRole('button', { name: /9 人局/ });
    expect(group).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(group);
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '九人标准场' })).not.toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /9 人局/ })).toHaveAttribute('aria-expanded', 'false');
  });
});

describe('RoleStep 添加角色', () => {
  it('only offers roles the board does not already have', async () => {
    renderStep();
    await waitFor(() => expect(presetButton('九人标准场')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /添加角色/ }));
    const dialog = await screen.findByRole('dialog');
    // 板子里已有的五个角色不能再选
    for (const name of ['狼人', '平民', '预言家', '女巫', '猎人']) {
      expect(within(dialog).queryByRole('checkbox', { name })).not.toBeInTheDocument();
    }
    expect(within(dialog).getByRole('checkbox', { name: '摄梦人' })).toBeInTheDocument();
  });

  it('adds the picked roles and switches to a custom roster', async () => {
    const { onRoleCountsChange } = renderStep();
    await waitFor(() => expect(presetButton('九人标准场')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /添加角色/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('checkbox', { name: '摄梦人' }));
    fireEvent.click(within(dialog).getByRole('button', { name: '添加 1 个角色' }));
    expect(onRoleCountsChange).toHaveBeenCalledWith(
      expect.objectContaining({ 'wolf-killer-dream-weaver': 1 }),
    );
  });

  it('keeps the confirm button disabled until something is picked', async () => {
    renderStep();
    await waitFor(() => expect(presetButton('九人标准场')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /添加角色/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: '添加 0 个角色' })).toBeDisabled();
  });

  it('drops the selection when the dialog is closed and reopened', async () => {
    renderStep();
    await waitFor(() => expect(presetButton('九人标准场')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /添加角色/ }));
    fireEvent.click(within(await screen.findByRole('dialog'))
      .getByRole('checkbox', { name: '摄梦人' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /添加角色/ }));
    const reopened = await screen.findByRole('dialog');
    // 关掉不保留：留着会让人以为已经加了。
    expect(within(reopened).getByRole('checkbox', { name: '摄梦人' })).not.toBeChecked();
    expect(within(reopened).getByRole('button', { name: '添加 0 个角色' })).toBeDisabled();
  });

  it('offers a collapsible board picker for narrow viewports', async () => {
    // 窄屏把左栏折到上面，默认收起，先把右栏让出来；桌面端由断点强制显示，
    // 不受这个状态影响（所以这里只断言窄屏入口本身）。
    renderStep();

    const toggle = await screen.findByRole('button', { name: '选择板子' });

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
  });
});
