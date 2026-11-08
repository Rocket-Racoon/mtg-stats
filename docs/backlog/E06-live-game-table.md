# E-6 — Live Game Table

| | |
|---|---|
| **Phase(s)** | 1 (MVP) |
| **Goal** | Run a live Commander game for 2–8 players. Each player can use their own phone, or one device can run the whole table. The server is authoritative and keeps an append-only event log. Clients update the screen right away, and the log feeds undo, results and stats. Each game has a trust mode (casual or strict) chosen by the host, and records the life and threshold settings it was played with. |
| **Success measures** | • Local optimistic update ≤ 50 ms after a tap; server ack p95 ≤ 250 ms and broadcast to every other client p95 ≤ 500 ms (same region / LAN).<br>• Zero lost or duplicated events in the multi-process soak test (8 players + 4 spectators, 10 events/s for 60 s, with one API process restarted).<br>• After the network returns, a reconnecting client matches the server state within 2 s.<br>• 100% of the JSON vectors in `packages/game-core/vectors/` pass in both the TS and Python reducers on every CI run. |
| **Out of scope** | Tracking cards, the battlefield or the stack · turn timers / chess clocks · in-game chat or voice · replay viewer (F-7.3) · league scoring (E-10) · Planechase / Archenemy variants · one-to-many effects other than the group drain (S-6.1.7) · shared wins (D6: a game has one winner, or `no_winner = true` for a full draw) · "Draw" as a win-condition category (a draw is `no_winner`, not a D5 code) · **Won't (D10):** the host locking seated players' devices to read-only in host-controls-all mode |

---

## F-6.1 — Game state core (TS + Python, shared vectors)

A pure, deterministic reducer that turns an ordered list of game events into game state (life, commander damage per commander, poison, counters, turn and round, eliminations). Starting life and the commander-damage and poison thresholds are game settings, never constants (D3). It exists twice: in TypeScript (`packages/game-core`) for clients and in Python (`apps/api`) for the authoritative server. Both must pass the same JSON test vectors in `packages/game-core/vectors/`.

Phase 1 · Priority Must

#### S-6.1.1 — Game state and event contract

**As a** developer **I want** one written contract for the game state, the event envelope and every event type **so that** the TS and Python reducers can't drift apart.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | shared, api | 1 | F-1.1 |

**Acceptance criteria**
- **Given** the contract **When** a developer reads it **Then** it defines the event envelope `event_id` (client-generated UUID v4), `game_id`, `seq` (server-assigned, absent until acked), `type`, `payload`, `actor`, `on_behalf_of_seat` (nullable), `ts` (server UTC) and `v` (schema version), plus the state shape and the list of reducer error codes. The state includes the game settings `starting_life`, `commander_damage_threshold`, `poison_threshold` and `trust_mode` (`casual` | `strict`).
- **Given** any game state **When** it is serialized by the TS or the Python implementation **Then** the canonical JSON is byte-identical (sorted keys, integers only, no floats, no locale-dependent output).
- **Given** a reducer call **When** it runs **Then** it reads no clock, randomness or I/O: timestamps and random choices (for example, a random first player) arrive inside event payloads.
- **Given** an event with an unknown `type` or an unsupported `v` **When** either reducer receives it **Then** it is rejected with `unknown_event_type` or `unsupported_version`.

**Tasks**
- [ ] T-6.1.1.1 (shared) Write `packages/game-core/CONTRACT.md`: envelope, state shape, event-type registry, error codes
- [ ] T-6.1.1.2 (shared) TS types for the state, envelope, events and error codes, plus a canonical JSON serializer
- [ ] T-6.1.1.3 (api) Pydantic v2 models that mirror the contract in `app/games/` and a matching canonical serializer
- [ ] T-6.1.1.4 (shared) Reducer entry points `apply(state, event) → state | error` in both languages, starting with no-op handlers

#### S-6.1.2 — Shared JSON test vectors and runners

**As a** developer **I want** the same JSON test vectors to run against both reducers **so that** a rule change can't ship in only one language.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | shared, api, infra | 1 | S-6.1.1, F-1.8 |

**Acceptance criteria**
- **Given** a vector file in `packages/game-core/vectors/` with `settings`, `events` and `expect` (a final state, or the index of the rejected event plus its error code) **When** the TS suite (Vitest) and the Python suite (pytest) run **Then** both execute every vector file and compare canonical JSON.
- **Given** a vector that passes in one implementation and fails in the other **When** CI runs **Then** the verify gate fails and names the vector file.
- **Given** an event type in the contract registry that no vector exercises **When** either runner starts **Then** it fails with `uncovered event type: <type>`.
- **Given** a malformed vector file **When** the runners load it **Then** they fail and name the file and the bad field.

**Tasks**
- [ ] T-6.1.2.1 (shared) Vector file format spec and a validator for it
- [ ] T-6.1.2.2 (shared) Vitest runner that loads every file in `vectors/`
- [ ] T-6.1.2.3 (api) pytest runner, parametrized per vector file
- [ ] T-6.1.2.4 (infra) Add both runners and the event-type coverage check to the CI verify gate

#### S-6.1.3 — Life totals, concession and life-based elimination

**As a** player **I want** life changes and eliminations to follow Commander rules **so that** the tracker matches the real table.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | shared, api | 1 | S-6.1.2 |

**Acceptance criteria**
- **Given** a game started with default settings **When** the state is computed **Then** every seat has 40 life. **Given** `starting_life = 30` (from house rules or a lobby override) **Then** every seat has 30. Values outside 1–999 are rejected with `invalid_setting`.
- **Given** seat 3 at 3 life **When** `life_changed {seat: 3, delta: -3, source_seat: 1}` is applied **Then** seat 3 has 0 life and is eliminated with reason `life`, `eliminated_by = 1` and `eliminated_at_seq` equal to that event's seq.
- **Given** seat 2 at 5 life **When** `life_changed {delta: -8}` is applied **Then** life is recorded as -3 (not clamped) and seat 2 is eliminated with `eliminated_by = null`.
- **Given** seat 2 is eliminated **When** any event other than a revert or a host correction targets seat 2 **Then** it is rejected with `seat_eliminated`.
- **Given** an active game **When** `player_conceded {seat: 4}` or a host's `player_eliminated {seat: 4, cause}` is applied **Then** seat 4 is eliminated with reason `concession` or `declared`, whatever its life. `cause` is optional and, when given, must be one of the D5 win-condition codes (for example `mill`, `combo`, `alternate_win`). A `delta` of 0 or with |delta| > 999 is rejected with `invalid_amount`.
- **Given** one event that eliminates two or more seats (for example a group drain, S-6.1.7) **When** it is applied **Then** every one of them gets the same `eliminated_at_seq`, which marks them as eliminated at the same time (they will share a placement, D6).
- **Given** one event that eliminates every seat still in the game **When** it is applied **Then** no seat is left standing, and the state records a provisional full draw (`no_winner = true`) in which every seat eliminated by that event is a drawing seat; seats eliminated earlier stay losses (D6, X1). S-6.7.1 picks this up.

**Tasks**
- [ ] T-6.1.3.1 (shared) TS handlers: `life_changed` (optional `source_seat`), `player_conceded`, `player_eliminated` (optional `cause`), and the elimination bookkeeping, including simultaneous eliminations
- [ ] T-6.1.3.2 (api) Python handlers with the same behavior
- [ ] T-6.1.3.3 (shared) ≥ 14 vectors: starting life, the boundary at 0, negative life, concession, events on eliminated seats, invalid amounts, simultaneous elimination, everyone eliminated at once

#### S-6.1.4 — Commander damage per commander source

