import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router';
import { NAV_ITEMS } from './nav';
import './App.css';

/**
 * Root layout. Navigation is a sidebar at >= 1024px, a top bar of links
 * between 768 and 1023px, and a toggle menu below 768px (see App.css).
 */
export function App() {
  // The menu is open only for the path it was opened on, so navigating closes it.
  const [openAt, setOpenAt] = useState<string | null>(null);
  const { pathname } = useLocation();
  const open = openAt === pathname;
  const toggleRef = useRef<HTMLButtonElement>(null);
  const navRef = useRef<HTMLElement>(null);

  // Move focus into the menu when it opens.
  useEffect(() => {
    if (open) navRef.current?.querySelector<HTMLElement>('a')?.focus();
  }, [open]);

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === 'Escape' && open) {
      setOpenAt(null);
      toggleRef.current?.focus();
    }
  }

  return (
    <div className="shell" onKeyDown={onKeyDown}>
      <header className="shell-header">
        <h1>MTG Stats</h1>
        <button
          ref={toggleRef}
          type="button"
          className="menu-toggle"
          aria-expanded={open}
          aria-controls="primary-nav"
          onClick={() => {
            setOpenAt(open ? null : pathname);
          }}
        >
          {open ? 'Close menu' : 'Menu'}
        </button>
      </header>
      <nav
        id="primary-nav"
        ref={navRef}
        className="shell-nav"
        aria-label="Primary"
        data-open={open}
      >
        <ul>
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink to={item.to} end={item.end}>
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <main className="shell-main">
        <Outlet />
      </main>
      <footer className="shell-footer">
        <small>Footer placeholder</small>
      </footer>
    </div>
  );
}
