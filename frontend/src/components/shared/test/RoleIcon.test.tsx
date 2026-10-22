// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it } from 'vitest';

import RoleIcon from '../RoleIcon';

afterEach(() => {
  cleanup();
});

describe('RoleIcon', () => {
  it('renders nothing when the role is unknown-yet (null)', () => {
    const { container } = render(<RoleIcon role={null} />);
    expect(container).toBeEmptyDOMElement();
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
    ['wolf-killer-fool-seer', '愚言家'],
    ['wolf-killer-iron-villager', '铁好人'],
    ['wolf-killer-crow', '乌鸦'],
    ['wolf-killer-cupid', '丘比特'],
    ['wolf-killer-admirer', '暗恋者'],
    ['wolf-killer-awakened-hunter', '觉醒猎人'],
    ['wolf-killer-awakened-wolf-beauty', '觉醒狼美人'],
    ['wolf-killer-crimson-envoy', '赤月使徒'],
    ['wolf-killer-dancer', '舞者'],
    ['wolf-killer-demon-hunter', '猎魔人'],
    ['wolf-killer-mask', '假面'],
    ['wolf-killer-nightmare', '噩梦之影'],
    ['wolf-killer-pure-white', '纯白之女'],
    ['wolf-killer-wolf-witch', '狼巫'],
  ])('renders a labelled Material icon for %s', (role, label) => {
    render(<RoleIcon role={role} />);
    expect(screen.getByLabelText(label)).toBeInTheDocument();
  });

  it('falls back to a neutral icon for unrecognized role ids', () => {
    render(<RoleIcon role="wolf-killer-unknown" />);
    expect(screen.getByLabelText('未知身份')).toBeInTheDocument();
  });
});
