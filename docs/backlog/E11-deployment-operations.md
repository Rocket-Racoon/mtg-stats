# E-11 — Deployment & Operations

| | |
|---|---|
| **Phase(s)** | 5 |
| **Goal** | Run MTG Stats for the playgroup every week on a home server or VPS with Docker Compose, reached only through a Cloudflare Tunnel: images built on `main` and published to GitHub Container Registry, a one-step production deploy by the owner, Postgres and Redis with tested daily backups, structured logs, an admin error page and uptime alerts, Android builds through Expo internal distribution (iPhone players use the web app), and a security review before launch. |
| **Success measures** | • A merge to `main` publishes an image to GitHub Container Registry within 15 minutes, and the owner puts it live in production with one command; production is the only environment (decision I5).<br>• Daily backups are kept for 14 days, with an encrypted copy on a second disk or NAS on the local network; a restore drill has met the 24 h recovery point and 4 h restore target, and it repeats every quarter (I5).<br>• Every playgroup tester with an Android phone has installed the app from an Expo internal distribution build, and every iPhone player uses the web app from the home screen; no app store is used (I7).<br>• When `/api/v1/health/ready` or the operations check fails, the external uptime monitor alerts the owner within 10 minutes (A17). |
| **Out of scope** | App Store and Google Play releases (I7: decided after real use); a staging environment (I5); more than one production API instance or any high-availability setup (I6); Sentry or any other error-tracking or metrics service (A17); over-the-air updates with EAS Update (A19); an email provider (plan §9.4); native iOS builds and an Apple Developer account (iPhone players use the web app; Apple is revisited later, PO decision 2026-11-07); cloud storage for backups (PO decision 2026-11-07). Data export, account deletion and playgroup archive or delete are built in E-2 (decision C9: S-2.6.1, S-2.6.2, S-2.6.3); this epic only checks them in the security review and keeps restores from undoing them. |

## F-11.1 — Hosting & environments

Prepares the production server (a home server or VPS chosen by the owner, decision I4), builds the production image, and runs the whole app with Docker Compose behind a Cloudflare Tunnel: one API instance (I6), one worker, Postgres, Redis and `cloudflared`, with no open inbound ports. There is no staging environment (I5). The repo is private on GitHub Free (decision I1); production secrets live only on the server.

**Phase** 5 · **Priority** Must

#### S-11.1.1 — Production server baseline and operations decision record

**As the** project owner **I want** the hosting decisions recorded and the server prepared to one written baseline **so that** all deployment work targets one known machine.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra | 5 | — |

**Acceptance criteria**
- **Given** `docs/adr/` **When** the operations ADR is written **Then** it records decisions I4, I5, I6, I7, A17, A18 and A19: home server or VPS with Docker Compose, Cloudflare Tunnel with no open ports, one production API instance, GitHub Container Registry, production only, the backup policy and quarterly restore drill, the external uptime monitor, structured logs and the admin error page, pip-audit, no Sentry, no EAS Update and Expo internal distribution; it names which machine the owner chose and its CPU, memory and disk.
- **Given** the server **When** the baseline in `docs/operations/server.md` is checked **Then** it runs a supported Linux release with automatic security updates, Docker Engine with the Compose plugin, the clock in UTC with time sync, a non-root `deploy` user in the `docker` group, SSH by key only (no passwords, no root login), and a firewall that denies all inbound traffic except SSH from the owner's network or through the tunnel.
- **Given** a home server **When** the baseline is checked **Then** it also records the power-loss behavior (Docker and the stack start again on boot) and the expected outage handling (the uptime monitor reports it, S-11.4.4).

**Tasks**
- [ ] T-11.1.1.1 (infra) Operations ADR in `docs/adr/` recording the decisions above
- [ ] T-11.1.1.2 (infra) Server setup and the baseline checklist in `docs/operations/server.md`
- [ ] T-11.1.1.3 (infra) Firewall, automatic updates and SSH hardening applied and checked off

#### S-11.1.2 — Production container images

**As an** operator **I want** a small, non-root production image built from the lockfiles **so that** what runs in production is reproducible and has no dev tools in it.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra, api | 5 | S-1.3.2 |

**Acceptance criteria**
- **Given** the multi-stage Dockerfile **When** the `prod` target is built **Then** one image serves the API (with the web build, S-11.1.5), the worker and the one-off migration task through different start commands, contains no dev dependencies, tests or `.env` files, runs as a non-root user and is at most 450 MB.
- **Given** the same commit, `uv.lock` and `pnpm-lock.yaml` **When** the image is built twice **Then** both images install the same dependency versions.
- **Given** `APP_ENV=prod` with a required setting missing **When** the container starts **Then** it exits non-zero with the setting's name in the log (S-1.4.1).
- **Given** the built image **When** it is tagged **Then** the tag is the git SHA, and `GET /api/v1/health/ready` reports that SHA as the version.

**Tasks**
- [ ] T-11.1.2.1 (infra) `prod` stage in `apps/api/Dockerfile` with a non-root user and a Node stage that builds `apps/web`
- [ ] T-11.1.2.2 (api) Production start commands for API (uvicorn, decision A1, with proxy headers and a graceful-shutdown timeout), worker (arq) and migration task
- [ ] T-11.1.2.3 (infra) Image size and contents check in CI

#### S-11.1.3 — Production environment on Docker Compose

**As the** project owner **I want** the whole production stack defined in one Compose file **so that** the server runs exactly what the repo describes and comes back by itself after a restart.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | infra | 5 | S-11.1.1, S-11.1.2, S-11.3.1 |

**Acceptance criteria**
- **Given** `infra/docker-compose.prod.yml` **When** `docker compose -f infra/docker-compose.prod.yml up -d` runs on the server **Then** it starts exactly one `api`, one `worker`, `postgres`, `redis` and `cloudflared` (S-11.1.6), all from pinned image tags (the app image by git SHA from GitHub Container Registry), and each reports healthy within 2 minutes.
- **Given** the running stack **When** published ports are listed **Then** no service publishes a port on the host; services reach each other only on the Compose network.
- **Given** the server reboots or a container crashes **When** Docker starts again **Then** every service restarts by itself (`restart: unless-stopped`) and the app is reachable again without manual steps.
- **Given** the one API instance (decision I6) **When** it runs **Then** WebSocket fan-out still goes through Redis pub/sub (F-6.3), so a second instance later needs no code change, and container memory limits are set so one service can't starve the others.
- **Given** each container **When** its log settings are checked **Then** the Docker log driver rotates by size (at most 3 × 10 MB per container), so container logs can't fill the disk; long-term logs are S-11.4.1.

