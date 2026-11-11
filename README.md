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
pnpm hooks:install               # = uv run --frozen --project apps/api pre-commit install
```

The hooks run on staged files at every `git commit`: the secrets check, `detect-private-key`, `check-added-large-files` (500 KB), `end-of-file-fixer`, Ruff (check and format) on Python, and Prettier and ESLint on TypeScript. Run them on everything with `uv run --project apps/api pre-commit run --all-files`.

Install the hook from the main checkout. Worktrees share one hooks directory, so the installed hook is active in every worktree, and branches that lack `.pre-commit-config.yaml` fail to commit until this config is on `main`. On such old branches only, set `PRE_COMMIT_ALLOW_NO_CONFIG=1` to let the commit through (this is not `--no-verify`; don't use it on branches that have the config). Each worktree's first commit builds its own `.venv` (about 10-20 s).

Each worktree needs `pnpm install` before you commit TS, JSON, YAML or Markdown files covered by Prettier.

**Never commit with `--no-verify`.** The `api` CI job re-runs the secrets check over every tracked file and fails the branch.

## Verify

`pnpm verify` is the local gate and runs the same checks as CI. It runs twelve steps in order and stops at the first failure, printing `verify: FAILED at step "<name>"` and exiting non-zero:

1. secrets check, 2. `ruff check`, 3. `ruff format --check`, 4. `mypy --strict`, 5. `pytest --cov=app --cov-fail-under=85`, 6. `alembic check`, 7. client drift check, 8. `eslint`, 9. `prettier --check`, 10. `tsc --noEmit`, 11. Vitest, 12. Jest.

- `pnpm verify:api` runs steps 1-6 only, `pnpm verify:ts` runs steps 7-12 only.
- `pnpm verify --list` prints the steps and their commands without running anything.
- Needs `uv` and `pnpm` on the PATH, the Compose stack up (`docker compose -f infra/docker-compose.yml up -d postgres redis`) and a root `.env`. `alembic check` needs the dev database at head; if it fails, run `uv run alembic upgrade head` from `apps/api` (dev database only).
- Test workspaces run one at a time, so a full run takes several minutes. The runner is one Node script with no shell, so it is cross-platform by design (developed on Windows PowerShell; CI runs it on Linux). Start it with `pnpm verify`: running `node scripts/verify.js` directly cannot find pnpm on Windows.
- CI (`.github/workflows/ci.yml`): the `api` job runs the API half (`node scripts/verify.js --only api`) against Postgres 16 and Redis 7 service containers, then an Alembic round-trip (`downgrade base`, `upgrade head`, `check`). The job summary shows each step's result and the coverage figure; the HTML and XML coverage report is the `coverage-report` artifact, kept 7 days.

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
