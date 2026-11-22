// S-2.4.5: the playgroup home is the app's Home. "Home" is current on / and on a playgroup's
// home; "Playgroups" is current on the list/create page and on the join page, not on a home.
import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { isNavCurrent, NAV_ITEMS } from './nav';
import { group, mountApp, person, stubWorld } from './test/playgroups-api';

const current = () =>
  within(screen.getByRole('navigation', { name: 'Primary' }))
    .getAllByRole('link')
    .filter((a) => a.getAttribute('aria-current') === 'page')
    .map((a) => a.textContent);

describe('nav current page', () => {
  it.each([
    ['/', ['Home']],
    ['/playgroups/g1', ['Home']],
    ['/playgroups', ['Playgroups']],
    ['/playgroups/join', ['Playgroups']],
    ['/games', ['Games']],
  ])('marks %s as current: %j', async (path, expected) => {
    stubWorld({
      groups: [group('g1', 'Crew', 'owner')],
      players: { g1: [person('p1', 'Pat', 'owner')] },
    });
    mountApp(path);
    await screen.findByRole('main');
    expect(current()).toEqual(expected);
  });

  it('keeps the default rule for the other items (the path or below it)', () => {
    const games = NAV_ITEMS.find((i) => i.label === 'Games');
    expect(games && isNavCurrent(games, '/games')).toBe(true);
    expect(games && isNavCurrent(games, '/games/42')).toBe(true);
    expect(games && isNavCurrent(games, '/gamesx')).toBe(false);
  });
});
