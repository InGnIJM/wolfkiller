// @vitest-environment jsdom

/**
 * 渲染面事件覆盖门禁。
 *
 * 每新增一个观众事件，三个渲染面都必须显式表态：要么渲染，要么在下面的表里
 * 写明 `ignore`。这张表是唯一会因「漏了 case」而变红的地方 —— 提交形如
 * `feat(frontend): render the X event` 的改动时，也要回来给表补一行。
 *
 * 清单来源（改后端投影时同步这里）：
 * - `backend/app/services/audience_projector.py` 的 `_EVENTS`：26 类公开投影
 * - `backend/app/api/routes/game_routes.py` 合成的 `narration`
 * - 旧档里仍会出现的 `witch_thought` / `seer_thought`
 */

import { cleanup, render } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useGameStore } from '../../../store/gameStore';
import type { PublicReplayEvent } from '../../../store/types';
import ActivityCard from '../ActivityCard';
import CenterDisplay from '../CenterDisplay';
import HistoryPanel from '../HistoryPanel';

const TS = '2026-08-13T00:00:00Z';
const at = { timestamp: TS } as const;

type Surface = 'activity' | 'center' | 'history';
type Verdict = 'render' | 'ignore';

interface SurfaceCase {
  /** 一句话说明这个事件是什么，方便后来者判断该不该被看见。 */
  what: string;
  event: PublicReplayEvent;
  expected: Record<Surface, Verdict>;
}

const CASES: SurfaceCase[] = [
  {
    what: '开局座位与配置',
    event: { ...at, event_type: 'game_initialized', payload: { players: {}, config: {} } },
    expected: { activity: 'ignore', center: 'ignore', history: 'ignore' },
  },
  {
    what: '阶段推进（只驱动 store，不作为事件卡）',
    event: { ...at, event_type: 'phase', payload: { phase: 'night', round_number: 1 } },
    expected: { activity: 'ignore', center: 'ignore', history: 'render' },
  },
  {
    what: '翻牌公开身份（只更新座位状态）',
    event: {
      ...at,
      event_type: 'player_revealed',
      payload: { seat_number: 2, role: 'wolf-killer-seer', camp: 'good' },
    },
    expected: { activity: 'ignore', center: 'ignore', history: 'ignore' },
  },
  {
    what: '执行状态（只驱动 store 的暂停/中断标记）',
    event: {
      ...at,
      event_type: 'execution_state',
      payload: { execution_status: 'running', recoverable: false, recovery_block_code: null },
    },
    expected: { activity: 'ignore', center: 'ignore', history: 'ignore' },
  },
  {
    what: '开始发言的通告',
    event: { ...at, event_type: 'speaking', payload: { seat: 3, round_number: 1 } },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '发言正文',
    event: {
      ...at,
      event_type: 'speech',
      payload: { player_seat: 1, text: '我是预言家', round_number: 1, phase: 'speech' },
    },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '夜间思考（含各角色 reasoning）',
    event: {
      ...at,
      event_type: 'night_thought',
      payload: {
        seat: 6,
        action_type: 'seer_reasoning',
        target_seat: 2,
        reasoning: '查验2号',
        round_number: 1,
      },
    },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '女巫思考（旧档事件类型）',
    event: { ...at, event_type: 'witch_thought', payload: { seat: 5, text: '考虑救人', round_number: 1 } },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '预言家思考（旧档事件类型）',
    event: { ...at, event_type: 'seer_thought', payload: { seat: 6, text: '查验2号', round_number: 1 } },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '狼队频道发言',
    event: { ...at, event_type: 'wolf_chat_message', payload: { seat: 1, text: '刀4号', round_number: 1 } },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '狼队投票',
    event: {
      ...at,
      event_type: 'wolf_vote',
      payload: { seat: 1, target_seat: 4, reasoning: '像神', round_number: 1 },
    },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '夜间行动结果',
    event: {
      ...at,
      event_type: 'night_action',
      payload: { action_type: 'seer_check', target_seat: 2, round_number: 1 },
    },
    expected: { activity: 'ignore', center: 'render', history: 'render' },
  },
  {
    what: '死亡',
    event: { ...at, event_type: 'death', payload: { player_seat: 4, cause: 'wolf_kill', round_number: 1 } },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '白天投票',
    event: { ...at, event_type: 'vote', payload: { voter_seat: 1, target_seat: 4, round_number: 2 } },
    expected: { activity: 'ignore', center: 'render', history: 'render' },
  },
  {
    what: '投票结果',
    event: { ...at, event_type: 'vote_result', payload: { exiled_seat: 4, round_number: 2 } },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '技术性弃权',
    event: {
      ...at,
      event_type: 'technical_abstain',
      payload: { voter_seat: 7, round_number: 2, failure_code: 'provider_timeout' },
    },
    expected: { activity: 'ignore', center: 'render', history: 'render' },
  },
  {
    what: '白痴翻牌免于出局',
    event: { ...at, event_type: 'exile_cancelled', payload: { target_seat: 4, round_number: 2 } },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '白狼王自爆',
    event: { ...at, event_type: 'self_explode', payload: { seat: 1, target_seat: 4, round_number: 2 } },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '骑士翻牌决斗',
    event: {
      ...at,
      event_type: 'knight_duel',
      payload: { seat: 4, target_seat: 9, camp: 'werewolf', round_number: 2 },
    },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '狼美人魅惑（上帝视角带目标）',
    event: {
      ...at,
      event_type: 'wolf_beauty_charm',
      payload: { seat: 6, target_seat: 2, round_number: 2 },
    },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '狼美人殉情',
    event: {
      ...at,
      event_type: 'wolf_beauty_revenge',
      payload: { seat: 6, target_seat: 2, cause: 'exile', round_number: 2 },
    },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '警长当选',
    event: { ...at, event_type: 'sheriff_elected', payload: { seat: 2, round_number: 1, reason: 'vote' } },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '警徽移交',
    event: { ...at, event_type: 'sheriff_badge', payload: { from_seat: 2, to_seat: 5, round_number: 2 } },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '上警表态',
    event: { ...at, event_type: 'sheriff_run', payload: { seat: 2, choice: 'run', round_number: 1 } },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '退水表态',
    event: { ...at, event_type: 'sheriff_withdraw', payload: { seat: 2, choice: 'withdraw', round_number: 1 } },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '警长竞选票',
    event: {
      ...at,
      event_type: 'sheriff_vote',
      payload: { voter_seat: 3, target_seat: 2, kind: 'vote', round_number: 1 },
    },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '警长选发言方向',
    event: {
      ...at,
      event_type: 'sheriff_side',
      payload: { seat: 2, side: 'sheriff_left', round_number: 2 },
    },
    expected: { activity: 'render', center: 'render', history: 'render' },
  },
  {
    what: '旁白（API 层合成）',
    event: {
      ...at,
      event_type: 'narration',
      payload: { round_number: 1, title: '天黑请闭眼', text: '狼人请睁眼' },
    },
    expected: { activity: 'ignore', center: 'render', history: 'render' },
  },
  {
    what: '结算给座位留下的标记（中毒/中枪未死、延迟死亡）',
    event: {
      ...at,
      event_type: 'player_status',
      payload: { player_seat: 5, status: 'poisoned', round_number: 2 },
    },
    expected: { activity: 'ignore', center: 'render', history: 'render' },
  },
  {
    what: '胜负',
    event: { ...at, event_type: 'winner', payload: { winning_camp: 'werewolf', reason: 'all_gods_dead' } },
    expected: { activity: 'ignore', center: 'render', history: 'render' },
  },
];

