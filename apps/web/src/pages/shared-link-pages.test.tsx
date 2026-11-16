// S-1.6.7 acceptance (AC2, AC3): the invite, reset and join pages through the real route table,
// signed out, on Android, iOS and desktop navigators. Complements ui/OpenInAppButton.test.tsx.
import { render, screen, within } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { AppProviders } from '../providers/AppProviders';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { routes } from '../routes';
import { PUBLIC_PATHS } from '../session/public-paths';
import { useSessionStore } from '../session/store';
import { LINK_KINDS, OpenInAppButton, type LinkKind } from '../ui/OpenInAppButton';
import { isAndroidBrowser, type NavigatorLike } from '../ui/android-browser';

const ANDROID: NavigatorLike = {
  userAgent:
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36',
  platform: 'Linux armv81',
  maxTouchPoints: 5,
};
const IPHONE: NavigatorLike = {
  userAgent:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
  platform: 'iPhone',
  maxTouchPoints: 5,
};
const IPAD: NavigatorLike = {
  userAgent:
    'Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
  platform: 'iPad',
  maxTouchPoints: 5,
};
const IPADOS_DESKTOP_MODE: NavigatorLike = {
  userAgent:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15',
  platform: 'MacIntel',
  maxTouchPoints: 5,
};
const WINDOWS_CHROME: NavigatorLike = {
  userAgent:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
  platform: 'Win32',
  maxTouchPoints: 0,
};
const MAC_SAFARI: NavigatorLike = {
  userAgent:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15',
  platform: 'MacIntel',
  maxTouchPoints: 0,
};
const LINUX_FIREFOX: NavigatorLike = {
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0',
  platform: 'Linux x86_64',
  maxTouchPoints: 0,
};

const TITLES: Record<LinkKind, string> = {
  invite: 'Invite',
  reset: 'Reset password',
  join: 'Join',
};

function mount(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

beforeEach(() => {
  useSessionStore.setState({ status: 'signed_out' });
});

afterEach(() => {
  vi.unstubAllGlobals();
});
const ANDROID_TABLET: NavigatorLike = {
  userAgent:
    'Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
  platform: 'Linux armv81',
  maxTouchPoints: 5,
  userAgentData: { mobile: false },
};

describe('Android detection', () => {
  it.each<[string, NavigatorLike]>([
    ['iPhone Safari', IPHONE],
    ['iPad', IPAD],
    ['iPadOS in desktop mode', IPADOS_DESKTOP_MODE],
    ['Windows Chrome', WINDOWS_CHROME],
    ['macOS Safari without touch', MAC_SAFARI],
    ['Linux Firefox', LINUX_FIREFOX],
  ])('does not treat %s as Android', (_name, nav) => {
    expect(isAndroidBrowser(nav)).toBe(false);
  });

  it.each<[string, NavigatorLike]>([
    ['Android Chrome phone', ANDROID],
    ['Android tablet', ANDROID_TABLET],
  ])('treats %s as Android', (_name, nav) => {
    expect(isAndroidBrowser(nav)).toBe(true);
  });

  it('reads the real navigator when none is passed', () => {
    vi.stubGlobal('navigator', ANDROID);
    expect(isAndroidBrowser()).toBe(true);
    vi.stubGlobal('navigator', IPHONE);
    expect(isAndroidBrowser()).toBe(false);
  });
});

describe.each(LINK_KINDS)('/%s/<value> signed out', (kind) => {
  it('shows the page and an mtgstats:// link on an Android browser', () => {
    vi.stubGlobal('navigator', ANDROID);
    const router = mount(`/${kind}/tok123`);
    expect(router.state.location.pathname).toBe(`/${kind}/tok123`);
    expect(screen.getByRole('heading', { level: 2, name: TITLES[kind] })).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Open in app' });
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', `mtgstats://${kind}/tok123`);
  });

  it('offers exactly one Open in app link, and no https link to the same path', () => {
    vi.stubGlobal('navigator', ANDROID);
    mount(`/${kind}/tok123`);
    expect(screen.getAllByRole('link', { name: 'Open in app' })).toHaveLength(1);
    for (const a of document.querySelectorAll('a[href]')) {
      expect(a.getAttribute('href')).not.toMatch(new RegExp(`^https?://.*/${kind}/`));
    }
  });

  it('round-trips an encoded slash and space from the browser path', () => {
    vi.stubGlobal('navigator', ANDROID);
    mount(`/${kind}/a%2Fb%20c`);
    expect(screen.getByRole('link', { name: 'Open in app' })).toHaveAttribute(
      'href',
      `mtgstats://${kind}/a%2Fb%20c`,
    );
  });

  it('round-trips an encoded unicode value', () => {
    vi.stubGlobal('navigator', ANDROID);
    mount(`/${kind}/caf%C3%A9`);
    expect(screen.getByRole('link', { name: 'Open in app' })).toHaveAttribute(
      'href',
      `mtgstats://${kind}/caf%C3%A9`,
    );
  });

  it('shows the page without the link on a desktop browser', () => {
    vi.stubGlobal('navigator', WINDOWS_CHROME);
    const router = mount(`/${kind}/tok123`);
    expect(router.state.location.pathname).toBe(`/${kind}/tok123`);
    expect(screen.getByRole('heading', { level: 2, name: TITLES[kind] })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open in app' })).not.toBeInTheDocument();
    expect(screen.queryByText(/open in app/i)).not.toBeInTheDocument();
  });

  it.each([
    ['iPhone Safari', IPHONE],
    ['iPad', IPAD],
    ['iPadOS in desktop mode', IPADOS_DESKTOP_MODE],
    ['Mac Safari', MAC_SAFARI],
    ['Linux Firefox', LINUX_FIREFOX],
  ])('shows the page with no link on %s', (_name, nav) => {
    vi.stubGlobal('navigator', nav);
    mount(`/${kind}/tok123`);
    expect(screen.queryByRole('link', { name: 'Open in app' })).not.toBeInTheDocument();
  });

  it.each([
    ['Android', ANDROID],
    ['iPhone', IPHONE],
    ['desktop', WINDOWS_CHROME],
  ])('keeps the public layout (main landmark and footer) on %s', (_name, nav) => {
    vi.stubGlobal('navigator', nav);
    mount(`/${kind}/tok123`);
    const main = screen.getByRole('main');
    expect(within(main).getByRole('heading', { level: 2 })).toBeInTheDocument();
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
  });

  it('does not redirect a signed-out visitor to sign-in', () => {
    vi.stubGlobal('navigator', WINDOWS_CHROME);
    const router = mount(`/${kind}/tok123`);
    expect(router.state.location.pathname).not.toBe('/sign-in');
    expect(router.state.location.search).toBe('');
  });
});

describe('public paths (AC3)', () => {
  it.each(['/invite/*', '/reset/*', '/join/*'])('lists %s in PUBLIC_PATHS', (pattern) => {
    expect((PUBLIC_PATHS as readonly string[]).includes(pattern)).toBe(true);
  });
});

describe('OpenInAppButton accessibility', () => {
  it.each(LINK_KINDS)('is a link with the accessible name "Open in app" (%s)', (kind) => {
    render(<OpenInAppButton kind={kind} value="abc" nav={ANDROID} />);
    const link = screen.getByRole('link', { name: 'Open in app' });
    expect(link).toHaveAccessibleName('Open in app');
    expect(link).toHaveAttribute('href', `mtgstats://${kind}/abc`);
  });

  it('never renders a link for an iPadOS desktop-mode device that is not touch', () => {
    render(<OpenInAppButton kind="join" value="abc" nav={MAC_SAFARI} />);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
});
