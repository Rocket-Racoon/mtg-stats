# MTG Stats

A companion app for Magic: The Gathering **Commander** playgroups. It has five parts:

- **Live game table:** 2–8 players join from their own phones and track life, commander damage, poison and counters in real time. Drag and drop works for seats and damage, and every drag also has a tap alternative.
- **Decks:** import a deck as text or from a Moxfield or Archidekt link. The app checks legality (official bans plus playgroup house rules) and works out the **official WotC Commander Bracket**, using Commander Spellbook for combo detection.
- **Stats and activity:** win rates by player, deck, commander, seat and pod size; meta shifts; streaks; and a drag-and-drop dashboard.
- **Collection and budget:** a full collection tracker with daily Scryfall prices, a purchase log, spend reports and profit/loss.
- **Leagues:** seasons, scoring presets, standings and balanced pod matchmaking.

Stack: FastAPI · SQLAlchemy 2.0 · Alembic · Postgres · Redis/arq · React (Vite) · Expo (React Native) · pnpm + uv monorepo.

## Status

Planning is complete; implementation has not started. Phase 0 (Foundations) is next.

| | |
|---|---|
| Plan | [docs/plans/mtg-stats-plan.md](docs/plans/mtg-stats-plan.md) |
| Backlog (import snapshot) | [docs/backlog/README.md](docs/backlog/README.md) |
| Decisions log | [docs/backlog/OPEN-QUESTIONS.md](docs/backlog/OPEN-QUESTIONS.md) |

Once imported, **GitHub Issues** is the source of truth for the backlog. Issues are titled with their backlog ID, for example `[S-6.4.2]`.

## Setup

After cloning, install dependencies and the git hooks (required):

```bash
uv sync --project apps/api       # Python deps
pnpm install                     # TypeScript deps
cp .env.example .env             # root .env (PowerShell: Copy-Item .env.example .env); edit CHANGE_ME values if you like
pnpm hooks:install               # = uv run --frozen --project apps/api pre-commit install
```

The hooks run on staged files at every `git commit`: the secrets check, `detect-private-key`, `check-added-large-files` (500 KB), `end-of-file-fixer`, Ruff (check and format) on Python, and Prettier and ESLint on TypeScript. Run them on everything with `uv run --project apps/api pre-commit run --all-files`.

