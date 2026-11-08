# CLAUDE.md — MTG Stats

This file governs the MTG Stats monorepo: a FastAPI API, a React (Vite) web app and an Expo mobile app. It **overrides the Django-specific rules** in `Desktop/CLAUDE.md`. That file's spirit still applies: plan first, delegate, and never merge unverified code. You are the orchestrator: you pick stories, delegate them, verify the result and merge.

## Commands

```bash
# Setup
uv sync                                   # Python deps (apps/api)
pnpm install                              # TypeScript deps (all workspaces)
docker compose -f infra/docker-compose.yml up -d postgres redis

# Run
pnpm dev:api                              # uv run uvicorn app.main:app --reload (apps/api)
pnpm dev:worker                           # uv run arq app.workers.settings.WorkerSettings
pnpm dev:web                              # Vite dev server
pnpm dev:mobile                           # Expo dev server

# Database (dev only)
uv run alembic revision --autogenerate -m "<msg>"
uv run alembic upgrade head
uv run alembic check                      # migration drift gate

# Verify gate: `pnpm verify` runs all of these in order
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict app
uv run pytest --cov=app --cov-fail-under=85
uv run alembic check
pnpm -r lint                              # eslint
pnpm -r format:check                      # prettier --check
pnpm -r typecheck                         # tsc --noEmit
pnpm -r test                              # Vitest (web, packages) + Jest/jest-expo (mobile)
pnpm --filter api-client generate         # regenerate the TS client after an API change
```

The exact commands are confirmed once the scaffold stories S-1.1.2 and S-1.1.3 are done.

## Workflow

1. **Pick a story issue** on GitHub (`Rocket-Racoon/mtg-stats`). **GitHub Issues are the source of truth**, and `docs/backlog/` is only the import snapshot. Work in phase order and respect each story's "Depends on" list.
2. **Confirm the acceptance criteria** before writing code. If a criterion looks wrong or conflicts with another story, flag it to the user. Never change it silently.
3. **Branch**: `s-<id>-<slug>` (for example `s-6.4.2-seat-drag`). **One story per PR.** Parallel stories each run in their own git worktree.
4. **Implement** through the right agent (see below). The test-engineer writes tests from the acceptance criteria, not from the implementation.
5. **Verify**: every story ends with the whole verify gate green. No exceptions.
6. **Review**: run `code-reviewer` on the diff against this file and the story.
7. **Close the loop**: tick the story's tasks in the issue, put `Closes #<n>` and any behavior changes or deviations in the PR description, then merge. Never push directly to `main`; this is a convention because GitHub Free can't enforce it (decision I1).

## Subagent delegation

Definitions live in `.claude/agents/`. Architecture decisions stay with the orchestrator, and agents never change the plan, story acceptance criteria or this file.

| Agent | Model | Owns | Story platform |
|---|---|---|---|
| `api-architect` | opus | Design notes and ADRs only, no code | design spikes, cross-domain changes |
| `api-backend` | sonnet | `apps/api`: models, schemas, routers, services, repositories, migrations, workers | `api`, `infra` (API side) |
| `frontend-web` | sonnet | `apps/web` + shared `packages/*` | `web`, `shared` |
| `frontend-mobile` | sonnet | `apps/mobile` + shared `packages/*` | `mobile`, `shared` |
| `test-engineer` | sonnet | Tests only (`tests/`, `*.test.*`, `*.spec.*`, `vectors/`, `e2e/`, `.maestro/`) | every story |
| `code-reviewer` | opus | Read-only review plus running the verify gate | every story |

- Fan out independent stories in parallel. Launch implementing agents with `isolation: "worktree"`.
- A story tagged with several platforms is split by platform. The API part lands first, then the TS client is regenerated, then web and mobile.
- `infra` stories with no API code (CI, Docker, deploy) are done by the orchestrator or `api-backend`.

## Architecture rules

Full detail lives in @docs/architecture.md. The non-negotiables:

