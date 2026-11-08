# MTG Stats — Commander Companion: Project Plan

Status: **DRAFT, awaiting approval**. No code until this plan is approved.
Date: 2026-11-07

## 1. Goal

A Commander (EDH) companion for a private playgroup that:

- runs **live multiplayer games** (2–8 players) in real time across phones, with drag-and-drop seats and damage;
- records **results and statistics** per player, deck, commander and pod;
- imports **decks**, checks them for **legality** (banned list, color identity, singleton, size) and works out their **official WotC Commander Bracket**;
- tracks **player activity and habits**: how often people play, meta shifts, and a **full card collection with spend and value tracking**;
- manages **playgroups, seasons and leagues**.

Clients: a web app and a mobile app (iOS/Android), both talking to a Python API.

## 2. Decisions captured (from kickoff Q&A)

| Topic | Decision |
|---|---|
| Backend | **FastAPI** (Python 3.12+) |
| Web frontend | **React + TypeScript** (Vite) |
| Mobile | **React Native (Expo)**, sharing TS types and the API client with web |
| Users | **Private playgroup**: simple accounts; guest players can join a game without signing up |
| Live play | **Shared table, real-time**: every player joins from their own phone (WebSockets); one device can also run the whole table |
| Drag & drop | Seat arrangement, damage/effects between players, deck sorting boards, dashboard widgets |
| Brackets | **Official WotC Commander Brackets (1–5)** |
| Card data | **Scryfall** (bulk data + API) plus decklist import from **Moxfield / Archidekt** URLs |
| MVP | **Live tracker + decks together** |
| Infra | **Docker Compose** (Postgres + Redis) for dev; cloud host chosen later |
| Budget | **Full collection tracker** (printing, foil, cost, current value, P/L, deck usage) |
| Repo | **Monorepo** |

## 3. Proposed architecture

```
apps/
  api/        FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic v2 · WebSockets
  web/        React · Vite · TanStack Query · Zustand · dnd-kit · react-grid-layout · Recharts
  mobile/     Expo · React Native Gesture Handler + Reanimated · TanStack Query · Zustand
packages/
  api-client/ TS types + client generated from the FastAPI OpenAPI spec
  game-core/  Shared TS game-state reducer (life, cmdr damage, poison, eliminations)
infra/
  docker-compose.yml   postgres, redis, api, worker
docs/
  plans/  audits/  architecture.md
```

**Backend layering** (this keeps the spirit of the Desktop CLAUDE.md, adapted to FastAPI):

```
apps/api/app/
  <domain>/            cards, decks, games, players, stats, collection, leagues
    models.py          SQLAlchemy models
    schemas.py         Pydantic request/response
    router.py          thin: parse → call service → respond
    services/          business logic; __init__.py is the domain's public interface
    repository.py      reusable query builders (the equivalent of Django managers)
  core/                config (pydantic-settings, env only), db, auth, websocket hub
  workers/             background jobs (Scryfall sync, price snapshots, imports)
```

**Real-time game flow**

- The server is authoritative. Each game is a **room** (`/ws/games/{id}`) and every action is an **event** (`life_changed`, `cmdr_damage`, `poison`, `player_eliminated`, `seat_moved`, `turn_passed`, …).
- Events are appended to a `game_events` table (an event log), applied to the current state, and broadcast through **Redis pub/sub** so the system can run more than one API process.
- The same reducer lives in `packages/game-core`. Clients update the screen right away and reconcile with the server's sequence number. On reconnect, a client replays the events it missed.
- The event log gives replays and fine-grained stats (who dealt the damage, when players were knocked out, turn timing) for free.

**Background jobs:** a worker process (arq on Redis) for the daily Scryfall bulk sync, price snapshots and deck-site imports.

## 4. Core domain model (first pass)

- **User**: login account. **Player**: a person at the table, linked to a User or a guest.
- **Playgroup**: members, with a house-rules / house-ban list. **Season / League**: belongs to a Playgroup.
- **Card**: from Scryfall, keyed by `oracle_id`. **Printing**: keyed by Scryfall `id`. **PriceSnapshot**: printing, date, usd / usd_foil / eur.
- **Deck**: owner, commander(s), source URL, current **DeckVersion**. **DeckCard**: version, card, quantity, category/board.
- **DeckValidation**: results of the legality checks. **BracketAssessment**: version, bracket, reasons, rules version used.
- **Game**: playgroup, season, status, start/end, turn count, win condition. **GameSeat**: game, player, deck version, seat order, final placement, how they were eliminated. **GameEvent**: game, sequence number, type, payload, actor, timestamp.
- **CollectionItem**: user, printing, foil, condition, language, quantity, acquisition cost, date, store. **Purchase**: singles or sealed, with line items.