**Tasks**
- [ ] T-11.1.3.1 (infra) `infra/docker-compose.prod.yml` with healthchecks, restart policies, memory limits and log rotation
- [ ] T-11.1.3.2 (infra) Pull access to the private GitHub Container Registry package on the server (read-only token, S-11.1.4)
- [ ] T-11.1.3.3 (infra) Reboot test on the server, recorded in the PR

#### S-11.1.4 — Production configuration and secrets

**As an** operator **I want** production settings and secrets held only on the server **so that** no secret lives in the repo, an image, GitHub or a CI log.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | infra | 5 | S-11.1.3 |

**Acceptance criteria**
- **Given** the database and Redis passwords, the JWT signing key, the Cloudflare Tunnel token and the read-only registry token **When** production is configured **Then** they are stored in one env file outside the repo checkout, owned by the `deploy` user with mode `600`, and none of them is in the repo, an image or GitHub secrets; CI publishes images with the workflow's own `GITHUB_TOKEN` and holds no production credential.
- **Given** the outbound User-Agent for Scryfall and the deck sites **When** it is checked in production **Then** it is `MTGStats/<release version> (+<repo URL>)` built from the release version and a non-secret `REPO_URL` setting, and contains no personal email (decision E4).
- **Given** `docs/operations/production.md` **When** it is read **Then** it lists every production variable by name and purpose, with no values, and says where the env file lives and who can read it.
- **Given** production **When** CORS and WebSocket origin settings are checked **Then** only the production web origin (`PUBLIC_WEB_URL`, S-1.6.7) is allowed.

**Tasks**
- [ ] T-11.1.4.1 (infra) Production env file with the permissions above, and the registry login with the read-only token
- [ ] T-11.1.4.2 (infra) Write `docs/operations/production.md`

#### S-11.1.5 — Serve the web app from the API container

**As a** player **I want** the web app served quickly from the same address as the API **so that** I can open it from any browser at the table and stay signed in without cross-site cookie problems.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, infra, web | 5 | S-11.1.2, S-1.6.1 |

**Acceptance criteria**
- **Given** `SERVE_WEB=true` **When** the API starts **Then** it serves the Vite build with Starlette's built-in static files support (no new dependency), and the web app is built with the same-origin API base URL `/api/v1`.
- **Given** the production web URL **When** a browser opens a page such as `/decks/123` or `/join/ABCD` directly **Then** the app loads that page; any path outside `/api/` and `/.well-known/` that isn't a file falls back to `index.html`, while an unknown `/api/...` path still returns the JSON 404 envelope.
- **Given** a deployed build **When** response headers are checked **Then** hashed assets are cached for 1 year as `immutable` and `index.html` is served with `no-cache`.

**Tasks**
- [ ] T-11.1.5.1 (api) Static mount, SPA fallback and cache headers in `app/core/web.py`, enabled by `SERVE_WEB`, with tests
- [ ] T-11.1.5.2 (web) Production build settings for the same-origin API URL

#### S-11.1.6 — Cloudflare Tunnel and domain

**As the** project owner **I want** the app reachable at our domain only through a Cloudflare Tunnel **so that** the server has no open ports and players still get HTTPS and working WebSockets (decision I4).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra, api | 5 | S-11.1.3 |

