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

describe('CenterDisplay dramatic phase page', () => {
  it('shows english eyebrow, chinese day numerals and phase name', () => {
    renderCenter({ phase: 'speech', roundNumber: 2 });
    expect(screen.getByText('THE SECOND DAY')).toBeInTheDocument();
    expect(screen.getByText('第贰天')).toBeInTheDocument();
    expect(screen.getByText('白天 · 发言')).toBeInTheDocument();
    expect(screen.getByText(/存活 0\/0 · 余狼 0/)).toBeInTheDocument();
  });

  it('renders the night phase with roman day numeral', () => {
    renderCenter({ phase: 'night', roundNumber: 1 });
    expect(screen.getByText('第壹天')).toBeInTheDocument();
    expect(screen.getByText('黑夜')).toBeInTheDocument();
  });

  it('appends the acting role to the night phase label', () => {
    renderCenter({
      phase: 'night',
      roundNumber: 1,
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'night_action',
          payload: { action_type: 'witch_save', target_seat: 2, round_number: 1 },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('黑夜 · 女巫')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      phase: 'night',
      roundNumber: 1,
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'wolf_chat_message',
          payload: { round_number: 1, seat: 1, text: '刀谁' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('黑夜 · 狼人')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      phase: 'night',
      roundNumber: 1,
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'night_thought',
          payload: { round_number: 1, seat: 3, action_type: 'seer_reasoning', target_seat: 4, reasoning: '查验4号' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('黑夜 · 预言家')).toBeInTheDocument();
  });

  it('shows a paused hint when paused with no event', () => {
    useGameStore.setState({ isPaused: true });
    render(<CenterDisplay />);
    expect(screen.getByText('游戏已暂停')).toBeInTheDocument();
  });
});

describe('CenterDisplay war reports', () => {
  it('reports the latest death from the timeline', () => {
    renderCenter({
      phase: 'dawn',
      roundNumber: 2,
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'death',
          payload: { player_seat: 9, cause: 'wolf_kill', round_number: 2 },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText(/9号在夜色中出局，死因：狼人袭击/)).toBeInTheDocument();
  });

  it('reports the latest exile result', () => {
    renderCenter({
      phase: 'vote_resolution',
      roundNumber: 1,
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'vote_result',
          payload: { exiled_seat: 2, round_number: 1 },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText(/2号被公投放逐，尘土落定/)).toBeInTheDocument();
  });

  it('reports the wolf-kill blade target', () => {
    renderCenter({
      phase: 'dawn',
      roundNumber: 1,
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'night_action',
          payload: { action_type: 'werewolf_kill', target_seat: 2, round_number: 1, vote_counts: { '2': 2 } },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText(/狼人的刀锋昨夜指向 2号/)).toBeInTheDocument();
  });

  it('falls back to a phase-flavored report when no key events exist', () => {
    renderCenter({ phase: 'speech', roundNumber: 1 });
    expect(screen.getByText(/众人各执一词，真伪难辨/)).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({ phase: 'night', roundNumber: 1 });
    expect(screen.getByText(/长夜未尽，狼人已在暗中谋划/)).toBeInTheDocument();
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
    expect(screen.getByText('9号 狼人袭击')).toBeInTheDocument();
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

  it('summarizes sheriff election events and campaign report', () => {
    renderCenter({
      phase: 'sheriff_election',
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'sheriff_run',
          payload: { round_number: 1, seat: 2, choice: 'run' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('2号上警')).toBeInTheDocument();
    expect(screen.getByText('2号上警竞选')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      phase: 'sheriff_election',
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'sheriff_run',
          payload: { round_number: 1, seat: 4, choice: 'pass' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('4号过')).toBeInTheDocument();
    expect(screen.getByText('4号选择不上警')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      phase: 'sheriff_election',
      roundNumber: 1,
      timeline: [],
      timelineIndex: -1,
    });
    expect(screen.getByText('警长竞选进行中……')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'sheriff_elected',
          payload: { round_number: 1, seat: 2, reason: 'vote' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getAllByText('2号当选警长').length).toBeGreaterThan(0);
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'sheriff_elected',
          payload: { round_number: 1, seat: null, reason: 'none' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getAllByText('警长竞选结束，警徽流失').length).toBeGreaterThan(0);
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'sheriff_badge',
          payload: { round_number: 2, from_seat: 2, to_seat: 5 },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getAllByText('2号将警徽移交给5号').length).toBeGreaterThan(0);
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'sheriff_badge',
          payload: { round_number: 2, from_seat: 5, to_seat: null },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getAllByText('5号撕毁警徽').length).toBeGreaterThan(0);
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'sheriff_withdraw',
          payload: { round_number: 1, seat: 2, choice: 'withdraw' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('2号退水')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'sheriff_withdraw',
          payload: { round_number: 1, seat: 3, choice: 'stay' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('3号留下')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'sheriff_vote',
          payload: { round_number: 1, voter_seat: 4, target_seat: 2, kind: 'vote' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('4号投给 2号')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'sheriff_vote',
          payload: { round_number: 1, voter_seat: 1, target_seat: null, kind: 'pk' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('1号弃权')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'sheriff_side',
          payload: { round_number: 1, seat: 2, side: 'sheriff_right' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('2号选择警右发言')).toBeInTheDocument();
  });

  it('labels campaign explode deaths as 竞选自爆', () => {
    renderCenter({
      phase: 'sheriff_election',
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'death',
          payload: { player_seat: 1, cause: 'self_explode', round_number: 1 },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('1号 竞选自爆')).toBeInTheDocument();
    cleanup();
    useGameStore.getState().reset();

    renderCenter({
      phase: 'dawn',
      timeline: [
        {
          ...replayEventMeta,
          event_type: 'death',
          payload: { player_seat: 1, cause: 'self_explode', round_number: 1 },
        },
        {
          ...replayEventMeta,
          event_type: 'sheriff_elected',
          payload: { round_number: 1, seat: null, reason: 'explode' },
        },
      ],
      timelineIndex: 0,
    });
    expect(screen.getByText('1号 竞选自爆')).toBeInTheDocument();
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
    expect(screen.getByText('黑夜')).toBeInTheDocument();
  });
});
