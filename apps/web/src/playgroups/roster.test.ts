import { describe, expect, it } from 'vitest';
import type { PlaygroupPlayer } from '@mtg-stats/api-client';
import { groupRoster, readableTextColor } from './roster';

const p = (id: string, role: PlaygroupPlayer['role']): PlaygroupPlayer => ({
  id,
  display_name: id,
  avatar_color: '#000000',
  is_guest: role === null,
  role,
  created_by: null,
});

describe('groupRoster', () => {
  it('splits owner, admins, members and guests and keeps the API order inside each group', () => {
    const groups = groupRoster([
      p('amy', 'member'),
      p('bob', 'admin'),
      p('cat', 'owner'),
      p('dan', 'admin'),
      p('eve', 'member'),
      p('fay', null),
      p('gus', null),
    ]);
    expect(groups.owners.map((x) => x.id)).toEqual(['cat']);
    expect(groups.admins.map((x) => x.id)).toEqual(['bob', 'dan']);
    expect(groups.members.map((x) => x.id)).toEqual(['amy', 'eve']);
    expect(groups.guests.map((x) => x.id)).toEqual(['fay', 'gus']);
  });

  it('treats is_guest as a guest whatever the role says, and unknown roles as members', () => {
    const odd = { ...p('x', 'admin'), is_guest: true };
    const noRole = { ...p('y', null), is_guest: false };
    const groups = groupRoster([odd, noRole]);
    expect(groups.guests.map((x) => x.id)).toEqual(['x']);
    expect(groups.members.map((x) => x.id)).toEqual(['y']);
  });
});

describe('groupRoster duplicates', () => {
  it('shows a person listed on two pages once, the first occurrence winning', () => {
    const first = { ...p('amy', 'member'), display_name: 'Amy (old)' };
    const second = { ...p('amy', 'member'), display_name: 'Amy (new)' };
    const groups = groupRoster([first, p('bob', 'member'), second]);
    expect(groups.members.map((x) => x.display_name)).toEqual(['Amy (old)', 'bob']);
  });
});

describe('readableTextColor', () => {
  it.each([
    ['#ffffff', '#000000'],
    ['#fff', '#000000'],
    ['#ffeb3b', '#000000'],
    ['#000000', '#ffffff'],
    ['#112233', '#ffffff'],
    ['#5646e0', '#ffffff'],
  ])('%s gets %s text', (background, text) => {
    expect(readableTextColor(background)).toBe(text);
  });

  it.each(['red', 'rgb(0,0,0)', '#12', '#12345', '#gggggg', 'url(x)', ''])(
    'returns null for %j',
    (value) => {
      expect(readableTextColor(value)).toBeNull();
    },
  );
});
