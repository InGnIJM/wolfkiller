// @vitest-environment jsdom

import { act, cleanup, fireEvent, render } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';

import PageBackground from '../PageBackground';
import { BACKDROP, BACKGROUNDS, CANVAS } from '../../../theme/tokens';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('PageBackground', () => {
  it.each(['main', 'lobby', 'game'] as const)('uses the %s plate', (variant) => {
    const { container } = render(<PageBackground variant={variant} />);
    const img = container.querySelector('img');
    expect(img).toHaveAttribute('src', BACKGROUNDS[variant]);
    expect(img).toHaveStyle({ opacity: '0' });
    expect(container.firstChild).toHaveStyle({ backgroundColor: CANVAS.bg });
  });

  it('fades the plate in 300ms after it loads', () => {
    vi.useFakeTimers();
    const { container } = render(<PageBackground variant="main" />);
    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    fireEvent.load(img!);
    expect(img).toHaveStyle({ opacity: '0' });
    act(() => {
      vi.advanceTimersByTime(BACKDROP.fadeMs);
    });
    expect(img).toHaveStyle({ opacity: '1' });
  });

  it('drops the image and keeps the canvas when loading fails', () => {
    const { container } = render(<PageBackground variant="game" />);
    const img = container.querySelector('img');
    fireEvent.error(img!);
    expect(container.querySelector('img')).toBeNull();
    expect(container.firstChild).toHaveStyle({ backgroundColor: CANVAS.bg });
  });

  it('can sit inside a page slot instead of the viewport', () => {
    const { container } = render(<PageBackground variant="lobby" placement="contained" />);
    expect(container.firstChild).toHaveStyle({ position: 'absolute' });
  });
});
