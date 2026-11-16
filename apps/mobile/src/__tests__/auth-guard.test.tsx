import * as fs from 'node:fs';
import * as path from 'node:path';
import { useEffect } from 'react';
import { router } from 'expo-router';
import * as lib from 'expo-router/testing-library';
import * as SplashScreen from 'expo-splash-screen';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { AppProviders } from '../components/AppProviders';
import { RootNavigator } from '../app/_layout';
import { isProtectedRoot, isPublicPath, PUBLIC_ROUTES } from '../session/publicRoutes';
import {
  SessionProvider,
  useSession,
  type Session,
  type SessionStatus,
} from '../session/SessionProvider';

jest.setTimeout(60000);

jest.mock('expo-splash-screen', () => ({
  preventAutoHideAsync: jest.fn(() => Promise.resolve(true)),
  hideAsync: jest.fn(() => Promise.resolve()),
}));

const preventAutoHide = SplashScreen.preventAutoHideAsync as jest.Mock;
const hideAsync = SplashScreen.hideAsync as jest.Mock;
// The root layout calls keepSplashVisible() once, when its module loads (before any test runs).
const preventCallsAtModuleLoad = preventAutoHide.mock.calls.length;

const APP_DIR = './src/app';
const SIGN_IN = 'Continue (placeholder)';

let session: Session;
const latest: { current: Session | null } = { current: null };

function Capture() {
  const value = useSession();
  useEffect(() => {
    latest.current = value;
    session = value;
  });
  return null;
}

/** Mounts the real route tree under a session whose status the test controls via `session`. */
async function mount(initialStatus: SessionStatus, initialUrl = '/') {
  return lib.renderRouter(
    {
      appDir: APP_DIR,
      overrides: {
        _layout: () => (
          <GestureHandlerRootView style={{ flex: 1 }}>
            <AppProviders>
              <SessionProvider initialStatus={initialStatus}>
                <Capture />
                <RootNavigator />
              </SessionProvider>
            </AppProviders>
          </GestureHandlerRootView>
        ),
      },
    },
    { initialUrl },
  );
}

const flip = (change: () => void) => lib.act(async () => change());

beforeEach(() => {
  hideAsync.mockClear();
});

describe('root layout per session status', () => {
  it('shows the sign-in stack and no tabs when signed_out', async () => {
    await mount('signed_out');
    expect(await lib.screen.findByLabelText(SIGN_IN)).toBeTruthy();
    expect(lib.screen.queryByLabelText('Play')).toBeNull();
  });

  it('shows the tabs and no sign-in stack when signed_in', async () => {
    await mount('signed_in');
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(lib.screen.queryByLabelText(SIGN_IN)).toBeNull();
  });

  it('mounts nothing from either stack while restoring', async () => {
    await mount('restoring');
    expect(lib.screen.queryByLabelText(SIGN_IN)).toBeNull();
    expect(lib.screen.queryByLabelText('Play')).toBeNull();
    expect(lib.screen.queryByLabelText('Decks')).toBeNull();
  });

  it('mounts nothing while restoring even for a protected link', async () => {
    await mount('restoring', '/join/ABC123');
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
    expect(lib.screen.queryByLabelText(SIGN_IN)).toBeNull();
  });

  it('goes from restoring to the tabs without showing sign-in when the user is signed in', async () => {
    await mount('restoring');
    await flip(() => session.signIn());
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(lib.screen.queryByLabelText(SIGN_IN)).toBeNull();
  });

  it('goes from restoring to the sign-in stack when there is no session', async () => {
    await mount('restoring');
    await flip(() => session.signOut());
    expect(await lib.screen.findByLabelText(SIGN_IN)).toBeTruthy();
    expect(lib.screen.queryByLabelText('Play')).toBeNull();
  });

  it('swaps the sign-in stack for the tabs when signed_out becomes signed_in, and back', async () => {
    await mount('signed_out');
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(lib.screen.queryByLabelText(SIGN_IN)).toBeNull();
    await flip(() => session.signOut());
    expect(await lib.screen.findByLabelText(SIGN_IN)).toBeTruthy();
    expect(lib.screen.queryByLabelText('Play')).toBeNull();
  });
});

