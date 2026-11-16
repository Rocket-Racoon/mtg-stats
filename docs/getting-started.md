# Getting started

From a fresh clone to Postgres, Redis, the API, the worker, the web dev server and the Expo dev server running takes about 15 minutes, most of it downloads. Every command below works in **PowerShell on Windows 11** (no bash or WSL needed) and in a macOS terminal. Where the two differ, both forms are shown. After step 1, run everything from the repository root (`mtg-stats`) unless a step says otherwise.

## 1. Clone the repository

```powershell
git clone https://github.com/Rocket-Racoon/mtg-stats.git
cd mtg-stats
```

## 2. Install the prerequisites

| Tool            | Required                               | Notes                                                       |
| --------------- | -------------------------------------- | ----------------------------------------------------------- |
| Docker          | 25.0 or newer                          | Docker Desktop on Windows 11 and macOS. Start it, then check `docker info` works. `healthcheck.start_interval` in `infra/docker-compose.yml` needs Engine 25. |
| Docker Compose  | 2.24 or newer (`docker compose version`) | Ships with Docker Desktop. The long-form `env_file` with `required:` in `infra/docker-compose.yml` needs 2.24. |
| uv              | 0.10 or newer                          | Installs and runs Python for you. The repo pins 0.10.12 (`apps/api/Dockerfile`, CI). |
| Python          | 3.12 or newer                          | uv can install it (`uv python install 3.12`).               |
| Node            | 24 (`>=24 <25`)                        | From `package.json` `engines`.                              |
| pnpm            | 11.1.3 or newer 11.x (`>=11.1.3 <12`)  | From `package.json` `engines`. `corepack enable` works too. |
| Git             | any recent version                     |                                                             |

Docker Desktop on Windows uses WSL 2 internally; you do not need to install or run any WSL commands yourself.

Install hints:

```powershell
# Windows 11 (PowerShell)
winget install Docker.DockerDesktop
winget install astral-sh.uv
winget show OpenJS.NodeJS.LTS --versions            # list the versions winget offers
winget install OpenJS.NodeJS.LTS --version <24.x.y from the list above>
npm install --global pnpm@11.1.3
```

After any `winget install`, **close PowerShell and open a new window**: the new tools are only on the PATH of windows opened afterwards. Docker Desktop may also ask you to sign out or restart.

Node note for Windows: the repo needs Node 24 (`>=24 <25`), and the unpinned `OpenJS.NodeJS.LTS` package is already newer than Node 24, so an unpinned install fails the check. winget accepts only exact versions: replace `<24.x.y from the list above>` with an exact number from the `--versions` listing that starts with 24. If `pnpm prereqs` reports node 25 or newer, uninstall that Node (`winget uninstall OpenJS.NodeJS.LTS`) and install a 24 build the same way, or use a version manager such as fnm (`winget install Schniz.fnm`, then `fnm install 24` and `fnm use 24`; fnm needs its shell hook, see its docs).

```bash
# macOS (Homebrew)
brew install --cask docker-desktop   # older Homebrew: `--cask docker`; confirm the name with `brew search docker`
brew install uv node@24
brew link --overwrite --force node@24   # node@24 is keg-only; this puts node and npm on the PATH
npm install --global pnpm@11.1.3
```

On macOS, if you would rather not link it, add `export PATH="$(brew --prefix node@24)/bin:$PATH"` to your shell profile instead.

The minimums for Python, Node and pnpm are read by the check from `apps/api/pyproject.toml` and `package.json`. The Docker, Docker Compose and uv floors are set in `scripts/check_prereqs.py`, for the reasons in the table.

### Check the prerequisites (still step 2)

```powershell
pnpm prereqs
```

This is `uv run --no-project python scripts/check_prereqs.py`: Python standard library only, one line per tool. The `python` line reports the interpreter uv picks for `uv run --no-project`, which can differ from `apps/api/.python-version` (3.12, which `uv sync` downloads by itself); if that line fails, run `uv python install 3.12` and check again. If a tool is missing or too old, the line names the tool, the required version and the version found (or `not found`), the script prints `prereqs: FAILED (...)` and exits non-zero. Example:

```
FAIL docker: required >=25.0, not found
ok   node: required >=24 <25, found 24.19.0
prereqs: FAILED (docker). Install or upgrade (docs/getting-started.md)
```

