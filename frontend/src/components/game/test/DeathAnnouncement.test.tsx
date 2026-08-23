// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it } from 'vitest';

import DeathAnnouncement from '../DeathAnnouncement';
import type { DeathRecord } from '../../../store/types';

afterEach(() => {
  cleanup();
});

const record = (cause: string): DeathRecord => ({
  player_seat: 9,
  cause: cause as DeathRecord['cause'],
  round_number: 2,
});

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

  it.each([
    ['poison', '毒杀'],
    ['hunter_shot', '猎人带走'],
  ])('maps cause "%s" to a Material icon announcement', (cause, label) => {
    render(<DeathAnnouncement deaths={[record(cause)]} />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(`${label} · 第2轮`)).toBeInTheDocument();
  });

  it('falls back to a neutral icon and raw cause for unknown causes', () => {
    render(<DeathAnnouncement deaths={[record('mysterious_cause')]} />);
    expect(screen.getByText(/mysterious_cause · 第2轮/)).toBeInTheDocument();
  });
});
