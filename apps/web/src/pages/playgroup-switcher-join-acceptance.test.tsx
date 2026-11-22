// S-2.4.5 acceptance (T-2.4.5.3, web), criterion 4 and the "I have an invite" entry point:
// the playgroup switcher used with the keyboard only, and the join screen reached from the empty
// state. Driven through the real route table with a stubbed network (see
// playgroup-home-acceptance.test.tsx for criteria 1 to 3).
//
// Type-ahead (typing a letter to jump to a group) is not required by the criteria and is not
// tested.
import { StrictMode } from 'react';
import {
  act,
  configure,
  fireEvent,
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
import { ACTIVE_PLAYGROUP_ITEM_NAME } from '../playgroups/active-store';
import { routes } from '../routes';
import { useSessionStore } from '../session/store';
import { failure, group, json, person, stubWorld, type World } from '../test/playgroups-api';

configure({ asyncUtilTimeout: 5000 });

// ---------------------------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------------------------

interface Hit {
  method: string;
  origin: string;
  path: string;
  search: string;
  body: string;
}
type Rule = (hit: Hit) => Response | Promise<Response | undefined> | undefined;

/** Wraps the current fetch stub: a rule may answer or delay a request; otherwise it passes. */
function intercept(rule: Rule): void {
  const inner = globalThis.fetch;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const probe = input instanceof Request ? input : new Request(input, init);
      const url = new URL(probe.url);
      const hit: Hit = {
        method: probe.method,
        origin: url.origin,
        path: url.pathname,
        search: url.search,
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

function mount(path: string): {
  router: ReturnType<typeof createMemoryRouter>;
  view: RenderResult;
} {
  useSessionStore.setState({ status: 'signed_in', accessToken: 'access-1' }); // secrets-check: allow
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const view = render(
    <StrictMode>
      <AppRoot
        router={router}
        queryClient={createQueryClient({ defaultOptions: { queries: { retry: false } } })}
      />
    </StrictMode>,
  );
  return { router, view };
}

function rememberedId(): unknown {
  const raw = localStorage.getItem(ACTIVE_PLAYGROUP_ITEM_NAME);
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

type User = ReturnType<typeof userEvent.setup>;

const THREE = () => [
  group('g1', 'Crew', 'owner'),
  group('g2', 'Brew', 'member'),
  group('g3', 'Stew', 'admin'),
];
const ROSTERS = (): World['players'] => ({
  g1: [person('a1', 'Olive', 'owner'), person('a4', 'Gus', null)],
  g2: [person('b1', 'Bianca', 'owner'), person('b3', 'Greta', null)],
  g3: [person('c1', 'Cyrus', 'owner'), person('c4', 'Cobb', null)],
});
const threeGroups = (over: Partial<World> = {}): World => ({
  groups: THREE(),
  players: ROSTERS(),
  ...over,
});

const heading2 = (name: string) => screen.findByRole('heading', { level: 2, name });
const trigger = () => screen.getByRole('combobox');

/** Where the highlighted option is: the option that `aria-activedescendant` names. */
function highlightedName(): string | null {
  const id = trigger().getAttribute('aria-activedescendant');
  if (!id) return null;
  const option = document.getElementById(id);
  expect(option, 'aria-activedescendant points at an element').not.toBeNull();
  expect(option).toHaveAttribute('role', 'option');
  return option?.textContent ?? null;
}
const selectedNames = () =>
  screen
    .queryAllByRole('option')
    .filter((o) => o.getAttribute('aria-selected') === 'true')
    .map((o) => o.textContent);

/** Tab from the top of the page until the switcher has focus: the only way a keyboard gets there. */
async function tabToSwitcher(user: User): Promise<number> {
  const target = await screen.findByRole('combobox');
  for (let presses = 0; presses < 60; presses += 1) {
    if (document.activeElement === target) return presses;
    await user.tab();
  }
  throw new Error('Tab never reached the playgroup switcher in 60 presses');
}

async function openOnCrew(user: User) {
  const home = mount('/playgroups/g1');
  await heading2('Crew');
  await screen.findByText('Olive');
  await tabToSwitcher(user);
  return home;
}

// ---------------------------------------------------------------------------------------------
// Criterion 4: the switcher with the keyboard only
// ---------------------------------------------------------------------------------------------

describe('AC4: the switcher without a mouse', () => {
  it('is reached with Tab and has an accessible name that includes the active group', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    mount('/playgroups/g1');
    await heading2('Crew');
    const presses = await tabToSwitcher(user);
    expect(presses).toBeGreaterThan(0);
    expect(trigger()).toHaveFocus();
    expect(trigger()).not.toHaveAttribute('tabindex', '-1');
    expect(trigger()).toHaveAccessibleName(/Crew/);
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(trigger()).toHaveAttribute('aria-haspopup', 'listbox');
    // Closed: the list is not exposed and has no options in the tree.
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(screen.queryAllByRole('option')).toEqual([]);
  });

  it('points aria-controls at the listbox', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    await openOnCrew(user);
    const id = trigger().getAttribute('aria-controls');
    expect(id).toBeTruthy();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('listbox')).toHaveAttribute('id', id);
  });

  it.each([
    ['Enter', '{Enter}'],
    ['Space', ' '],
    ['ArrowDown', '{ArrowDown}'],
    ['ArrowUp', '{ArrowUp}'],
  ])('opens with %s, on the current group, and names the options by group', async (_name, key) => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    await openOnCrew(user);
    await user.keyboard(key);
    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    const listbox = screen.getByRole('listbox');
    const options = within(listbox).getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual(['Crew', 'Brew', 'Stew']);
    for (const option of options) expect(option).toHaveAccessibleName(option.textContent ?? '');
    expect(highlightedName()).toBe('Crew');
    expect(selectedNames()).toEqual(['Crew']);
    expect(trigger()).toHaveFocus();
  });

  it('moves through the options with the arrows, stopping at both ends, without changing the group', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    const { router } = await openOnCrew(user);
    await user.keyboard('{Enter}');
    const steps: [string, string][] = [
      ['{ArrowDown}', 'Brew'],
      ['{ArrowDown}', 'Stew'],
      ['{ArrowDown}', 'Stew'], // does not wrap to the first
      ['{ArrowUp}', 'Brew'],
      ['{ArrowUp}', 'Crew'],
      ['{ArrowUp}', 'Crew'], // does not wrap to the last
    ];
    for (const [key, expected] of steps) {
      await user.keyboard(key);
      expect(highlightedName(), key).toBe(expected);
      expect(trigger()).toHaveAttribute('aria-expanded', 'true');
      expect(trigger()).toHaveFocus();
      // Moving is not choosing: the selected option, the name and the page are still Crew's.
      expect(selectedNames()).toEqual(['Crew']);
      expect(trigger()).toHaveAccessibleName(/Crew/);
      expect(router.state.location.pathname).toBe('/playgroups/g1');
    }
    expect(screen.getByRole('heading', { level: 2, name: 'Crew' })).toBeInTheDocument();
  });

  it('jumps to the last option with End and to the first with Home', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    await openOnCrew(user);
    await user.keyboard('{Enter}');
    await user.keyboard('{End}');
    expect(highlightedName()).toBe('Stew');
    await user.keyboard('{Home}');
    expect(highlightedName()).toBe('Crew');
    await user.keyboard('{End}{ArrowUp}');
    expect(highlightedName()).toBe('Brew');
  });

  it('selects with Enter, closes, and shows the chosen group', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    const { router } = await openOnCrew(user);
    await user.keyboard('{Enter}{ArrowDown}{ArrowDown}{Enter}');
    expect(await heading2('Stew')).toBeInTheDocument();
    await screen.findByText('Cyrus');
    expect(router.state.location.pathname).toBe('/playgroups/g3');
    expect(screen.queryByText('Olive')).not.toBeInTheDocument();
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(trigger()).not.toHaveAttribute('aria-activedescendant');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(trigger()).toHaveAccessibleName(/Stew/);
    expect(rememberedId()).toBe('g3');
    // Focus was not dropped on the page body, so the keyboard user keeps their place.
    expect(document.activeElement).not.toBe(document.body);
    // And the new choice is the selected option when the list is opened again.
    trigger().focus();
    await user.keyboard('{Enter}');
    expect(selectedNames()).toEqual(['Stew']);
    expect(highlightedName()).toBe('Stew');
  });

  it('selects with Space', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    await openOnCrew(user);
    await user.keyboard('{ArrowDown}{End} ');
    expect(await heading2('Stew')).toBeInTheDocument();
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
  });

  it('selects with Enter on the first option too (Home, Enter) when another group was active', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    mount('/playgroups/g2');
    await heading2('Brew');
    await tabToSwitcher(user);
    await user.keyboard('{Enter}{Home}{Enter}');
    expect(await heading2('Crew')).toBeInTheDocument();
    await screen.findByText('Olive');
  });

  it('closes with Escape without changing the group, and focus is back on the trigger', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    const { router } = await openOnCrew(user);
    await user.keyboard('{Enter}{ArrowDown}{ArrowDown}');
    expect(highlightedName()).toBe('Stew');
    await user.keyboard('{Escape}');
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(trigger()).not.toHaveAttribute('aria-activedescendant');
    expect(trigger()).toHaveFocus();
    expect(trigger()).toHaveAccessibleName(/Crew/);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/playgroups/g1');
    expect(screen.getByRole('heading', { level: 2, name: 'Crew' })).toBeInTheDocument();
    expect(rememberedId()).toBe('g1');
    // Reopening starts from the current group again, not from where Escape left the highlight.
    await user.keyboard('{Enter}');
    expect(highlightedName()).toBe('Crew');
  });

  it('closes when Tab moves on, without selecting the highlighted option', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    const { router } = await openOnCrew(user);
    await user.keyboard('{Enter}{ArrowDown}');
    expect(highlightedName()).toBe('Brew');
    await user.tab();
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(trigger()).not.toHaveFocus();
    expect(trigger()).toHaveAccessibleName(/Crew/);
    expect(router.state.location.pathname).toBe('/playgroups/g1');
    expect(screen.getByRole('heading', { level: 2, name: 'Crew' })).toBeInTheDocument();
    expect(rememberedId()).toBe('g1');
  });

  it('closes when Shift+Tab moves back, without selecting', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    await openOnCrew(user);
    await user.keyboard('{Enter}{End}');
    await user.tab({ shift: true });
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(trigger()).toHaveAccessibleName(/Crew/);
    expect(screen.getByRole('heading', { level: 2, name: 'Crew' })).toBeInTheDocument();
  });

  it('can be opened, closed and opened again, and keeps working after a selection', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    await openOnCrew(user);
    await user.keyboard('{Enter}{Escape}{Enter}');
    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    await user.keyboard('{ArrowDown}{Enter}');
    expect(await heading2('Brew')).toBeInTheDocument();
    await screen.findByText('Bianca');
    // The page may move focus into <main> after navigating; the next Tab from there reaches the
    // switcher again, since it is the first thing in the page area.
    await tabToSwitcher(user);
    await user.keyboard('{Enter}{End}{Enter}');
    expect(await heading2('Stew')).toBeInTheDocument();
  });

  it('selecting the group that is already active changes nothing', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    const { router } = await openOnCrew(user);
    await user.keyboard('{Enter}{Enter}');
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(router.state.location.pathname).toBe('/playgroups/g1');
    expect(screen.getByRole('heading', { level: 2, name: 'Crew' })).toBeInTheDocument();
  });

  it('works with a single playgroup: it still names the group and opens with the keyboard', async () => {
    stubWorld({ groups: [group('g1', 'Crew', 'owner')], players: ROSTERS() });
    const user = userEvent.setup();
    const { router } = mount('/playgroups/g1');
    await heading2('Crew');
    await tabToSwitcher(user);
    expect(trigger()).toHaveAccessibleName(/Crew/);
    await user.keyboard('{ArrowDown}');
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Crew']);
    expect(highlightedName()).toBe('Crew');
    await user.keyboard('{ArrowDown}');
    expect(highlightedName()).toBe('Crew');
    await user.keyboard('{Enter}');
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(router.state.location.pathname).toBe('/playgroups/g1');
    expect(screen.getByRole('heading', { level: 2, name: 'Crew' })).toBeInTheDocument();
  });

  it('chooses the active group for people on pages other than a playgroup home, without navigating', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    const { router } = mount('/playgroups');
    await heading2('Playgroups');
    await tabToSwitcher(user);
    await user.keyboard('{Enter}{End}{Enter}');
    await waitFor(() => {
      expect(trigger()).toHaveAccessibleName(/Stew/);
    });
    expect(router.state.location.pathname).toBe('/playgroups');
    expect(rememberedId()).toBe('g3');
  });

  it('keeps the groups of a paged list reachable by keyboard through "Load more playgroups"', async () => {
    stubWorld(threeGroups({ pageSize: 2 }));
    const user = userEvent.setup();
    mount('/playgroups/g1');
    await heading2('Crew');
    await tabToSwitcher(user);
    await user.keyboard('{Enter}');
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Crew', 'Brew']);
    await user.keyboard('{Escape}');
    await user.tab();
    expect(screen.getByRole('button', { name: 'Load more playgroups' })).toHaveFocus();
    await user.keyboard('{Enter}');
    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: 'Load more playgroups' }),
      ).not.toBeInTheDocument();
    });
    trigger().focus();
    await user.keyboard('{Enter}{End}');
    expect(highlightedName()).toBe('Stew');
    await user.keyboard('{Enter}');
    expect(await heading2('Stew')).toBeInTheDocument();
  });

  it('ignores keys with a modifier held, so browser shortcuts keep working', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    await openOnCrew(user);
    await user.keyboard('{Control>}{ArrowDown}{/Control}');
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    await user.keyboard('{Alt>}{Enter}{/Alt}');
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
  });

  it('tells two playgroups with the same name apart: the keyboard picks the one highlighted', async () => {
    stubWorld({
      groups: [group('g1', 'Crew', 'owner'), group('g2', 'Crew', 'member')],
      players: ROSTERS(),
    });
    const user = userEvent.setup();
    const { router } = await openOnCrew(user);
    await user.keyboard('{Enter}{ArrowDown}');
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Crew', 'Crew']);
    await user.keyboard('{Enter}');
    await screen.findByText('Bianca');
    expect(router.state.location.pathname).toBe('/playgroups/g2');
    expect(screen.queryByText('Olive')).not.toBeInTheDocument();
    expect(rememberedId()).toBe('g2');
  });

  it('shows group and people names as plain text, never as markup', async () => {
    const hostile = '<img src=x onerror=alert(1)>';
    stubWorld({
      groups: [group('g1', hostile, 'owner'), group('g2', 'Brew', 'member')],
      players: { g1: [person('a1', '<b>Bold</b>', 'owner')], g2: [] },
    });
    const user = userEvent.setup();
    mount('/playgroups/g1');
    expect(await heading2(hostile)).toBeInTheDocument();
    expect(await screen.findByText('<b>Bold</b>')).toBeInTheDocument();
    await tabToSwitcher(user);
    expect(trigger()).toHaveAccessibleName(expect.stringContaining(hostile));
    expect(document.querySelector('main img, main b')).toBeNull();
  });

  it('renders nothing for the switcher when the user has no playgroups', async () => {
    stubWorld({ groups: [], players: {} });
    mount('/');
    await screen.findByRole('link', { name: 'I have an invite' });
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------------------------
// "I have an invite": the join screen
// ---------------------------------------------------------------------------------------------

const CODE = 'Zk3-vQ_9aLm'; // not a credential: an invite code made up for the test
const JOIN = '/api/v1/invites/join';
const field = () => screen.getByRole('textbox', { name: 'Invite link or code' });
const joinButton = () => screen.getByRole('button', { name: 'Join playgroup' });

interface JoinStub {
  joins: Hit[];
}

/** Stubs the world plus POST /invites/join. A successful join adds the group, newest first. */
function stubJoin(
  world: World,
  answer: (hit: Hit) => Response | Promise<Response | undefined> | undefined = () => undefined,
): JoinStub {
  stubWorld(world);
  const stub: JoinStub = { joins: [] };
  intercept(async (hit) => {
    if (hit.path !== JOIN) return undefined;
    stub.joins.push(hit);
    const custom = await answer(hit);
    if (custom) return custom;
    const joined = group('gj', 'Joined Crew', 'member');
    world.groups.unshift(joined);
    world.players.gj = [person('j1', 'Jolene', 'owner'), person('j2', 'Newcomer', 'member')];
    return json({ already_member: false, playgroup: joined });
  });
  return stub;
}

async function paste(user: User, text: string) {
  await user.click(field());
  if (text !== '') await user.paste(text);
}

describe('the join screen reached from "I have an invite"', () => {
  it('is reached from the empty state and takes a pasted link', async () => {
    const stub = stubJoin({ groups: [], players: {} });
    const user = userEvent.setup();
    const { router } = mount('/');
    const link = await screen.findByRole('link', { name: 'I have an invite' });
    link.focus();
    await user.keyboard('{Enter}');
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/playgroups/join');
    });
    await paste(user, `https://mtgstats.example/invite/${CODE}`);
    await user.keyboard('{Enter}'); // submitting the form with the keyboard
    expect(await screen.findByText('You joined Joined Crew.')).toBeInTheDocument();
    expect(stub.joins).toHaveLength(1);
  });

  it.each([
    ['a full link', `https://mtgstats.example/invite/${CODE}`],
    ['an app link', `mtgstats://invite/${CODE}`],
    ['a bare code', CODE],
    ['a bare code with whitespace around it', `  \t${CODE} \n `],
    ['a full link with whitespace around it', `\n  https://mtgstats.example/invite/${CODE}  `],
    ['a link with a trailing slash', `https://mtgstats.example/invite/${CODE}/`],
    ['an app link with whitespace', `  mtgstats://invite/${CODE}  `],
  ])('sends the same code to POST /invites/join for %s', async (_label, pasted) => {
    const stub = stubJoin({ groups: [group('g1', 'Crew', 'owner')], players: ROSTERS() });
    const user = userEvent.setup();
    mount('/playgroups/join');
    await heading2('Join a playgroup');
    await paste(user, pasted);
    await user.click(joinButton());
    await screen.findByText('You joined Joined Crew.');
    expect(stub.joins).toHaveLength(1);
    const [hit] = stub.joins;
    expect(hit?.method).toBe('POST');
    expect(hit?.search).toBe(''); // the code is in the body, never in a URL
    expect(JSON.parse(hit?.body ?? 'null')).toEqual({ token: CODE });
  });

  it('makes the joined group active, shows it in the switcher and offers a way into it', async () => {
    stubJoin({ groups: [group('g1', 'Crew', 'owner')], players: ROSTERS() });
    localStorage.setItem(
      ACTIVE_PLAYGROUP_ITEM_NAME,
      JSON.stringify({ state: { activeId: 'g1' }, version: 1 }),
    );
    const user = userEvent.setup();
    const { router } = mount('/playgroups/join');
    await heading2('Join a playgroup');
    await paste(user, CODE);
    await user.click(joinButton());
    await screen.findByText('You joined Joined Crew.');
    await waitFor(() => {
      expect(rememberedId()).toBe('gj');
    });
    // The refreshed list must not put the old group back.
    await waitFor(() => {
      expect(trigger()).toHaveAccessibleName(/Joined Crew/);
    });
    expect(rememberedId()).toBe('gj');
    expect(field()).toHaveValue('');
    const into = screen.getByRole('link', { name: 'Go to Joined Crew' });
    await user.click(into);
    expect(await heading2('Joined Crew')).toBeInTheDocument();
    await screen.findByText('Jolene');
    expect(router.state.location.pathname).toBe('/playgroups/gj');
    expect(screen.getByText('Your role:').parentElement).toHaveTextContent('Your role: Member');
    expect(screen.queryByText('Olive')).not.toBeInTheDocument();
  });

  it('makes the joined group active for a user who had no playgroups, and "/" then lands on it', async () => {
    stubJoin({ groups: [], players: {} });
    const user = userEvent.setup();
    const { router } = mount('/playgroups/join');
    await heading2('Join a playgroup');
    await paste(user, CODE);
    await user.click(joinButton());
    await screen.findByText('You joined Joined Crew.');
    await waitFor(() => {
      expect(trigger()).toHaveAccessibleName(/Joined Crew/);
    });
    expect(rememberedId()).toBe('gj');
    await act(async () => {
      await router.navigate('/');
    });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/playgroups/gj');
    });
    expect(await heading2('Joined Crew')).toBeInTheDocument();
  });

  it('says so when the user is already in the playgroup, and still points to it', async () => {
    stubJoin({ groups: [group('g1', 'Crew', 'owner')], players: ROSTERS() }, () =>
      json({ already_member: true, playgroup: group('g1', 'Crew', 'owner') }),
    );
    const user = userEvent.setup();
    mount('/playgroups/join');
    await paste(user, CODE);
    await user.click(joinButton());
    expect(await screen.findByText("You're already in Crew.")).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go to Crew' })).toHaveAttribute(
      'href',
      '/playgroups/g1',
    );
  });

  it.each([
    ['410 and wording A', 410, 'invite_no_longer_valid', 'Wording A: nobody can use this.'],
    ['410 and wording B', 410, 'invite_no_longer_valid', 'Totally different text now.'],
    ['400 with the same code', 400, 'invite_no_longer_valid', 'Wording C.'],
  ])(
    'shows the dead-invite error from the code, not the server text (%s)',
    async (_label, status, code, message) => {
      stubJoin({ groups: [group('g1', 'Crew', 'owner')], players: ROSTERS() }, () =>
        failure(status, code, message),
      );
      const user = userEvent.setup();
      mount('/playgroups/join');
      await paste(user, CODE);
      await user.click(joinButton());
      const alert = await screen.findByText(/expired or was revoked/);
      expect(alert).toHaveAttribute('role', 'alert');
      expect(screen.queryByText(message)).not.toBeInTheDocument();
      expect(field()).toHaveAttribute('aria-invalid', 'true');
      expect(field()).toHaveFocus();
      expect(screen.queryByText(/You joined/)).not.toBeInTheDocument();
      expect(screen.queryByRole('link', { name: /^Go to / })).not.toBeInTheDocument();
    },
  );

  it('does not treat a 410 with another code as a dead invite', async () => {
    stubJoin({ groups: [group('g1', 'Crew', 'owner')], players: ROSTERS() }, () =>
      failure(410, 'something_else', 'A different problem.'),
    );
    const user = userEvent.setup();
    mount('/playgroups/join');
    await paste(user, CODE);
    await user.click(joinButton());
    expect(await screen.findByText(/A different problem\./)).toBeInTheDocument();
    expect(screen.queryByText(/expired or was revoked/)).not.toBeInTheDocument();
  });

  it('can retry with a different code after a dead invite', async () => {
    let first = true;
    const stub = stubJoin({ groups: [group('g1', 'Crew', 'owner')], players: ROSTERS() }, () => {
      if (!first) return undefined;
      first = false;
      return failure(410, 'invite_no_longer_valid', 'gone');
    });
    const user = userEvent.setup();
    mount('/playgroups/join');
    await paste(user, 'deadcode1');
    await user.click(joinButton());
    await screen.findByText(/expired or was revoked/);
    await user.clear(field());
    await paste(user, `mtgstats://invite/${CODE}`);
    await user.click(joinButton());
    await screen.findByText('You joined Joined Crew.');
    expect(stub.joins.map((h) => (JSON.parse(h.body) as { token: string }).token)).toEqual([
      'deadcode1',
      CODE,
    ]);
    expect(screen.queryByText(/expired or was revoked/)).not.toBeInTheDocument();
  });

  it.each([
    ['nothing', ''],
    ['only spaces', '   \n '],
    ['two words', 'two words'],
    ['a link to another page', 'https://mtgstats.example/reset/abc123'],
    ['a link with no code', 'https://mtgstats.example/invite/'],
    ['an app link with no code', 'mtgstats://invite/'],
    ['a path with extra segments', 'https://mtgstats.example/invite/abc/def'],
    ['a slash inside', 'abc/def'],
    ['a broken percent escape', '%E0%A4%A'],
  ])(
    'does not send %s to the API, and says what is wrong next to the field',
    async (_label, pasted) => {
      const stub = stubJoin({ groups: [group('g1', 'Crew', 'owner')], players: ROSTERS() });
      const user = userEvent.setup();
      mount('/playgroups/join');
      await heading2('Join a playgroup');
      await paste(user, pasted);
      await user.click(joinButton());
      const message = await screen.findByText(/invite link or code/i, {
        selector: '[role="alert"]',
      });
      expect(message).toBeInTheDocument();
      expect(field()).toHaveAttribute('aria-invalid', 'true');
      expect(field()).toHaveFocus();
      expect(stub.joins).toEqual([]);
      expect(screen.queryByText(/You joined/)).not.toBeInTheDocument();
    },
  );

  it('sends a single request when the button is pressed twice while the first is in flight', async () => {
    const gate = deferred();
    const stub = stubJoin({ groups: [group('g1', 'Crew', 'owner')], players: ROSTERS() }, () =>
      gate.promise.then(() => undefined),
    );
    // The rule above only delays; once the gate opens stubJoin answers with the success.
    const user = userEvent.setup();
    mount('/playgroups/join');
    await paste(user, CODE);
    await user.click(joinButton());
    await user.click(screen.getByRole('button', { name: /Joining/ }));
    await user.keyboard('{Enter}');
    expect(stub.joins).toHaveLength(1);
    gate.release();
    await screen.findByText('You joined Joined Crew.');
    expect(stub.joins).toHaveLength(1);
  });

  it('shows a network failure as an error, keeps what was typed, and allows another try', async () => {
    let down = true;
    const stub = stubJoin({ groups: [group('g1', 'Crew', 'owner')], players: ROSTERS() }, () =>
      down ? Promise.reject(new TypeError('Failed to fetch')) : undefined,
    );
    const user = userEvent.setup();
    mount('/playgroups/join');
    await paste(user, CODE);
    await user.click(joinButton());
    const alert = await screen.findByText(/reach the server/i);
    expect(alert).toHaveAttribute('role', 'alert');
    expect(field()).toHaveValue(CODE);
    down = false;
    await user.click(joinButton());
    await screen.findByText('You joined Joined Crew.');
    expect(stub.joins).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------------------------
// A press outside closes the switcher, even when the trigger never had focus
// ---------------------------------------------------------------------------------------------

/**
 * Safari and iOS do not focus a button on click, so no blur ever follows a press elsewhere. jsdom
 * cannot reproduce that natively (and user-event focuses on mousedown), so this simulates it:
 * the trigger's focus() is a no-op, and the press is dispatched with fireEvent (which never moves
 * focus). The list is opened by the events a click produces, and document.activeElement stays on
 * <body> throughout.
 */
async function openWithoutFocus() {
  stubWorld(threeGroups());
  mount('/playgroups/g1');
  await heading2('Crew');
  const button = await screen.findByRole('combobox');
  button.focus = () => undefined;
  fireEvent.pointerDown(button);
  fireEvent.mouseDown(button);
  fireEvent.pointerUp(button);
  fireEvent.mouseUp(button);
  fireEvent.click(button);
  expect(button).toHaveAttribute('aria-expanded', 'true');
  expect(document.activeElement).toBe(document.body);
  return button;
}

describe('a pointer press outside the open switcher', () => {
  it.each([
    ['the page heading', () => screen.getByRole('heading', { level: 2, name: 'Crew' })],
    ['the page body', () => document.body],
    ['the navigation', () => screen.getByRole('navigation', { name: 'Primary' })],
  ])(
    'closes it without selecting when pressed on %s, with the trigger never focused',
    async (_n, target) => {
      localStorage.setItem(
        ACTIVE_PLAYGROUP_ITEM_NAME,
        JSON.stringify({ state: { activeId: 'g1' }, version: 1 }),
      );
      const button = await openWithoutFocus();
      expect(screen.getAllByRole('option')).toHaveLength(3);
      fireEvent.pointerDown(target());
      expect(button).toHaveAttribute('aria-expanded', 'false');
      expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
      expect(button).toHaveAccessibleName(/Crew/);
      expect(screen.getByRole('heading', { level: 2, name: 'Crew' })).toBeInTheDocument();
      expect(rememberedId()).toBe('g1');
      expect(document.activeElement).toBe(document.body);
    },
  );

  it('keeps the list open for a press inside it, and a press on an option still selects', async () => {
    const button = await openWithoutFocus();
    const brew = screen.getAllByRole('option')[1] as HTMLElement;
    fireEvent.pointerDown(screen.getByRole('listbox'));
    expect(button).toHaveAttribute('aria-expanded', 'true');
    fireEvent.pointerDown(brew);
    fireEvent.mouseDown(brew);
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    fireEvent.pointerUp(brew);
    fireEvent.mouseUp(brew);
    fireEvent.click(brew);
    expect(await heading2('Brew')).toBeInTheDocument();
    await screen.findByText('Bianca');
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(rememberedId()).toBe('g2');
  });

  it('does nothing while the list is closed, and a second press on the trigger closes it', async () => {
    stubWorld(threeGroups());
    mount('/playgroups/g1');
    await heading2('Crew');
    const button = await screen.findByRole('combobox');
    fireEvent.pointerDown(document.body);
    expect(button).toHaveAttribute('aria-expanded', 'false');
    button.focus = () => undefined;
    fireEvent.pointerDown(button);
    fireEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'true');
    fireEvent.pointerDown(button);
    fireEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes with a real pointer press elsewhere too (user-event, which does focus)', async () => {
    stubWorld(threeGroups());
    const user = userEvent.setup();
    mount('/playgroups/g1');
    await heading2('Crew');
    await user.click(await screen.findByRole('combobox'));
    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    await user.click(screen.getByRole('heading', { level: 2, name: 'Crew' }));
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('heading', { level: 2, name: 'Crew' })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------------------------
// Join: the last /invite/<token> segment of any pasted URL, and the pasted URL is never opened
// ---------------------------------------------------------------------------------------------

describe('the join screen with pasted URLs of any shape', () => {
  it.each([
    ['a host with a path prefix', `https://pasted.example/app/prefix/invite/${CODE}`],
    ['a path prefix and a trailing slash', `https://pasted.example/app/invite/${CODE}/`],
    ['a query string after the code', `https://pasted.example/invite/${CODE}?utm_source=mail&x=1`],
    ['a fragment after the code', `https://pasted.example/invite/${CODE}#welcome`],
    ['a query string and a fragment', `https://pasted.example/invite/${CODE}?a=b#frag`],
    [
      'a prefix, query string and fragment',
      `  https://pasted.example/p/invite/${CODE}/?a=b#frag  `,
    ],
    [
      'two /invite/ segments (the last one wins)',
      `https://pasted.example/invite/first/invite/${CODE}`,
    ],
    ['an app link with a query string', `mtgstats://invite/${CODE}?ref=1`],
  ])('sends only the code for %s, and never opens the pasted URL', async (_label, pasted) => {
    const stub = stubJoin({ groups: [group('g1', 'Crew', 'owner')], players: ROSTERS() });
    const seen: Hit[] = [];
    intercept((hit) => {
      seen.push(hit);
      return undefined;
    });
    const user = userEvent.setup();
    const { router } = mount('/playgroups/join');
    await heading2('Join a playgroup');
    const locationBefore = window.location.href;
    await paste(user, pasted);
    await user.click(joinButton());
    await screen.findByText('You joined Joined Crew.');
    expect(stub.joins).toHaveLength(1);
    expect(JSON.parse(stub.joins[0]?.body ?? 'null')).toEqual({ token: CODE });
    expect(stub.joins[0]?.search).toBe('');
    // Nothing was fetched from, or navigated to, the pasted host.
    expect(seen.length).toBeGreaterThan(0);
    for (const hit of seen) {
      expect(hit.origin, `${hit.method} ${hit.path}`).not.toBe('https://pasted.example');
      expect(hit.path).toMatch(/^\/api\/v1\//);
    }
    expect(new Set(seen.map((h) => h.origin)).size).toBe(1);
    expect(seen.filter((h) => h.method === 'POST')).toHaveLength(1);
    expect(router.state.location.pathname).toBe('/playgroups/join');
    expect(window.location.href).toBe(locationBefore);
  });

  it.each([
    [
      'a link that only has a query string after /invite/',
      'https://pasted.example/invite/?code=abc',
    ],
    ['/invite/ in a query string only', 'https://pasted.example/reset?next=/invite/abc'],
    ['the word invite with no code', 'https://pasted.example/invite'],
  ])('still sends nothing for %s', async (_label, pasted) => {
    const stub = stubJoin({ groups: [group('g1', 'Crew', 'owner')], players: ROSTERS() });
    const user = userEvent.setup();
    mount('/playgroups/join');
    await heading2('Join a playgroup');
    await paste(user, pasted);
    await user.click(joinButton());
    await screen.findByText(/invite link or code/i, { selector: '[role="alert"]' });
    expect(stub.joins).toEqual([]);
  });
});
