# E-4 — Decks & Legality

| | |
|---|---|
| **Phase(s)** | 1 (F-4.1 … F-4.6), 2 (F-4.7), 3 (S-4.1.6) |
| **Goal** | Players can bring their Commander decks into the app from a pasted list or a Moxfield/Archidekt URL, edit them in the app, keep a version history, share them read-only with their playgroups, and see at a glance whether a deck is legal for Commander and for their playgroup's house rules. |
| **Success measures** | • A 100-card list pasted in Moxfield, Arena or MTGO format imports in under 5 s, with every unrecognised line reported back to the user (0 silent drops).<br>• When Moxfield or Archidekt is unreachable or blocks us, 100% of URL imports end in a clear "paste the list instead" fallback, never a dead end.<br>• Validation catches every rule in F-4.5 against a test corpus of known-legal and known-illegal decks, with no false "legal" results.<br>• Every game seat (E-6) can point at an immutable deck version. |
| **Out of scope** | Bracket assessment (E-5). Collection ownership, assigning copies to decks and deck cost (E-9). Per-card win rates (F-7.5). Exporting decks back to Moxfield/Archidekt. Formats other than Commander. |

## F-4.1 — Text decklist import

Parse a pasted decklist in Moxfield, Arena or MTGO style, resolve each line to a Scryfall card (and printing when given), and show the user a preview that lists every line we could not match, with "did you mean" suggestions, before anything is saved. Unknown lines can be skipped explicitly; skipped lines are kept as notes on the version (decision E1). When no printing is given, the default printing rule applies (decisions E3 and G4): Scryfall's default printing until Phase 3; from Phase 3, the printing of the owner's copy assigned to that deck card, otherwise Scryfall's default (S-4.1.6). There is no "cheapest owned copy" rule.

**Phase:** 1 · **Priority:** Must

#### S-4.1.1 — Parse common decklist text formats

**As a** deck owner **I want** to paste my list exactly as Moxfield, Arena or MTGO exports it **so that** I don't have to reformat it by hand.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | F-1.4 |

**Acceptance criteria**
- **Given** the lines `1 Sol Ring`, `1x Sol Ring` and `Sol Ring` **When** the text is parsed **Then** each yields quantity 1 and name "Sol Ring" (a missing quantity defaults to 1).
- **Given** the line `1 Sol Ring (C21) 263` (optionally followed by a foil marker such as `*F*`) **When** it is parsed **Then** the result has name "Sol Ring", set code "C21" and collector number "263", and the foil marker does not end up in the name.
- **Given** a list with section headers `Commander`, `Deck`, `Sideboard`, `Maybeboard` and `Companion` (case-insensitive, with or without a trailing colon or count such as `Commander (1)`) **When** it is parsed **Then** every card is assigned to the matching board.
- **Given** an MTGO list where sideboard lines start with `SB:` or the sideboard follows a single blank line after the main deck **When** it is parsed **Then** those cards go to the sideboard board.
- **Given** a line that matches no supported pattern (for example `// comment` or `???`) **When** it is parsed **Then** it is returned in an `unparsed_lines` list with its 1-based line number, and parsing continues with the next line.

**Tasks**
- [ ] T-4.1.1.1 (api) `decks/services/text_parser.py`: pure function from text to a list of `ParsedLine` (qty, name, set, collector number, board, line number)
- [ ] T-4.1.1.2 (api) Section-header and `SB:` handling, with the blank-line sideboard rule for MTGO lists
- [ ] T-4.1.1.3 (api) Table-driven pytest cases built from real export samples of each format (stored as `.txt` test data, not JSON fixtures)

#### S-4.1.2 — Resolve parsed lines to cards and report unknown names

**As a** deck owner **I want** every card name matched to the right card, and told exactly which ones weren't found **so that** no card quietly disappears from my deck.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-4.1.1, F-3.1, F-3.2 |

**Acceptance criteria**
- **Given** names that differ from Scryfall only in letter case or curly vs. straight apostrophes **When** they are resolved **Then** they match the card's `oracle_id`.
- **Given** a double-faced card entered by its front-face name only (e.g. "Delver of Secrets") or a split card entered as "Fire // Ice" or "Fire/Ice" **When** it is resolved **Then** it matches the single correct card.
- **Given** a line with a set code and collector number that exist in the printings table **When** it is resolved **Then** the specific printing is recorded; **Given** they don't exist **Then** the card is still matched by name, the default printing is recorded and a warning "printing not found, used default" is added for that line.
- **Given** a line with no set code **When** it is resolved **Then** the default printing is recorded explicitly on the line, which in Phase 1–2 is Scryfall's default printing for that card (decision E3; from Phase 3, S-4.1.6 uses the copy assigned to the deck card first, decision G4).
- **Given** a 100-line list containing 3 misspelled names **When** it is resolved **Then** the result contains 97 resolved cards and an `unknown_cards` list of exactly 3 entries, each with its line number, the original text and up to 3 "did you mean" suggestions from card search.

**Tasks**
- [ ] T-4.1.2.1 (api) Name normalisation (case, punctuation, face names, split-card separators) and batch lookup against the cards table
- [ ] T-4.1.2.2 (api) Printing lookup by set + collector number, with fallback to the default printing and a per-line warning
- [ ] T-4.1.2.3 (api) Fuzzy suggestions for unknown names using the F-3.2 search service (through `cards/services/__init__.py` only)
- [ ] T-4.1.2.4 (api) Tests for each case above, using factory-built cards
- [ ] T-4.1.2.5 (api) `default_printing(deck, oracle_id)` in `decks/services`, returning Scryfall's default printing; the single place S-4.1.6 extends, also used by in-app editing (S-4.4.6) and the sorting board (S-4.7.1)

#### S-4.1.3 — Import endpoint with preview and commit

**As a** deck owner **I want** to preview an import before saving it **so that** I can fix unknown cards first.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-4.1.2, S-4.4.1 |

**Acceptance criteria**
- **Given** a pasted list **When** the client calls `POST /decks/import/text?mode=preview` **Then** the response contains the resolved cards by board, `unknown_cards`, `unparsed_lines` and warnings, and nothing is written to the database.
- **Given** a preview with 0 unknown cards **When** the client calls the same endpoint with `mode=commit` **Then** a Deck and its first DeckVersion are created and the response returns their IDs with status 201.
- **Given** a list with unknown cards **When** the client commits without listing every unknown line in `skip_lines` **Then** the request is rejected with 422 and the unknown cards are returned again.
- **Given** a commit that lists the unknown lines in `skip_lines` **When** it succeeds **Then** the saved DeckVersion keeps each skipped line (line number and original text) as a note, so the deck page can show "3 lines skipped on import" and list them (decision E1).
- **Given** a commit that leaves the commander and main boards with fewer than 100 cards (for example after skipping lines) **When** it succeeds **Then** the deck is saved and marked "incomplete" (see S-4.4.2).

