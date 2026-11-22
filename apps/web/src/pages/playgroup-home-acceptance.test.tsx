// S-2.4.5 acceptance (T-2.4.5.3, web), criteria 1 to 3: the empty state, the remembered active
// playgroup and the role-based playgroup home. Written from the story's criteria and the API's
// documented rules (docs/architecture.md section 5, the route descriptions in openapi.json), and
// driven through the real route table with a stubbed network. Criterion 4 (keyboard) and the
// "I have an invite" screen are in playgroup-switcher-join-acceptance.test.tsx.
//
// "Restart" means: unmount everything, reset the in-memory store, rehydrate it from the same
// localStorage and mount a fresh app with a fresh QueryClient. The Zustand module is loaded once
// per test file, so rehydrating is what a page reload does to it.
import { StrictMode } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import {
  act,
  cleanup,
  configure,
  render,
  screen,
  waitFor,
  within,
  type RenderResult,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createQueryClient } from '@mtg-stats/api-client';
import { AppRoot } from '../providers/AppRoot';
import { ACTIVE_PLAYGROUP_ITEM_NAME, useActivePlaygroupStore } from '../playgroups/active-store';
import { routes } from '../routes';
import { handleSessionExpired } from '../session/sign-out';
import { useSessionStore } from '../session/store';
import {
  failure,
  group,
  json,
  person,
  stubWorld,
  type Call,
  type Role,
  type World,
} from '../test/playgroups-api';

// The first test in a worker pays the cold start of the whole module graph.
configure({ asyncUtilTimeout: 5000 });

const STORE_KEY = ACTIVE_PLAYGROUP_ITEM_NAME;

// ---------------------------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------------------------

interface Hit {
  method: string;
  path: string;
  body: string;
}
type Rule = (hit: Hit) => Response | Promise<Response | undefined> | undefined;

/** Wraps the current fetch stub: a rule may answer, delay (return a promise) or fail a request. */
function intercept(rule: Rule): void {
  const inner = globalThis.fetch;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const probe = input instanceof Request ? input : new Request(input, init);
      const hit: Hit = {
        method: probe.method,
        path: new URL(probe.url).pathname,
        body: await probe.clone().text(),
      };
      const answer = await rule(hit);
      return answer ?? inner(input, init);
    }),
  );
}

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

const loginOk = () => json({ access_token: 'access-x', token_type: 'bearer', expires_in: 900 }); // secrets-check: allow

function mount(
  path: string,
  options: { queryClient?: QueryClient; signedIn?: boolean } = {},
): { router: ReturnType<typeof createMemoryRouter>; queryClient: QueryClient; view: RenderResult } {
  useSessionStore.setState(
    options.signedIn === false
      ? { status: 'signed_out', accessToken: null }
      : { status: 'signed_in', accessToken: 'access-1' }, // secrets-check: allow
  );
  const queryClient =
    options.queryClient ??
    createQueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const view = render(
    <StrictMode>
      <AppRoot router={router} queryClient={queryClient} />
    </StrictMode>,
  );
  return { router, queryClient, view };
}

/** What the app does at load: read the stored value. A failure there is never fatal. */
async function rehydrate(): Promise<void> {
  await Promise.resolve(useActivePlaygroupStore.persist.rehydrate()).catch(() => undefined);
}

/** A restart: everything unmounted, the store rebuilt from what is in localStorage. */
async function restart(): Promise<void> {
  const stored = localStorage.getItem(STORE_KEY);
  cleanup();
  useActivePlaygroupStore.setState({ activeId: null }); // also rewrites storage: put it back
  if (stored === null) localStorage.removeItem(STORE_KEY);
  else localStorage.setItem(STORE_KEY, stored);
  await rehydrate();
}

/** What a device holds in localStorage for the remembered playgroup, as the app wrote it. */
function rememberedId(): unknown {
  const raw = localStorage.getItem(STORE_KEY);
  if (raw === null) return null;
  return (JSON.parse(raw) as { state?: { activeId?: unknown } }).state?.activeId ?? null;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  useSessionStore.setState({ status: 'signed_out', accessToken: null, signOutReason: null });
});

// ---------------------------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------------------------

const THREE = () => [
  group('g1', 'Crew', 'owner'),
  group('g2', 'Brew', 'member'),
  group('g3', 'Stew', 'admin'),
];
const ROSTERS = (): World['players'] => ({
  g1: [
    person('a1', 'Olive', 'owner'),
    person('a2', 'Amy', 'admin'),
    person('a3', 'Zed', 'member'),
    person('a4', 'Gus', null),
  ],
  g2: [
    person('b1', 'Bianca', 'owner'),
    person('b2', 'Bruno', 'member'),
    person('b3', 'Greta', null),
  ],
  g3: [
    person('c1', 'Cyrus', 'owner'),
    person('c2', 'Cleo', 'admin'),
    person('c3', 'Coral', 'member'),
    person('c4', 'Cobb', null),
  ],
});
const threeGroups = (over: Partial<World> = {}): World => ({
  groups: THREE(),
  players: ROSTERS(),
  ...over,
});

