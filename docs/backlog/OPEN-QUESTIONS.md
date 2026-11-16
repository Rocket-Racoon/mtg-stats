# Open Questions — merged register

The roughly 150 open questions from the 11 epic files, with duplicates merged. Each question links back to the epics that raised it.
**Blocks** = the earliest phase that can't finish until the question is answered.
Status: `open` · `answered` (with the decision recorded). Answers are also copied into plan §8.

## Decisions log

### 2026-11-07 — kickoff rounds 1–5

| ID | Decision |
|---|---|
| A1–A3 | Approved: **uvicorn**, **psycopg 3 + async SQLAlchemy**, **pytest + pytest-cov + pytest-asyncio**, **factory_boy** |
| A4 | **openapi-typescript + openapi-fetch**. The TanStack Query hooks are written by hand. |
| A5 | **React Router** (web) and **Expo Router** (mobile) |
| A6–A8 | Approved: **Vitest + Testing Library** (jest-expo and RN Testing Library on mobile), **ESLint + Prettier**, **Playwright + Maestro**, **pre-commit hooks** |
| A9 | Password hashing with **argon2-cffi** (argon2id) |
| A10, A13 | Approved: **expo-secure-store** and **react-native-svg** |
| A11, A12, A14, A15 | Approved: **ijson**, **pg_trgm + unaccent**, **qrcode** (SVG drawn on the server), **redis-py** |
| A16 | Mobile charts use **victory-native** (this adds @shopify/react-native-skia) |
| B1 | Primary keys are **UUID v7** |
| B3, C1 | Login is by **username**, and email is optional. Access tokens last 15 minutes and refresh tokens 30 days; on web the refresh token is kept in an HttpOnly cookie. |
| C2, C3 | Anyone can create a playgroup and becomes its owner. Only the owner and admins can invite. Admins can reset passwords only for members who belong to no other playgroup; the owner handles admins. |
| C4 | Guests are **counted in stats and standings**. When a guest claims an account, their history is merged into it and stats are rebuilt. |
| C5 | Decks are **visible to every member of a shared playgroup**. Only the owner can edit them. |
| D1 | **Per-game trust mode**. Casual (the default): anyone can edit any seat. Strict: players edit only their own seat and the damage they deal, and the host can edit everything. |
| D2 | **Undo**: each player can undo their own last 10 events; the host can undo any of the game's last 10. Undo adds a compensating event. |
| D3 | 40 life, 21 commander damage and 10 poison are **defaults**. House rules can change them per playgroup, and a game can override them in the lobby. Each game records the values it used. |
| D4 | Turn count means **rounds** |
| D6 | Players eliminated at the same time **share a placement**. If everyone draws there is no winner. In win rate, a draw counts as not a win. |
| E2 | House rules can **add bans and also allow officially banned cards** ("rule 0"). They apply per playgroup, using the playgroup of the game or view. |
| E3 | Default printing: before Phase 3, Scryfall's default. After Phase 3, the user's owned copy (the cheapest if they own several), otherwise Scryfall's default. |
| E8 | A deck with no declared intent is treated as **Bracket 2**, shows a badge, and its owner is prompted to declare. |
| E9 | A new global **site admin** role publishes bracket rule-set versions in an admin screen. Owners get an in-app notice when their deck's bracket changes. **This adds a new story; the backlog needs updating.** |

### 2026-11-07 — rounds 6–10