**Tasks**
- [ ] T-4.1.3.1 (api) Pydantic request/response schemas and a thin router calling `decks.services.import_text`
- [ ] T-4.1.3.2 (api) Import notes column on DeckVersion (skipped lines kept as notes, warnings) plus Alembic migration
- [ ] T-4.1.3.3 (api) Endpoint tests for preview, commit, 422 on unacknowledged unknowns and commit with `skip_lines`

#### S-4.1.4 — Paste-import screen (web)

**As a** deck owner on my computer **I want** to paste a list, see the preview and fix problems inline **so that** importing feels like one quick step.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web | 1 | S-4.1.3, F-1.6, F-3.3 |

**Acceptance criteria**
- **Given** a pasted list **When** the user clicks "Preview" **Then** the screen shows card counts per board and a list of unknown cards with their line numbers.
- **Given** an unknown card with suggestions **When** the user picks a suggestion **Then** that line is rewritten in the text area and the preview refreshes.
- **Given** unknown cards that remain **When** the user clicks "Import" **Then** they must confirm "Skip N lines" before the commit is sent; cancelling keeps them on the preview.
- **Given** a successful import **When** the commit returns **Then** the user lands on the new deck's detail page.

**Tasks**
- [ ] T-4.1.4.1 (web) Import route with text area, preview call and board summary using TanStack Query mutations
- [ ] T-4.1.4.2 (web) Unknown-card list with "did you mean" picks that edit the source text
- [ ] T-4.1.4.3 (web) Skip-lines confirmation dialog and redirect on success, with Vitest component tests

#### S-4.1.5 — Paste-import screen (mobile)

**As a** deck owner on my phone **I want** to paste a list copied from another app **so that** I can import decks without a computer.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | mobile | 1 | S-4.1.3, F-1.7, F-3.3 |

**Acceptance criteria**
- **Given** a list on the clipboard **When** the user taps "Paste from clipboard" **Then** the text field is filled and a preview runs automatically.
- **Given** a preview with unknown cards **When** the user taps an unknown card **Then** a sheet shows the suggestions, and choosing one updates the text and re-runs the preview.
- **Given** remaining unknown cards **When** the user taps "Import" **Then** a confirmation "Skip N lines" is shown before the commit is sent.

**Tasks**
- [ ] T-4.1.5.1 (mobile) Import screen with multiline input, clipboard paste and preview summary
- [ ] T-4.1.5.2 (mobile) Unknown-card suggestion sheet and skip confirmation
- [ ] T-4.1.5.3 (mobile) Jest tests for the preview and confirmation states

#### S-4.1.6 — Use the copy assigned to the deck card

**As a** deck owner with a collection **I want** a deck card without a chosen printing to use the copy I assigned to that deck **so that** my decks show the cards I actually play with.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 3 | S-4.1.2, S-9.1.1, S-9.6.6 |

**Acceptance criteria**
- **Given** an owned copy of a card assigned to this deck (each copy is assigned to one deck, decision G4) **When** a new version is created by import, in-app editing or a sorting-board save and the card's line names no printing **Then** the exact printing of the assigned copy is recorded on the deck card.
- **Given** assigned copies of more than one printing of the same card (for example 6 Forests of one printing and 4 of another) **When** the version is created **Then** the deck card is recorded as one line per printing with the matching quantities, and the singleton and size checks still count by `oracle_id`.
- **Given** an owner who owns copies of a card but none is assigned to this deck, or who owns no copy **When** the default printing is chosen **Then** Scryfall's default printing is recorded; the app never picks a "cheapest owned copy" (decision G4).
- **Given** a line that names a set code and collector number that exist **When** it is resolved **Then** that printing is recorded whether or not the owner has a copy of it.
- **Given** deck versions saved before a copy was assigned, or before this story ships **When** they are read **Then** their recorded printings are unchanged; a new assignment applies from the next saved version.
- **Given** a playgroup member who views or picks the deck **When** printings are chosen **Then** only the deck owner's assigned copies are consulted.

**Tasks**
- [ ] T-4.1.6.1 (api) Extend `default_printing(deck, oracle_id)` to ask the collection domain's public service interface (S-9.6.6) for the copies assigned to the deck, and fall back to Scryfall's default printing
- [ ] T-4.1.6.2 (api) Split a deck card into one line per assigned printing when its assigned copies differ
- [ ] T-4.1.6.3 (api) Tests for an assigned copy, mixed assigned printings, owned but unassigned, nothing owned, an explicit printing and unchanged older versions

## F-4.2 — Moxfield URL import

Import a deck from a public Moxfield URL through Moxfield's unofficial API, as a background job with caching and an honest User-Agent. Any failure ends in a clear fallback to text paste. This feature also builds the shared URL-import job and UI that F-4.3 reuses.

**Gate (decision E4):** before any other F-4.2 or F-4.3 work starts, Moxfield's and Archidekt's current terms are checked (T-4.2.1.5). If either site's terms don't allow this use, that site's import is stopped and raised with the product owner; text paste stays the fallback.

**Phase:** 1 · **Priority:** Must

#### S-4.2.1 — Polite deck-site HTTP client with caching

**As the** system **I want** one well-behaved HTTP client for deck sites **so that** we don't get blocked and we don't fetch the same deck twice in a short time.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | F-1.4, F-1.3 |

**Acceptance criteria**
- **Given** any outbound request to a deck site **When** it is sent **Then** it carries the User-Agent `MTGStats/<version> (+<repo URL>)`, with the version from the app build and the repo URL from settings; it contains no personal email and is never a browser-impersonating string (decision E4).
- **Given** a deck fetched successfully **When** the same deck URL is requested again within the cache TTL (from settings; default 15 minutes, long enough to cover preview, commit and repeated clicks while keeping re-imports of a freshly edited deck reasonably current) **Then** the cached response is used and no outbound request is made.
- **Given** a deck site that answers 429 **When** the client receives it **Then** it honours `Retry-After` up to a configured cap and makes at most 2 retries before reporting `rate_limited`.
- **Given** a request that takes longer than the configured timeout (default 10 s) **When** it times out **Then** the client reports `timeout` rather than raising an unhandled error.

**Tasks**
- [ ] T-4.2.1.5 (api) **Do first, before any F-4.2/F-4.3 work:** check Moxfield's and Archidekt's current terms of use and API policies (including whether API access must be requested), record the outcome and date on the F-4.2 issue, and raise any blocker with the product owner
- [ ] T-4.2.1.1 (api) `decks/services/deck_sites/http.py`: shared httpx `AsyncClient` with User-Agent, timeout and retry policy from pydantic-settings
- [ ] T-4.2.1.2 (api) Response cache in Redis keyed by site + deck ID, with TTL from settings (default 15 minutes)
- [ ] T-4.2.1.3 (api) Per-site concurrency limit so a burst of imports can't flood one site
- [ ] T-4.2.1.4 (api) Tests with a mocked transport (`httpx.MockTransport`) for success, cache hit, 429 and timeout

#### S-4.2.2 — Map a Moxfield deck to the import model

**As a** deck owner **I want** my Moxfield commanders, mainboard, sideboard, maybeboard and companion brought over correctly **so that** the imported deck matches what I see on Moxfield.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-4.2.1, S-4.1.2 |

