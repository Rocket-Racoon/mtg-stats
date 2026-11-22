// S-2.4.5: the role rules behind the playgroup home. Table-driven: one row per action.
import { describe, expect, it } from 'vitest';
import { ACTION_ROLES, PLAYGROUP_ACTIONS, can, type PlaygroupAction } from './permissions';

type Row = [PlaygroupAction, { owner: boolean; admin: boolean; member: boolean }];

const TABLE: Row[] = [
  ['playgroup.edit', { owner: true, admin: true, member: false }],
  ['houseRules.edit', { owner: true, admin: true, member: false }],
  ['invites.manage', { owner: true, admin: true, member: false }],
  ['guests.add', { owner: true, admin: true, member: true }],
  ['guests.rename', { owner: true, admin: true, member: false }],
  ['members.changeRole', { owner: true, admin: false, member: false }],
  ['ownership.transfer', { owner: true, admin: false, member: false }],
];

describe('can(role, action)', () => {
  it.each(TABLE)('%s', (action, expected) => {
    expect(can('owner', action)).toBe(expected.owner);
    expect(can('admin', action)).toBe(expected.admin);
    expect(can('member', action)).toBe(expected.member);
  });

  it('covers every action exactly once', () => {
    expect(TABLE.map(([action]) => action).sort()).toEqual([...PLAYGROUP_ACTIONS].sort());
    expect(Object.keys(ACTION_ROLES).sort()).toEqual([...PLAYGROUP_ACTIONS].sort());
  });

  it('denies everything without a role (not a member, or not loaded yet)', () => {
    for (const action of PLAYGROUP_ACTIONS) {
      expect(can(null, action)).toBe(false);
      expect(can(undefined, action)).toBe(false);
    }
  });

  it('never gives an admin something a plain member has not, or the owner something an admin has not', () => {
    for (const action of PLAYGROUP_ACTIONS) {
      if (can('member', action)) expect(can('admin', action)).toBe(true);
      if (can('admin', action)) expect(can('owner', action)).toBe(true);
    }
  });
});