function mount(surface: Surface, event: PublicReplayEvent): HTMLElement {
  useGameStore.setState({
    gameId: 'game-1',
    phase: 'night',
    roundNumber: 1,
    timeline: [event],
    timelineIndex: 0,
  });
  if (surface === 'activity') return render(<ActivityCard />).container;
  if (surface === 'center') return render(<CenterDisplay />).container;
  return render(<HistoryPanel onClose={vi.fn()} />).container;
}

/**
 * 中心面板与编年史是整块 UI，忽略事件时外壳的文本依然存在，所以按事件自己的
 * 渲染锚点判断：摘要胶囊（`.wk-event-summary`）与编年史条目（`.wk-chronicle-entry`）。
 */
function showsEvent(surface: Surface, container: HTMLElement): boolean {
  if (surface === 'activity') {
    return (container.textContent ?? '').trim().length > 0;
  }
  const anchor = surface === 'center' ? '.wk-event-summary' : '.wk-chronicle-entry';
  return container.querySelector(anchor) !== null;
}

beforeEach(() => {
  useGameStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useGameStore.getState().reset();
});

describe('render-surface event coverage', () => {
  it('covers every backend audience event type exactly once', () => {
    const listed = CASES.map((item) => item.event.event_type);
    expect(new Set(listed).size).toBe(listed.length);
    expect(listed).toHaveLength(30);
  });

  const surfaces: Surface[] = ['activity', 'center', 'history'];

  for (const surface of surfaces) {
    describe(surface, () => {
      it.each(CASES)('$what', ({ event, expected }) => {
        const container = mount(surface, event);
        const verdict = showsEvent(surface, container);
        if (expected[surface] === 'render') {
          expect(verdict).toBe(true);
        } else {
          expect(verdict).toBe(false);
        }
      });
    });
  }
});