describe('splash screen', () => {
  it('asks to keep the splash visible once, when the root layout loads', () => {
    expect(preventCallsAtModuleLoad).toBe(1);
  });

  it('does not hide the splash while restoring', async () => {
    await mount('restoring');
    expect(hideAsync).not.toHaveBeenCalled();
  });

  it('hides the splash once status leaves restoring', async () => {
    await mount('restoring');
    expect(hideAsync).not.toHaveBeenCalled();
    await flip(() => session.signIn());
    await lib.screen.findByLabelText('Play');
    expect(hideAsync).toHaveBeenCalledTimes(1);
  });

  it.each(['signed_in', 'signed_out'] as const)(
    'hides the splash on a start that is %s',
    async (status) => {
      await mount(status);
      await lib.waitFor(() => expect(hideAsync).toHaveBeenCalled());
    },
  );

  it('still shows the sign-in stack when hiding the splash rejects', async () => {
    hideAsync.mockImplementationOnce(() => Promise.reject(new Error('native')));
    await mount('signed_out');
    expect(await lib.screen.findByLabelText(SIGN_IN)).toBeTruthy();
  });
});

describe('pending deep link', () => {
  it('opens the protected screen a link pointed to once status becomes signed_in', async () => {
    await mount('signed_out', '/join/ABC123');
    await lib.screen.findByLabelText(SIGN_IN);
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
    await flip(() => session.signIn());
    expect(await lib.screen.findByTestId('join-screen-value')).toHaveTextContent('ABC123');
    expect(lib.screen).toHavePathname('/join/ABC123');
  });

  it('keeps the query params of the pending link', async () => {
    await mount('signed_out', '/join/ABC123?ref=friend');
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    await lib.screen.findByTestId('join-screen');
    expect(lib.screen).toHavePathname('/join/ABC123');
    expect(lib.screen).toHaveSearchParams({ code: 'ABC123', ref: 'friend' });
  });

  it('opens the pending screen over the tabs so Back to app reaches them', async () => {
    await mount('signed_out', '/join/ABC123');
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    await lib.fireEvent.press(await lib.screen.findByLabelText('Back to app'));
    await lib.waitFor(() => expect(lib.screen.queryByTestId('join-screen')).toBeNull());
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
  });

  it('opens the pending link only once and clears it afterwards', async () => {
    await mount('signed_out', '/join/ABC123');
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    await lib.fireEvent.press(await lib.screen.findByLabelText('Back to app'));
    await lib.screen.findByLabelText('Play');
    await flip(() => session.signOut());
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
  });

  it('clears the pending link on sign-out so the next sign-in lands on the tabs', async () => {
    await mount('signed_in');
    await lib.screen.findByLabelText('Play');
    await lib.act(async () => router.push('/join/OLD1'));
    await lib.screen.findByTestId('join-screen');
    await flip(() => session.signOut());
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
  });

  it('opens the latest link when a second protected link arrives while still signed out', async () => {
    await mount('signed_out', '/join/FIRST');
    await lib.screen.findByLabelText(SIGN_IN);
    await lib.act(async () => router.navigate('/join/SECOND'));
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    expect(await lib.screen.findByTestId('join-screen-value')).toHaveTextContent('SECOND');
  });

  it('keeps a pending code with an encoded space decoded', async () => {
    await mount('signed_out', '/join/AB%20C1');
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    expect(await lib.screen.findByTestId('join-screen-value')).toHaveTextContent('AB C1');
  });

  it('opens the join screen after a cold start that restores, resolves signed_out and then signs in', async () => {
    await mount('restoring', '/join/ABC123');
    await flip(() => session.signOut());
    await lib.screen.findByLabelText(SIGN_IN);
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
    await flip(() => session.signIn());
    expect(await lib.screen.findByTestId('join-screen-value')).toHaveTextContent('ABC123');
    expect(lib.screen.queryByLabelText('Decks')).toBeNull();
  });

  it('clears a pending link on a real sign-out from signed_in', async () => {
    await mount('signed_out', '/join/ABC123');
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    await lib.screen.findByTestId('join-screen');
    await flip(() => session.signOut());
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
  });

  it('drops a stale pending link when a public Invite link is opened signed out', async () => {
    await mount('signed_out', '/join/A1');
    await lib.screen.findByLabelText(SIGN_IN);
    await lib.act(async () => router.navigate('/invite/T1'));
    await lib.screen.findByTestId('invite-screen');
    await flip(() => session.signIn());
    await lib.fireEvent.press(await lib.screen.findByLabelText('Back to app'));
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
  });

  it('keeps the pending link while the user visits the About screen', async () => {
    await mount('signed_out', '/join/A1');
    await lib.fireEvent.press(await lib.screen.findByLabelText('About'));
    await lib.waitFor(() => expect(lib.screen).toHavePathname('/about'));
    await lib.act(async () => router.back());
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    expect(await lib.screen.findByTestId('join-screen-value')).toHaveTextContent('A1');
  });

  it('keeps the pending link when the sign-in screen is opened explicitly', async () => {
    await mount('signed_out', '/join/A1');
    await lib.screen.findByLabelText(SIGN_IN);
    await lib.act(async () => router.navigate('/sign-in'));
    await flip(() => session.signIn());
    expect(await lib.screen.findByTestId('join-screen-value')).toHaveTextContent('A1');
  });

  it('treats only join and the tabs as protected roots, so unmatched and dev URLs are never captured', () => {
    expect(isProtectedRoot('join')).toBe(true);
    expect(isProtectedRoot('(tabs)')).toBe(true);
    for (const root of [
      'garbage',
      '+not-found',
      'dev',
      'sign-in',
      'about',
      'invite',
      'reset',
      '',
    ]) {
      expect(isProtectedRoot(root)).toBe(false);
    }
  });

  it('does not capture the dev-only gesture-test URL as a pending link', async () => {
    await mount('signed_out', '/dev/gesture-test');
    await flip(() => session.signIn());
    await lib.waitFor(() => expect(session.status).toBe('signed_in'));
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
    expect(lib.screen).toHavePathname('/dev/gesture-test');
  });

  it('keeps repeated query keys of the pending link', async () => {
    await mount('signed_out', '/join/ABC123?x=1&x=2');
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    await lib.screen.findByTestId('join-screen');
    expect(lib.screen).toHavePathname('/join/ABC123');
    expect(lib.screen).toHaveSearchParams({ code: 'ABC123', x: ['1', '2'] });
  });

  it('keeps an encoded slash in a pending code', async () => {
    await mount('signed_out', '/join/AB%20C%2F1');
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    expect(await lib.screen.findByTestId('join-screen-value')).toHaveTextContent('AB C/1');
  });

  it('goes to the tabs as before when there is no link', async () => {
    await mount('signed_out');
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
  });

  it('does not treat the sign-in stack screens as a pending link', async () => {
    await mount('signed_out', '/sign-in');
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    expect(await lib.screen.findByLabelText('Play')).toBeTruthy();
  });

  it('opens a protected link that was opened while restoring once the user is signed in', async () => {
    await mount('restoring', '/join/ABC123');
    await flip(() => session.signIn());
    expect(await lib.screen.findByTestId('join-screen-value')).toHaveTextContent('ABC123');
  });

  it('sends a link to a tab route opened while signed out to that tab after sign-in', async () => {
    await mount('signed_out', '/decks');
    await lib.screen.findByLabelText(SIGN_IN);
    await flip(() => session.signIn());
    expect(await lib.screen.findByRole('header', { name: 'Decks' })).toBeTruthy();
  });
});