| ID | Decision |
|---|---|
| I1 | **GitHub Free**: CI checks run but branch protection on the private repo isn't enforced. Secret scanning is done by the pre-commit hook. |
| I2 | After the one-time import, **GitHub Issues** is the source of truth. The markdown files stay as the original snapshot. |
| I3 | Project agents live in `.claude/agents/`: **api-architect, api-backend, frontend-web, frontend-mobile, test-engineer, code-reviewer** |
| — | Open questions for phases 2–5 become one **"Decisions" issue per epic**, answered before that phase starts. |
| D5 | Win conditions: combat damage, commander damage, combo, alternate-win card, poison, mill/decking, life drain/burn, concession, last standing, and other (with a note) |
| D7 | Playgroup **admins can amend results at any time**. Every change is audit-logged (who, when, what changed) and stats/standings are recomputed. |
| D10 | Up to **20 spectators**, who see the **board only**. Decklists are shown only where the deck would be visible to that viewer anyway. |
| D11 | The **group drain** ("each opponent loses N, you gain the total") is in Phase 1 |
| C6 | A logged-in non-member who joins by code plays under their **own account for that game only**. Afterwards the host gets a one-tap "invite to playgroup". |
| D8 | A guest's game token expires **24 h after the game ends**. Lobbies idle for 2 h and games idle for 24 h are marked **abandoned** and left out of stats unless the host resumes them. |
| D9 | The **host can fix seating mid-game**. The change is logged as a correction event, and seat-position stats use the final order. |
| C10 | Avatars are **a color with initials, or a card's art crop from Scryfall**. No uploads. |
| E1 | Unknown lines on import show "did you mean" suggestions and can be **skipped explicitly**. Skipped lines are kept as notes, and the deck is marked "incomplete" until it has 100 cards. |
| E5 | A companion's deckbuilding condition is **checked automatically** where possible, otherwise shown as "verify yourself". The companion **counts toward the bracket**. |
| E10 | **In-app deck editing (S-4.4.6) is a Must for the MVP**. Each save creates a new deck version. |
| E12, E11 | Also store **tokens, emblems, art-series and oversized cards** (art-series and oversized are left out of default search). Store Scryfall's **`game_changer`** flag and warn the site admin when it differs from our rule set. |
| E4, I8 | The User-Agent is `MTGStats/<version> (+<repo URL>)` and contains no personal email. **Card images are hotlinked from Scryfall.** The WotC Fan Content Policy disclaimer and a Scryfall credit go in the footer and About page. Before building F-4.2 and F-4.3, check Moxfield's and Archidekt's terms. |
| E6, E7 | The **site admin** publishes rule sets and maintains the category lists. **Commander Spellbook** is queried per deck version with a **7-day cache**. Only combos tagged infinite or win-the-game count, and a combo that needs the commander counts as two cards. |
| B2, B4 | Lists use **cursor (keyset) paging**. Shared TypeScript card helpers go in a new **`packages/card-utils`**. |
| B5 | Stats use a **per-seat fact table** that an arq job refreshes after each game, plus a **weekly full rebuild** |
| B6, B7 | Invite and join links use the **`mtgstats://` deep-link scheme** in the MVP; universal links come in Phase 5. The **Scryfall sync runs daily at 09:00 UTC**. |
| F1 | A win rate based on **fewer than 5 games** shows "n games" and a low-sample marker. The threshold is configurable per playgroup. |

### 2026-11-07 — rounds 11–12 (Phase 2 and privacy)

| ID | Decision |
|---|---|
| F2 | Bracket-vs-outcome stats use the **bracket frozen when the game started**, together with that game's rule-set version |
| F3, F5 | **Per-partner stats** are shown as well as stats per pair. Basic lands and staples are **hidden by default** on the card leaderboard, with a toggle to show them. Card stats can be **filtered by day**, so aggregates are stored daily. |
| B8, F6 | Weeks start on **ISO Monday**. Each playgroup has a time zone set by the owner, and a user can override it. A **streak counts weeks**, with the current week as a grace period. Personal activity counts games **from all of a player's playgroups**. |
| F4, F6 | The dashboard starts with a **curated set of widgets** (win rate, recent games, top commanders, activity heatmap, seat position). On web there is one layout per breakpoint. Meta shares show the **top 8 plus an expandable "Other"**. |
| F7 | A season filter on stats is tracked as a story in **E-10**, depending on F-7.x |
| E13 | The sorting board **syncs live** across the owner's devices over WebSocket |
| C7 | When a member leaves, their games **stay in the playgroup's stats under their name**. They keep read-only access to those games from their own profile. |
| C8 | Visitors see the owner's profile layout, and the owner can hide sections. **Spend and collection value are private by default**, and the owner can opt in to share them with the playgroup. |
| C9 | Users can **export their data** (JSON or CSV) and **delete their account**. A deleted user's past seats show as "Deleted player". The owner can archive or delete a playgroup. |

### 2026-11-07 — rounds 13–17 (collection, leagues, operations, confirmations)

