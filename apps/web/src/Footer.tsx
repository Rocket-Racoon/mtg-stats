import { Link } from 'react-router';
import { AttributionNotices } from './Attribution';
import { ThemeToggle } from './theme/ThemeToggle';

/** Site footer: legal notices (S-1.6.6), About link and theme toggle. Used by every layout. */
export function Footer() {
  return (
    <footer className="shell-footer">
      <div className="attribution">
        <AttributionNotices />
      </div>
      <div className="footer-controls">
        <Link to="/about" className="footer-link">
          About
        </Link>
        <ThemeToggle />
      </div>
    </footer>
  );
}