## 5. Feature specs (summary)

**Live game table**
- 2–8 seats around a virtual table. Drag to set seats and turn order; pick a deck version for each seat.
- Life (40 by default), commander damage (21 from one commander, partners tracked separately), poison (10), plus custom counters (monarch, initiative, energy, experience, tax).
- Drag from one player onto another to apply commander damage or drain. Tap and long-press for life.
- Join by QR code or short code. Spectator mode. A host device can control every seat.
- End of game: winner, win condition, turn count, placements. Most of this is filled in from the event log.

**Decks**
- Import a pasted list (Moxfield, Arena or MTGO text formats) or a Moxfield/Archidekt URL. Keep a version history.
- Validation: Commander banned list (Scryfall `legalities.commander`), color identity, singleton (with exceptions such as Relentless Rats), 100-card count, commander eligibility (partner, background, friends forever, companion), and the playgroup's house bans.
- Drag-and-drop sorting board: piles, categories and maybe/cut lists.

**Bracket calculator (official WotC)**
- The rule set is stored as **versioned data** (Game Changers list, mass land denial, extra-turn cards, tutors, two-card combo rules) with an effective date. When WotC updates the rules, we change data, not code.
- Output: the **minimum bracket** the deck qualifies for, with an itemized list of why (for example, "4 Game Changers → Bracket 4+"). Brackets 1–2 depend partly on the deck's intent, so the user declares that and the app checks it against the rules.

**Stats and dashboard**
- Win rate by player, deck, commander, color identity, seat position and pod size. Average game length and turns. Win conditions. Head-to-head records.
- Activity: games per week or month, streaks, commanders played over time (meta shifts).
- The dashboard is a drag-and-drop widget grid (react-grid-layout on web; a reorderable list on mobile) whose layout is saved per user.

**Collection and budget**
- Owned cards by printing and finish, cost basis, current market value (daily Scryfall prices), profit/loss, and which decks use each card.
- Purchase log for singles and sealed product. Spend per date range and category. Deck value vs. the part of it already owned.

**Leagues**
- Seasons, points systems, standings. Pod matchmaking that balances seats by bracket and recent win rate.

## 6. Delivery phases

### Phase 0 — Foundations
- [ ] Monorepo scaffold (`apps/api`, `apps/web`, `apps/mobile`, `packages/*`), with uv for Python and pnpm workspaces for TypeScript
- [ ] Project-level `CLAUDE.md` adapting the Desktop rules (layering, verify gate, guardrails) to FastAPI + SQLAlchemy + Alembic
- [ ] `docker-compose.yml`: Postgres 16, Redis 7, api, worker
- [ ] FastAPI skeleton: settings from env, DB session, Alembic, health endpoint, error envelope
- [ ] Auth: email + password accounts, JWT access and refresh tokens, guest players
- [ ] OpenAPI → `packages/api-client` generation script
- [ ] Web and Expo shells: routing, auth screens, shared client
- [ ] Verify gate: ruff, mypy --strict, pytest (≥85% coverage), Alembic drift check, eslint + tsc, Vitest/Jest

### Phase 1 — MVP: live tracker + decks
- [ ] Scryfall bulk sync job (cards, printings, legalities) and card search endpoint
- [ ] Players and playgroups (members, guests, house bans)
- [ ] Deck import: text paste, Moxfield URL, Archidekt URL (with a fallback to text paste if those sites block us)
- [ ] Deck validation service
- [ ] Bracket rules data model, seed data and calculator service
- [ ] `packages/game-core` reducer, tested against the same spec on the server side
- [ ] WebSocket game rooms (Redis pub/sub, event log, reconnect and replay)
- [ ] Web game table: drag-and-drop seats, drag-to-damage, counters
- [ ] Mobile game table: the same features with native gestures
- [ ] End-of-game flow and results saved
- [ ] Basic stats: win rate by player, deck and commander

### Phase 2 — Stats dashboard and activity
- [ ] Stats endpoints (seat position, pod size, head-to-head, win conditions, game length)
- [ ] Activity and habits (frequency, streaks, meta shifts over time)
- [ ] Drag-and-drop widget dashboard with saved layouts
- [ ] Drag-and-drop deck sorting board

### Phase 3 — Collection and budget
- [ ] Collection import (CSV from Moxfield, Archidekt, ManaBox, Deckbox) and manual entry
- [ ] Daily price snapshots for owned cards and cards in decks
- [ ] Purchase log (singles and sealed), spend by period and category
- [ ] Value, profit/loss and "deck cost vs. owned" views