**Acceptance criteria**
- **Given** a URL such as `https://www.moxfield.com/decks/<publicId>` (with or without `www.`, trailing slash or query string) **When** it is parsed **Then** the public ID is extracted; any other URL shape is rejected with a clear "not a Moxfield deck URL" error.
- **Given** a Moxfield response containing Scryfall IDs for cards **When** it is mapped **Then** cards are matched by Scryfall ID first and by name only when the ID is missing or unknown.
- **Given** Moxfield's commander, main, side, maybe and companion boards **When** the deck is mapped **Then** each card goes to the matching board with its quantity.
- **Given** a card in the response that can't be matched **When** the deck is mapped **Then** it appears in `unknown_cards` exactly as in a text import.

**Tasks**
- [ ] T-4.2.2.1 (api) URL parser and Moxfield response → `ParsedLine` mapper, tolerant of missing optional fields
- [ ] T-4.2.2.2 (api) Schema-drift guard: if required fields are missing, report `unexpected_format` instead of importing a partial deck
- [ ] T-4.2.2.3 (api) Tests using recorded sample responses stored as test data files

#### S-4.2.3 — URL import as a background job with text-paste fallback

**As a** deck owner **I want** URL imports to keep working even when the deck site is slow or blocks us **so that** I always have a way to get my deck in.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-4.2.2, S-4.1.3, F-1.9 |

**Acceptance criteria**
- **Given** a valid deck URL **When** the client calls `POST /decks/import/url` **Then** an arq job is queued and the response returns a job ID with status 202.
- **Given** a queued job **When** the client polls `GET /decks/import/jobs/{id}` **Then** it sees one of `queued`, `running`, `preview_ready` or `failed`, and a `preview_ready` job returns the same preview payload as a text import.
- **Given** a fetch that ends in `rate_limited`, `timeout`, `blocked` (403), `not_found` (404), `unexpected_format` or a 5xx **When** the job finishes **Then** its status is `failed` with a machine-readable reason, a user-facing message, and `fallback: "text_paste"`.
- **Given** a committed URL import **When** the deck is saved **Then** the Deck stores the source URL and site, and the DeckVersion records that it came from that URL.

**Tasks**
- [ ] T-4.2.3.1 (api) arq task `import_deck_from_url`, with job state stored in Redis and a TTL
- [ ] T-4.2.3.2 (api) Job status endpoint and a commit endpoint that reuses the S-4.1.3 commit path
- [ ] T-4.2.3.3 (api) Failure-reason enum mapped to user messages, with a test for each reason

#### S-4.2.4 — URL import screen with fallback (web + mobile)

**As a** deck owner **I want** to paste a deck URL and either get my deck or be sent straight to paste mode **so that** a site outage never blocks me.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web, mobile | 1 | S-4.2.3, S-4.1.4, S-4.1.5 |

**Acceptance criteria**
- **Given** a pasted URL **When** the user submits it **Then** the site is detected from the URL (Moxfield or Archidekt), a progress state is shown, and polling stops after 60 s with a timeout message.
- **Given** a job that reaches `preview_ready` **When** the preview loads **Then** the user sees the same preview and unknown-card handling as the text import.
- **Given** a job that fails **When** the failure is shown **Then** the message explains what went wrong in plain words and a "Paste the list instead" button opens text import with the deck URL kept in the deck's source field.
- **Given** a URL from an unsupported site **When** it is entered **Then** the form says which sites are supported and offers text paste, without sending a request.

**Tasks**
- [ ] T-4.2.4.1 (shared) Site detection helper and job-polling hook in `packages/api-client` usable by web and mobile
- [ ] T-4.2.4.2 (web) URL tab on the import screen with progress, failure and fallback states
- [ ] T-4.2.4.3 (mobile) URL input on the import screen with the same states
- [ ] T-4.2.4.4 (web, mobile) Component tests for success, each failure class and unsupported site

## F-4.3 — Archidekt URL import

Import a deck from a public Archidekt URL, reusing the deck-site client, the job and the fallback flow from F-4.2. Archidekt's custom categories are kept so the deck looks familiar. Work starts only after the terms check in T-4.2.1.5 (decision E4).

**Phase:** 1 · **Priority:** Must

#### S-4.3.1 — Fetch Archidekt decks

**As a** deck owner **I want** to import from an Archidekt URL **so that** players who build on Archidekt get the same experience as Moxfield users.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-4.2.1 |

**Acceptance criteria**
- **Given** a URL such as `https://archidekt.com/decks/<id>/<slug>` (slug optional) **When** it is parsed **Then** the numeric deck ID is extracted; other shapes are rejected with "not an Archidekt deck URL".
- **Given** a public deck **When** it is fetched **Then** the request goes through the shared client (User-Agent, cache, retries) from S-4.2.1.
- **Given** a private or deleted deck **When** it is fetched **Then** the result is `not_found` and no partial data is stored.

**Tasks**
- [ ] T-4.3.1.1 (api) Archidekt URL parser and fetcher on top of the shared deck-site client
- [ ] T-4.3.1.2 (api) Tests with recorded responses for a public, a private and a deleted deck

#### S-4.3.2 — Map Archidekt categories to boards

**As a** deck owner **I want** my Archidekt commander, maybeboard, sideboard and custom categories carried over **so that** my deck's organisation survives the import.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-4.3.1, S-4.1.2, S-4.4.1 |

**Acceptance criteria**
- **Given** cards in Archidekt's Commander category **When** they are mapped **Then** they go to the commander board.
- **Given** categories that Archidekt marks as not included in the deck (e.g. Maybeboard, Sideboard) **When** they are mapped **Then** the cards go to the maybe or side board and are not counted toward the 100.
- **Given** a custom category such as "Ramp" **When** it is mapped **Then** the cards stay on the main board and keep "Ramp" as their DeckCard category.
- **Given** a card listed in more than one category **When** it is mapped **Then** it is imported once, with its first (primary) category, and the quantity is not doubled.

**Tasks**
- [ ] T-4.3.2.1 (api) Archidekt response → `ParsedLine` mapper with board and category rules
- [ ] T-4.3.2.2 (api) Same schema-drift guard as Moxfield (`unexpected_format`)
- [ ] T-4.3.2.3 (api) Tests for each mapping rule

#### S-4.3.3 — Archidekt in the URL-import job and fallback

**As a** deck owner **I want** Archidekt imports to behave exactly like Moxfield imports, including the fallback **so that** I don't have to learn two flows.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 1 | S-4.3.2, S-4.2.3 |

**Acceptance criteria**
- **Given** an Archidekt URL **When** it is sent to `POST /decks/import/url` **Then** the job routes it to the Archidekt fetcher and returns the same status values as for Moxfield.
- **Given** an Archidekt failure of any class in S-4.2.3 **When** the job finishes **Then** it returns the same reason codes and `fallback: "text_paste"`.

**Tasks**
- [ ] T-4.3.3.1 (api) Register Archidekt in the deck-site registry used by the import job
- [ ] T-4.3.3.2 (api) Job tests covering Archidekt success and failure paths