Fix what it names and run it again until it ends with `prereqs: all prerequisites met`. If `pnpm` is the missing tool, `pnpm prereqs` cannot start; run `uv run --no-project python scripts/check_prereqs.py` instead. If `uv` is missing too, run `python scripts/check_prereqs.py` (PowerShell: `python`, macOS: `python3`); any Python 3.8 or newer can start it, and it reports itself if it is too old.

## 3. Install the dependencies

```powershell
uv sync --project apps/api       # Python dependencies
pnpm install                     # TypeScript dependencies, all workspaces
```

If you plan to commit, also run `pnpm hooks:install` once from the main checkout (not from a git worktree). See the README "Setup" section for what the hooks do and the rules around them. Never commit with `--no-verify`.

## 4. Create the `.env` file

There is one `.env`, at the repository root, and it is gitignored.

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS
cp .env.example .env
```

Most values in `.env.example` are safe local defaults, but `JWT_SECRET` is not: the API refuses to start while it is the `CHANGE_ME` placeholder or shorter than 32 characters. `POSTGRES_PASSWORD=CHANGE_ME` works as a literal local password; if you change it, change the password inside `DATABASE_URL` too. Changing `POSTGRES_PASSWORD` after Postgres has first started has no effect, because the existing data volume keeps the old password (remove the volume to reset it, dev only). Keep `127.0.0.1` rather than `localhost` in the URLs (on Windows `localhost` tries IPv6 first and can hang). Never commit `.env` or real secrets; the secrets check fails the branch if you do.

Now generate a `JWT_SECRET` (the secret that signs access and refresh tokens) and paste it into `.env`. The command is the same in PowerShell and macOS:

```powershell
uv run --no-project python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Open `.env`, replace `JWT_SECRET=CHANGE_ME` with `JWT_SECRET=<the printed value>` and save. Do not commit or share it. The Compose `migrate`, `api` and `worker` services receive it through `env_file`, so the same `.env` serves the Compose and native paths.

## 5. Start the backend (Postgres, Redis, API, worker)

```powershell
docker compose -f infra/docker-compose.yml up
```

This starts Postgres, Redis, a one-shot `migrate` service (runs `alembic upgrade head` on the dev database, and only when `APP_ENV=dev`), then the API and the worker. Leave it running in its own terminal. Add `-d` to run it in the background.

- Check it: open `http://127.0.0.1:8000/api/v1/health/ready`. It returns 200 when the database and Redis are `ok`.
- The API reloads when you save a file under `apps/api/app`. The worker does not: run `docker compose -f infra/docker-compose.yml restart worker` after a change.
- Details (ports, hot reload, credentials) are in the README "Run the backend" section and the header of `infra/docker-compose.yml`.

**Native alternative** (Python runs on your machine instead of in containers). Start only the infrastructure, then run the API and worker with the root scripts, each in its own terminal:

```powershell
docker compose -f infra/docker-compose.yml up -d postgres redis
uv run --directory apps/api alembic upgrade head   # creates the schema (dev database only)
pnpm dev:api
pnpm dev:worker
```

The `alembic` line is the same in PowerShell and macOS: `--directory` makes uv run it from `apps/api`, where `alembic.ini` lives. Do not run the Compose `api` and `worker` services at the same time as the native ones (the API port 8000 clashes).

## 6. Start the web and mobile dev servers

Each one runs in its own terminal, from the repository root.

| Script            | What it runs                                         | Where                                       |
| ----------------- | ---------------------------------------------------- | ------------------------------------------- |
| `pnpm dev:api`    | uvicorn with reload, native (no container)           | `http://127.0.0.1:8000`                     |
| `pnpm dev:api:lan` | same, on all interfaces, for a physical phone       | `http://<lan-ip>:8000`                      |
| `pnpm dev:worker` | the arq worker, native                               | no port                                     |
| `pnpm dev:web`    | the Vite dev server for `apps/web`                   | `http://localhost:5173`                     |
| `pnpm dev:mobile` | the Expo dev server for `apps/mobile`                | Expo prints a QR code and a menu            |

```powershell
pnpm dev:web
pnpm dev:mobile
```

