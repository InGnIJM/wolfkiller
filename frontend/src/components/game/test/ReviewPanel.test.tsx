// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ReviewPanel from '../ReviewPanel';
import * as client from '../../../api/client';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ReviewPanel', () => {
  it('shows empty state and triggers generation', async () => {
    vi.spyOn(client, 'fetchGameReview').mockResolvedValue({
      game_id: 'g-1',
      status: 'none',
      review: null,
      failure: null,
      generated_at: null,
      model_id: null,
    });
    const genSpy = vi.spyOn(client, 'generateGameReview').mockResolvedValue({
      game_id: 'g-1',
      status: 'pending',
      review: null,
      failure: null,
      generated_at: null,
      model_id: null,
    });

    render(<ReviewPanel gameId="g-1" open onClose={vi.fn()} />);

    expect(await screen.findByText('当前对局尚未生成赛后复盘报告。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '生成赛后复盘' }));

    await waitFor(() => {
      expect(genSpy).toHaveBeenCalledWith('g-1', { force: false });
    });
    expect(await screen.findByText(/正在进行两阶段上帝视角复盘分析/)).toBeInTheDocument();
  });

  it('renders ready review and supports forced regeneration', async () => {
    vi.spyOn(client, 'fetchGameReview').mockResolvedValue({
      game_id: 'g-1',
      status: 'ready',
      review: {
        overview: '整局好人压制狼队。',
        mvp: { seat: 1, role: '预言家', reason: '连验两狼' },
        turning_points: [{ round: 2, title: '放逐狼王', detail: '2号出局' }],
        mistakes: [{ seat: 3, title: '站错边', detail: '首日误投' }],
        camp_assessment: { good: '好人稳健', werewolf: '狼队被动', third_party: '无' },
        closing: '正义必胜',
        round_digests: [{ round: 1, digest: '首轮摘要' }],
      },
      failure: null,
      generated_at: '2026-10-29T00:00:00Z',
      model_id: 'm-1',
    });
    const genSpy = vi.spyOn(client, 'generateGameReview').mockResolvedValue({
      game_id: 'g-1',
      status: 'pending',
      review: null,
      failure: null,
      generated_at: null,
      model_id: null,
    });

    render(<ReviewPanel gameId="g-1" open onClose={vi.fn()} />);

    expect(await screen.findByText('整局好人压制狼队。')).toBeInTheDocument();
    expect(screen.getByText('1号 · 预言家')).toBeInTheDocument();
    expect(screen.getByText('第2轮 · 放逐狼王')).toBeInTheDocument();
    expect(screen.getByText('3号 · 站错边')).toBeInTheDocument();
    expect(screen.getByText('正义必胜')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重新生成' }));
    await waitFor(() => {
      expect(genSpy).toHaveBeenCalledWith('g-1', { force: true });
    });
  });

  it('shows failure state and retry button', async () => {
    vi.spyOn(client, 'fetchGameReview').mockResolvedValue({
      game_id: 'g-1',
      status: 'failed',
      review: null,
      failure: 'provider_timeout',
      generated_at: '2026-10-29T00:00:00Z',
      model_id: 'm-1',
    });

    render(<ReviewPanel gameId="g-1" open onClose={vi.fn()} />);

    expect(await screen.findByText(/复盘生成失败（provider_timeout）/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试生成' })).toBeInTheDocument();
  });
});