## F-4.4 — Deck management & versions

Store decks with an immutable version history so games, stats and bracket assessments always point at the exact list that was played. Owners can list, view, rename, archive and edit their decks; every save creates a new version (decision E10). Decks are visible read-only to every member of a playgroup the owner belongs to; only the owner can change them (decision C5). A deck whose commander and main boards hold fewer than 100 cards is marked "incomplete" (decision E1).

**Phase:** 1 · **Priority:** Must

#### S-4.4.1 — Deck, DeckVersion and DeckCard data model

**As a** developer **I want** a deck model where every saved list is an immutable version **so that** games and assessments always refer to the list that was actually used.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | F-1.4, F-2.2, F-3.1 |

**Acceptance criteria**
- **Given** the Alembic migration **When** it is applied to an empty database and then downgraded **Then** both succeed, and the drift check reports no changes afterwards.
- **Given** a Deck **When** it is created **Then** it has an owner, a name, a source (`text`, `moxfield` or `archidekt`), an optional source URL, a current-version pointer and an `archived_at` that defaults to null.
- **Given** a DeckCard **When** it is stored **Then** it references a version, a card by `oracle_id`, an optional printing, a quantity ≥ 1, a board (`commander`, `main`, `side`, `maybe` or `companion`) and a category string (default `""`).
- **Given** a DeckVersion that has been saved **When** the service is asked to change its cards **Then** it refuses; changes always create a new version with the next version number.

**Tasks**
- [ ] T-4.4.1.1 (api) SQLAlchemy models `Deck`, `DeckVersion`, `DeckCard` with indexes on owner and (version, board)
- [ ] T-4.4.1.2 (api) Alembic migration with upgrade and downgrade
- [ ] T-4.4.1.3 (api) Repository query builders (decks for owner, version with cards and cards eagerly loaded)
- [ ] T-4.4.1.4 (api) factory_boy factories for Deck, DeckVersion and DeckCard

#### S-4.4.2 — Deck list, detail, rename and archive endpoints

**As a** deck owner **I want** to see, rename and archive my decks, and let my playgroups see them **so that** I can keep my deck list tidy and others can pick or check my decks.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-4.4.1, F-2.1, S-2.4.1 |

**Acceptance criteria**
- **Given** a user with 12 decks, 2 of them archived **When** they call `GET /decks` **Then** they get 10 decks, each with name, commander(s), color identity, current version number, card count and an `incomplete` flag, and the query count does not grow with the number of decks.
- **Given** `?include_archived=true` **When** the list is requested **Then** all 12 decks are returned.
- **Given** more decks than the page size **When** the list is requested **Then** it is paged with an opaque keyset cursor (`next_cursor`), never an offset (decision B2).
- **Given** a deck whose current version has fewer than 100 cards in the commander and main boards **When** it is listed or read **Then** `incomplete` is true; once a later version reaches 100 cards it is false (decision E1).
- **Given** a deck used in a recorded game **When** the owner deletes it **Then** it is archived (not removed) and its versions stay readable by the game history.
- **Given** a user who shares at least one playgroup with the owner **When** they read the deck, its versions, diff, validation or stats **Then** they get it read-only; a user who shares no playgroup with the owner gets 404 (decision C5).
- **Given** a member of playgroup P **When** they call `GET /decks?playgroup_id=P` **Then** they get the non-archived decks of all current members of P, with the same fields and constant query count; a non-member gets 403.
- **Given** a user who is not the owner **When** they try to rename, archive, edit or re-import into a deck **Then** the API returns 403.

**Tasks**
- [ ] T-4.4.2.1 (api) Router, schemas and `decks.services` functions for list, get, rename and archive
- [ ] T-4.4.2.2 (api) Eager loading of commanders and current version in the list query, with a query-count test
- [ ] T-4.4.2.3 (api) Permission tests for owner vs. non-owner
- [ ] T-4.4.2.4 (api) `can_view_deck(user, deck)` and `can_edit_deck(user, deck)` exported from `decks/services/__init__.py`, using shared-playgroup membership from the playgroups service interface; reused by E-6 for seats and spectators
- [ ] T-4.4.2.5 (api) `playgroup_id` list filter, card count and `incomplete` flag, with tests for member, non-member and the 99/100 boundary

#### S-4.4.3 — New versions and version history with diff

**As a** deck owner **I want** each re-import or edit saved as a new version, and to see what changed between versions **so that** I can track how my deck evolved.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-4.4.1, S-4.1.3 |

**Acceptance criteria**
- **Given** an existing deck **When** the owner re-imports a list (text or URL) into it **Then** a new DeckVersion is created with the next number and becomes current; earlier versions are unchanged.
- **Given** a re-import identical to the current version **When** it is committed **Then** no new version is created and the response says "no changes".
- **Given** versions 3 and 5 **When** the client calls `GET /decks/{id}/versions/diff?from=3&to=5` **Then** the response lists cards added, removed and with changed quantity or board.
- **Given** a game seat that references version 3 **When** version 4 becomes current **Then** the seat still references version 3.

**Tasks**
- [ ] T-4.4.3.1 (api) `create_version` service with a "no changes" check based on a content hash
- [ ] T-4.4.3.2 (api) Version list and diff endpoints
- [ ] T-4.4.3.3 (api) Tests for version numbering, no-op re-import and diff output

#### S-4.4.4 — Deck list and deck detail screens (web)

**As a** deck owner on the web **I want** a page with all my decks and a detail page per deck **so that** I can browse lists and history.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web | 1 | S-4.4.2, S-4.4.3, F-1.6, F-3.3 |

**Acceptance criteria**
- **Given** a user with decks **When** they open "My decks" **Then** each deck shows its commander image, name, color identity and a validation status badge (when F-4.5 results exist).
- **Given** a deck detail page **When** it loads **Then** cards are grouped by board and then by type (Creature, Instant, …) with counts per group.
- **Given** a deck marked `incomplete` **When** it is shown in the list or opened **Then** an "Incomplete (97/100)" badge is shown, and a version with skipped import lines lists them as notes on the detail page.
- **Given** the history tab **When** the user selects two versions **Then** the diff is shown as added/removed/changed lists.
- **Given** a keyboard-only user **When** they tab through the deck list **Then** every deck card is focusable and Enter opens it.

**Tasks**
- [ ] T-4.4.4.1 (web) "My decks" route with TanStack Query and empty state linking to import
- [ ] T-4.4.4.2 (web) Deck detail with grouped list using the F-3.3 card components
- [ ] T-4.4.4.3 (web) History tab with version picker and diff view

#### S-4.4.5 — Deck list and deck detail screens (mobile)

**As a** deck owner on my phone **I want** to browse my decks and their history **so that** I can check a list at the table.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | mobile | 1 | S-4.4.2, S-4.4.3, F-1.7, F-3.3 |

