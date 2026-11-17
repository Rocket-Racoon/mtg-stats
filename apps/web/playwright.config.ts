import { defineConfig, devices } from '@playwright/test';

/**
 * Web end-to-end smoke suite (S-1.8.5). The same config runs locally and in CI, in Chromium only.
 * The backend is the Compose stack (api on :8000); the web app is built and served by `vite preview`
 * on :5173, which is the API's dev CORS origin (DEV_WEB_ORIGIN).
 */
const WEB_URL = 'http://localhost:5173';
const API_BASE_URL = 'http://localhost:8000/api/v1';

// Fail fast with a clear message (`--list` only enumerates tests, so it needs no credentials).
if (!process.argv.includes('--list') && !(process.env.E2E_USERNAME && process.env.E2E_PASSWORD)) {
  throw new Error(
    'E2E_USERNAME and E2E_PASSWORD must be set to the seeded test account. ' +
      'Seed it with `uv run python -m app.scripts.seed_e2e_user` (see apps/web/e2e/README.md).',
  );
}

export default defineConfig({
  testDir: './e2e',
  outputDir: 'test-results',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  globalTimeout: 10 * 60_000,
  forbidOnly: Boolean(process.env.CI),
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: WEB_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'pnpm build && pnpm exec vite preview --port 5173 --strictPort',
    url: WEB_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: { VITE_API_BASE_URL: API_BASE_URL },
  },
});
