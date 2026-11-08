# ADR-0003: Domain split, API errors, task runner and agent workflow

- **Status**: accepted (revised after the PR #448 review)
- **Date**: 2026-11-07
- **Deciders**: product owner, orchestrator
- **Related**: [docs/architecture.md](../architecture.md), [CLAUDE.md](../../CLAUDE.md), stories S-1.2.1–S-1.2.3, S-1.4.5, S-1.4.6, S-1.8.1

## Context

Writing the architecture document and the agent definitions surfaced choices the backlog didn't make: how to split the backend into domains, how to keep that split free of cycles, how monorepo commands run, which model each agent uses, how parallel agents are isolated, and how branches and PRs map to the backlog.

The first draft of this ADR also picked an API error format (RFC 9457). That contradicted story S-1.4.5, so the product owner reversed it after review (decision 1 below).

## Decision

1. **API errors follow story S-1.4.5**: `{"error": {"code", "message", "details", "request_id"}}`, with validation errors in `details`. The RFC 9457 proposal is withdrawn.
2. **Ten backend domains**: `system` (health and job runs; a leaf), `identity`, `playgroups`, `cards`, `decks`, `brackets`, `games`, `stats`, `collection` and `leagues`.
   - **Player belongs to `identity`**, together with User and profile fields.
   - `playgroups` owns membership (including guest membership), invites, house rules and settings. Anything that needs membership also lives there: profile visibility, the profile view, and per-playgroup sharing of spend and collection value (`financials_visible_to`).
   - Architecture §4.1 maps the backlog's "players" and "prices" names onto these domains.
3. **The dependency graph** (architecture §4.2) is transitive and acyclic, and `tests/architecture/test_domain_imports.py` enforces it.
   - Every need for an upward call is resolved in §4.3 with a workflow, a job enqueued by name, an inverted call, a change of ownership or client composition.
   - Public services accept an optional caller session, so a workflow can span domains in one transaction. A service inside a caller's session never commits and never enqueues directly; follow-up jobs use `enqueue_after_commit`.
   - Auth dependencies live in `core.auth`. `require_site_admin` uses a checker that `identity` registers, so no domain needs to import `identity` for auth.
4. **Task runner**: root `package.json` scripts call `pnpm -r` for TypeScript and `uv run` (from `apps/api`) for Python. `pnpm verify` runs the gate in the S-1.8.1 order, and `verify:api` and `verify:ts` run one half. turborepo, just and Make are not used.
5. **Agent models**: `api-architect` and `code-reviewer` run on **opus**. `api-backend`, `frontend-web`, `frontend-mobile` and `test-engineer` run on **sonnet**.
6. **Agent permissions**:
   - `test-engineer` edits only test files: `apps/api/tests/`, `apps/api/app/**/tests/`, `*.test.*`, `*.spec.*`, `packages/game-core/vectors/`, `apps/web/e2e/` and `apps/mobile/e2e/`.
   - `code-reviewer` is read-only, apart from running the verify gate.
7. **Parallel work**: implementing agents run in **their own git worktree and branch**. The orchestrator merges after the verify gate passes and the review is done.
8. **Branches and PRs**: **one story per PR**, on a branch named `s-<id>-<slug>`, and the PR closes the story issue. The one exception is the bootstrap PR #448 for S-1.2.1 to S-1.2.3, because those three reference each other.
9. **Event-log exceptions** (product-owner decision): `game_events` stays append-only, except `games.reassign_player` (guest merge, S-2.3.4) and `games.delete_playgroup_data` (playgroup deletion, S-2.6.3), which each write an audit row.
10. **Spend and collection-value sharing is per playgroup** (product-owner decision, S-8.3.6). The global flags in S-2.2.5 are dropped.
11. **Docs never override stories.** When an architecture choice conflicts with a story, the conflict goes to the product owner and the issue is updated before any code is written.

## Alternatives considered

The rationale below is the orchestrator's; the product owner chose among these options.

- **Seven domains** (players, decks including brackets): rejected. The modules would grow large and their ownership by epic would be unclear.
- **Player in `playgroups`**: rejected. Registration (S-2.1.3) creates the User and the Player in one transaction, which would need an upward call.
- **Relaxing the graph to allow the extra edges**: rejected. It would weaken layering, and decks and brackets would still form a cycle.
- **justfile or Makefile**: rejected. Each is a new tool to install, and Make is awkward on Windows.
- **All agents inheriting the session model**: rejected. Opus is reserved for design and review.

## Consequences

- Several story tasks need rewording so they match the mechanisms in architecture §4.3. The list goes to the product owner, and the Issues are updated after approval.
- S-1.4.6 gains a task: the import-graph direction test.
- Stories that span several platforms are split by agent: the API part first, then the client is regenerated, then the web and mobile parts.
- More PRs, but each one is small and easy to review.
