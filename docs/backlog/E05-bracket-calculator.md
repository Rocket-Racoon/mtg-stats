# E-5 — Bracket Calculator

| | |
|---|---|
| **Phase(s)** | 1 |
| **Goal** | Work out the official WotC Commander Bracket (1–5) for any deck version: the minimum bracket it qualifies for, an itemized list of reasons, and a check of the owner's declared intent. The bracket rules are stored as versioned data with an effective date, so a WotC update is a data change, not a code change. |
| **Success measures** | • Publishing a new WotC rule-set version needs 0 code changes: the site admin edits and publishes it in the admin screen (or loads one data file with one command), every current deck version is re-assessed automatically, and owners whose deck's bracket changed get an in-app notice.<br>• Every assessment records the rule-set version it used and lists each reason with the cards behind it.<br>• When Commander Spellbook is unavailable, 100% of assessments still finish, marked "combo check incomplete", and are completed automatically once the API is back.<br>• For a reference set of decks with hand-checked brackets (built at seed time), the engine's minimum bracket matches in every case. |
| **Out of scope** | Judging a deck's power beyond the official bracket rules (no custom "power level" score). Using brackets for pod matchmaking (F-10.4). Deck legality (F-4.5). Any card list or threshold written into code. Showing Commander Spellbook's own bracket estimate next to ours (decision E11 adopts only Scryfall's `game_changer` cross-check). |

## F-5.1 — Versioned bracket rule set

The official bracket rules (Game Changers list, mass land denial, extra-turn cards, tutor rules, two-card combo rules and per-bracket limits) are stored as immutable, versioned data with a source and an effective date. The global **site admin** role (F-2.1) checks rule sets against the official WotC rules, maintains the category lists (tutors, mass land denial, extra turns) and publishes new versions in an in-app admin screen (decisions E6, E9). The command-line loader stays for seeding and development. A cross-check warns the site admin when Scryfall's `game_changer` flag disagrees with the active Game Changers list (decision E11). Nothing in this feature hard-codes a card or a threshold; the values come from the seeded rule-set version.

**Phase:** 1 · **Priority:** Must

#### S-5.1.1 — Rule-set data model

**As a** developer **I want** the bracket rules modelled as versioned data **so that** the engine never needs code changes when WotC updates the rules.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | F-1.4, F-3.1 |

**Acceptance criteria**
- **Given** the Alembic migration **When** it is applied and downgraded on an empty database **Then** both succeed and the drift check is clean.
- **Given** a rule set **When** it is stored **Then** it has a version label, an effective date, a source URL, a retrieval date, a status (`draft` or `published`) and notes.
- **Given** a rule set **When** it is stored **Then** it holds (a) card lists per category (`game_changer`, `mass_land_denial`, `extra_turn`, `tutor`) keyed by `oracle_id`, and (b) for each bracket 1–5, a constraint per category with fields `allowed`, `max_count` (null = no limit) and `enforcement` (`hard` or `advisory`), plus a combo policy (number of cards and the result tags that make a combo count, any of which is enough), an `intent_dependent` flag and description text, and the bracket to assume when a deck in the intent-dependent range has no declared intent.
- **Given** a published rule set **When** any service tries to change it **Then** the change is refused; a correction means a new version.

**Tasks**
- [ ] T-5.1.1.1 (api) SQLAlchemy models `BracketRuleSet`, `BracketCategoryCard`, `BracketConstraint`, `BracketComboPolicy` in a `brackets` domain
- [ ] T-5.1.1.2 (api) Alembic migration with upgrade and downgrade
- [ ] T-5.1.1.3 (api) Immutability guard in the service layer and a test for it
- [ ] T-5.1.1.4 (api) factory_boy factories that build small synthetic rule sets for engine tests (no real card lists)

#### S-5.1.2 — Rule-set file format and loader

**As an** operator **I want** to describe a rule set in a reviewable data file and load it with one command **so that** updating the rules is a reviewed data change.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-5.1.1 |

**Acceptance criteria**
- **Given** a rule-set JSON file in `apps/api/app/brackets/rulesets/` **When** the operator runs `brackets load <file>` **Then** it is checked against a Pydantic schema and stored as a `draft`.
- **Given** a file that lists cards by name **When** it is loaded **Then** every name is resolved to an `oracle_id`; if any name does not resolve, the load fails, lists every unresolved name, and stores nothing.
- **Given** a file with a bracket constraint that references an unknown category, or brackets other than 1–5 **When** it is loaded **Then** the load fails with a clear schema error.
- **Given** a successful load **When** the command finishes **Then** it prints a summary: card count per category and the constraints per bracket.

**Tasks**
- [ ] T-5.1.2.1 (api) Pydantic schema for the rule-set file and a JSON schema export for reviewers
- [ ] T-5.1.2.2 (api) `brackets load` CLI command calling the brackets service; name resolution through the cards service interface
- [ ] T-5.1.2.3 (api) Tests for valid file, unresolved names and schema errors, using a synthetic rule-set file

#### S-5.1.3 — Seed the current official rule set

**As a** playgroup **we want** the app to ship with the current official bracket rules **so that** assessments are correct from day one.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-5.1.2, F-3.1 |

**Acceptance criteria**
- **Given** the seed file **When** it is reviewed **Then** it states the official WotC source URL, the date it was checked, and the effective date WotC gives; every card list and limit in it is copied from that source, and the site admin signs it off before it is published (decision E6).
- **Given** the seed file **When** it is reviewed **Then** its combo policy counts two-card combos tagged infinite or win-the-game, with a commander plus one other card counting as two cards (decision E7), and its intent-dependent range (Brackets 1–2) assumes Bracket 2 when no intent is declared (decision E8).
- **Given** a fresh dev environment after the first Scryfall sync **When** the operator runs the documented seed step **Then** the rule set loads and is published with no unresolved card names.
- **Given** the seeded rule set **When** the reference decks (built at seed time, with brackets checked by hand against the official article) are assessed **Then** every one gets its expected minimum bracket.

**Tasks**
- [ ] T-5.1.3.1 (api) Write the seed rule-set file from the official WotC announcement current at seed time
- [ ] T-5.1.3.2 (api) Reference decks as test data (text decklists) with their expected brackets
- [ ] T-5.1.3.3 (infra) Seed step in the dev setup script, run after the first Scryfall sync

#### S-5.1.4 — Publish a new version and pick the active one by date

**As an** operator **I want** to publish a new rule-set version with an effective date **so that** the app switches to it on the right day and old assessments keep their version.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-5.1.2 |

**Acceptance criteria**
- **Given** a draft rule set **When** the operator runs `brackets diff <draft>` **Then** it prints the cards added and removed per category and every changed constraint compared with the active version.
- **Given** a draft **When** the operator runs `brackets publish <draft>` **Then** its status becomes `published` and it can no longer change.
- **Given** published versions with effective dates 2026-01-01 and 2026-12-01 **When** the active version is requested on 2026-11-07 **Then** the 2026-01-01 version is returned; from 2026-12-01 the newer one is.
- **Given** a newly active version **When** it takes effect **Then** a re-assessment job is queued (S-5.3.4).

**Tasks**
- [ ] T-5.1.4.1 (api) `brackets diff` and `brackets publish` CLI commands
- [ ] T-5.1.4.2 (api) `get_active_ruleset(on_date)` in the brackets service, using timezone-aware dates
- [ ] T-5.1.4.3 (api) Daily arq check that queues re-assessment when a future-dated version becomes active
- [ ] T-5.1.4.4 (api) Tests for date selection and publish immutability

#### S-5.1.5 — Rule-set reference screen

**As a** player **I want** to see which bracket rules the app uses and their source **so that** I can trust and check the result.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 3 | api, web, mobile | 1 | S-5.1.4, F-3.3 |

**Acceptance criteria**
- **Given** the active rule set **When** a user opens "Bracket rules" **Then** they see the version, effective date, a link to the WotC source, each bracket's description and constraints, and the card lists per category.
- **Given** a card in a list **When** the user taps or clicks it **Then** the card preview opens.

**Tasks**
- [ ] T-5.1.5.1 (api) `GET /brackets/rulesets/active` and `GET /brackets/rulesets/{version}`
- [ ] T-5.1.5.2 (web) Rule-set reference page
- [ ] T-5.1.5.3 (mobile) Rule-set reference screen

#### S-5.1.6 — Rule-set admin API (site admin)

**As a** site admin **I want** to draft, edit, compare and publish rule-set versions through the API **so that** keeping the bracket rules current doesn't need server access.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-5.1.4, S-2.1.11, F-3.2 |

**Acceptance criteria**
- **Given** a site admin **When** they call `POST /admin/brackets/rulesets` **Then** a new `draft` is created as a copy of the active version; any user who is not a site admin gets 403 on every `/admin/brackets/*` endpoint (decision E9).
- **Given** a draft **When** the site admin adds or removes a card in a category list (`game_changer`, `mass_land_denial`, `extra_turn`, `tutor`) by `oracle_id`, or edits a bracket constraint, the combo policy, the intent settings or the source fields **Then** the draft is updated; the same edit on a published version returns 409 (decision E6).
- **Given** a draft **When** the site admin calls `GET /admin/brackets/rulesets/{id}/diff` **Then** the response lists the same changes as `brackets diff` (cards added and removed per category, changed constraints).
- **Given** a draft **When** the site admin publishes it with an effective date **Then** it becomes `published` and immutable, records who published it and when, and is picked up by the date rule and re-assessment from S-5.1.4.

**Tasks**
- [ ] T-5.1.6.1 (api) Admin router and schemas calling the existing brackets service functions (create draft, edit, diff, publish), guarded by the site-admin check from S-2.1.11
- [ ] T-5.1.6.2 (api) `published_by` and `published_at` columns on the rule set, with migration
- [ ] T-5.1.6.3 (api) Tests for permissions, draft edits, 409 on published versions, diff and publish

#### S-5.1.7 — Rule-set admin screen (web)

**As a** site admin **I want** an admin screen for bracket rule sets **so that** I can maintain the category lists and publish a new version in a few clicks.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 1 | S-5.1.6, S-2.1.11, F-1.6, F-3.3 |

**Acceptance criteria**
- **Given** a site admin **When** they open "Admin → Bracket rules" **Then** they see every version with its status, effective date and an "active" marker; users who are not site admins see no admin entry and the route shows "not allowed".
- **Given** a draft **When** the site admin edits it **Then** they can add cards to a category list through card search, remove cards, and edit the per-bracket constraints, the combo policy and the intent settings, with changes saved to the draft.
- **Given** a draft **When** the site admin clicks "Publish" **Then** a review step shows the diff against the active version and asks for the effective date and a confirmation before publishing.
- **Given** a keyboard-only user **When** they use the screen **Then** every action is reachable and operable from the keyboard.

**Tasks**
- [ ] T-5.1.7.1 (web) Admin route, guarded by the site-admin role, with the version list
- [ ] T-5.1.7.2 (web) Draft editor: category lists with F-3.3 card search, constraints table, combo policy and intent settings
- [ ] T-5.1.7.3 (web) Diff review and publish confirmation
- [ ] T-5.1.7.4 (web) Vitest tests for the guard, editing and publish flow

#### S-5.1.8 — Game Changers cross-check with Scryfall

**As a** site admin **I want** to be warned when Scryfall's `game_changer` flag disagrees with our Game Changers list **so that** I notice a WotC update I may have missed.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web | 1 | S-5.1.7, S-3.1.5 |

**Acceptance criteria**
- **Given** the active rule set and the `game_changer` flag stored from Scryfall **When** the check runs (after each Scryfall sync, and when a rule set is published or becomes active) **Then** it records the cards Scryfall flags that are missing from the active Game Changers list, and the cards on the list that Scryfall doesn't flag (decision E11).
- **Given** differences **When** the site admin opens the admin screen **Then** a warning shows both lists, the date of the Scryfall data and the rule-set version compared; with no differences there is no warning.
- **Given** differences **When** assessments run **Then** they still use only the rule set; the Scryfall flag never changes a bracket.
- **Given** a user who is not a site admin **When** they call `GET /admin/brackets/game-changer-check` **Then** they get 403.

**Tasks**
- [ ] T-5.1.8.1 (api) `compare_game_changers(ruleset)` in the brackets service, reading flags through `cards/services.game_changer_oracle_ids()` (S-3.1.5), with the result stored
- [ ] T-5.1.8.2 (api) Run the check after the Scryfall sync job and on publish/activation; admin endpoint
- [ ] T-5.1.8.3 (web) Warning panel in the admin screen
- [ ] T-5.1.8.4 (api, web) Tests for no difference, flagged-but-missing and listed-but-unflagged cards

## F-5.2 — Combo detection (Commander Spellbook)

Find the combos in a deck through the Commander Spellbook API, queried once per deck version (no bulk sync) with a 7-day cache, and classify them under the active rule set's combo policy: only combos tagged infinite or win-the-game count, and a commander plus one other card that together make such a combo counts as a two-card combo (decision E7, confirmed). If Spellbook is unavailable, the assessment is marked "combo check incomplete" instead of failing.

**Phase:** 1 · **Priority:** Must

#### S-5.2.1 — Commander Spellbook client

**As the** system **I want** a polite, cached client for Commander Spellbook **so that** we find combos without hammering their service.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | F-1.4, F-1.3 |

**Acceptance criteria**
- **Given** a deck version's commander, main-board and companion card names **When** the client asks Spellbook for the combos in that list (one query per deck version, decision E7) **Then** it returns the combos fully included in the deck, each with its Spellbook ID, its cards and its result tags (e.g. infinite or win-the-game), as reported by Spellbook.
- **Given** a request **When** it is sent **Then** it carries the User-Agent `MTGStats/<version> (+<repo URL>)` with no personal email (decision E4), and a timeout from settings (default 10 s).
- **Given** the same card set (same sorted `oracle_id`s) requested again within the cache TTL (setting, default 7 days, decision E7) **When** the client is called **Then** the cached result is returned with no outbound request.
- **Given** a timeout, a 429 after at most 2 retries, a 5xx or a response that doesn't match the expected schema **When** the call ends **Then** the client returns `unavailable` with a reason and never raises to the caller.

**Tasks**
- [ ] T-5.2.1.1 (api) httpx client for the Spellbook combo-finding endpoint (endpoint and payload confirmed against the current Spellbook API docs at implementation)
- [ ] T-5.2.1.2 (api) Cache keyed by a hash of sorted `oracle_id`s, with TTL from settings
- [ ] T-5.2.1.3 (api) Pydantic models for the response, with a schema-drift guard
- [ ] T-5.2.1.4 (api) Tests with `httpx.MockTransport` for success, cache hit, 429, timeout and schema drift
- [ ] T-5.2.1.5 (api) Before building: check Commander Spellbook's current terms, rate limits and attribution requirements, record them on the issue, and add any required credit to the bracket UI

#### S-5.2.2 — Store combos locally and classify them under the rule set

**As the** bracket engine **I want** combos stored locally and tagged by the rule set's combo policy **so that** "two-card combo" means exactly what the active rule set says.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-5.2.1, S-5.1.1 |

**Acceptance criteria**
- **Given** combos returned by Spellbook **When** they are stored **Then** each combo is saved once by Spellbook ID, with its cards as `oracle_id`s and its result tags, plus the date it was fetched.
- **Given** a combo whose card names can't be matched to our cards **When** it is stored **Then** it is kept with the unmatched names listed and is still shown to the user, but marked "unverified".
- **Given** the active rule set's combo policy (number of cards and required result tags) **When** a deck's combos are classified **Then** only combos with the policy's number of cards and at least one of its required tags count as bracket-relevant combos; the others are listed as "other combos found". With the seeded policy, a two-card combo tagged neither infinite nor win-the-game does not count (decision E7).
- **Given** a combo tagged infinite or win-the-game that needs a commander plus one other card **When** it is classified **Then** it counts as a two-card combo: the commander counts as one of its two cards (decision E7, confirmed).
- **Given** a combo of a commander plus two other cards **When** it is classified under the seeded policy **Then** it counts as three cards and is listed under "other combos found".

**Tasks**
- [ ] T-5.2.2.1 (api) `Combo` and `ComboCard` models and migration
- [ ] T-5.2.2.2 (api) Upsert from the client result, matching names to `oracle_id`s through the cards service
- [ ] T-5.2.2.3 (api) `classify_combos(combos, combo_policy)` as a pure function, with tests using synthetic policies, including commander + 1 card (counts) and commander + 2 cards (does not)

#### S-5.2.3 — "Combo check incomplete" and automatic retry

**As a** deck owner **I want** to get a bracket even when Spellbook is down, and have it completed later **so that** an outside outage never blocks me.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-5.2.1, S-5.3.4, F-1.9 |

**Acceptance criteria**
- **Given** the Spellbook client returns `unavailable` **When** an assessment runs **Then** it completes with all non-combo reasons, `combo_check: "incomplete"` and the reason, and its minimum bracket is labelled "at least" in the API response.
- **Given** an incomplete assessment **When** the retry job runs (with backoff, at most once every 15 min per deck version, and up to a configured number of attempts) and Spellbook answers **Then** the assessment is re-run and stored as complete.
- **Given** Spellbook fails 5 times in a row within 1 minute **When** further assessments run in the next few minutes **Then** they skip the call straight away (circuit open) and are marked incomplete.

**Tasks**
- [ ] T-5.2.3.1 (api) Incomplete flag and reason on the assessment result
- [ ] T-5.2.3.2 (api) arq retry job with backoff and attempt limit
- [ ] T-5.2.3.3 (api) Simple circuit breaker state in Redis, with tests for open, half-open and closed

## F-5.3 — Bracket assessment engine

A deterministic engine that takes a deck version and a rule-set version and returns the minimum bracket, the itemized reasons behind it and a check of the owner's declared intent. Every assessment is stored with the rule-set version used.

**Phase:** 1 · **Priority:** Must

#### S-5.3.1 — Match a deck's cards to rule-set categories

**As the** bracket engine **I want** the cards in each rule category found for a deck **so that** every later decision is based on a clear list of cards.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-5.1.1, S-4.4.1 |

**Acceptance criteria**
- **Given** a deck version and a rule set **When** categories are matched **Then** the result lists, per category, the deck's cards that appear in that category's list, matched by `oracle_id`.
- **Given** cards in the commander, main and companion boards **When** categories are matched **Then** they are all included (the companion counts toward the bracket, decision E5); side and maybe boards are excluded.
- **Given** a card that is in two categories **When** categories are matched **Then** it appears in both.

**Tasks**
- [ ] T-5.3.1.1 (api) `match_categories(version, ruleset)` in `brackets/services`, reading deck cards through the decks service interface
- [ ] T-5.3.1.2 (api) Tests with a synthetic rule set covering each board and overlapping categories

#### S-5.3.2 — Minimum bracket with itemized reasons

**As a** deck owner **I want** the lowest bracket my deck qualifies for, with every reason listed **so that** I understand the result and know what to change.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-5.3.1, S-5.2.2 |

**Acceptance criteria**
- **Given** category matches, classified combos and a rule set **When** the engine runs **Then** the minimum bracket is the lowest bracket (1–5) whose `hard` constraints are all satisfied.
- **Given** a hard constraint that a lower bracket fails **When** reasons are built **Then** each reason names the category, the count found, the limit from the rule set, the cards involved and the resulting floor, in the form "<count> <category> → Bracket <n>+" (values come from the rule set, never from code).
- **Given** an `advisory` constraint that is exceeded **When** reasons are built **Then** it is listed as a guideline note and does not raise the minimum bracket.
- **Given** a synthetic rule set and any deck **When** a card is added to the deck **Then** the minimum bracket never goes down (monotonic property test).
- **Given** the same deck version, rule set and combo data **When** the engine runs twice **Then** the output is identical.

**Tasks**
- [ ] T-5.3.2.1 (api) `assess(matches, combos, ruleset)` as a pure function returning a `BracketResult` (minimum, reasons, notes)
- [ ] T-5.3.2.2 (api) Reason builder with structured fields plus a display string
- [ ] T-5.3.2.3 (api) Table-driven tests using synthetic rule sets, plus a monotonic property test over generated decks

#### S-5.3.3 — Declared-intent check

**As a** deck owner **I want** to declare the bracket I mean my deck to be and have it checked **so that** I can honestly say "this is a bracket 2 deck" before a game.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-5.3.2 |

**Acceptance criteria**
- **Given** a deck **When** the owner sets `declared_bracket` to a value 1–5 (or clears it) **Then** it is saved on the Deck, and only the owner can change it (403 for others).
- **Given** a declared bracket lower than the computed minimum **When** the assessment runs **Then** the result has `intent_check: "conflict"` and lists the reasons that push the deck above the declared bracket.
- **Given** a computed minimum that falls in the range of brackets the rule set marks `intent_dependent` **When** the result is built **Then** it shows that range, and if the declared bracket is inside it the result has `intent_check: "consistent"` plus the rule set's intent description for the owner to confirm; with no declaration it has `intent_check: "needs_declaration"`.
- **Given** a declared bracket higher than the computed minimum **When** the assessment runs **Then** `intent_check` is `"consistent"`, with a note that the automatic checks allow a lower bracket.
- **Given** any assessment **When** the result is built **Then** it includes an `effective_bracket` for other features (seat picking in E-6, matchmaking in F-10.4): the declared bracket when it is consistent; the computed minimum when the declaration conflicts or there is no declaration outside the intent-dependent range; and the rule set's undeclared default when there is no declaration inside that range.
- **Given** a deck whose computed minimum is in the Bracket 1–2 range and that has no declared intent **When** the result is built **Then** its `effective_bracket` is 2, `effective_bracket_assumed` is true and `intent_check` is `"needs_declaration"`; seating (E-6) and matchmaking (F-10.4) use 2 for it (decision E8). The seeded rule set's undeclared default is 2, and a test pins this case.

**Tasks**
- [ ] T-5.3.3.1 (api) `declared_bracket` column on Deck, migration and `PUT /decks/{id}/declared-bracket`
- [ ] T-5.3.3.2 (api) Intent-check step in the engine, driven by the rule set's `intent_dependent` flags and descriptions
- [ ] T-5.3.3.3 (api) Tests for conflict, consistent, needs_declaration and declared-higher cases
- [ ] T-5.3.3.4 (api) `effective_bracket` and `effective_bracket_assumed` in the result, using the rule set's undeclared default, with tests for each branch, including an undeclared deck at minimum 1 and at minimum 2 both getting `effective_bracket` = 2

#### S-5.3.4 — Store assessments, re-assess on change, and serve them

**As a** deck owner **I want** my deck's bracket kept up to date and its history kept **so that** I always see a current result and know which rules produced it.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-5.3.2, S-5.3.3, F-1.9 |

**Acceptance criteria**
- **Given** an assessment **When** it is stored **Then** the BracketAssessment records the deck version, the rule-set version, the minimum bracket, the reasons, the intent check, the combo-check status and the time it ran.
- **Given** a new DeckVersion is committed **When** the commit finishes **Then** an assessment job is queued, and the result is available from `GET /decks/{id}/versions/{n}/bracket` within 10 s when Spellbook responds normally.
- **Given** a new rule-set version becomes active **When** the re-assessment job runs **Then** every deck's current version gets a new assessment with the new version, and older assessments are kept.
- **Given** a deck version whose latest assessment used an older rule set **When** it is requested before re-assessment finishes **Then** the response includes `stale: true` and the rule-set version used.
- **Given** a list of a user's decks **When** it is requested with brackets included **Then** the latest assessment per deck is loaded without one query per deck.
- **Given** a game is starting **When** the games domain calls `current_assessment(deck_version_id)` from `brackets/services/__init__.py` **Then** it gets the assessment ID, minimum bracket, effective bracket and rule-set version, so the game can freeze them for bracket-vs-outcome stats (decision F2); later re-assessments never change what a started game recorded.

**Tasks**
- [ ] T-5.3.4.1 (api) `BracketAssessment` model and migration
- [ ] T-5.3.4.2 (api) arq jobs: assess one deck version; re-assess all current versions in batches
- [ ] T-5.3.4.3 (api) Endpoints: latest assessment, re-run (owner only), assessment history per deck
- [ ] T-5.3.4.4 (api) Tests for storage, staleness and query count in the list endpoint
- [ ] T-5.3.4.5 (api) `current_assessment(deck_version_id)` in the public brackets service interface, with a test that an older assessment ID stays resolvable after re-assessment

#### S-5.3.5 — Bracket-change notices (API)

**As a** deck owner **I want** to be told in the app when a new rule set changes my deck's bracket **so that** I'm not caught out at the table.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-5.3.4 |

**Acceptance criteria**
- **Given** a re-assessment caused by a newly active rule set **When** a deck's minimum or effective bracket differs from its previous assessment **Then** a notice is stored for the deck's owner with the deck, the old and new bracket, the rule-set version and the reasons that changed (decision E9).
- **Given** a re-assessment where the bracket is unchanged, or an assessment caused by the owner saving a new deck version **When** it finishes **Then** no notice is created.
- **Given** a user **When** they call `GET /brackets/notices` **Then** they get only their own notices, newest first, paged with a keyset cursor (decision B2), with an unread count; `POST /brackets/notices/{id}/read` and `POST /brackets/notices/read-all` mark them read.
- **Given** a rule-set change that affects 500 decks **When** notices are created **Then** they are written in batches inside the re-assessment job, one per affected deck.

**Tasks**
- [ ] T-5.3.5.1 (api) `BracketNotice` model and migration
- [ ] T-5.3.5.2 (api) Notice creation in the rule-set re-assessment job, comparing with the previous assessment
- [ ] T-5.3.5.3 (api) List and mark-read endpoints
- [ ] T-5.3.5.4 (api) Tests for changed, unchanged and owner-edit cases and for per-user access

## F-5.4 — Bracket UI

Show the bracket result on web and mobile: a badge with the minimum bracket, the itemized reasons, the combo-check status and the intent check (including the "assumed Bracket 2" state for undeclared decks), a control for the owner to declare the intended bracket, and in-app notices when a new rule set changes a deck's bracket.

**Phase:** 1 · **Priority:** Must

#### S-5.4.1 — Bracket badge and reasons panel (web)

**As a** deck owner on the web **I want** to see my deck's bracket and why **so that** I can tell my playgroup what to expect.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web | 1 | S-5.3.4, S-4.4.4, F-3.3 |

**Acceptance criteria**
- **Given** a deck with an assessment **When** the deck page opens **Then** a badge shows "Bracket <n>" (or "Bracket <n>+ — combo check incomplete" when incomplete), with the rule-set version on hover or focus.
- **Given** the reasons panel **When** it is opened **Then** each reason shows its text and its cards; clicking a card opens its preview; guideline notes are listed separately.
- **Given** an assessment with `stale: true` **When** the page opens **Then** the badge shows "updating…" until a fresh result arrives.
- **Given** an assessment with `effective_bracket_assumed: true` **When** the badge is shown **Then** it reads "Bracket 2 (assumed)" with a marker explaining "No intent declared, treated as Bracket 2", and the owner sees the declare prompt from S-5.4.3 (decision E8).
- **Given** the "My decks" list **When** it loads **Then** each deck shows its bracket badge.

**Tasks**
- [ ] T-5.4.1.1 (web) Bracket badge and reasons panel components
- [ ] T-5.4.1.2 (web) Incomplete, stale and intent-check states
- [ ] T-5.4.1.3 (web) Vitest tests for each state

#### S-5.4.2 — Bracket badge and reasons (mobile)

**As a** deck owner on my phone **I want** the same bracket result and reasons **so that** I can show them at the table.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | mobile | 1 | S-5.3.4, S-4.4.5, F-3.3 |

**Acceptance criteria**
- **Given** a deck with an assessment **When** the deck screen opens **Then** the bracket badge is shown with the same incomplete, stale and "assumed" states as on web.
- **Given** the badge **When** the user taps it **Then** a sheet lists the reasons with their cards, the guideline notes, the intent check and the rule-set version.

**Tasks**
- [ ] T-5.4.2.1 (mobile) Badge and reasons sheet
- [ ] T-5.4.2.2 (mobile) Jest tests for each state

#### S-5.4.3 — Declare the intended bracket

**As a** deck owner **I want** to set the bracket I intend my deck to be **so that** the app can check it and my playgroup can see it.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | web, mobile | 1 | S-5.3.3, S-5.4.1, S-5.4.2 |

**Acceptance criteria**
- **Given** the deck page **When** the owner picks a bracket 1–5 from a selector **Then** it is saved and the intent check updates within 2 s.
- **Given** a declaration that conflicts with the computed minimum **When** the result arrives **Then** a warning lists the reasons for the conflict.
- **Given** an intent-dependent result with no declaration **When** the owner views the deck **Then** they are prompted to declare a bracket, with the rule set's intent descriptions shown next to each option and a note that the deck is treated as Bracket 2 until they do (decision E8).

**Tasks**
- [ ] T-5.4.3.1 (web) Bracket selector with intent descriptions and conflict warning
- [ ] T-5.4.3.2 (mobile) Bracket picker with the same content

#### S-5.4.4 — Target-bracket helper

**As a** deck owner **I want** to pick a target bracket and see which cards keep my deck above it **so that** I can tune a deck down for a softer pod.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 3 | api, web, mobile | 1 | S-5.3.2, S-5.4.1, S-5.4.2 |

**Acceptance criteria**
- **Given** a deck whose minimum bracket is above the target **When** the owner picks the target **Then** the app lists, per failed hard constraint, the cards involved and how many must be removed to meet the target's limit.
- **Given** a combo that keeps the deck above the target **When** it is listed **Then** removing any one of its cards is shown as enough to break it.
- **Given** a target at or above the current minimum **When** it is picked **Then** the app says the deck already fits.

**Tasks**
- [ ] T-5.4.4.1 (api) `GET /decks/{id}/versions/{n}/bracket/target/{bracket}` computing the gap from the stored result
- [ ] T-5.4.4.2 (web) Target picker and gap list
- [ ] T-5.4.4.3 (mobile) Target picker and gap list

#### S-5.4.5 — Bracket-change notices (web + mobile)

**As a** deck owner **I want** to see in the app when a rule-set update moved my deck to another bracket **so that** I can check the reasons or adjust the deck.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web, mobile | 1 | S-5.3.5, S-5.4.1, S-5.4.2 |

**Acceptance criteria**
- **Given** unread bracket notices **When** the user opens the app **Then** an unread count is shown on a notices entry in the main navigation.
- **Given** the notices list **When** it opens **Then** each notice reads like "<deck>: Bracket 2 → 3 under rule set <version>" and opening it goes to the deck's reasons panel and marks it read.
- **Given** a deck with an unread notice **When** the owner opens the deck **Then** a dismissible banner names the rule-set change.
- **Given** no notices **When** the list opens **Then** an empty state is shown.

**Tasks**
- [ ] T-5.4.5.1 (shared) Notices hooks (list, unread count, mark read) in `packages/api-client`
- [ ] T-5.4.5.2 (web) Navigation badge, notices list and deck banner
- [ ] T-5.4.5.3 (mobile) Tab badge, notices screen and deck banner
- [ ] T-5.4.5.4 (web, mobile) Component tests for unread, read and empty states

## Open questions

None open. Resolved: see OPEN-QUESTIONS.md decisions log.