| ID | Decision |
|---|---|
| G1 | **Each user picks one currency**, USD or EUR. Values use Scryfall prices in that currency. Purchases are stored in the currency paid and **never converted**; when currencies are mixed, each gets its own total. |
| G2 | Price snapshots store **usd, usd_foil, usd_etched, eur and eur_foil** (no tix). Snapshots are kept **daily for 2 years**, then thinned to weekly. |
| G3 | The cost of opened sealed product is **split by each card's market value** on the opening date. Shipping and tax are split the same way. |
| G4 | **Ownership counts the exact printing**, and **each copy is assigned to one deck**. Proxies are tracked but marked as not owned. Cost to complete uses the printing listed in the deck. *This replaces E3 after Phase 3: a deck card uses the copy assigned to it, otherwise Scryfall's default.* |
| G5 | **Re-import updates and adds rows but never deletes**; rows missing from the new file are listed for review. The limit is 10 MB or 50k rows. **Collection CSV export** is included, in our own format and a Moxfield-compatible one. |
| G6 | Condition uses the **TCGplayer scale (NM/LP/MP/HP/DMG)**, with Cardmarket grades mapped onto it. It is **for records only and does not change value**. |
| G7 | Unopened sealed product is shown **at purchase cost, with a value the user can enter by hand**. No new price source. |
| G8 | CSV importers are built **from each site's public format documentation** and fixed as users report problems. |
| G9 | Foreign-language printings are **looked up on demand** from the Scryfall API and cached. The `all_cards` file is not used. |
| H1 | Players **opt in to a season roster**. A game counts **automatically** when it falls in the season's dates and every seated player is on the roster. The host can exclude a game. Seasons in the same playgroup can't overlap. |
| H2 | Scoring presets: **win-only, placement, achievements** (including achievements awarded by an admin) **and participation**, each editable per season. **If scoring changes, the whole season is rescored and the change is logged**; this also applies to ended seasons when a result is amended. |
| H3 | Ties are broken by **win rate, then head-to-head, then games played**. A player needs **at least 5 games** to be ranked; others are listed as "provisional". |
| H4 | **Pods of 4 are preferred; 3–6 players are allowed.** Any bracket mix is allowed. **Keeping a small bracket spread is optional, and so is balancing win rates over the last 20 games.** Pairings from the **last 3 game nights** are avoided, and **seat order is random**. |
| I4, I6 | The app runs on a **home server or VPS** with Docker Compose. It is exposed through a **Cloudflare Tunnel** (no open ports; needs a domain on Cloudflare). There is **one production API instance**, and images go to **GitHub Container Registry**. |
| I5 | **Production only**, with no staging. **Daily backups kept 14 days**, a recovery point within 24 h and a 4 h restore target, with a **restore drill every quarter**. Alerts come from the uptime monitor. |
| A17 | **Sentry is not approved.** Use structured JSON logs (with request and game IDs) kept for 30 days, an admin error page listing recent 5xx errors, and the Expo dashboard for mobile crashes. **An external uptime monitor is approved**: it checks `/health` and sends alerts. |
| A18 | **pip-audit** is approved for CI |
| A19 | **EAS Update is not approved** |
| I7 | **No app stores for now.** The playgroup installs the app from Expo internal distribution builds. Store accounts will be decided after real use. |
| E7 (confirmed) | A commander plus one other card that makes an infinite or win-the-game combo **counts as a two-card combo** |
| Auth screens | **E-2 owns every auth screen** (login, sign-up via invite, password reset). E-1 provides only routing, layout, the auth guard and the API client with token refresh. |

### 2026-11-07 — rounds 18–20 (follow-ups from the reconciliation pass)

| Topic | Decision |
|---|---|
| Bracket timing rules | WotC guidance that depends on timing or play pattern becomes **advisory notes**. These never raise the computed bracket. |
| Redis fan-out | The cross-process fan-out and the multi-process soak test **stay Must** in the MVP |
| Staples | A staple is **any non-basic card in at least 75% of the decks counted**. The 75% is a per-playgroup setting. Staples are hidden from the card leaderboard by default. |
| Owner password resets | The owner has **the same limit as admins**: only members who are in no other playgroup. Anyone else is reset through the site admin's CLI command. |
| Sign-in throttling | Lives in **S-2.1.10** (phase 0). E-11 reuses that limiter. |
| Latest prices | **Every printing stores its latest price**, updated daily. Daily price history is kept only for printings that are owned or in a deck. |
| G3 (revised) | **Shipping and tax are not recorded.** S-9.3.7 is now Won't. Opened sealed product's cost is still split across the cards pulled by market value, and a pulled card with no price on the opening date costs 0.00. |
| Collection CSV | Import and export are **web only** |
| Late roster joiners | Whether a game counts depends on the roster **as it was when the game finished**. A player who joins late never changes earlier standings. |
| Off-site backups | Encrypted copies go to a **second disk or a NAS** on the local network. The owner holds the key. |
| iPhone | iPhone players use the **web app**. Native internal builds are **Android only** for now, and Apple will be revisited later. |
| Publishing | Create the private repo and publish the issues **under a GitHub account the user picks** |