**Acceptance criteria**
- **Given** a user with decks **When** they open the Decks tab **Then** decks show commander art, name, color identity and validation status, and pull-to-refresh reloads them.
- **Given** a deck with 100 cards **When** the detail screen opens **Then** cards are grouped by board and type in a virtualised list that scrolls smoothly.
- **Given** a deck marked `incomplete` **When** it is shown in the list or opened **Then** an "Incomplete (97/100)" badge is shown, and a version with skipped import lines lists them as notes on the detail screen.
- **Given** the history screen **When** the user picks two versions **Then** the diff is shown.

**Tasks**
- [ ] T-4.4.5.1 (mobile) Decks tab with list and pull-to-refresh
- [ ] T-4.4.5.2 (mobile) Detail screen with sectioned, virtualised list
- [ ] T-4.4.5.3 (mobile) History and diff screen

#### S-4.4.6 — Edit a deck's cards in the app

**As a** deck owner **I want** to add, remove or change the quantity of cards without re-importing **so that** small swaps are quick.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 1 | S-4.4.3, S-4.4.2, S-4.1.2, F-3.2, F-3.3 |

**Acceptance criteria**
- **Given** a deck in edit mode **When** the user searches for a card and adds it **Then** it appears in the main board with quantity 1 in the pending changes, using the default printing rule from S-4.1.2.
- **Given** pending changes **When** the user taps "Save" **Then** exactly one new DeckVersion is created containing all of them, and it becomes current (decision E10).
- **Given** pending changes **When** the user leaves the screen **Then** they are asked to save or discard, and discarding creates no version.
- **Given** an incomplete deck **When** a save brings the commander and main boards to 100 cards **Then** the "incomplete" marker clears.
- **Given** a playgroup member who is not the owner **When** they view the deck **Then** no edit control is shown, and `PATCH /decks/{id}/cards` from them returns 403.

**Tasks**
- [ ] T-4.4.6.1 (api) `PATCH /decks/{id}/cards` that takes a change set and creates one new version
- [ ] T-4.4.6.2 (web) Edit mode with card search, quantity steppers and remove buttons
- [ ] T-4.4.6.3 (mobile) Edit mode with the same actions
- [ ] T-4.4.6.4 (api) Tests that one save creates one version and an empty change set creates none

#### S-4.4.7 — Browse playgroup members' decks (web + mobile)

**As a** playgroup member **I want** to browse the decks of everyone in my playgroup **so that** I can see what they play and check a deck before a game.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web, mobile | 1 | S-4.4.2, S-4.4.4, S-4.4.5, S-2.4.1 |

**Acceptance criteria**
- **Given** a member of a playgroup **When** they open the playgroup's "Decks" view **Then** they see every member's non-archived decks with owner, commander image, name, color identity, validation badge and "incomplete" badge, and can filter by owner (decision C5).
- **Given** another member's deck **When** it is opened **Then** the detail, history and stats views are shown read-only: no edit, rename, archive, re-import or declare-bracket controls.
- **Given** a deck opened from playgroup P's "Decks" view **When** its validation is shown **Then** P's house rules are applied by default (decision E2).

**Tasks**
- [ ] T-4.4.7.1 (shared) Hook for `GET /decks?playgroup_id=` and an `isOwner` helper in `packages/api-client`
- [ ] T-4.4.7.2 (web) Playgroup decks page and read-only mode of the deck detail page
- [ ] T-4.4.7.3 (mobile) Playgroup decks screen and read-only mode of the deck screen
- [ ] T-4.4.7.4 (web, mobile) Component tests for owner vs. member views

## F-4.5 — Deck validation

A validation service that checks a deck version against the Commander rules (banned list from Scryfall `legalities.commander`, color identity, singleton with exceptions, exactly 100 cards, commander eligibility, companion) and the playgroup's house rules from F-2.5. House rules can add bans and can allow officially banned cards ("rule 0"); they apply per playgroup, using the playgroup of the game or of the view (decision E2). Companion conditions are checked automatically where possible, otherwise shown as "verify yourself" (decision E5). Results are stored per version as a DeckValidation and shown in the UI. There is no restricted list.

**Phase:** 1 · **Priority:** Must

#### S-4.5.1 — Validation framework and stored results

**As a** developer **I want** each legality rule to be an independent check that returns structured issues **so that** rules can be tested on their own and results stored and shown consistently.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-4.4.1 |

**Acceptance criteria**
- **Given** a deck version **When** validation runs **Then** every registered check runs and each issue has a code (e.g. `banned_card`), severity (`error` or `warning`), a message and the card(s) involved.
- **Given** a deck with only warnings **When** validation finishes **Then** the status is `legal_with_warnings`; with any error it is `illegal`; with none it is `legal`.
- **Given** a validation result **When** it is stored **Then** the DeckValidation records the version, the playgroup context (or none), the date of the Scryfall data used and the time it ran.
- **Given** a new DeckVersion is committed **When** the commit finishes **Then** validation runs automatically without a playgroup context; `GET /decks/{id}/versions/{n}/validation?playgroup_id=…` runs or returns it for a playgroup.

**Tasks**
- [ ] T-4.5.1.1 (api) `DeckValidation` model and migration; `Issue` Pydantic schema
- [ ] T-4.5.1.2 (api) `decks/services/validation/` with a check registry and a `validate(version, playgroup)` entry point
- [ ] T-4.5.1.3 (api) Auto-run on commit and the validation endpoint
- [ ] T-4.5.1.4 (api) Re-validation arq job triggered after a Scryfall sync changes any `legalities.commander` value for cards in stored decks

#### S-4.5.2 — Commander eligibility and pairing rules

**As a** deck owner **I want** the app to check that my commander (or pair of commanders) is allowed **so that** I know the deck's foundation is legal.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 8 | api | 1 | S-4.5.1 |

**Acceptance criteria**
- **Given** a single commander that is a legendary creature, or whose oracle text says it "can be your commander" **When** validation runs **Then** no eligibility issue is raised; any other card in the commander board raises `ineligible_commander`.
- **Given** two commanders that both have plain Partner, or both have Friends forever **When** validation runs **Then** the pair is accepted.
- **Given** two commanders where one has "Partner with X" **When** the other is not exactly X **Then** `invalid_commander_pair` is raised; when it is X, the pair is accepted.
- **Given** one commander with "Choose a Background" and one legendary Background enchantment, or one "Doctor's companion" with one Time Lord Doctor creature **When** validation runs **Then** the pair is accepted; mixing pairing mechanics (e.g. Partner with Friends forever) raises `invalid_commander_pair`.
- **Given** 0 commanders, or 3 or more **When** validation runs **Then** `commander_count` is raised.

**Tasks**
- [ ] T-4.5.2.1 (api) Eligibility check from Scryfall type line and oracle text (no hard-coded card names)
- [ ] T-4.5.2.2 (api) Pairing rules: Partner, Partner with, Friends forever, Choose a Background, Doctor's companion, and the mixing rule
- [ ] T-4.5.2.3 (api) Test cases for every pairing mechanic, both legal and illegal, built with card factories that mirror real oracle text

#### S-4.5.3 — Color identity check

**As a** deck owner **I want** cards outside my commanders' color identity flagged **so that** I don't build an illegal deck by accident.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-4.5.2 |

