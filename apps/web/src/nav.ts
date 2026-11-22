export interface NavItem {
  to: string;
  label: string;
  end?: boolean;
  /** Overrides the default "current page" rule (the link's path, or below it unless `end`). */
  current?: (pathname: string) => boolean;
}

const PLAYGROUP_HOME = /^\/playgroups\/([^/]+)\/?$/;

/** A playgroup's own home (`/playgroups/:id`), not the invite entry `/playgroups/join`. */
function isPlaygroupHome(pathname: string): boolean {
  const match = PLAYGROUP_HOME.exec(pathname);
  return match !== null && match[1] !== 'join';
}

/**
 * Whether the nav item is the current page. The playgroup home is the app's Home, so "Home" is
 * current on `/` and on a playgroup's home; "Playgroups" (the list and create page) is current on
 * `/playgroups` and `/playgroups/join`, and not on a group's home.
 */
export function isNavCurrent(item: NavItem, pathname: string): boolean {
  if (item.current) return item.current(pathname);
  if (item.end) return pathname === item.to;
  return pathname === item.to || pathname.startsWith(`${item.to}/`);
}

export const NAV_ITEMS: readonly NavItem[] = [
  {
    to: '/',
    label: 'Home',
    end: true,
    current: (pathname) => pathname === '/' || isPlaygroupHome(pathname),
  },
  {
    to: '/playgroups',
    label: 'Playgroups',
    current: (pathname) => pathname === '/playgroups' || pathname === '/playgroups/join',
  },
  { to: '/games', label: 'Games' },
  { to: '/decks', label: 'Decks' },
  { to: '/stats', label: 'Stats' },
  { to: '/collection', label: 'Collection' },
  { to: '/leagues', label: 'Leagues' },
  { to: '/account', label: 'Account' },
];
