// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it } from 'vitest';

import SeatHoverCard from '../SeatHoverCard';
import { CARD_BACK, ROLE_COLORS, roleArt } from '../../../theme/tokens';

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

  it('shows the role portrait and keeps the copy on the card surface', () => {
    const { container } = render(
      <SeatHoverCard
        seat={1}
        roman="Ⅰ"
        roleLabel="狼人"
        camp="werewolf"
        isAlive
        isSheriff={false}
        isCurrentSpeaker={false}
        accent={ROLE_COLORS.werewolf.color}
        roleId="wolf-killer-werewolf"
      />,
    );
    expect(container.querySelector('img'))
      .toHaveAttribute('src', roleArt('wolf-killer-werewolf'));
    expect(screen.getByText('狼人 · 狼人阵营')).toBeInTheDocument();
  });

  it('shows the card back when the identity is still hidden', () => {
    const { container } = render(
      <SeatHoverCard
        seat={4}
        roman="Ⅳ"
        isAlive
        isSheriff={false}
        isCurrentSpeaker={false}
        accent={ROLE_COLORS.villager.color}
      />,
    );
    expect(container.querySelector('img')).toHaveAttribute('src', CARD_BACK);
  });

  it('shows the skill summary and rule bullets for a revealed role', () => {
    render(
      <SeatHoverCard
        seat={9}
        roman="Ⅸ"
        roleLabel="奇迹商人"
        camp="good"
        isAlive
        isSheriff={false}
        isCurrentSpeaker={false}
        accent={ROLE_COLORS.hunter.color}
        roleId="wolf-killer-miracle-merchant"
        description="每晚把一次查验、毒药或守护赠给幸运儿"
        rules={[
          '每晚最先行动，指定一名幸运儿并赠出一项技能。',
          '幸运儿是狼人时，商人次日发言结束后出局。',
        ]}
      />,
    );
    expect(screen.getByText('每晚把一次查验、毒药或守护赠给幸运儿')).toBeInTheDocument();
    expect(screen.getByTestId('seat-hover-rules')).toBeInTheDocument();
    expect(screen.getByText('每晚最先行动，指定一名幸运儿并赠出一项技能。')).toBeInTheDocument();
    expect(screen.getByText('幸运儿是狼人时，商人次日发言结束后出局。')).toBeInTheDocument();
  });

  it('omits the skill block when the identity is still hidden', () => {
    render(
      <SeatHoverCard
        seat={4}
        roman="Ⅳ"
        isAlive
        isSheriff={false}
        isCurrentSpeaker={false}
        accent={ROLE_COLORS.villager.color}
        description="不应出现的简介"
        rules={['不应出现的规则']}
      />,
    );
    expect(screen.queryByText('不应出现的简介')).not.toBeInTheDocument();
    expect(screen.queryByText('不应出现的规则')).not.toBeInTheDocument();
    expect(screen.queryByTestId('seat-hover-rules')).not.toBeInTheDocument();
  });
});