### 2026-11-07 — architecture review (PR #448)

| Topic | Decision |
|---|---|
| API errors | Keep the S-1.4.5 envelope `{"error": {code, message, details, request_id}}`. RFC 9457 isn't used. |
| Player ownership | Player lives in `identity`. Anything that needs membership lives in `playgroups`: profile visibility, the profile view, spend sharing. |
| Domain graph | Every need for an upward call gets a documented mechanism in architecture §4.3. Story wording that changes is listed on PR #448, then edited in the Issues. |
| Event log | `game_events` stays append-only, with **two audited exceptions**: `games.reassign_player` (guest merge) and `games.delete_playgroup_data` (playgroup deletion). |
| Spend sharing | **Per playgroup** (S-8.3.6). The global switch in S-2.2.5 is dropped. |
| Expo Router dependencies | **expo-linking and expo-constants approved** |
| Phase 1 dependencies | **websockets (dev only), expo-camera and expo-screen-orientation approved** |
| Story wording | The architecture-alignment list on PR #448 (about 100 rows) is **approved**, and the Issues are edited to match |

### 2026-11-08 — Phase 0 implementation

| Topic | Decision |
|---|---|
| `@types/node` | **Approved** as a type-only TypeScript devDependency (S-1.1.3 review). Under TypeScript 6 it loads only where a tsconfig opts in with `"types": ["node"]`, such as root tooling tests, Vite and Playwright configs, never app code. |
| `expo-splash-screen` | **Approved by the user (2026-11-16, S-1.7.3):** an Expo SDK module that keeps the native splash up while the session is `restoring`. |
| Issue titles | The imported issues keep the `[S-1.1.1] Title` form rather than S-1.2.4 AC1's `S-1.1.1 — …` example (comment on #11). |
| SQLAlchemy | **Approved by the user (2026-11-10):** 2.1.x (`>=2.1,<2.2`) falls within the plan §8 "SQLAlchemy 2.0" approval, because it is the same 2.0-style API. Locked at 2.1.8 in PR #451. CLAUDE.md's "SQLAlchemy 2.0" means this 2.0-style API. |
| Type-only and test-runner packages | **Approved** (2026-11-08): `@types/react` and `@types/react-dom` (web, S-1.6.1), `@types/jest` (mobile), and `jest` itself as the required peer of the approved jest-expo (S-1.7.1). None ships in the app at runtime. |
| S-1.7.1 platforms | **Android is required** for the device check, and iOS (Expo Go) is optional; it is checked when an iPhone is at hand and otherwise recorded as unverified. This follows the iPhone decision above (comment on #40). |
| Tap targets (S-1.6.5 AC3) | **User decision (2026-11-10, S-1.6.6):** links inside a sentence of text are exempt from the 44 × 44 px rule below 768 px, per WCAG 2.5.8's "Inline" exception. Standalone links (such as the footer's About link) and every button keep 44 × 44. The web tests list the exempt selectors (`.attribution p a`) explicitly. |
| CI actions (S-1.8.2, S-1.8.3) | **Approved by the user (2026-11-11):** GitHub's own `actions/*` plus **`astral-sh/setup-uv`** and **`pnpm/action-setup`**, used only in `.github/workflows/`. Each is pinned to a full commit SHA with a `# vX.Y.Z` comment, and none ships in the app. The uv version in `setup-uv` stays in step with the local uv that writes `apps/api/uv.lock`; pnpm comes from `packageManager`. A new third-party action still needs approval. |

## A. Dependencies needing approval (CLAUDE.md: ask before adding)

