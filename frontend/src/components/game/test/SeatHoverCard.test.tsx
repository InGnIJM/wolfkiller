// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it } from 'vitest';

import SeatHoverCard from '../SeatHoverCard';
import { ROLE_COLORS } from '../../../theme/tokens';

afterEach(() => cleanup());

describe('SeatHoverCard', () => {
  it('shows identity, live status, and the assigned model', () => {
    render(
      <SeatHoverCard
        seat={1}
        roman="Ⅰ"
        roleLabel="狼人"
        camp="werewolf"
        isAlive
        isSheriff
        isCurrentSpeaker
        accent={ROLE_COLORS.werewolf.color}
        model={{
          name: 'Model A',
          model_id: 'provider/model-a',
          provider_profile: 'openrouter',
        }}
      />,
    );

    expect(screen.getByText('Ⅰ · 1号')).toBeInTheDocument();
    expect(screen.getByText('狼人 · 狼人阵营')).toBeInTheDocument();
    expect(screen.getByText('存活 · 警长 · 发言中')).toBeInTheDocument();
    expect(screen.getByText('Model A')).toBeInTheDocument();
    expect(screen.getByText('provider/model-a')).toBeInTheDocument();
    expect(screen.getByText('OpenRouter')).toBeInTheDocument();
  });

  it('shows unknown model and omits unrecognized role ids', () => {
    render(
      <SeatHoverCard
        seat={3}
        roman="Ⅲ"
        camp="good"
        isAlive={false}
        isSheriff={false}
        isCurrentSpeaker={false}
        accent={ROLE_COLORS.villager.color}
      />,
    );

    expect(screen.getByText('Ⅲ · 3号')).toBeInTheDocument();
    expect(screen.getByText('好人阵营')).toBeInTheDocument();
    expect(screen.getByText('出局')).toBeInTheDocument();
    expect(screen.getByText('未知')).toBeInTheDocument();
    expect(screen.queryByText(/wolf-killer/)).not.toBeInTheDocument();
  });

  it('appends the current vote on the status line', () => {
    const { rerender } = render(
      <SeatHoverCard
        seat={2}
        roman="Ⅱ"
        isAlive
        isSheriff={false}
        isCurrentSpeaker={false}
        voteTarget={5}
        accent={ROLE_COLORS.villager.color}
      />,
    );
    expect(screen.getByText('存活 · → 5号')).toBeInTheDocument();

    rerender(
      <SeatHoverCard
        seat={2}
        roman="Ⅱ"
        isAlive
        isSheriff={false}
        isCurrentSpeaker={false}
        voteTarget={null}
        accent={ROLE_COLORS.villager.color}
      />,
    );
    expect(screen.getByText('存活 · 弃权')).toBeInTheDocument();
  });
});