- **Domains**: `identity`, `playgroups`, `cards`, `decks`, `brackets`, `games`, `stats`, `collection` and `leagues`, each in `apps/api/app/<domain>/` with `models.py`, `schemas.py`, `router.py`, `services/` and `repository.py`.
- **Public interface**: `services/__init__.py` is a domain's only public interface. Other domains, `workflows/` and `workers/` import only from it, never from another domain's models or repository.
- **Dependency direction** is fixed in docs/architecture.md §4, and **cycles are forbidden**. A workflow that spans domains goes in `app/workflows/`. A follow-up in a higher domain is enqueued as an arq job by name (for example `stats.refresh_game`), never by importing it.
- **Routers stay thin**: parse, call a service, respond. Business logic lives in `services/`. Reusable queries live in `repository.py`.
- **No ORM event listeners** (`@event.listens_for`) for business logic; use explicit service calls.
- **List endpoints eager-load** the relationships they serialize (`selectinload`/`joinedload`), with a test that counts queries.
- **Config comes from the environment only**, via pydantic-settings (`APP_ENV` = `dev` | `test` | `prod`). No secrets in code.
- **Errors** are RFC 9457 `application/problem+json` with a stable `code`. Validation errors carry `errors[]`.
- **The game event log is append-only.** Corrections and undo are compensating events. The server is authoritative. The Python and TypeScript reducers must both pass `packages/game-core/vectors/`.
- **Frontends** use TanStack Query hooks written by hand over the generated `openapi-fetch` client, plus Zustand for client state. They never call `fetch` directly.

## Conventions

- **Python and SQLAlchemy**
  - Python 3.12+, FastAPI and Pydantic v2, async SQLAlchemy 2.0 over **psycopg 3**.
  - **Primary keys are UUID v7**, from `app/core/ids.py`.
  - Timestamps are `DateTime(timezone=True)` set with `datetime.now(UTC)`. Never use a naive datetime.
  - String columns are never nullable; use a default of `""` instead.
  - Money is `Numeric(12, 2)` plus a currency code. Never use floats for money or points.
- **API**: everything is under `/api/v1`. **Cursor (keyset) paging** on list endpoints: `?cursor=&limit=` (default 50, max 200), returning `{items, next_cursor}`.
- **Alembic**
  - Never edit an applied migration.
  - Data migrations need a real `downgrade`, or an explicit no-op with a comment explaining why.
  - Squash migrations only in a dedicated PR.
  - Keep existing table and column names unless a story schedules a rename.
- **TypeScript**
  - Strict mode. ESLint and Prettier.
  - React Router on web and Expo Router on mobile.
  - Charts use Recharts on web and victory-native on mobile.
  - Drag and drop uses dnd-kit and react-grid-layout on web, and Gesture Handler plus Reanimated on mobile. **Every drag has a tap or keyboard alternative.**
- **Links**: every shareable link is an `https://` web URL whose page offers "Open in app" via `mtgstats://`. `mtgstats://` is also used for in-app navigation.
- **External APIs** send the User-Agent `MTGStats/<version> (+<repo URL>)` and never a personal email. Card images are hotlinked from Scryfall.

## Code style

- Type hints on everything, and `mypy --strict` must pass. Ruff is the single Python linter and formatter; don't hand-format against it.
- Docstrings on services and public functions. Skip them on obvious code.
- **Tests**: pytest, pytest-asyncio and factory_boy for Python; Vitest with Testing Library for web; jest-expo for mobile; Playwright and Maestro for end-to-end. No JSON fixtures, except the game-core test vectors.
- Test names describe behavior, for example `test_strict_mode_rejects_damage_to_other_seat`.

## Guardrails

- NEVER run `alembic upgrade`/`downgrade`, a data wipe or any destructive command against a non-dev database.
- NEVER commit secrets, `.env` files or coverage artifacts. NEVER bypass the pre-commit hooks (`--no-verify`).
- NEVER push directly to `main`.
- Ask before adding a dependency that isn't in plan §8 or the OPEN-QUESTIONS.md decisions log. Prefer the standard library and the approved stack.
- NEVER update or delete `game_events` rows from application code.
- If a spec or acceptance criterion looks wrong, flag it and ask. Don't silently "fix" it.

## Reference docs (load when needed)

- @docs/architecture.md: app map, domains and dependency rules, real-time flow, jobs, domain model
- `docs/adr/`: architecture decision records (template, ADR-0001 to ADR-0003)
- `docs/plans/mtg-stats-plan.md`: the plan, and section 8 for approved dependencies
- `docs/backlog/OPEN-QUESTIONS.md`: the decisions log for every product decision
- `docs/backlog/README.md`: the backlog conventions and the import snapshot
