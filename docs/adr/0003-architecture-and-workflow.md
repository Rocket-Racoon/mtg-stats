# ADR-0003: Domain split, API errors, task runner and agent workflow

- **Status**: accepted
- **Date**: 2026-11-07
- **Deciders**: product owner, orchestrator
- **Related**: [docs/architecture.md](../architecture.md), [CLAUDE.md](../../CLAUDE.md), stories S-1.2.1–S-1.2.3

## Context

Writing the architecture document and the agent definitions surfaced six choices the backlog didn't make: how to split the backend into domains, the API error format, how monorepo commands run, which model each agent uses, how parallel agents are isolated, and how branches and PRs map to the backlog.

## Decision

1. **Nine backend domains**: `identity`, `playgroups`, `cards`, `decks`, `brackets`, `games`, `stats`, `collection` and `leagues`. Their dependency direction is fixed in architecture §4.2. Cycles are forbidden, and an import-graph test enforces that. Work that spans domains goes in `app/workflows/` or in arq jobs enqueued by name.
2. **API errors** use RFC 9457 `application/problem+json` with a stable `code`, plus `errors[]` for validation errors.
3. **Task runner**: root `package.json` scripts call `pnpm -r` for TypeScript and `uv run` for Python. `pnpm verify` runs the whole verify gate. No new tool is needed.
4. **Agent models**: `api-architect` and `code-reviewer` run on **opus**. `api-backend`, `frontend-web`, `frontend-mobile` and `test-engineer` run on **sonnet**.
5. **Agent permissions**: `test-engineer` edits only test files (`tests/`, `*.test.*`, `*.spec.*`, `vectors/`, `e2e/`, `.maestro/`). `code-reviewer` is read-only, apart from running the verify gate, and reports findings instead of fixing them.
6. **Parallel work**: implementing agents run in **their own git worktree and branch**. The orchestrator merges after the verify gate passes and the review is done.
7. **Branches and PRs**: **one story per PR**, on a branch named `s-<id>-<slug>`, and the PR closes the story issue. The one exception is the bootstrap PR for S-1.2.1, S-1.2.2 and S-1.2.3: those three are written together because they reference each other.

## Alternatives considered

- **Seven domains** (players, decks including brackets): rejected. The `players` and `decks` modules would grow too large, and their ownership by epic would be unclear.
- **A custom error envelope**: rejected. It isn't a standard, and clients would need their own parsing.
- **justfile or Makefile**: rejected. Each is a new tool to install, and Make is awkward on Windows.
- **All agents inheriting the session model**: rejected. Opus is reserved for design and review, where judgment matters most.

## Consequences

- An import-graph test (`tests/architecture/test_domain_imports.py`) has to land with the API skeleton (F-1.4).
- Stories that span several platforms are split by agent: the API part first, then the TypeScript client is regenerated, then the web and mobile parts.
- More PRs, but each one is small and easy to review.
