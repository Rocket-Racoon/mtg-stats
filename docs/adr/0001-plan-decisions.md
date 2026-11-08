# ADR-0001: Plan decisions at kickoff

- **Status**: accepted
- **Date**: 2026-11-07
- **Deciders**: product owner, orchestrator
- **Related**: [plan §8](../plans/mtg-stats-plan.md), decisions log rounds 1–5 ([OPEN-QUESTIONS.md](../backlog/OPEN-QUESTIONS.md))

## Context

MTG Stats is a new Commander companion app for a private playgroup. It runs as a web app, a mobile app and a Python API. The kickoff Q&A left five open points in plan §8, covering legality checks, dependencies, combo detection, governance and git. Each needed an explicit answer before Phase 0.

## Decision

1. **Legality scope**: decks are checked against the official Commander banned list (Scryfall `legalities.commander`) **plus each playgroup's house rules**. House rules can add bans and can also allow officially banned cards ("rule 0"). Commander has no "restricted" list.
2. **Approved dependencies (initial)**:
   - Python: SQLAlchemy 2.0, Alembic, pydantic-settings, httpx, PyJWT, arq.
   - TypeScript: TanStack Query, Zustand, dnd-kit, react-grid-layout, Reanimated, Gesture Handler, Recharts.
   - ADR-0002 extends this list. Anything else needs approval before it's added.
3. **Combo detection** uses the **Commander Spellbook API**, cached locally. If the API is unavailable, the assessment is marked "combo check incomplete" rather than failing.
4. **Governance**: a project-level `MTG-Stats/CLAUDE.md` adapts the Django-oriented Desktop rules to FastAPI, SQLAlchemy and Alembic. It overrides the Desktop file wherever the two conflict.
5. **Git**: the repo is a private GitHub repository `mtg-stats`, owned by the `Rocket-Racoon` account.

## Alternatives considered

- **Banned list only**: rejected. Playgroups rely on house rules.
- **A hand-kept combo list**: rejected. It misses most combos and needs manual upkeep.
- **Following the Desktop CLAUDE.md as is**: rejected. Its Django rules don't map onto FastAPI.

## Consequences

- Deck validation needs the playgroup context (the game's or the view's playgroup) to apply house rules.
- Bracket assessments depend on an external API and must work, with a warning, when it is down.
- Every new library goes through a quick approval. The approved set is plan §8, ADR-0002 and the decisions log in `docs/backlog/OPEN-QUESTIONS.md`.