### Phase 4 — Leagues
- [ ] Seasons, scoring systems, standings
- [ ] Bracket- and win-rate-balanced pod matchmaking

### Phase 5 — Deployment
- [ ] Choose a host (Fly.io, Railway, Render or Azure), set up CI/CD, managed Postgres and Redis
- [ ] Expo EAS builds and internal distribution (TestFlight / Play internal testing)

## 7. Risks

- **Moxfield/Archidekt APIs are unofficial.** They may rate-limit, block or change without notice. Mitigation: always keep a text-paste fallback, cache imports, and send an honest User-Agent.
- **Bracket rules change.** WotC updates the Game Changers list and bracket rules periodically. Mitigation: store them as versioned data and record which rules version each assessment used.
- **Detecting two-card combos** needs a combo database. Option: the Commander Spellbook API (an external dependency; needs approval).
- **Real-time edge cases**: two people changing the same thing at once, phones going to sleep, flaky Wi-Fi at the table. Mitigation: server-ordered event sequence, idempotent events, replay on reconnect.
- **Drag and drop on mobile vs. web** uses different libraries. Mitigation: share the game logic (`game-core`), not the UI components.

## 8. Resolved questions (2026-11-07)

1. **Legality checks**: the official Commander banned list (Scryfall) **plus playgroup house bans**. There is no "restricted" concept.
2. **Dependencies approved.**
   - Python: SQLAlchemy 2.0, Alembic, pydantic-settings, httpx, PyJWT, arq.
   - TypeScript: TanStack Query, Zustand, dnd-kit, react-grid-layout, Reanimated, Gesture Handler, Recharts.
   - Anything beyond this list still needs approval.
3. **Combo detection**: use the **Commander Spellbook API**, with results cached locally. If the API is unavailable, the assessment is marked "combo check incomplete" rather than failing.
4. **Governance**: add a project-level `MTG-Stats/CLAUDE.md` that adapts the Desktop rules to FastAPI, SQLAlchemy and Alembic. This is the second item in Phase 0.
5. **Git**: initialize the repo here, with a **private GitHub repo named `mtg-stats`** as the remote.

6. **Further decisions (rounds 1–5)** are listed in [../backlog/OPEN-QUESTIONS.md](../backlog/OPEN-QUESTIONS.md) under "Decisions log". It extends the approved dependency list with:
   - **Python:** uvicorn, psycopg 3, pytest, pytest-cov, pytest-asyncio, factory_boy, argon2-cffi, ijson, qrcode, redis-py.
   - **TypeScript:** React Router, Expo Router, openapi-typescript, openapi-fetch, Vitest, Testing Library, jest-expo, ESLint, Prettier, Playwright, Maestro, victory-native (with react-native-skia), expo-secure-store, react-native-svg.
   - **Tooling and database:** pre-commit hooks, and the Postgres extensions pg_trgm and unaccent.

7. **Rounds 11–17** answered every remaining open question (see the decisions log).
   - **Approved:** pip-audit, an external uptime monitor, Cloudflare Tunnel, GitHub Container Registry.
   - **Not approved:** Sentry, EAS Update and app-store accounts, for now.
   - **Hosting:** a home server or VPS running Docker Compose, with production only.
   - **Card ownership:** counted by exact printing, with each copy assigned to one deck.

## 9. Plan review notes (2026-11-07)

Gaps found while reviewing, and how the backlog handles them:

1. **The game reducer exists in two languages.** The server is Python and the clients are TypeScript, so "shared reducer" really means two implementations. The backlog adds a shared set of **JSON test vectors** (`packages/game-core/vectors/`) that both implementations must pass.
2. **Undo and corrections were missing.** At a real table, people tap the wrong thing. The backlog adds Feature F-6.6: a game event can be reverted by adding a compensating event; the log is never edited.
3. **Drag and drop needs an alternative.** Every drag action also gets a tap or keyboard alternative. This helps accessibility and small screens.
4. **Registration and password reset with no email service.** For a private playgroup, registration is **invite-link only**, and a playgroup admin resets passwords. Adding an email provider later would need approval.
5. **Scryfall bulk files are large** (hundreds of MB). The sync job streams and processes them in batches and never loads a whole file into memory.
6. **Keep the screen awake** on mobile during a game. `expo-keep-awake` and `expo-haptics` are Expo SDK modules, counted as part of the approved Expo stack.
7. **Not yet approved, so these stay as open questions in the backlog:** an end-to-end test tool (Playwright for web, Maestro for mobile) and an error-tracking service (for example Sentry).
8. **Deck and card stats**: mana curve and color breakdown views, plus per-card win rate, were only implied in the plan. The backlog makes them features F-4.6 and F-7.5.
