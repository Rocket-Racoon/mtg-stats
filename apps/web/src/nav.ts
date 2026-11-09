export interface NavItem {
  to: string;
  label: string;
  end?: boolean;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: '/', label: 'Home', end: true },
  { to: '/games', label: 'Games' },
  { to: '/decks', label: 'Decks' },
  { to: '/stats', label: 'Stats' },
  { to: '/collection', label: 'Collection' },
  { to: '/leagues', label: 'Leagues' },
];
