// S-2.4.5: the landing page (/), the playgroup home (/playgroups/:id) and "I have an invite"
// (/playgroups/join), through the real app with a stubbed API.
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { useActivePlaygroupStore } from '../playgroups/active-store';
import {
  failure,
  group,
  json,
  mountApp,
  person,
  stubWorld,
  type Role,
  type World,
} from '../test/playgroups-api';
import { extractInviteToken } from './JoinWithInvitePage';

beforeEach(() => {
  localStorage.clear();
});

const crew = (role: Role) => [group('g1', 'Crew', role), group('g2', 'Other', 'owner')];
const ROSTER = [
  person('p1', 'Zed', 'member'),
  person('p2', 'Amy', 'admin'),
  person('p3', 'Olive', 'owner'),
  person('p4', 'Bob', 'member'),
  person('p5', 'Gus', null),
];

function world(role: Role, over: Partial<World> = {}): World {
  return {
    groups: crew(role),
    players: { g1: ROSTER, g2: [person('p9', 'Solo', 'owner')] },
    ...over,
  };
}

describe('landing page (/)', () => {
  it('shows the empty state with both entries when the user has no playgroups', async () => {
    stubWorld({ groups: [], players: {} });
    mountApp('/');
    expect(await screen.findByRole('link', { name: 'Create a playgroup' })).toHaveAttribute(
      'href',
      '/playgroups',
    );
    expect(screen.getByRole('link', { name: 'I have an invite' })).toHaveAttribute(
      'href',
      '/playgroups/join',
    );
    expect(screen.getByRole('heading', { level: 2, name: 'Home' })).toBeInTheDocument();
  });

  it('redirects to the home of the first playgroup when nothing is remembered', async () => {
    stubWorld(world('owner'));
    const router = mountApp('/');
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/playgroups/g1');
    });
    expect(await screen.findByRole('heading', { level: 2, name: 'Crew' })).toBeInTheDocument();
  });

  it('redirects to the remembered playgroup', async () => {
    stubWorld(world('owner'));
    useActivePlaygroupStore.setState({ activeId: 'g2' });
    const router = mountApp('/');
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/playgroups/g2');
    });
  });

  it('shows an error with Retry when the list cannot be loaded, and recovers', async () => {
    let fail = true;
    stubWorld({
      ...world('owner'),
      handler: (call) =>
        fail && call.path === '/api/v1/playgroups' ? failure(403, 'forbidden', 'Boom.') : undefined,
    });
    const user = userEvent.setup();
    const router = mountApp('/');
    expect(await screen.findByText(/Boom\./)).toHaveAttribute('role', 'alert');
    fail = false;
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/playgroups/g1');
    });
  });
});