describe('public routes', () => {
  it('lists the invite and reset routes as public', () => {
    expect(PUBLIC_ROUTES).toEqual(expect.arrayContaining(['invite/[token]', 'reset/[token]']));
    expect(isPublicPath('/invite/tok-1')).toBe(true);
    expect(isPublicPath('/reset/tok-1')).toBe(true);
  });

  it('treats join and the tab routes as protected', () => {
    expect(PUBLIC_ROUTES.some((r) => r.startsWith('join'))).toBe(false);
    for (const p of ['/join/ABC', '/play', '/decks', '/stats', '/more']) {
      expect(isPublicPath(p)).toBe(false);
    }
  });

  it('opens the invite screen from a link while signed out', async () => {
    await mount('signed_out', '/invite/tok-9f2');
    expect(await lib.screen.findByTestId('invite-screen-value')).toHaveTextContent('tok-9f2');
    expect(lib.screen.queryByLabelText(SIGN_IN)).toBeNull();
  });

  it('opens the invite screen from a link while signed in', async () => {
    await mount('signed_in', '/invite/tok-9f2');
    expect(await lib.screen.findByTestId('invite-screen-value')).toHaveTextContent('tok-9f2');
  });

  it('reaches every public route that has a route file while signed out', async () => {
    const existing = PUBLIC_ROUTES.filter((name) =>
      fs.existsSync(path.join(__dirname, '..', 'app', `${name}.tsx`)),
    );
    expect(existing).toContain('invite/[token]');
    for (const name of existing) {
      const url = '/' + name.replace(/\[[^\]]+\]/g, 'tok-x');
      await mount('signed_out', url);
      await lib.waitFor(() => expect(lib.screen).toHavePathname(url));
      expect(lib.screen.queryByLabelText(SIGN_IN)).toBeNull();
    }
  });

  it('does not reach join or the tabs while signed out', async () => {
    await mount('signed_out', '/join/ABC123');
    await lib.screen.findByLabelText(SIGN_IN);
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
    await lib.act(async () => router.navigate('/play'));
    await lib.screen.findByLabelText(SIGN_IN);
    expect(lib.screen.queryByLabelText('Play')).toBeNull();
  });

  it('does not record an invite link as pending and does not re-open it after sign-in', async () => {
    await mount('signed_out', '/invite/tok-9f2');
    await lib.screen.findByTestId('invite-screen');
    await flip(() => session.signIn());
    await lib.waitFor(() => expect(session.status).toBe('signed_in'));
    expect(lib.screen.getByTestId('invite-screen-value')).toHaveTextContent('tok-9f2');
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
  });

  it('does not record a reset link as pending or redirect to a protected screen', async () => {
    await mount('signed_out', '/reset/tok-1');
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
    expect(lib.screen.queryByLabelText('Play')).toBeNull();
    await flip(() => session.signIn());
    await lib.waitFor(() => expect(session.status).toBe('signed_in'));
    expect(lib.screen.queryByTestId('join-screen')).toBeNull();
    expect(lib.screen.queryByLabelText('Play')).toBeNull();
  });
});

