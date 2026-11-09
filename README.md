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
pnpm hooks:install               # = uv run --project apps/api pre-commit install
```

The hooks run on staged files at every `git commit`: the secrets check, `detect-private-key`, `check-added-large-files` (500 KB), `end-of-file-fixer`, Ruff (check and format) on Python, and Prettier and ESLint on TypeScript. Run them on everything with `uv run --project apps/api pre-commit run --all-files`.

Install the hook from the main checkout. Worktrees share one hooks directory, so the installed hook is active in every worktree, and branches that lack `.pre-commit-config.yaml` fail to commit until this config is on `main`. On such old branches only, set `PRE_COMMIT_ALLOW_NO_CONFIG=1` to let the commit through (this is not `--no-verify`; don't use it on branches that have the config). Each worktree's first commit builds its own `.venv` (about 10-20 s).

**Never commit with `--no-verify`.** CI will re-run the secrets check and fail the branch, once S-1.8.2 lands.

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
