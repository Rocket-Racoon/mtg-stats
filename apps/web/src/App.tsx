import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router';
import { NAV_ITEMS } from './nav';
import './App.css';

/**
 * Root layout. Navigation is a sidebar at >= 1024px, a top bar of links
 * between 768 and 1023px, and a toggle menu below 768px (see App.css).
 */
export function App() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  // Close the menu on every location change ("store the previous value" pattern).
  const [prevKey, setPrevKey] = useState(location.key);
  if (prevKey !== location.key) {
    setPrevKey(location.key);
    setOpen(false);
  }
  const toggleRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const lastFocusedKey = useRef(location.key);

  // After navigating (not on first load) move focus to the page so it never drops to <body>.
  useEffect(() => {
    if (lastFocusedKey.current !== location.key) {
      lastFocusedKey.current = location.key;
      mainRef.current?.focus();
    }
  }, [location.key]);

  // Escape closes the menu only when it comes from the toggle or the nav.
  function onKeyDown(event: KeyboardEvent) {
    if (event.key === 'Escape' && open) {
      setOpen(false);
      toggleRef.current?.focus();
    }
  }

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="shell-header" onKeyDown={onKeyDown}>
        <h1>MTG Stats</h1>
        <button
          ref={toggleRef}
          type="button"
          className="menu-toggle"
          aria-expanded={open}
          aria-controls="primary-nav"
          onClick={() => {
            setOpen((v) => !v);
          }}
        >
          Menu
        </button>
      </header>
      <nav
        id="primary-nav"
        className="shell-nav"
        aria-label="Primary"
        data-open={open}
        onKeyDown={onKeyDown}
      >
        <ul>
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                onClick={() => {
                  setOpen(false);
                }}
              >
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <main id="main" ref={mainRef} tabIndex={-1} className="shell-main">
        <Outlet />
      </main>
      <footer className="shell-footer">
        <small>Footer placeholder</small>
      </footer>
    </div>
  );
}
