# ADR-0002: Platform decisions

- **Status**: accepted
- **Date**: 2026-11-07
- **Deciders**: product owner, orchestrator
- **Related**: decisions log A1–A19, B1–B8, I1–I8 and "Auth screens" ([OPEN-QUESTIONS.md](../backlog/OPEN-QUESTIONS.md)); stories S-1.6.7, S-1.2.2, S-2.1.6/S-2.1.7

## Context

Before Phase 0, the backlog needed concrete choices for the runtime, tooling, IDs, paging, links, repository hosting, the work-tracking source of truth, the agent roles and who owns the auth screens.

## Decision

**Approved dependencies, A1–A16 (plus A18 and the uptime monitor)**

| Area | Approved |
|---|---|
| Python runtime | uvicorn; **psycopg 3** with **async** SQLAlchemy; argon2-cffi; ijson; qrcode; redis-py |
| Python test | pytest, pytest-cov, pytest-asyncio, factory_boy |
| Python ops | pip-audit; the `pre-commit` framework |
| TS client and routing | openapi-typescript + openapi-fetch (hooks written by hand); React Router (web); Expo Router (mobile) |
| TS test and lint | Vitest, Testing Library, jsdom, jest-expo, React Native Testing Library; ESLint (typescript-eslint, React plugins); Prettier |
| End-to-end tests | Playwright (web), Maestro (mobile) |
| Mobile | expo-secure-store, react-native-svg, expo-keep-awake, expo-haptics; **victory-native** (with react-native-skia) for charts |
| Database | Postgres extensions `pg_trgm` and `unaccent` |
| Services | Cloudflare Tunnel, GitHub Container Registry, an external uptime monitor |
| **Not approved** | Sentry and other hosted error trackers; EAS Update; app stores (for now); any FX-rate or sealed-price source; turborepo/just/make |

**Conventions**

- **Primary keys are UUID v7** (B1), from a standard-library generator in `app/core/ids.py`.
- **Cursor (keyset) paging** on every list endpoint (B2).
- Shared TypeScript card helpers live in a new **`packages/card-utils`** (B4).
- **Link policy** (B6, S-1.6.7): every shareable link (invites, password resets, game joins, QR codes) is an `https://` web URL. Its page offers "Open in app" through `mtgstats://`, which is also used for in-app navigation. Android App Links replace the button in Phase 5.

**Process and ownership**

- **GitHub Free** (I1): branch protection can't be enforced on a private repo. "No direct pushes to `main`" and "green CI before merge" are conventions, and secrets are caught by the pre-commit hooks.
- **GitHub Issues are the source of truth** (I2). `docs/backlog/` is the 2026-11-07 import snapshot.
- **Six project agents** (I3): `api-architect`, `api-backend`, `frontend-web`, `frontend-mobile`, `test-engineer` and `code-reviewer`, defined in `.claude/agents/`.
- **E-2 owns every auth screen**: sign-in, invite sign-up, password reset, sign-out and session expiry. E-1 provides only routing, layout, the auth guard and token refresh in the API client.

## Alternatives considered

- **asyncpg**: rejected. psycopg 3 supports sync and async, and is the SQLAlchemy default for Postgres going forward.
- **bigint keys**: rejected. They can be guessed in URLs, and UUID v7 keeps index locality.
- **Offset paging**: rejected. It is unstable when rows are inserted during paging.
- **A custom `mtgstats://` link in QR codes**: rejected. A phone without the app couldn't open it.

## Consequences

- factory_boy is synchronous, so factories build objects and an async helper persists them (S-1.4.2).
- iPhone users use the web app until an Apple developer account exists.
- CI must re-run the secret checks so a `--no-verify` commit is still caught.
