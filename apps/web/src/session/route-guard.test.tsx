// S-1.6.4 (T-1.6.4.4): the guard through the real route table and AppRoot, as main.tsx renders it.
// Complements session-guard.test.tsx; the fake session is injected with SessionProvider.
import { StrictMode, useState } from 'react';
import { act, render, screen } from '@testing-library/react';
import { createMemoryRouter } from 'react-router';
import { afterEach, describe, expect, it } from 'vitest';
import { AppRoot } from '../providers/AppRoot';
import { routes } from '../routes';
import { safeNext } from './next';
import { PUBLIC_PATHS } from './public-paths';
import { SessionProvider } from './SessionProvider';
import type { Session } from './session';
import { useSessionStore, type SessionStatus } from './store';

afterEach(() => {
  useSessionStore.setState({ status: 'signed_out' });
});

type Router = ReturnType<typeof createMemoryRouter>;

function mount(path: string | string[], session?: Session): Router {
  const entries = Array.isArray(path) ? path : [path];
  const router = createMemoryRouter(routes, {
    initialEntries: entries,
    initialIndex: entries.length - 1,
  });
  const tree = (
    <StrictMode>
      <AppRoot router={router} />
    </StrictMode>
  );
  render(session ? <SessionProvider session={session}>{tree}</SessionProvider> : tree);
  return router;
}

function nextOf(router: Router): string | null {
  return new URLSearchParams(router.state.location.search).get('next');
}

function record(router: Router): string[] {
  const seen = [router.state.location.pathname];
  router.subscribe((state) => {
    seen.push(state.location.pathname);
  });
  return seen;
}

const heading = (name: string) => screen.queryByRole('heading', { level: 2, name });
const loading = () => screen.queryByRole('status', { name: 'Loading page' });

describe('AC1: signed-out redirect through the real route table', () => {
  it('redirects /decks to /sign-in?next=/decks', () => {
    const router = mount('/decks', { status: 'signed_out' });
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(nextOf(router)).toBe('/decks');
    expect(heading('Sign in')).toBeInTheDocument();
  });

  it.each([
    ['/', '/'],
    ['/games', '/games'],
    ['/decks', '/decks'],
    ['/stats', '/stats'],
    ['/collection', '/collection'],
    ['/leagues', '/leagues'],
    ['/no/such/page', '/no/such/page'],
  ])('redirects %s with its own next', (path, expected) => {
    const router = mount(path, { status: 'signed_out' });
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(nextOf(router)).toBe(expected);
    expect(heading('Sign in')).toBeInTheDocument();
  });

  it('keeps the query string and hash in next', () => {
    const router = mount('/stats?range=30d&x=1#top', { status: 'signed_out' });
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(nextOf(router)).toBe('/stats?range=30d&x=1#top');
  });

  it('replaces history so Back does not return to the protected page', () => {
    const router = mount(['/about', '/decks'], { status: 'signed_out' });
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(router.state.historyAction).toBe('REPLACE');
    act(() => {
      void router.navigate(-1);
    });
    expect(router.state.location.pathname).toBe('/about');
  });

  it('redirects with the default store session (initial state signed_out)', () => {
    useSessionStore.setState({ status: 'signed_out' });
    const router = mount('/leagues');
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(nextOf(router)).toBe('/leagues');
  });

  it('decides on status only: extra fields on the session change nothing', () => {
    const rich = { status: 'signed_out', user: { id: 'u1' }, token: 't' } as Session;
    const router = mount('/decks', rich);
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(nextOf(router)).toBe('/decks');
  });

  it('lets a signed_in session with extra fields through', () => {
    const rich = { status: 'signed_in', user: null } as Session;
    const router = mount('/decks', rich);
    expect(router.state.location.pathname).toBe('/decks');
    expect(heading('Decks')).toBeInTheDocument();
  });

  it.each<SessionStatus>(['signed_out', 'restoring'])(
    'never redirects /sign-in to itself when %s',
    (status) => {
      const router = mount('/sign-in?next=/decks', { status });
      expect(router.state.location.pathname).toBe('/sign-in');
      expect(nextOf(router)).toBe('/decks');
      expect(heading('Sign in')).toBeInTheDocument();
      expect(loading()).not.toBeInTheDocument();
    },
  );
});

