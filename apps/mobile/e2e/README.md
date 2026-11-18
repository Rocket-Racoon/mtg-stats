# Mobile end-to-end smoke suite (S-1.8.6)

Maestro, Android emulator, one flow: `smoke.yaml`. It signs in with a seeded account, visits the
Play, Decks, Stats and More tabs, opens About, opens `mtgstats://join/SMOKE42` and checks the Join
screen, then signs out. Elements are found by testID (`id:`), never by coordinates. The app is a
release build (no Metro, no dev menu) that talks to `http://10.0.2.2:8000`, the host machine as seen
from the emulator.

## Run it locally

Prerequisites: JDK 17, the Android SDK with an x86_64 emulator image (a running AVD), and the
Maestro CLI **cli-2.12.1** (`maestro --version` must print `2.12.1`).

1. Start the backend: `docker compose -f infra/docker-compose.yml up -d`. The API must answer on
   `http://localhost:8000/api/v1/health/live`.
2. Seed the test account (from `apps/api`). Use the fixed username below so repeat runs reset its
   password instead of adding rows:

   ```bash
   E2E_USERNAME=e2e_local E2E_PASSWORD='<a strong password>' \
     uv run python -m app.scripts.seed_e2e_user
   ```

3. Build the release APK and install it on the emulator:

   ```bash
   pnpm --filter @mtg-stats/mobile e2e:build
   adb install -r apps/mobile/android/app/build/outputs/apk/release/app-release.apk
   ```

4. Export the credentials (Maestro reads `MAESTRO_`-prefixed shell variables) and run:

   ```bash
   # bash
   export MAESTRO_E2E_USERNAME=e2e_local
   export MAESTRO_E2E_PASSWORD='<the same password>'
   pnpm --filter @mtg-stats/mobile e2e
   ```

   ```powershell
   # PowerShell
   $env:MAESTRO_E2E_USERNAME = 'e2e_local'
   $env:MAESTRO_E2E_PASSWORD = '<the same password>'
   pnpm --filter @mtg-stats/mobile e2e
   ```

Screenshots and debug output go to `apps/mobile/e2e-output/` (git-ignored). Maestro captures a
screenshot of the failing step automatically.

## CI

`.github/workflows/mobile-e2e.yml` runs the same flow on an emulator nightly (03:00 UTC), on demand,
and on pull requests that touch `apps/mobile/e2e/**`. When the run fails it uploads
`apps/mobile/e2e-output/` as the `maestro-output` artifact.
