// S-2.4.5 review follow-ups: outside press closes the switcher, the highlight is clamped when the
// list shrinks, a failed membership check is an error (not "left"), a failed refetch keeps data.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';
import { failure, group, mountApp, person, stubWorld } from '../test/playgroups-api';
import { useActivePlaygroupStore } from './active-store';
import { PlaygroupListbox } from './PlaygroupSwitcher';

const GROUPS = [group('a', 'Alpha', 'owner'), group('b', 'Bravo', 'admin'), group('c', 'Cargo')];
const PLAYERS = { a: [person('p1', 'Pat', 'owner')], b: [], c: [] };

describe('press outside the switcher', () => {
  it('closes it without selecting, even when a click never focuses the button', async () => {
    stubWorld({ groups: GROUPS, players: PLAYERS });
    mountApp('/games');
    const box = await screen.findByRole('combobox');
    // Safari and Firefox on macOS: a click opens it but focus stays elsewhere.
    fireEvent.click(box);
    expect(box).toHaveAttribute('aria-expanded', 'true');
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.pointerDown(document.body);
    expect(box).toHaveAttribute('aria-expanded', 'false');
    expect(box).toHaveTextContent('Alpha');
    expect(useActivePlaygroupStore.getState().activeId).toBe('a');
  });

  it('does not close it for a press inside the switcher', async () => {
    stubWorld({ groups: GROUPS, players: PLAYERS });
    mountApp('/games');
    const box = await screen.findByRole('combobox');
    fireEvent.click(box);
    fireEvent.pointerDown(screen.getByRole('listbox'));
    expect(box).toHaveAttribute('aria-expanded', 'true');
  });
});

describe('highlight when the list shrinks', () => {
  it('never points aria-activedescendant at a missing option', async () => {
    const three = GROUPS.map(({ id, name }) => ({ id, name }));
    const user = userEvent.setup();
    const { rerender } = render(
      <PlaygroupListbox playgroups={three} activeId="a" onSelect={() => undefined} />,
    );
    const box = screen.getByRole('combobox');
    box.focus();
    await user.keyboard('{End}');
    expect(box).toHaveAttribute(
      'aria-activedescendant',
      screen.getByRole('option', { name: 'Cargo' }).id,
    );
    rerender(
      <PlaygroupListbox playgroups={three.slice(0, 1)} activeId="a" onSelect={() => undefined} />,
    );
    const id = box.getAttribute('aria-activedescendant');
    expect(id).not.toBeNull();
    expect(document.getElementById(id ?? '')).toBe(screen.getByRole('option', { name: 'Alpha' }));
  });
});

describe('a remembered playgroup that is not on the loaded pages', () => {
  it('stays remembered, and the switcher reports an error, when the check fails with anything but 404', async () => {
    stubWorld({
      groups: GROUPS,
      players: PLAYERS,
      pageSize: 1,
      handler: (call) =>
        call.path === '/api/v1/playgroups/c' ? failure(403, 'forbidden', 'Nope.') : undefined,
    });
    useActivePlaygroupStore.setState({ activeId: 'c' });
    mountApp('/games');
    expect(await screen.findByText('Could not load your playgroups.')).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(useActivePlaygroupStore.getState().activeId).toBe('c');
  });

  it('falls back to the first group and overwrites it on a 404 not_found', async () => {
    stubWorld({ groups: GROUPS, players: PLAYERS, pageSize: 1 });
    useActivePlaygroupStore.setState({ activeId: 'gone' });
    mountApp('/games');
    expect(await screen.findByRole('combobox')).toHaveTextContent('Alpha');
    await waitFor(() => {
      expect(useActivePlaygroupStore.getState().activeId).toBe('a');
    });
  });
});

describe('a failed background refetch', () => {
  it('keeps the playgroup home and the switcher on screen', async () => {
    let fail = false;
    stubWorld({
      groups: GROUPS,
      players: { a: [person('p1', 'Pat', 'owner')], b: [], c: [] },
      handler: (call) =>
        fail && call.path.startsWith('/api/v1/playgroups')
          ? failure(403, 'forbidden', 'Nope.')
          : undefined,
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mountApp('/playgroups/a', queryClient);
    await screen.findByRole('heading', { level: 2, name: 'Alpha' });
    await screen.findByText('Pat');
    fail = true;
    await queryClient.refetchQueries();
    expect(screen.getByRole('heading', { level: 2, name: 'Alpha' })).toBeInTheDocument();
    expect(screen.getByText('Pat')).toBeInTheDocument();
    expect(screen.getByRole('combobox')).toHaveTextContent('Alpha');
    expect(screen.queryByText('Could not load your playgroups.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });
});

describe('retry after a failed membership check', () => {
  it('re-runs the check, so the remembered group becomes active once the server recovers', async () => {
    let broken = true;
    stubWorld({
      groups: GROUPS,
      players: PLAYERS,
      pageSize: 1,
      handler: (call) =>
        broken && call.path === '/api/v1/playgroups/c'
          ? failure(403, 'forbidden', 'Nope.')
          : undefined,
    });
    useActivePlaygroupStore.setState({ activeId: 'c' });
    const user = userEvent.setup();
    mountApp('/games');
    await screen.findByText('Could not load your playgroups.');
    broken = false;
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('combobox')).toHaveTextContent('Cargo');
    expect(useActivePlaygroupStore.getState().activeId).toBe('c');
  });
});

describe('a failed refetch of the roster', () => {
  it('keeps the people on screen', async () => {
    let fail = false;
    stubWorld({
      groups: GROUPS,
      players: PLAYERS,
      handler: (call) =>
        fail && call.path.endsWith('/players') ? failure(403, 'forbidden') : undefined,
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mountApp('/playgroups/a', queryClient);
    await screen.findByText('Pat');
    fail = true;
    await queryClient.refetchQueries();
    expect(screen.getByText('Pat')).toBeInTheDocument();
    expect(screen.queryByText('Loading people…')).not.toBeInTheDocument();
  });
});
