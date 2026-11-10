# CLAUDE.md — MTG Stats

This file governs the MTG Stats monorepo: a FastAPI API, a React (Vite) web app and an Expo mobile app. It **overrides the Django-specific rules** in `Desktop/CLAUDE.md`. That file's spirit still applies: plan first, delegate, and never merge unverified code. You are the orchestrator: you pick stories, delegate them, verify the result and merge.

## Commands

```bash
# Setup
uv sync                                   # Python deps (apps/api)
pnpm install                              # TypeScript deps (all workspaces)
docker compose -f infra/docker-compose.yml up -d postgres redis
pnpm hooks:install                        # pre-commit hooks (S-1.8.7); run once from the main checkout

# Run
pnpm dev:api                              # uv run uvicorn app.main:app --reload (apps/api)
pnpm dev:worker                           # uv run python -m app.workers (arq; SelectorEventLoop on Windows)
pnpm dev:web                              # Vite dev server
pnpm dev:mobile                           # Expo dev server

# Database (dev only; uv commands run from apps/api)
uv run alembic revision --autogenerate -m "<msg>"
uv run alembic upgrade head

# API client: regenerate after any API change (CI fails on drift)
pnpm --filter @mtg-stats/api-client generate

# Verify gate (S-1.8.1). `pnpm verify` runs these in order and stops at the first failure.
# `pnpm verify:api` and `pnpm verify:ts` run one half.
#  1. pnpm secrets:check                   # scripts/secrets_check.py over all tracked files
#  2. uv run ruff check . && uv run ruff format --check .
#  3. uv run mypy --strict app/
#  4. uv run pytest --cov=app --cov-fail-under=85
#  5. uv run alembic check                 # migration drift
#  6. client drift check                   # the generated api-client matches the OpenAPI spec
#  7. pnpm -r lint                         # eslint
#  8. pnpm -r format:check                 # prettier --check
#  9. pnpm -r typecheck                    # tsc --noEmit
# 10. pnpm -r test                         # Vitest (web, packages) + Jest/jest-expo (mobile)
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
| `api-backend` | sonnet | `apps/api` (models, schemas, routers, services, repositories, migrations, workers), plus `infra/` when a story's infra work serves the API | `api`, `infra` (API side) |
| `frontend-web` | sonnet | `apps/web` + shared `packages/*` | `web`, `shared` |
| `frontend-mobile` | sonnet | `apps/mobile` + shared `packages/*` | `mobile`, `shared` |
| `test-engineer` | sonnet | Tests only (`apps/api/tests/`, `apps/api/app/**/tests/`, `*.test.*`, `*.spec.*`, `packages/game-core/vectors/`, `apps/web/e2e/`, `apps/mobile/e2e/`) | every story |
| `code-reviewer` | opus | Read-only review plus running the verify gate | every story |

- Fan out independent stories in parallel. Launch implementing agents with `isolation: "worktree"`.
- A story tagged with several platforms is split by platform. The API part lands first, then the TS client is regenerated, then web and mobile.
- **`shared` tie-break**: `frontend-web` owns a change to `packages/*` unless the story is mobile-only. When both apps need the change, `frontend-web` lands the package change first and `frontend-mobile` consumes it.
- **`infra` tie-break**: the orchestrator does CI (`.github/`), deploy and backups. `api-backend` does the infra work that ships with API code, such as Compose services and env settings.

## Architecture rules

Full detail lives in @docs/architecture.md. The non-negotiables:

- **Domains**: `system`, `identity`, `playgroups`, `cards`, `decks`, `brackets`, `games`, `stats`, `collection` and `leagues`, each in `apps/api/app/<domain>/` with `models.py`, `schemas.py`, `router.py`, `services/`, `repository.py` and `tests/`. Architecture §4.1 maps the backlog's names ("players", "prices") onto these domains.
- **Public interface**: `services/__init__.py` is a domain's only public interface. Other domains, `workflows/` and `workers/` import only from it, never from another domain's models or repository.
- **Dependency direction** is fixed in docs/architecture.md §4.2. The graph is transitive, **cycles are forbidden**, and `tests/architecture/test_domain_imports.py` enforces it.
  - Every case that would need an upward call has a documented mechanism in §4.3, one of the five: a workflow, a job enqueued by name, an inverted call, a change of ownership or client composition. A new case needs a row there before any code is written.
  - Cross-domain workflows live in `app/workflows/` and share one transaction by passing the session.
  - A service running in a caller's session never commits, enqueues or publishes directly. Follow-ups use `core.db.after_commit` (and `core.jobs.enqueue_after_commit`). Job names are the canonical `<owner>.<job>` names in architecture §7.
  - Auth dependencies (`current_user`, `require_site_admin`) come from `core.auth`, never from `identity`.
  - Follow-ups in a higher domain are enqueued by name (for example `stats.refresh_game_stats`), never imported.
- **Routers stay thin**: parse, call a service, respond. Business logic lives in `services/`. Reusable queries live in `repository.py`, which queries only its own domain's tables.
- **No ORM event listeners** (`@event.listens_for`) for business logic; use explicit service calls.
- **List endpoints eager-load** the relationships they serialize (`selectinload`/`joinedload`), with a test that counts queries.
- **Config comes from the environment only**, via pydantic-settings (`APP_ENV` = `dev` | `test` | `prod`). No secrets in code.
- **Errors** (S-1.4.5): every error returns `{"error": {"code", "message", "details", "request_id"}}`. `code` is stable snake_case, and validation errors go in `details`. Clients switch on `code`, never on `message`.
- **The game event log is append-only.** Corrections and undo are compensating events. The only exceptions are the audited `games.reassign_player` and `games.delete_playgroup_data`. The server is authoritative. The Python and TypeScript reducers must both pass `packages/game-core/vectors/`.
- **Frontends** use TanStack Query hooks written by hand over the generated `openapi-fetch` client, plus Zustand for client state. They never call `fetch` directly.

## Conventions

- **Python and SQLAlchemy**
  - Python 3.12+, FastAPI and Pydantic v2, async SQLAlchemy 2.0 over **psycopg 3** (decision A2).
  - **Primary keys are UUID v7** (decision B1), from `app/core/ids.py`.
  - Timestamps are `DateTime(timezone=True)` set with `datetime.now(UTC)`. Never use a naive datetime.
  - String columns are never nullable; use a default of `""` instead.
  - Money is `Numeric(12, 2)` plus a currency code. Never use floats for money or points.
- **API**: everything is under `/api/v1`. **Cursor (keyset) paging** on list endpoints (decision B2): `?cursor=&limit=` (default 50, max 200), returning `{items, next_cursor}`.
- **Backlog**: **GitHub Issues are the source of truth** for backlog work (decision I2). `docs/backlog/` is the 2026-11-07 import snapshot.
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
- **Links** (S-1.6.7): shareable links are built only on the server, with `app/core/links.py` `public_link()`, as `https://` web URLs. The page's `OpenInAppButton` uses `mtgstats://`, which is also used for in-app navigation.
- **External APIs** send the User-Agent `MTGStats/<version> (+<repo URL>)` and never a personal email. Card images are hotlinked from Scryfall.

## Code style

- Type hints on everything, and `mypy --strict` must pass. Ruff is the single Python linter and formatter; don't hand-format against it.
- Docstrings on services and public functions. Skip them on obvious code.
- **Tests**: pytest, pytest-asyncio and factory_boy for Python; Vitest with Testing Library for web; jest-expo for mobile; Playwright and Maestro for end-to-end. No JSON fixtures, except the game-core test vectors.
- Test names describe behavior, for example `test_strict_mode_rejects_damage_to_other_seat`.

## Guardrails

- NEVER run `alembic upgrade`/`downgrade`, a data wipe or any destructive command against a non-dev database.
- NEVER edit an applied migration. Data migrations need a real `downgrade`, or an explicit no-op with a comment explaining why.
- NEVER commit secrets, `.env` files or coverage artifacts. NEVER bypass the pre-commit hooks (`--no-verify`).
  - The hooks (`.pre-commit-config.yaml`, S-1.8.7) run on staged files: the secrets check (first, fail-fast), `detect-private-key`, `check-added-large-files` (500 KB), `end-of-file-fixer`, `ruff check --fix` and `ruff format`, Prettier and ESLint.
  - Never run `pre-commit install` inside an agent worktree: worktrees share one `.git/hooks`.
- NEVER push directly to `main`. This is a convention, because GitHub Free can't enforce it (decision I1).
- **Approved dependencies** are those in plan §8, ADR-0002 and the OPEN-QUESTIONS.md decisions log. Ask before adding anything else, and prefer the standard library.
- The game event log is append-only: NEVER update or delete `game_events` rows. Corrections and undo are compensating events. The only exceptions are the audited `games.reassign_player` (guest merge) and `games.delete_playgroup_data` (playgroup deletion).
- Every drag-and-drop interaction has a tap or keyboard alternative.
- If a spec or acceptance criterion looks wrong, flag it and ask. Don't silently "fix" it. Docs never override a story; a conflict is raised with the user.

## Reference docs (load when needed)

- @docs/architecture.md: app map, domains and dependency rules, real-time flow, jobs, domain model
- `docs/adr/`: architecture decision records (template, ADR-0001 to ADR-0003)
- `docs/plans/mtg-stats-plan.md`: the plan, and section 8 for approved dependencies
- `docs/backlog/OPEN-QUESTIONS.md`: the decisions log for every product decision
- `docs/backlog/README.md`: the backlog conventions and the import snapshot