**As a** player **I want** commander damage tracked separately for each enemy commander **so that** 21 (or the game's threshold) from a single commander knocks me out, but damage from two partners doesn't add up.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | shared, api | 1 | S-6.1.3, S-6.1.6 |

**Acceptance criteria**
- **Given** seat 1 has one commander **When** `commander_damage {source_seat: 1, commander: 0, target_seat: 3, amount: 7}` is applied three times **Then** seat 3's tally from source `1:0` is 21, its life is 19, and it is eliminated with reason `commander_damage` and `eliminated_by = 1`.
- **Given** seat 2 has partner commanders A (`2:0`) and B (`2:1`) **When** A deals 12 and B deals 12 to seat 4 **Then** seat 4 shows two tallies of 12, has 16 life and is **not** eliminated.
- **Given** a `commander_damage` event **When** it is applied **Then** the tally and the life loss change in that one event, so reverting it restores both.
- **Given** one event that takes seat 4 to ≥ 21 from one commander and to ≤ 0 life at the same time **When** it is applied **Then** the elimination reason is `commander_damage` (precedence: `commander_damage` > `poison` > `life`).
- **Given** an amount outside 1–999, or a commander index the source seat doesn't have **When** it is applied **Then** it is rejected with `invalid_amount` or `unknown_commander`.
- **Given** a game with `commander_damage_threshold = 15` **When** one commander's tally on a seat reaches 15 **Then** that seat is eliminated with reason `commander_damage`. The default is 21; values outside 1–99 (the range S-2.5.6 allows for playgroup defaults) are rejected with `invalid_setting`.

**Tasks**
- [ ] T-6.1.4.1 (shared) State: `commander_damage[target][source_key]` where `source_key = "<seat>:<commander index>"`; TS handler that reads `commander_damage_threshold` from the settings
- [ ] T-6.1.4.2 (api) Python handler
- [ ] T-6.1.4.3 (shared) Vectors: single commander to 21, partners tracked separately, precedence, invalid source, a non-default threshold

#### S-6.1.5 — Poison and player counters

**As a** player **I want** poison, energy, experience, commander tax, monarch and initiative tracked **so that** I don't need dice or paper next to my phone.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | shared, api | 1 | S-6.1.3, S-6.1.6 |

**Acceptance criteria**
- **Given** seat 2 with 9 poison and the default `poison_threshold` of 10 **When** `poison_changed {seat: 2, delta: 1, source_seat: 4}` is applied **Then** seat 2 has 10 poison and is eliminated with reason `poison`, `eliminated_by = 4`. **Given** `poison_threshold = 15` **Then** seat 2 is eliminated only at 15. Threshold values outside 1–99 (as in S-2.5.6) are rejected with `invalid_setting`. Any change that would take poison, energy or experience below 0 is rejected with `invalid_amount`.
- **Given** seat 2 with partners A and B **When** `commander_cast {seat: 2, commander: 1, delta: 1}` is applied twice **Then** B's cast count is 2 and its tax is 4, while A's stays 0 / 0. A `delta` of -1 is allowed down to a count of 0.
- **Given** seat 1 is the monarch **When** `monarch_set {seat: 3}` is applied **Then** seat 3 is the only monarch. `initiative_set` works the same way, independently of the monarch.
- **Given** seat 3 is the monarch and seat 1 is the active player **When** seat 3 is eliminated **Then** seat 1 becomes the monarch. If the eliminated monarch was the active player, the next non-eliminated seat in turn order becomes the monarch. The initiative follows the same rule.

**Tasks**
- [ ] T-6.1.5.1 (shared) TS handlers: `poison_changed`, `counter_changed` (energy, experience), `commander_cast`, `monarch_set`, `initiative_set`, and the hand-off of monarch / initiative on elimination
- [ ] T-6.1.5.2 (api) Python handlers
- [ ] T-6.1.5.3 (shared) Vectors for every counter, the poison elimination at the default and a non-default threshold, partner tax and the monarch / initiative hand-off

#### S-6.1.6 — Lobby, seat and turn events

**As a** host **I want** seating, deck choice, game start and turn passing to be events too **so that** the lobby and the turn order sync and replay like everything else.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | shared, api | 1 | S-6.1.2 |

**Acceptance criteria**
- **Given** a lobby with 4 seats **When** `seat_moved {from: 4, to: 2}` is applied **Then** seats 4 and 2 swap places in seat order.
- **Given** an active game where seat 3 is the active player **When** `seat_moved {from: 4, to: 2, correction: true}` is applied **Then** the seats swap, the same player stays active, the turn and round counters don't change, and later `turn_passed` events follow the new order (D9). **Given** an active game and a `seat_moved` without `correction: true` **Then** it is rejected with `invalid_phase`. (Only the host may send it, see S-6.3.3.)
- **Given** a lobby **When** `settings_changed` sets `trust_mode` to `strict` **Then** the state's trust mode is `strict`. A value other than `casual` or `strict` is rejected with `invalid_setting`, and any `settings_changed` after `game_started` is rejected with `invalid_phase`.
- **Given** a lobby with 8 seats **When** `seat_joined` is applied **Then** it is rejected with `table_full`. **Given** a lobby with 1 seat **When** `game_started` is applied **Then** it is rejected with `not_enough_players`.
- **Given** `game_started {first_seat: 3}` **When** it is applied **Then** seat 3 is active, the turn is 1, the round is 1 and every seat is at starting life.
- **Given** seat 3 is active in a 4-seat game where seat 4 is eliminated **When** `turn_passed` is applied **Then** seat 1 becomes active (seat 4 is skipped), the turn counter goes up by 1, and the round goes up by 1 when play returns to the first player (or to the next surviving seat after them). The round is the game's "turn count" for results and stats (D4); individual turns stay in the log.

**Tasks**
- [ ] T-6.1.6.1 (shared) TS handlers: `game_created`, `settings_changed` (starting life, both thresholds, trust mode), `seat_joined`, `seat_left`, `seat_moved` (lobby swap and mid-game correction), `deck_assigned`, `commanders_set` (1–2 commanders per seat), `game_started`, `turn_passed`
- [ ] T-6.1.6.2 (api) Python handlers
- [ ] T-6.1.6.3 (shared) Vectors: seat limits, swaps in the lobby and as a mid-game correction, the start rules, settings validation, turn and round counting with eliminated seats

#### S-6.1.7 — Group drain: each opponent loses N

**As a** player **I want** "each opponent loses N" (and optionally "I gain the total") recorded as one event **so that** the most common Commander drain takes one action and undoes in one step.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | shared, api | 1 | S-6.1.3 |

**Acceptance criteria**
- **Given** a 4-seat game with every seat at 40 **When** `group_drain {source_seat: 1, amount: 3, gain: true}` is applied **Then** seats 2, 3 and 4 have 37 life and seat 1 has 49 (the total of 9). **Given** `gain: false` **Then** seat 1 stays at 40.
- **Given** seat 4 is already eliminated **When** the same group drain is applied **Then** only seats 2 and 3 lose 3, and seat 1 gains 6.
- **Given** seats 2 and 3 are at 2 life **When** a group drain of 3 is applied **Then** both are eliminated with reason `life`, `eliminated_by = 1` and the same `eliminated_at_seq`. If that leaves only seat 1, seat 1 is the provisional winner (S-6.7.1).
- **Given** an amount outside 1–999, or a source seat that is eliminated **When** it is applied **Then** it is rejected with `invalid_amount` or `seat_eliminated`.
- **Given** a group drain **When** it is reverted (S-6.6.1) **Then** every seat it touched, and any eliminations it caused, are restored together.

**Tasks**
- [ ] T-6.1.7.1 (shared) `group_drain` in the contract registry and the TS handler
- [ ] T-6.1.7.2 (api) Python handler with the same behavior
- [ ] T-6.1.7.3 (shared) ≥ 6 vectors: with and without gain, eliminated opponents skipped, simultaneous eliminations, last opponent eliminated, invalid amount

---

## F-6.2 — Game lifecycle (lobby, join by QR/code)

Create a game inside a playgroup with its trust mode and rule settings, gather players in a lobby (by short code or QR code, including guests and logged-in non-members), assign a deck version or commanders to each seat, and start. Deck validation problems are shown as warnings, never as blockers. Idle lobbies and games are abandoned automatically and can be resumed by the host.

Phase 1 · Priority Must

#### S-6.2.1 — Create a game in a playgroup

**As a** playgroup member **I want** to create a game with its settings **so that** my pod can start joining.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 1 | S-6.1.6, S-6.3.1, S-2.4.1, S-2.5.6 |

**Acceptance criteria**
- **Given** a playgroup member in a playgroup with no house-rule overrides **When** they create a game with default settings **Then** a game in status `lobby` is created with `starting_life = 40`, `commander_damage_threshold = 21`, `poison_threshold = 10`, `trust_mode = casual`, the creator as host and a short code, and its log begins with `game_created`.
- **Given** a playgroup whose game defaults (S-2.5.6) set starting life to 30 and poison to 15 **When** a game is created there **Then** the form is pre-filled with 30 / 21 / 15, read through `game_defaults(playgroup_id)`, and those values go into `game_created`. The creator can override any of them for this game.
- **Given** the creator picks "Strict" trust mode **When** the game is created **Then** `trust_mode = strict` is in `game_created`, and the form explains both modes in one line each ("Casual: anyone can edit any seat" / "Strict: players edit their own seat and the damage they deal; the host edits everything").
- **Given** the creator chose "Take a seat" (the default) **When** the game is created **Then** the host sits in seat 1. Otherwise the host has no seat and controls the game only.
- **Given** a user who is not a member of the playgroup **When** they try to create a game in it **Then** the API returns 403.
- **Given** `starting_life` of 0 or 1000, or a threshold outside 1–99 **When** the game is created **Then** the API returns 422 and names the field.

**Tasks**
- [ ] T-6.2.1.1 (api) `Game` and `GameSeat` SQLAlchemy models plus an Alembic migration; status enum `lobby | active | ending | finished | abandoned`; `games.state` JSONB snapshot and `games.last_seq`; columns for `starting_life`, `commander_damage_threshold`, `poison_threshold` and `trust_mode`
- [ ] T-6.2.1.2 (api) `create_game` service and `POST /playgroups/{id}/games` (thin router); defaults read from `game_defaults(playgroup_id)` in `playgroups/services` (S-2.5.6), which returns 40 / 21 / 10 for a playgroup that never changed them
- [ ] T-6.2.1.3 (web) "New game" form (starting life, commander-damage and poison thresholds, trust mode, take a seat) that opens the lobby
- [ ] T-6.2.1.4 (mobile) "New game" screen with the same fields

#### S-6.2.2 — Join a game by short code

**As a** player at the table **I want** to type a short code **so that** I can join from my own phone in seconds, with or without an account.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 1 | S-6.2.1, S-2.3.1, S-2.3.2 |

**Acceptance criteria**
- **Given** a new game **When** its code is generated **Then** it has 6 characters from an alphabet without look-alikes (no `0 O 1 I L`), is case-insensitive, and is unique among games in `lobby`, `active` or `ending`.
- **Given** a logged-in user enters a valid lobby code **When** they join **Then** they take the next free seat (`seat_joined`) and see the lobby within 1 s. Joining again returns the same seat.
- **Given** someone without an account enters the code and a display name **When** they join **Then** a guest player is created through F-2.3, a game-scoped token is issued that only works for this game's room, and the guest is seated. The guest plays fully, like any other seat, and their results count in stats (C4).
- **Given** a guest's game-scoped token **When** the game is open, or less than 24 h has passed since it became `finished` or `abandoned` **Then** the token is accepted for this game (so the guest can see the final results). **When** more than 24 h has passed since the game ended or was abandoned, or the token is more than 7 days old (the hard cap, S-2.3.2) **Then** it is rejected with 401, whichever comes first (D8, X11). The 24 h rule is checked against the game's current end time on the server, because that time isn't known when the token is issued; if the host resumes the game (S-6.2.9) the game is open again, so a token still inside its 7-day cap works again.
- **Given** a logged-in user who is **not** a member of the game's playgroup **When** they join with the code **Then** they are seated under their own account for this game only: they are not added to the playgroup, get no access to its other pages, and the seat is marked "non-member" for S-6.7.7 (C6).
- **Given** an unknown code or the code of a finished or abandoned game **When** it is submitted **Then** the user sees "Code not valid". After 10 failed attempts from one IP within 1 minute, the API returns 429.
- **Given** a full table (8 seats) or a game already `active` that the user has no seat in **When** they enter its code **Then** they are offered spectating (S-6.8.1) rather than a seat.

**Tasks**
- [ ] T-6.2.2.1 (api) Code generator and `POST /games/join {code}` with a Redis-backed rate limit on failed attempts
- [ ] T-6.2.2.2 (api) Game-scoped guest JWT (claims: `player_id`, `game_id`, `role`), issued through the S-2.3.2 guest-token service (7-day hard `exp`), plus the games-service check S-2.3.2 calls, which refuses it 24 h after the game ends or is abandoned
- [ ] T-6.2.2.3 (web) `/join` and `/join/:code` routes at the https web URL every shared join link uses (X2)
- [ ] T-6.2.2.4 (mobile) Join screen, the `mtgstats://join/<code>` deep link used for in-app navigation and as the target of the web page's "Open in app" button (expo-linking, B6, X2), and the guest token kept in expo-secure-store
- [ ] T-6.2.2.5 (api) Seat a logged-in non-member under their own account without creating a membership; mark the seat `non_member`

#### S-6.2.3 — Join a game by QR code

**As a** player **I want** to scan a QR code on the host's screen **so that** I don't have to type anything.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web, mobile | 1 | S-6.2.2 |

**Acceptance criteria**
- **Given** a lobby **When** the host opens "Invite" **Then** a QR code of at least 200×200 px encoding the https web URL `<PUBLIC_WEB_URL>/join/<CODE>` is shown with the code as text underneath. The QR code never encodes a `mtgstats://` link (X2). The SVG is drawn on the server with `qrcode` (A14) and served by `GET /games/{id}/qr.svg` to the host only.
- **Given** the mobile app's in-app scanner **When** it reads that QR code **Then** the join flow opens in the app with the code filled in.
- **Given** any phone's system camera, or a tapped join link **When** it opens `<PUBLIC_WEB_URL>/join/<CODE>` **Then** the web join page opens with the code filled in and lets the person join in the browser. It also shows an "Open in app" button that follows `mtgstats://join/<CODE>` (B6, X2) and opens the app's join flow with the code filled in when the app is installed. Universal and App Links replace this button in Phase 5 (S-11.5.6).
- **Given** the "Copy join link" or "Share" action **When** the host uses it **Then** it copies or shares only the https web URL `<PUBLIC_WEB_URL>/join/<CODE>`; `mtgstats://` is used only for in-app navigation and the "Open in app" button.
- **Given** camera permission is denied **When** the user opens the scanner **Then** they see a message and the code-entry field instead.

**Tasks**
- [ ] T-6.2.3.1 (web) Show the server-drawn QR SVG in the lobby's Invite panel, plus "Copy join link" (https URL); "Open in app" button on the `/join/:code` page linking to `mtgstats://join/<CODE>`
- [ ] T-6.2.3.2 (mobile) Show the same QR SVG on the host's mobile lobby with react-native-svg (A13), plus a "Share" action for the https URL
- [ ] T-6.2.3.3 (mobile) In-app scanner with expo-camera barcode scanning and permission handling; reads the code from the https join URL (and also accepts a `mtgstats://join/` link)
- [ ] T-6.2.3.4 (api) `GET /games/{id}/qr.svg` (host only) rendering the join URL with `qrcode`'s SVG factory, with a test on the decoded content

#### S-6.2.4 — Assign a deck version or commanders to each seat

**As a** player **I want** to choose which deck I'm playing (or just my commander) **so that** commander damage sources are labelled and stats are recorded for the right deck.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 1 | S-6.2.2, S-4.4.3, S-4.5.1, F-3.2 |

**Acceptance criteria**
- **Given** a seated player with decks **When** they pick a deck **Then** its current version is assigned (`deck_assigned`) and that version's 1–2 commanders become the seat's commander-damage sources. An older version can be chosen from a version picker.
- **Given** the chosen version has F-4.5 validation errors (for example, a banned card or 99 cards) **When** it is assigned **Then** every participant sees a warning badge with the count and the reasons, and the game can still be started.
- **Given** a guest or a player with no decks **When** they search for and pick 1–2 commander cards (F-3.2) **Then** `commanders_set` is recorded. **Given** they pick nothing **Then** the seat gets a single unnamed source, "Commander".
- **Given** a `strict` game and a non-host player **When** they try to assign a deck or commanders to another seat **Then** it is rejected with `forbidden`. **Given** a `casual` game **Then** any seated player may do it (D1). The host can assign decks or commanders to any seat in both modes. A deck can only be assigned to its owner's seat.
- **Given** a participant viewing another seat's deck **When** they open its details **Then** the decklist is shown only if they could see that deck anyway (C5: they share a playgroup with its owner). Otherwise they see the deck name and commanders only.

**Tasks**
- [ ] T-6.2.4.1 (api) Validate `deck_assigned` / `commanders_set` through the decks public service (F-4.4), read the validation summary (F-4.5), and ask the decks service whether each viewer may see a decklist
- [ ] T-6.2.4.2 (web) Deck and version picker, plus commander search for seats without a deck
- [ ] T-6.2.4.3 (mobile) The same picker as a bottom sheet
- [ ] T-6.2.4.4 (web, mobile) Validation warning badge with an expandable list of reasons

#### S-6.2.5 — Lobby screen with a live roster

**As a** host **I want** to see who has joined, with which deck, as it happens **so that** I know when the table is ready.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web, mobile | 1 | S-6.2.1, S-6.3.4 |

**Acceptance criteria**
- **Given** 3 devices in a lobby **When** a 4th player joins **Then** the roster updates on all 3 devices within 1 s without a refresh.
- **Given** the roster **When** it is shown **Then** each seat shows its name, a guest badge, its deck or commanders, any validation warning and a connection dot.
- **Given** the host **When** they change starting life, a threshold or the trust mode **Then** `settings_changed` is recorded and every device shows the new value. Non-hosts see the settings read-only, with a "House rules" label on values that came from the playgroup and "Changed for this game" on lobby overrides.
- **Given** the host **When** they remove a seat, or a player taps "Leave" on their own seat **Then** `seat_left` is recorded and the seat disappears for everyone.

**Tasks**
- [ ] T-6.2.5.1 (web) Lobby screen bound to the shared sync engine
- [ ] T-6.2.5.2 (mobile) Lobby screen bound to the shared sync engine
- [ ] T-6.2.5.3 (api) Authorization for `settings_changed` (host only) and `seat_left` (host or the seat's own player)

#### S-6.2.6 — Start the game

**As a** host **I want** to start the game and choose who goes first **so that** everyone moves to the table together.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web, mobile | 1 | S-6.2.5 |

**Acceptance criteria**
- **Given** a lobby with ≥ 2 seats **When** the host taps "Start" and picks a first player **Then** `game_started` is recorded, the status becomes `active`, and every device switches to the table within 1 s.
- **Given** the host picks "Random" **When** the game starts **Then** the server chooses the first seat with a secure RNG and writes it into the event payload, and every device shows the same first player.
- **Given** a lobby with 1 seat **When** the host views it **Then** "Start" is disabled with the text "Need at least 2 players".
- **Given** a non-host **When** they send `game_started` **Then** it is rejected with `forbidden`.
- **Given** seats with validation warnings or no deck **When** the host taps "Start" **Then** the confirm dialog lists those warnings, and starting is still allowed.
- **Given** `game_started` commits **When** the game row is read **Then** its `starting_life`, `commander_damage_threshold`, `poison_threshold` and `trust_mode` columns hold the values the game is played with, and they can no longer change (D3).

**Tasks**
- [ ] T-6.2.6.1 (api) Start handler: host-only check, random first seat written into the payload, status change and the frozen settings columns in the same transaction as the event
- [ ] T-6.2.6.2 (web) Start dialog with first-player choice and the warnings summary
- [ ] T-6.2.6.3 (mobile) The same start dialog

#### S-6.2.7 — Find and resume my active games

**As a** player **I want** to get back into a game I'm part of **so that** closing the app or the browser tab doesn't lock me out.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api, web, mobile | 1 | S-6.2.1 |

**Acceptance criteria**
- **Given** I am seated in or hosting games in `lobby`, `active` or `ending` **When** I open the home screen **Then** those games are listed with their status and seat count.
- **Given** the app was closed during an active game **When** I reopen it **Then** a "Resume game" banner appears, and tapping it opens the table with the current state.
- **Given** a guest with a valid game-scoped token **When** they reopen the join link **Then** they return to their own seat, or to the final results if the game ended or was abandoned less than 24 h ago (within the token's 7-day cap, S-6.2.2).

**Tasks**
- [ ] T-6.2.7.1 (api) `GET /me/games?status=open`
- [ ] T-6.2.7.2 (web) "My games" list on home
- [ ] T-6.2.7.3 (mobile) "My games" list and the resume banner

#### S-6.2.8 — Cancel or abandon a game

**As a** host **I want** to cancel a lobby or abandon a game nobody will finish **so that** it doesn't stay open or affect stats.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | api, web, mobile | 1 | S-6.2.1 |

**Acceptance criteria**
- **Given** a lobby or an active game **When** the host confirms "Abandon" **Then** `game_abandoned {reason: "host"}` is recorded, the status becomes `abandoned`, its code stops working and every device shows "Game abandoned".
- **Given** an abandoned game **When** results are read for stats **Then** it is not included (see S-6.7.4), unless the host resumes it (S-6.2.9) and it later finishes.
- **Given** a non-host **When** they send `game_abandoned` **Then** it is rejected with `forbidden`.

**Tasks**
- [ ] T-6.2.8.1 (api) `game_abandoned` handler (with `reason`) and status change
- [ ] T-6.2.8.2 (web, mobile) Abandon action with a confirm dialog

#### S-6.2.9 — Abandon idle lobbies and games automatically, and resume them

**As a** host **I want** forgotten lobbies and games closed automatically, with a way to bring one back **so that** stale games don't sit open or count in stats, but a paused game isn't lost.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | shared, api, web, mobile | 1 | S-6.2.8, S-6.3.3, F-1.9 |

**Acceptance criteria**
- **Given** a lobby with no stored event for 2 h **When** the scheduled `abandon_idle_games` arq job runs **Then** `game_abandoned {reason: "idle"}` is recorded with actor `system`, the status becomes `abandoned`, its code stops working, and connected devices show "Game abandoned (idle)" (D8).
- **Given** an `active` or `ending` game with no stored event for 24 h **When** the job runs **Then** it is abandoned the same way. Presence and heartbeats don't count as activity.
- **Given** the job runs every 10 minutes **When** a game crosses its idle limit **Then** it is abandoned within 10 minutes, and running the job twice never records a second `game_abandoned`.
- **Given** an abandoned game (idle or by the host) **When** its host chooses "Resume" from "My games" **Then** `game_resumed` is recorded, the status returns to what it was before (`lobby`, `active` or `ending`), a new join code is issued, and seated players can reconnect to their seats.
- **Given** a non-host **When** they send `game_resumed` **Then** it is rejected with `forbidden`. Every other event on an abandoned game is still rejected with `game_closed`.
- **Given** an abandoned game that is never resumed **When** stats are computed **Then** it is left out (S-6.7.4).

**Tasks**
- [ ] T-6.2.9.1 (api) `abandon_idle_games` scheduled job (F-1.9) using an index on the game's last event time; idempotent
- [ ] T-6.2.9.2 (shared) `game_abandoned` reasons and `game_resumed` handlers in TS and Python, plus vectors (resume from each status, events refused while abandoned)
- [ ] T-6.2.9.3 (api) Host-only permission for `game_resumed` and issuing the new code
- [ ] T-6.2.9.4 (web, mobile) "Abandoned" section in "My games" with a "Resume" action for the host

#### S-6.2.10 — Freeze each seat's bracket when the game starts

**As a** stats consumer (S-7.2.5) **I want** each seat's deck bracket and rule-set version recorded at game start **so that** bracket-vs-outcome stats use the bracket the deck had when it was played, not a later re-assessment (F2).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 1 | S-6.2.6, S-6.2.4, S-5.3.4 |

**Acceptance criteria**
- **Given** a seat with an assigned deck version that has a bracket assessment **When** `game_started` commits **Then**, in the same transaction, the `GameSeat` stores `bracket_at_start` (the assessment's `effective_bracket`), `bracket_assumed` (its `effective_bracket_assumed`, so an undeclared Bracket 1–2 deck is stored as 2, E8) and `bracket_rule_set_version`, read through the bracket domain's public service.
- **Given** the latest assessment is `stale` or still running for an older rule set **When** the game starts **Then** the latest stored assessment and its own rule-set version are frozen; starting is never blocked.
- **Given** a seat with no deck (commanders only) or a deck version with no assessment **When** the game starts **Then** `bracket_at_start` is null and the seat counts as `unassessed`.
- **Given** a new rule-set version is published or the deck is re-assessed after the game started **When** the game's results are read **Then** the frozen values are unchanged. A host seating fix (D9) or a result amendment (S-6.7.6) doesn't change them either.
- **Given** another domain reads results **When** it calls `get_game_result` / `list_results` (S-6.7.4) **Then** each seat DTO includes `bracket_at_start`, `bracket_assumed` and `bracket_rule_set_version`.

**Tasks**
- [ ] T-6.2.10.1 (api) Alembic migration for the three bracket columns on `game_seats`
- [ ] T-6.2.10.2 (api) Read the latest assessment per seat in one query through the bracket public service and write it in the `game_started` transaction
- [ ] T-6.2.10.3 (api) Tests: assessed, assumed Bracket 2, stale assessment, unassessed and commanders-only seats, unchanged after a later re-assessment

---

## F-6.3 — Real-time sync (WebSocket, Redis, event log, reconnect)

Each game is a WebSocket room. The server checks the JWT, validates every event with the Python reducer, gives it the next sequence number, stores it in `game_events`, and fans it out to every API process through Redis pub/sub. Clients apply their own events right away, reconcile on the server's sequence number, and replay missed events after a reconnect. Events are idempotent by a client-generated `event_id`.

Phase 1 · Priority Must

#### S-6.3.1 — Event log storage and ordered append

**As a** developer **I want** an append-only event log with gap-free sequence numbers per game **so that** every client agrees on the order of events.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-6.1.1, F-1.4 |

**Acceptance criteria**
- **Given** the migration **When** it runs **Then** `game_events` has `game_id`, `seq`, `event_id`, `type`, `payload` (JSONB), `actor`, `on_behalf_of_seat`, `ts` and `v`, with unique constraints on `(game_id, seq)` and `(game_id, event_id)`, and the Alembic drift check passes.
- **Given** 20 concurrent appends to one game from 2 API processes **When** they all commit **Then** their seqs are exactly 1–20 with no gaps or duplicates.
- **Given** an `event_id` that is already stored **When** it is appended again **Then** the stored event (with its original seq) is returned and no new row is written.
- **Given** an append **When** it commits **Then** the event row, `games.state` and `games.last_seq` are written in one transaction. If anything fails, none of them change.
- **Given** the games repository **When** its public functions are listed **Then** it can only append and read events. Application code never updates or deletes `game_events` rows.

**Tasks**
- [ ] T-6.3.1.1 (api) `GameEvent` model and Alembic migration
- [ ] T-6.3.1.2 (api) Repository: `append`, `read_since(game_id, seq)`, `get_by_event_id`
- [ ] T-6.3.1.3 (api) `append_event` service: lock the game row (`SELECT … FOR UPDATE`), apply the Python reducer, write the event and the snapshot
- [ ] T-6.3.1.4 (api) Concurrency test with two engines and the idempotency tests

#### S-6.3.2 — Authenticated WebSocket game room

**As a** player **I want** my device to join the game room securely with my login **so that** only the people at my table can see and change the game.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-6.3.1, F-2.1, S-2.3.2 |

**Acceptance criteria**
- **Given** a client connects to `/ws/games/{id}` **When** it sends `{type: "auth", token}` with a valid JWT within 5 s **Then** it receives `welcome` with its role (`player`, `host` or `spectator`), its seat, the current state snapshot and `last_seq`.
- **Given** no auth message within 5 s or an invalid token **When** the timer runs out or the token is checked **Then** the socket closes with code 4401. A valid user who is not part of the game gets 4403, and an unknown game gets 4404.
- **Given** a guest token scoped to game A **When** it is used on game B **Then** the socket closes with 4403.
- **Given** the access token expires during a game **When** the server notices **Then** it sends `auth_expired`. Events are refused until the client re-authenticates with a fresh token, and the socket closes with 4401 if that doesn't happen within 30 s.
- **Given** a connected client **When** it misses heartbeat replies for 30 s (pings every 15 s) **Then** the server closes the socket and updates presence.

**Tasks**
- [ ] T-6.3.2.1 (api) WebSocket endpoint with the first-message auth handshake and close codes
- [ ] T-6.3.2.2 (api) Role resolution: participant, host, spectator, game-scoped guest
- [ ] T-6.3.2.3 (api) Re-auth on expiry and the heartbeat
- [ ] T-6.3.2.4 (shared) Message types (`auth`, `welcome`, `auth_expired`, `error`) added to the contract

#### S-6.3.3 — Submit, validate and acknowledge events

**As a** player **I want** each of my actions confirmed or refused by the server **so that** the table never disagrees about the game state.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-6.3.2, S-6.1.3, S-6.1.4, S-6.1.5, S-6.1.6 |

**Acceptance criteria**
- **Given** the player in seat 2 **When** they submit `life_changed {seat: 2, delta: -3}` with a new `event_id` **Then** the server validates it, applies it, stores it, replies `ack {event_id, seq}` (p95 ≤ 100 ms server-side) and broadcasts it to the room.
- **Given** a `casual` game (the default) **When** a seated player submits an in-game event (life, damage, drain, group drain, poison, counters, commander tax, monarch, initiative, concession) for any seat **Then** it is accepted (D1).
- **Given** a `strict` game **When** a seated player submits an event **Then** it is accepted only if it changes their own seat, or they are its source (`commander_damage`, `life_changed` or `poison_changed` with their `source_seat`, `drain`, `group_drain`) whatever the target. Anything else is rejected with `forbidden`. The host may submit any in-game event for any seat.
- **Given** either trust mode **When** events are submitted **Then** only the host may change settings, move seats (in the lobby, or mid-game as a correction), start, end, abandon or resume the game, switch the control mode and send corrections; the active player or the host may pass the turn; reverts follow S-6.6.2; spectators may submit nothing. Anything else is rejected with `forbidden`.
- **Given** an event the reducer refuses **When** it is submitted **Then** the client gets `reject {event_id, code}`, and nothing is stored or broadcast.
- **Given** an `event_id` that was already accepted **When** it is submitted again **Then** the client gets the original `ack`, and the event is not broadcast a second time.
- **Given** a `finished` or `abandoned` game **When** any event is submitted **Then** it is rejected with `game_closed`.

**Tasks**
- [ ] T-6.3.3.1 (shared) Message types `submit`, `ack`, `reject`, `event` added to the contract
- [ ] T-6.3.3.2 (api) Permission policy module (trust mode × role × event type × seat), with table-driven tests covering both modes
- [ ] T-6.3.3.3 (api) Submit handler wired to `append_event`
- [ ] T-6.3.3.4 (api) Latency test for the server-side p95

#### S-6.3.4 — Fan-out across API processes with Redis pub/sub

**As a** player **I want** to see every change no matter which server process my phone is connected to **so that** the API can run more than one worker process. (Production runs one API instance, I6; this keeps several uvicorn workers in that instance, and process restarts, from losing or splitting events.)

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, infra | 1 | S-6.3.3, F-1.3 |

**Acceptance criteria**
- **Given** client A on API process 1 and client B on process 2 **When** A submits an event **Then** B receives it with the same seq within 300 ms (p95, local Compose).
- **Given** any client **When** it receives events **Then** their seqs strictly increase. **Given** a gap (for example 41 then 43) **When** the client notices **Then** it sends `sync {since: 41}` and gets the missing events from the Postgres log.
- **Given** an event **When** it is published to Redis **Then** that happens only after the database commit, so an unstored event can never be broadcast.
- **Given** Redis restarts during a game **When** it comes back **Then** clients resync from the log and no stored event is missing.
- **Given** 8 players and 4 spectators on 2 processes **When** 10 events/s are submitted for 60 s and one process is restarted **Then** every client ends with the server's state, with no lost or duplicated events.

**Tasks**
- [ ] T-6.3.4.1 (api) Publish to `game:{id}` after commit; per-process subscriber that relays to its local sockets (redis-py's asyncio client as a direct dependency, A15)
- [ ] T-6.3.4.2 (infra) Compose profile with 2 API replicas behind a local proxy for the multi-process tests
- [ ] T-6.3.4.3 (api) Soak test script using the `websockets` client (A15, dev dependency) and its pass/fail report

#### S-6.3.5 — Optimistic client sync engine

**As a** player **I want** my taps to show up on screen instantly and stay correct when others act at the same moment **so that** the app feels fast and stays accurate.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 8 | shared, web, mobile | 1 | S-6.3.3, S-6.1.2 |

**Acceptance criteria**
- **Given** a local action **When** it is created **Then** the displayed state updates within 50 ms, before any network reply, and the event joins a pending queue.
- **Given** pending events **When** a remote event arrives before my ack **Then** the displayed state is rebuilt as confirmed state + the remote event + my pending events, and after my ack it equals the server's state.
- **Given** two simulated clients sending 50 random, interleaved events **When** all acks arrive **Then** both clients' confirmed states are identical to the server's (canonical JSON).
- **Given** a `reject` **When** it arrives **Then** that pending event is dropped, the screen rolls back, and the UI gets the error code to show in a toast.
- **Given** a pending event with no ack after 5 s **When** the timer fires **Then** it is sent again with the same `event_id`.

**Tasks**
- [ ] T-6.3.5.1 (shared) Framework-agnostic sync engine in `packages/game-core` (confirmed state, pending queue, rebase, transport interface)
- [ ] T-6.3.5.2 (shared) Simulation tests with a fake transport and random interleavings
- [ ] T-6.3.5.3 (web) Zustand store and hooks bound to the engine over a browser WebSocket
- [ ] T-6.3.5.4 (mobile) Zustand store and hooks bound to the engine over a React Native WebSocket

#### S-6.3.6 — Reconnect and replay missed events

**As a** player on flaky Wi-Fi **I want** my phone to catch up automatically after it drops **so that** I never have to rejoin or re-enter anything.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | shared, api, web, mobile | 1 | S-6.3.5 |

**Acceptance criteria**
- **Given** a client at seq 120 is offline while events 121–135 happen **When** it reconnects and sends `sync {since: 120}` **Then** it receives 121–135 in order, and its state equals the server's within 2 s of the network coming back.
- **Given** up to 50 actions were made while offline **When** the client reconnects **Then** they are sent after the replay with their original `event_id`s, and any the server already stored are not applied twice.
- **Given** a client more than 500 events behind **When** it syncs **Then** the server sends a state snapshot and `last_seq` instead of individual events.
- **Given** the socket drops **When** the client retries **Then** it backs off (0.5 s, 1 s, 2 s, 4 s … capped at 10 s, with jitter), and shows "Reconnecting…" after 2 s offline and "Offline — changes will sync" while actions are pending.
- **Given** the mobile app goes to the background and comes back **When** it returns to the foreground **Then** it reconnects and resyncs without the user doing anything.

**Tasks**
- [ ] T-6.3.6.1 (api) `sync` handler with the snapshot fallback
- [ ] T-6.3.6.2 (shared) Reconnect, backoff and the offline queue in the sync engine
- [ ] T-6.3.6.3 (web) Connection status banner
- [ ] T-6.3.6.4 (mobile) AppState listener and connection status banner

#### S-6.3.7 — Seat presence

**As a** host **I want** to see whose device is connected **so that** I know which seats I might need to run myself.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, web, mobile | 1 | S-6.3.2, S-6.3.4 |

**Acceptance criteria**
- **Given** a seat with a connected device **When** the table is shown **Then** its connection dot is green. It turns grey after 30 s with no connected device, on every client.
- **Given** presence changes **When** they are broadcast **Then** they travel through Redis only and are **not** written to `game_events`.
- **Given** a seat with no device for more than 30 s **When** the host views it **Then** the host sees a "Control this seat" hint (see S-6.8.2).

**Tasks**
- [ ] T-6.3.7.1 (api) Ephemeral presence tracking in Redis, with `presence` messages
- [ ] T-6.3.7.2 (web, mobile) Connection dots and the host hint

#### S-6.3.8 — Rebuild a game's state from its log

**As a** developer **I want** to rebuild any game's state by replaying its log **so that** the stored snapshot can always be checked and repaired, and undo can recompute state.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 1 | S-6.3.1 |

**Acceptance criteria**
- **Given** a game whose `games.state` snapshot was damaged **When** `rebuild_state(game_id)` runs **Then** the snapshot is replaced by the replay of the log, and the mismatch is logged.
- **Given** a game with 2,000 events **When** it is rebuilt **Then** it takes less than 500 ms.
- **Given** the rebuild command **When** it is run against a game that is in progress **Then** it takes the same row lock as `append_event`, so no event can slip in between.

**Tasks**
- [ ] T-6.3.8.1 (api) `rebuild_state` service and a management CLI command
- [ ] T-6.3.8.2 (api) Performance test with a 2,000-event factory game

---

## F-6.4 — Web game table UI

The browser table: a virtual table for 2–8 seats with drag-and-drop seating, drag-to-damage, group drains, tap and long-press life controls with batched deltas, and counters, covered end to end by Playwright. Every drag has a keyboard and tap alternative (dnd-kit with the keyboard sensor).

Phase 1 · Priority Must

#### S-6.4.1 — Virtual table layout for 2–8 seats

**As a** player **I want** to see every seat around a virtual table **so that** the screen matches where people actually sit.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 1 | S-6.3.5, S-6.2.6 |

**Acceptance criteria**
- **Given** 2 to 8 seats **When** the table renders at 1280×800 **Then** the seats are spaced evenly around the table in seat order (2 seats face each other), with no overlap and no scrolling. Below 768 px wide, the seats show as a vertical list in seat order.
- **Given** a seat panel **When** it renders **Then** it shows the name, life (font ≥ 48 px at 1280×800), commander(s) with tax, poison when > 0, other counters, monarch / initiative icons, and the commander damage received per source.
- **Given** the active seat and eliminated seats **When** the table renders **Then** the active seat is highlighted, eliminated seats are dimmed with an icon for the reason, and my own seat is labelled "You".
- **Given** a screen reader **When** it reaches a seat **Then** it reads a label such as "Seat 2, Alice, 34 life, active turn", and committed changes are announced through a polite live region.

**Tasks**
- [ ] T-6.4.1.1 (web) Responsive table layout component for 2–8 seats
- [ ] T-6.4.1.2 (web) Seat panel component bound to the game store
- [ ] T-6.4.1.3 (web) Accessible names, the live region and component tests for each seat count

#### S-6.4.2 — Drag a seat to change turn order

**As a** game host **I want** to drag players around the table **so that** seating and turn order match the real table.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web, api | 1 | S-6.4.1, S-6.1.6 |

**Acceptance criteria**
- **Given** a lobby with 4 seated players **When** the host drags seat 4 onto seat 2 **Then** the seats swap and turn order updates for every connected client within 1 s.
- **Given** a keyboard-only user **When** they focus a seat and press Space then the arrow keys **Then** the seat moves the same way it would by dragging.
- **Given** a mouse or touch user who doesn't want to drag **When** they open a seat's menu and choose "Swap with…" and a target seat **Then** the result is the same as the drag.
- **Given** an active game **When** the host turns on "Fix seating" and swaps two seats (by drag, keyboard or "Swap with…") and confirms **Then** `seat_moved` is recorded as a correction, the same player stays active, and the recent actions list shows "Host corrected seating" (D9).
- **Given** a non-host **When** they view the table, in the lobby or mid-game **Then** seats are not draggable and the menu option is hidden.

**Tasks**
- [ ] T-6.4.2.1 (web) Seat drag-and-drop with dnd-kit, with the keyboard sensor enabled
- [ ] T-6.4.2.2 (web) "Swap with…" option in the seat menu
- [ ] T-6.4.2.3 (api) Enforce `seat_moved` as host-only in the permission policy, and mark it `correction: true` when the game is active
- [ ] T-6.4.2.4 (web) Host "Fix seating" mode on the active table with a confirm step

#### S-6.4.3 — Life controls with batched deltas

**As a** player **I want** to tap or hold to change life and see the running total before it commits **so that** "+5" becomes one change, not five.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | shared, web | 1 | S-6.4.1 |

**Acceptance criteria**
- **Given** a seat at 40 **When** I click "+" 5 times within 1.5 s **Then** a "+5" pending badge shows next to the life total, and after 1.5 s with no more clicks a single `life_changed {delta: 5}` is committed and every client shows 45.
- **Given** I press and hold "−" for ≥ 500 ms **When** I keep holding **Then** it applies −10, then another −10 every 600 ms, all inside the same pending batch.
- **Given** a pending batch **When** I press Esc or click the badge's "×" **Then** it is discarded and no event is sent. **Given** a batch whose net delta is 0 **Then** no event is sent.
- **Given** a keyboard user with a seat focused **When** they press `+` or `−` (with Shift for ±10) and then Enter **Then** the batch commits right away.
- **Given** a seat I'm not allowed to change (in a `strict` game, any seat but mine unless I'm the host; see S-6.3.3) **When** the table renders **Then** its life buttons are disabled. In a `casual` game every seat's buttons are enabled for every seated player.

**Tasks**
- [ ] T-6.4.3.1 (shared) Platform-agnostic delta batcher in `packages/game-core` (debounce, net zero, cancel) with unit tests
- [ ] T-6.4.3.2 (web) Life control component: click, press-and-hold repeat, pending badge
- [ ] T-6.4.3.3 (web) Keyboard bindings and permission-based disabling

#### S-6.4.4 — Drag to apply commander damage, damage or drain

**As a** player **I want** to drag from my seat onto an opponent to deal commander damage, damage or a drain **so that** the change happens in one gesture and is credited to me.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 1 | S-6.4.3, S-6.1.4 |

**Acceptance criteria**
- **Given** my commander chip on seat 1 **When** I drag it onto seat 3 **Then** a popover opens with "Commander damage" selected and an amount stepper (the last amount used for this source, otherwise 1). Confirming records `commander_damage`, and seat 3's tally and life update on every client within 1 s.
- **Given** my seat's life badge **When** I drag it onto seat 3 **Then** the popover offers "Damage" (`life_changed` with `source_seat: 1`) and "Drain" (seat 3 −N and seat 1 +N in one `drain` event).
- **Given** a keyboard or tap user **When** they open their seat menu, choose "Deal damage…", then a target seat, a type and an amount, and press Enter **Then** exactly the same event is recorded.
- **Given** a drag in progress **When** it moves over seats **Then** valid targets are highlighted, eliminated seats are not valid targets, and Esc cancels the drag.

**Tasks**
- [ ] T-6.4.4.1 (shared) `drain` event handlers in TS and Python plus vectors
- [ ] T-6.4.4.2 (web) Draggable commander chip and life badge, droppable seat panels (dnd-kit, keyboard sensor)
- [ ] T-6.4.4.3 (web) Damage popover: type, amount stepper, last-amount memory
- [ ] T-6.4.4.4 (web) "Deal damage…" menu flow as the non-drag alternative

#### S-6.4.5 — Counters: poison, energy, experience, commander tax, monarch and initiative

**As a** player **I want** to manage all my counters from my seat panel **so that** nothing gets tracked on paper.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 1 | S-6.4.3, S-6.1.5 |

**Acceptance criteria**
- **Given** my seat **When** I open its counters **Then** poison, energy and experience each have ± controls that use the same batching as life. Counters at 0 are hidden unless "Show all counters" is on.
- **Given** each of my commanders **When** I click "Cast" **Then** its cast count goes up by 1 and the label shows the tax ("Tax +2", "Tax +4" …). "−" undoes a mis-tap down to 0.
- **Given** the monarch crown or the initiative token **When** I drag it onto a seat, or choose "Make monarch" / "Take the initiative" from that seat's menu (keyboard reachable) **Then** `monarch_set` / `initiative_set` is recorded and only one crown or token is shown on the table.
- **Given** a seat's poison reaches the game's poison threshold (10 by default) **When** the event commits **Then** that seat shows as eliminated by poison on every client. The poison counter shows the threshold ("7 / 10"), and commander-damage tallies show theirs ("14 / 21").

**Tasks**
- [ ] T-6.4.5.1 (web) Counters panel with batched ± controls
- [ ] T-6.4.5.2 (web) Commander cast / tax control per commander
- [ ] T-6.4.5.3 (web) Draggable monarch and initiative tokens, plus the menu alternative

#### S-6.4.6 — Turn controls and concession

**As a** player **I want** to pass the turn and concede from the table **so that** the turn count and eliminations stay accurate.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web | 1 | S-6.4.1 |

**Acceptance criteria**
- **Given** I am the active player (or the host) **When** I click "Pass turn" **Then** `turn_passed` is recorded, the next surviving seat is highlighted, and the turn and round counters update on every client within 1 s.
- **Given** a player who is not active and not the host **When** they view the table **Then** "Pass turn" is not shown to them.
- **Given** my seat menu **When** I choose "Concede" and confirm **Then** `player_conceded` is recorded. Cancelling the dialog sends nothing.
- **Given** an active game **When** the table renders **Then** it shows the round (labelled as the game's turn count, D4), the individual turn and the elapsed time since `game_started`.

**Tasks**
- [ ] T-6.4.6.1 (web) Turn bar: active player, round, turn, elapsed time, "Pass turn"
- [ ] T-6.4.6.2 (web) Concede action with a confirm dialog

#### S-6.4.7 — Group drain on web

**As a** player **I want** to drain every opponent at once from the table **so that** "each opponent loses N" takes one gesture instead of one per seat.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web | 1 | S-6.4.4, S-6.1.7 |

**Acceptance criteria**
- **Given** my seat's life badge **When** I drag it onto the "Each opponent" zone in the middle of the table **Then** a popover opens with an amount stepper and a "Gain the total" checkbox (off by default), showing a preview ("Bob, Cara, Dan −3 · You +9"). Confirming records one `group_drain`, and every client updates within 1 s.
- **Given** a keyboard or tap user **When** they open their seat menu, choose "Each opponent loses…", set the amount and the checkbox, and press Enter **Then** exactly the same event is recorded.
- **Given** eliminated opponents **When** the preview is shown **Then** they are left out of it and out of the total.
- **Given** a group drain I recorded **When** I press Undo (S-6.6.3) **Then** the whole drain is reverted in one step.

**Tasks**
- [ ] T-6.4.7.1 (web) "Each opponent" drop zone (dnd-kit, keyboard sensor) and the group-drain popover with the preview
- [ ] T-6.4.7.2 (web) "Each opponent loses…" seat-menu flow as the non-drag alternative
- [ ] T-6.4.7.3 (shared) Human-readable description for `group_drain` in `packages/game-core`

#### S-6.4.8 — Web game table end-to-end tests (Playwright)

**As a** developer **I want** Playwright tests that play real games across several browser contexts **so that** the table's multi-device behavior is checked on every CI run, not only in component tests.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web, infra | 1 | S-1.8.5, S-6.2.6, S-6.4.4, S-6.4.6, S-6.6.3, S-6.7.3 |

**Acceptance criteria**
- **Given** the Compose stack and 4 browser contexts (host, a member, a non-member and a guest) **When** the "full game" spec runs **Then** they create, join by code, start, change life with batching, deal commander damage, run a group drain, undo, eliminate players and confirm the results, and every context ends with the same final screen.
- **Given** a `strict` game **When** a player tries to change another seat **Then** the controls are disabled and a forged submit is refused with `forbidden`. **Given** a `casual` game **Then** the same change succeeds.
- **Given** one context goes offline for 5 s while others act **When** it comes back **Then** it shows the same state as the others within 2 s.
- **Given** the CI pipeline **When** the TypeScript job runs **Then** these specs run headless, and a failure uploads the trace and fails the verify gate.

**Tasks**
- [ ] T-6.4.8.1 (web) Playwright fixtures: seeded playgroup, users and a multi-context helper
- [ ] T-6.4.8.2 (web) Specs: full game, trust modes, reconnect
- [ ] T-6.4.8.3 (infra) Run the specs in CI against the Compose stack, with traces kept on failure

---

## F-6.5 — Mobile game table UI

The same game on phones and tablets, with native gestures (Gesture Handler + Reanimated), haptics (expo-haptics), the screen kept awake (expo-keep-awake), a layout that works in both orientations, and a single-device pass-and-play view. Every drag has a tap alternative and screen-reader actions. The main flows are covered end to end by Maestro.

Phase 1 · Priority Must

#### S-6.5.1 — Personal table view on my own phone

**As a** player using my own phone **I want** my seat large and everyone else compact **so that** I can change my life quickly and still see the table.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | mobile | 1 | S-6.3.5, S-6.2.6 |

**Acceptance criteria**
- **Given** a game with 2–8 seats on a 390×844 pt phone **When** the table opens in portrait **Then** my seat takes at least 40% of the screen and every opponent is visible without scrolling, showing name, life and the commander damage they've taken from me.
- **Given** the phone is rotated to landscape **When** the layout changes **Then** all seats stay visible and any pending life batch is kept.
- **Given** I tap an opponent's tile **When** the detail sheet opens **Then** it shows all their counters and their commander damage received per source.
- **Given** a screen reader **When** it focuses a tile **Then** it reads a label such as "Bob, 31 life, 9 commander damage from Atraxa".

**Tasks**
- [ ] T-6.5.1.1 (mobile) Personal layout for portrait and landscape (expo-screen-orientation unlocked during games)
- [ ] T-6.5.1.2 (mobile) Opponent tile and detail sheet bound to the game store
- [ ] T-6.5.1.3 (mobile) Accessibility labels and layout tests for 2, 4 and 8 seats

#### S-6.5.2 — Life controls with gestures, batching and haptics

**As a** player **I want** to tap or hold my life total and feel each change **so that** I can adjust it without looking closely at the screen.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | mobile | 1 | S-6.5.1, S-6.4.3 |

**Acceptance criteria**
- **Given** my seat **When** I tap the upper or lower half of the life area **Then** life goes +1 or −1 into a pending batch with a "+N" / "−N" badge, and it commits as one `life_changed` after 1.5 s with no more taps.
- **Given** I hold the upper or lower half for ≥ 500 ms **When** I keep holding **Then** it applies ±10, then another ±10 every 600 ms.
- **Given** haptics are on **When** I tap, a batch commits, or a seat is eliminated **Then** I feel a light, medium and notification-style haptic respectively. **Given** haptics are off in settings **Then** none fire.
- **Given** VoiceOver or TalkBack **When** the life area is focused **Then** increment and decrement accessibility actions change life by 1 each.

**Tasks**
- [ ] T-6.5.2.1 (mobile) Life control using Gesture Handler tap and long-press, with the shared delta batcher
- [ ] T-6.5.2.2 (mobile) Haptics through expo-haptics, with a settings toggle
- [ ] T-6.5.2.3 (mobile) Accessibility actions

#### S-6.5.3 — Drag to apply commander damage, damage or drain (native gestures)

**As a** player **I want** to drag my commander onto an opponent's tile **so that** commander damage takes one gesture on my phone.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | mobile | 1 | S-6.5.2, S-6.4.4 |

**Acceptance criteria**
- **Given** my commander chip **When** I drag it onto an opponent's tile **Then** the tile highlights with a light haptic as I hover, and on drop a sheet opens with the type (Commander damage / Damage / Drain) and an amount stepper. Confirming records the matching event, and every client updates within 1 s.
- **Given** the drag animation **When** I drag **Then** it runs on the UI thread (Reanimated) at 60 fps on a mid-range test device, and dropping outside a tile cancels.
- **Given** a user who doesn't drag **When** they long-press their seat and choose "Deal damage…", then a target, a type and an amount **Then** exactly the same event is recorded.
- **Given** VoiceOver or TalkBack **When** my seat is focused **Then** a "Deal damage" accessibility action opens the same flow.

**Tasks**
- [ ] T-6.5.3.1 (mobile) Draggable commander chip and life badge with Gesture Handler pan + Reanimated, with drop-target hit testing
- [ ] T-6.5.3.2 (mobile) Damage sheet: type, amount stepper, last-amount memory
- [ ] T-6.5.3.3 (mobile) "Deal damage…" action sheet and accessibility action

#### S-6.5.4 — Reorder seats on mobile (lobby, or a host fix mid-game)

**As a** host on my phone **I want** to rearrange the seats **so that** turn order matches the real table.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | mobile | 1 | S-6.2.5, S-6.1.6, S-6.5.1 |

**Acceptance criteria**
- **Given** a lobby with 4 seats **When** the host long-presses seat 4 and drags it onto seat 2 **Then** the seats swap and every connected client updates within 1 s.
- **Given** a host who doesn't drag **When** they open a seat's menu and choose "Move up", "Move down" or "Swap with…" **Then** the seat swaps with its neighbor or the chosen seat.
- **Given** an active game **When** the host chooses "Fix seating", swaps two seats (by drag or the menu) and confirms **Then** `seat_moved` is recorded as a correction, the same player stays active, and every client shows the new order within 1 s (D9).
- **Given** a non-host **When** they view the seats, in the lobby or mid-game **Then** dragging and the move options are disabled.

**Tasks**
- [ ] T-6.5.4.1 (mobile) Long-press drag to swap seats (Gesture Handler + Reanimated)
- [ ] T-6.5.4.2 (mobile) Seat menu with the move and swap options
- [ ] T-6.5.4.3 (mobile) Host "Fix seating" mode on the active table with a confirm step

#### S-6.5.5 — Counters and turn controls on mobile

**As a** player on my phone **I want** poison, energy, experience, tax, monarch, initiative, passing the turn and conceding at hand **so that** I can play a whole game from the app.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | mobile | 1 | S-6.5.2, S-6.1.5 |

**Acceptance criteria**
- **Given** my seat **When** I open the counters sheet **Then** poison, energy and experience have batched ± controls, and each commander has a "Cast" button with its tax label.
- **Given** the monarch crown or the initiative token **When** I drag it onto a tile, or long-press a tile and choose "Make monarch" / "Take the initiative" **Then** the matching event is recorded and only one crown or token is shown.
- **Given** I am the active player (or the host) **When** I tap "Pass turn" **Then** `turn_passed` is recorded and the turn bar (round, as the game's turn count, plus the individual turn) updates on every client within 1 s.
- **Given** I choose "Concede" and confirm **When** the event commits **Then** my seat shows as conceded everywhere.

**Tasks**
- [ ] T-6.5.5.1 (mobile) Counters sheet with batched controls and the commander cast / tax buttons
- [ ] T-6.5.5.2 (mobile) Draggable monarch and initiative tokens, plus the menu alternative
- [ ] T-6.5.5.3 (mobile) Turn bar and the concede flow

#### S-6.5.6 — Keep the screen awake during a game

**As a** player **I want** my phone to stay on during a game **so that** I don't have to unlock it every time my life changes.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 1 | mobile | 1 | S-6.5.1 |

**Acceptance criteria**
- **Given** I am on the table screen of an `active` or `ending` game **When** I stop touching the phone **Then** the screen does not lock.
- **Given** I leave the table screen, or the game finishes **When** that happens **Then** keep-awake is released.
- **Given** "Keep screen awake" is off in settings **When** I'm on the table **Then** the phone's normal auto-lock applies.

**Tasks**
- [ ] T-6.5.6.1 (mobile) expo-keep-awake tied to the table screen's lifecycle and the setting

#### S-6.5.7 — Pass-and-play single-device table view

**As a** host with one tablet or phone in the middle of the table **I want** every seat shown and facing its player **so that** the whole pod can play from one device.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | mobile | 1 | S-6.5.2, S-6.5.3, S-6.8.2 |

**Acceptance criteria**
- **Given** host-controls-all mode **When** the host opens the pass-and-play view **Then** all 2–8 seats show as equal tiles, each rotated to face its side of the table (the opposite row at 180°, the ends at 90° in layouts with 5 or more seats), and the orientation is locked.
- **Given** 8 seats on a 390×844 pt phone **When** the view renders **Then** each tile shows life in a font of at least 28 pt. On a tablet (≥ 768 pt wide) the tiles grow to fill the screen.
- **Given** any tile **When** someone taps or holds it, or drags a commander chip from one tile to another **Then** the same batching, haptics and damage sheet as S-6.5.2 and S-6.5.3 apply, and the "Deal damage…" tile menu works as the non-drag alternative.
- **Given** host-controls-all is off **When** the host tries to open this view **Then** they are asked to turn it on first.

**Tasks**
- [ ] T-6.5.7.1 (mobile) Rotated tile layouts for 2–8 seats in portrait and landscape
- [ ] T-6.5.7.2 (mobile) Orientation lock in this view (expo-screen-orientation)
- [ ] T-6.5.7.3 (mobile) Reuse the life, counter and damage controls in the rotated tiles

#### S-6.5.8 — Group drain on mobile

**As a** player on my phone **I want** to drain every opponent with one gesture or one menu choice **so that** "each opponent loses N" is as quick as single-target damage.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | mobile | 1 | S-6.5.3, S-6.1.7 |

**Acceptance criteria**
- **Given** my life badge **When** I drag it onto the "Each opponent" zone **Then** the zone highlights with a light haptic, and on drop a sheet opens with an amount stepper, a "Gain the total" switch (off by default) and a preview. Confirming records one `group_drain`, and every client updates within 1 s.
- **Given** a user who doesn't drag **When** they long-press their seat and choose "Each opponent loses…" **Then** the same sheet opens and records exactly the same event.
- **Given** VoiceOver or TalkBack **When** my seat is focused **Then** an "Each opponent loses" accessibility action opens the same flow.
- **Given** the pass-and-play view (S-6.5.7) **When** someone uses the tile menu of any seat **Then** "Each opponent loses…" is available there too, with that seat as the source.

**Tasks**
- [ ] T-6.5.8.1 (mobile) "Each opponent" drop zone with Gesture Handler + Reanimated hit testing
- [ ] T-6.5.8.2 (mobile) Group-drain sheet with the preview, the long-press menu entry and the accessibility action

#### S-6.5.9 — Mobile game table end-to-end flows (Maestro)

**As a** developer **I want** Maestro flows that play a game on a real build **so that** gestures, deep links and reconnects are checked on devices, not only in component tests.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | mobile, infra | 1 | S-1.8.6, S-6.2.3, S-6.5.5, S-6.6.4, S-6.7.3 |

**Acceptance criteria**
- **Given** an Android emulator build pointed at the Compose stack **When** the "join and play" flow runs **Then** it opens `mtgstats://join/<code>` (the link behind the web join page's "Open in app" button, X2), joins as a guest, changes life by tapping and holding, deals commander damage through the "Deal damage…" menu, undoes from the snackbar and concedes, and the API's game state matches what the screen shows.
- **Given** the "host a game" flow **When** it runs **Then** it creates a `strict` game with a non-default poison threshold, seats a second player created through the API, starts, and confirms the results on the review screen.
- **Given** the app is sent to the background for 10 s while the API records events **When** the flow brings it back **Then** the table shows the server's state within 2 s.
- **Given** the CI pipeline **When** the mobile E2E job runs **Then** the flows run on an emulator and a failure keeps the screenshots and fails the job.

**Tasks**
- [ ] T-6.5.9.1 (mobile) testIDs on the table, join, undo and results controls, plus API helpers for seeding games and acting as other seats
- [ ] T-6.5.9.2 (mobile) Maestro flows: join and play, host a game, background and resume
- [ ] T-6.5.9.3 (infra) CI job running the flows on an Android emulator, with screenshots kept on failure

---

## F-6.6 — Undo & corrections

People tap the wrong thing. A recent event can be reverted by adding a compensating event (`event_reverted`): each player can undo their own last 10 events, and the host any of the game's last 10 (D2). The log is never edited. Reducers rebuild state as if the reverted event never happened. The host also gets direct corrections, including fixing the seating mid-game (D9), for when the app and the physical table disagree.

Phase 1 · Priority Must

#### S-6.6.1 — Revert an event with a compensating event

**As a** player **I want** a mistaken action reverted without rewriting history **so that** the state is fixed and the log still shows what happened.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | shared, api | 1 | S-6.1.3, S-6.1.4, S-6.1.5, S-6.3.8 |

**Acceptance criteria**
- **Given** seat 3 at 40, then `life_changed −7` (seq 10) and `life_changed +2` (seq 12) **When** `event_reverted {target_seq: 10}` is applied (seq 14) **Then** seat 3 has 42 life, and seq 10 is still in the log unchanged.
- **Given** an event that eliminated a player (and so moved the monarch) **When** it is reverted **Then** the player is back in the game, the monarch returns to them, and elimination order is recomputed.
- **Given** an `event_reverted` **When** it is itself reverted **Then** the original event's effect comes back (redo).
- **Given** `game_created`, `game_started`, `game_ended` or an already reverted event **When** a revert targets it **Then** it is rejected with `not_revertible` or `already_reverted`.
- **Given** a later event that becomes invalid once the earlier one is reverted (for example poison −2 after the +3 is reverted) **When** state is rebuilt **Then** that later event is skipped, and its seq is listed as `voided` in the state and in the revert's ack.

**Tasks**
- [ ] T-6.6.1.1 (shared) TS reducer: fold the log while excluding reverted events, with redo and voided-event handling
- [ ] T-6.6.1.2 (api) The same semantics in Python, using the `rebuild_state` path for recomputation
- [ ] T-6.6.1.3 (shared) ≥ 11 vectors: simple revert, revert of an elimination, revert of a group drain with simultaneous eliminations, redo, not revertible, voided events

#### S-6.6.2 — Who can undo, and when

**As a** host **I want** clear undo rules **so that** mistakes can be fixed quickly but nobody can quietly rewrite an old part of the game.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-6.6.1, S-6.3.3 |

**Acceptance criteria**
- **Given** a player **When** they revert one of their own 10 most recent events (events where they are the `actor`, not counting `event_reverted` events) **Then** the revert is accepted. **When** they try to revert their 11th most recent event or older **Then** it is rejected with `undo_window_expired` (D2).
- **Given** the host **When** they revert any of the game's 10 most recent events (not counting `event_reverted` events), whoever made it **Then** the revert is accepted. Older events are rejected with `undo_window_expired`, and the host is pointed to corrections (S-6.6.5).
- **Given** an event made by another player, including one that targeted my seat **When** I (not the host) try to revert it **Then** it is rejected with `forbidden`, in both trust modes.
- **Given** an `event_reverted` (a redo) **When** someone reverts it **Then** the same rules apply as for the original event it restores.
- **Given** two clients revert the same event at the same moment **When** both requests arrive **Then** exactly one succeeds and the other gets `already_reverted`.
- **Given** a committed batch (for example "+5") or a group drain **When** it is reverted **Then** the whole batch or drain is undone, because it is a single event.

**Tasks**
- [ ] T-6.6.2.1 (api) Undo rules in the permission policy, with the 10-event windows (per actor and per game) as contract constants
- [ ] T-6.6.2.2 (api) Table-driven tests: own event, host, other player, the target of an event, spectator, the 10th and 11th event, redo, concurrent reverts

#### S-6.6.3 — Undo and recent actions on web

**As a** player on the web **I want** an Undo button and a list of recent actions **so that** I can spot and fix a mistake in seconds.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web | 1 | S-6.6.2, S-6.4.3 |

**Acceptance criteria**
- **Given** I have a pending (uncommitted) batch **When** I click "Undo" or press Ctrl/Cmd+Z **Then** the pending batch is discarded and no event is sent. **Given** no pending batch **Then** my latest non-reverted event inside the window is reverted.
- **Given** the recent actions panel **When** it opens **Then** it lists the last 20 events in plain words (for example "Alice → Bob: 7 commander damage (Atraxa)"), with a "Revert" button only on rows I'm allowed to revert.
- **Given** a reverted event **When** the panel shows it **Then** the row is struck through with "reverted by <name>", and every client reflects the change within 1 s.

**Tasks**
- [ ] T-6.6.3.1 (shared) Human-readable event descriptions in `packages/game-core`
- [ ] T-6.6.3.2 (web) Undo button, Ctrl/Cmd+Z shortcut and recent actions panel

#### S-6.6.4 — Undo and recent actions on mobile

**As a** player on my phone **I want** an undo prompt right after each action **so that** fixing a mis-tap takes one tap.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | mobile | 1 | S-6.6.2, S-6.5.2 |

**Acceptance criteria**
- **Given** I committed an action **When** it is acked **Then** an "Undo" snackbar shows for 5 s, and tapping it reverts that event with a medium haptic.
- **Given** the recent actions sheet **When** I open it **Then** it shows the last 20 events, with "Revert" on the rows I'm allowed to revert and reverted rows struck through.
- **Given** a screen reader **When** the snackbar appears **Then** it is announced and its Undo action is reachable.

**Tasks**
- [ ] T-6.6.4.1 (mobile) Undo snackbar tied to acks
- [ ] T-6.6.4.2 (mobile) Recent actions bottom sheet using the shared descriptions

#### S-6.6.5 — Host corrections

**As a** host **I want** to set a value directly or bring an eliminated player back **so that** the app can be brought back in line with the real table after the undo window has passed.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | shared, api, web, mobile | 1 | S-6.6.1 |

**Acceptance criteria**
- **Given** the host **When** they set seat 2's life to 23 (`value_set {seat: 2, field: "life", value: 23}`) **Then** seat 2 shows 23 on every client, and the log records who made the correction.
- **Given** the host sets a value that crosses a threshold (life ≤ 0, poison ≥ the game's poison threshold, a commander tally ≥ the game's commander-damage threshold) **When** it commits **Then** the elimination rules apply exactly as for a normal event.
- **Given** an eliminated seat **When** the host chooses "Revive" with a reason **Then** `player_revived` is recorded and the seat is back in turn order.
- **Given** a non-host **When** they send `value_set` or `player_revived` **Then** it is rejected with `forbidden`.

**Tasks**
- [ ] T-6.6.5.1 (shared) `value_set` and `player_revived` handlers in TS and Python, plus vectors
- [ ] T-6.6.5.2 (web) Host "Correct…" dialog on each seat
- [ ] T-6.6.5.3 (mobile) Host "Correct…" sheet on each seat

---

## F-6.7 — End of game & results

When one player is left, or the host ends the game, the app builds a summary from the event log: winner (or `no_winner = true` for a full draw), placements (shared placements use competition ranking), eliminations, turn count (rounds, D4) and duration. The host reviews and confirms it, and the results are saved for stats (F-7.1). Playgroup admins can amend saved results at any time, with an audit trail (D7).

Phase 1 · Priority Must

#### S-6.7.1 — Detect the end of the game

**As a** player **I want** the app to notice when the game is over **so that** we go straight to the results.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | shared, api, web, mobile | 1 | S-6.1.3 |

**Acceptance criteria**
- **Given** a 4-seat game **When** the third player is eliminated **Then** the state moves to `ending` with the remaining seat as provisional winner. The host sees "Game over — review results" and everyone else sees "Waiting for host to confirm", within 1 s.
- **Given** a game in `ending` **When** the last elimination is reverted **Then** it goes back to `active`. Events are still accepted while in `ending`.
- **Given** an alternate win, a combo, a draw or running out of time **When** the host chooses "End game" and either picks one winner seat plus a D5 win condition, or picks "No winner (draw)" **Then** `game_end_requested` is recorded (with `winner_seat` and `win_condition`, or with `no_winner: true` and no win condition) and the state moves to `ending`. "Draw" is never offered as a win condition (X1).
- **Given** "No winner (draw)" is requested while seats 1, 2 and 3 are still in the game and seat 4 was eliminated earlier **When** the state moves to `ending` **Then** seats 1–3 are provisional drawing seats and seat 4 stays a loss (X1).
- **Given** a 2-player game **When** one player concedes **Then** the state moves to `ending` with the other player as provisional winner.
- **Given** one event eliminates every remaining seat (S-6.1.3) **When** it is applied **Then** the state moves to `ending` with `no_winner = true` and those seats as provisional drawing seats (D6, X1).

**Tasks**
- [ ] T-6.7.1.1 (shared) `ending` phase and `game_end_requested` (single winner plus win condition, or `no_winner`) in the TS and Python reducers, plus vectors including the everyone-eliminated draw and a host-requested draw with an earlier elimination
- [ ] T-6.7.1.2 (web) Game-over banner and the host's "End game" action
- [ ] T-6.7.1.3 (mobile) Game-over banner and the host's "End game" action

#### S-6.7.2 — Auto-fill the game summary from the event log

**As a** host **I want** the results filled in for me **so that** recording a game takes seconds and doesn't rely on memory.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-6.7.1, S-6.6.1 |

**Acceptance criteria**
- **Given** a game in `ending` **When** the summary is requested **Then** it contains `no_winner` (true only for a full draw), the winner seat (none when `no_winner`), placements (the winner, or every drawing seat, 1st; then reverse elimination order), each seat's result (`win`, `loss` or `draw`), elimination reason, eliminator and round, the round count (the game's turn count, D4) and the individual turn count for reference, the settings the game was played with, and the duration from `game_started` to the start of `ending`.
- **Given** seats eliminated by the same event (the same `eliminated_at_seq`) **When** placements are worked out **Then** they share a placement, using competition ranking (for example 1, 2, 3, 3 in a 4-player game) (D6).
- **Given** a full draw **When** placements are worked out **Then** `no_winner = true`, every seat still in the game when it ended (or eliminated by the final event) gets placement 1 with result `draw`, seats eliminated earlier get result `loss` and placements after them by reverse elimination order (for example 1, 1, 1, 4), and the win condition is empty (D6, X1).
- **Given** the last elimination **When** the proposed win condition is worked out **Then** it is one of the D5 codes: `commander_damage` → "Commander damage"; `poison` → "Poison"; `life` from a `drain` or `group_drain` → "Life drain / burn"; any other `life` → "Combat damage"; every opponent conceded → "Concession"; a host-declared elimination with a `cause` → that cause (for example "Mill / decking"); a host-declared elimination without a cause → "Last standing"; a host-requested end → the condition the host gave. The list is exactly D5: combat damage, commander damage, combo, alternate-win card, poison, mill/decking, life drain/burn, concession, last standing, and other (with a required note). There is no "Draw" category; a draw is recorded only as `no_winner` (X1).
- **Given** reverted and voided events **When** the summary is built **Then** they are ignored.
- **Given** 3 recorded fixture games **When** their summaries are built **Then** they match the expected JSON exactly, and a 2,000-event game is summarized in under 200 ms.

**Tasks**
- [ ] T-6.7.2.1 (api) `build_summary` service in `games/services`, with the win-condition enum shared through the contract (S-6.1.1)
- [ ] T-6.7.2.2 (api) `GET /games/{id}/summary` endpoint (thin router)
- [ ] T-6.7.2.3 (api) Fixture games built with factories, plus the performance test

#### S-6.7.3 — Host reviews and confirms results

**As a** host **I want** to check and fix the summary before saving it **so that** the stats are right even when the app couldn't know (for example, an alternate win).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 1 | S-6.7.2 |

**Acceptance criteria**
- **Given** the pre-filled summary **When** the host edits the winner (or marks the game "No winner (draw)", which clears the win condition), win condition (from the D5 list only, or "Other" with a required note), turn count (rounds) or placements **Then** the changed fields are marked "edited", and other players see a read-only copy that updates live.
- **Given** the placements list **When** the host drags a player to a new place, or uses "Move up" / "Move down" (also with the keyboard), or "Tie with the player above" **Then** the placements are renumbered in the same way, using competition ranking for ties.
- **Given** a valid summary (every seat has a placement from 1 to n with competition ranking; with a winner, exactly one seat is 1st with result `win`, every other seat is `loss`, and there is a win condition; with `no_winner = true`, two or more seats share 1st with result `draw`, every other seat is `loss`, and there is no win condition) **When** the host confirms **Then** `game_ended` is recorded with the final summary, the status becomes `finished`, and every device shows the final screen within 1 s.
- **Given** an invalid summary (for example, two players placed 1st in a game with a winner, a no-winner game with a win condition, or "Other" with no note) **When** the host tries to confirm **Then** "Confirm" is disabled and the problem is explained.

**Tasks**
- [ ] T-6.7.3.1 (api) `game_ended` validation and the status change
- [ ] T-6.7.3.2 (web) Review screen with dnd-kit placement reordering (keyboard sensor) and move buttons
- [ ] T-6.7.3.3 (mobile) Review screen with drag reordering and move buttons
- [ ] T-6.7.3.4 (web, mobile) Read-only "waiting for host" view and final screen

#### S-6.7.4 — Save results for stats

**As a** stats consumer (F-7.1) **I want** each confirmed game stored as structured results **so that** win rates by player, deck and commander are simple queries.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-6.7.3, S-6.2.6, S-6.2.9, S-6.2.10 |

**Acceptance criteria**
- **Given** the host confirms results **When** `game_ended` commits **Then**, in the same transaction, `Game` stores `ended_at`, `round_count` (the game's turn count, D4; individual turns stay in the event log), `duration_seconds`, `no_winner` (boolean, true only for a full draw, X1), `win_condition` and `win_condition_note` (both empty when `no_winner`), alongside the settings frozen at start (S-6.2.6). Each `GameSeat` stores `placement` (shared placements use competition ranking), `result` (`win` | `loss` | `draw`), `seat_position` (the final seat order, including any mid-game correction, D9), `elimination_reason`, `eliminated_by_seat`, `eliminated_round`, `deck_version_id` and its commanders, next to the bracket frozen at start (S-6.2.10).
- **Given** guest seats and seats of logged-in non-members **When** results are saved **Then** they get result rows like any other seat and are returned by `list_results`, so they count in stats and standings (C4, C6).
- **Given** the games domain's public interface (`games/services/__init__.py`) **When** another domain needs results **Then** it reads them through `get_game_result(game_id)` and `list_results(playgroup_id, filters)` DTOs, never from the games models directly.
- **Given** abandoned games or games that never finished **When** `list_results` runs **Then** they are not returned. A game that was abandoned, resumed (S-6.2.9) and then finished is returned.
- **Given** the confirm is resent with the same `event_id` **When** it arrives **Then** no duplicate result rows are written.

**Tasks**
- [ ] T-6.7.4.1 (api) Alembic migration for the result columns on `games` and `game_seats`
- [ ] T-6.7.4.2 (api) Results write inside the `game_ended` handler
- [ ] T-6.7.4.3 (api) Public read services and DTO schemas, with tests

#### S-6.7.5 — Rematch with the same table

**As a** host **I want** to start a new game with the same players and decks **so that** back-to-back games take one tap.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 3 | api, web, mobile | 1 | S-6.7.3, S-6.2.1 |

**Acceptance criteria**
- **Given** a finished game **When** the host taps "Rematch" **Then** a new lobby is created with the same settings, seats, seat order and decks (or commanders), and every device that was connected moves to it within 1 s.
- **Given** a player who left the first game's screen **When** the rematch is created **Then** it shows in their "My games" list.
- **Given** the rematch lobby **When** anyone changes seats or decks **Then** the finished game is not affected.

**Tasks**
- [ ] T-6.7.5.1 (api) `create_rematch` service that copies the seats, and a `rematch` notice to the old room
- [ ] T-6.7.5.2 (web, mobile) "Rematch" button and the redirect

#### S-6.7.6 — Playgroup admins amend saved results

**As a** playgroup admin **I want** to correct a finished game's results at any time, with a record of what changed **so that** stats and standings stay right when a mistake is found later.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 1 | S-6.7.4, S-2.4.3 |

**Acceptance criteria**
- **Given** a `finished` game in playgroup P and a user who is P's owner or an admin **When** they change the winner or `no_winner`, win condition (and note), round count or placements and save **Then** the result rows are updated and an audit record is written in the same transaction, holding who, when, and the before and after values of every changed field (D7).
- **Given** an amendment **When** it is saved **Then** it goes through the same validation as S-6.7.3, and an invalid result returns 422 naming the problem.
- **Given** a host or player who is not an owner or admin of P **When** they try to amend **Then** the API returns 403. An `abandoned` game can't be amended (409).
- **Given** an amendment commits **When** the results-saved path runs **Then** it is the same path `game_ended` uses, so the stats facts for that game are refreshed (S-7.1.1), and league standings will be refreshed the same way once E-10 exists.
- **Given** an amended game **When** a playgroup member opens it **Then** it shows "Amended by <name> on <date>" and a history listing each amendment's before and after. The game's event log is not changed.

**Tasks**
- [ ] T-6.7.6.1 (api) `game_result_amendments` audit table (actor, timestamp, before and after as JSONB) and its Alembic migration
- [ ] T-6.7.6.2 (api) `amend_game_result` service and `PATCH /games/{id}/result` (thin router), with the admin check through the playgroups public service and the shared results-saved call
- [ ] T-6.7.6.3 (web) "Amend results" form reusing the review screen, plus the amendment history
- [ ] T-6.7.6.4 (mobile) The same form and history

#### S-6.7.7 — Invite a non-member after the game

**As a** host **I want** a one-tap invite (or invite suggestion) for anyone who played with their own account but isn't in the playgroup **so that** a regular visitor can join the group easily.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | api, web, mobile | 1 | S-6.7.3, S-6.2.2, S-2.4.6 |

**Acceptance criteria**
- **Given** a finished game in P with a seat marked `non_member` (S-6.2.2) **When** the host, who is P's owner or an admin, opens the final screen **Then** that seat shows "Invite to <P>". One tap creates a targeted playgroup invite for that account through the S-2.4.6 service, and the button changes to "Invited" (C6, X12).
- **Given** a host who is a plain member of P (C3: only owners and admins can invite) **When** they tap the same button, labelled "Suggest inviting" **Then** an invite suggestion is created through S-2.4.6 for P's owner and admins to approve or dismiss, and the button changes to "Suggested" (X12).
- **Given** the user has since become a member, or already has a pending invite or suggestion **When** the final screen opens **Then** no invite button is shown for them, and a repeated tap never creates a second invite or suggestion.
- **Given** a guest seat **When** the final screen opens **Then** no invite is offered (guests claim an account through F-2.3).

**Tasks**
- [ ] T-6.7.7.1 (api) `list_non_member_seats(game_id)` in `games/services/__init__.py` for S-2.4.6, and an `invite_from_game` service that calls the S-2.4.6 invite or suggestion service by the host's role, idempotent per game and user
- [ ] T-6.7.7.2 (web, mobile) Final-screen slot with the "Invite to <P>" / "Suggest inviting" button and its "Invited" / "Suggested" states

---

## F-6.8 — Spectator & host-controls-all

Up to 20 spectators can watch a live game read-only, seeing the board only (D10). The host can switch on a mode that puts every seat's controls on one device, which is needed for pass-and-play and for guests without a phone. (The host may already edit any seat in both trust modes, D1; the mode changes the host's screen and how actions are labelled, not what the server allows.) The host cannot lock seated players' devices to read-only in this mode; that is Won't (D10).

Phase 1 · Priority Should (host-controls-all is Must because pass-and-play depends on it)

#### S-6.8.1 — Join as a spectator

**As a** playgroup member who isn't playing **I want** to watch the game live **so that** I can follow along without being able to change anything.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, web, mobile | 1 | S-6.2.2, S-6.3.2 |

**Acceptance criteria**
- **Given** a valid game code **When** I choose "Watch" **Then** I join with the role `spectator`, get the current state, and receive live events like any player.
- **Given** a spectator **When** they submit any event **Then** it is rejected with `forbidden`.
- **Given** spectators are connected **When** players look at the table **Then** they see an eye icon with the spectator count.
- **Given** the game already has 20 spectators (the fixed maximum, D10) **When** another person tries to watch **Then** they get `room_full`. Players don't count toward the limit.

**Tasks**
- [ ] T-6.8.1.1 (api) Spectator role on join and on the socket, with the spectator limit
- [ ] T-6.8.1.2 (web, mobile) "Watch" option in the join flow and the spectator count on the table

#### S-6.8.2 — Host controls all seats

**As a** host **I want** to control every seat from my device **so that** guests without phones, or one shared device, can still play.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web, mobile | 1 | S-6.3.3 |

**Acceptance criteria**
- **Given** a lobby or an active game **When** the host turns on "Host controls all seats" **Then** `control_mode_changed` is recorded and every seat's controls are shown and enabled on the host's devices.
- **Given** that mode **When** the host acts for seat 4 **Then** the event records `actor = host` and `on_behalf_of_seat = 4`, and the recent actions list reads "Host for Dana: −3 life". The host can undo it under the host's undo rule (S-6.6.2).
- **Given** that mode **When** seated players act from their own devices **Then** they keep the permissions of the game's trust mode; the host has no way to lock their devices (D10).
- **Given** the host turns the mode off **When** they change another seat from that seat's panel or menu **Then** it is still accepted (D1: the host may edit everything in both modes), and `on_behalf_of_seat` is still recorded. Only the host's layout returns to normal.

**Tasks**
- [ ] T-6.8.2.1 (shared) `control_mode_changed` handlers in TS and Python, plus vectors
- [ ] T-6.8.2.2 (api) Record `on_behalf_of_seat` whenever the host acts on a seat that isn't their own (no extra permission branch is needed after D1)
- [ ] T-6.8.2.3 (web, mobile) Mode toggle, with every seat's controls enabled for the host

#### S-6.8.3 — Read-only spectator table

**As a** spectator **I want** a clean, read-only view of the table **so that** I can follow the game without hitting controls by accident.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api, web, mobile | 1 | S-6.8.1, S-6.4.1, S-6.5.1, S-6.2.4, S-6.4.8 |

**Acceptance criteria**
- **Given** the spectator role **When** the table renders **Then** it uses the full virtual table layout (2–8 seats), with every control, drag handle and menu hidden, and a "Spectating" banner.
- **Given** a spectator **When** the table renders **Then** they see the board only: seats, life, counters, commanders, commander damage, turn and round, and the recent actions list. Deck validation warnings are not shown (D10).
- **Given** a spectator opens a seat's deck **When** the deck's owner shares a playgroup with the spectator (C5) **Then** the decklist is shown. Otherwise only the deck name and commanders are shown, and the API never sends the list.
- **Given** a spectator **When** events arrive **Then** the seats and the recent actions list update within 1 s, and screen readers announce the changes.
- **Given** the game reaches `finished` **When** the spectator is still connected **Then** they see the final results screen.

**Tasks**
- [ ] T-6.8.3.1 (web) Read-only mode for the table components
- [ ] T-6.8.3.2 (mobile) Read-only mode for the table components
- [ ] T-6.8.3.3 (api) Spectator view of the state and deck details: strip validation warnings, and include a decklist only when the decks service says the viewer may see it
- [ ] T-6.8.3.4 (web) Playwright spec: a spectator context sees every change, no controls and no warnings

#### S-6.8.4 — Claim a host-created seat from a new device

**As a** guest whose seat the host set up **I want** to take over my seat from my own phone **so that** I can play on my own device without starting over.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 3 | api, web, mobile | 1 | S-6.8.2, S-6.2.2 |

**Acceptance criteria**
- **Given** a guest seat created by the host with no device attached **When** someone joins with the code and picks "I'm <name>" **Then** the host gets a prompt, and after the host approves, that device is bound to the seat (`seat_claimed`) with a game-scoped token.
- **Given** the host rejects the claim **When** the claimant is told **Then** they are offered spectating instead.
- **Given** a seat that already has a connected device **When** someone tries to claim it **Then** the seat is not offered.

**Tasks**
- [ ] T-6.8.4.1 (shared) `seat_claimed` handlers in TS and Python, plus vectors
- [ ] T-6.8.4.2 (api) Claim request and host-approval flow over the socket
- [ ] T-6.8.4.3 (web, mobile) Claim option in the join flow and the host's approval prompt

---

## Open questions

None open. Resolved: see OPEN-QUESTIONS.md decisions log.