describe('top-level routes are all classified', () => {
  const appDir = path.join(__dirname, '..', 'app');
  const protectedRoots = ['(tabs)', 'join'];
  const publicRoots = [...new Set(PUBLIC_ROUTES.map((r) => r.split('/')[0] ?? r))];
  const entries = fs.readdirSync(appDir).map((e) => e.replace(/\.tsx?$/, ''));

  it('puts every top-level entry of src/app in exactly one class', () => {
    const classes: Record<string, string[]> = {
      auth: ['(auth)'],
      protected: protectedRoots,
      public: publicRoots,
      dev: ['dev'],
      layout: ['index', '_layout'],
    };
    for (const entry of entries) {
      const matching = Object.entries(classes).filter(([, names]) => names.includes(entry));
      expect({ entry, classes: matching.map(([k]) => k).length }).toEqual({ entry, classes: 1 });
    }
  });

  it('has a route file or directory for every protected root', () => {
    for (const root of protectedRoots) expect(entries).toContain(root);
  });

  it('agrees with isProtectedRoot about which roots are protected', () => {
    for (const entry of entries) {
      expect({ entry, protected: isProtectedRoot(entry) }).toEqual({
        entry,
        protected: protectedRoots.includes(entry),
      });
    }
  });
});

describe('the guard stores no tokens itself', () => {
  const src = path.join(__dirname, '..');
  const files = [
    ...fs
      .readdirSync(path.join(src, 'session'))
      .filter((f) => /\.tsx?$/.test(f))
      .map((f) => `session/${f}`),
    'app/_layout.tsx',
    'app/index.tsx',
  ];

  it('has source files to check', () => {
    expect(files).toEqual(
      expect.arrayContaining([
        'session/SessionProvider.tsx',
        'session/pendingLink.ts',
        'app/_layout.tsx',
        'app/index.tsx',
      ]),
    );
  });

  it.each(files)('%s imports no storage and makes no network call', (file) => {
    const source = fs
      .readFileSync(path.join(src, file), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, ''); // comments may mention the store that E-2 adds
    expect(source).not.toMatch(/expo-secure-store/);
    expect(source).not.toMatch(/async-storage/i);
    expect(source).not.toMatch(/\blocalStorage\b|\bsessionStorage\b/);
    expect(source).not.toMatch(/\bfetch\s*\(/);
    expect(source).not.toMatch(/@mtg-stats\/api-client/);
  });
});
