# E-1 — Platform Foundations

| | |
|---|---|
| **Phase(s)** | 0 |
| **Goal** | A working monorepo where the API, worker, web and mobile apps start locally, share a generated API client, follow one set of project rules, and pass the same verify gate locally and in CI before anything merges. |
| **Success measures** | • A fresh clone reaches a running stack (Postgres, Redis, API, worker, web, Expo) in ≤ 15 minutes by following the getting-started doc.<br>• `GET /api/v1/health/ready` reports database and Redis status, and every error response uses the same envelope.<br>• CI runs the full verify gate on every pull request, and by documented convention nothing merges with a red check (GitHub Free can't enforce branch protection on a private repo, decision I1).<br>• The pre-commit hooks run lint, format and the secret scan before every commit.<br>• The generated API client on `main` always matches the API's OpenAPI spec. |
| **Out of scope** | Account, invite, guest and playgroup endpoints (E-2, F-2.1 … F-2.5); every auth screen (sign-in, sign-up via invite, password reset), which E-2 owns (S-2.1.6, S-2.1.7), while E-1 provides only routing, layout, the auth guard and the API client with token refresh; any domain feature (E-3 … E-10); hosting, deployment and production operations (E-11). |

## F-1.1 — Monorepo & tooling

Sets up the git repository with its private GitHub remote, the folder layout from plan §3, uv for Python and pnpm workspaces for TypeScript, and the shared lint, format and type-check configuration every later story relies on.

**Phase** 0 · **Priority** Must

#### S-1.1.1 — Initialize the repository and private GitHub remote

**As a** developer **I want** the project in git with a private GitHub remote **so that** every change is versioned and reviewable from day one.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 1 | infra | 0 | — |

**Acceptance criteria**
- **Given** the `MTG-Stats` folder **When** the repository is initialized **Then** the default branch is `main` and `git remote -v` points to the GitHub repo `mtg-stats`, whose visibility is **private**.
- **Given** the first commit **When** it is pushed **Then** it contains the existing `docs/plans/` and `docs/backlog/` files and a root README linking to both.
- **Given** a file named `.env`, a `.venv/` or `node_modules/` folder, or a coverage artifact (`.coverage`, `coverage/`, `htmlcov/`) anywhere in the tree **When** `git status` runs **Then** it is not listed as untracked.
- **Given** a developer on Windows **When** they check out shell scripts or migration files **Then** the files have LF line endings (enforced by `.gitattributes`).

**Tasks**
- [ ] T-1.1.1.1 (infra) `git init` with `main` as the default branch; add `.gitignore` and `.gitattributes`
- [ ] T-1.1.1.2 (infra) Create the private GitHub repo `mtg-stats`, add it as `origin` and push the first commit
- [ ] T-1.1.1.3 (infra) Root README: purpose, folder layout, links to the plan and backlog

#### S-1.1.2 — Python project managed with uv

**As a** backend developer **I want** the API's Python project managed by uv with a committed lockfile **so that** every machine and CI run uses the same interpreter and dependency versions.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 0 | S-1.1.1 |

**Acceptance criteria**
- **Given** a fresh clone with uv installed **When** `uv sync` runs in `apps/api` **Then** a virtual environment with Python 3.12 or newer is created from `uv.lock`, and `uv sync --locked` fails if the lockfile is out of date.
- **Given** `apps/api/pyproject.toml` **When** it is reviewed **Then** its runtime dependencies are only FastAPI and the plan §8 list (SQLAlchemy 2.0 with its asyncio extra, Alembic, pydantic-settings, httpx, PyJWT, arq), plus the approved **uvicorn** and **psycopg 3** (decisions A1, A2) and items other epics add from the OPEN-QUESTIONS.md decisions log; its dev dependency group is only ruff, mypy, **pytest, pytest-cov, pytest-asyncio, factory_boy** (A3) and **pre-commit** (A7).
- **Given** the empty `apps/api/app` package **When** `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy --strict app/` run **Then** all three exit 0.
- **Given** pytest is configured **When** `uv run pytest` runs on the skeleton **Then** it discovers `apps/api/tests/` and fails on unknown markers (`--strict-markers`).

**Tasks**
- [ ] T-1.1.2.1 (api) `pyproject.toml` with `requires-python = ">=3.12"`, `.python-version`, committed `uv.lock`
- [ ] T-1.1.2.2 (api) Ruff config (rule sets, line length, import sorting) in `pyproject.toml`
- [ ] T-1.1.2.3 (api) mypy config with `strict = true`; pytest config (test paths, strict markers, coverage source `app`, pytest-asyncio `asyncio_mode = "auto"`)

#### S-1.1.3 — TypeScript workspaces managed with pnpm

**As a** frontend developer **I want** web, mobile and the shared packages in one pnpm workspace with shared configs **so that** shared code is used directly without publishing it.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | shared, web, mobile | 0 | S-1.1.1 |

**Acceptance criteria**
- **Given** a fresh clone with the pinned Node LTS and pnpm versions (`packageManager` field and `engines`) **When** `pnpm install --frozen-lockfile` runs at the root **Then** every workspace in `apps/web`, `apps/mobile` and `packages/*` installs without errors.
- **Given** `packages/game-core`, `packages/api-client` and `packages/card-utils` placeholders (card-utils holds the shared TypeScript card helpers, decision B4) **When** they are imported with `workspace:*` from `apps/web` and `apps/mobile` **Then** `tsc --noEmit` passes in both apps.
- **Given** `tsconfig.base.json` with `strict: true` and `noUncheckedIndexedAccess: true` **When** any workspace `tsconfig.json` is checked **Then** it extends the base config and does not turn either option off.
- **Given** root scripts `pnpm lint`, `pnpm format:check`, `pnpm typecheck` and `pnpm test` **When** one workspace has an error **Then** the root script exits non-zero and names that workspace.
- **Given** the shared ESLint flat config (typescript-eslint plus the React and React Hooks plugins) and one root Prettier config (decision A6) **When** a file breaks a formatting rule **Then** Prettier reports it and ESLint does not (`eslint-config-prettier` turns off the overlapping rules).

**Tasks**
- [ ] T-1.1.3.1 (shared) `pnpm-workspace.yaml`, root `package.json` with pinned `packageManager`, `pnpm-lock.yaml`
- [ ] T-1.1.3.2 (shared) `tsconfig.base.json`, one shared ESLint flat config and one root Prettier config (with `.prettierignore` for generated code) used by all workspaces
- [ ] T-1.1.3.3 (shared) Placeholder `packages/game-core`, `packages/api-client` and `packages/card-utils` with `index.ts` and a Vitest smoke test each
- [ ] T-1.1.3.4 (shared) Root `lint`, `format`, `format:check`, `typecheck` and `test` scripts that run across all workspaces

#### S-1.1.4 — Getting-started guide and prerequisite check

**As a** new contributor **I want** a documented setup with an automatic prerequisite check **so that** I can go from clone to a running stack without asking anyone.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | infra | 0 | S-1.1.2, S-1.1.3, S-1.3.2, S-1.6.1, S-1.7.1 |

**Acceptance criteria**
- **Given** a fresh clone on Windows 11 or macOS with Docker, uv, Node and pnpm installed **When** the developer follows `docs/getting-started.md` **Then** Postgres, Redis, API, worker, the web dev server and the Expo dev server are all running within 15 minutes.
- **Given** a missing or too-old prerequisite (Docker, uv, Python, Node, pnpm) **When** the prerequisite check runs **Then** it names the tool, the required version and the version found, and exits non-zero.
- **Given** a Windows developer using PowerShell **When** they run any documented setup command **Then** it works without needing bash or WSL.

**Tasks**
- [ ] T-1.1.4.1 (infra) `docs/getting-started.md` covering install, `.env` setup, starting each app and running the verify gate
- [ ] T-1.1.4.2 (infra) Prerequisite check script written with the Python standard library only, run via `uv run`
- [ ] T-1.1.4.3 (infra) Root scripts `dev:api`, `dev:web`, `dev:mobile` documented in the guide

## F-1.2 — Project governance (CLAUDE.md, architecture doc)

Writes the project-level `MTG-Stats/CLAUDE.md` that adapts the Desktop Django rules to FastAPI, async SQLAlchemy and Alembic (plan §8.4), the architecture document, the six project agents in `.claude/agents/` (decision I3) and the one-time import of this backlog into GitHub Issues, which are the source of truth from then on (decision I2).

**Phase** 0 · **Priority** Must

#### S-1.2.1 — Project-level CLAUDE.md adapted to FastAPI

**As the** orchestrator **I want** a project CLAUDE.md that states the FastAPI layering, the verify gate and the guardrails **so that** every agent and contributor builds the code the same way.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra | 0 | S-1.1.2, S-1.1.3 |

**Acceptance criteria**
- **Given** `MTG-Stats/CLAUDE.md` **When** it is reviewed **Then** it has these sections: Commands, Workflow, Subagent delegation, Architecture rules, Conventions (Python/SQLAlchemy/Alembic and TypeScript), Code style, Guardrails, Reference docs; it is at most 200 lines, and longer material is linked with `@docs/...` rather than pasted in.
- **Given** the Architecture rules section **When** it is read **Then** it states: each domain lives in `apps/api/app/<domain>/` with `models.py`, `schemas.py`, `router.py`, `services/` and `repository.py`; `services/__init__.py` is the domain's only public interface and other domains and `workers/` import only from it; routers stay thin (parse → call service → respond); reusable queries live in `repository.py`; no SQLAlchemy ORM event listeners for business logic; configuration comes from env only through pydantic-settings; list endpoints must eager-load relationships (`selectinload`/`joinedload`).
- **Given** the verify gate in the Commands and Workflow sections **When** it is read **Then** it lists exactly: `ruff check` and `ruff format --check`, `mypy --strict`, `pytest` with `--cov-fail-under=85`, `alembic check` (migration drift), `eslint`, `prettier --check`, `tsc --noEmit`, Vitest (web and packages) and Jest with jest-expo (mobile), and says every story ends with all of them green.
- **Given** the Guardrails section **When** it is read **Then** it includes: never run `alembic upgrade`/`downgrade` or any destructive command against a non-dev database; never edit an applied migration; data migrations have a real `downgrade` or an explicit no-op with a comment explaining why; never commit secrets, `.env` files or coverage artifacts; never bypass the pre-commit hooks (`--no-verify`); never push directly to `main` (convention, decision I1); ask before adding any dependency not listed in plan §8 or the OPEN-QUESTIONS.md decisions log; the game event log is append-only and corrections are compensating events; every drag-and-drop interaction has a tap or keyboard alternative; if a spec or acceptance criterion looks wrong, flag it instead of silently changing it.
- **Given** the Conventions section **When** it is read **Then** it states: primary keys are UUID v7 (decision B1); list endpoints use cursor paging (B2); database access is async SQLAlchemy over psycopg 3 (A2); GitHub Issues are the source of truth for backlog work (I2).
- **Given** a clean clone after setup **When** each command in the Commands section is run **Then** each exits 0 (checked once and recorded in the PR description).

**Tasks**
- [ ] T-1.2.1.1 (infra) Map each Desktop rule to its FastAPI equivalent (managers → `repository.py`, signals → ORM events, `makemigrations --check` → `alembic check`, `select_related` → `selectinload`/`joinedload`, settings split → pydantic-settings with `APP_ENV`) and record the mapping in the PR
- [ ] T-1.2.1.2 (infra) Write the Commands section with exact `uv`, `pnpm`, `docker compose` and `alembic` commands
- [ ] T-1.2.1.3 (infra) Write the Conventions: string columns not nullable with default `""`; `DateTime(timezone=True)` and `datetime.now(UTC)` only; Alembic rules; game-core JSON test vectors as the one allowed JSON test data
- [ ] T-1.2.1.4 (infra) Write the Workflow: story issue on GitHub → confirm acceptance criteria → implement → verify gate → tick the story's tasks in the issue → note behavior changes in the PR

#### S-1.2.2 — Subagent roles for the project

**As the** orchestrator **I want** project agent definitions for the API, web and mobile work **so that** design, implementation, testing and review stay separated across all three apps.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra | 0 | S-1.2.1 |

**Acceptance criteria**
- **Given** `.claude/agents/` in the repo **When** it is listed **Then** it contains exactly the six agents from decision I3: `api-architect` (plan and design docs only, no code), `api-backend` (models, schemas, routers, services, repositories, migrations, workers), `frontend-web` (React web app in `apps/web`), `frontend-mobile` (Expo app in `apps/mobile`), `test-engineer` (tests written from acceptance criteria, not from the implementation) and `code-reviewer` (reviews diffs against CLAUDE.md and the story).
- **Given** `frontend-web` and `frontend-mobile` **When** their definitions are read **Then** each may change its own app and the shared `packages/` (`api-client`, `game-core`, `card-utils`), never `apps/api`, and each says the TanStack Query hooks are written by hand (decision A4).
- **Given** any agent definition **When** it is read **Then** it states its scope, the tools it may use, and that it never changes the plan, the backlog issues' acceptance criteria or CLAUDE.md.
- **Given** the Subagent delegation section of CLAUDE.md **When** it is read **Then** it names the same six agents, says which agent owns each story platform (`api`, `web`, `mobile`, `shared`, `infra`) and says independent stories may be fanned out in parallel.

**Tasks**
- [ ] T-1.2.2.1 (infra) Write the `api-architect`, `api-backend`, `test-engineer` and `code-reviewer` definitions, adapted from the Desktop roles
- [ ] T-1.2.2.2 (infra) Cross-reference them from CLAUDE.md
- [ ] T-1.2.2.3 (infra) Write the `frontend-web` and `frontend-mobile` definitions (React Router and Vitest on web; Expo Router and jest-expo on mobile)

#### S-1.2.3 — Architecture document and decision records

**As a** contributor **I want** one architecture document and a decision log **so that** I can see how the apps fit together and why key choices were made.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | infra | 0 | S-1.2.1 |

**Acceptance criteria**
- **Given** `docs/architecture.md` **When** it is read **Then** it covers: the app and package map (plan §3), the backend layering, the domain list (cards, decks, games, players, stats, collection, leagues), the real-time game flow (event log, Redis pub/sub, sequence numbers, reconnect and replay), background jobs and the domain model (plan §4).
- **Given** the domain dependency section **When** it is read **Then** it lists the allowed cross-domain service calls (for example `games` → `decks.services`) and states that cycles are not allowed.
- **Given** `docs/adr/` **When** it is listed **Then** it has an ADR template, ADR-0001 recording the plan §8 decisions (legality scope, approved dependencies, combo detection, governance, git) and ADR-0002 recording the platform decisions of 2026-11-07 (approved dependencies A1–A16, UUID v7 keys, cursor paging, `packages/card-utils`, the link policy — every shareable link is an `https://` web URL whose page offers "Open in app" via `mtgstats://`, which is also used for in-app navigation (B6, S-1.6.7) — GitHub Free, Issues as source of truth, the six agents, and E-2 owning every auth screen).
- **Given** CLAUDE.md's Reference docs section **When** it is read **Then** it links `@docs/architecture.md` and `docs/adr/`.

**Tasks**
- [ ] T-1.2.3.1 (infra) Write `docs/architecture.md` with Mermaid diagrams for layering and game flow
- [ ] T-1.2.3.2 (infra) Add `docs/adr/template.md`, ADR-0001 and ADR-0002

#### S-1.2.4 — Import the backlog into GitHub Issues

**As the** product owner **I want** the approved backlog imported once into GitHub Issues with labels, milestones and a Project board **so that** work is tracked where the code lives and Issues are the single source of truth from then on (decision I2).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | infra | 0 | S-1.1.1 |

**Acceptance criteria**
- **Given** the approved backlog **When** the import script runs **Then** every epic, feature and story exists as an issue whose title starts with its ID (for example `S-1.1.1 — …`), stories are sub-issues of their feature and features of their epic.
- **Given** the labels listed in the backlog README **When** an issue is created **Then** it carries the matching `type:`, `phase:`, `platform:`, `priority:` and `points:` labels, and each epic is in the `Phase <n>` milestone for its phase.
- **Given** the import was interrupted **When** the script runs again **Then** it creates no duplicate issues (matched by ID), only creates the missing ones and reports what it did; it never overwrites an issue that already exists.
- **Given** each epic whose phase is 2–5 **When** the import runs **Then** one "Decisions" issue is created for that epic listing its still-open questions, in the epic's milestone, to be answered before that phase starts.
- **Given** the import has finished **When** `docs/backlog/README.md` is read **Then** it states that the markdown files are the original snapshot as of the import date and that GitHub Issues are the source of truth; later changes are made only in Issues.
- **Given** the Project board **When** it is opened **Then** it has the statuses Backlog, Ready, In progress, In review and Done.

**Tasks**
- [ ] T-1.2.4.1 (infra) Create labels and milestones `Phase 0` … `Phase 5` with `gh`
- [ ] T-1.2.4.2 (infra) One-time import script (Python standard library + `gh api`) that parses the backlog markdown, creates the issues and the per-epic "Decisions" issues, and is safe to resume
- [ ] T-1.2.4.3 (infra) Create the Project board; add the snapshot notice to the backlog README and record in CLAUDE.md that Issues are authoritative

## F-1.3 — Local infrastructure (Docker Compose)

A single `infra/docker-compose.yml` that runs Postgres 16, Redis 7, the API and the worker for development, with healthchecks, persistent volumes, env-based configuration and a safe dev database reset.

**Phase** 0 · **Priority** Must

#### S-1.3.1 — Postgres and Redis in Docker Compose

**As a** developer **I want** Postgres and Redis started by one command **so that** I don't install or configure them by hand.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | infra | 0 | S-1.1.1 |

**Acceptance criteria**
- **Given** `infra/docker-compose.yml` **When** `docker compose up -d postgres redis` runs **Then** Postgres 16 and Redis 7 (pinned image tags) report healthy through their healthchecks within 30 s.
- **Given** data in the dev database **When** the stack is stopped with `docker compose down` and started again **Then** the data is still there; only `docker compose down -v` removes it.
- **Given** the Postgres container starts on an empty volume **When** initialization finishes **Then** both the dev database and a separate test database (`mtg_stats_test`) exist.
- **Given** the compose file **When** it is reviewed **Then** credentials come from `.env` (no hard-coded passwords) and ports are bound to `127.0.0.1` only.

**Tasks**
- [ ] T-1.3.1.1 (infra) `postgres` and `redis` services with pinned tags, healthchecks and named volumes
- [ ] T-1.3.1.2 (infra) Init script that creates the test database
- [ ] T-1.3.1.3 (infra) Compose variables added to `.env.example`

#### S-1.3.2 — API and worker containers for development

**As a** developer **I want** the API and worker running in Compose with hot reload **so that** the whole backend starts with one command and picks up my changes.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra, api | 0 | S-1.3.1, S-1.4.1, S-1.4.3, S-1.9.1 |

**Acceptance criteria**
- **Given** `docker compose up` **When** the stack starts **Then** `api` and `worker` start only after Postgres and Redis are healthy.
- **Given** an empty dev database **When** the stack starts with `APP_ENV=dev` **Then** `alembic upgrade head` runs once before the API accepts requests; with any other `APP_ENV` the migration step refuses to run.
- **Given** the running API **When** a Python file under `apps/api/app` is saved **Then** the API reloads within 5 s without rebuilding the image, and the worker restarts on the next `docker compose restart worker`.
- **Given** the stack is up **When** `GET http://localhost:8000/api/v1/health/ready` is called **Then** it returns 200 with database and Redis both `ok`.

**Tasks**
- [ ] T-1.3.2.1 (infra) `apps/api/Dockerfile` with a `dev` target built from `uv.lock`
- [ ] T-1.3.2.2 (infra) `api` service with a source bind mount, started with `uvicorn --reload` (decision A1)
- [ ] T-1.3.2.3 (infra) `worker` service using the same image with the arq command
- [ ] T-1.3.2.4 (infra) One-shot `migrate` service guarded to `APP_ENV=dev`

#### S-1.3.3 — Environment variables and secrets hygiene

**As a** developer **I want** one documented list of env variables and a guard against committed secrets **so that** setup is predictable and secrets never reach the repo.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | infra | 0 | S-1.3.1 |

**Acceptance criteria**
- **Given** `.env.example` **When** it is read **Then** it lists every variable the API, worker, web and mobile apps read, each with a one-line comment and either a safe dev default or `CHANGE_ME`.
- **Given** a tracked file named `.env` or `.env.<anything>` other than `.env.example` **When** the secrets check runs (pre-commit hook, verify command and CI) **Then** it fails and names the file.
- **Given** a staged file containing a private key block, a connection string with an inline password (`://user:pass@`) or a value assigned to a variable whose name ends in `_SECRET`, `_KEY`, `_TOKEN` or `_PASSWORD` other than `CHANGE_ME` or an empty string **When** the secrets check runs **Then** it fails and names the file and line, without printing the value; a line ending in `# secrets-check: allow` is skipped.
- **Given** a required variable is missing **When** the API or worker starts **Then** it stops with an error naming the variable (see S-1.4.1).

**Tasks**
- [ ] T-1.3.3.1 (infra) Write `.env.example` grouped by app
- [ ] T-1.3.3.2 (infra) Secrets check script (standard library) wired into the verify command and the pre-commit hooks (S-1.8.7)
- [ ] T-1.3.3.3 (infra) Tests for each pattern above and for the allow comment

#### S-1.3.4 — Reset and seed the dev database

**As a** developer **I want** one command that resets and seeds my local database **so that** I can get back to a known state quickly and never by accident on a real database.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | infra, api | 0 | S-1.3.2, S-1.4.3 |

**Acceptance criteria**
- **Given** `APP_ENV=dev` and a database host of `localhost`, `127.0.0.1` or the Compose `postgres` service **When** the reset command runs **Then** the dev database is dropped, recreated, migrated to head and seeded within 60 s.
- **Given** `APP_ENV` is not `dev`, or the database host is not in that allow-list **When** the reset command runs **Then** it refuses, touches nothing and exits non-zero with the reason.
- **Given** domains register seed functions **When** seeding runs **Then** each one is called through its domain service; with none registered, seeding is a no-op that reports "0 seeders".

**Tasks**
- [ ] T-1.3.4.1 (api) Reset command (`argparse`, run via `uv run`) with the env and host guard
- [ ] T-1.3.4.2 (api) Seed registry that domains add to through their `services/__init__.py`
- [ ] T-1.3.4.3 (api) Tests for the refusal cases

## F-1.4 — API skeleton

The FastAPI application foundation: env-based settings, database session and base model, Alembic with a drift check, health endpoints, the error envelope, request IDs, the per-domain package layout with enforced import boundaries, and list-endpoint conventions.

**Phase** 0 · **Priority** Must

#### S-1.4.1 — App factory and environment settings

**As a** backend developer **I want** an app factory with typed settings read only from the environment **so that** the same code runs in dev, test and prod without code changes.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 0 | S-1.1.2 |

**Acceptance criteria**
- **Given** `app/core/config.py` **When** settings load **Then** every value comes from env variables (or `.env` in dev only), `APP_ENV` is one of `dev`, `test`, `prod`, and no secret has a default value in code.
- **Given** a required setting such as `DATABASE_URL` is missing or malformed **When** the app starts **Then** startup fails with a validation error naming that variable.
- **Given** `create_app()` **When** the app is built **Then** all routers are mounted under `/api/v1` and the OpenAPI spec is served at `/api/v1/openapi.json`.
- **Given** `APP_ENV=prod` **When** a request arrives from an origin not in `CORS_ALLOWED_ORIGINS` **Then** no CORS headers are returned; in `dev`, the web dev server origin is allowed. Allowed origins get `Access-Control-Allow-Credentials: true` so the web app can send the HttpOnly refresh cookie (decision B3), and a wildcard origin is rejected at startup.

**Tasks**
- [ ] T-1.4.1.1 (api) Settings class with pydantic-settings and `APP_ENV` profiles
- [ ] T-1.4.1.2 (api) `create_app()` factory, `/api/v1` prefix and CORS middleware
- [ ] T-1.4.1.3 (api) Tests for missing, malformed and per-environment settings

#### S-1.4.2 — Database session and base model

**As a** backend developer **I want** a standard async session lifecycle and base model **so that** every domain gets transactions, IDs, timestamps and constraint names right without repeating code.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 0 | S-1.4.1, S-1.3.1 |

**Acceptance criteria**
- **Given** a route that depends on the session dependency **When** the handler succeeds **Then** the transaction commits; **When** it raises **Then** the transaction rolls back; in both cases the `AsyncSession` is closed after the request. The engine is SQLAlchemy's async engine over psycopg 3 (`postgresql+psycopg://`, decision A2), and Alembic uses the same driver.
- **Given** the declarative `Base` **When** Alembic autogenerates a migration **Then** primary key, foreign key, unique, check and index names follow one naming convention and are identical on every run.
- **Given** a model using the ID mixin **When** a row is inserted without an ID **Then** its primary key is a UUID v7 generated by the application (decision B1), stored in a Postgres `uuid` column, and IDs created one after another sort in creation order.
- **Given** a model using the timestamp mixin **When** a row is inserted or updated **Then** `created_at` and `updated_at` are `timestamptz` values in UTC, and assigning a naive datetime raises an error.
- **Given** two async tests that insert rows **When** they run in any order against `mtg_stats_test` **Then** neither sees the other's rows.
- **Given** a factory_boy factory for a model (decision A3) **When** a test creates an instance through the shared async helper **Then** the row is added to the test's session and rolled back with it.

**Tasks**
- [ ] T-1.4.2.1 (api) `app/core/db.py`: async engine, `async_sessionmaker` and request-scoped session dependency
- [ ] T-1.4.2.2 (api) `Base` with naming conventions; UUID v7 ID mixin and timestamp mixin
- [ ] T-1.4.2.3 (api) `conftest.py` with pytest-asyncio fixtures and per-test transaction rollback against the test database
- [ ] T-1.4.2.4 (api) UUID v7 generator in `app/core/ids.py` (standard library only, since Python's `uuid.uuid7` arrives in 3.14 and Postgres 16 has no `uuidv7()`), with ordering and version-bit tests
- [ ] T-1.4.2.5 (api) factory_boy base factory (build strategy) plus an async `create` helper that adds to the test session

#### S-1.4.3 — Alembic migrations and drift check

**As a** backend developer **I want** Alembic set up with a drift check **so that** models and migrations never silently diverge.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 0 | S-1.4.2 |

**Acceptance criteria**
- **Given** an empty database **When** `alembic upgrade head` runs, then `alembic downgrade base`, then `upgrade head` again **Then** all three succeed (round-trip test in pytest).
- **Given** models and migrations in sync **When** `alembic check` runs **Then** it exits 0; **Given** a model change without a migration **Then** it exits non-zero.
- **Given** `migrations/env.py` **When** it runs **Then** the database URL comes from the settings class (not `alembic.ini`), and every domain's models are loaded from one registry module so autogenerate sees all tables.
- **Given** a new migration **When** it is generated **Then** its file name follows `YYYYMMDD_HHMM_<rev>_<slug>.py`.

**Tasks**
- [ ] T-1.4.3.1 (api) Alembic init, `env.py` reading settings, file name template
- [ ] T-1.4.3.2 (api) Model registry module imported by `env.py`
- [ ] T-1.4.3.3 (api) Round-trip migration test

#### S-1.4.4 — Health endpoints

**As an** operator **I want** liveness and readiness endpoints **so that** Compose, CI and the future host can tell whether the API is up and connected.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 1 | api | 0 | S-1.4.2 |

**Acceptance criteria**
- **Given** the API is running **When** `GET /api/v1/health/live` is called **Then** it returns 200 `{"status": "ok"}` without contacting Postgres or Redis.
- **Given** Postgres and Redis are reachable **When** `GET /api/v1/health/ready` is called **Then** it returns 200 with `checks.database = "ok"`, `checks.redis = "ok"` and the build version (git SHA from env, `"dev"` locally).
- **Given** Postgres or Redis is down **When** `GET /api/v1/health/ready` is called **Then** it returns 503 within 3 s, with the failing check set to `"error"`; each check times out after 2 s.

**Tasks**
- [ ] T-1.4.4.1 (api) Health router in a `system` domain, the reference example of the domain layout; Redis ping through redis-py's asyncio client (decision A15)
- [ ] T-1.4.4.2 (api) Tests with Postgres and Redis simulated as down

#### S-1.4.5 — Error envelope and request IDs

**As a** client developer **I want** every error in one JSON shape with a request ID **so that** web and mobile can show useful messages and bugs can be traced to server logs.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 0 | S-1.4.1 |

**Acceptance criteria**
- **Given** any error response (422, 401, 403, 404, 409, 429 or 500) **When** it is returned **Then** the body is `{"error": {"code": "<snake_case>", "message": "<text>", "details": <object or null>, "request_id": "<id>"}}`.
- **Given** a request body that fails Pydantic validation **When** it is processed **Then** the status is 422, `code` is `validation_error` and `details` lists each failing field path with its message.
- **Given** an unhandled exception **When** it is processed **Then** the status is 500, `code` is `internal_error`, the body contains no stack trace or exception text, and the full traceback is logged with the same `request_id`.
- **Given** a request with an `X-Request-ID` header of at most 128 safe characters **When** it is processed **Then** that ID is used; otherwise a new one is generated; either way it is returned in the `X-Request-ID` response header and included in every log line for the request.
- **Given** any log line from the API or worker **When** it is written **Then** it is one JSON object with a UTC timestamp, level, logger, message and `request_id` (or `job_id`), plus `game_id` whenever a domain has set it in the shared logging context (structured JSON logs, decision A17).
- **Given** a domain service rejects an operation **When** it raises a subclass of `DomainError` (code + HTTP status) **Then** the envelope uses that code and status; services never raise FastAPI `HTTPException`.

**Tasks**
- [ ] T-1.4.5.1 (api) `app/core/errors.py`: `DomainError` hierarchy and exception handlers
- [ ] T-1.4.5.2 (api) Request-ID middleware, a `contextvars` logging context (`request_id`, `job_id`, `game_id`) and a JSON log formatter (standard library `logging`)
- [ ] T-1.4.5.3 (api) Error envelope schema declared in OpenAPI so the client can type it
- [ ] T-1.4.5.4 (api) Tests for each status code above

#### S-1.4.6 — Domain package layout and import boundaries

**As the** orchestrator **I want** the domain layout enforced by a test **so that** routers stay thin and domains only talk through their service interfaces.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 0 | S-1.4.4 |

**Acceptance criteria**
- **Given** the domain template in `docs/` **When** a new domain is created from it **Then** it has `models.py`, `schemas.py`, `router.py`, `services/__init__.py`, `repository.py` and a `tests/` folder.
- **Given** a module in domain A or in `app/workers/` **When** it imports `app.<B>.models`, `app.<B>.repository` or any `app.<B>.services.<submodule>` (anything other than `app.<B>.services`) **Then** the boundary test fails and names the file and the import.
- **Given** a `router.py` **When** it imports its own domain's `repository` or a SQLAlchemy query API **Then** the boundary test fails.

**Tasks**
- [ ] T-1.4.6.1 (api) Domain template doc, with the `system` domain as the worked example
- [ ] T-1.4.6.2 (api) Boundary test that parses imports with the standard library `ast` module

#### S-1.4.7 — List endpoint conventions

**As a** client developer **I want** every list endpoint to page and load data the same way **so that** clients reuse one paging helper and lists stay fast as data grows.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api | 0 | S-1.4.2 |

**Acceptance criteria**
- **Given** a list endpoint **When** it is called without paging parameters **Then** it returns at most 50 items in a shared `Page[T]` shape `{"items": [...], "next_cursor": "<opaque string or null>"}` (cursor/keyset paging, decision B2); a `limit` above 200 is rejected with 422.
- **Given** a `next_cursor` from one page **When** the next page is requested with `cursor=<value>` **Then** the items continue after the last item of the previous page in the endpoint's sort order, with the UUID v7 `id` as the tie-breaker, and no item that existed at the first call is repeated or skipped, even when rows are inserted between the two calls.
- **Given** the last page **When** it is returned **Then** `next_cursor` is `null`; **Given** a cursor that is malformed or was made for a different sort **When** it is sent **Then** the response is 422 with `code = "invalid_cursor"`.
- **Given** a test helper that counts SQL statements **When** a list endpoint is called with 5 items and then with 50 **Then** the statement count is the same (no N+1 queries).

**Tasks**
- [ ] T-1.4.7.1 (api) Generic `Page[T]` schema and paging parameter dependency
- [ ] T-1.4.7.2 (api) SQL statement-count test helper in `conftest.py`
- [ ] T-1.4.7.3 (api) Keyset paging helper in `app/core/paging.py`: opaque base64url cursor encoding the sort key and `id`, with tests for inserts between pages and invalid cursors

## F-1.5 — Shared API client generation

Generates the TypeScript types in `packages/api-client` from the FastAPI OpenAPI spec with **openapi-typescript** and wraps them in an **openapi-fetch** client (decision A4), adds auth handling and the conventions for hand-written TanStack Query hooks shared by web and mobile, and fails CI when the client drifts from the API.

**Phase** 0 · **Priority** Must

#### S-1.5.1 — Export the OpenAPI spec without a running server

**As a** developer **I want** the OpenAPI spec exported by a script **so that** client generation and CI don't need a running API.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 1 | api | 0 | S-1.4.1 |

**Acceptance criteria**
- **Given** the API code **When** the export script runs twice **Then** it writes `packages/api-client/openapi.json` and both files are byte-identical (sorted keys, stable formatting).
- **Given** two routes with the same `operationId` **When** the API test suite runs **Then** a test fails naming both routes; operation IDs follow `<domain>_<function>`.

**Tasks**
- [ ] T-1.5.1.1 (api) Export script run via `uv run`
- [ ] T-1.5.1.2 (api) Operation ID generator and uniqueness test

#### S-1.5.2 — Generate TypeScript types and client

**As a** frontend developer **I want** typed calls for every API operation **so that** an API change shows up as a type error instead of a runtime bug.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | shared | 0 | S-1.5.1, S-1.1.3 |

**Acceptance criteria**
- **Given** an exported spec **When** `pnpm --filter @mtg-stats/api-client generate` runs **Then** openapi-typescript writes the `paths` and `components` types to `packages/api-client/src/generated/schema.d.ts`, and the package exports an openapi-fetch client typed with those `paths`, so every operation's path, parameters, body and response are type-checked.
- **Given** a field is removed from a response schema **When** the client is regenerated **Then** web code that reads that field fails `tsc --noEmit`.
- **Given** the API returns an error envelope **When** a client call fails **Then** it throws an `ApiError` with `status`, `code`, `message`, `details` and `requestId`.
- **Given** the client **When** it is bundled by Vite and by Metro **Then** both builds succeed (no Node-only APIs; openapi-fetch uses the platform `fetch`).

**Tasks**
- [ ] T-1.5.2.1 (shared) Generation script running openapi-typescript on `openapi.json`; generated file excluded from ESLint and Prettier
- [ ] T-1.5.2.2 (shared) `ApiError` class and response unwrapping around the openapi-fetch client
- [ ] T-1.5.2.3 (shared) Vitest tests for success, error envelope and network failure

#### S-1.5.3 — Client configuration, auth header and token refresh

**As a** player **I want** my session to refresh quietly in the background **so that** I'm not logged out in the middle of a game.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | shared | 0 | S-1.5.2, S-2.1.5 |

**Acceptance criteria**
- **Given** a client created with a base URL and a token store supplied by the app **When** a request is sent while an access token exists **Then** it carries `Authorization: Bearer <token>`.
- **Given** a request returns 401 because the access token expired **When** a refresh token is available **Then** the client refreshes once and retries the original request once; 3 requests failing at the same time cause exactly 1 refresh call.
- **Given** the refresh call fails **When** the client handles it **Then** it clears the token store, calls the app's `onSessionExpired` callback and does not retry again.
- **Given** web and mobile store tokens differently (decision B3: on web the access token lives in memory and the 30-day refresh token in an HttpOnly cookie; on mobile both live in secure storage) **When** each creates the client **Then** neither needs to change code inside `packages/api-client`: a token store that has no refresh token makes the client call the refresh endpoint with `credentials: "include"` and no body, and one that has a refresh token sends it in the body.

**Tasks**
- [ ] T-1.5.3.1 (shared) `createApiClient({ baseUrl, tokenStore, onSessionExpired })`, with auth as openapi-fetch middleware
- [ ] T-1.5.3.2 (shared) Single-flight refresh with retry-once logic, in cookie mode and body mode
- [ ] T-1.5.3.3 (shared) Vitest tests for concurrent 401s and failed refresh

#### S-1.5.4 — TanStack Query helpers and hand-written hook conventions

**As a** frontend developer **I want** shared query keys, retry rules and a pattern for writing hooks **so that** web and mobile cache and refetch data the same way.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | shared | 0 | S-1.5.2 |

**Acceptance criteria**
- **Given** the query key factory **When** a key is built for a domain operation **Then** it has the shape `[domain, operation, params]`, and invalidating `[domain]` invalidates every query in that domain.
- **Given** a query fails with an `ApiError` of status 400–499 **When** TanStack Query handles it **Then** it is not retried; a 5xx or network error is retried at most 2 times.
- **Given** a domain needs data hooks **When** they are added **Then** they are written by hand in `packages/api-client/src/hooks/<domain>.ts` on top of the openapi-fetch client and the key factory (decision A4); no hook-generation tool is added, and the `system` health hook is the worked example.
- **Given** a list endpoint returning `Page[T]` (S-1.4.7) **When** a hook uses the shared infinite-query options helper **Then** it passes `next_cursor` as the next page parameter and reports no further pages when it is `null`.

**Tasks**
- [ ] T-1.5.4.1 (shared) Query key factory and default `QueryClient` options (TanStack Query as a peer dependency)
- [ ] T-1.5.4.2 (shared) Vitest tests for key shape and retry rules
- [ ] T-1.5.4.3 (shared) Cursor-paging infinite-query helper, the example health hook and a short "writing hooks" section in the package README

#### S-1.5.5 — Client drift check

**As a** reviewer **I want** CI to fail when the API changes without regenerating the client **so that** web and mobile never ship against stale types.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 1 | shared, infra | 0 | S-1.5.2 |

**Acceptance criteria**
- **Given** a branch that changes an API schema without regenerating the client **When** the drift check runs **Then** it exits non-zero and prints the changed files.
- **Given** a branch with a freshly regenerated client **When** the drift check runs **Then** it exits 0.

**Tasks**
- [ ] T-1.5.5.1 (shared) Drift check script: export spec, regenerate, compare with the committed files
- [ ] T-1.5.5.2 (infra) Add it to the verify command (S-1.8.1)

## F-1.6 — Web app shell

The React + Vite + TypeScript app with React Router (decision A5), responsive layout, app-wide providers, error handling, the auth guard and protected routes, the public pages behind shareable `https://` links with their "Open in app" button (decision B6), a theme and accessibility baseline, and the Fan Content Policy and Scryfall notices (decision I8) the feature epics build on. The sign-in, invite registration and password reset screens belong to E-2 (S-2.1.6).

**Phase** 0 · **Priority** Must

#### S-1.6.1 — Vite React TypeScript app

**As a** frontend developer **I want** a working web app scaffold **so that** feature work starts from a running, linted, tested app.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | web | 0 | S-1.1.3 |

**Acceptance criteria**
- **Given** the repo **When** `pnpm --filter web dev` runs **Then** the app serves on `http://localhost:5173` and shows the app shell.
- **Given** the repo **When** `pnpm --filter web build` runs **Then** it produces a production bundle with no type errors, and it fails if `VITE_API_BASE_URL` is not set.
- **Given** the web workspace **When** `eslint`, `prettier --check`, `tsc --noEmit` and Vitest run **Then** all pass, including at least one render smoke test written with Testing Library in a jsdom environment (decision A6).

**Tasks**
- [ ] T-1.6.1.1 (web) Vite React TS scaffold extending the shared tsconfig, ESLint and Prettier configs
- [ ] T-1.6.1.2 (web) Typed env access with a build-time check for required variables
- [ ] T-1.6.1.3 (web) Vitest setup with jsdom, `@testing-library/react` and `@testing-library/jest-dom`; render smoke test

#### S-1.6.2 — Routing, layout and navigation

**As a** user **I want** clear navigation that works on phone and desktop browsers **so that** I can reach every part of the app.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web | 0 | S-1.6.1 |

**Acceptance criteria**
- **Given** the router **When** the app loads **Then** the routes `/sign-in`, `/`, `/games`, `/decks`, `/stats`, `/collection` and `/leagues` exist (placeholders for features not built yet; the `/sign-in` page itself comes from S-2.1.6), and any other path shows a 404 page with a link home.
- **Given** a viewport of 1024 px or wider **When** the layout renders **Then** navigation is a sidebar; below 768 px it is a compact menu; at 360 px wide there is no horizontal scroll.
- **Given** a keyboard-only user **When** they press Tab through the navigation **Then** each item gets a visible focus ring and Enter opens it.
- **Given** a browser opened directly at `/decks` **When** the page loads **Then** the decks placeholder renders (no 404 from the dev server).

**Tasks**
- [ ] T-1.6.2.1 (web) React Router setup (decision A5) with a root layout route and nested placeholder routes
- [ ] T-1.6.2.2 (web) Responsive layout and navigation
- [ ] T-1.6.2.3 (web) Placeholder pages and 404 page

#### S-1.6.3 — App providers and error handling

**As a** user **I want** errors shown as clear messages instead of blank screens **so that** I know what went wrong and what to do.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | web | 0 | S-1.6.1, S-1.5.4 |

**Acceptance criteria**
- **Given** the app root **When** it renders **Then** it provides a TanStack `QueryClient` with the S-1.5.4 defaults and a Zustand store for session and UI state.
- **Given** a component throws during render **When** the error boundary catches it **Then** a fallback with a "Reload" button is shown, plus the `request_id` when the error is an `ApiError`.
- **Given** a mutation fails with an `ApiError` **When** the error is shown **Then** the user sees the envelope's `message` in a toast or inline message, never raw JSON.

**Tasks**
- [ ] T-1.6.3.1 (web) Providers component with `QueryClient` and the Zustand session store
- [ ] T-1.6.3.2 (web) Error boundary and toast component (no new dependency)
- [ ] T-1.6.3.3 (web) Vitest tests for the boundary fallback and the toast message

#### S-1.6.4 — Auth guard and protected routes on web

**As a** playgroup member **I want** private pages kept behind sign-in and to be sent back to the page I wanted **so that** private data stays protected without losing my place. Only the guard is built here: the sign-in page, the session store, session restore, sign-out and the session-expired handling are E-2's (S-2.1.6, S-2.1.12).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 1 | web | 0 | S-1.6.2, S-1.6.3, S-1.5.3 |

**Acceptance criteria**
- **Given** a signed-out user **When** they open `/decks` **Then** the guard redirects them to `/sign-in?next=/decks`, using only a "signed in or not" check from the session interface E-2's store implements.
- **Given** the public routes (`/sign-in`, `/about`, and the `/invite/*`, `/reset/*` and `/join/*` pages behind shareable links, S-1.6.7) **When** a signed-out visitor opens them **Then** the guard lets them through.
- **Given** a session that is still being restored on page load **When** a protected route renders **Then** the guard shows a loading state instead of redirecting, so a reload never bounces a signed-in user to `/sign-in`.

**Tasks**
- [ ] T-1.6.4.1 (web) Drop the login page from this story: `/sign-in` stays a placeholder route until S-2.1.6 provides the page (decision: E-2 owns every auth screen)
- [ ] T-1.6.4.2 (web) Route guard with the `next` redirect, the public-route list and the restoring state
- [ ] T-1.6.4.3 (web) Session interface (`status: restoring | signed_in | signed_out`) that E-2's session store implements
- [ ] T-1.6.4.4 (web) Vitest tests for redirect, public routes and the restoring state, using a fake session

#### S-1.6.5 — Theme and accessibility baseline

**As a** user **I want** a readable theme that follows my system setting **so that** the app is comfortable at a dim game table and usable by everyone.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | web | 0 | S-1.6.2 |

**Acceptance criteria**
- **Given** the system is in dark mode **When** the app loads for the first time **Then** the dark theme is used; a manual light/dark toggle overrides it and is remembered on that device.
- **Given** any text in either theme **When** its contrast is measured **Then** it is at least 4.5:1 against its background (3:1 for large text).
- **Given** a viewport below 768 px **When** interactive controls render **Then** each tap target is at least 44 × 44 px.
- **Given** the user has `prefers-reduced-motion` set **When** animations would play **Then** they are disabled or reduced to fades.

**Tasks**
- [ ] T-1.6.5.1 (web) Color tokens as CSS custom properties for both themes
- [ ] T-1.6.5.2 (web) Theme toggle stored in the Zustand store and persisted locally
- [ ] T-1.6.5.3 (web) Contrast check of the token pairs documented in the PR

#### S-1.6.6 — Fan Content Policy disclaimer and Scryfall credit on web

**As the** project owner **I want** the Wizards of the Coast Fan Content Policy disclaimer and a Scryfall credit on every page **so that** the app meets the terms its card data and images are used under (decision I8).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | web, shared | 0 | S-1.6.2 |

**Acceptance criteria**
- **Given** any page, signed in or not **When** it renders **Then** the footer shows: "MTG Stats is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC." with a link to the Fan Content Policy, and "Card data and images courtesy of Scryfall" with a link to scryfall.com.
- **Given** the `/about` route **When** it is opened (no sign-in needed) **Then** it shows the app version, the full disclaimer, the Scryfall credit and a note that card images are loaded from Scryfall.
- **Given** the notice text **When** web and mobile show it **Then** both read it from one module in `packages/card-utils`, so the wording can't drift between apps.
- **Given** a 360 px wide viewport **When** the footer renders **Then** the text wraps without horizontal scroll and meets the S-1.6.5 contrast rule.

**Tasks**
- [ ] T-1.6.6.1 (shared) `attribution.ts` in `packages/card-utils` with the disclaimer, the credit and their link URLs
- [ ] T-1.6.6.2 (web) Footer in the root layout and the `/about` page
- [ ] T-1.6.6.3 (web) Vitest test that the footer renders on public and protected routes

#### S-1.6.7 — Shareable https links with an "Open in app" button

**As a** player **I want** every link I share (invite, password reset, game join, the game QR code) to be a normal web address **so that** it opens for anyone, with or without the app, and still takes app users into the app (decision B6).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api, web | 0 | S-1.4.1, S-1.6.2, S-1.7.2 |

**Acceptance criteria**
- **Given** the `PUBLIC_WEB_URL` setting (required; must be `https://` when `APP_ENV=prod`) **When** a domain service calls `public_link(kind, value)` from `app/core/links.py` with `kind` one of `invite`, `reset` or `join` **Then** it returns `<PUBLIC_WEB_URL>/<kind>/<value>` with the value URL-encoded. E-2 (invite and reset links) and E-6 (join links and QR code contents) build shareable links only through this helper, and no API response offers a `mtgstats://` link for sharing.
- **Given** the shared `OpenInAppButton` component **When** the invite, reset and join pages (S-2.1.6, S-2.1.9, S-6.2.3) render it on a mobile browser **Then** it links to `mtgstats://<kind>/<value>`, the same path the mobile app handles (S-1.7.2); on a desktop browser it is hidden, and the web page stays fully usable either way.
- **Given** the `/invite/*`, `/reset/*` and `/join/*` paths **When** the router is configured **Then** they are public (S-1.6.4), so a signed-out visitor reaches the page.
- **Given** in-app navigation on mobile **When** one screen links to another **Then** it uses `mtgstats://` paths, never the `https://` form. Universal and App Links replace the button in Phase 5 (S-11.5.6).

**Tasks**
- [ ] T-1.6.7.1 (api) `PUBLIC_WEB_URL` setting and `public_link()` in `app/core/links.py`, with tests for each kind, encoding and the prod `https` rule
- [ ] T-1.6.7.2 (web) Shared `OpenInAppButton` component with a Vitest test for each kind and for desktop hiding

## F-1.7 — Mobile app shell

The Expo React Native app inside the pnpm monorepo, with Expo Router navigation and the `mtgstats://` scheme used for in-app navigation and the web pages' "Open in app" button (decisions A5, B6), the auth guard and secure session storage with expo-secure-store (A10; the sign-in screens are E-2's, S-2.1.7), per-environment configuration, an About screen with the Fan Content Policy and Scryfall notices (I8), and Gesture Handler and Reanimated ready for the game table.

**Phase** 0 · **Priority** Must

#### S-1.7.1 — Expo app in the monorepo

**As a** mobile developer **I want** an Expo app that uses the shared workspace packages **so that** game logic and API types are shared with web without copying.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | mobile | 0 | S-1.1.3 |

**Acceptance criteria**
- **Given** the repo **When** `pnpm --filter mobile start` runs **Then** the Expo dev server starts and the app opens on an iOS simulator or device and an Android emulator or device, showing the shell without red-screen errors.
- **Given** a change saved in `packages/game-core` **When** the app is running **Then** Metro picks it up and the app reloads with the change.
- **Given** Gesture Handler and Reanimated are installed **When** the hidden dev-only gesture test screen is opened **Then** a box can be dragged and released on both platforms without errors, and a "Move" button moves the same box by tap, the pattern every later drag interaction must follow.
- **Given** the mobile workspace **When** `eslint`, `prettier --check`, `tsc --noEmit` and Jest run **Then** all pass, including at least one render smoke test written with React Native Testing Library under the jest-expo preset (decision A6).

**Tasks**
- [ ] T-1.7.1.1 (mobile) Expo TypeScript app extending the shared tsconfig, ESLint and Prettier configs
- [ ] T-1.7.1.2 (mobile) Metro and pnpm settings for the monorepo (watch folders, module resolution, hoisting where React Native needs it)
- [ ] T-1.7.1.3 (mobile) Gesture Handler and Reanimated setup with the dev-only test screen
- [ ] T-1.7.1.4 (mobile) Jest setup with the jest-expo preset and `@testing-library/react-native`; render smoke test

#### S-1.7.2 — Navigation shell

**As a** player **I want** simple tab navigation **so that** I can get to a game, my decks or stats in one tap.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | mobile | 0 | S-1.7.1 |

**Acceptance criteria**
- **Given** a signed-in user **When** the app opens **Then** it shows bottom tabs Play, Decks, Stats and More (placeholders for features not built yet); a signed-out user sees the sign-in stack instead (its screens come from S-2.1.7; a placeholder until then).
- **Given** Android **When** the hardware back button is pressed inside a stack **Then** it goes back one screen, and it exits the app only from a root tab.
- **Given** the link `mtgstats://join/<code>` (the MVP deep-link scheme, decision B6) **When** it is opened on the device **Then** the app opens a Join placeholder screen showing the code (used later by F-6.2); **Given** `mtgstats://invite/<token>` **When** it is opened **Then** the app opens an Invite placeholder screen showing the token (used later by F-2.1). These are the targets of the web pages' "Open in app" button (S-1.6.7); shared links themselves are `https://`. Universal and app links are Phase 5 (S-11.5.6).
- **Given** a screen reader is on **When** the user moves through the tabs and buttons **Then** each has an accessibility label, and each tap target is at least 44 × 44 pt.

**Tasks**
- [ ] T-1.7.2.1 (mobile) Expo Router file-based routes in `apps/mobile/app/` (decision A5): auth group and tab layout
- [ ] T-1.7.2.2 (mobile) `scheme: "mtgstats"` in `app.config.ts`; `join/[code]` and `invite/[token]` placeholder routes
- [ ] T-1.7.2.3 (mobile) Jest tests for signed-in and signed-out navigation and for both deep links

#### S-1.7.3 — Auth guard on mobile

**As a** player **I want** the app to show the sign-in stack only when I'm signed out, and to take me where a link pointed once I sign in **so that** I can join a table quickly. Only the guard is built here: the sign-in screens, the secure session store, restore on launch, sign-out and the session-expired handling are E-2's (S-2.1.7, S-2.1.12).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | mobile | 0 | S-1.7.2, S-1.5.3 |

**Acceptance criteria**
- **Given** the session interface E-2's secure session store implements **When** its status is `signed_out` **Then** the root layout shows the sign-in stack; `signed_in` shows the tabs; `restoring` shows the splash screen, so a relaunch never flashes the sign-in stack for a signed-in user.
- **Given** a `mtgstats://` link to a protected screen (S-1.7.2) opened while signed out **When** the status later becomes `signed_in` **Then** the guard opens the screen the link pointed to; links to the Invite and Reset screens open while signed out.
- **Given** the guard **When** it is reviewed **Then** it stores no tokens itself; token storage stays in E-2's store on expo-secure-store (decision A10).

**Tasks**
- [ ] T-1.7.3.1 (mobile) Drop the login screen from this story: the sign-in stack stays a placeholder until S-2.1.7 provides its screens (decision: E-2 owns every auth screen)
- [ ] T-1.7.3.2 (mobile) Root-layout guard on the session interface (`restoring | signed_in | signed_out`) with the pending deep link
- [ ] T-1.7.3.3 (mobile) Public-route list for the Invite and Reset screens
- [ ] T-1.7.3.4 (mobile) Jest tests for each status, the pending deep link and the public routes, using a fake session

#### S-1.7.4 — Per-environment configuration for devices

**As a** mobile developer **I want** the API address set per environment **so that** a physical phone on my network can reach my local API, and later builds reach the hosted one.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | mobile | 0 | S-1.7.1, S-1.7.5 |

**Acceptance criteria**
- **Given** `EXPO_PUBLIC_API_BASE_URL` set to the dev machine's LAN address **When** the app runs on a physical phone on the same network **Then** it calls `GET /api/v1/health/ready` successfully.
- **Given** the variable is missing **When** the app starts **Then** it shows a configuration error screen naming the variable instead of failing silently.
- **Given** a dev build **When** the About screen (S-1.7.5) is opened **Then** it also shows the build profile and API base URL; release builds hide the API URL.

**Tasks**
- [ ] T-1.7.4.1 (mobile) `app.config.ts` reading env per profile
- [ ] T-1.7.4.2 (mobile) Build profile and API URL on the About screen; config error screen
- [ ] T-1.7.4.3 (mobile) Getting-started section on testing with a physical device

#### S-1.7.5 — About screen with the Fan Content Policy disclaimer and Scryfall credit

**As the** project owner **I want** the mobile app to show the Wizards of the Coast Fan Content Policy disclaimer and a Scryfall credit **so that** the app meets the terms its card data and images are used under (decision I8).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 1 | mobile | 0 | S-1.7.2, S-1.6.6 |

**Acceptance criteria**
- **Given** the More tab **When** the user opens About **Then** it shows the app version, the full Fan Content Policy disclaimer and "Card data and images courtesy of Scryfall", read from the shared module in `packages/card-utils` (S-1.6.6), with the same wording as the web footer.
- **Given** the links to the Fan Content Policy and scryfall.com **When** they are tapped **Then** they open in the system browser.
- **Given** a signed-out user in the sign-in stack (S-1.7.2) **When** it renders **Then** it has an "About" link to the same screen, so the notices are reachable while signed out.

**Tasks**
- [ ] T-1.7.5.1 (mobile) About route under More, plus the link from the sign-in stack
- [ ] T-1.7.5.2 (mobile) Jest test that the notice text matches the shared module

## F-1.8 — CI & verify gate

One local verify command, pre-commit hooks (decision A7) and GitHub Actions workflows that run the full verify gate (ruff, mypy --strict, pytest at ≥ 85% coverage, Alembic drift, eslint, Prettier, tsc, Vitest, Jest, client drift) on every pull request, Playwright and Maestro smoke tests (A8), and pull request conventions. The repo is private on GitHub Free, so branch protection can't be enforced; green checks before merging are a documented convention, and secret scanning comes from the pre-commit hook (decision I1).

**Phase** 0 · **Priority** Must

#### S-1.8.1 — Local verify command

**As a** developer **I want** one command that runs the same checks as CI **so that** I find failures before pushing.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | infra | 0 | S-1.1.2, S-1.1.3, S-1.4.3, S-1.5.5, S-1.3.3 |

**Acceptance criteria**
- **Given** the repo root **When** `pnpm verify` runs **Then** it runs, in order: secrets check, `ruff check`, `ruff format --check`, `mypy --strict app/`, `pytest --cov=app --cov-fail-under=85`, `alembic check`, client drift check, `eslint`, `prettier --check`, `tsc --noEmit`, Vitest and Jest.
- **Given** any step fails **When** `pnpm verify` runs **Then** it stops, exits non-zero and prints the name of the failed step.
- **Given** `pnpm verify:api` or `pnpm verify:ts` **When** run **Then** only the Python or only the TypeScript steps run.
- **Given** Windows PowerShell, macOS or Linux **When** `pnpm verify` runs with the Compose stack up **Then** it behaves the same on each.

**Tasks**
- [ ] T-1.8.1.1 (infra) Root `verify`, `verify:api` and `verify:ts` scripts
- [ ] T-1.8.1.2 (infra) Document the command in CLAUDE.md and getting-started

#### S-1.8.2 — GitHub Actions: Python job

**As a** reviewer **I want** the Python checks to run on every pull request against real Postgres and Redis **so that** nothing merges with failing API code.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra, api | 0 | S-1.8.1 |

**Acceptance criteria**
- **Given** a pull request or a push to `main` **When** the workflow runs **Then** the `api` job runs the secrets check (S-1.3.3) on every file in the branch, `ruff check`, `ruff format --check`, `mypy --strict`, `pytest` with coverage and the Alembic drift check and round-trip, against Postgres 16 and Redis 7 service containers.
- **Given** total coverage below 85% **When** the job runs **Then** it fails and the job summary shows the coverage figure.
- **Given** an unchanged `uv.lock` **When** the job runs a second time **Then** the uv cache is restored and the job finishes in 5 minutes or less.
- **Given** the job finishes **When** the repo is inspected **Then** no coverage file has been committed; the report is a workflow artifact kept 7 days.

**Tasks**
- [ ] T-1.8.2.1 (infra) `.github/workflows/ci.yml` with the `api` job and service containers
- [ ] T-1.8.2.2 (infra) uv cache, coverage summary and artifact upload

#### S-1.8.3 — GitHub Actions: TypeScript job

**As a** reviewer **I want** the web, mobile and package checks to run on every pull request **so that** nothing merges with type errors or failing tests.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra, web, mobile, shared | 0 | S-1.8.1 |

**Acceptance criteria**
- **Given** a pull request or a push to `main` **When** the workflow runs **Then** the `ts` job runs in parallel with the `api` job and runs `pnpm install --frozen-lockfile`, `eslint`, `prettier --check`, `tsc --noEmit`, Vitest, Jest, the web production build and the client drift check.
- **Given** any of these steps fails **When** the job ends **Then** the job fails and the failed step is named in the summary.
- **Given** an unchanged `pnpm-lock.yaml` **When** the job runs a second time **Then** the pnpm store cache is restored and the job finishes in 6 minutes or less.

**Tasks**
- [ ] T-1.8.3.1 (infra) `ts` job in `ci.yml` with pnpm store cache
- [ ] T-1.8.3.2 (infra) Client drift step (needs Python, so it sets up uv as well)

#### S-1.8.4 — Required-checks convention and pull request conventions

**As the** project owner **I want** the CI checks treated as required by documented convention, and pull requests in one format **so that** every change is reviewed and passes the gate before merging, even though GitHub Free can't enforce branch protection on a private repo (decision I1).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | infra | 0 | S-1.8.2, S-1.8.3 |

**Acceptance criteria**
- **Given** a pull request or a push to `main` **When** CI runs **Then** the `api` and `ts` checks run and report their status on the pull request and the commit.
- **Given** `CONTRIBUTING.md` and the Guardrails section of CLAUDE.md **When** they are read **Then** they state the convention: all work goes through a pull request; merge only when `api` and `ts` are green and `code-reviewer` has reviewed the diff; no direct pushes, force-pushes or deletion of `main`; and they say plainly that GitHub does not enforce this on the current plan.
- **Given** a commit lands on `main` that is not part of a merged pull request, or a pull request is merged while a required check was red **When** the push workflow runs **Then** it fails and opens a GitHub issue titled `Process bypass: <short SHA>` naming the commit, so a bypass is visible rather than silent.
- **Given** a new pull request **When** it is opened **Then** the template asks for: the story ID, a verify-gate checklist (including "pre-commit hooks ran, no `--no-verify`"), behavior changes, migration notes and screenshots for UI changes.

**Tasks**
- [ ] T-1.8.4.1 (infra) Document the required-checks convention in `CONTRIBUTING.md` and CLAUDE.md
- [ ] T-1.8.4.2 (infra) `.github/pull_request_template.md`
- [ ] T-1.8.4.3 (infra) Push-to-`main` bypass check using `gh api` (commit → associated pull request → check runs)

#### S-1.8.5 — Web end-to-end smoke tests with Playwright

**As a** reviewer **I want** a short browser test of the main path **so that** wiring between web, client and API is checked, not only units.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web, infra | 0 | S-1.8.3, S-1.6.4, S-1.6.6, S-1.3.2, S-2.1.6 |

**Acceptance criteria**
- **Given** Playwright (approved, decision A8) in an `apps/web/e2e/` suite **When** `pnpm --filter web e2e` runs locally against the Compose stack **Then** the same suite runs as in CI, in Chromium.
- **Given** the Compose stack running in CI with a seeded test account **When** the suite runs **Then** it signs in with username and password, opens each top-level route, checks the footer notice (S-1.6.6), signs out, and finishes in 10 minutes or less.
- **Given** a test fails **When** the job ends **Then** Playwright traces and screenshots are uploaded as workflow artifacts kept 7 days.
- **Given** the suite **When** it is written **Then** it targets elements by role and label (no CSS selectors), so it also checks the accessibility baseline.

**Tasks**
- [ ] T-1.8.5.1 (web) Playwright config and smoke suite for sign-in, navigation and sign-out
- [ ] T-1.8.5.2 (infra) `e2e` job that starts the Compose stack, seeds the test account and caches the Playwright browser

#### S-1.8.6 — Mobile end-to-end smoke tests with Maestro

**As a** mobile developer **I want** an automated smoke test on a real build **so that** native wiring problems show up before testers install the app.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | mobile, infra | 0 | S-1.7.3, S-1.7.5, S-1.8.3, S-2.1.7 |

**Acceptance criteria**
- **Given** Maestro (approved, decision A8) flows in `apps/mobile/e2e/` **When** a developer runs them against a local emulator build **Then** the same flows run as in CI.
- **Given** an Android emulator build of the app pointing at the CI Compose stack **When** the suite runs nightly **Then** it signs in, switches through all four tabs, opens About (S-1.7.5), opens a `mtgstats://join/<code>` link and sees the Join screen, and signs out.
- **Given** a nightly run fails **When** the workflow ends **Then** the failure is visible on the Actions page with Maestro screenshots attached.

**Tasks**
- [ ] T-1.8.6.1 (mobile) Maestro flows for sign-in, tabs, About, deep link and sign-out
- [ ] T-1.8.6.2 (infra) Nightly workflow with an Android emulator

#### S-1.8.7 — Pre-commit hooks with secret scanning

**As a** developer **I want** fast checks and a secret scan to run before every commit **so that** formatting slips and leaked secrets are stopped on my machine, which on GitHub Free is the only place secret scanning happens (decisions A7, I1).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | infra | 0 | S-1.1.2, S-1.1.3, S-1.3.3 |

**Acceptance criteria**
- **Given** a fresh clone **When** the developer runs the documented setup (`uv run pre-commit install`) **Then** the hooks from `.pre-commit-config.yaml` run on every `git commit`, on Windows PowerShell, macOS and Linux.
- **Given** staged files **When** the hooks run **Then** they run, on staged files only: the secrets check (S-1.3.3), `detect-private-key`, `check-added-large-files` (500 KB), `end-of-file-fixer`, `ruff check --fix` and `ruff format` on Python, and Prettier and ESLint on TypeScript; the full run on a typical commit takes 15 s or less.
- **Given** a staged `.env` file or a staged line with a secret pattern **When** `git commit` runs **Then** the commit is refused and the file and line are named, without printing the secret.
- **Given** a hook was skipped with `--no-verify` **When** the pull request's CI runs **Then** the same secrets check in the `api` job (S-1.8.2) still fails the branch.
- **Given** `pre-commit run --all-files` **When** it runs on `main` **Then** it exits 0.

**Tasks**
- [ ] T-1.8.7.1 (infra) `.pre-commit-config.yaml` with pinned hook revisions; the secrets check, ESLint and Prettier as local hooks so they use the repo's own versions
- [ ] T-1.8.7.2 (infra) Document `uv run pre-commit install` as a required setup step in the root README
- [ ] T-1.8.7.3 (infra) Record the hook list and the "no `--no-verify`" rule in CLAUDE.md

## F-1.9 — Background worker

The arq worker process on Redis with conventions for enqueuing, retries, timeouts, idempotency and cron schedules, plus a run history so failed or stale jobs are visible. Consumers come later: Scryfall sync (F-3.1), price snapshots (F-3.4) and deck imports (F-4.2, F-4.3).

**Phase** 0 · **Priority** Must

#### S-1.9.1 — arq worker process

**As a** backend developer **I want** a worker process with a simple enqueue helper **so that** slow work runs outside web requests.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 0 | S-1.4.1, S-1.4.6 |

**Acceptance criteria**
- **Given** the arq worker settings in `app/workers/` **When** the worker starts **Then** it connects to the Redis URL from settings and logs the names of all registered jobs.
- **Given** a service calls the `enqueue(job_name, **kwargs)` helper **When** the worker is running **Then** the job runs and its status (queued, in progress, complete, failed) can be read by job ID.
- **Given** the worker receives SIGTERM **When** a job is running **Then** it stops taking new jobs and either finishes the job within 30 s or leaves it to be retried.
- **Given** a job function **When** it is tested with pytest **Then** it can be called in-process without Redis, and the S-1.4.6 boundary test confirms it imports only from domain `services`.

**Tasks**
- [ ] T-1.9.1.1 (api) `WorkerSettings`, Redis pool and job registry in `app/workers/`
- [ ] T-1.9.1.2 (api) `enqueue` helper and job-status lookup
- [ ] T-1.9.1.3 (api) Test helper that runs a job function in-process

#### S-1.9.2 — Retries, timeouts and idempotent jobs

**As a** backend developer **I want** standard retry, timeout and de-duplication rules **so that** a flaky network call or a double enqueue doesn't corrupt data.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 0 | S-1.9.1 |

**Acceptance criteria**
- **Given** a job with default settings **When** it runs longer than 10 minutes **Then** it is cancelled and marked failed; jobs may set their own timeout.
- **Given** a job raises a retryable error **When** it fails **Then** it is retried up to 3 times with growing delays; a non-retryable error is not retried.
- **Given** a job is enqueued twice with the same deterministic job ID (for example `scryfall_sync:2026-11-07`) **When** the first is still queued or running **Then** the second does not start another run.
- **Given** any job run **When** it ends **Then** one log line records job name, job ID, duration in ms and outcome.

**Tasks**
- [ ] T-1.9.2.1 (api) Retryable and non-retryable error classes and the default job wrapper
- [ ] T-1.9.2.2 (api) Deterministic job ID helper
- [ ] T-1.9.2.3 (api) Tests for timeout, retry count and de-duplication

#### S-1.9.3 — Scheduled jobs

**As a** backend developer **I want** to register jobs on a schedule **so that** daily work like the Scryfall sync runs without anyone starting it.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 0 | S-1.9.1 |

**Acceptance criteria**
- **Given** a domain registers a scheduled job with a UTC schedule **When** the worker starts **Then** the job appears in the startup log with its next run time.
- **Given** two worker processes running **When** a scheduled time arrives **Then** the job runs exactly once.
- **Given** `APP_ENV=test` or `SCHEDULER_ENABLED=false` **When** the worker starts **Then** no scheduled jobs run.
- **Given** dev **When** the worker has run for 10 minutes **Then** the sample heartbeat job (every 5 minutes) has run twice.

**Tasks**
- [ ] T-1.9.3.1 (api) Schedule registry built on arq cron jobs, with the unique-run option
- [ ] T-1.9.3.2 (api) Heartbeat job and the enable/disable setting

#### S-1.9.4 — Job run history and stale-job check

**As an** operator **I want** a record of every job run and a stale-job check **so that** I notice when the daily sync has stopped working.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | api | 0 | S-1.9.2, S-1.9.3, S-1.4.4, S-2.1.11 |

**Acceptance criteria**
- **Given** any job run **When** it ends **Then** a `job_runs` row records job name, job ID, start and end time (UTC), status and an error summary of at most 2 KB; rows older than 30 days are purged daily.
- **Given** a site admin (the global role, decision E9) **When** they call `GET /api/v1/health/jobs` **Then** it lists each scheduled job with its last successful run, and marks it `stale` when that is older than twice its interval; everyone else, including playgroup admins, gets 403.

**Tasks**
- [ ] T-1.9.4.1 (api) `job_runs` model, migration and repository
- [ ] T-1.9.4.2 (api) Recording in the job wrapper; daily purge job
- [ ] T-1.9.4.3 (api) Site-admin-only jobs health endpoint and tests

## Open questions

None open. Resolved: see OPEN-QUESTIONS.md decisions log.
