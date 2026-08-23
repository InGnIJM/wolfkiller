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
    ['wolf-killer-villager', '村民'],
    ['wolf-killer-seer', '预言家'],
    ['wolf-killer-witch', '女巫'],
    ['wolf-killer-hunter', '猎人'],
    ['wolf-killer-guard', '守卫'],
  ])('renders a labelled Material icon for %s', (role, label) => {
    render(<RoleIcon role={role} />);
    expect(screen.getByLabelText(label)).toBeInTheDocument();
  });

  it('falls back to a neutral icon for unrecognized role ids', () => {
    render(<RoleIcon role="wolf-killer-unknown" />);
    expect(screen.getByLabelText('未知身份')).toBeInTheDocument();
  });
});
