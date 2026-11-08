# MTG Stats — Backlog

Source of truth: [../plans/mtg-stats-plan.md](../plans/mtg-stats-plan.md).
Status: **RECONCILED, awaiting approval to publish** (2026-11-07). These are local markdown drafts. Once approved, they become GitHub Issues in the private `mtg-stats` repo (sub-issues for the hierarchy, plus labels, milestones and a Project board).

## Hierarchy and IDs

| Level | ID format | Example | GitHub mapping |
|---|---|---|---|
| Epic | `E-<n>` | `E-6` | Issue, type **Epic**, milestone = phase |
| Feature | `F-<epic>.<n>` | `F-6.4` | Sub-issue of the epic, type **Feature** |
| User Story | `S-<epic>.<feature>.<n>` | `S-6.4.2` | Sub-issue of the feature, type **Story** |
| Task | `T-<epic>.<feature>.<story>.<n>` | `T-6.4.2.1` | Checklist item in the story (it becomes its own issue only when picked up) |

IDs never change once assigned. If an item is dropped, it is marked **Won't** rather than removed.

## Fields on every story

- **Priority**, using MoSCoW: Must / Should / Could / Won't.
- **Points**, on the Fibonacci scale: 1, 2, 3, 5, 8. **13 means the story must be split.**
- **Platform**: one or more of `api`, `web`, `mobile`, `shared` (the packages/), `infra`.
- **Phase**: 0–5, matching the plan.
- **Depends on**: story IDs (in any epic, as long as they exist) or feature IDs (`F-x.y`).
- **Acceptance criteria**: Given/When/Then. The test-engineer writes tests from these, not from the implementation.
- **Tasks**: technical steps, each tagged with its platform.

## Story template

```markdown
#### S-6.4.2 — Drag a seat to change turn order

**As a** game host **I want** to drag players around the table **so that** seating and turn order match the real table.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 1 | S-6.4.1, F-6.1 |

**Acceptance criteria**
- **Given** a lobby with 4 seated players **When** the host drags seat 4 onto seat 2 **Then** the seats swap and turn order updates for every connected client within 1 s.
- **Given** a keyboard-only user **When** they focus a seat and press Space then the arrow keys **Then** the seat moves the same way it would by dragging.

**Tasks**
- [ ] T-6.4.2.1 (web) Seat drag-and-drop with dnd-kit, with keyboard sensor enabled
- [ ] T-6.4.2.2 (shared) `seat_moved` event in the game-core reducer and test vectors
- [ ] T-6.4.2.3 (api) Validate `seat_moved` (lobby state only, host only)
```

## Labels (GitHub)

`type:epic` `type:feature` `type:story` · `phase:0`…`phase:5` · `platform:api` `platform:web` `platform:mobile` `platform:shared` `platform:infra` · `priority:must` `priority:should` `priority:could` · `points:1`…`points:8`

## Epic and feature map

