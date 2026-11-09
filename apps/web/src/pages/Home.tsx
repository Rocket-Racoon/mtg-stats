import { env } from '../env';
import { linkedPackages } from '../index';
import { PlaceholderPage } from './Placeholder';

export function HomePage() {
  return (
    <PlaceholderPage title="Home">
      <p>App shell. API: {env.apiBaseUrl}</p>
      <p>Packages: {linkedPackages.join(', ')}</p>
    </PlaceholderPage>
  );
}
