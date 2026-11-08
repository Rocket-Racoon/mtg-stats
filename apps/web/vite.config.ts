import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';

// One .env at the repo root is shared by every workspace.
const envDir = fileURLToPath(new URL('../..', import.meta.url));

// Dev and test fall back to this value; `vite build` requires VITE_API_BASE_URL to be set.
const DEV_API_BASE_URL = 'http://localhost:8000/api/v1';

export default defineConfig(({ command, mode }) => {
  const env = { ...loadEnv(mode, envDir, 'VITE_'), ...process.env };
  const apiBaseUrl = env.VITE_API_BASE_URL?.trim();

  if (command === 'build' && !apiBaseUrl) {
    throw new Error(
      'VITE_API_BASE_URL is required for `vite build` but is not set. ' +
        'Set it in the repo-root .env or the environment (see .env.example).',
    );
  }

  return {
    envDir,
    plugins: [react()],
    define: apiBaseUrl
      ? {}
      : { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify(DEV_API_BASE_URL) },
    server: { port: 5173, strictPort: true },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
  };
});