| Epic | Phase | Features |
|---|---|---|
| [E-1 Platform Foundations](E01-platform-foundations.md) | 0 | F-1.1 Monorepo & tooling · F-1.2 Project governance (CLAUDE.md, architecture doc) · F-1.3 Local infrastructure (Docker Compose) · F-1.4 API skeleton · F-1.5 Shared API client generation · F-1.6 Web app shell · F-1.7 Mobile app shell · F-1.8 CI & verify gate · F-1.9 Background worker |
| [E-2 Identity & Playgroups](E02-identity-playgroups.md) | 0–1 | F-2.1 Accounts & authentication (invite-only) · F-2.2 Player profiles · F-2.3 Guest players · F-2.4 Playgroups & membership · F-2.5 House rules & house bans · F-2.6 Data rights (export, delete, archive) |
| [E-3 Card Data](E03-card-data.md) | 1, 3 | F-3.1 Scryfall bulk sync · F-3.2 Card search & lookup API · F-3.3 Card UI components · F-3.4 Price snapshots (phase 3) |
| [E-4 Decks & Legality](E04-decks-legality.md) | 1–2 | F-4.1 Text decklist import · F-4.2 Moxfield URL import · F-4.3 Archidekt URL import · F-4.4 Deck management & versions · F-4.5 Deck validation · F-4.6 Deck views (curve, colors) · F-4.7 Drag-and-drop sorting board (phase 2) |
| [E-5 Bracket Calculator](E05-bracket-calculator.md) | 1 | F-5.1 Versioned bracket rule set · F-5.2 Combo detection (Commander Spellbook) · F-5.3 Bracket assessment engine · F-5.4 Bracket UI |
| [E-6 Live Game Table](E06-live-game-table.md) | 1 | F-6.1 Game state core (TS + Python, shared vectors) · F-6.2 Game lifecycle (lobby, join by QR/code) · F-6.3 Real-time sync (WebSocket, Redis, event log, reconnect) · F-6.4 Web game table UI · F-6.5 Mobile game table UI · F-6.6 Undo & corrections · F-6.7 End of game & results · F-6.8 Spectator & host-controls-all |
| [E-7 Stats & Dashboard](E07-stats-dashboard.md) | 1–2 | F-7.1 Basic stats (phase 1) · F-7.2 Advanced stats · F-7.3 Game history & replay · F-7.4 Drag-and-drop dashboard · F-7.5 Card performance stats |
| [E-8 Player Activity & Habits](E08-player-activity.md) | 2 | F-8.1 Activity tracking · F-8.2 Meta shift analysis · F-8.3 Personal dashboard |
| [E-9 Collection & Budget](E09-collection-budget.md) | 3 | F-9.1 Collection management · F-9.2 Collection CSV import · F-9.3 Purchase log · F-9.4 Spend reports · F-9.5 Valuation & P/L · F-9.6 Deck ownership & cost to complete |
| [E-10 Leagues & Matchmaking](E10-leagues-matchmaking.md) | 4 | F-10.1 Seasons · F-10.2 Scoring systems · F-10.3 Standings · F-10.4 Pod matchmaking |
| [E-11 Deployment & Operations](E11-deployment-operations.md) | 5 | F-11.1 Hosting & environments · F-11.2 CI/CD deploy · F-11.3 Data, backups & restore · F-11.4 Observability · F-11.5 Mobile distribution (Expo internal builds) · F-11.6 Security hardening |

## Totals (checked 2026-11-07, after all decisions were applied)

**11 epics · 62 features · 374 stories (369 active, 5 Won't) · 1,241 tasks · 1,363 active points**

| Epic | Active stories | Points |
|---|---|---|
| E-1 Platform Foundations | 47 | 110 |
| E-2 Identity & Playgroups | 38 | 143 |
| E-3 Card Data | 23 | 82 |
| E-4 Decks & Legality | 37 | 148 |
| E-5 Bracket Calculator | 21 | 78 |
| E-6 Live Game Table | 58 | 232 |
| E-7 Stats & Dashboard | 34 | 154 |
| E-8 Player Activity & Habits | 14 | 60 |
| E-9 Collection & Budget | 38 | 157 |
| E-10 Leagues & Matchmaking | 27 | 110 |
| E-11 Deployment & Operations | 32 | 89 |

| Phase | Active stories | Points |
|---|---|---|
| 0 Foundations | 59 | 151 |
| 1 MVP | 156 | 601 |
| 2 Stats & activity | 44 | 199 |
| 3 Collection & budget | 48 | 192 |
| 4 Leagues | 27 | 110 |
| 5 Deployment | 35 | 110 |

By priority: Must 260 stories / 955 points · Should 89 / 336 · Could 20 / 72 · Won't 5 / 14.
**Shortest path to the MVP** (the Must stories in phases 0–1): **180 stories, 648 points.**

What the consistency check covered:
- every ID is unique, and every story has a complete field row;
- points are 1, 2, 3, 5 or 8, and every task ID matches its story;
- every dependency resolves, and no active story depends on a Won't story;
- no story depends on a story, or on a feature's earliest story, in a later phase;
- there are no dependency cycles, even when a feature-level dependency is expanded to all of the feature's stories.

Decisions are recorded in [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md).
