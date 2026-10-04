// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it } from 'vitest';

import RolePortrait from '../RolePortrait';
import { CARD_BACK, ROLE_ART_BY_ID, roleArt } from '../../../theme/tokens';

afterEach(cleanup);

/** `alt=""` 让 img 变成 presentation 角色，所以按标签查而不是按 role 查。 */
function portraitImage(container: HTMLElement): HTMLImageElement {
  const img = container.querySelector('img');
  if (img === null) throw new Error('portrait image is missing');
  return img;
}

describe('RolePortrait', () => {
  it("renders the role's own portrait", () => {
    const { container } = render(<RolePortrait roleId="wolf-killer-seer" width={64} />);
    expect(portraitImage(container)).toHaveAttribute('src', ROLE_ART_BY_ID['wolf-killer-seer']);
  });

  it('renders the card back plus a name watermark when the role has no portrait', () => {
    const { container } = render(<RolePortrait roleId="wolf-killer-gargoyle" width={64} />);
    expect(portraitImage(container)).toHaveAttribute('src', CARD_BACK);
    // 卡背不是空白：叠了一层角色名，免得整个格子看不出是什么。
    expect(screen.getByText('石像鬼')).toBeInTheDocument();
  });

  it('shows no watermark for a role that has its own portrait', () => {
    render(<RolePortrait roleId="wolf-killer-seer" width={64} />);
    expect(screen.queryByText('预言家')).not.toBeInTheDocument();
  });

  it('can turn the watermark off', () => {
    render(<RolePortrait roleId="wolf-killer-gargoyle" width={64} showLabel={false} />);
    expect(screen.queryByText('石像鬼')).not.toBeInTheDocument();
  });

  it('falls back to the card back for a missing or unknown role', () => {
    for (const roleId of [null, undefined, '', 'wolf-killer-unknown']) {
      const { container, unmount } = render(<RolePortrait roleId={roleId} width={64} />);
      expect(portraitImage(container)).toHaveAttribute('src', CARD_BACK);
      unmount();
    }
  });

  it('labels an unknown role rather than leaving the watermark blank', () => {
    render(<RolePortrait roleId="wolf-killer-unknown" width={64} />);
    expect(screen.getByText('未知身份')).toBeInTheDocument();
  });

  it('is decorative by default so the row is not announced twice', () => {
    const { container } = render(<RolePortrait roleId="wolf-killer-seer" width={64} />);
    expect(portraitImage(container)).toHaveAttribute('alt', '');
    // 装饰性图片不该有 img 角色，读屏会跳过它。
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('becomes a labelled image when asked', () => {
    render(<RolePortrait roleId="wolf-killer-seer" width={64} alt="预言家立绘" />);
    expect(screen.getByAltText('预言家立绘')).toBeInTheDocument();
    expect(screen.getByRole('img')).toBeInTheDocument();
  });

  it('goes through the shared art lookup, not a local copy', () => {
    const { container } = render(<RolePortrait roleId="wolf-killer-wolf-king" width={64} />);
    expect(portraitImage(container)).toHaveAttribute('src', roleArt('wolf-killer-wolf-king'));
  });

  it('lazy-loads and reserves space so the list does not jump', () => {
    const { container } = render(<RolePortrait roleId="wolf-killer-seer" width={64} />);
    const img = portraitImage(container);
    expect(img).toHaveAttribute('loading', 'lazy');
    expect(img).toHaveAttribute('decoding', 'async');
  });
});
