// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useGameStore } from '../../../store/gameStore';
import type { PublicReplayEvent } from '../../../store/types';
import ActivityCard from '../ActivityCard';

const replayEventMeta = { timestamp: '2026-08-13T00:00:00Z' } as const;

function setTimeline(timeline: PublicReplayEvent[]) {
  useGameStore.getState().loadLogs({ game_id: 'game-1', events: timeline });
  useGameStore.getState().seekTo(0);
}

beforeEach(() => {
  useGameStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useGameStore.getState().reset();
});

describe('ActivityCard', () => {
  it('renders nothing when there is no current event', () => {
    useGameStore.getState().reset();
    const { container } = render(<ActivityCard />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing for events without a bottom activity card', () => {
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'vote',
        payload: { voter_seat: 1, target_seat: 2, round_number: 1 },
      },
    ]);
    const { container } = render(<ActivityCard />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the speaking player seat and full speech body', () => {
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'speech',
        payload: { player_seat: 5, text: '我怀疑 2 号是狼', round_number: 2, phase: 'speech' },
      },
    ]);
    render(<ActivityCard />);

    expect(screen.getByText('5号')).toBeInTheDocument();
    expect(screen.getByText('正在发言')).toBeInTheDocument();
    expect(screen.getByText('我怀疑 2 号是狼')).toBeInTheDocument();
  });

  it('labels last-word events as 遗言', () => {
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'speech',
        payload: { player_seat: 4, text: '我是狼，你们上当了', round_number: 1, phase: 'last_words' },
      },
    ]);
    render(<ActivityCard />);

    expect(screen.getByText('遗言')).toBeInTheDocument();
    expect(screen.getByText('我是狼，你们上当了')).toBeInTheDocument();
  });

  it('computes duration from the previous event timestamp and shows token placeholder', () => {
    useGameStore.getState().loadLogs({
      game_id: 'game-1',
      events: [
        {
          ...replayEventMeta,
          event_type: 'phase',
          payload: { phase: 'speech', round_number: 2 },
        },
        {
          timestamp: '2026-08-13T00:01:30Z',
          event_type: 'speech',
          payload: { player_seat: 5, text: '耗时应当为 90 秒', round_number: 2, phase: 'speech' },
        },
      ],
    });
    useGameStore.getState().seekTo(1);
    render(<ActivityCard />);

    expect(screen.getByText(/第2轮 · 发言/)).toBeInTheDocument();
    expect(screen.getByText(/耗时 01:30/)).toBeInTheDocument();
    expect(screen.getByText('tokens --')).toBeInTheDocument();
  });

  it('renders witch thoughts with seat and body', () => {
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'witch_thought',
        payload: { round_number: 1, seat: 5, text: '昨晚2号被刀，我决定救人' },
      },
    ]);
    render(<ActivityCard />);

    expect(screen.getByText('女巫思考 · 5号')).toBeInTheDocument();
    expect(screen.getByText('昨晚2号被刀，我决定救人')).toBeInTheDocument();
    expect(screen.getByText(/第1轮 · 女巫思考/)).toBeInTheDocument();
    expect(screen.getByText('tokens --')).toBeInTheDocument();
  });

  it('renders night reasoning thoughts', () => {
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'night_thought',
        payload: { round_number: 1, seat: 4, action_type: 'hunter_reasoning', target_seat: 6, reasoning: '我开枪带走6号' },
      },
    ]);
    render(<ActivityCard />);

    expect(screen.getByText('猎人思考 · 4号')).toBeInTheDocument();
    expect(screen.getByText(/目标 6号：我开枪带走6号/)).toBeInTheDocument();
    expect(screen.getByText(/第1轮 · 猎人思考/)).toBeInTheDocument();
  });

  it('computes night-thought duration from the previous event timestamp', () => {
    useGameStore.getState().loadLogs({
      game_id: 'game-1',
      events: [
        {
          ...replayEventMeta,
          event_type: 'night_action',
          payload: { action_type: 'werewolf_kill', target_seat: 2, round_number: 1, vote_counts: { '2': 2 } },
        },
        {
          timestamp: '2026-08-13T00:00:45Z',
          event_type: 'night_thought',
          payload: { round_number: 1, seat: 5, action_type: 'witch_reasoning', target_seat: null, reasoning: '' },
        },
      ],
    });
    useGameStore.getState().seekTo(1);
    render(<ActivityCard />);

    expect(screen.getByText('女巫思考 · 5号')).toBeInTheDocument();
    expect(screen.getByText(/耗时 00:45/)).toBeInTheDocument();
  });

  it('renders wolf chat messages', () => {
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'wolf_chat_message',
        payload: { round_number: 1, seat: 1, text: '今晚刀谁？' },
      },
    ]);
    render(<ActivityCard />);

    expect(screen.getByText('狼群密谋')).toBeInTheDocument();
    expect(screen.getByText('今晚刀谁？')).toBeInTheDocument();
    expect(screen.getByText(/第1轮 · 狼聊/)).toBeInTheDocument();
    expect(screen.getByText('tokens --')).toBeInTheDocument();
  });

  it('renders the death announcement with cause', () => {
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'death',
        payload: { player_seat: 9, cause: 'wolf_kill', round_number: 2 },
      },
    ]);
    render(<ActivityCard />);

    expect(screen.getByText('9号玩家出局')).toBeInTheDocument();
    expect(screen.getByText(/夜间死亡 · 第2轮/)).toBeInTheDocument();
  });

  it('renders exile and draw vote results', () => {
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'vote_result',
        payload: { exiled_seat: 2, round_number: 1 },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('2号被放逐')).toBeInTheDocument();

    cleanup();
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'vote_result',
        payload: { exiled_seat: null, round_number: 1 },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('平票，无人被放逐')).toBeInTheDocument();
  });

  it('labels campaign speeches and sheriff public events', () => {
    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'speech',
        payload: { player_seat: 2, text: '请投我', round_number: 1, phase: 'sheriff_election' },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('竞选发言')).toBeInTheDocument();
    expect(screen.getByText('请投我')).toBeInTheDocument();
    cleanup();

    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'sheriff_elected',
        payload: { round_number: 1, seat: 2, reason: 'vote' },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('2号当选警长')).toBeInTheDocument();
    cleanup();

    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'sheriff_elected',
        payload: { round_number: 1, seat: null, reason: 'none' },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('警长竞选结束，警徽流失')).toBeInTheDocument();
    cleanup();

    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'sheriff_badge',
        payload: { round_number: 2, from_seat: 2, to_seat: 5 },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('2号将警徽移交给5号')).toBeInTheDocument();
    cleanup();

    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'sheriff_badge',
        payload: { round_number: 2, from_seat: 5, to_seat: null },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('5号撕毁警徽')).toBeInTheDocument();
    cleanup();

    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'sheriff_run',
        payload: { round_number: 1, seat: 2, choice: 'run' },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('2号上警')).toBeInTheDocument();
    cleanup();

    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'sheriff_run',
        payload: { round_number: 1, seat: 4, choice: 'pass' },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('4号过')).toBeInTheDocument();
    cleanup();

    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'sheriff_withdraw',
        payload: { round_number: 1, seat: 2, choice: 'withdraw' },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('2号退水')).toBeInTheDocument();
    cleanup();

    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'sheriff_withdraw',
        payload: { round_number: 1, seat: 3, choice: 'stay' },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('3号留下')).toBeInTheDocument();
    cleanup();

    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'sheriff_vote',
        payload: { round_number: 1, voter_seat: 4, target_seat: 2, kind: 'vote' },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('4号投给 2号')).toBeInTheDocument();
    cleanup();

    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'sheriff_vote',
        payload: { round_number: 1, voter_seat: 1, target_seat: null, kind: 'pk' },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('1号弃权')).toBeInTheDocument();
    expect(screen.getByText(/PK票/)).toBeInTheDocument();
    cleanup();

    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'sheriff_side',
        payload: { round_number: 1, seat: 2, side: 'sheriff_left' },
      },
    ]);
    render(<ActivityCard />);
    expect(screen.getByText('2号选择警左发言')).toBeInTheDocument();
  });

  it('labels election self-explode deaths as 竞选自爆', () => {
    useGameStore.getState().loadLogs({
      game_id: 'game-1',
      events: [
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
    });
    useGameStore.getState().seekTo(0);
    render(<ActivityCard />);
    expect(screen.getByText(/竞选自爆 · 第1轮/)).toBeInTheDocument();
    cleanup();

    setTimeline([
      {
        ...replayEventMeta,
        event_type: 'death',
        payload: { player_seat: 3, cause: 'self_explode', round_number: 2 },
      },
    ]);
    useGameStore.setState({ phase: 'sheriff_election' });
    render(<ActivityCard />);
    expect(screen.getByText(/竞选自爆 · 第2轮/)).toBeInTheDocument();
  });
});
