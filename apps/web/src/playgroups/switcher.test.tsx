// S-2.4.5: the playgroup switcher (select-only combobox) through the real app, with a stubbed API.
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { group, mountApp, person, stubWorld, type World } from '../test/playgroups-api';
import { ACTIVE_PLAYGROUP_ITEM_NAME, useActivePlaygroupStore } from './active-store';

const GROUPS = [group('a', 'Alpha', 'owner'), group('b', 'Bravo', 'admin'), group('c', 'Cargo')];

function world(over: Partial<World> = {}): World {
  return {
    groups: GROUPS,
    players: { a: [person('p1', 'Pat', 'owner')], b: [person('p2', 'Quinn', 'owner')], c: [] },
    ...over,
  };
}

const trigger = () => screen.findByRole('combobox', { name: /playgroup/i });

beforeEach(() => {
  localStorage.clear();
});

describe('the switcher shows the active playgroup', () => {
  it('lists the playgroups and marks the first one active when nothing is remembered', async () => {
    stubWorld(world());
    const user = userEvent.setup();
    mountApp('/games');
    const box = await trigger();
    expect(box).toHaveTextContent('Alpha');
    await user.click(box);
    const options = within(screen.getByRole('listbox')).getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual(['Alpha', 'Bravo', 'Cargo']);
    expect(options.map((o) => o.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);
  });

  it('uses the remembered playgroup', async () => {
    stubWorld(world());
    useActivePlaygroupStore.setState({ activeId: 'b' });
    mountApp('/games');
    expect(await trigger()).toHaveTextContent('Bravo');
  });

  it('still shows the name with exactly one playgroup', async () => {
    stubWorld(world({ groups: [GROUPS[2]!] }));
    mountApp('/games');
    expect(await trigger()).toHaveTextContent('Cargo');
  });

  it('shows no switcher for a user with no playgroups', async () => {
    stubWorld(world({ groups: [] }));
    mountApp('/games');
    await screen.findByRole('heading', { level: 2, name: 'Games' });
    await waitFor(() => {
      expect(screen.queryByText('Loading playgroups…')).not.toBeInTheDocument();
    });
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('falls back to the first playgroup and overwrites the stored id when it is gone', async () => {
    stubWorld(world());
    useActivePlaygroupStore.setState({ activeId: 'left-long-ago' });
    mountApp('/games');
    expect(await trigger()).toHaveTextContent('Alpha');
    await waitFor(() => {
      expect(useActivePlaygroupStore.getState().activeId).toBe('a');
    });
    expect(JSON.parse(localStorage.getItem(ACTIVE_PLAYGROUP_ITEM_NAME) ?? 'null')).toMatchObject({
      state: { activeId: 'a' },
    });
  });

  it('keeps a remembered playgroup that is on a later page of the list', async () => {
    const calls = stubWorld(world({ pageSize: 1 }));
    useActivePlaygroupStore.setState({ activeId: 'c' });
    mountApp('/games');
    expect(await trigger()).toHaveTextContent('Cargo');
    expect(useActivePlaygroupStore.getState().activeId).toBe('c');
    // Checked with one GET, not by downloading the rest of the list.
    expect(calls.filter((c) => c.path === '/api/v1/playgroups/c')).not.toHaveLength(0);
    expect(calls.filter((c) => c.path === '/api/v1/playgroups')).toHaveLength(1);
  });

  it('offers to load more playgroups when the list has more pages', async () => {
    stubWorld(world({ pageSize: 2 }));
    const user = userEvent.setup();
    mountApp('/games');
    await trigger();
    await user.click(screen.getByRole('button', { name: 'Load more playgroups' }));
    await user.click(screen.getByRole('combobox'));
    expect(within(screen.getByRole('listbox')).getAllByRole('option')).toHaveLength(3);
    expect(screen.queryByRole('button', { name: 'Load more playgroups' })).not.toBeInTheDocument();
  });

  it('links to the create flow', async () => {
    stubWorld(world());
    mountApp('/games');
    await trigger();
    expect(screen.getByRole('link', { name: 'Create a playgroup' })).toHaveAttribute(
      'href',
      '/playgroups',
    );
  });
});

describe('choosing a playgroup', () => {
  it('remembers it and shows it, without a page reload', async () => {
    stubWorld(world());
    const user = userEvent.setup();
    mountApp('/games');
    await user.click(await trigger());
    await user.click(screen.getByRole('option', { name: 'Bravo' }));
    expect(screen.getByRole('combobox')).toHaveTextContent('Bravo');
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-expanded', 'false');
    expect(useActivePlaygroupStore.getState().activeId).toBe('b');
    expect(JSON.parse(localStorage.getItem(ACTIVE_PLAYGROUP_ITEM_NAME) ?? 'null')).toMatchObject({
      state: { activeId: 'b' },
    });
  });

  it("moves a playgroup's own home to the chosen group, with that group's data only", async () => {
    stubWorld(world());
    const user = userEvent.setup();
    const router = mountApp('/playgroups/a');
    await screen.findByRole('heading', { level: 2, name: 'Alpha' });
    expect(await screen.findByText('Pat')).toBeInTheDocument();
    await user.click(await trigger());
    await user.click(screen.getByRole('option', { name: 'Bravo' }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/playgroups/b');
    });
    expect(await screen.findByRole('heading', { level: 2, name: 'Bravo' })).toBeInTheDocument();
    expect(await screen.findByText('Quinn')).toBeInTheDocument();
    expect(screen.queryByText('Pat')).not.toBeInTheDocument();
  });

  it('stays on the current screen when it is not a playgroup home', async () => {
    stubWorld(world());
    const user = userEvent.setup();
    const router = mountApp('/games');
    await user.click(await trigger());
    await user.click(screen.getByRole('option', { name: 'Cargo' }));
    expect(router.state.location.pathname).toBe('/games');
  });
});

describe('keyboard (S-2.4.5 AC4)', () => {
  it('is reachable by Tab and exposes combobox semantics', async () => {
    stubWorld(world());
    const user = userEvent.setup();
    mountApp('/games');
    const box = await trigger();
    expect(box).toHaveAttribute('aria-haspopup', 'listbox');
    expect(box).toHaveAttribute('aria-expanded', 'false');
    for (let i = 0; i < 40 && document.activeElement !== box; i += 1) await user.tab();
    expect(box).toHaveFocus();
  });

  it.each([['{Enter}'], [' '], ['{ArrowDown}'], ['{ArrowUp}']])(
    'opens with %j on the active option, keeping focus on the trigger',
    async (key) => {
      stubWorld(world());
      useActivePlaygroupStore.setState({ activeId: 'b' });
      const user = userEvent.setup();
      mountApp('/games');
      const box = await trigger();
      box.focus();
      await user.keyboard(key);
      expect(box).toHaveAttribute('aria-expanded', 'true');
      expect(box).toHaveFocus();
      expect(screen.getByRole('listbox')).toBeVisible();
      const bravo = screen.getByRole('option', { name: 'Bravo' });
      expect(box).toHaveAttribute('aria-activedescendant', bravo.id);
    },
  );

  it('moves with the arrows, stops at the ends, and jumps with Home and End', async () => {
    stubWorld(world());
    const user = userEvent.setup();
    mountApp('/games');
    const box = await trigger();
    box.focus();
    await user.keyboard('{ArrowDown}'); // opens on Alpha
    const id = (name: string) => screen.getByRole('option', { name }).id;
    await user.keyboard('{ArrowDown}');
    expect(box).toHaveAttribute('aria-activedescendant', id('Bravo'));
    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}');
    expect(box).toHaveAttribute('aria-activedescendant', id('Cargo'));
    await user.keyboard('{Home}');
    expect(box).toHaveAttribute('aria-activedescendant', id('Alpha'));
    await user.keyboard('{ArrowUp}');
    expect(box).toHaveAttribute('aria-activedescendant', id('Alpha'));
    await user.keyboard('{End}');
    expect(box).toHaveAttribute('aria-activedescendant', id('Cargo'));
  });

  it('opens on the last option with End while closed', async () => {
    stubWorld(world());
    const user = userEvent.setup();
    mountApp('/games');
    const box = await trigger();
    box.focus();
    await user.keyboard('{End}');
    expect(box).toHaveAttribute(
      'aria-activedescendant',
      screen.getByRole('option', { name: 'Cargo' }).id,
    );
  });

  it.each([['{Enter}'], [' ']])(
    'selects the highlighted option with %j and closes',
    async (key) => {
      stubWorld(world());
      const user = userEvent.setup();
      mountApp('/games');
      const box = await trigger();
      box.focus();
      await user.keyboard('{ArrowDown}{ArrowDown}');
      await user.keyboard(key);
      expect(box).toHaveAttribute('aria-expanded', 'false');
      expect(box).toHaveFocus();
      expect(box).toHaveTextContent('Bravo');
      expect(useActivePlaygroupStore.getState().activeId).toBe('b');
    },
  );

  it('closes on Escape without changing anything and returns focus to the trigger', async () => {
    stubWorld(world());
    const user = userEvent.setup();
    mountApp('/games');
    const box = await trigger();
    box.focus();
    await user.keyboard('{ArrowDown}{ArrowDown}{Escape}');
    expect(box).toHaveAttribute('aria-expanded', 'false');
    expect(box).toHaveFocus();
    expect(box).toHaveTextContent('Alpha');
  });

  it('closes on Tab without selecting', async () => {
    stubWorld(world());
    const user = userEvent.setup();
    mountApp('/games');
    const box = await trigger();
    box.focus();
    await user.keyboard('{ArrowDown}{ArrowDown}');
    await user.tab();
    expect(box).toHaveAttribute('aria-expanded', 'false');
    expect(box).toHaveTextContent('Alpha');
  });

  it('does not close the compact navigation when Escape closes the list', async () => {
    stubWorld(world());
    const user = userEvent.setup();
    mountApp('/games');
    const toggle = screen.getByRole('button', { name: 'Menu' });
    await user.click(toggle);
    const box = await trigger();
    box.focus();
    await user.keyboard('{ArrowDown}{Escape}');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
  });
});
