// @vitest-environment jsdom

/**
 * 上帝视角的两个新信息：
 * - 狼队为次日定的战术（`wolf_chat_message.day_plan`）——原来被映射层截掉丢弃；
 * - 结算留给座位的标记（`player_status`）——原来没有观众事件类型。
 *
 * 这两条都只发布数据没有用，必须在渲染面上真的出现，所以这里按渲染结果断言。
 */

import { cleanup, render } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useGameStore } from '../../../store/gameStore';
import type { PublicReplayEvent } from '../../../store/types';
import ActivityCard from '../ActivityCard';
import CenterDisplay from '../CenterDisplay';
import HistoryPanel from '../HistoryPanel';

const at = { timestamp: '2026-08-13T00:00:00Z' } as const;

function mount(event: PublicReplayEvent) {
  useGameStore.setState({
    gameId: 'game-1',
    phase: 'night',
    roundNumber: 1,
    timeline: [event],
    timelineIndex: 0,
  });
  return {
    activity: render(<ActivityCard />).container,
    center: render(<CenterDisplay />).container,
    history: render(<HistoryPanel onClose={vi.fn()} />).container,
  };
}

beforeEach(() => {
  useGameStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useGameStore.getState().reset();
});

describe('狼队次日计划', () => {
  it('renders the plan in the chat card and in the chronicle', () => {
    const surfaces = mount({
      ...at,
      event_type: 'wolf_chat_message',
      payload: {
        round_number: 2,
        seat: 4,
        text: '今晚刀预言家',
        day_plan: '明天推3号，5号留着当挡箭牌',
      },
    });

    expect(surfaces.activity.textContent).toContain('今晚刀预言家');
    expect(surfaces.activity.textContent).toContain('次日计划：明天推3号，5号留着当挡箭牌');
    expect(surfaces.history.textContent).toContain('次日计划：明天推3号，5号留着当挡箭牌');
  });

  it('leaves a chat line without a plan untouched', () => {
    const surfaces = mount({
      ...at,
      event_type: 'wolf_chat_message',
      payload: { round_number: 2, seat: 4, text: '先听5号的' },
    });

    expect(surfaces.activity.textContent).toContain('先听5号的');
    expect(surfaces.activity.textContent).not.toContain('次日计划');
    expect(surfaces.history.textContent).not.toContain('次日计划');
  });
});

describe('座位状态标记', () => {
  it('renders a known mark in the centre panel and the chronicle', () => {
    const surfaces = mount({
      ...at,
      event_type: 'player_status',
      payload: { round_number: 2, player_seat: 5, status: 'poisoned' },
    });

    expect(surfaces.center.textContent).toContain('5号 中毒未死');
    expect(surfaces.history.textContent).toContain('5号 中毒未死 · 第2轮');
  });

  it('falls back to the raw token for a mark it does not know', () => {
    const surfaces = mount({
      ...at,
      event_type: 'player_status',
      payload: { round_number: 3, player_seat: 8, status: 'some_new_mark' },
    });

    expect(surfaces.center.textContent).toContain('8号 some_new_mark');
    expect(surfaces.history.textContent).toContain('8号 some_new_mark · 第3轮');
  });

  it('advances the round without naming a speaker', () => {
    useGameStore.getState().loadLogs({
      game_id: 'game-1',
      events: [
        { ...at, event_type: 'phase', payload: { phase: 'dawn', round_number: 1 } },
        {
          ...at,
          event_type: 'player_status',
          payload: { round_number: 2, player_seat: 5, status: 'delayed_death' },
        },
      ],
    });
    useGameStore.getState().seekTo(1);

    expect(useGameStore.getState().roundNumber).toBe(2);
    expect(useGameStore.getState().currentSpeaker).toBeNull();
  });
});
