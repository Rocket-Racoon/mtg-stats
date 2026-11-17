# Web end-to-end smoke suite (S-1.8.5)

Playwright, Chromium only, one worker. It signs in with a seeded account, opens each top-level route
through the navigation, checks the footer notices, and checks layout at 360, 768 and 1024 px.
Elements are found by role and label only (ESLint bans `.locator()` here).

## Run it locally

1. Start the backend (Postgres, Redis, migrate, api): `docker compose -f infra/docker-compose.yml up -d`.
   The API must answer on `http://localhost:8000/api/v1/health/live`.
2. Seed the test account (from `apps/api`; repeat runs reset its password):

   ```bash
   E2E_USERNAME=e2e-local E2E_PASSWORD='<a strong password>' \
     uv run python -m app.scripts.seed_e2e_user
   ```

3. Install the browser once: `pnpm --filter web exec playwright install chromium`
4. Run: `E2E_USERNAME=e2e-local E2E_PASSWORD='<the same password>' pnpm --filter web e2e`

Playwright builds the web app and serves it with `vite preview` on `http://localhost:5173` (the
API's dev CORS origin). If something already listens on 5173 it is reused outside CI.

`pnpm --filter web exec playwright test --list` prints the tests without needing credentials or a
running stack. Output goes to `apps/web/playwright-report/` and `apps/web/test-results/` (traces and
screenshots of failures); both are git-ignored.

The sign-out test is `test.fixme` until web gets a sign-out control (S-2.1.12, #71).