describe('AC2: public routes while signed out', () => {
  it('PUBLIC_PATHS is exactly the AC list', () => {
    expect([...PUBLIC_PATHS]).toEqual(['/sign-in', '/about', '/invite/*', '/reset/*', '/join/*']);
  });

  it.each([
    ['/sign-in', 'Sign in'],
    ['/sign-in?next=/decks', 'Sign in'],
    ['/about', 'About'],
    ['/invite/abc123', 'Invite'],
    ['/reset/xyz', 'Reset password'],
    ['/join/some-code', 'Join'],
    ['/invite/abc/extra', 'Invite'],
    ['/reset/a/b/c', 'Reset password'],
    ['/join/some-code?ref=1#x', 'Join'],
  ])('%s renders its page without redirecting', (path, title) => {
    const router = mount(path, { status: 'signed_out' });
    const url = new URL(path, 'http://x.invalid');
    expect(router.state.location.pathname).toBe(url.pathname);
    expect(router.state.location.search).toBe(url.search);
    expect(heading(title)).toBeInTheDocument();
    expect(loading()).not.toBeInTheDocument();
  });

  it.each(['/invites', '/about-us', '/joinx', '/resets/abc', '/sign-in-now', '/sign-ins'])(
    'the look-alike %s is protected',
    (path) => {
      const router = mount(path, { status: 'signed_out' });
      expect(router.state.location.pathname).toBe('/sign-in');
      expect(nextOf(router)).toBe(path);
    },
  );
});

describe('AC3: restoring a session on load', () => {
  it('shows the loading state on a protected route, with no redirect and no page content', () => {
    const router = mount('/decks?a=1#h', { status: 'restoring' });
    expect(router.state.location.pathname).toBe('/decks');
    expect(router.state.location.search).toBe('?a=1');
    expect(router.state.location.hash).toBe('#h');
    expect(loading()).toBeInTheDocument();
    expect(heading('Decks')).not.toBeInTheDocument();
    expect(heading('Sign in')).not.toBeInTheDocument();
  });

  it.each(['/', '/games', '/stats', '/collection', '/leagues', '/unknown'])(
    'shows the loading state on %s and does not redirect',
    (path) => {
      const router = mount(path, { status: 'restoring' });
      expect(router.state.location.pathname).toBe(path);
      expect(loading()).toBeInTheDocument();
    },
  );

  it('exposes the loading state with role status and an accessible name', () => {
    mount('/decks', { status: 'restoring' });
    expect(screen.getByRole('status', { name: 'Loading page' })).toBeInTheDocument();
  });

  it.each(['/sign-in', '/about', '/invite/abc', '/reset/x', '/join/c'])(
    'renders public %s right away while restoring, with no loading state',
    (path) => {
      const router = mount(path, { status: 'restoring' });
      expect(router.state.location.pathname).toBe(path);
      expect(loading()).not.toBeInTheDocument();
      expect(screen.getByRole('main')).toBeInTheDocument();
    },
  );

  it('renders the page at the same URL when restoring becomes signed_in', () => {
    useSessionStore.setState({ status: 'restoring' });
    const router = mount('/collection?x=1');
    expect(loading()).toBeInTheDocument();
    act(() => {
      useSessionStore.getState().setStatus('signed_in');
    });
    expect(router.state.location.pathname).toBe('/collection');
    expect(router.state.location.search).toBe('?x=1');
    expect(loading()).not.toBeInTheDocument();
    expect(heading('Collection')).toBeInTheDocument();
  });

  it('redirects with the original next when restoring becomes signed_out', () => {
    useSessionStore.setState({ status: 'restoring' });
    const router = mount('/leagues?y=2#z');
    expect(loading()).toBeInTheDocument();
    act(() => {
      useSessionStore.getState().setStatus('signed_out');
    });
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(nextOf(router)).toBe('/leagues?y=2#z');
    expect(heading('Sign in')).toBeInTheDocument();
  });

  it('a reload on a deep URL never visits /sign-in when the session restores as signed in', () => {
    let setSession: (s: Session) => void = () => undefined;
    let seen: string[] = [];
    function Harness() {
      const [session, set] = useState<Session>({ status: 'restoring' });
      const [router] = useState(() => {
        const r = createMemoryRouter(routes, { initialEntries: ['/games?page=2#row'] });
        seen = record(r);
        return r;
      });
      setSession = set;
      return (
        <SessionProvider session={session}>
          <AppRoot router={router} />
        </SessionProvider>
      );
    }
    render(<Harness />);
    expect(loading()).toBeInTheDocument();
    act(() => {
      setSession({ status: 'signed_in' });
    });
    expect(heading('Games')).toBeInTheDocument();
    expect(seen.length).toBeGreaterThanOrEqual(1);
    expect(seen).not.toContain('/sign-in');
    expect(seen.every((p) => p === '/games')).toBe(true);
  });

  it('a reload via the store also never visits /sign-in', () => {
    useSessionStore.setState({ status: 'restoring' });
    const router = createMemoryRouter(routes, { initialEntries: ['/stats'] });
    const seen = record(router);
    render(<AppRoot router={router} />);
    act(() => {
      useSessionStore.getState().setStatus('signed_in');
    });
    expect(heading('Stats')).toBeInTheDocument();
    expect(seen).not.toContain('/sign-in');
    expect(router.state.location.pathname).toBe('/stats');
  });
});