The web app reads `VITE_API_BASE_URL` and the mobile app reads `EXPO_PUBLIC_API_BASE_URL` from the root `.env`; the defaults point at `http://localhost:8000/api/v1`. After changing the mobile value, restart with `pnpm dev:mobile --clear`. To test on a physical phone or an emulator, follow the README "Test on a phone" section.

With the backend (step 5) and both dev servers up, you have Postgres, Redis, the API, the worker, the web dev server and the Expo dev server running.

## 7. Run the verify gate

`pnpm verify` is the local gate that every change must pass; CI runs the same commands, plus the web production build. It runs twelve steps in order and stops at the first failure, printing `verify: FAILED at step "<name>"` and exiting non-zero:

1. secrets check, 2. `ruff check`, 3. `ruff format --check`, 4. `mypy --strict`, 5. `pytest --cov=app --cov-fail-under=85`, 6. `alembic check`, 7. client drift check, 8. `eslint`, 9. `prettier --check`, 10. `tsc --noEmit`, 11. Vitest, 12. Jest.

Before the first run:

1. Start Postgres and Redis (step 5 does this; `docker compose -f infra/docker-compose.yml up -d postgres redis` is enough).
2. Have the root `.env` in place (step 4).
3. Have the dev database at head, which `alembic check` needs. The Compose `migrate` service already does this; for the native setup run `uv run --directory apps/api alembic upgrade head`. Dev database only, never against another one.

Then start it with `pnpm verify`:

```powershell
pnpm verify
```

Always start it with `pnpm verify`, not `node scripts/verify.js`: on Windows the runner can only find pnpm through `npm_execpath`, which pnpm sets.

- `pnpm verify:api` runs steps 1-6 only, `pnpm verify:ts` runs steps 7-12 only.
- `pnpm verify --list` prints the steps and their commands without running anything.
- Test workspaces run one at a time, so a full run takes several minutes.
- The web production build that CI adds: PowerShell `$env:VITE_API_BASE_URL="http://localhost:8000/api/v1"; pnpm --filter @mtg-stats/web build`; macOS `VITE_API_BASE_URL=http://localhost:8000/api/v1 pnpm --filter @mtg-stats/web build`.
- CI (`.github/workflows/ci.yml`): the `api` job runs the API half (`node scripts/verify.js --only api`) against Postgres 16 and Redis 7 service containers, then an Alembic round-trip (`downgrade base`, `upgrade head`, `check`). The job summary shows each step's result and the coverage figure; the HTML and XML coverage report is the `coverage-report` artifact, kept 7 days. The `ts` job runs in parallel: `pnpm install --frozen-lockfile`, the TypeScript half (`pnpm verify:ts`) and the web production build, with the pnpm store and uv caches; its summary names the failed step.

## Troubleshooting

- **`pnpm prereqs` fails**: install or upgrade the tool it names (step 2) and run it again.
- **PowerShell says `pnpm.ps1 cannot be loaded because running scripts is disabled`**: allow local scripts for your user with `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then open a new PowerShell window.
- **A tool you just installed is "not found"**: open a new PowerShell window so it sees the updated PATH.
- **Docker commands fail with a connection error**: Docker Desktop is not running. Start it and wait until it says it is running.
- **Port 5432, 6379 or 8000 already in use**: stop the other program, or change the host port. See the header of `infra/docker-compose.yml`.
- **The API, the `migrate` service or `alembic upgrade head` exits with `JWT_SECRET must be at least 32 characters and not the CHANGE_ME placeholder; generate one with: ...`** (in Compose, read it with `docker compose -f infra/docker-compose.yml logs migrate api`; `migrate` fails first, so `api` may show nothing): `JWT_SECRET` in `.env` is still the placeholder or is too short. Generate one as in step 4, paste it into `.env`, and restart (`docker compose -f infra/docker-compose.yml up -d --force-recreate`, with no service list so `migrate` runs again, or restart the native process).
- **`alembic check` fails in `pnpm verify`**: the dev database is not at head. Run `uv run --directory apps/api alembic upgrade head` (dev database only).
- **The mobile app shows a "Configuration error" screen**: `EXPO_PUBLIC_API_BASE_URL` is missing or not an `http(s)` URL; see the README "Run the mobile app" section.
