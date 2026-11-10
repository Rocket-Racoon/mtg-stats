import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';

// One .env at the repo root is shared by every workspace.
const envDir = fileURLToPath(new URL('../..', import.meta.url));

// The app version shown on /about (S-1.6.6) comes from this package's own package.json.
const pkg = JSON.parse(
  readFileSync(fileURLToPath(new URL('./package.json', import.meta.url)), 'utf8'),
) as { version: string };

// Dev and test fall back to this value; `vite build` requires VITE_API_BASE_URL to be set.
const DEV_API_BASE_URL = 'http://localhost:8000/api/v1';

// Variables that must be set for a production build.
const REQUIRED = ['VITE_API_BASE_URL'] as const;

export default defineConfig(({ command, mode }) => {
  const env = { ...loadEnv(mode, envDir, 'VITE_'), ...process.env };
  const values = Object.fromEntries(REQUIRED.map((name) => [name, env[name]?.trim()]));

  if (command === 'build') {
    for (const name of REQUIRED) {
      if (!values[name]) {
        throw new Error(
          `${name} is required for \`vite build\` but is not set. ` +
            'Set it in the repo-root .env or the environment (see .env.example).',
        );
      }
    }
  }

  const apiBaseUrl = values.VITE_API_BASE_URL || DEV_API_BASE_URL;

  return {
    envDir,
    plugins: [react()],
    define: {
      'import.meta.env.VITE_API_BASE_URL': JSON.stringify(apiBaseUrl),
      'import.meta.env.VITE_APP_VERSION': JSON.stringify(pkg.version),
    },
    appType: 'spa',
    server: { port: 5173, strictPort: true },
    test: {
      include: ['src/**/*.test.{ts,tsx}', 'tests/**/*.test.ts'],
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
  };
});
