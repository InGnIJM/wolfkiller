// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchRoleCatalog } from '../../../api/client';
import type { ModelSnapshotEntry, PublicPlayerState, RoleCatalogItem } from '../../../store/types';
import { CARD_BACK, roleArt } from '../../../theme/tokens';
import SeatMap from '../SeatMap';

vi.mock('../../../api/client', () => ({
  fetchRoleCatalog: vi.fn(),
}));

const WEREWOLF_CATALOG: RoleCatalogItem = {
  role_id: 'wolf-killer-werewolf',
  display_name: '狼人',
  name_zh: '狼人',
  camp: 'werewolf',
  icon: 'wolf',
  description: '夜晚睁眼，与狼队友商议刀人',
  rules: ['夜晚与狼队友共用同一次狼刀。'],
  max_count: null
};

class MockResizeObserver {
  static instances: MockResizeObserver[] = [];

  callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    MockResizeObserver.instances.push(this);
  }

  observe() {}
  unobserve() {}
  disconnect() {}
}

function fireResize(width: number, height: number) {
  const entry = { contentRect: { width, height } } as ResizeObserverEntry;
  for (const instance of MockResizeObserver.instances) {
    act(() => instance.callback([entry], instance as unknown as ResizeObserver));
  }
}

beforeEach(() => {
  MockResizeObserver.instances = [];
  vi.stubGlobal('ResizeObserver', MockResizeObserver);
  vi.mocked(fetchRoleCatalog).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('SeatMap role badges', () => {
  it('renders a role badge for known roles and none for a player without a role', () => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-werewolf', camp: 'werewolf' },
      2: { seat_number: 2, is_alive: true, is_sheriff: false, role: 'wolf-killer-villager', camp: 'good' },
      3: { seat_number: 3, is_alive: true, is_sheriff: false },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    expect(screen.getByText('狼人')).toBeInTheDocument();
    expect(screen.getByText('平民')).toBeInTheDocument();
    expect(screen.getAllByText(/狼人|女巫|预言家|猎人|平民|守卫/)).toHaveLength(2);
  });

  it('renders a badge for every known role', () => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-werewolf', camp: 'werewolf' },
      2: { seat_number: 2, is_alive: true, is_sheriff: false, role: 'wolf-killer-witch', camp: 'good' },
      3: { seat_number: 3, is_alive: true, is_sheriff: false, role: 'wolf-killer-seer', camp: 'good' },
      4: { seat_number: 4, is_alive: true, is_sheriff: false, role: 'wolf-killer-hunter', camp: 'good' },
      5: { seat_number: 5, is_alive: true, is_sheriff: false, role: 'wolf-killer-villager', camp: 'good' },
      6: { seat_number: 6, is_alive: true, is_sheriff: false, role: 'wolf-killer-guard', camp: 'good' },
      7: { seat_number: 7, is_alive: true, is_sheriff: false, role: 'wolf-killer-idiot', camp: 'good' },
      8: { seat_number: 8, is_alive: true, is_sheriff: false, role: 'wolf-killer-werewolf-king', camp: 'werewolf' },
      9: { seat_number: 9, is_alive: true, is_sheriff: false, role: 'wolf-killer-knight', camp: 'good' },
      10: { seat_number: 10, is_alive: true, is_sheriff: false, role: 'wolf-killer-wolf-beauty', camp: 'werewolf' },
      11: { seat_number: 11, is_alive: true, is_sheriff: false, role: 'wolf-killer-old-drunkard', camp: 'good' },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    expect(screen.getByText('狼人')).toBeInTheDocument();
    expect(screen.getByText('女巫')).toBeInTheDocument();
    expect(screen.getByText('预言家')).toBeInTheDocument();
    expect(screen.getByText('猎人')).toBeInTheDocument();
    expect(screen.getByText('平民')).toBeInTheDocument();
    expect(screen.getByText('守卫')).toBeInTheDocument();
    expect(screen.getByText('白痴')).toBeInTheDocument();
    expect(screen.getByText('白狼王')).toBeInTheDocument();
    expect(screen.getByText('骑士')).toBeInTheDocument();
    expect(screen.getByText('狼美人')).toBeInTheDocument();
    expect(screen.getByText('老酒鬼')).toBeInTheDocument();
  });

  it.each([
    ['wolf-killer-werewolf', '狼人'],
    ['wolf-killer-villager', '平民'],
    ['wolf-killer-seer', '预言家'],
    ['wolf-killer-witch', '女巫'],
    ['wolf-killer-hunter', '猎人'],
    ['wolf-killer-guard', '守卫'],
    ['wolf-killer-idiot', '白痴'],
    ['wolf-killer-werewolf-king', '白狼王'],
    ['wolf-killer-knight', '骑士'],
    ['wolf-killer-wolf-beauty', '狼美人'],
    ['wolf-killer-old-drunkard', '老酒鬼'],
  ])('names %s on the seat card as %s', (role, label) => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role, camp: 'good' },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    expect(screen.getByLabelText(`1号 ${label} 存活`)).toBeInTheDocument();
  });

  it('marks a flipped seat as alive without a ballot', async () => {
    const user = userEvent.setup();
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-idiot', camp: 'good', can_vote: false },
      2: { seat_number: 2, is_alive: false, is_sheriff: false, role: 'wolf-killer-idiot', camp: 'good', can_vote: false },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    expect(screen.getByLabelText('1号 白痴 存活·无投票权')).toBeInTheDocument();
    expect(screen.getByLabelText('2号 白痴 出局')).toBeInTheDocument();

    await user.hover(screen.getByLabelText('1号 白痴 存活·无投票权'));
    const card = await screen.findByRole('tooltip');
    expect(card).toHaveTextContent('存活 · 无投票权');
  });

  it('labels each seat card with the role for god view', () => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-witch', camp: 'good' },
      2: { seat_number: 2, is_alive: false, is_sheriff: true, role: 'wolf-killer-seer', camp: 'good' },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    expect(screen.getByLabelText('1号 女巫 存活')).toBeInTheDocument();
    expect(screen.getByLabelText('2号 预言家 出局 警长')).toBeInTheDocument();
  });

  it('labels a card without a role with seat and status only', () => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    expect(screen.getByLabelText('1号 存活')).toBeInTheDocument();
  });

  it('renders nothing for an unknown role instead of leaking the raw role string', () => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-unknown', camp: 'werewolf' },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    expect(screen.queryByText(/wolf-killer-unknown/)).not.toBeInTheDocument();
    expect(screen.queryAllByText(/狼人|女巫|预言家|猎人|平民|守卫/)).toHaveLength(0);
    expect(screen.getByLabelText('1号 存活')).toBeInTheDocument();
  });

  it('paints the role portrait in the seat and the card back when the role is hidden', () => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-werewolf', camp: 'werewolf' },
      2: { seat_number: 2, is_alive: true, is_sheriff: false },
    };

    render(<SeatMap players={players} />);
    fireResize(800, 600);

    const wolf = screen.getByLabelText('1号 狼人 存活');
    const hidden = screen.getByLabelText('2号 存活');
    expect(wolf.querySelector('img'))
      .toHaveAttribute('src', roleArt('wolf-killer-werewolf'));
    expect(hidden.querySelector('img')).toHaveAttribute('src', CARD_BACK);
    expect(wolf).not.toHaveTextContent('存活');
    expect(wolf).toHaveTextContent('狼人');
  });

  it('keeps the vote line on the seat nameplate', () => {
    const players: Record<number, PublicPlayerState> = {
      1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-wolf-beauty', camp: 'werewolf' },
      2: { seat_number: 2, is_alive: true, is_sheriff: false, role: 'wolf-killer-villager', camp: 'good' },
    };

    render(<SeatMap players={players} voteTargets={{ 1: 5, 2: null }} />);
    fireResize(800, 600);

    expect(screen.getByText('→ 5号')).toBeInTheDocument();
    expect(screen.getByText('弃权')).toBeInTheDocument();
    expect(screen.getByText('狼美人')).toBeInTheDocument();
  });
});

