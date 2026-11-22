import type { PlaygroupRole } from '@mtg-stats/api-client';

/**
 * What a member may do in a playgroup, by role (S-2.4.5). Every screen that shows a management
 * action asks `can(role, action)`: a denied action is not rendered at all. The server stays the
 * authority; this only avoids showing buttons that would answer 403.
 *
 * Where each rule comes from (apps/api/app/playgroups/router.py):
 * - `playgroup.edit`: PATCH /playgroups/{id}, owner or admin.
 * - `houseRules.edit`: PUT /playgroups/{id}/house-rules, owner or admin (GET is any member).
 * - `invites.manage`: create, list and revoke a playgroup's invites, owner or admin.
 * - `guests.add`: POST /playgroups/{id}/guests, any member.
 * - `guests.rename`: PATCH /playgroups/{id}/guests/{player_id}: owner, admin or the guest's
 *   creator. The creator case needs the guest's `created_by`, so a screen adds it on top of this
 *   role-only rule.
 * - `members.changeRole`: PATCH /playgroups/{id}/members/{player_id}, owner only.
 * - `ownership.transfer`: POST /playgroups/{id}/transfer-ownership, owner only.
 *
 * Not modelled yet because the API has no route: removing a member, leaving, deleting the
 * playgroup (S-2.4.3, S-2.6.x). Add their rows here when those routes land.
 */
export const PLAYGROUP_ACTIONS = [
  'playgroup.edit',
  'houseRules.edit',
  'invites.manage',
  'guests.add',
  'guests.rename',
  'members.changeRole',
  'ownership.transfer',
] as const;

export type PlaygroupAction = (typeof PLAYGROUP_ACTIONS)[number];

const MEMBER_UP: readonly PlaygroupRole[] = ['owner', 'admin', 'member'];
const ADMIN_UP: readonly PlaygroupRole[] = ['owner', 'admin'];
const OWNER_ONLY: readonly PlaygroupRole[] = ['owner'];

export const ACTION_ROLES: Readonly<Record<PlaygroupAction, readonly PlaygroupRole[]>> = {
  'playgroup.edit': ADMIN_UP,
  'houseRules.edit': ADMIN_UP,
  'invites.manage': ADMIN_UP,
  'guests.add': MEMBER_UP,
  'guests.rename': ADMIN_UP,
  'members.changeRole': OWNER_ONLY,
  'ownership.transfer': OWNER_ONLY,
};

/** True when `role` (null or undefined: not a member, or not loaded yet) may do `action`. */
export function can(role: PlaygroupRole | null | undefined, action: PlaygroupAction): boolean {
  if (!role) return false;
  return ACTION_ROLES[action].includes(role);
}