const heading2 = (name: string) => screen.findByRole('heading', { level: 2, name });
const combobox = () => screen.getByRole('combobox');
/** Alerts with something in them (the toast host is an always-present, empty alert region). */
const liveAlerts = () =>
  screen.queryAllByRole('alert').filter((alert) => (alert.textContent ?? '').trim() !== '');
const playgroupCalls = (calls: Call[]) =>
  calls.filter((c) => /^\/api\/v1\/playgroups\/[^/]+/.test(c.path));

async function pick(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(combobox());
  await user.click(await screen.findByRole('option', { name }));
}

// ---------------------------------------------------------------------------------------------
// Criterion 1: a user with no playgroups
// ---------------------------------------------------------------------------------------------

describe('AC1: a user with no playgroups sees the empty state', () => {
  it('shows "Create a playgroup" and "I have an invite" as focusable links once the list has loaded', async () => {
    stubWorld({ groups: [], players: {} });
    mount('/');
    const create = await screen.findByRole('link', { name: 'Create a playgroup' });
    const invite = screen.getByRole('link', { name: 'I have an invite' });
    for (const link of [create, invite]) {
      expect(link).toHaveAttribute('href');
      expect(link).not.toHaveAttribute('tabindex', '-1');
      link.focus();
      expect(link).toHaveFocus();
    }
    // Loading is over: no loading text is left behind the empty state.
    expect(screen.queryByText(/Loading your playgroups/)).not.toBeInTheDocument();
  });

  it('shows the empty state after signing in through the sign-in form', async () => {
    stubWorld({ groups: [], players: {} });
    intercept((hit) => (hit.path === '/api/v1/auth/login' ? loginOk() : undefined));
    const user = userEvent.setup();
    const { router } = mount('/sign-in', { signedIn: false });
    await user.type(await screen.findByRole('textbox', { name: 'Username' }), 'ada');
    await user.type(screen.getByLabelText('Password'), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('link', { name: 'Create a playgroup' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'I have an invite' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/');
  });

  it('reaches the create flow when "Create a playgroup" is followed with the keyboard', async () => {
    stubWorld({ groups: [], players: {} });
    const user = userEvent.setup();
    const { router } = mount('/');
    const create = await screen.findByRole('link', { name: 'Create a playgroup' });
    create.focus();
    await user.keyboard('{Enter}');
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/playgroups');
    });
    expect(await screen.findByRole('textbox', { name: 'Name' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create playgroup' })).toBeInTheDocument();
  });

  it('reaches the join screen when "I have an invite" is followed with the keyboard', async () => {
    stubWorld({ groups: [], players: {} });
    const user = userEvent.setup();
    const { router } = mount('/');
    const invite = await screen.findByRole('link', { name: 'I have an invite' });
    invite.focus();
    await user.keyboard('{Enter}');
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/playgroups/join');
    });
    expect(await heading2('Join a playgroup')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Invite link or code' })).toBeInTheDocument();
  });

  it('shows no switcher, no group data and never asks for a playgroup it does not have', async () => {
    const calls = stubWorld({ groups: [], players: { ghost: [person('x', 'Ghost', 'owner')] } });
    mount('/');
    await screen.findByRole('link', { name: 'I have an invite' });
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.queryByRole('listbox', { hidden: true })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { hidden: true })).not.toBeInTheDocument();
    expect(screen.queryByText('Ghost')).not.toBeInTheDocument();
    expect(document.querySelector('[data-slot]')).toBeNull();
    expect(playgroupCalls(calls)).toEqual([]);
    expect(screen.getAllByRole('link', { name: 'Create a playgroup' })).toHaveLength(1);
  });

  it('does not show the empty state while the list is still loading, and shows it when loaded', async () => {
    const gate = deferred();
    stubWorld({ groups: [], players: {} });
    intercept((hit) =>
      hit.path === '/api/v1/playgroups' && hit.method === 'GET'
        ? gate.promise.then(() => undefined)
        : undefined,
    );
    mount('/');
    expect(await screen.findByText(/Loading your playgroups/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'I have an invite' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Create a playgroup' })).not.toBeInTheDocument();
    gate.release();
    expect(await screen.findByRole('link', { name: 'I have an invite' })).toBeInTheDocument();
    expect(screen.queryByText(/Loading your playgroups/)).not.toBeInTheDocument();
  });

  it.each([
    ['a 500 response', () => failure(500, 'internal_error', 'Server fell over.')],
    ['a network failure', () => Promise.reject(new TypeError('Failed to fetch'))],
  ])(
    'shows an error, not the empty state, when the list request fails with %s, and Retry recovers',
    async (_label, fail) => {
      stubWorld({ groups: [], players: {} });
      let failing = true;
      intercept((hit) => {
        if (failing && hit.path === '/api/v1/playgroups' && hit.method === 'GET') {
          return fail() as Response | Promise<Response>;
        }
        return undefined;
      });
      const user = userEvent.setup();
      mount('/');
      const retry = await screen.findByRole('button', { name: 'Retry' });
      expect(retry.closest('[role="alert"]')?.textContent?.trim().length).toBeGreaterThan(
        'Retry'.length,
      );
      expect(screen.queryByRole('link', { name: 'I have an invite' })).not.toBeInTheDocument();
      expect(screen.queryByRole('link', { name: 'Create a playgroup' })).not.toBeInTheDocument();
      expect(screen.queryByText(/not in a playgroup yet/i)).not.toBeInTheDocument();

      failing = false;
      await user.click(retry);
      expect(await screen.findByRole('link', { name: 'I have an invite' })).toBeInTheDocument();
      expect(liveAlerts()).toEqual([]);
    },
  );
});

// ---------------------------------------------------------------------------------------------
// Criterion 2: the switcher and the remembered choice
// ---------------------------------------------------------------------------------------------

describe('AC2: picking a playgroup in the switcher', () => {
  it('shows the chosen group and its people and none of the previous group, and back again', async () => {
    const calls = stubWorld(threeGroups());
    const user = userEvent.setup();
    const { router } = mount('/');
    await heading2('Crew');
    await screen.findByText('Olive');
    expect(router.state.location.pathname).toBe('/playgroups/g1');

    const callsBefore = calls.length;
    await pick(user, 'Brew');
    await heading2('Brew');
    await screen.findByText('Bianca');
    expect(router.state.location.pathname).toBe('/playgroups/g2');
    expect(screen.getByText('Bruno')).toBeInTheDocument();
    expect(screen.getByText('Greta')).toBeInTheDocument();
    for (const gone of ['Olive', 'Amy', 'Zed', 'Gus']) {
      expect(screen.queryByText(gone), `${gone} belongs to Crew`).not.toBeInTheDocument();
    }
    expect(screen.queryByRole('heading', { level: 2, name: 'Crew' })).not.toBeInTheDocument();
    expect(combobox()).toHaveAccessibleName(/Brew/);

    // Every playgroup-scoped request after the switch is about the new group.
    const scoped = playgroupCalls(calls.slice(callsBefore));
    expect(scoped.length).toBeGreaterThan(0);
    for (const call of scoped) {
      expect(call.path, `${call.method} ${call.path}`).toMatch(/^\/api\/v1\/playgroups\/g2(\/|$)/);
    }
    expect(scoped.map((c) => c.path)).toContain('/api/v1/playgroups/g2/players');

    await pick(user, 'Crew');
    await heading2('Crew');
    await screen.findByText('Olive');
    expect(screen.getByText('Zed')).toBeInTheDocument();
    for (const gone of ['Bianca', 'Bruno', 'Greta']) {
      expect(screen.queryByText(gone), `${gone} belongs to Brew`).not.toBeInTheDocument();
    }
    expect(router.state.location.pathname).toBe('/playgroups/g1');
  });

  it('never shows the previous group while the new one is still loading', async () => {
    stubWorld(threeGroups());
    const gate = deferred();
    let hold = false;
    intercept((hit) =>
      hold && hit.path.startsWith('/api/v1/playgroups/g3')
        ? gate.promise.then(() => undefined)
        : undefined,
    );
    const user = userEvent.setup();
    mount('/playgroups/g1');
    await screen.findByText('Olive');
    hold = true;
    await pick(user, 'Stew');
    await waitFor(() => {
      expect(screen.queryByText('Olive')).not.toBeInTheDocument();
    });
    expect(screen.queryByText('Amy')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 2, name: 'Crew' })).not.toBeInTheDocument();
    expect(screen.queryByText('Cyrus')).not.toBeInTheDocument();
    gate.release();
    await screen.findByText('Cyrus');
    expect(await heading2('Stew')).toBeInTheDocument();
  });

  it('remembers the choice across a restart and shows that group first', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    mount('/');
    await heading2('Crew');
    await pick(user, 'Stew');
    await heading2('Stew');
    expect(rememberedId()).toBe('g3');

    await restart();
    stubWorld(threeGroups());
    const { router } = mount('/');
    expect(await heading2('Stew')).toBeInTheDocument();
    await screen.findByText('Cyrus');
    expect(router.state.location.pathname).toBe('/playgroups/g3');
    expect(screen.queryByText('Olive')).not.toBeInTheDocument();
    expect(combobox()).toHaveAccessibleName(/Stew/);
  });

  it('remembers a group reached by a deep link', async () => {
    stubWorld(threeGroups());
    mount('/playgroups/g2');
    await heading2('Brew');
    await waitFor(() => {
      expect(rememberedId()).toBe('g2');
    });
    await restart();
    stubWorld(threeGroups());
    const { router } = mount('/');
    await heading2('Brew');
    expect(router.state.location.pathname).toBe('/playgroups/g2');
  });

  it('shows the remembered group when it is on a later page of the list', async () => {
    localStorage.setItem(STORE_KEY, JSON.stringify({ state: { activeId: 'g3' }, version: 1 }));
    await rehydrate();
    stubWorld(threeGroups({ pageSize: 2 }));
    const { router } = mount('/');
    expect(await heading2('Stew')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/playgroups/g3');
    expect(rememberedId()).toBe('g3');
    expect(screen.queryByText('Olive')).not.toBeInTheDocument();
  });

  it.each([
    ['the whole list', undefined],
    ['a list paged two at a time', 2],
  ])(
    'falls back to the first group and corrects the stored value when the remembered id is no longer one of the playgroups (%s)',
    async (_label, pageSize) => {
      localStorage.setItem(
        STORE_KEY,
        JSON.stringify({ state: { activeId: 'left-long-ago' }, version: 1 }),
      );
      await rehydrate();
      stubWorld(threeGroups(pageSize ? { pageSize } : {}));
      const { router } = mount('/');
      expect(await heading2('Crew')).toBeInTheDocument();
      expect(router.state.location.pathname).toBe('/playgroups/g1');
      await waitFor(() => {
        expect(rememberedId()).toBe('g1');
      });
    },
  );

  const HUGE = 'x'.repeat(200_000);
  it.each([
    ['text that is not JSON', 'not json {{{'],
    ['a bare number', '42'],
    ['an object instead of the state', '{"a":1}'],
    ['a state whose id is a number', JSON.stringify({ state: { activeId: 42 }, version: 1 })],
    [
      'a state whose id is an object',
      JSON.stringify({ state: { activeId: { id: 'g2' } }, version: 1 }),
    ],
    ['a state whose id is an array', JSON.stringify({ state: { activeId: ['g2'] }, version: 1 })],
    [
      'a state whose id is an empty string',
      JSON.stringify({ state: { activeId: '' }, version: 1 }),
    ],
    ['a null state', JSON.stringify({ state: null, version: 1 })],
    ['an empty string', ''],
    ['a huge string that is not JSON', HUGE],
    ['a huge id', JSON.stringify({ state: { activeId: 'y'.repeat(5_000) }, version: 1 })],
  ])('ignores a corrupted stored value without crashing: %s', async (_label, raw) => {
    localStorage.setItem(STORE_KEY, raw);
    await rehydrate();
    stubWorld(threeGroups());
    mount('/');
    expect(await heading2('Crew')).toBeInTheDocument();
    await screen.findByText('Olive');
    expect(
      screen.queryByRole('heading', { name: /something went wrong/i }),
    ).not.toBeInTheDocument();
    await waitFor(() => {
      expect(rememberedId()).toBe('g1');
    });
  });

  it('keeps working when localStorage throws on every read', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    await rehydrate();
    stubWorld(threeGroups());
    const user = userEvent.setup();
    mount('/');
    expect(await heading2('Crew')).toBeInTheDocument();
    await pick(user, 'Brew');
    expect(await heading2('Brew')).toBeInTheDocument();
    await screen.findByText('Bianca');
  });

  it('keeps working, and the choice holds for the session, when localStorage throws on every write', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError');
    });
    stubWorld(threeGroups());
    const user = userEvent.setup();
    const { router } = mount('/');
    expect(await heading2('Crew')).toBeInTheDocument();
    await pick(user, 'Stew');
    expect(await heading2('Stew')).toBeInTheDocument();
    await screen.findByText('Cyrus');
    expect(router.state.location.pathname).toBe('/playgroups/g3');
    // Going home again within the session still lands on the chosen group.
    await act(async () => {
      await router.navigate('/');
    });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/playgroups/g3');
    });
  });

  it('forgets the choice on sign-out, so the next account on the same browser does not inherit it', async () => {
    // Account A is in g1, g2 and g3 and remembers g3. Account B also belongs to g3 (a shared
    // playgroup) but its first playgroup is "Mine".
    const accountB = (): World => ({
      groups: [group('m1', 'Mine', 'owner'), group('g3', 'Stew', 'member')],
      players: { m1: [person('d1', 'Dora', 'owner')], g3: ROSTERS().g3 ?? [] },
    });
    stubWorld(threeGroups());
    intercept((hit) => {
      if (hit.path === '/api/v1/auth/logout') return new Response(null, { status: 204 });
      return undefined;
    });
    const user = userEvent.setup();
    mount('/');
    await heading2('Crew');
    await pick(user, 'Stew');
    await heading2('Stew');
    expect(rememberedId()).toBe('g3');

    await user.click(screen.getByRole('button', { name: 'Account menu' }));
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(await heading2('Sign in')).toBeInTheDocument();
    expect(rememberedId()).toBeNull();
    expect(useActivePlaygroupStore.getState().activeId).toBeNull();

    // The second account signs in through the form, in the same browser.
    cleanup();
    stubWorld(accountB());
    intercept((hit) => (hit.path === '/api/v1/auth/login' ? loginOk() : undefined));
    mount('/sign-in', { signedIn: false });
    await user.type(await screen.findByRole('textbox', { name: 'Username' }), 'bea');
    await user.type(screen.getByLabelText('Password'), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await heading2('Mine')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 2, name: 'Stew' })).not.toBeInTheDocument();
    expect(combobox()).toHaveAccessibleName(/Mine/);
  });

  it('forgets the choice when the session expires', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    mount('/');
    await heading2('Crew');
    await pick(user, 'Brew');
    await heading2('Brew');
    expect(rememberedId()).toBe('g2');
    act(() => {
      handleSessionExpired();
    });
    expect(await heading2('Sign in')).toBeInTheDocument();
    expect(rememberedId()).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
// Criterion 3: the playgroup home, people and role-based actions
// ---------------------------------------------------------------------------------------------

const ACTION_TITLES = [
  'Invite people',
  'Name and description',
  'House rules',
  'Manage guests',
  'Admins and members',
  'Transfer ownership',
] as const;
type ActionTitle = (typeof ACTION_TITLES)[number];

/**
 * Who may do what, derived from the API's own rules (route descriptions in openapi.json):
 * - invites: create, list and revoke are "owner or admin only" (403 for a plain member)
 * - name and description: PATCH /playgroups/{id}, "owner or admin only"
 * - house rules: PUT is "owner or admin only" (GET is any member)
 * - guests: POST is "any member may"; PATCH is the guest's creator, the owner and admins
 * - change a member's role: PATCH /members/{player_id}, "Owner only"
 * - transfer ownership: "Owner only"
 */
const API_MATRIX: Record<ActionTitle, readonly Role[]> = {
  'Invite people': ['owner', 'admin'],
  'Name and description': ['owner', 'admin'],
  'House rules': ['owner', 'admin'],
  'Manage guests': ['owner', 'admin', 'member'],
  'Admins and members': ['owner'],
  'Transfer ownership': ['owner'],
};

const viewerWorld = (role: Role, over: Partial<World> = {}): World => ({
  groups: [group('g1', 'Crew', role), group('g2', 'Brew', 'owner')],
  players: ROSTERS(),
  ...over,
});

const row = (name: string) => {
  const item = screen.getByText(name, { exact: true }).closest('li');
  if (!item) throw new Error(`${name} is not in a list item`);
  return item;
};
const precedes = (a: HTMLElement, b: HTMLElement) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

describe('AC3: who is shown, in which order, with which role', () => {
  it.each<Role>(['owner', 'admin', 'member'])(
    'lists owner, admins, members, then guests, each with the role as text (viewer is %s)',
    async (viewer) => {
      // The API order is members then guests; interleave on purpose so grouping is the page's job.
      stubWorld(
        viewerWorld(viewer, {
          players: {
            g1: [
              person('a3', 'Zed', 'member'),
              person('a4', 'Gus', null),
              person('a2', 'Amy', 'admin'),
              person('a5', 'Bob', 'member'),
              person('a1', 'Olive', 'owner'),
              person('a6', 'Hal', null),
              person('a7', 'Dan', 'admin'),
            ],
          },
        }),
      );
      mount('/playgroups/g1');
      await heading2('Crew');
      await screen.findByText('Olive');

      const people = within(screen.getByRole('region', { name: 'People' }));
      const expected: [string, string][] = [
        ['Olive', 'Owner'],
        ['Amy', 'Admin'],
        ['Dan', 'Admin'],
        ['Zed', 'Member'],
        ['Bob', 'Member'],
        ['Gus', 'Guest'],
        ['Hal', 'Guest'],
      ];
      const items = people.getAllByRole('listitem');
      expect(items).toHaveLength(expected.length);
      for (const [name, role] of expected) {
        expect(row(name), name).toHaveTextContent(new RegExp(`\\(${role}\\)`));
        // Only that role is written on the row.
        for (const other of ['Owner', 'Admin', 'Member', 'Guest'].filter((r) => r !== role)) {
          expect(row(name), `${name} is not ${other}`).not.toHaveTextContent(`(${other})`);
        }
      }
      // DOM order is the reading order: owner, admins, members, guests.
      const order = expected.map(([name]) => row(name));
      for (let i = 1; i < order.length; i += 1) {
        expect(
          precedes(order[i - 1] as HTMLElement, order[i] as HTMLElement),
          `${expected[i]?.[0] ?? ''}`,
        ).toBe(true);
      }
      // Within a group the API order is kept.
      expect(precedes(row('Zed'), row('Bob'))).toBe(true);
      expect(precedes(row('Amy'), row('Dan'))).toBe(true);
      // The viewer's own role is stated as text.
      expect(screen.getByText('Your role:').parentElement).toHaveTextContent(
        `Your role: ${viewer[0]?.toUpperCase() ?? ''}${viewer.slice(1)}`,
      );
    },
  );

  it('groups the people under headings in the order Owner, Admins, Members, Guests, omitting empty groups', async () => {
    stubWorld(viewerWorld('member'));
    mount('/playgroups/g1');
    await screen.findByText('Olive');
    const people = within(screen.getByRole('region', { name: 'People' }));
    expect(people.getAllByRole('heading', { level: 4 }).map((h) => h.textContent)).toEqual([
      'Owner',
      'Admins',
      'Members',
      'Guests',
    ]);
    cleanup();

    // A playgroup with only an owner and one guest shows only those two groups.
    stubWorld(
      viewerWorld('owner', {
        players: { g1: [person('a1', 'Olive', 'owner'), person('a4', 'Gus', null)] },
      }),
    );
    mount('/playgroups/g1');
    await screen.findByText('Gus');
    const only = within(screen.getByRole('region', { name: 'People' }));
    expect(only.getAllByRole('heading', { level: 4 }).map((h) => h.textContent)).toEqual([
      'Owner',
      'Guests',
    ]);
  });

  it('marks guests as guests and nobody else', async () => {
    stubWorld(viewerWorld('admin'));
    mount('/playgroups/g1');
    await screen.findByText('Gus');
    expect(row('Gus')).toHaveTextContent('(Guest)');
    for (const member of ['Olive', 'Amy', 'Zed']) {
      expect(row(member)).not.toHaveTextContent('Guest');
    }
    const guests = within(screen.getByRole('region', { name: 'Guests' }));
    expect(guests.getAllByRole('listitem')).toHaveLength(1);
  });

  it.each([
    [9, 3],
    [10, 3],
    [4, 4],
    [5, 2],
  ])(
    'shows a roster of %i members over pages of %i whole, in the right groups, with no duplicates',
    async (memberCount, pageSize) => {
      const owner = person('own', 'Olivia', 'owner');
      const admins = [person('ad1', 'Adam', 'admin'), person('ad2', 'Ada', 'admin')];
      const members = Array.from({ length: memberCount }, (_, i) =>
        person(`m${String(i)}`, `Member${String(i).padStart(2, '0')}`, 'member'),
      );
      const guests = [person('gu1', 'Guest01', null), person('gu2', 'Guest02', null)];
      const all = [owner, ...admins, ...members, ...guests];
      const calls = stubWorld(
        viewerWorld('owner', { players: { g1: all }, playersPageSize: pageSize }),
      );
      mount('/playgroups/g1');
      await screen.findByText('Guest02');
      const names = (title: string) =>
        within(screen.getByRole('region', { name: title }))
          .getAllByRole('listitem')
          .map((li) => li.textContent ?? '');
      expect(names('Owner')).toHaveLength(1);
      expect(names('Admins')).toHaveLength(2);
      expect(names('Members')).toHaveLength(memberCount);
      expect(names('Guests')).toHaveLength(2);
      const everyone = screen
        .getAllByRole('listitem')
        .filter((li) => /\((Owner|Admin|Member|Guest)\)/.test(li.textContent ?? ''));
      expect(everyone).toHaveLength(all.length);
      expect(new Set(everyone.map((li) => li.textContent)).size).toBe(all.length);
      // Every page was asked for once, in cursor order, and nothing beyond the last.
      const cursors = calls
        .filter((c) => c.path === '/api/v1/playgroups/g1/players')
        .map((c) => c.query.get('cursor'))
        // StrictMode mounts the observer twice, which asks for the first page twice.
        .filter((cursor, i, all) => i === 0 || cursor !== all[i - 1]);
      const pages = Math.ceil(all.length / pageSize);
      expect(cursors).toEqual(
        Array.from({ length: pages }, (_, i) => (i === 0 ? null : String(i * pageSize))),
      );
    },
  );

  it('does not show a half-loaded roster as if it were the whole one', async () => {
    const gate = deferred();
    const many = Array.from({ length: 6 }, (_, i) =>
      person(`m${String(i)}`, `Pal${String(i)}`, 'member'),
    );
    stubWorld(
      viewerWorld('owner', {
        players: { g1: [person('o', 'Olivia', 'owner'), ...many] },
        playersPageSize: 3,
      }),
    );
    let seen = 0;
    intercept((hit) => {
      if (hit.path !== '/api/v1/playgroups/g1/players') return undefined;
      seen += 1;
      return seen === 2 ? gate.promise.then(() => undefined) : undefined;
    });
    mount('/playgroups/g1');
    await heading2('Crew');
    // The second page is held back: whatever is shown must not claim to be the final roster
    // (no group list that is missing the people still to come).
    await waitFor(() => {
      expect(seen).toBeGreaterThanOrEqual(2);
    });
    expect(screen.queryByText('Pal5')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Members' })).not.toBeInTheDocument();
    gate.release();
    await screen.findByText('Pal5');
    expect(
      within(screen.getByRole('region', { name: 'Members' })).getAllByRole('listitem'),
    ).toHaveLength(6);
  });
});

describe('AC3: actions by role (hidden means not in the DOM)', () => {
  it.each<Role>(['owner', 'admin', 'member'])(
    'shows a %s exactly the actions the API allows and leaves the rest out of the DOM',
    async (viewer) => {
      stubWorld(viewerWorld(viewer));
      mount('/playgroups/g1');
      await heading2('Crew');
      await screen.findByText('Olive');
      const allowed = ACTION_TITLES.filter((title) => API_MATRIX[title].includes(viewer));
      for (const title of ACTION_TITLES) {
        const shown = allowed.includes(title);
        expect(
          screen.queryByText(title, { exact: true }) !== null,
          `${title} text for ${viewer}`,
        ).toBe(shown);
        expect(
          screen.queryByRole('region', { name: title, hidden: true }) !== null,
          `${title} region for ${viewer}`,
        ).toBe(shown);
      }
      // Not merely hidden: no element carries these slots, `hidden`, aria-hidden or display none.
      expect(document.querySelectorAll('[data-slot]')).toHaveLength(allowed.length);
      expect(screen.queryAllByText('Coming soon.')).toHaveLength(allowed.length);
      const manage = within(screen.getByRole('region', { name: 'Manage' }));
      expect(manage.getAllByRole('heading').map((h) => h.textContent)).toEqual([
        'Manage',
        ...allowed,
      ]);
      for (const el of document.querySelectorAll('[data-slot]')) {
        expect(el).not.toHaveAttribute('hidden');
        expect(el).not.toHaveAttribute('aria-hidden');
        expect(el).not.toHaveAttribute('inert');
      }
    },
  );

  it('offers a plain member no control that invites, edits, changes roles or transfers ownership', async () => {
    stubWorld(viewerWorld('member'));
    mount('/playgroups/g1');
    await heading2('Crew');
    await screen.findByText('Olive');
    const main = within(screen.getByRole('main'));
    const ownerOrAdmin =
      /invite|edit|rename|house rules|name and description|admins and members|promote|demote|transfer|revoke/i;
    expect(main.queryAllByRole('button', { name: ownerOrAdmin })).toEqual([]);
    expect(main.queryAllByRole('link', { name: ownerOrAdmin })).toEqual([]);
    expect(main.queryAllByRole('region', { name: ownerOrAdmin })).toEqual([]);
  });

  it('offers an admin no owner-only control (roles, ownership)', async () => {
    stubWorld(viewerWorld('admin'));
    mount('/playgroups/g1');
    await heading2('Crew');
    await screen.findByText('Olive');
    const main = within(screen.getByRole('main'));
    const ownerOnly = /transfer|promote|demote|admins and members|change.*role/i;
    expect(main.queryAllByRole('button', { name: ownerOnly })).toEqual([]);
    expect(main.queryAllByRole('link', { name: ownerOnly })).toEqual([]);
    expect(main.queryAllByRole('region', { name: ownerOnly })).toEqual([]);
    // ...but does see the owner-and-admin ones.
    expect(main.getByRole('region', { name: 'Invite people' })).toBeInTheDocument();
  });

  it('takes the actions away from a viewer who is demoted, without a reload', async () => {
    const world = viewerWorld('admin');
    stubWorld(world);
    const { queryClient } = mount('/playgroups/g1');
    await heading2('Crew');
    expect(await screen.findByRole('region', { name: 'Invite people' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'House rules' })).toBeInTheDocument();

    world.groups[0] = group('g1', 'Crew', 'member');
    await act(async () => {
      await queryClient.invalidateQueries();
    });
    await waitFor(() => {
      expect(screen.queryByRole('region', { name: 'Invite people' })).not.toBeInTheDocument();
    });
    expect(screen.queryByRole('region', { name: 'House rules' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Name and description' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Manage guests' })).toBeInTheDocument();
    expect(screen.getByText('Your role:').parentElement).toHaveTextContent('Your role: Member');
  });

  it('gives the actions to a viewer who is promoted to owner, without a reload', async () => {
    const world = viewerWorld('member');
    stubWorld(world);
    const { queryClient } = mount('/playgroups/g1');
    await heading2('Crew');
    expect(await screen.findByRole('region', { name: 'Manage guests' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Transfer ownership' })).not.toBeInTheDocument();

    world.groups[0] = group('g1', 'Crew', 'owner');
    await act(async () => {
      await queryClient.invalidateQueries();
    });
    expect(await screen.findByRole('region', { name: 'Transfer ownership' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Admins and members' })).toBeInTheDocument();
  });

  it('shows no action at all until the role is known', async () => {
    const gate = deferred();
    stubWorld(viewerWorld('owner'));
    intercept((hit) =>
      hit.path === '/api/v1/playgroups/g1' ? gate.promise.then(() => undefined) : undefined,
    );
    mount('/playgroups/g1');
    await screen.findByText(/Loading playgroup/);
    expect(document.querySelector('[data-slot]')).toBeNull();
    gate.release();
    expect(await screen.findByRole('region', { name: 'Transfer ownership' })).toBeInTheDocument();
  });
});

describe('AC3: a playgroup the user is not in, and failures', () => {
  it('shows the not-found page and none of the stubbed data when the detail answers 404 not_found', async () => {
    stubWorld(
      viewerWorld('owner', {
        // The roster request would succeed if it were trusted: it must not leak into the page.
        players: { ...ROSTERS(), stranger: [person('s1', 'Leaky', 'owner')] },
      }),
    );
    mount('/playgroups/stranger');
    expect(await heading2('Page not found')).toBeInTheDocument();
    expect(screen.queryByText('Leaky')).not.toBeInTheDocument();
    expect(screen.queryByText(/Your role/)).not.toBeInTheDocument();
    expect(document.querySelector('[data-slot]')).toBeNull();
    expect(rememberedId()).not.toBe('stranger');
    // The switcher still names the user's own group.
    expect(await screen.findByRole('combobox', { name: /Crew/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go home' })).toBeInTheDocument();
  });

  it('does not let a stranger group replace the remembered one', async () => {
    localStorage.setItem(STORE_KEY, JSON.stringify({ state: { activeId: 'g2' }, version: 1 }));
    await rehydrate();
    stubWorld(viewerWorld('owner'));
    mount('/playgroups/stranger');
    await heading2('Page not found');
    await waitFor(() => {
      expect(combobox()).toHaveAccessibleName(/Brew/);
    });
    expect(rememberedId()).toBe('g2');
  });

  it('shows an error with a Retry for a 500, shows no data meanwhile, and Retry works', async () => {
    stubWorld(viewerWorld('owner'));
    let failing = true;
    intercept((hit) =>
      failing && hit.path === '/api/v1/playgroups/g1'
        ? failure(500, 'internal_error', 'The server fell over.')
        : undefined,
    );
    const user = userEvent.setup();
    mount('/playgroups/g1');
    const alert = (await screen.findByText(/The server fell over\./)).closest(
      '[role="alert"]',
    ) as HTMLElement;
    expect(alert).toHaveTextContent('The server fell over.');
    expect(screen.queryByText('Olive')).not.toBeInTheDocument();
    expect(document.querySelector('[data-slot]')).toBeNull();
    expect(
      screen.queryByRole('heading', { level: 2, name: 'Page not found' }),
    ).not.toBeInTheDocument();

    failing = false;
    await user.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await heading2('Crew')).toBeInTheDocument();
    await screen.findByText('Olive');
    expect(liveAlerts()).toEqual([]);
  });

  it('shows an error with a Retry for the people list alone, keeping the rest, and Retry works', async () => {
    stubWorld(viewerWorld('owner'));
    let failing = true;
    intercept((hit) =>
      failing && hit.path === '/api/v1/playgroups/g1/players'
        ? failure(500, 'internal_error', 'No people today.')
        : undefined,
    );
    const user = userEvent.setup();
    mount('/playgroups/g1');
    await heading2('Crew');
    const people = within(screen.getByRole('region', { name: 'People' }));
    const alert = await people.findByRole('alert');
    expect(alert).toHaveTextContent('No people today.');
    expect(screen.queryByText('Olive')).not.toBeInTheDocument();
    failing = false;
    await user.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Olive')).toBeInTheDocument();
  });

  it('treats the code, not the message, as the not-found signal', async () => {
    stubWorld(viewerWorld('owner'));
    intercept((hit) =>
      hit.path === '/api/v1/playgroups/g1'
        ? failure(404, 'not_found', 'Some brand new wording.')
        : undefined,
    );
    mount('/playgroups/g1');
    expect(await heading2('Page not found')).toBeInTheDocument();
    expect(screen.queryByText(/brand new wording/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------------------------
// Basic accessibility of the new screens. The repo has no axe dependency, so these are the
// structural checks that matter for the screens: heading order, one h1 and one h2, names,
// unique ids, labelled fields.
// ---------------------------------------------------------------------------------------------

function headingLevels(): number[] {
  return screen.getAllByRole('heading').map((h) => Number(h.tagName.slice(1)));
}
function expectSaneStructure() {
  const levels = headingLevels();
  expect(levels[0]).toBe(1);
  expect(levels.filter((l) => l === 1)).toHaveLength(1);
  expect(levels.filter((l) => l === 2)).toHaveLength(1);
  for (let i = 1; i < levels.length; i += 1) {
    expect(
      levels[i] as number,
      `heading ${String(i)} follows level ${String(levels[i - 1])}`,
    ).toBeLessThanOrEqual((levels[i - 1] as number) + 1);
  }
  const ids = [...document.querySelectorAll('[id]')].map((el) => el.id);
  expect(ids.filter((id, i) => ids.indexOf(id) !== i)).toEqual([]);
  for (const control of screen.getAllByRole('button')) expect(control).toHaveAccessibleName();
  for (const control of screen.getAllByRole('link')) expect(control).toHaveAccessibleName();
}

describe('accessibility of the new screens', () => {
  it('keeps headings in order and every control named on the playgroup home (owner)', async () => {
    stubWorld(viewerWorld('owner'));
    mount('/playgroups/g1');
    await screen.findByText('Olive');
    await screen.findByRole('region', { name: 'Transfer ownership' });
    expectSaneStructure();
    expect(document.title).toBe('Crew · MTG Stats');
    const names = within(screen.getByRole('main'))
      .getAllByRole('region')
      .map((r) => document.getElementById(r.getAttribute('aria-labelledby') ?? '')?.textContent);
    expect(names.every(Boolean)).toBe(true);
    expect(names.filter((n, i) => names.indexOf(n) !== i)).toEqual([]);
  });

  it('keeps headings in order on the empty state', async () => {
    stubWorld({ groups: [], players: {} });
    mount('/');
    await screen.findByRole('link', { name: 'I have an invite' });
    expectSaneStructure();
    expect(headingLevels()).toEqual([1, 2, 3]);
  });

  it('keeps headings in order and the field labelled on the join screen', async () => {
    stubWorld({ groups: [], players: {} });
    mount('/playgroups/join');
    await heading2('Join a playgroup');
    expectSaneStructure();
    expect(screen.getByRole('textbox', { name: 'Invite link or code' })).toBeInTheDocument();
    expect(document.title).toBe('Join a playgroup · MTG Stats');
  });

  it('keeps headings in order on the not-found state and on the list error state', async () => {
    stubWorld(viewerWorld('owner'));
    mount('/playgroups/stranger');
    await heading2('Page not found');
    expectSaneStructure();
    cleanup();

    stubWorld({ groups: [], players: {} });
    intercept((hit) =>
      hit.path === '/api/v1/playgroups' ? failure(500, 'internal_error', 'Broken.') : undefined,
    );
    mount('/');
    await screen.findByRole('button', { name: 'Retry' });
    expectSaneStructure();
  });

  it('shows a single alert for a failed load', async () => {
    stubWorld(viewerWorld('owner'));
    intercept((hit) =>
      hit.path === '/api/v1/playgroups/g1' ? failure(500, 'internal_error', 'Broken.') : undefined,
    );
    mount('/playgroups/g1');
    await screen.findByText(/Broken\./);
    expect(liveAlerts()).toHaveLength(1);
  });
});