describe('SeatMap sheriff election sides', () => {
  const players: Record<number, PublicPlayerState> = {
    1: { seat_number: 1, is_alive: true, is_sheriff: false, role: 'wolf-killer-seer', camp: 'good' },
    2: { seat_number: 2, is_alive: true, is_sheriff: false, role: 'wolf-killer-werewolf', camp: 'werewolf' },
    3: { seat_number: 3, is_alive: false, is_sheriff: false, role: 'wolf-killer-villager', camp: 'good' },
  };

  it('marks the on-badge and off-badge seats during the election', async () => {
    const user = userEvent.setup();
    render(
      <SeatMap
        players={players}
        phase="sheriff_election"
        badgeCandidates={[1]}
        offBadgeSeats={[2]}
      />,
    );
    fireResize(800, 600);

    expect(screen.getByLabelText('1号 预言家 存活 警上')).toBeInTheDocument();
    expect(screen.getByLabelText('2号 狼人 存活 警下')).toBeInTheDocument();
    expect(screen.getByText('警上')).toBeInTheDocument();
    expect(screen.getByText('警下')).toBeInTheDocument();

    await user.hover(screen.getByLabelText('1号 预言家 存活 警上'));
    expect(await screen.findByRole('tooltip')).toHaveTextContent('存活 · 警上');
  });

  it('keeps the sides off the seats outside the election phase', () => {
    render(
      <SeatMap players={players} phase="night" badgeCandidates={[1]} offBadgeSeats={[2]} />,
    );
    fireResize(800, 600);

    expect(screen.queryByText('警上')).not.toBeInTheDocument();
    expect(screen.queryByText('警下')).not.toBeInTheDocument();
    expect(screen.getByLabelText('1号 预言家 存活')).toBeInTheDocument();
    expect(screen.getByLabelText('2号 狼人 存活')).toBeInTheDocument();
  });

  it('drops the side mark for a seat that died during the election', () => {
    render(
      <SeatMap players={players} phase="sheriff_election" badgeCandidates={[3]} offBadgeSeats={[1]} />,
    );
    fireResize(800, 600);

    expect(screen.getByLabelText('3号 平民 出局')).toBeInTheDocument();
    expect(screen.getByText('警下')).toBeInTheDocument();
    expect(screen.queryByText('警上')).not.toBeInTheDocument();
  });
});

