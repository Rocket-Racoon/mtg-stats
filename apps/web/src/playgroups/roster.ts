import type { PlaygroupPlayer } from '@mtg-stats/api-client';

export interface RosterGroups {
  owners: PlaygroupPlayer[];
  admins: PlaygroupPlayer[];
  members: PlaygroupPlayer[];
  guests: PlaygroupPlayer[];
}

/**
 * Splits the roster for display: owner, then admins, then members, then guests. The API lists
 * members before guests, each group alphabetical, and that order is kept inside every group. A
 * guest is whoever has `is_guest` (their `role` is null); a member row without a known role
 * lands with the members rather than disappearing. A person listed twice (the API documents that
 * a row renamed between pages can appear on two) is shown once.
 */
export function groupRoster(players: readonly PlaygroupPlayer[]): RosterGroups {
  const groups: RosterGroups = { owners: [], admins: [], members: [], guests: [] };
  // A row renamed between two pages can come back on both: the first occurrence wins.
  const seen = new Set<string>();
  for (const player of players) {
    if (seen.has(player.id)) continue;
    seen.add(player.id);
    if (player.is_guest) groups.guests.push(player);
    else if (player.role === 'owner') groups.owners.push(player);
    else if (player.role === 'admin') groups.admins.push(player);
    else groups.members.push(player);
  }
  return groups;
}

const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;

/**
 * Black or white, whichever reads better on the avatar colour (WCAG relative luminance), or null
 * when the value is not a `#rgb` or `#rrggbb` colour (the avatar then keeps the page colours).
 */
export function readableTextColor(hex: string): '#000000' | '#ffffff' | null {
  const match = HEX.exec(hex);
  const digits = match?.[1];
  if (!digits) return null;
  const full = digits.length === 3 ? [...digits].map((c) => c + c).join('') : digits;
  const [r, g, b] = [0, 2, 4].map((i) => {
    const channel = parseInt(full.slice(i, i + 2), 16) / 255;
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  // Black has the better contrast above this luminance (the point where both ratios are equal).
  return luminance > 0.179 ? '#000000' : '#ffffff';
}