**Acceptance criteria**
- **Given** a commander pair **When** the deck's color identity is computed **Then** it is the union of the commanders' Scryfall `color_identity`.
- **Given** a card in the main board or companion board whose `color_identity` is not a subset of the deck's identity **When** validation runs **Then** `color_identity` is raised for that card.
- **Given** a colorless commander **When** validation runs **Then** any card with a colored identity is flagged and colorless cards (including Wastes) are accepted.
- **Given** cards in the side or maybe board **When** validation runs **Then** they are not checked.

**Tasks**
- [ ] T-4.5.3.1 (api) Color identity check using stored Scryfall `color_identity`
- [ ] T-4.5.3.2 (api) Tests for mono, multi, partner-union and colorless cases

#### S-4.5.4 — Deck size and singleton check

**As a** deck owner **I want** to know if my deck isn't exactly 100 cards or has illegal duplicates **so that** I can fix the count before a game.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-4.5.1 |

**Acceptance criteria**
- **Given** commander and main boards that together hold 99 or 101 cards **When** validation runs **Then** `deck_size` is raised with the actual count; exactly 100 passes. Side, maybe and companion boards are not counted.
- **Given** two copies of the same card by `oracle_id` **When** validation runs **Then** `singleton` is raised naming the card and the count.
- **Given** 30 copies of a card with the Basic supertype (including snow-covered basics and Wastes) **When** validation runs **Then** no singleton issue is raised.
- **Given** a card whose oracle text says "A deck can have any number of cards named …" **When** validation runs **Then** any number is allowed; **Given** oracle text that says "A deck can have up to N cards named …" **Then** up to N copies are allowed and N+1 raises `singleton`.

**Tasks**
- [ ] T-4.5.4.1 (api) Size check over the commander and main boards
- [ ] T-4.5.4.2 (api) Singleton check with exceptions read from type line and oracle text (no hard-coded card names)
- [ ] T-4.5.4.3 (api) Tests for 99/100/101, basics, "any number" and "up to N" cards

#### S-4.5.5 — Banned list and playgroup house rules (bans and allowances)

**As a** player in a playgroup **I want** officially banned cards, our house bans and our "rule 0" allowances applied **so that** a deck is checked against the rules we actually play by.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-4.5.1, F-3.1, F-2.5 |

**Acceptance criteria**
- **Given** a card whose Scryfall `legalities.commander` is `banned` **When** validation runs without a playgroup context, or with a playgroup that doesn't allow it **Then** `banned_card` (error) is raised for it, in any board that counts toward the deck, including the commander and companion boards.
- **Given** a playgroup whose house rules allow an officially banned card **When** validation runs with that playgroup's context **Then** no `banned_card` error is raised for it; instead a `house_allowed` warning reads "Banned officially, allowed by <playgroup>" (decision E2).
- **Given** a card whose `legalities.commander` is `not_legal` **When** validation runs **Then** `not_legal_in_commander` (error) is raised; house allowances apply only to officially banned cards.
- **Given** a playgroup whose house-ban list contains a card **When** validation runs with that playgroup's context **Then** `house_banned` (error) is raised and the message names the playgroup; without a playgroup context, house rules are not applied.
- **Given** a deck version picked for a seat in a game **When** it is validated for that game **Then** the game's playgroup is the context; on a deck page, the playgroup chosen in the view is (decision E2).
- **Given** a playgroup admin adds or removes a house ban or a house allowance **When** the change is saved **Then** stored validations for that playgroup are marked stale and re-run on next read.

**Tasks**
- [ ] T-4.5.5.1 (api) Banned and not-legal checks using stored Scryfall legalities
- [ ] T-4.5.5.2 (api) House-ban check that reads the list through the playgroups service interface (F-2.5)
- [ ] T-4.5.5.3 (api) Staleness marker when the house-ban or house-allowance list changes, with tests
- [ ] T-4.5.5.4 (api) House-allowance check that turns `banned_card` into a `house_allowed` warning for the playgroup in context, reading allowances through the playgroups service interface (F-2.5), with tests for allowed, not allowed, other playgroup and no context

#### S-4.5.6 — Companion handling

**As a** deck owner with a companion **I want** it validated separately from the 100 cards **so that** companion decks aren't wrongly marked illegal.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api | 1 | S-4.5.3, S-4.5.4 |

**Acceptance criteria**
- **Given** a card in the companion board **When** validation runs **Then** it is not counted toward the 100, but it is checked for color identity and the banned list.
- **Given** a card in the companion board without the Companion keyword **When** validation runs **Then** `invalid_companion` (error) is raised.
- **Given** more than 1 card in the companion board **When** validation runs **Then** `companion_count` (error) is raised.
- **Given** a valid companion whose deckbuilding condition has no automatic check (S-4.5.8) **When** validation runs **Then** a `companion_condition_unverified` warning shows the condition text and "verify yourself" (decision E5).

**Tasks**
- [ ] T-4.5.6.1 (api) Companion board checks (keyword, count, identity, banned)
- [ ] T-4.5.6.2 (api) "Verify yourself" warning for companions without an automatic check, with tests

#### S-4.5.7 — Show validation results (web + mobile)

**As a** deck owner **I want** a clear legal/illegal status with the reasons listed **so that** I know exactly what to fix.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web, mobile | 1 | S-4.5.1, S-4.4.4, S-4.4.5 |

**Acceptance criteria**
- **Given** a deck version with validation results **When** the deck page opens **Then** a badge shows Legal, Legal with warnings or Illegal, and the issues are listed grouped by severity with the cards involved.
- **Given** an issue that names a card **When** the user taps or clicks the card **Then** the card preview from F-3.3 opens.
- **Given** a user who belongs to playgroups **When** they pick a playgroup from a selector on the deck page **Then** validation is shown with that playgroup's house rules (bans and allowances) applied; when the page was opened from a playgroup or a game, that playgroup is preselected (decision E2).
- **Given** a card allowed by the selected playgroup despite an official ban **When** the issues are listed **Then** it is shown as "Banned officially, allowed by <playgroup>".
- **Given** a companion with a `companion_condition_unverified` warning **When** the issues are listed **Then** the condition text is shown with "verify yourself".
- **Given** validation marked stale **When** the page opens **Then** a "re-checking…" state is shown until fresh results arrive.

**Tasks**
- [ ] T-4.5.7.1 (shared) Issue-code → display text map in `packages/api-client`
- [ ] T-4.5.7.2 (web) Validation badge, issue list and playgroup selector
- [ ] T-4.5.7.3 (mobile) Validation badge, issue list and playgroup selector
- [ ] T-4.5.7.4 (web, mobile) Component tests for each status

#### S-4.5.8 — Automatic companion condition checks

**As a** deck owner with a companion **I want** the app to check its deckbuilding condition for me where it can **so that** I only have to verify the conditions a computer can't judge.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api | 1 | S-4.5.6 |

