// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it } from 'vitest';

import DeathAnnouncement from '../DeathAnnouncement';
import type { DeathCause, DeathRecord } from '../../../store/types';

afterEach(() => {
  cleanup();
});

const record = (cause: string): DeathRecord => ({
  player_seat: 9,
  cause: cause as DeathRecord['cause'],
  round_number: 2,
});

// Typed as DeathCause so a cause missing from the union fails `tsc`, not just the render.
const CAUSE_CASES: Array<[DeathCause, string]> = [
  ['wolf_kill', '夜间死亡'],
  ['poison', '毒杀'],
  ['exile', '放逐'],
  ['hunter_shot', '猎人带走'],
  ['self_explode', '白狼王自爆'],
  ['knight_duel', '骑士决斗'],
  ['charm', '殉情'],
];

describe('DeathAnnouncement', () => {
  it('renders nothing when there are no deaths', () => {
    const { container } = render(<DeathAnnouncement deaths={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('announces only the latest death with its cause', () => {
    render(
      <DeathAnnouncement
        deaths={[record('exile'), { player_seat: 3, cause: 'wolf_kill', round_number: 3 }]}
      />,
    );

    expect(screen.getByText('3号玩家出局')).toBeInTheDocument();
    expect(screen.getByText(/夜间死亡 · 第3轮/)).toBeInTheDocument();
  });

  it('shows the revealed role only when provided', () => {
    const { rerender } = render(<DeathAnnouncement deaths={[record('exile')]} />);
    expect(screen.queryByText(/身份：/)).not.toBeInTheDocument();

    rerender(
      <DeathAnnouncement deaths={[record('exile')]} revealedRole="wolf-killer-werewolf" />,
    );
    expect(screen.getByText('身份：狼人')).toBeInTheDocument();
  });

  it.each([
    ['wolf-killer-werewolf', '狼人'],
    ['wolf-killer-villager', '村民'],
    ['wolf-killer-seer', '预言家'],
    ['wolf-killer-witch', '女巫'],
    ['wolf-killer-hunter', '猎人'],
    ['wolf-killer-guard', '守卫'],
    ['wolf-killer-idiot', '白痴'],
    ['wolf-killer-werewolf-king', '白狼王'],
    ['wolf-killer-knight', '骑士'],
    ['wolf-killer-wolf-beauty', '狼美人'],
    ['wolf-killer-old-drunkard', '老酒鬼'],
  ])('names the revealed role %s as %s', (role, label) => {
    render(<DeathAnnouncement deaths={[record('exile')]} revealedRole={role} />);
    expect(screen.getByText(`身份：${label}`)).toBeInTheDocument();
  });

  it.each(CAUSE_CASES)('maps cause "%s" to the %s announcement', (cause, label) => {
    render(<DeathAnnouncement deaths={[record(cause)]} />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(`${label} · 第2轮`)).toBeInTheDocument();
  });

  it('falls back to a neutral icon and raw cause for unknown causes', () => {
    render(<DeathAnnouncement deaths={[record('mysterious_cause')]} />);
    expect(screen.getByText(/mysterious_cause · 第2轮/)).toBeInTheDocument();
  });
});
