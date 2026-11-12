// S-1.6.4: route guard, public routes, restoring state, safeNext. The fake session is injected with
// SessionProvider, so the guard is exercised through the session interface only.
import { useState } from 'react';
import { act, render, screen } from '@testing-library/react';
import { createMemoryRouter, type RouteObject } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, describe, expect, it } from 'vitest';
import { routes } from '../routes';
import { safeNext } from './next';
import { PUBLIC_PATHS } from './public-paths';
import { RequireSession } from './RequireSession';
import { SessionProvider } from './SessionProvider';
import type { Session } from './session';
import { useSessionStore } from './store';

afterEach(() => {
  useSessionStore.setState({ status: 'signed_out' });
});

function renderAt(path: string, session: Session) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <SessionProvider session={session}>
      <RouterProvider router={router} />
    </SessionProvider>,
  );
  return router;
}

const SIGNED_OUT: Session = { status: 'signed_out' };
const SIGNED_IN: Session = { status: 'signed_in' };
const RESTORING: Session = { status: 'restoring' };

function nextOf(router: ReturnType<typeof renderAt>) {
  return new URLSearchParams(router.state.location.search).get('next');
}

describe('guard redirect (AC1)', () => {
  it('sends a signed-out visitor from /decks to /sign-in?next=/decks', () => {
    const router = renderAt('/decks', SIGNED_OUT);
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(nextOf(router)).toBe('/decks');
    expect(screen.getByRole('heading', { level: 2, name: 'Sign in' })).toBeInTheDocument();
  });

  it('preserves query and hash in next', () => {
    const router = renderAt('/decks?tab=a&x=1#top', SIGNED_OUT);
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(nextOf(router)).toBe('/decks?tab=a&x=1#top');
  });

  it('protects home and unknown paths, and replaces history', () => {
    const home = renderAt('/', SIGNED_OUT);
    expect(nextOf(home)).toBe('/');
    const unknown = renderAt('/nope/deeper', SIGNED_OUT);
    expect(nextOf(unknown)).toBe('/nope/deeper');
    expect(unknown.state.historyAction).toBe('REPLACE');
  });

  it('renders the page for a signed-in user', () => {
    const router = renderAt('/decks', SIGNED_IN);
    expect(router.state.location.pathname).toBe('/decks');
    expect(screen.getByRole('heading', { level: 2, name: 'Decks' })).toBeInTheDocument();
  });

  it('reads the store when no session is injected', () => {
    useSessionStore.setState({ status: 'signed_in' });
    const router = createMemoryRouter(routes, { initialEntries: ['/games'] });
    render(<RouterProvider router={router} />);
    expect(screen.getByRole('heading', { level: 2, name: 'Games' })).toBeInTheDocument();
  });
});

describe('public routes (AC2)', () => {
  it('is exactly the AC list', () => {
    expect([...PUBLIC_PATHS]).toEqual(['/sign-in', '/about', '/invite/*', '/reset/*', '/join/*']);
  });

  it.each(['/sign-in', '/about', '/invite/abc123', '/reset/tok', '/join/CODE', '/join/a/b'])(
    '%s is reachable signed out',
    (path) => {
      const router = renderAt(path, SIGNED_OUT);
      expect(router.state.location.pathname).toBe(path);
      expect(screen.getByRole('main')).toBeInTheDocument();
      expect(screen.queryByText('Loading…')).not.toBeInTheDocument();
    },
  );
});

