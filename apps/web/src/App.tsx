import { env } from './env';
import { linkedPackages } from './index';

export function App() {
  return (
    <div>
      <header>
        <h1>MTG Stats</h1>
      </header>
      <main>
        <p>App shell. API: {env.apiBaseUrl}</p>
        <p>Packages: {linkedPackages.join(', ')}</p>
      </main>
      <footer>
        <small>Footer placeholder</small>
      </footer>
    </div>
  );
}