Install the hook from the main checkout. Worktrees share one hooks directory, so the installed hook is active in every worktree, and branches that lack `.pre-commit-config.yaml` fail to commit until this config is on `main`. On such old branches only, set `PRE_COMMIT_ALLOW_NO_CONFIG=1` to let the commit through (this is not `--no-verify`; don't use it on branches that have the config). Each worktree's first commit builds its own `.venv` (about 10-20 s).

Each worktree needs `pnpm install` before you commit TS, JSON, YAML or Markdown files covered by Prettier.

**Never commit with `--no-verify`.** The `api` CI job re-runs the secrets check over every tracked file and fails the branch.

## Run the backend

With the root `.env` from Setup in place, run from the repo root:

```bash
docker compose -f infra/docker-compose.yml up              # postgres, redis, migrate, api, worker
docker compose -f infra/docker-compose.yml up -d postgres redis   # infrastructure only, for native dev
```

- `api` and `worker` start only after Postgres and Redis are healthy and the one-shot `migrate` service has finished. `migrate` runs `alembic upgrade head` **only when `APP_ENV=dev`**; with any other value it exits non-zero without touching the database, and so `api` and `worker` do not start. Test it with `APP_ENV=prod docker compose -f infra/docker-compose.yml up` (bash).
- The API is at `http://127.0.0.1:8000` (`API_PORT` changes the host port). `GET /api/v1/health/ready` returns 200 when the database and Redis are `ok`.
- **Hot reload:** `apps/api` is bind-mounted into the containers, so saving a file under `apps/api/app` reloads the API within a few seconds, with no rebuild. The reloader polls, because Docker Desktop on Windows does not deliver file-change events. Rebuild only after changing `pyproject.toml` or `uv.lock`: `docker compose -f infra/docker-compose.yml up --build`.
- The **worker does not reload**. After a change, run `docker compose -f infra/docker-compose.yml restart worker`.
- Inside Compose the services use `postgres:5432` and `redis:6379`; the root `.env` keeps `127.0.0.1` URLs for native dev. The root `.env` is the single source of the database credentials: the containers build their `DATABASE_URL` from its `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB`. The containers run as uid 1000 and read the read-only `apps/api` mount, so on Linux keep source files world-readable (umask 022, not 077).
- **Native dev** (no API container; needs Postgres and Redis up): `pnpm dev:api` (uvicorn with `--factory app.main:create_app --reload`) and `pnpm dev:worker`. Without `--reload` on Windows, add `--loop asyncio:SelectorEventLoop`. Keep `127.0.0.1` (not `localhost`) in native-dev URLs.

## Run the mobile app

```bash
pnpm dev:mobile                  # Expo dev server (from the repo root)
```

`EXPO_PUBLIC_API_BASE_URL` is **required**. Without it (or with a value that is not an `http(s)` URL) the app shows a "Configuration error" screen naming the variable. For the `development` profile `apps/mobile/app.config.ts` copies it from the root `.env`; `preview` and `production` builds never read that file and must get the value from the build's own environment (a missing value shows the error screen). A value set in the shell or in `apps/mobile/.env` wins. The value is inlined into the bundle, so restart with a cleared cache after changing it: `pnpm dev:mobile --clear`.

The build profile comes from `EAS_BUILD_PROFILE`, else `APP_PROFILE` (`development`, `preview` or `production`), else `development`. In a dev build (a dev-server or dev-client bundle whose profile is not `production`), **More > About** shows the profile, the API base URL and the result of `GET /api/v1/health/ready`, with a retry button. Release builds show none of this.

### Test on a phone

`localhost` and `127.0.0.1` on a phone are the phone itself, not your computer. The API and Compose publish on `127.0.0.1` only, so to reach the API from a physical Android phone on the same Wi-Fi:

1. Start Postgres and Redis: `docker compose -f infra/docker-compose.yml up -d postgres redis`.
2. Start the API on all interfaces: `pnpm dev:api:lan` (same as `pnpm dev:api` with `--host 0.0.0.0`). **This exposes the dev API to everyone on your network; use it only on a network you trust.** Do not also run the Compose `api` service (both use port 8000). On Windows, allow Python/uvicorn through the firewall on a private network when the prompt appears.
3. Find your computer's LAN IP: Windows `ipconfig` (the IPv4 Address of the Wi-Fi adapter); macOS `ipconfig getifaddr en0` (Wi-Fi). Check it from the phone's browser: `http://<lan-ip>:8000/api/v1/health/ready`.
4. In the root `.env` set `EXPO_PUBLIC_API_BASE_URL=http://<lan-ip>:8000/api/v1`.
5. Run `pnpm dev:mobile --clear` and open the app on the phone with Expo Go (scan the QR code) or a development build. The computer and phone must be on the same network.
6. Open **More > About**: the API line should read "API: reachable (database ok, redis ok)".

For the Android emulator use `http://10.0.2.2:8000/api/v1` (the emulator's alias for the host) and the normal `pnpm dev:api`.

## Verify

`pnpm verify` is the local gate; CI runs the same commands, plus the web production build (locally: `VITE_API_BASE_URL=http://localhost:8000/api/v1 pnpm --filter @mtg-stats/web build`). It runs twelve steps in order and stops at the first failure, printing `verify: FAILED at step "<name>"` and exiting non-zero:

1. secrets check, 2. `ruff check`, 3. `ruff format --check`, 4. `mypy --strict`, 5. `pytest --cov=app --cov-fail-under=85`, 6. `alembic check`, 7. client drift check, 8. `eslint`, 9. `prettier --check`, 10. `tsc --noEmit`, 11. Vitest, 12. Jest.

- `pnpm verify:api` runs steps 1-6 only, `pnpm verify:ts` runs steps 7-12 only.
- `pnpm verify --list` prints the steps and their commands without running anything.
- Needs `uv` and `pnpm` on the PATH, the Compose stack up (`docker compose -f infra/docker-compose.yml up -d postgres redis`) and a root `.env`. `alembic check` needs the dev database at head; if it fails, run `uv run alembic upgrade head` from `apps/api` (dev database only).
- Test workspaces run one at a time, so a full run takes several minutes. The runner is one Node script with no shell, so it is cross-platform by design (developed on Windows PowerShell; CI runs both halves on Linux). Start it with `pnpm verify`: running `node scripts/verify.js` directly cannot find pnpm on Windows.
- CI (`.github/workflows/ci.yml`): the `api` job runs the API half (`node scripts/verify.js --only api`) against Postgres 16 and Redis 7 service containers, then an Alembic round-trip (`downgrade base`, `upgrade head`, `check`). The job summary shows each step's result and the coverage figure; the HTML and XML coverage report is the `coverage-report` artifact, kept 7 days. The `ts` job runs in parallel: `pnpm install --frozen-lockfile`, the TypeScript half (`pnpm verify:ts`) and the web production build, with the pnpm store and uv caches; its summary names the failed step.

## Planned layout

```
apps/
  api/        FastAPI service and arq worker
  web/        React + Vite web app
  mobile/     Expo React Native app
packages/
  api-client/ Generated TypeScript client (openapi-typescript + openapi-fetch)
  game-core/  Shared game-state reducer and JSON test vectors
  card-utils/ Shared card helpers (mana parsing, color identity, disclaimers)
infra/        Docker Compose and deploy scripts
docs/         Plan, backlog, architecture, operations
```

## Legal

MTG Stats is unofficial Fan Content permitted under the Wizards of the Coast Fan Content Policy. It is not approved or endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC. Card data and images are provided by [Scryfall](https://scryfall.com).