**Acceptance criteria**
- **Given** a companion whose condition can be checked from Scryfall data (mana value parity or limits, mana-cost symbols, card types, creature types, unique names) **When** validation runs **Then** the condition is evaluated over the starting deck (commander and main boards) and a deck that meets it gets no companion-condition issue (decision E5).
- **Given** a deck that fails a checkable condition **When** validation runs **Then** `companion_condition_failed` (error) is raised, naming the companion and listing the cards that break the condition.
- **Given** a companion whose condition needs rules judgement (for example "has an activated ability") or has no registered check **When** validation runs **Then** the S-4.5.6 "verify yourself" warning is used instead.
- **Given** a modal double-faced or split card in the deck **When** a mana-value condition is checked **Then** the same mana value rule as S-4.6.1 is used (front face).

**Tasks**
- [ ] T-4.5.8.1 (api) Companion-condition registry keyed by the condition sentence in the companion's oracle text (no hard-coded card names), each entry a pure predicate over Scryfall fields
- [ ] T-4.5.8.2 (api) Predicates for every condition that is checkable from card data
- [ ] T-4.5.8.3 (api) Pass and fail tests per predicate with factory-built cards that mirror real oracle text, plus the fallback-to-warning case

## F-4.6 — Deck views (curve, colors)

Per-version statistics such as the mana curve, type breakdown and color pip and source counts, shown as charts on web (Recharts) and on mobile (victory-native, decision A16). The mobile charts are built on the shared victory-native chart kit S-7.2.8, which is scheduled in Phase 1 for this reason.

**Phase:** 1 · **Priority:** Should

#### S-4.6.1 — Deck statistics endpoint

**As a** deck owner **I want** my deck's mana curve and color breakdown calculated **so that** I can see if the deck is balanced.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api | 1 | S-4.4.1, F-3.1 |

**Acceptance criteria**
- **Given** a deck version **When** the client calls `GET /decks/{id}/versions/{n}/stats` **Then** the response has the count of non-land cards per mana value 0, 1, … 6 and 7+, and the average mana value of non-land cards, rounded to 2 decimals.
- **Given** the same request **Then** it also returns card counts by card type (a card with two types counts in both) and the number of colored pips per color across non-land cards.
- **Given** lands in the deck **When** stats are computed **Then** the response counts how many lands can produce each color, based on Scryfall `produced_mana`.
- **Given** a modal double-faced card **When** its mana value is counted **Then** the front face's mana value is used.

**Tasks**
- [ ] T-4.6.1.1 (api) `decks/services/stats.py`: pure function from a version's cards to a stats object
- [ ] T-4.6.1.2 (api) Endpoint and Pydantic schema
- [ ] T-4.6.1.3 (api) Tests with hand-built decks whose expected numbers are worked out by hand

#### S-4.6.2 — Curve and color charts (web)

**As a** deck owner on the web **I want** the curve and colors shown as charts **so that** I can read them at a glance.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | web | 1 | S-4.6.1, S-4.4.4 |

**Acceptance criteria**
- **Given** a deck detail page **When** the Stats tab opens **Then** it shows a mana-curve bar chart, a type breakdown and a pip-vs-source comparison per color, drawn with Recharts.
- **Given** a bar in the curve chart **When** the user hovers or focuses it **Then** a tooltip lists the count and the card names at that mana value.
- **Given** a screen-reader user **When** they reach a chart **Then** an equivalent data table is available.

**Tasks**
- [ ] T-4.6.2.1 (web) Stats tab with Recharts curve, type and color charts
- [ ] T-4.6.2.2 (web) Accessible table fallback and Vitest tests

#### S-4.6.3 — Curve and color charts (mobile)

**As a** deck owner on my phone **I want** the same curve and color charts **so that** I can check a deck away from my computer.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | mobile | 1 | S-4.6.1, S-4.4.5, S-7.2.8 |

**Acceptance criteria**
- **Given** a deck detail screen **When** the Stats section opens **Then** the mana-curve bar chart, type breakdown and color pips vs. sources are drawn with the shared victory-native chart kit from S-7.2.8 (decision A16), not with chart code of their own.
- **Given** a curve bar **When** the user taps it **Then** the card names at that mana value are listed.
- **Given** a screen-reader user **When** they reach a chart **Then** the same numbers are available as an accessible list.

**Tasks**
- [ ] T-4.6.3.1 (mobile) Curve, type and color charts built on the S-7.2.8 bar and stacked-bar components
- [ ] T-4.6.3.2 (mobile) Tap-to-list interaction and Jest tests
- [ ] T-4.6.3.3 (mobile) Accessible list fallback for each chart, using the kit's "Show as list" toggle

## F-4.7 — Drag-and-drop sorting board

A board where owners drag cards between piles (board categories, custom piles, maybe and cut) to plan changes, on web with dnd-kit and on mobile with Gesture Handler + Reanimated. Every drag has a tap and keyboard alternative. Changes are saved as a new deck version only when the owner chooses to. The draft syncs live across all of the owner's open devices over WebSocket (decision E13, S-4.7.6): a move on the laptop shows up on the phone without a reload, and the revision check stays only as a guard against true races.

**Phase:** 2 · **Priority:** Should

#### S-4.7.1 — Sorting-board draft model and API

**As a** deck owner **I want** my sorting work kept as a draft until I save it **so that** moving cards around doesn't create dozens of versions.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api | 2 | S-4.4.3 |

**Acceptance criteria**
- **Given** a deck **When** the owner opens the sorting board **Then** a draft is created from the current version (or the existing draft is returned), with one pile per board and per category, plus fixed "Maybe" and "Cut" piles.
- **Given** a move request (card, from pile, to pile, position) **When** it is applied **Then** the draft is updated and the response returns a draft revision number; a request with an old revision number is rejected with 409 and the current draft.
- **Given** the owner opens the board on a second device **When** a draft already exists **Then** the same draft (one per deck) is returned, never a second one.
- **Given** a draft **When** the owner saves it **Then** one new DeckVersion is created: cards in "Cut" are removed, cards in "Maybe" go to the maybe board, and custom piles become categories.
- **Given** a draft **When** the owner discards it **Then** it is deleted and the current version is unchanged.
- **Given** any applied change (move, pile change, save or discard) **When** it is committed **Then** the drafts service publishes it for live sync (S-4.7.6) after the commit, explicitly from the service (no signals).

**Tasks**
- [ ] T-4.7.1.1 (api) `DeckDraft` model and migration storing piles, order and revision, unique per deck
- [ ] T-4.7.1.2 (api) Draft open, move, save and discard endpoints with optimistic concurrency
- [ ] T-4.7.1.3 (api) Tests for the save mapping (cut, maybe, custom piles), the second-device open and the 409 path
- [ ] T-4.7.1.4 (api) Post-commit publish hook in the drafts service that S-4.7.6 relays

#### S-4.7.2 — Sorting board (web)

**As a** deck owner on the web **I want** to drag cards between piles **so that** I can sort and plan cuts visually.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | web | 2 | S-4.7.1, S-4.7.6, F-3.3 |

