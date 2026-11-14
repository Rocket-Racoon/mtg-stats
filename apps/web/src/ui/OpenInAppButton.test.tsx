import { render, screen } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { routes } from '../routes';
import { useSessionStore } from '../session/store';
import { OpenInAppButton, LINK_KINDS } from './OpenInAppButton';
import { isMobileBrowser, type NavigatorLike } from './mobile-browser';

const PHONE: NavigatorLike = { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)' };
const DESKTOP: NavigatorLike = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('isMobileBrowser', () => {
  it.each<[string, NavigatorLike, boolean]>([
    ['UA client hint mobile', { userAgentData: { mobile: true } }, true],
    ['Android UA', { userAgent: 'Mozilla/5.0 (Linux; Android 14; Pixel 8)' }, true],
    ['iPhone UA', { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)' }, true],
    ['iPad UA', { userAgent: 'Mozilla/5.0 (iPad; CPU OS 17_0)' }, true],
    ['iPod UA', { userAgent: 'Mozilla/5.0 (iPod touch)' }, true],
    [
      'iPadOS desktop mode',
      { userAgent: 'Macintosh', platform: 'MacIntel', maxTouchPoints: 5 },
      true,
    ],
    [
      'Mac without touch',
      { userAgent: 'Macintosh', platform: 'MacIntel', maxTouchPoints: 0 },
      false,
    ],
    ['Windows desktop', DESKTOP, false],
    ['hint says not mobile, desktop UA', { ...DESKTOP, userAgentData: { mobile: false } }, false],
    ['empty navigator', {}, false],
  ])('%s', (_name, nav, expected) => {
    expect(isMobileBrowser(nav)).toBe(expected);
  });

  it('is false when navigator is undefined', () => {
    vi.stubGlobal('navigator', undefined);
    expect(isMobileBrowser()).toBe(false);
  });
});

describe('OpenInAppButton', () => {
  it.each(LINK_KINDS)('links to mtgstats://%s/<value> on a mobile browser', (kind) => {
    render(<OpenInAppButton kind={kind} value="abc123" nav={PHONE} />);
    expect(screen.getByRole('link', { name: 'Open in app' })).toHaveAttribute(
      'href',
      `mtgstats://${kind}/abc123`,
    );
  });

  it('encodes slash, space, question mark, hash and unicode in the value', () => {
    render(<OpenInAppButton kind="join" value={'a/b c?d#e é'} nav={PHONE} />);
    expect(screen.getByRole('link', { name: 'Open in app' })).toHaveAttribute(
      'href',
      'mtgstats://join/a%2Fb%20c%3Fd%23e%20%C3%A9',
    );
  });

  it.each(LINK_KINDS)('renders nothing on a desktop browser (%s)', (kind) => {
    const { container } = render(<OpenInAppButton kind={kind} value="abc" nav={DESKTOP} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing for an empty value', () => {
    const { container } = render(<OpenInAppButton kind="invite" value="" nav={PHONE} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('public pages (S-1.6.7 AC2, AC3)', () => {
  beforeEach(() => {
    useSessionStore.setState({ status: 'signed_out' });
  });

  it('shows /join/abc to a signed-out visitor on mobile with an mtgstats://join/abc link', () => {
    vi.stubGlobal('navigator', PHONE);
    render(
      <RouterProvider router={createMemoryRouter(routes, { initialEntries: ['/join/abc'] })} />,
    );
    expect(screen.getByRole('heading', { level: 2, name: 'Join' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open in app' })).toHaveAttribute(
      'href',
      'mtgstats://join/abc',
    );
  });

  it('shows the page without the link on a desktop browser', () => {
    vi.stubGlobal('navigator', DESKTOP);
    render(
      <RouterProvider router={createMemoryRouter(routes, { initialEntries: ['/reset/tok'] })} />,
    );
    expect(screen.getByRole('heading', { level: 2, name: 'Reset password' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open in app' })).not.toBeInTheDocument();
  });
});
