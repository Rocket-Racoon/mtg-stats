import { render, screen } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { routes } from '../routes';
import { useSessionStore } from '../session/store';
import { OpenInAppButton, LINK_KINDS } from './OpenInAppButton';
import { isAndroidBrowser, type NavigatorLike } from './android-browser';

const ANDROID_PHONE: NavigatorLike = {
  userAgent:
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36',
};
const ANDROID_TABLET: NavigatorLike = {
  userAgent:
    'Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
  userAgentData: { mobile: false },
};
const IPHONE: NavigatorLike = { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)' };
const DESKTOP: NavigatorLike = { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('isAndroidBrowser', () => {
  it.each<[string, NavigatorLike, boolean]>([
    ['UA client hint platform Android', { userAgentData: { platform: 'Android' } }, true],
    ['Android phone UA', ANDROID_PHONE, true],
    ['Android tablet UA with mobile false', ANDROID_TABLET, true],
    ['hint mobile true alone', { userAgentData: { mobile: true } }, false],
    ['iPhone UA', IPHONE, false],
    ['iPad UA', { userAgent: 'Mozilla/5.0 (iPad; CPU OS 17_0)' }, false],
    [
      'iPadOS desktop mode',
      { userAgent: 'Macintosh', platform: 'MacIntel', maxTouchPoints: 5 },
      false,
    ],
    ['Windows desktop', DESKTOP, false],
    ['empty navigator', {}, false],
  ])('%s', (_name, nav, expected) => {
    expect(isAndroidBrowser(nav)).toBe(expected);
  });

  it('is false when navigator is undefined', () => {
    vi.stubGlobal('navigator', undefined);
    expect(isAndroidBrowser()).toBe(false);
  });
});

describe('OpenInAppButton', () => {
  it.each(LINK_KINDS)('links to mtgstats://%s/<value> on an Android phone', (kind) => {
    render(<OpenInAppButton kind={kind} value="abc123" nav={ANDROID_PHONE} />);
    expect(screen.getByRole('link', { name: 'Open in app' })).toHaveAttribute(
      'href',
      `mtgstats://${kind}/abc123`,
    );
  });

  it.each(LINK_KINDS)('links to mtgstats://%s/<value> on an Android tablet', (kind) => {
    render(<OpenInAppButton kind={kind} value="abc123" nav={ANDROID_TABLET} />);
    expect(screen.getByRole('link', { name: 'Open in app' })).toHaveAttribute(
      'href',
      `mtgstats://${kind}/abc123`,
    );
  });

  it('encodes slash, space, question mark, hash and unicode in the value', () => {
    render(<OpenInAppButton kind="join" value={'a/b c?d#e é'} nav={ANDROID_PHONE} />);
    expect(screen.getByRole('link', { name: 'Open in app' })).toHaveAttribute(
      'href',
      'mtgstats://join/a%2Fb%20c%3Fd%23e%20%C3%A9',
    );
  });

  it.each(LINK_KINDS)('renders nothing on a desktop browser (%s)', (kind) => {
    const { container } = render(<OpenInAppButton kind={kind} value="abc" nav={DESKTOP} />);
    expect(container).toBeEmptyDOMElement();
  });

  it.each(LINK_KINDS)('renders nothing on iPhone Safari (%s)', (kind) => {
    const { container } = render(<OpenInAppButton kind={kind} value="abc" nav={IPHONE} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing for an empty value', () => {
    const { container } = render(<OpenInAppButton kind="invite" value="" nav={ANDROID_PHONE} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('public pages (S-1.6.7 AC2, AC3)', () => {
  beforeEach(() => {
    useSessionStore.setState({ status: 'signed_out' });
  });

  it('shows /join/abc to a signed-out visitor on Android with an mtgstats://join/abc link', () => {
    vi.stubGlobal('navigator', ANDROID_PHONE);
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
