// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useGameStore } from '../../../store/gameStore';
import type { PublicReplayEvent } from '../../../store/types';
import HistoryPanel from '../HistoryPanel';
import TimelineController from '../TimelineController';

const timeline: PublicReplayEvent[] = [
  { event_type: 'phase', payload: { phase: 'speech', round_number: 1 } },
  { event_type: 'speech', payload: { player_seat: 1, text: '公开发言', round_number: 1 } },
  { event_type: 'vote', payload: { voter_seat: 1, target_seat: 2, round_number: 1 } },
  { event_type: 'vote', payload: { voter_seat: 2, target_seat: null, round_number: 1 } },
  { event_type: 'vote_result', payload: { exiled_seat: 2, round_number: 1 } },
  { event_type: 'vote_result', payload: { exiled_seat: null, round_number: 2 } },
  { event_type: 'death', payload: { player_seat: 2, cause: 'wolf_kill', round_number: 1 } },
  { event_type: 'winner', payload: { winning_camp: 'good', reason: 'all_wolves_dead' } },
];

beforeEach(() => {
  useGameStore.getState().reset();
});

afterEach(() => {
  cleanup();
  useGameStore.getState().reset();
  vi.restoreAllMocks();
});

describe('TimelineController accessibility', () => {
  it('allows replay from the end, pauses while playing, and disables play only when empty', () => {
    const play = vi.fn();
    const pause = vi.fn();
    useGameStore.setState({
      timeline,
      timelineIndex: timeline.length - 1,
      phase: 'game_over',
      play,
      pause,
    });

    const { rerender } = render(<TimelineController />);
    const replayButton = screen.getByRole('button', { name: '播放' });
    expect(replayButton).toBeEnabled();
    fireEvent.click(replayButton);
    expect(play).toHaveBeenCalledOnce();

    useGameStore.setState({ isPlaying: true });
    rerender(<TimelineController />);
    const pauseButton = screen.getByRole('button', { name: '暂停' });
    expect(pauseButton).toBeEnabled();
    fireEvent.click(pauseButton);
    expect(pause).toHaveBeenCalledOnce();

    useGameStore.setState({ timeline: [], timelineIndex: -1, isPlaying: false });
    rerender(<TimelineController />);
    expect(screen.getByRole('button', { name: '播放' })).toBeDisabled();
  });

  it('labels icon controls and exposes an operable slider without double handling arrows', () => {
    const seekTo = vi.fn();
    const stepBack = vi.fn();
    const stepForward = vi.fn();
    const play = vi.fn();
    const setSpeed = vi.fn();
    useGameStore.setState({
      timeline,
      timelineIndex: 1,
      phase: 'speech',
      roundNumber: 1,
      seekTo,
      stepBack,
      stepForward,
      play,
      setSpeed,
    });

    render(<TimelineController />);

    expect(screen.getByRole('button', { name: '上一个事件' })).toBeVisible();
    expect(screen.getByRole('button', { name: '播放' })).toBeVisible();
    expect(screen.getByRole('button', { name: '下一个事件' })).toBeVisible();

    const slider = screen.getByRole('slider', { name: '回放进度' });
    expect(slider).toHaveAttribute('tabindex', '0');
    expect(slider).toHaveAttribute('aria-valuemin', '0');
    expect(slider).toHaveAttribute('aria-valuemax', String(timeline.length - 1));
    expect(slider).toHaveAttribute('aria-valuenow', '1');
    expect(slider).toHaveAttribute('aria-valuetext', `第2条，共${timeline.length}条事件`);

    fireEvent.keyDown(slider, { key: 'ArrowRight' });
    expect(seekTo).toHaveBeenLastCalledWith(2);
    expect(stepForward).not.toHaveBeenCalled();
    fireEvent.keyDown(slider, { key: 'ArrowLeft' });
    expect(seekTo).toHaveBeenLastCalledWith(0);
    expect(stepBack).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '上一个事件' }));
    fireEvent.click(screen.getByRole('button', { name: '播放' }));
    fireEvent.click(screen.getByRole('button', { name: '下一个事件' }));
    fireEvent.click(screen.getByRole('button', { name: '2x' }));
    fireEvent.keyDown(window, { key: ' ' });
    expect(stepBack).toHaveBeenCalledOnce();
    expect(play).toHaveBeenCalledTimes(2);
    expect(stepForward).toHaveBeenCalledOnce();
    expect(setSpeed).toHaveBeenCalledWith(2);
  });

  it('preserves global shortcuts, ignores text inputs, and labels the pause state', () => {
    const stepBack = vi.fn();
    const stepForward = vi.fn();
    const pause = vi.fn();
    useGameStore.setState({
      timeline,
      timelineIndex: timeline.length - 1,
      phase: 'game_over',
      roundNumber: 2,
      isPlaying: true,
      isPaused: true,
      stepBack,
      stepForward,
      pause,
    });

    render(
      <>
        <input aria-label="输入框" />
        <TimelineController />
      </>,
    );

    expect(screen.getByRole('button', { name: '暂停' })).toBeVisible();
    expect(screen.getByText('第2轮 · 已结束 (已暂停)')).toBeVisible();
    fireEvent.keyDown(window, { key: 'ArrowLeft' });
    fireEvent.keyDown(window, { key: 'ArrowRight' });
    fireEvent.keyDown(window, { key: ' ' });
    expect(stepBack).toHaveBeenCalledOnce();
    expect(stepForward).toHaveBeenCalledOnce();
    expect(pause).toHaveBeenCalledOnce();

    fireEvent.keyDown(screen.getByRole('textbox', { name: '输入框' }), { key: 'ArrowLeft' });
    expect(stepBack).toHaveBeenCalledOnce();
  });

  it('supports pointer seeking and describes an empty timeline safely', () => {
    const seekTo = vi.fn();
    useGameStore.setState({
      timeline,
      timelineIndex: 0,
      phase: 'unknown' as never,
      seekTo,
    });
    const { rerender } = render(<TimelineController />);
    const slider = screen.getByRole('slider', { name: '回放进度' });
    vi.spyOn(slider, 'getBoundingClientRect').mockReturnValue({
      bottom: 3,
      height: 3,
      left: 0,
      right: 100,
      top: 0,
      width: 100,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });

    fireEvent.click(slider, { clientX: 200 });
    expect(seekTo).toHaveBeenLastCalledWith(timeline.length - 1);
    fireEvent.click(slider, { clientX: -20 });
    expect(seekTo).toHaveBeenLastCalledWith(0);
    fireEvent.keyDown(slider, { key: 'Escape' });
    expect(seekTo).toHaveBeenCalledTimes(2);

    useGameStore.setState({ timeline: [], timelineIndex: -1, phase: 'waiting' });
    rerender(<TimelineController />);
    expect(screen.getByRole('slider', { name: '回放进度' })).toHaveAttribute(
      'aria-valuetext',
      '暂无回放事件',
    );
  });
});