describe('safeNext: open-redirect safety', () => {
  it.each(['/decks', '/decks?x=1#y', '/', '/decks/abc?tab=a'])('accepts %s', (raw) => {
    expect(safeNext(raw)).toBe(raw);
  });

  it.each([
    ['null', null],
    ['empty', ''],
    ['no leading slash', 'decks'],
    ['protocol-relative', '//evil.com'],
    ['slash-backslash', '/\\evil.com'],
    ['slash-double-backslash', '/\\\\evil.com'],
    ['absolute https', 'https://evil.com'],
    ['absolute http with path', 'http://evil.com/decks'],
    ['javascript scheme', 'javascript:alert(1)'],
    ['data scheme', 'data:text/html,x'],
    ['leading space', ' /decks'],
    ['leading space before //', ' //evil.com'],
    ['tab inside //', '/\t/evil.com'],
    ['newline inside //', '/\n/evil.com'],
    ['carriage return inside //', '/\r/evil.com'],
    ['NUL', '/\u0000/evil.com'],
    ['leading tab', '\t//evil.com'],
    ['backslash after path', '/decks\\..\\evil'],
  ])('rejects %s and returns /', (_label, raw) => {
    expect(safeNext(raw)).toBe('/');
  });

  it('rejects //evil.com as URLSearchParams would hand it back (decoded)', () => {
    const decoded = new URLSearchParams('next=%2F%2Fevil.com').get('next');
    expect(decoded).toBe('//evil.com');
    expect(safeNext(decoded)).toBe('/');
  });

  it('rejects the still-encoded %2F%2Fevil.com', () => {
    expect(safeNext('%2F%2Fevil.com')).toBe('/');
  });

  it('rejects an encoded backslash form after a round trip', () => {
    const decoded = new URLSearchParams('next=%2F%5Cevil.com').get('next');
    expect(decoded).toBe('/\\evil.com');
    expect(safeNext(decoded)).toBe('/');
  });

  it('rejects an absolute URL after a URLSearchParams round trip', () => {
    const encoded = new URLSearchParams({ next: 'https://evil.com' }).toString();
    expect(safeNext(new URLSearchParams(encoded).get('next'))).toBe('/');
  });

  it('keeps a path whose query contains an encoded URL', () => {
    expect(safeNext('/decks?u=https%3A%2F%2Fevil.com')).toBe('/decks?u=https%3A%2F%2Fevil.com');
  });
});
