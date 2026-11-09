import { Link } from 'react-router';
import { PlaceholderPage } from './Placeholder';

export function NotFoundPage() {
  return (
    <PlaceholderPage title="Page not found">
      <p>We could not find that page.</p>
      <p>
        <Link to="/">Go home</Link>
      </p>
    </PlaceholderPage>
  );
}
