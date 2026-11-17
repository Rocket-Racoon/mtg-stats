import { request } from '@playwright/test';

const LOGIN_URL = 'http://localhost:8000/api/v1/auth/login';

/**
 * Signs in once through the API before any test runs. A wrong or unseeded password then costs one
 * attempt and one clear message, not 28 failing tests and a throttled account (S-2.1.10).
 */
export default async function globalSetup(): Promise<void> {
  const username = process.env.E2E_USERNAME;
  const password = process.env.E2E_PASSWORD;
  if (!username || !password) {
    throw new Error('E2E_USERNAME and E2E_PASSWORD must be set (see apps/web/e2e/README.md).');
  }
  const api = await request.newContext();
  try {
    const response = await api.post(LOGIN_URL, { data: { username, password } });
    if (response.status() === 401) {
      throw new Error(
        'E2E credentials rejected (401); seed the account (see apps/web/e2e/README.md).',
      );
    }
    if (response.status() === 429) {
      throw new Error(
        'E2E sign-in throttled (429); wait for the lockout to end or use another username.',
      );
    }
    if (!response.ok()) {
      throw new Error(`E2E pre-flight sign-in failed with HTTP ${String(response.status())}.`);
    }
  } finally {
    await api.dispose();
  }
}