describe('playgroup home', () => {
  it('shows the owner, then admins, then members with their roles, then guests', async () => {
    stubWorld(world('member'));
    mountApp('/playgroups/g1');
    expect(await screen.findByRole('heading', { level: 2, name: 'Crew' })).toBeInTheDocument();
    await screen.findByText('Olive');
    const headings = screen
      .getAllByRole('heading', { level: 4 })
      .map((h) => h.textContent)
      .filter((t) => ['Owner', 'Admins', 'Members', 'Guests'].includes(t));
    expect(headings).toEqual(['Owner', 'Admins', 'Members', 'Guests']);
    const names = (title: string) =>
      within(screen.getByRole('region', { name: title }))
        .getAllByRole('listitem')
        .map((li) => li.textContent);
    expect(names('Owner')).toEqual(['OOlive (Owner)']);
    expect(names('Admins')).toEqual(['AAmy (Admin)']);
    // The API order is kept inside a group.
    expect(names('Members')).toEqual(['ZZed (Member)', 'BBob (Member)']);
    expect(names('Guests')).toEqual(['GGus (Guest)']);
  });

  it('follows next_cursor until the end, never truncating the roster', async () => {
    const many = Array.from({ length: 7 }, (_, i) =>
      person(`m${String(i)}`, `Member ${String(i)}`, 'member'),
    );
    const calls = stubWorld(
      world('owner', {
        players: { g1: [...many, person('gg', 'Guest', null)] },
        playersPageSize: 3,
      }),
    );
    mountApp('/playgroups/g1');
    await screen.findByText('Guest');
    const members = within(screen.getByRole('region', { name: 'Members' })).getAllByRole(
      'listitem',
    );
    expect(members).toHaveLength(7);
    const cursors = calls
      .filter((c) => c.path === '/api/v1/playgroups/g1/players')
      .map((c) => c.query.get('cursor'));
    expect(cursors).toEqual([null, '3', '6']);
  });

  it('shows no avatar colour as the only role cue: roles are text', async () => {
    stubWorld(world('member'));
    mountApp('/playgroups/g1');
    await screen.findByText('Olive');
    expect(screen.getByText('(Owner)')).toBeInTheDocument();
    expect(screen.getByText('Your role:').parentElement).toHaveTextContent('Your role: Member');
  });

  it('makes a playgroup reached by a deep link the active one', async () => {
    stubWorld(world('owner'));
    useActivePlaygroupStore.setState({ activeId: 'g1' });
    mountApp('/playgroups/g2');
    await screen.findByRole('heading', { level: 2, name: 'Other' });
    await waitFor(() => {
      expect(useActivePlaygroupStore.getState().activeId).toBe('g2');
    });
  });

  it('shows the not-found page for a playgroup the user is not in, and no data', async () => {
    stubWorld(world('owner'));
    mountApp('/playgroups/not-mine');
    expect(
      await screen.findByRole('heading', { level: 2, name: 'Page not found' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('Olive')).not.toBeInTheDocument();
    expect(useActivePlaygroupStore.getState().activeId).not.toBe('not-mine');
  });

  it('shows an error with the request id and a Retry for other failures', async () => {
    stubWorld({
      ...world('owner'),
      handler: (call) =>
        call.path === '/api/v1/playgroups/g1' ? failure(403, 'forbidden', 'Boom.') : undefined,
    });
    mountApp('/playgroups/g1');
    const alert = await screen.findByText(/Boom\./, { selector: '[role="alert"]' });
    expect(alert).toHaveTextContent('(request req-9)');
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('shows an error for the people list on its own, with Retry', async () => {
    stubWorld({
      ...world('owner'),
      handler: (call) =>
        call.path === '/api/v1/playgroups/g1/players'
          ? failure(403, 'forbidden', 'No people.')
          : undefined,
    });
    mountApp('/playgroups/g1');
    expect(await screen.findByRole('heading', { level: 2, name: 'Crew' })).toBeInTheDocument();
    const people = screen.getByRole('region', { name: 'People' });
    expect(await within(people).findByRole('alert')).toHaveTextContent('No people.');
  });
});

describe('role-based actions (S-2.4.5 AC3)', () => {
  const SLOTS: [string, string][] = [
    ['invites.manage', 'Invite people'],
    ['playgroup.edit', 'Name and description'],
    ['houseRules.edit', 'House rules'],
    ['guests.add', 'Manage guests'],
    ['members.changeRole', 'Admins and members'],
    ['ownership.transfer', 'Transfer ownership'],
  ];
  const shown = () =>
    [...document.querySelectorAll('[data-slot]')].map((el) => el.getAttribute('data-slot'));

  it.each([
    ['owner', SLOTS.map(([id]) => id)],
    ['admin', ['invites.manage', 'playgroup.edit', 'houseRules.edit', 'guests.add']],
    ['member', ['guests.add']],
  ] as [Role, string[]][])(
    'renders for a %s only the slots the role allows',
    async (role, expected) => {
      stubWorld(world(role));
      mountApp('/playgroups/g1');
      await screen.findByRole('heading', { level: 2, name: 'Crew' });
      expect(shown()).toEqual(SLOTS.map(([id]) => id).filter((id) => expected.includes(id)));
      for (const [id, title] of SLOTS) {
        const present = screen.queryByRole('region', { name: title }) !== null;
        expect(present, `${title} for ${role}`).toBe(expected.includes(id));
      }
    },
  );
});

describe('"I have an invite" (/playgroups/join)', () => {
  it.each([
    ['https://mtg.example/invite/abc123', 'abc123'],
    ['  https://mtg.example/invite/abc123/  ', 'abc123'],
    ['https://mtg.example/invite/abc%2Fdef', 'abc/def'],
    ['https://mtg.example/invite/abc123?utm=x#frag', 'abc123'],
    ['mtgstats://invite/abc123', 'abc123'],
    ['/invite/abc123', 'abc123'],
    ['abc123', 'abc123'],
    ['  abc-123_XYZ  ', 'abc-123_XYZ'],
  ])('extracts the token from %j', (raw, token) => {
    expect(extractInviteToken(raw)).toBe(token);
  });

  it.each([[''], ['   '], ['two words'], ['https://mtg.example/reset/abc'], ['a/b'], ['%E0%A4%A']])(
    'finds no token in %j',
    (raw) => {
      expect(extractInviteToken(raw)).toBeNull();
    },
  );

  it('joins with the pasted link, makes the playgroup active and links to it', async () => {
    let body: unknown = null;
    stubWorld({
      ...world('owner'),
      handler: (call) => {
        if (call.path === '/api/v1/invites/join') {
          body = call.body;
          return json({ already_member: false, playgroup: group('g2', 'Other', 'member') });
        }
        return undefined;
      },
    });
    const user = userEvent.setup();
    mountApp('/playgroups/join');
    await user.type(
      screen.getByRole('textbox', { name: 'Invite link or code' }),
      'https://mtg.example/invite/tok-1',
    );
    await user.click(screen.getByRole('button', { name: 'Join playgroup' }));
    expect(await screen.findByText('You joined Other.')).toBeInTheDocument();
    expect(body).toEqual({ token: 'tok-1' });
    expect(useActivePlaygroupStore.getState().activeId).toBe('g2');
    expect(screen.getByRole('link', { name: 'Go to Other' })).toHaveAttribute(
      'href',
      '/playgroups/g2',
    );
  });

  it('says so when the user is already a member', async () => {
    stubWorld({
      ...world('owner'),
      handler: (call) =>
        call.path === '/api/v1/invites/join'
          ? json({ already_member: true, playgroup: group('g1', 'Crew', 'owner') })
          : undefined,
    });
    const user = userEvent.setup();
    mountApp('/playgroups/join');
    await user.type(screen.getByRole('textbox', { name: 'Invite link or code' }), 'tok');
    await user.click(screen.getByRole('button', { name: 'Join playgroup' }));
    expect(await screen.findByText('You are already in Crew.')).toBeInTheDocument();
  });

  it('branches on the error code for a dead invite, next to the field', async () => {
    stubWorld({
      ...world('owner'),
      handler: (call) =>
        call.path === '/api/v1/invites/join'
          ? failure(410, 'invite_no_longer_valid', 'text that is never shown')
          : undefined,
    });
    const user = userEvent.setup();
    mountApp('/playgroups/join');
    const field = screen.getByRole('textbox', { name: 'Invite link or code' });
    await user.type(field, 'tok');
    await user.click(screen.getByRole('button', { name: 'Join playgroup' }));
    expect(await screen.findByText(/expired or was revoked/)).toHaveAttribute('role', 'alert');
    expect(screen.queryByText('text that is never shown')).not.toBeInTheDocument();
    expect(field).toHaveFocus();
    expect(field).toHaveAttribute('aria-invalid', 'true');
  });

  it('asks for something to be pasted without calling the API', async () => {
    const calls = stubWorld(world('owner'));
    const user = userEvent.setup();
    mountApp('/playgroups/join');
    await user.click(screen.getByRole('button', { name: 'Join playgroup' }));
    expect(await screen.findByText(/Paste the invite link or code/)).toHaveAttribute(
      'role',
      'alert',
    );
    expect(calls.filter((c) => c.path === '/api/v1/invites/join')).toHaveLength(0);
  });
});