describe('HistoryPanel accessibility', () => {
  it('labels close and lets keyboard users activate interactive event rows', () => {
    const seekTo = vi.fn();
    const pause = vi.fn();
    const onClose = vi.fn();
    useGameStore.setState({ timeline, seekTo, pause });

    render(<HistoryPanel onClose={onClose} />);

    fireEvent.click(screen.getByRole('button', { name: '关闭历史记录' }));
    expect(onClose).toHaveBeenCalledOnce();

    const speechRow = screen.getByRole('button', { name: /1号发言/ });
    expect(speechRow).toHaveAttribute('tabindex', '0');
    fireEvent.keyDown(speechRow, { key: 'Enter' });
    expect(seekTo).toHaveBeenLastCalledWith(1);
    expect(pause).toHaveBeenCalledOnce();
    fireEvent.keyDown(speechRow, { key: ' ' });
    expect(seekTo).toHaveBeenLastCalledWith(1);
    expect(pause).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(speechRow, { key: 'Escape' });
    expect(pause).toHaveBeenCalledTimes(2);
    fireEvent.click(speechRow);
    expect(pause).toHaveBeenCalledTimes(3);

    fireEvent.click(screen.getByRole('tab', { name: /发言/ }));
    expect(screen.getByRole('button', { name: /1号发言/ })).toBeVisible();
    fireEvent.click(screen.getByRole('tab', { name: /投票/ }));
    expect(screen.getByRole('button', { name: /平票/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /2号被放逐/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /弃权/ })).toBeVisible();
    fireEvent.click(screen.getByRole('tab', { name: /死亡/ }));
    expect(screen.getByRole('button', { name: /2号出局/ })).toBeVisible();
  });

  it('shows the empty state for every history category', () => {
    useGameStore.setState({ timeline: [] });
    render(<HistoryPanel onClose={vi.fn()} />);

    for (const tabName of [/全部/, /发言/, /投票/, /死亡/]) {
      fireEvent.click(screen.getByRole('tab', { name: tabName }));
      expect(screen.getByText('暂无记录')).toBeVisible();
    }

    expect(within(screen.getByRole('tablist')).getAllByRole('tab')).toHaveLength(4);
  });
});