**Acceptance criteria**
- **Given** the sorting board **When** the user drags a card from "Ramp" to "Cut" **Then** it moves immediately and the move is sent to the API; on a 409 (a race with another device) the board applies the current draft returned with it, without a full reload.
- **Given** the same board open on the owner's phone **When** a card is moved there **Then** the web board shows the move within 1 s without a reload (decision E13), and a card the user is dragging at that moment is not yanked away until they drop it.
- **Given** a keyboard-only user **When** they focus a card, press Space, use the arrow keys to choose a pile and press Space again **Then** the card moves exactly as with a drag (dnd-kit keyboard sensor).
- **Given** a mouse or touch user who prefers not to drag **When** they open a card's "Move to…" menu and pick a pile **Then** the card moves the same way.
- **Given** a board with 100+ cards in 10 piles **When** the user drags a card **Then** the drag preview follows the pointer without visible lag and piles auto-scroll near the edge.

**Tasks**
- [ ] T-4.7.2.1 (web) Board layout with pile columns and card stacks using dnd-kit (pointer and keyboard sensors)
- [ ] T-4.7.2.2 (web) "Move to…" menu as the non-drag alternative
- [ ] T-4.7.2.3 (web) Optimistic moves with TanStack Query and 409 reconciliation
- [ ] T-4.7.2.4 (web) Vitest tests for keyboard and menu moves
- [ ] T-4.7.2.5 (web) Bind the board to the S-4.7.6 live-sync hook, deferring remote moves of the card being dragged until drop

#### S-4.7.3 — Sorting board (mobile)

**As a** deck owner on my phone **I want** to long-press and drag cards between piles **so that** I can sort a deck on the couch.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 8 | mobile | 2 | S-4.7.1, S-4.7.6, F-3.3 |

**Acceptance criteria**
- **Given** the sorting board **When** the user long-presses a card for about 300 ms and drags it onto another pile **Then** the card moves, with a haptic tick on pick-up and drop, and the move is sent to the API.
- **Given** a drag near the left or right screen edge **When** the card is held there **Then** the horizontally scrolling pile list auto-scrolls so off-screen piles can be reached.
- **Given** a user who doesn't want to drag **When** they tap a card and choose "Move to…" from the action sheet **Then** the card moves the same way.
- **Given** a 409 from the API (a race with another device) **When** it arrives **Then** the board applies the current draft returned with it and briefly says "updated from another device".
- **Given** the same board open on the owner's computer **When** a card is moved there **Then** the phone board shows the move within 1 s without a reload (decision E13), and a card the user is holding is not moved until they drop it.
- **Given** the app returns from the background **When** the board is shown again **Then** it reconnects and catches up before accepting new drags.

**Tasks**
- [ ] T-4.7.3.1 (mobile) Pile columns in a horizontal scroll view; drag with Gesture Handler and Reanimated shared values
- [ ] T-4.7.3.2 (mobile) Drop-target hit testing and edge auto-scroll
- [ ] T-4.7.3.3 (mobile) "Move to…" action sheet and haptics (expo-haptics)
- [ ] T-4.7.3.4 (mobile) Jest tests for the action-sheet move path
- [ ] T-4.7.3.5 (mobile) Bind the board to the S-4.7.6 live-sync hook, with reconnect on app foreground

#### S-4.7.4 — Manage custom piles

**As a** deck owner **I want** to create, rename, reorder and delete piles **so that** the board matches how I think about my deck.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 3 | api, web, mobile | 2 | S-4.7.2, S-4.7.3 |

**Acceptance criteria**
- **Given** the sorting board **When** the owner adds a pile named "Wincons" **Then** an empty pile appears and is saved in the draft.
- **Given** a pile with cards **When** the owner deletes it **Then** they are asked where its cards should go (default: "Uncategorised") and nothing is lost.
- **Given** the board **When** the owner reorders piles by dragging a pile header, or by using "Move left" / "Move right" from the pile menu **Then** the new order is saved in the draft.
- **Given** the fixed "Maybe" and "Cut" piles **When** the owner opens their menu **Then** rename and delete are not offered.

**Tasks**
- [ ] T-4.7.4.1 (api) Pile create, rename, reorder and delete on the draft
- [ ] T-4.7.4.2 (web) Pile menu and draggable pile headers with keyboard support
- [ ] T-4.7.4.3 (mobile) Pile menu with move left/right and reorder

#### S-4.7.5 — Review the impact before saving

**As a** deck owner **I want** to see the card count, legality and bracket of my draft before saving **so that** I don't save a broken deck by accident.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, web, mobile | 2 | S-4.7.1, S-4.5.1, S-5.3.2 |

**Acceptance criteria**
- **Given** a draft **When** the owner opens "Review & save" **Then** they see the diff against the current version, the deck size, the validation status and the minimum bracket computed from the draft.
- **Given** a draft that would be illegal **When** the owner saves **Then** the save is allowed but needs an extra confirmation naming the errors.

**Tasks**
- [ ] T-4.7.5.1 (api) `POST /decks/{id}/draft/preview` running validation and the bracket engine on the draft without saving
- [ ] T-4.7.5.2 (web) Review dialog
- [ ] T-4.7.5.3 (mobile) Review screen

#### S-4.7.6 — Live draft sync across the owner's devices

**As a** deck owner with the board open on my computer and my phone **I want** every change to show up on all my devices at once **so that** I can sort on whichever screen is at hand without reloading or losing work (decision E13).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api, shared | 2 | S-4.7.1, S-6.3.2, S-6.3.4 |

**Acceptance criteria**
- **Given** a client connects to `/ws/decks/{id}/draft` **When** it sends `{type: "auth", token}` with the deck owner's valid JWT within 5 s **Then** it receives `welcome` with the full draft and its revision; any other user gets close code 4403, an unknown deck 4404 and a missing or invalid token 4401, following the S-6.3.2 handshake.
- **Given** two of the owner's devices connected **When** a move, pile change (S-4.7.4), save or discard is committed from either one, through REST or the socket **Then** the other device receives a `draft_changed` message with the change and the new revision within 1 s (p95, local Compose), published only after the database commit.
- **Given** a client whose last revision is 12 **When** it receives revision 14 or reconnects after being offline **Then** it sends `sync {since: 12}` and gets the full current draft; no change is lost or applied twice.
- **Given** the draft is saved or discarded on one device **When** the others receive the message **Then** they leave the board and show "Saved as version N on another device" or "Draft discarded on another device".
- **Given** a playgroup member who can view the deck **When** they try to connect **Then** they are refused with 4403; the board is owner-only.

**Tasks**
- [ ] T-4.7.6.1 (api) Draft WebSocket endpoint reusing the S-6.3.2 auth handshake, heartbeat and close codes, restricted to the deck owner via `can_edit_deck`
- [ ] T-4.7.6.2 (api) Publish to `deck_draft:{id}` after commit and relay to local sockets with the S-6.3.4 Redis pub/sub relay
- [ ] T-4.7.6.3 (shared) `useDraftSync` hook in `packages/api-client` (connect, auth, revision tracking, gap resync, reconnect with backoff) used by web and mobile
- [ ] T-4.7.6.4 (api, shared) Tests: two clients see each other's moves, gap resync, save and discard messages, and non-owner refusal

## Open questions

None open. Resolved: see OPEN-QUESTIONS.md decisions log.