describe('restoring (AC3)', () => {
  it('shows a loading state, does not redirect and does not render the page', () => {
    const router = renderAt('/decks', RESTORING);
    expect(router.state.location.pathname).toBe('/decks');
    expect(screen.getByText('Loading…')).toBeInTheDocument();
    expect(screen.getByRole('status', { name: 'Loading page' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 2, name: 'Decks' })).not.toBeInTheDocument();
  });

  it('renders the page once restoring becomes signed_in, without a reload', () => {
    useSessionStore.setState({ status: 'restoring' });
    const router = createMemoryRouter(routes, { initialEntries: ['/decks'] });
    render(<RouterProvider router={router} />);
    expect(screen.getByText('Loading…')).toBeInTheDocument();
    act(() => {
      useSessionStore.getState().setStatus('signed_in');
    });
    expect(router.state.location.pathname).toBe('/decks');
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'Decks' })).toBeInTheDocument();
  });

  it('redirects once restoring becomes signed_out', () => {
    useSessionStore.setState({ status: 'restoring' });
    const router = createMemoryRouter(routes, { initialEntries: ['/decks?a=1'] });
    render(<RouterProvider router={router} />);
    act(() => {
      useSessionStore.getState().setStatus('signed_out');
    });
    expect(router.state.location.pathname).toBe('/sign-in');
    expect(nextOf(router)).toBe('/decks?a=1');
  });

  it('works with a fake session that changes over time', () => {
    let set: (s: Session) => void = () => undefined;
    function Harness() {
      const [session, setSession] = useState<Session>(RESTORING);
      const [router] = useState(() => createMemoryRouter(routes, { initialEntries: ['/stats'] }));
      set = setSession;
      return (
        <SessionProvider session={session}>
          <RouterProvider router={router} />
        </SessionProvider>
      );
    }
    render(<Harness />);
    expect(screen.getByText('Loading…')).toBeInTheDocument();
    act(() => {
      set(SIGNED_IN);
    });
    expect(screen.getByRole('heading', { level: 2, name: 'Stats' })).toBeInTheDocument();
  });
});

describe('safeNext', () => {
  it.each([
    ['/decks', '/decks'],
    ['/decks?tab=a#x', '/decks?tab=a#x'],
    ['/', '/'],
    ['/a//b', '/a//b'],
  ])('keeps the relative path %s', (raw, expected) => {
    expect(safeNext(raw)).toBe(expected);
  });

  it.each([
    null,
    undefined,
    '',
    'decks',
    '//evil.com',
    '///evil.com',
    '/\\evil.com',
    '/a\\b',
    'https://evil.com',
    'http://evil.com/x',
    'javascript:alert(1)',
    '/\t/evil.com',
    '/\n/evil.com',
    ' /decks',
  ])('falls back to / for %j', (raw) => {
    expect(safeNext(raw)).toBe('/');
  });

  it('round-trips what the guard produces', () => {
    const router = renderAt('/decks?x=1#h', SIGNED_OUT);
    expect(safeNext(nextOf(router))).toBe('/decks?x=1#h');
  });
});

describe('route table coverage', () => {
  interface Leaf {
    pattern: string;
    guarded: boolean;
  }

  function isGuardElement(element: RouteObject['element']): boolean {
    return (
      typeof element === 'object' &&
      element !== null &&
      'type' in element &&
      element.type === RequireSession
    );
  }

  function walk(table: RouteObject[], parent = '', guarded = false): Leaf[] {
    return table.flatMap((r) => {
      const inGuard = guarded || isGuardElement(r.element);
      const own =
        r.path === undefined
          ? parent
          : r.path.startsWith('/')
            ? r.path
            : `${parent.replace(/\/$/, '')}/${r.path}`;
      if (r.children) return walk(r.children, own, inGuard);
      return [{ pattern: own || '/', guarded: inGuard }];
    });
  }

  const leaves = walk(routes);

  it('finds the routes (guards the walker)', () => {
    expect(leaves.length).toBeGreaterThanOrEqual(11);
    expect(leaves.some((l) => l.guarded)).toBe(true);
  });

  it('puts every route behind RequireSession or in PUBLIC_PATHS, and never both', () => {
    for (const leaf of leaves) {
      const isPublic = (PUBLIC_PATHS as readonly string[]).includes(leaf.pattern);
      expect(isPublic !== leaf.guarded, `${leaf.pattern} must be guarded xor public`).toBe(true);
    }
  });

  it('has a route for every public path', () => {
    const patterns = leaves.map((l) => l.pattern);
    for (const p of PUBLIC_PATHS) expect(patterns).toContain(p);
  });
});
