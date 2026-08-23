// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useGameStore } from '../../../store/gameStore';
import type { PublicReplayEvent } from '../../../store/types';
import CenterDisplay from '../CenterDisplay';

const replayEventMeta = { timestamp: '2026-08-13T00:00:00Z' } as const;

// 中心只展示阶段信息 + 一句话事件摘要（完整内容在底部活动栏 / 编年史）
function renderCenter({
  phase = 'speech',
  roundNumber = 1,
  timeline = [],
  timelineIndex = -1,
}: {
  phase?: string;
  roundNumber?: number;
  timeline?: PublicReplayEvent[];
  timelineIndex?: number;
} = {}) {
  useGameStore.setState({
    phase: phase as never,
    roundNumber,
    timeline,
    timelineIndex,
  });
  return render(<CenterDisplay />);
}

beforeEach(() => {
  useGameStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useGameStore.getState().reset();
});

describe('CenterDisplay phase page', () => {
  it('shows the day number, phase name and survival count', () => {
    renderCenter({ phase: 'speech', roundNumber: 2 });
    expect(screen.getByText('DAY 2')).toBeInTheDocument();
    expect(screen.getByText('发言阶段')).toBeInTheDocument();
  });

  it('shows a paused hint when paused with no event', () => {
    useGameStore.setState({ isPaused: true });
    render(<CenterDisplay />);
    expect(screen.getByText('游戏已暂停')).toBeInTheDocument();
  });
});

describe('CenterDisplay event summary', () => {
  it('summarizes a speech event as 正在发言', () => {
    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'speech',
          payload: { player_seat: 5, text: '全文在活动栏', round_number: 1, phase: 'speech' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('5号正在发言')).toBeInTheDocument();
    expect(screen.queryByText('全文在活动栏')).not.toBeInTheDocument();
  });

  it('summarizes a last-word speech as 遗言', () => {
    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'speech',
          payload: { player_seat: 4, text: '遗言全文', round_number: 1, phase: 'last_words' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('4号遗言')).toBeInTheDocument();
  });

  it('summarizes death, vote and vote-result events', () => {
    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'death',
          payload: { player_seat: 9, cause: 'wolf_kill', round_number: 2 },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('9号 夜间死亡')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'vote',
          payload: { voter_seat: 1, target_seat: 2, round_number: 1 },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('1号投给 2号')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'vote_result',
          payload: { exiled_seat: 2, round_number: 1 },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('2号被放逐')).toBeInTheDocument();
  });

  it('summarizes thoughts, wolf chat and night actions', () => {
    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'witch_thought',
          payload: { round_number: 1, seat: 5, text: '思考全文' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('女巫思考中')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'wolf_chat_message',
          payload: { round_number: 1, seat: 1, text: '刀谁' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('狼群密谋中')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'night_action',
          payload: { action_type: 'werewolf_kill', target_seat: 2, round_number: 1, vote_counts: { '2': 2 } },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('狼人行动 · 目标 2号')).toBeInTheDocument();
  });

  it('summarizes narration title and winner result', () => {
    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'narration',
          payload: { round_number: 1, title: '天黑请闭眼', text: '狼人请睁眼' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('天黑请闭眼')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'winner',
          payload: { winning_camp: 'werewolf', reason: 'all_villagers_dead' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('狼人阵营获胜')).toBeInTheDocument();
  });

  it('renders no summary for phase events', () => {
    renderCenter({
      phase: 'night',
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'phase',
          payload: { phase: 'night', round_number: 1 },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('夜晚进行中')).toBeInTheDocument();
  });
});