describe('SeatMap hover card', () => {
  const snapshot: ModelSnapshotEntry[] = [{
    config_id: 'model-a',
    name: 'Model A',
    model_id: 'provider/model-a',
    base_url: 'https://models.example/v1',
    provider_profile: 'openrouter',
    count: 1,
    seats: [1],
  }];

  it('shows the assigned model after hovering a seat', async () => {
    const user = userEvent.setup();
    render(
      <SeatMap
        players={{
          1: {
            seat_number: 1, is_alive: true, is_sheriff: true,
            role: 'wolf-killer-werewolf', camp: 'werewolf',
          },
        }}
        currentSpeaker={1}
        modelSnapshot={snapshot}
      />,
    );
    fireResize(800, 600);

    await user.hover(screen.getByLabelText('1号 狼人 存活 警长'));
    const card = await screen.findByRole('tooltip');
    expect(card).toHaveTextContent('Model A');
    expect(card).toHaveTextContent('provider/model-a');
    expect(card).toHaveTextContent('OpenRouter');
    expect(card).toHaveTextContent('存活 · 警长 · 发言中');
  });

  it('shows unknown when the seat has no model mapping and hides raw role ids', async () => {
    const user = userEvent.setup();
    render(
      <SeatMap
        players={{
          1: {
            seat_number: 1, is_alive: true, is_sheriff: false,
            role: 'wolf-killer-unknown', camp: 'werewolf',
          },
        }}
      />,
    );
    fireResize(800, 600);

    await user.hover(screen.getByLabelText('1号 存活'));
    const card = await screen.findByRole('tooltip');
    expect(card).toHaveTextContent('未知');
    expect(card).not.toHaveTextContent('wolf-killer-unknown');
  });

  it('shows catalog skill copy after the directory loads', async () => {
    vi.mocked(fetchRoleCatalog).mockResolvedValue([WEREWOLF_CATALOG]);
    const user = userEvent.setup();
    render(
      <SeatMap
        players={{
          1: {
            seat_number: 1, is_alive: true, is_sheriff: true,
            role: 'wolf-killer-werewolf', camp: 'werewolf',
          },
        }}
        currentSpeaker={1}
        modelSnapshot={snapshot}
      />,
    );
    fireResize(800, 600);

    await user.hover(screen.getByLabelText('1号 狼人 存活 警长'));
    const card = await screen.findByRole('tooltip');
    await waitFor(() => {
      expect(card).toHaveTextContent('夜晚睁眼，与狼队友商议刀人');
    });
    expect(card).toHaveTextContent('夜晚与狼队友共用同一次狼刀。');
  });

  it('keeps the hover card usable when the catalog request fails', async () => {
    vi.mocked(fetchRoleCatalog).mockRejectedValue(new Error('catalog down'));
    const user = userEvent.setup();
    render(
      <SeatMap
        players={{
          1: {
            seat_number: 1, is_alive: true, is_sheriff: false,
            role: 'wolf-killer-werewolf', camp: 'werewolf',
          },
        }}
      />,
    );
    fireResize(800, 600);

    await user.hover(screen.getByLabelText('1号 狼人 存活'));
    const card = await screen.findByRole('tooltip');
    expect(card).toHaveTextContent('存活');
    expect(card).not.toHaveTextContent('夜晚睁眼');
  });
});

function nVillagers(count: number): Record<number, PublicPlayerState> {
  return Object.fromEntries(
    Array.from({ length: count }, (_, index) => {
      const seat = index + 1;
      return [seat, {
        seat_number: seat,
        is_alive: true,
        is_sheriff: false,
        role: 'wolf-killer-villager',
        camp: 'good',
      } satisfies PublicPlayerState];
    }),
  );
}

describe('SeatMap stage layout', () => {
  it('uses compact columns on a narrow 12-seat stage and still shows every seat', () => {
    render(<SeatMap players={nVillagers(12)} />);
    fireResize(500, 400);

    expect(document.querySelector('[data-layout="compact"]')).toBeInTheDocument();
    for (let seat = 1; seat <= 12; seat += 1) {
      expect(screen.getByLabelText(`${seat}号 平民 存活`)).toBeInTheDocument();
    }
  });

  it('keeps the ellipse on a wide short 12-seat stage', () => {
    render(<SeatMap players={nVillagers(12)} />);
    fireResize(1100, 360);

    expect(document.querySelector('[data-layout="ellipse"]')).toBeInTheDocument();
  });

  it('keeps a 9-seat 800x600 board on the ellipse', () => {
    render(<SeatMap players={nVillagers(9)} />);
    fireResize(800, 600);

    expect(document.querySelector('[data-layout="ellipse"]')).toBeInTheDocument();
  });
});