| ID | Question | Raised in | Blocks | Status |
|---|---|---|---|---|
| A1 | ASGI server: uvicorn (FastAPI can't run without one) | E-1 | 0 | answered |
| A2 | Postgres driver: psycopg 3 or asyncpg. SQLAlchemy sessions: sync or async | E-1 | 0 | answered |
| A3 | Python test stack: pytest, pytest-cov, an async test plugin. Factories: factory_boy (named in CLAUDE.md) or polyfactory | E-1 | 0 | answered |
| A4 | Generator for the TypeScript client from the OpenAPI spec: openapi-typescript + openapi-fetch, orval, or hey-api | E-1 | 0 | answered |
| A5 | Web router (React Router or TanStack Router) and mobile navigation (Expo Router or React Navigation) | E-1 | 0 | answered |
| A6 | Frontend test and lint tools (Testing Library, jsdom, jest-expo, typescript-eslint, React ESLint plugins). Prettier: yes or no | E-1 | 0 | answered |
| A7 | Pre-commit hook tool | E-1 | 0 | answered |
| A8 | End-to-end test tools: Playwright (web), Maestro (mobile) | E-1, E-6, E-11 | 0 | answered |
| A9 | Password hashing: standard-library `hashlib.scrypt` or `argon2-cffi` | E-2 | 0 | answered |
| A10 | `expo-secure-store` for token storage on the phone | E-1, E-2 | 0 | answered |
| A11 | Streaming JSON parser for the Scryfall bulk files: `ijson` or a standard-library reader | E-3 | 1 | answered |
| A12 | Postgres extensions `pg_trgm` and `unaccent` for fuzzy card search | E-3 | 1 | answered |
| A13 | `react-native-svg` for mana symbols on mobile | E-3 | 1 | answered |
| A14 | QR code rendering library (JavaScript, or SVG drawn on the server in Python) | E-6 | 1 | answered |
| A15 | `redis` (redis-py) as a direct dependency for pub/sub. A `websockets` client for the multi-process soak tests | E-6 | 1 | answered |
| A16 | Mobile chart library, or lists and tiles only on mobile | E-4, E-7, E-8, E-9 | 1 | answered |
| A17 | Error tracking and observability tools (for example Sentry, uptime checks, metrics) | E-11 | 5 | answered |
| A18 | `pip-audit` (or similar) for checking Python dependencies for known vulnerabilities | E-11 | 5 | answered |
| A19 | EAS Update (over-the-air updates for the mobile app) | E-11 | 5 | answered |

## B. Technical defaults

| ID | Question | Raised in | Blocks | Status |
|---|---|---|---|---|
| B1 | Primary keys: UUID or bigint identity | E-1 | 0 | answered |
| B2 | List paging: cursor (keyset) or offset | E-1 | 0 | answered |
| B3 | Token lifetimes (proposed: 15-minute access, 30-day refresh). On web, keep the refresh token in an HttpOnly cookie | E-1, E-2 | 0 | answered |
| B4 | Shared TS card helpers: a new `packages/card-utils`, or inside `api-client` | E-3 | 1 | answered |
| B5 | Stats storage: a per-seat fact table refreshed by arq (proposed), or Postgres materialized views. How often to run a full rebuild | E-7 | 1 | answered |
| B6 | Deep links for invites and joining games: a custom URL scheme for the MVP, with universal/app links later | E-2, E-6 | 1 | answered |
| B7 | Daily Scryfall sync time (proposed: 09:00 UTC) | E-3 | 1 | answered |
| B8 | Week start (ISO Monday or Sunday) and the default time zone | E-8, E-9 | 2 | answered |

## C. Identity, privacy and guests

| ID | Question | Raised in | Blocks | Status |
|---|---|---|---|---|
| C1 | Login with an unverified email, or with a username | E-2 | 0 | answered |
| C2 | Admin password reset scope: can an admin reset another admin, or a user who is also in other playgroups? (account-takeover risk) | E-2 | 0 | answered |
| C3 | Who can create playgroups and send invites (proposed: anyone can create; only admins invite). Is there a cap on how many playgroups a user can join? | E-2 | 0 | answered |
| C4 | Guests: do they appear in stats, profiles, activity and league standings? When a guest is linked to an account, is their history merged and stats rebuilt? Can two guests be merged into one account? | E-2, E-6, E-7, E-8, E-10 | 1 | answered |
| C5 | Deck visibility: owner only, or all playgroup members (needed when picking decks for seats) | E-4 | 1 | answered |
| C6 | Logged-in non-members who join with a game code: added to the playgroup, or treated as guests? How long is a guest's game token valid? | E-6 | 1 | answered |
| C7 | When a member leaves: do their games stay in the playgroup's stats, and can they still see them? | E-7 | 2 | answered |
| C8 | Profiles: do visitors see the owner's custom layout? Can a player hide sections? Is spend visible only to its owner, or opt-in shareable? | E-8 | 2 | answered |
| C9 | Deleting an account or a playgroup, and exporting a person's data (also needed for the app-store privacy forms) | E-2, E-11 | 5 | answered |
| C10 | Avatars: color and initials only (proposed), or image upload (needs object storage) | E-2 | 1 | answered |

## D. Live game rules

| ID | Question | Raised in | Blocks | Status |
|---|---|---|---|---|
| D1 | Edit permissions ("trust mode"): any seated player can change any seat, or only their own seat plus events they cause? Per-game setting? | E-6 | 1 | answered |
| D2 | Undo: how far back (number of events or seconds), and can the target of an event undo it, or only the player who caused it and the host? | E-6 | 1 | answered |
| D3 | The 21 commander-damage and 10 poison thresholds and the starting life total: fixed, or configurable through house rules? | E-2, E-6 | 1 | answered |
| D4 | Does "turn count" mean rounds or individual turns? (Both are tracked; stats and leagues need one definition) | E-6, E-7 | 1 | answered |
| D5 | The list of win-condition categories | E-6, E-7 | 1 | answered |
| D6 | Draws, shared wins and simultaneous eliminations: how are they recorded and scored? | E-6, E-7, E-10 | 1 | answered |
| D7 | Can the host amend results after the game ends? If so, how is the change recorded and passed on to stats? | E-6 | 1 | answered |
| D8 | Should idle lobbies and games be abandoned automatically (for example after 24 h)? | E-6 | 1 | answered |
| D9 | Can the host change seating mid-game, or only in the lobby? | E-6 | 1 | answered |
| D10 | Spectators: can they see decklists and warnings? Maximum number (proposed 20). In host-controls-all mode, can the host lock players' devices? | E-6 | 1 | answered |
| D11 | "Each opponent loses N" group drains: in Phase 1, or later? | E-6 | 1 | answered |

## E. Decks, legality and brackets

| ID | Question | Raised in | Blocks | Status |
|---|---|---|---|---|
| E1 | Can an import be saved after explicitly skipping unknown lines? | E-4 | 1 | answered |
| E2 | Can house rules also allow officially banned cards ("rule 0"), or only add bans? Which playgroup's house bans apply to a deck? | E-2, E-4 | 1 | answered |
| E3 | Default printing when none is given: Scryfall's default printing, or one the user owns (after E-9) | E-3, E-4, E-9 | 1 | answered |
| E4 | Moxfield/Archidekt: do their terms allow this use, do we need to ask for API access, which contact goes in the User-Agent, and how long to cache imports | E-4 | 1 | answered |
| E5 | Companion: check its deckbuilding condition automatically, or show a "check this yourself" warning? Does the companion count toward the bracket? | E-4, E-5 | 1 | answered |
| E6 | Who checks the seeded bracket rule set against the current official WotC rules, and who maintains the tutor, mass-land-denial and extra-turn lists? | E-5 | 1 | answered |
| E7 | Commander Spellbook: how its tags map to the "two-card combo" rule, whether a combo that uses the commander counts, its rate limits and terms, cache time, and per-deck queries vs. a bulk sync | E-5 | 1 | answered |
| E8 | Which bracket is used for seating and matchmaking when a bracket 1–2 deck has no declared intent? | E-5, E-10 | 1 | answered |
| E9 | Publishing a rule set: command-line tool only, or a global admin role in the app? Notify owners when a new rule set changes their deck's bracket? | E-5 | 1 | answered |
| E10 | In-app card editing (S-4.4.6) in the MVP, or re-import until the sorting board? | E-4 | 1 | answered |
| E11 | Store Scryfall's `game_changer` flag as a cross-check? Show Spellbook's bracket estimate next to ours? | E-3, E-5 | 1 | answered |
| E12 | Tokens, emblems and art-series cards: keep them out of the database? | E-3 | 1 | answered |
| E13 | Sorting-board draft: live sync across devices, or reload on conflict? | E-4 | 2 | answered |

## F. Stats and activity

| ID | Question | Raised in | Blocks | Status |
|---|---|---|---|---|
| F1 | Small-sample thresholds: default values, and configurable per playgroup? Do meta shares reuse the same thresholds? | E-7, E-8 | 1 | answered |
| F2 | Bracket vs. outcome stats: use the bracket assessment current when the game was played, or the latest one? | E-7 | 2 | answered |
| F3 | Show stats for each partner commander separately, as well as per pair? | E-7 | 2 | answered |
| F4 | Default dashboard and profile widgets. Web grid: one layout per screen-size breakpoint, or one layout that reflows? | E-7 | 2 | answered |
| F5 | Card leaderboard: hide basic lands and near-universal staples? Card stats filtered by month, or by day? | E-7 | 2 | answered |
| F6 | Streak unit (proposed: weeks). Do activity numbers combine all of a player's playgroups? Meta shares: top 8 plus "Other"? | E-8 | 2 | answered |
| F7 | Season filter on stats: tracked in E-7 or E-10? | E-7, E-10 | 4 | answered |

## G. Collection and budget

| ID | Question | Raised in | Blocks | Status |
|---|---|---|---|---|
| G1 | Currency: one display currency per user? Is conversion needed (that means a new exchange-rate source)? Which Scryfall price is used for value? | E-9 | 3 | answered |
| G2 | Price snapshot fields (add `usd_etched`, `eur_foil`, `tix`?), how long snapshots are kept, and whether to store latest prices for every printing | E-3, E-9 | 3 | answered |
| G3 | How the cost of opened sealed product is split across the cards pulled. Do shipping and tax go into a card's cost basis? | E-9 | 3 | answered |
| G4 | Ownership matching: any printing or the exact printing; does one copy count for every deck that uses it; how are proxies handled. Cost to complete: cheapest printing or the listed one | E-9 | 3 | answered |
| G5 | Re-import: rows missing from the new file are deleted, set to zero, or left alone? Maximum file size. Collection CSV export? | E-9 | 3 | answered |
| G6 | Condition scale and mapping from each source. Does condition or language adjust value? | E-9 | 3 | answered |
| G7 | Sealed product has no Scryfall price: accept that, or add another price source (needs approval)? | E-9 | 3 | answered |
| G8 | Who supplies sample CSV exports (Moxfield, Archidekt, ManaBox, Deckbox)? | E-9 | 3 | answered |
| G9 | Foreign-language printings: on-demand lookups, or the `all_cards` bulk file (about 2 GB)? | E-3 | 3 | answered |

## H. Leagues

| ID | Question | Raised in | Blocks | Status |
|---|---|---|---|---|
| H1 | Are games linked to the active season automatically or by opt-in? Can seasons overlap? Do players opt in to a season roster? | E-10 | 4 | answered |
| H2 | Scoring presets and achievements to ship. Hand-awarded achievements? If scoring changes mid-season, rescore or apply to later games only? | E-10 | 4 | answered |
| H3 | Tiebreaker order, and the minimum number of games to be ranked | E-10 | 4 | answered |
| H4 | Matchmaking: fairness metric and weights, win-rate window, how far back "repeat pairing" looks, preferred pod sizes, whether it also sets seat order | E-10 | 4 | answered |

## I. Operations, accounts and legal

| ID | Question | Raised in | Blocks | Status |
|---|---|---|---|---|
| I1 | GitHub plan (Free, Pro or Team). This decides branch protection, required reviewers and secret scanning on a private repo | E-1, E-11 | 0 | answered |
| I2 | Source of truth after publishing: GitHub Issues or these markdown files | E-1 | 0 | answered |
| I3 | Add a web/mobile frontend subagent role (the Desktop roles are backend-only) | E-1 | 0 | answered |
| I4 | Host choice, a staging environment, a monthly budget ceiling, domain and DNS ownership | E-11 | 5 | answered |
| I5 | Backups: retention, RPO and RTO (possible starting point: 14 days, 24 h, 4 h). Where alerts are sent | E-11 | 5 | answered |
| I6 | Container registry (GitHub Container Registry or the host's). One or two production API instances | E-11 | 5 | answered |
| I7 | Who owns the Apple Developer and Google Play accounts. Is the EAS free build quota enough? | E-11 | 5 | answered |
| I8 | WotC Fan Content Policy disclaimer; Scryfall data and image terms; hotlinking card images vs. caching them | E-3, E-11 | 1 | answered |