**Acceptance criteria**
- **Given** the domain is on Cloudflare **When** the tunnel is set up **Then** the `cloudflared` service in the production stack connects outbound with the tunnel token from S-11.1.4, and its ingress routes the production hostname to the `api` service (which also serves the web app, S-11.1.5); the tunnel configuration in the repo holds no secret.
- **Given** an external port scan of the server's public address **When** it runs **Then** no app port (80, 443, 8000, 5432, 6379) is open.
- **Given** `http://<domain>` **When** it is opened **Then** it redirects to `https://` with a valid certificate managed by Cloudflare, and the minimum TLS version is 1.2.
- **Given** a game table left idle for 10 minutes **When** a player then changes a life total **Then** the WebSocket is still connected or reconnects by itself and the change is broadcast (the F-6.3 heartbeat keeps the socket under Cloudflare's idle timeout).
- **Given** a request through the tunnel **When** the API logs it or rate-limits it **Then** the client address is taken from `CF-Connecting-IP`, and only on traffic from the `cloudflared` service; uvicorn's forwarded-allow list names only that service.

**Tasks**
- [ ] T-11.1.6.1 (infra) `cloudflared` service (pinned image) and tunnel ingress configuration
- [ ] T-11.1.6.2 (infra) DNS and Cloudflare settings (Always Use HTTPS, WebSockets on, minimum TLS 1.2) in `docs/operations/cloudflare.md`
- [ ] T-11.1.6.3 (api) Real client address from `CF-Connecting-IP` with a trusted-proxy check, with tests
- [ ] T-11.1.6.4 (infra) Port scan and 10-minute idle WebSocket test, recorded in the PR

## F-11.2 — CI/CD deploy

Extends the F-1.8 GitHub Actions setup to build and publish images to GitHub Container Registry (decision I6), runs migrations as a release step, lets the owner deploy a published build to production with one command on the server, and rolls back quickly. There is no staging (I5), so checks that used to run on staging run on production right after a deploy or on a scratch copy of the stack.

**Phase** 5 · **Priority** Must

#### S-11.2.1 — Build and publish images from `main`

**As an** operator **I want** an image built once per commit on `main` **so that** production runs exactly the build that passed the verify gate.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | infra | 5 | S-11.1.2, S-1.8.2, S-1.8.3 |

**Acceptance criteria**
- **Given** a push to `main` **When** the `api` and `ts` jobs pass **Then** the production image is built and pushed to GitHub Container Registry as a private package with tags `<git SHA>` and `main`, using the workflow's `GITHUB_TOKEN` with `packages: write`.
- **Given** the verify gate fails, or the push is to any branch other than `main` **When** the workflow runs **Then** no image is built or pushed, so only verified `main` builds exist in the registry.
- **Given** only application code changed **When** the image is rebuilt **Then** the dependency layers come from cache and the build finishes in 5 minutes or less.

**Tasks**
- [ ] T-11.2.1.1 (infra) `publish.yml` build job with layer caching
- [ ] T-11.2.1.2 (infra) GitHub Container Registry package set to private and linked to the repo

#### S-11.2.2 — Migrations as a release step

**As an** operator **I want** database migrations to run once, before new code gets traffic **so that** a failed migration never leaves production half-upgraded.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra, api | 5 | S-11.2.1, S-11.3.3 |

**Acceptance criteria**
- **Given** a release **When** it is deployed **Then** `alembic upgrade head` runs exactly once in a one-off migration container (`docker compose run --rm migrate`) before the new API and worker containers start.
- **Given** the migration fails **When** the release step ends **Then** the deploy stops, the previous version keeps serving and the deploy command prints the failure.
- **Given** a migration that drops or renames a column the running version still uses **When** it is reviewed **Then** it is rejected; schema changes follow expand-then-contract so the previous version keeps working against the new schema (rule added to CLAUDE.md and the PR template).
- **Given** production database credentials with DDL rights **When** access is checked **Then** only the migration container receives them; developers never run migrations against production from their machines.

**Tasks**
- [ ] T-11.2.2.1 (infra) `migrate` service in the production Compose file using the migrator role (S-11.6.5)
- [ ] T-11.2.2.2 (infra) Expand-then-contract rule in CLAUDE.md and the PR template
- [ ] T-11.2.2.3 (api) CI test that the previous release's tests pass against the new schema

#### S-11.2.3 — One-step production deploy by the owner

**As the** project owner **I want** to put a published build live with one command **so that** the playgroup never gets a build I didn't choose, and never one that skipped the verify gate.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra | 5 | S-11.2.2, S-11.1.3, S-11.1.5, S-11.1.6 |

**Acceptance criteria**
- **Given** an image for a SHA in GitHub Container Registry **When** the owner runs `infra/deploy.sh <sha>` on the server **Then** it pulls the image, takes the pre-migration snapshot if needed (S-11.3.3), runs migrations (S-11.2.2) and recreates `api` and `worker` on the new image, all within 10 minutes.
- **Given** a SHA with no image in the registry **When** the deploy runs **Then** it stops before changing anything; only verified `main` builds can be deployed (S-11.2.1).
- **Given** who may deploy **When** access is checked **Then** only the owner's SSH key can log in as the `deploy` user, and no GitHub workflow can reach the server or holds a production credential.
- **Given** a deploy **When** it completes **Then** `/api/v1/health/ready` through the public URL returns 200 with the new SHA within 2 minutes and a WebSocket handshake to a test room succeeds; otherwise the deploy is marked failed and rollback (S-11.2.4) starts by itself.
- **Given** a deploy during a live game, with one API instance (I6) **When** the API restarts **Then** clients reconnect and replay missed events (F-6.3) with no lost events; the deploy command warns that games in progress will see a short reconnect.

**Tasks**
- [ ] T-11.2.3.1 (infra) `infra/deploy.sh`: pull, snapshot, migrate, recreate, record the deployed SHA
- [ ] T-11.2.3.2 (infra) Post-deploy health and WebSocket smoke check with automatic rollback
- [ ] T-11.2.3.3 (infra) Deploy section in `docs/operations/production.md`

#### S-11.2.4 — Rollback

**As an** operator **I want** a one-step rollback to a previous build **so that** a bad release is undone in minutes.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | infra | 5 | S-11.2.3 |

**Acceptance criteria**
- **Given** a previous image SHA **When** `infra/deploy.sh --rollback <sha>` runs **Then** API, worker and web return to that version within 5 minutes without rebuilding anything.
- **Given** a rollback **When** it runs **Then** migrations are not downgraded automatically; expand-then-contract keeps the previous version compatible with the current schema.
- **Given** `docs/operations/rollback.md` **When** it is followed on a scratch Compose project on the server, restored from the latest backup (S-11.3.4) and never on production data **Then** the rollback works, and the drill date and duration are recorded in the doc.

**Tasks**
- [ ] T-11.2.4.1 (infra) Rollback mode in `infra/deploy.sh` that takes a SHA
- [ ] T-11.2.4.2 (infra) Rollback runbook and drill on a scratch Compose project

#### S-11.2.5 — Playwright smoke tests against production after a deploy

**As the** project owner **I want** the Playwright smoke suite run against production right after a deploy **so that** broken wiring is caught before game night, now that there is no staging.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | infra, web | 5 | S-11.2.3, S-1.8.5 |

**Acceptance criteria**
- **Given** the S-1.8.5 Playwright suite (decision A8) with its base URL taken from an environment variable **When** the owner starts the `smoke.yml` workflow for a SHA after a deploy (and once a day on a schedule) **Then** it runs against the production URL with a dedicated test account, signs in, opens each top-level route and signs out, and checks that the reported version matches the SHA.
- **Given** the test account **When** its access is checked **Then** it belongs only to an empty test playgroup, so the suite reads no real player's data and writes none; its credentials are the only production values in GitHub, as `PROD_E2E_*` repository secrets.
- **Given** a run fails **When** the job ends **Then** the Playwright trace is uploaded as a workflow artifact kept 7 days, the owner is notified by GitHub, and the runbook says to roll back (S-11.2.4).

**Tasks**
- [ ] T-11.2.5.1 (infra) `smoke.yml` with `workflow_dispatch` (SHA input) and a daily schedule
- [ ] T-11.2.5.2 (infra) Production test account in an empty test playgroup; `PROD_E2E_*` repository secrets
- [ ] T-11.2.5.3 (infra) Version check step and the rollback note in the runbook

## F-11.3 — Data, backups & restore

Runs Postgres and Redis inside the production Compose stack, takes daily backups kept 14 days with a recovery point within 24 h and a 4 h restore target (decision I5), with an encrypted copy on a second disk or NAS on the local network (PO decision 2026-11-07), snapshots before migrations, proves restores work with a quarterly drill, and confirms the app tolerates losing Redis.

**Phase** 5 · **Priority** Must

#### S-11.3.1 — Postgres and Redis in the production stack

**As an** operator **I want** Postgres 16 and Redis 7 set up safely inside the production Compose stack **so that** the data services need no separate hosting and can't be reached from outside.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | infra | 5 | S-11.1.1, S-1.3.1 |

**Acceptance criteria**
- **Given** the production Compose file **When** the data services start **Then** Postgres 16 and Redis 7 use pinned tags and named volumes on the server's disk, publish no host port, and require passwords from the production env file.
- **Given** Redis holds the arq queues **When** its settings are checked **Then** `maxmemory-policy` is `noeviction` and append-only persistence is on, so queued jobs are never evicted silently.
- **Given** the Scryfall data (F-3.1) and expected game, deck and collection data **When** storage is sized **Then** at least 50% disk headroom remains after the first full sync plus 14 days of backups, and the sizing is recorded in `docs/operations/production.md`.

**Tasks**
- [ ] T-11.3.1.1 (infra) `postgres` and `redis` services in the production Compose file
- [ ] T-11.3.1.2 (infra) Redis persistence and memory policy
- [ ] T-11.3.1.3 (infra) Storage sizing after a full Scryfall sync

#### S-11.3.2 — Daily backups kept 14 days

**As the** project owner **I want** daily backups kept apart from the live database **so that** months of game history can't be lost to one failure.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra, api | 5 | S-11.3.1, S-1.9.4 |

**Acceptance criteria**
- **Given** production **When** a day passes **Then** a `backup` service using the Postgres image's own `pg_dump` takes one compressed custom-format dump, so the recovery point is at most 24 h (decision I5).
- **Given** a finished dump **When** it is stored **Then** it is written to a disk other than the one holding the Postgres volume, and an encrypted copy goes to the second disk or NAS on the local network named in `docs/operations/production.md`; no cloud bucket is used (PO decision 2026-11-07).
- **Given** the encrypted copy **When** it is made **Then** it is encrypted on the server with GnuPG from the server's base OS packages (no new tool) to the owner's public key, so only the owner, who holds the private key, can decrypt it; the server never stores that private key.
- **Given** the second disk or NAS is unreachable **When** the daily run tries to copy **Then** the local dump is still kept, the run is recorded as failed in `job_runs`, and the owner is alerted (S-11.4.4).
- **Given** backups older than 14 days **When** the daily run ends **Then** they are deleted automatically in both places, and every backup inside the 14 days is still available.
- **Given** any backup run **When** it ends **Then** it records a `db_backup` row in `job_runs` (S-1.9.4), and `db_backup` is registered as an external daily job so a failed or missing backup shows as `stale` and reaches the owner through the uptime monitor (S-11.4.4).

**Tasks**
- [ ] T-11.3.2.1 (infra) `backup` service and script: dump, local copy, GnuPG-encrypted copy to the local-network disk or NAS, 14-day pruning in both places
- [ ] T-11.3.2.2 (api) External job registration so the stale-job check covers `db_backup`
- [ ] T-11.3.2.3 (infra) Backup section in `docs/operations/production.md`: the disk or NAS target, the owner's public key, and where the owner keeps the private key for restores

#### S-11.3.3 — Snapshot before migrations

**As an** operator **I want** a fresh snapshot taken before any release that changes the schema **so that** a bad migration can be undone with no data loss.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | infra | 5 | S-11.3.2 |

**Acceptance criteria**
- **Given** a release whose Alembic head differs from production's current revision **When** the deploy starts **Then** an on-demand dump is taken with the S-11.3.2 backup script and confirmed complete before `alembic upgrade head` runs.
- **Given** a release with no new migrations **When** it deploys **Then** no extra snapshot is taken.
- **Given** the snapshot fails **When** the deploy checks it **Then** the deploy stops before migrating.

**Tasks**
- [ ] T-11.3.3.1 (infra) Revision comparison and snapshot step in `infra/deploy.sh`
- [ ] T-11.3.3.2 (infra) Snapshot naming with the release SHA, kept under the same 14-day pruning

#### S-11.3.4 — Quarterly restore drill and runbook

**As the** project owner **I want** a tested, written restore procedure that is repeated every quarter **so that** I know backups actually work before I need them.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra | 5 | S-11.3.2 |

**Acceptance criteria**
- **Given** the latest production backup **When** it is restored into a scratch Compose project on the server **Then** `alembic current` matches the production revision and row counts for `games`, `game_events`, `decks` and `collection_items` match production as of the backup time.
- **Given** `docs/operations/restore.md` **When** someone other than its author follows it **Then** they complete the restore without asking questions, within the 4 h restore target and with at most 24 h of data lost (decision I5), and the time taken is recorded.
- **Given** accounts deleted (S-2.6.2) or playgroups deleted (S-2.6.3) after the backup was taken (decision C9) **When** a backup is restored into production **Then** the runbook repeats those deletions from the deletion entries in the logs (S-11.4.1) before the app is reopened, so a restore never brings back a deleted account or playgroup.
- **Given** the restore procedure **When** it is read **Then** it never targets the production database unless the owner chooses that explicitly; **Given** a scheduled workflow **When** a quarter starts **Then** it opens a "Quarterly restore drill" GitHub issue linking the runbook (decision I5).

**Tasks**
- [ ] T-11.3.4.1 (infra) Restore into a scratch Compose project and row-count comparison script
- [ ] T-11.3.4.2 (infra) Write `docs/operations/restore.md`, including re-applying account deletions; record the first drill
- [ ] T-11.3.4.3 (infra) Quarterly scheduled workflow that opens the drill issue

#### S-11.3.5 — Survive a Redis restart

**As a** player **I want** a game to carry on if Redis restarts **so that** no life totals or damage are lost mid-game.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | api, infra | 5 | S-11.3.1, S-1.9.3, F-6.3 |

**Acceptance criteria**
- **Given** a live test game on a scratch copy of the production stack **When** Redis is restarted **Then** clients reconnect, the game state is rebuilt from the Postgres event log, and every event committed before the restart is still there.
- **Given** queued jobs are lost in the restart **When** the next scheduled time arrives **Then** scheduled jobs (Scryfall sync, price snapshots) run again normally, and `docs/operations/redis.md` lists which jobs are safe to lose and which need re-enqueuing by hand.

**Tasks**
- [ ] T-11.3.5.1 (infra) Redis restart drill on a scratch Compose project during a test game
- [ ] T-11.3.5.2 (api) Fix any gaps found and add a regression test for each
- [ ] T-11.3.5.3 (infra) Write `docs/operations/redis.md`

## F-11.4 — Observability

Uses only what decision A17 approved: structured JSON logs (with request and game IDs) kept 30 days, an admin error page listing recent 5xx errors, the Expo dashboard for mobile crashes, and an external uptime monitor that checks the health endpoints and sends the alerts (I5). Sentry and metrics tools are not used.

**Phase** 5 · **Priority** Must (logs, error page, alerts), Could (client error reports)

#### S-11.4.1 — Structured logs kept 30 days

**As an** operator **I want** production logs as JSON lines I can search by request or game ID for 30 days **so that** I can trace any error a player reports.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, infra | 5 | S-1.4.5, S-11.1.3 |

**Acceptance criteria**
- **Given** API and worker in production **When** they log **Then** every line is JSON (S-1.4.5) with a UTC timestamp, level, `request_id` or `job_id`, `game_id` when known, route or job name, status and `duration_ms`, plus `user_id` when known.
- **Given** a request with a password, token or `Authorization` header **When** it is logged at any level **Then** those values never appear; a test asserts the redaction.
- **Given** production **When** logs are written **Then** each process also writes them to a daily-rotated file on a `logs` volume with the standard library's `TimedRotatingFileHandler`, keeping 30 days (decision A17); older files are deleted by the handler.
- **Given** a `request_id` or `game_id` from a player's report **When** the owner runs `docker compose exec api python -m app.core.logsearch --request-id <id>` (or `--game-id`) **Then** every matching line from the last 30 days is printed in time order; no new logging tool is added.

**Tasks**
- [ ] T-11.4.1.1 (api) Production log fields and redaction filter, with tests
- [ ] T-11.4.1.2 (api) Rotating file handler and the `logsearch` command (standard library only), with tests
- [ ] T-11.4.1.3 (infra) `logs` volume in the production Compose file; log section in `docs/operations/production.md`

#### S-11.4.2 — Admin error page for recent server errors

**As the** project owner **I want** recent 5xx errors from the API and worker grouped on an admin page **so that** I fix bugs before the playgroup reports them, without an error-tracking service (decision A17).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web | 5 | S-11.4.1, S-1.9.2, S-2.1.11 |

**Acceptance criteria**
- **Given** an unhandled exception in the API (a 500 response) or a failed worker job **When** it happens **Then** an `error_events` row records the time, `request_id` or `job_id`, `game_id` when known, route or job name, exception type, release SHA, a fingerprint (exception type plus the innermost app frame) and a traceback cut to 8 KB, with passwords, tokens and email addresses scrubbed.
- **Given** a site admin (decision E9) **When** they open the error page in the web app's admin area **Then** it lists error groups by fingerprint, newest first, with count, first seen, last seen and release, and a group opens its recent events with their request IDs and the matching `logsearch` command (S-11.4.1).
- **Given** anyone who is not a site admin **When** they call the error endpoints **Then** they get 403.
- **Given** rows older than 30 days **When** the daily purge runs **Then** they are deleted, matching log retention.

**Tasks**
- [ ] T-11.4.2.1 (api) `error_events` model, migration and recording from the 500 handler and the job wrapper, with scrubbing and tests
- [ ] T-11.4.2.2 (api) Site-admin list and detail endpoints (cursor paging) and the daily purge
- [ ] T-11.4.2.3 (web) Admin error page with groups and events

#### S-11.4.3 — Client error reports and Expo crash checks

**As the** project owner **I want** errors in the web and mobile apps to reach the admin error page, and mobile crashes checked in the Expo dashboard **so that** I see problems players don't mention, with no new tool (decision A17).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 3 | api, web, mobile | 5 | S-11.4.2, S-11.5.1, S-1.6.3 |

**Acceptance criteria**
- **Given** an uncaught error or an error-boundary catch in web or mobile while signed in **When** it happens **Then** the app posts a report with release, platform, app version, route and (for `ApiError`) `request_id` to `POST /api/v1/client-errors`, and it appears on the admin error page grouped like server errors.
- **Given** a client sends more than 20 reports in 10 minutes **When** the next arrives **Then** it is dropped with 429 (S-11.6.2), and reports never contain tokens or form contents.
- **Given** a native mobile crash **When** the owner checks the Expo dashboard for internal builds **Then** `docs/operations/production.md` says where to look and how to match the crash to an app version; no crash-reporting SDK is added.

**Tasks**
- [ ] T-11.4.3.1 (api) `client-errors` endpoint feeding `error_events`, with tests
- [ ] T-11.4.3.2 (web) Report from the error boundary and global handlers
- [ ] T-11.4.3.3 (mobile) Report from the error boundary and global JS handler; Expo dashboard note in the docs

#### S-11.4.4 — Uptime, job and backup alerts through the uptime monitor

**As the** project owner **I want** to be alerted when the app is down, a daily job is stale, a backup fails or the disk is filling **so that** I find out before game night.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, infra | 5 | S-1.4.4, S-1.9.4, S-11.1.6, S-11.3.2 |

**Acceptance criteria**
- **Given** the approved external uptime monitor (decision A17) **When** it checks production `/api/v1/health/ready` every 5 minutes **Then** 2 failed checks in a row send the owner an alert through the monitor's own notifications, and a recovery notice when it passes again; this covers the server, the tunnel and the API being down.
- **Given** `GET /api/v1/health/ops` **When** any scheduled job (including `db_backup`, S-11.3.2) is `stale` or disk use on the data or backup volume is above 85% **Then** it returns 503 with only `status: "degraded"` and the names of the failing checks (no other data), and the monitor, which also checks this URL, alerts naming them; otherwise it returns 200.
- **Given** a backup failure **When** it happens **Then** the owner gets an alert within 10 minutes of the failed run being recorded.

**Tasks**
- [ ] T-11.4.4.1 (api) `health/ops` endpoint combining the stale-job check and disk use (standard library `shutil.disk_usage`), with tests
- [ ] T-11.4.4.2 (infra) Monitor checks for both URLs and the owner's notification settings, recorded in `docs/operations/production.md`

#### S-11.4.5 — Basic service metrics

**As an** operator **I want** a simple view of traffic, latency, errors and live games **so that** I can tell if the app is getting slow before players complain.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Won't | 3 | api, infra | 5 | S-11.4.1 |

Won't: no metrics tool is approved (decision A17); logs (S-11.4.1), the admin error page (S-11.4.2) and the uptime monitor (S-11.4.4) cover the need.

**Acceptance criteria**
- **Given** production **When** the owner opens the metrics view **Then** it shows, for the last 24 hours: request rate, p95 latency, 5xx rate, open WebSocket connections, active games and arq queue depth.
- **Given** p95 latency above 1 s or a 5xx rate above 2% for 10 minutes **When** the threshold is crossed **Then** the owner gets an alert.

**Tasks**
- [ ] T-11.4.5.1 (api) Expose the counts above in the format the chosen tool or host needs
- [ ] T-11.4.5.2 (infra) Metrics view and the two threshold alerts

## F-11.5 — Mobile distribution (EAS)

Expo EAS build profiles and **Expo internal distribution builds for Android only** that the playgroup installs directly; there are no app stores for now (decision I7) and no EAS Update (A19), so every mobile change ships as a new build and the app tells testers when they need it. **iPhone players use the web app**, added to the home screen; native iOS builds and an Apple account are out of scope for now and will be revisited later (PO decision 2026-11-07). Android App Links replace the web pages' "Open in app" button (decision B6, S-1.6.7).

**Phase** 5 · **Priority** Must

#### S-11.5.1 — EAS project and build profiles

**As a** mobile developer **I want** EAS build profiles for development and internal release **so that** each build points at the right API and can be installed the right way.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | mobile, infra | 5 | S-1.7.4, S-11.1.6 |

**Acceptance criteria**
- **Given** `eas.json` **When** it is read **Then** it has the profiles `development` (dev client, internal distribution, local API) and `internal` (release build, internal distribution, production API); there is no store profile and no EAS Update channel (decisions I7, A19).
- **Given** `eas build --profile internal --platform android` **When** it finishes **Then** it produces an APK that installs on a tester's phone and reaches the production API.
- **Given** an internal build **When** it is created **Then** its build number is higher than the previous one without anyone editing it by hand, and its bundle identifier and app name are the ones recorded in `docs/operations/mobile-release.md`.

**Tasks**
- [ ] T-11.5.1.1 (mobile) `eas.json` profiles and per-profile env values
- [ ] T-11.5.1.2 (mobile) Bundle identifiers, app name and automatic build numbering
- [ ] T-11.5.1.3 (infra) Link the Expo project to the repo and store the EAS token as a GitHub secret

#### S-11.5.2 — iOS internal testing on TestFlight

**As a** playgroup member with an iPhone **I want** to install the app from TestFlight **so that** I can use it at the table without side-loading.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Won't | 3 | mobile | 5 | S-11.5.1, S-11.5.4 |

Won't: no app stores for now (decision I7); TestFlight needs App Store Connect. iPhone players use the web app instead (S-11.5.7, PO decision 2026-11-07).

**Acceptance criteria**
- **Given** a production-profile iOS build **When** it is submitted with `eas submit` **Then** it is processed by App Store Connect and available on TestFlight.
- **Given** a playgroup member added as an internal tester **When** they accept the invite **Then** they can install and open the app and sign in against production.

**Tasks**
- [ ] T-11.5.2.1 (mobile) App Store Connect app record and internal tester group
- [ ] T-11.5.2.2 (mobile) First `eas submit` to TestFlight
- [ ] T-11.5.2.3 (mobile) Record compliance and privacy answers

#### S-11.5.3 — Android internal testing on Google Play

**As a** playgroup member with an Android phone **I want** to install the app from Play internal testing **so that** I get updates the normal way.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Won't | 2 | mobile | 5 | S-11.5.1, S-11.5.4 |

Won't: no app stores for now (decision I7). Replaced by S-11.5.7.

**Acceptance criteria**
- **Given** a production-profile Android build (AAB) **When** it is submitted with `eas submit` **Then** it is published to the Play Console internal testing track.
- **Given** a playgroup member on the tester list **When** they open the opt-in link **Then** they can install the app from Google Play and sign in against production.

**Tasks**
- [ ] T-11.5.3.1 (mobile) Play Console app, internal track and tester list
- [ ] T-11.5.3.2 (mobile) First `eas submit` to the internal track; Data safety form

#### S-11.5.4 — App icon, splash screen and name

**As a** player **I want** the app to have a proper icon and name **so that** I can find it on my phone.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 1 | mobile | 5 | S-11.5.1, S-1.7.5 |

**Acceptance criteria**
- **Given** an Android internal build **When** it is installed **Then** it shows the app icon (including the Android adaptive icon), the splash screen and the app name.
- **Given** the icon, splash and the install page text (S-11.5.7) **When** they are reviewed **Then** they use no Wizards of the Coast logos or card art, the About screen (S-1.7.5) shows the Fan Content Policy disclaimer and Scryfall credit (decision I8), and the install page text ends with the same disclaimer.

**Tasks**
- [ ] T-11.5.4.1 (mobile) Icon, adaptive icon and splash assets in `app.config.ts`
- [ ] T-11.5.4.2 (mobile) Install page text with the disclaimer; check the About screen in an internal build

#### S-11.5.5 — Mobile release from CI

**As a** mobile developer **I want** tagged releases built by CI **so that** testers get new builds without manual steps on my laptop.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | infra, mobile | 5 | S-11.5.7 |

**Acceptance criteria**
- **Given** a git tag matching `mobile-v*` on a commit that passed the verify gate **When** it is pushed **Then** CI starts an EAS `internal` build for Android only (no iOS builds, PO decision 2026-11-07) and posts the install links in a GitHub release for the tag; nothing is submitted to a store.
- **Given** a build fails **When** the workflow ends **Then** the workflow fails and links to the EAS build log.
- **Given** the mobile app must match the API it talks to **When** a mobile release is tagged **Then** the workflow checks that production's API version is the same as or newer than the tagged commit's, and stops otherwise.

**Tasks**
- [ ] T-11.5.5.1 (infra) `mobile-release.yml` triggered by `mobile-v*` tags
- [ ] T-11.5.5.2 (infra) API version compatibility check against production `/health/ready`

#### S-11.5.6 — Android App Links for invites, resets and joining

**As a** player **I want** an invite, reset or game link to open straight in the app when it is installed, and on the web otherwise **so that** I no longer need the "Open in app" button (decision B6).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | mobile, web, infra | 5 | S-11.1.5, S-11.5.1, S-1.7.2, S-1.6.7, F-2.1, F-6.2 |

**Acceptance criteria**
- **Given** the production domain **When** `/.well-known/assetlinks.json` is requested **Then** it is served as `application/json` without redirects or Cloudflare challenges and lists only the `/invite/*`, `/reset/*` and `/join/*` paths for the Android internal build (package name and the EAS signing-certificate fingerprint from S-11.5.1). No `apple-app-site-association` file is served, because there is no iOS build (PO decision 2026-11-07).
- **Given** the Android app is installed **When** the user taps `https://<production domain>/invite/<token>`, `/reset/<token>` or `/join/<code>` in a message or browser **Then** the app opens on the matching screen with that token or code.
- **Given** the app is not installed, or the user is on an iPhone **When** the same link is tapped **Then** the web page opens as before; links have been `https://` since the MVP (S-1.6.7), so no link format changes.
- **Given** `assetlinks.json` verifies on Android **When** the invite, reset and join web pages render **Then** the "Open in app" button is removed; the `mtgstats://` scheme stays registered for in-app navigation and older builds.

**Tasks**
- [ ] T-11.5.6.1 (infra) Serve `assetlinks.json` from the API container (S-11.1.5) and exclude `/.well-known/*` from Cloudflare challenges
- [ ] T-11.5.6.2 (mobile) `intentFilters` with `autoVerify` (Android) in `app.config.ts`; no iOS `associatedDomains` (PO decision 2026-11-07)
- [ ] T-11.5.6.3 (mobile) Map `https` invite, reset and join paths to the existing Expo Router routes; Maestro flow for the links
- [ ] T-11.5.6.4 (web) Remove the "Open in app" button from the invite, reset and join pages (S-1.6.7) once `assetlinks.json` verifies

#### S-11.5.7 — Install the app from Expo internal distribution builds

**As a** playgroup member **I want** to install the app from a link the owner sends me on Android, or add the web app to my home screen on an iPhone **so that** I can use it at the table without an app store (decision I7).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | mobile, web, infra | 5 | S-11.5.1, S-11.1.5 |

**Acceptance criteria**
- **Given** an `internal` Android build **When** a tester opens its EAS install link or QR code on their phone **Then** they can install the APK, open the app and sign in against production, following the steps (including allowing installs from this source) in `docs/operations/mobile-release.md`.
- **Given** an iPhone player **When** they open the production web app in Safari and choose "Add to Home Screen" **Then** it is added with the app icon and name and opens the web app full screen at sign-in; there is no iOS build, device registration or Apple account (PO decision 2026-11-07), and the steps are in `docs/operations/mobile-release.md`.
- **Given** the EAS free build quota **When** builds are planned **Then** `docs/operations/mobile-release.md` records the quota and the expected builds per month, and the owner is warned in that doc when the plan would exceed it.
- **Given** a new internal build **When** it is published **Then** the install link is shared with the playgroup through the GitHub release (S-11.5.5) or by the owner directly.

**Tasks**
- [ ] T-11.5.7.1 (mobile) First Android internal build and install on a tester's phone
- [ ] T-11.5.7.2 (infra) Write `docs/operations/mobile-release.md` (Android install steps, iPhone "Add to Home Screen" steps, quota)
- [ ] T-11.5.7.3 (mobile) Won't: iOS ad hoc build for registered devices. iPhone players use the web app; Apple is revisited later (PO decision 2026-11-07)
- [ ] T-11.5.7.4 (web) Web app manifest, `apple-touch-icon` and `apple-mobile-web-app` meta tags so the web app opens full screen from the iPhone home screen

#### S-11.5.8 — Tell testers when a newer build is required

**As a** player on Android **I want** the app to tell me when a newer build is available or required **so that** I'm not left on an old version, since there are no store updates and no over-the-air updates (decisions I7, A19).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, mobile | 5 | S-11.5.7, S-1.4.1 |

**Acceptance criteria**
- **Given** the settings `MOBILE_LATEST_BUILD`, `MOBILE_MIN_BUILD` and `MOBILE_INSTALL_URL` **When** the app calls `GET /api/v1/meta/mobile` (no sign-in needed) **Then** it gets those three values.
- **Given** the app's build number is below `MOBILE_MIN_BUILD` **When** it starts or returns to the foreground **Then** it shows a blocking "Update required" screen with a button that opens the install link; below `MOBILE_LATEST_BUILD` only, it shows a dismissible banner once per build.
- **Given** the call fails or the app is offline **When** it starts **Then** the check is skipped and the app works normally.
- **Given** an iPhone player **When** they use the web app **Then** no build check applies, because the web app is always current after a deploy; the check covers Android builds only (PO decision 2026-11-07).

**Tasks**
- [ ] T-11.5.8.1 (api) Settings and the `meta/mobile` endpoint, with tests
- [ ] T-11.5.8.2 (mobile) Version check on start and foreground, update screen and banner (Android builds), with Jest tests

## F-11.6 — Security hardening

Hardens transport and HTTP headers, extends rate limiting to the remaining sensitive endpoints, sets up secret rotation and dependency monitoring with pip-audit (decision A18), applies least-privilege database roles and finishes with a security review before the playgroup moves to production.

**Phase** 5 · **Priority** Must

#### S-11.6.1 — HTTP, CORS and WebSocket hardening

**As the** project owner **I want** the app locked to HTTPS with strict headers and origins **so that** common browser attacks don't work against us.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api, web, infra | 5 | S-11.1.5, S-11.1.6, S-1.6.6 |

**Acceptance criteria**
- **Given** production responses (API and web, both served by the API container) **When** headers are checked **Then** they include HSTS with a max-age of at least 180 days, `X-Content-Type-Options: nosniff`, a `Referrer-Policy`, and a Content Security Policy that disallows inline scripts and framing by other sites, and whose `img-src` allows Scryfall's image hosts (`cards.scryfall.io`, `svgs.scryfall.io`) because card images are hotlinked from Scryfall (decisions E4, I8).
- **Given** the production web app **When** any page is opened **Then** the Fan Content Policy disclaimer and Scryfall credit (S-1.6.6) are present, and the header scan recorded in the PR shows Scryfall images loading without CSP violations.
- **Given** a request from any origin other than the production web origin **When** it reaches the API **Then** no CORS headers are returned.
- **Given** a WebSocket handshake **When** its `Origin` is not allowed, or it has no valid user token or guest join token (F-2.3, F-6.2) **Then** the connection is refused before joining any room.

**Tasks**
- [ ] T-11.6.1.1 (api) Security headers middleware and WebSocket origin and token check, with tests
- [ ] T-11.6.1.2 (infra) Cloudflare HSTS and HTTPS settings; record a header scan in the PR

#### S-11.6.2 — Rate limiting on the remaining sensitive endpoints

**As the** project owner **I want** token refresh, deck URL imports and client error reports rate-limited, on top of the sign-in throttling from S-2.1.10 **so that** outside sites don't block us for abuse and nobody floods the API.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 5 | S-2.1.10, S-11.1.6, F-4.2, F-4.3 |

**Acceptance criteria**
- **Given** the Redis fixed-window limiter from S-2.1.10 **When** a user starts more than 10 deck URL imports in one minute **Then** the 11th is rejected with 429, `code = "rate_limited"` and a `Retry-After` header.
- **Given** one client address **When** it calls token refresh more than 30 times, or sends more than 20 client error reports (S-11.4.3), in 10 minutes **Then** further calls get 429.
- **Given** traffic through the Cloudflare Tunnel **When** IP-based limits are counted **Then** they use the real client address from S-11.1.6, never the `cloudflared` container's address.
- **Given** an API restart **When** limits are checked again **Then** the counters are still there (Redis), and every limit can be changed through env variables without a code change.

**Tasks**
- [ ] T-11.6.2.1 (api) Apply the S-2.1.10 limiter to refresh, deck URL imports and client error reports
- [ ] T-11.6.2.2 (api) Tests for each limit, `Retry-After` and the real client address

#### S-11.6.3 — Signing key rotation and secret leaks

**As an** operator **I want** to rotate the JWT signing key without logging everyone out, and a plan for leaked secrets **so that** a leak is cheap to fix.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, infra | 5 | S-11.1.4, S-1.8.7, F-2.1 |

**Acceptance criteria**
- **Given** a current and a previous signing key, each with a key ID (`kid`) **When** the key is rotated **Then** tokens signed with the previous key stay valid until they expire, and new tokens use the new key.
- **Given** `docs/operations/secrets.md` **When** it is followed **Then** it rotates the JWT key, the database and Redis passwords, the Cloudflare Tunnel token, the registry read token and the `PROD_E2E_*` test account, with the expected downtime written down for each (zero for the JWT key).
- **Given** GitHub secret scanning and push protection aren't available on a private repo on GitHub Free (decision I1) **When** a secret is staged for commit **Then** the pre-commit hook (S-1.8.7) refuses the commit; **When** the hook was bypassed and the secret reaches a branch **Then** the CI secrets check fails the run, and `docs/operations/secrets.md` says to treat the secret as leaked and start the rotation runbook even after the commit is removed.
- **Given** the production secret names **When** the secrets check patterns are reviewed **Then** each one would be caught, and a test covers each pattern.

**Tasks**
- [ ] T-11.6.3.1 (api) Multiple signing keys with `kid` in the F-2.1 token service, with tests
- [ ] T-11.6.3.2 (infra) Write the rotation runbook and test it on a scratch Compose project
- [ ] T-11.6.3.3 (infra) Extend the S-1.3.3 secrets check with the production secret patterns, and add the "leaked secret" steps to the runbook

#### S-11.6.4 — Dependency vulnerability monitoring

**As the** project owner **I want** to be told about vulnerable dependencies and get update pull requests **so that** known security holes don't stay in the app.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | infra | 5 | S-1.8.2, S-1.8.3 |

**Acceptance criteria**
- **Given** Dependabot (built into GitHub) **When** it is configured **Then** it raises security alerts and opens weekly grouped update pull requests for uv, pnpm, GitHub Actions and Docker base images (including the pinned `postgres`, `redis` and `cloudflared` images).
- **Given** the `api` CI job **When** pip-audit (decision A18, in the dev dependency group) checks the production dependencies exported from `uv.lock` **Then** any known vulnerability not listed in the exceptions file fails the job.
- **Given** the `ts` CI job **When** `pnpm audit --prod` finds a critical vulnerability **Then** the job fails.
- **Given** a critical or high vulnerability in a production dependency **When** it is reported **Then** it is fixed, or an exception with a reason and an expiry date is written in `docs/operations/security.md`, within 7 days.

**Tasks**
- [ ] T-11.6.4.1 (infra) `.github/dependabot.yml` with groups per ecosystem
- [ ] T-11.6.4.2 (infra) `pnpm audit` step in the `ts` job; exceptions section in `docs/operations/security.md`
- [ ] T-11.6.4.3 (infra) pip-audit step in the `api` job with its exceptions file

#### S-11.6.5 — Least-privilege database access

**As an** operator **I want** the running app to use a database role that cannot change the schema **so that** a bug or injection can't drop tables.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | infra, api | 5 | S-11.3.1, S-11.2.2 |

**Acceptance criteria**
- **Given** production Postgres **When** roles are checked **Then** the API and worker connect as an `app` role with data read/write only (no DDL, not superuser), only the migration container uses the `migrator` role, and the backup uses a read-only role.
- **Given** the `app` role **When** it runs `DROP TABLE` or `ALTER TABLE` **Then** Postgres refuses it (checked once in production before launch and recorded).
- **Given** a new table created by a migration **When** the migration runs **Then** the `app` and backup roles automatically get their access to it (default privileges), so no deploy breaks.

**Tasks**
- [ ] T-11.6.5.1 (infra) Create `app`, `migrator` and backup roles with default privileges
- [ ] T-11.6.5.2 (infra) Point the runtime, migration and backup containers at their roles; record the check

#### S-11.6.6 — Security review before launch

**As the** project owner **I want** a structured security review before the playgroup moves onto production **so that** private game and collection data is protected.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web, mobile, infra | 5 | S-11.6.1, S-11.6.2, S-11.6.3, S-11.6.4, S-11.6.5, S-2.6.1, S-2.6.2, S-2.6.3 |

**Acceptance criteria**
- **Given** a checklist based on OWASP ASVS Level 1 (auth, sessions, access control, input validation, WebSockets, secrets) plus the server baseline (S-11.1.1: no open inbound ports, SSH by key only, env file permissions) **When** the review is done **Then** every item is marked pass, fail or not applicable, with evidence, in `docs/operations/security.md`.
- **Given** a user in playgroup A **When** they request games, decks, stats or collection items of playgroup B or another user by guessing IDs, over REST or WebSocket **Then** they get 403 or 404; automated tests cover each domain's endpoints.
- **Given** a guest player token (F-2.3) **When** it is used outside the game it was issued for, or after it expires **Then** it is rejected.
- **Given** the data-rights stories (decision C9: S-2.6.1 export your own data, S-2.6.2 delete your account with past seats shown as "Deleted player", S-2.6.3 the owner archiving or deleting a playgroup) **When** they are reviewed **Then** an export contains only the requester's own data, a deleted account leaves no personal data outside the 14-day backups, and the result is recorded in the checklist.
- **Given** the review findings **When** production launches **Then** no High or Critical finding is open; others are tracked as issues.

**Tasks**
- [ ] T-11.6.6.1 (infra) ASVS L1 and server checklist and review
- [ ] T-11.6.6.2 (api) Cross-playgroup and cross-user access tests for every domain and the WebSocket rooms
- [ ] T-11.6.6.3 (infra) File issues for findings and record sign-off

## Open questions

None open. Resolved: see OPEN-QUESTIONS.md decisions log.
