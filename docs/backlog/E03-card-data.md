# E-3 — Card Data

| | |
|---|---|
| **Phase(s)** | 1 (Scryfall sync, card search and lookup, card UI components) · 3 (price snapshots and their retention, on-demand foreign-language printings) |
| **Goal** | Keep a complete local copy of Scryfall card data (cards, tokens, emblems, art-series and oversized cards, printings, legalities and the `game_changer` flag), refreshed daily from bulk files, so decks, validation, brackets and collections never call Scryfall during a user request. Card images are hotlinked from Scryfall. From Phase 3, store the latest price (usd, usd_foil, usd_etched, eur, eur_foil) on every printing, refreshed by the daily sync so card search and deck building can show prices, and keep daily price history only for the printings people actually own or play, and fetch foreign-language printings on demand when a collection needs them. |
| **Success measures** | - The daily sync of `oracle_cards` and `default_cards` finishes in under 15 minutes, and the worker's peak memory stays under 512 MB.<br>- Running the sync twice on the same bulk files changes 0 rows the second time (idempotent).<br>- Card name autocomplete answers in under 150 ms at p95 on the full dataset.<br>- Phase 3: after each daily sync, every printing in `default_cards` carries that file's latest prices.<br>- Phase 3: exactly one price snapshot per tracked printing per day for the last 2 years, at most one per ISO week before that, and none for untracked printings.<br>- Phase 3: a foreign-language printing named in a collection import is stored locally after one Scryfall lookup and never fetched again within 30 days. |
| **Out of scope** | The `all_cards` bulk file (decision G9: foreign-language printings are looked up on demand instead, S-3.2.5) and the `rulings` bulk file, card rulings UI, a card-browsing or deck-building catalog, re-hosting or caching card images, price sources other than Scryfall, `tix` prices (G2), price history for untracked printings, price alerts, deck legality logic (F-4.5), bracket rules and the Game Changer comparison (E-5), choosing which printing a deck card uses (the copy assigned to it, E-9, decision G4). |

## F-3.1 — Scryfall bulk sync

A background job downloads Scryfall's `oracle_cards` and `default_cards` bulk files once a day at 09:00 UTC, but only when they have changed. It reads them as a stream with `ijson`, in batches, without loading a whole file into memory, and loads them into Card (keyed by `oracle_id`) and Printing (keyed by Scryfall `id`) tables, including legalities and the `game_changer` flag. Tokens, emblems, art-series and oversized cards are stored too. All Scryfall calls follow Scryfall's API guidelines and send the User-Agent `MTGStats/<version> (+<repo URL>)`.

Phase 1 · Priority: Must

#### S-3.1.1 — Card and printing schema

**As a** developer **I want** Card and Printing tables that match Scryfall's data model **so that** every other domain can refer to cards by `oracle_id` and to printings by Scryfall id.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | F-1.4 |

**Acceptance criteria**
- **Given** the migration **When** it is applied **Then** `cards` has primary key `oracle_id` (UUID) and these columns: name, normalized search name, mana_cost, cmc, type_line, oracle_text, colors, color_identity (array of WUBRG letters), keywords, layout, card_faces (JSONB), legalities (JSONB), an indexed `commander_legality`, `game_changer` (boolean, false when Scryfall omits it), `oversized`, `is_extra`, `default_printing_id`, `reserved`, `missing_since` and `updated_at`.
- **Given** the migration **When** it is applied **Then** `printings` has primary key Scryfall `id` (UUID), a foreign key `oracle_id` to `cards`, and these columns: set_code, set_name, collector_number, rarity, lang, released_at, finishes (array), image_uris (JSONB, per face for double-faced cards), digital, oversized and `updated_at`, with a unique index on (set_code, collector_number, lang).
- **Given** a card whose layout is `art_series` or which Scryfall marks `oversized` **When** it is stored **Then** `is_extra = true`, which keeps it out of default search (S-3.2.2). Every other card, including tokens and emblems, has `is_extra = false`.
- **Given** Scryfall legality values **When** they are stored **Then** they are kept exactly as Scryfall sends them (`legal`, `not_legal`, `banned`, `restricted`), with no translation.
- **Given** the migration **When** it is upgraded and then downgraded on an empty database **Then** both directions succeed, and the Alembic drift check passes.
- **Given** another domain needs card data **When** it imports from `cards` **Then** it can use only `cards/services`, never the models directly.

**Tasks**
- [ ] T-3.1.1.1 (api) `Card` and `Printing` SQLAlchemy models and Alembic migration
- [ ] T-3.1.1.2 (api) Enable `pg_trgm` and `unaccent` in the migration (`CREATE EXTENSION IF NOT EXISTS`), add an `IMMUTABLE` wrapper around `unaccent` for index expressions, and build the normalized name column (lower-case, accents removed, punctuation folded) and its trigram index
- [ ] T-3.1.1.3 (api) Public `cards/services/__init__.py` with typed read functions
- [ ] T-3.1.1.4 (api) Card and Printing factories (including double-faced, split, adventure, token, emblem, art-series and oversized layouts)

#### S-3.1.2 — Scryfall HTTP client that follows the API guidelines

**As an** operator **I want** every call to Scryfall to follow their published guidelines **so that** our access is never throttled or blocked.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | F-1.4, F-1.3 |

**Acceptance criteria**
- **Given** any request to `api.scryfall.com` or `data.scryfall.io` **When** it is sent **Then** it carries the header `User-Agent: MTGStats/<version> (+<repo URL>)`, with the version taken from the API package and the repo URL from settings, and no personal email. It also carries `Accept: application/json` (or `*/*` for bulk file downloads).
- **Given** several worker coroutines or processes **When** they call `api.scryfall.com` **Then** the calls are spaced at least 100 ms apart (no more than 10 per second), using a limiter shared through Redis.
- **Given** a 429 response **When** the client handles it **Then** it waits for the `Retry-After` time (60 s if the header is missing) and retries at most 3 times. A 5xx response is retried 3 times with exponential backoff starting at 2 s, and after that the error reaches the caller.
- **Given** an API route that handles a user request **When** it needs card data **Then** it reads the local database and never calls Scryfall. An architecture test enforces this.

**Tasks**
- [ ] T-3.1.2.1 (api) `ScryfallClient` built on an `httpx.AsyncClient`, with the default headers and timeouts
- [ ] T-3.1.2.2 (api) Redis rate limiter for the `api.scryfall.com` host
- [ ] T-3.1.2.3 (api) Retry and backoff policy
- [ ] T-3.1.2.4 (api) Tests with `httpx.MockTransport`: headers (including the exact User-Agent format), spacing, 429 and 5xx handling
- [ ] T-3.1.2.5 (api) Architecture test checking that no module under `*/router.py` imports `ScryfallClient`

#### S-3.1.3 — Download bulk files only when they change

**As an** operator **I want** the sync to download a bulk file only when Scryfall has published a new one **so that** we do not waste bandwidth or put needless load on Scryfall.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-3.1.2 |

**Acceptance criteria**
- **Given** `GET /bulk-data` **When** the sync starts **Then** it reads the `oracle_cards` and `default_cards` entries (`download_uri`, `updated_at`, `size`).
- **Given** an `updated_at` that matches the last successful run for that file **When** the sync runs without `--force` **Then** that file is skipped with status `unchanged` and is not downloaded.
- **Given** a file of any size **When** it downloads **Then** it is streamed to a temporary file in 1 MB chunks, and the download step's memory stays under 50 MB. The temporary file is deleted after both success and failure.
- **Given** the download is interrupted **When** the run fails **Then** the existing card data is untouched and the run is recorded as `failed`.

**Tasks**
- [ ] T-3.1.3.1 (api) `CardSyncRun` model (file kind, bulk_updated_at, started_at, finished_at, status, counts JSONB, error) and migration
- [ ] T-3.1.3.2 (api) Bulk index fetch and the change check
- [ ] T-3.1.3.3 (api) Streaming download to a temporary file, with cleanup
- [ ] T-3.1.3.4 (api) Tests for the unchanged, changed and interrupted cases

#### S-3.1.4 — Read bulk JSON as a stream, in batches

**As an** operator **I want** bulk files read one card at a time and loaded in batches **so that** files of hundreds of megabytes never sit in memory and readers never see half-loaded data.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-3.1.3 |

**Acceptance criteria**
- **Given** a JSON-array bulk file of about 500 MB **When** it is parsed with `ijson` (`ijson.items(file, "item")`, using the C backend when it is available) **Then** card objects come out one at a time, and the parse step's peak memory stays under 200 MB.
- **Given** the parsed objects **When** they are grouped **Then** batches of 1,000 objects (a setting) are written to staging tables, and all changes move into `cards` and `printings` in one final transaction.
- **Given** the run fails partway through, for example on a malformed object **When** the failure happens **Then** `cards` and `printings` are unchanged, and the run records the error with the index of the object where it happened.
- **Given** numbers in the file **When** `ijson` yields them **Then** prices and `cmc` arrive as `Decimal`, never `float`.

**Tasks**
- [ ] T-3.1.4.1 (api) Bulk reader (a generator) built on `ijson.items`
- [ ] T-3.1.4.2 (api) Staging tables and a batched insert writer
- [ ] T-3.1.4.3 (api) Merge step (`INSERT … ON CONFLICT DO UPDATE` from the staging tables, run in one transaction)
- [ ] T-3.1.4.4 (api) Tests: Unicode escapes, a malformed object, Decimal handling, and a memory check on a generated 200 MB file

#### S-3.1.5 — Sync oracle cards and legalities

**As the** deck validation and bracket services **I want** an up-to-date Card row, legalities and Game Changer flag for every oracle card **so that** legality checks use the current banned list and E-5 can cross-check its rule set.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-3.1.1, S-3.1.4 |

**Acceptance criteria**
- **Given** the `oracle_cards` file **When** the sync runs **Then** there is exactly one Card per `oracle_id`. A second run on the same file reports 0 inserted and 0 updated.
- **Given** a `reversible_card` entry that has no top-level `oracle_id` **When** it is loaded **Then** the `oracle_id` and face data come from `card_faces[0]`.
- **Given** entries with the layouts `token`, `double_faced_token`, `emblem` or `art_series`, or entries marked `oversized` **When** the sync runs **Then** they are stored like any other card, `is_extra` is set as described in S-3.1.1, and the run summary counts cards per layout.
- **Given** a card whose `legalities.commander` changes between runs (for example, newly banned) **When** the sync finishes **Then** its `oracle_id` is listed in the run's `commander_legality_changed`, which `cards/services.legality_changes_since(run_id)` exposes so F-4.5 can re-validate affected decks.
- **Given** Scryfall's `game_changer` field **When** a card is stored **Then** `cards.game_changer` holds its value. A card whose flag changes between runs is listed in the run's `game_changer_changed`. `cards/services.game_changer_oracle_ids()` returns the current flagged set so E-5 can compare it with the active rule set and warn the site admin. E-3 does not compare.
- **Given** a card that is no longer in the file **When** the sync finishes **Then** the card is kept, because decks may refer to it, and its `missing_since` is set.

**Tasks**
- [ ] T-3.1.5.1 (api) Mapping from Scryfall objects to Card rows, covering every layout, the `is_extra` rule and `game_changer`
- [ ] T-3.1.5.2 (api) Detect legality and `game_changer` changes during the merge step
- [ ] T-3.1.5.3 (api) `legality_changes_since` and `game_changer_oracle_ids` in the public services interface
- [ ] T-3.1.5.4 (api) Tests with sample entries for each layout (including token, emblem, art-series and oversized), a ban change and a `game_changer` change between two runs

#### S-3.1.6 — Sync printings from default cards

**As a** developer **I want** every English-or-only-language printing loaded **so that** imports, images and collections can point to a specific printing.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-3.1.5 |

**Acceptance criteria**
- **Given** the `default_cards` file **When** the sync runs after the oracle step **Then** there is one Printing per Scryfall `id`, linked to its Card, including token, emblem, art-series and oversized printings. A printing whose `oracle_id` is not in `cards` is skipped and counted, and a non-zero count is logged at WARNING.
- **Given** a double-faced printing **When** it is stored **Then** `image_uris` holds one set of URIs per face. Single-faced printings hold one set.
- **Given** the oracle step has run **When** printings are merged **Then** each Card's `default_printing_id` is set to the printing that its `oracle_cards` entry pointed to (Scryfall's default printing).
- **Given** a dev machine running Docker Compose with Postgres 16 **When** a full sync of both files runs **Then** it finishes in under 15 minutes. A second run on the same files changes 0 printings.

**Tasks**
- [ ] T-3.1.6.1 (api) Mapping from Scryfall objects to Printing rows, including per-face images and `oversized`
- [ ] T-3.1.6.2 (api) Step that sets `default_printing_id` after the merge
- [ ] T-3.1.6.3 (api) Sync orchestrator that runs oracle first, then default
- [ ] T-3.1.6.4 (api) Tests for linking, skipped printings and idempotency

#### S-3.1.7 — Schedule the daily sync and record runs

**As an** operator **I want** the sync to run every day by itself, never twice at once, and to be triggerable by hand **so that** card data stays fresh without anyone watching it.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, infra | 1 | S-3.1.6, F-1.9 |

**Acceptance criteria**
- **Given** the worker is running **When** 09:00 UTC arrives (the time is a setting) **Then** the sync job is queued through an arq cron.
- **Given** a sync already running **When** a second one is triggered **Then** it is skipped and logged, because a Redis lock with a 2-hour TTL is held.
- **Given** an operator **When** they run `uv run python -m app.cli sync-cards [--force]` **Then** the job is queued, and `--force` downloads the files even if they are unchanged.
- **Given** a run fails **When** the job handles the failure **Then** it retries once after 1 hour. Two failures in a row are logged at ERROR level.
- **Given** a site admin (S-2.1.11) **When** they call `GET /admin/card-sync/runs` **Then** they get the last 30 runs with status, duration and counts. Everyone else gets 403.

**Tasks**
- [ ] T-3.1.7.1 (api) arq cron registration and the Redis lock
- [ ] T-3.1.7.2 (api) `sync-cards` CLI command
- [ ] T-3.1.7.3 (api) Admin runs endpoint, protected by `require_site_admin`
- [ ] T-3.1.7.4 (infra) Worker service settings in `docker-compose.yml` (temp directory volume, repo URL environment variable for the User-Agent)

#### S-3.1.8 — Small sample card set for dev and tests

**As a** developer **I want** to load a small, realistic card set without downloading hundreds of megabytes **so that** local setup and the test suite are fast and work offline.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | api | 1 | S-3.1.6 |

**Acceptance criteria**
- **Given** a fresh dev database **When** the developer runs `sync-cards --sample` **Then** about 300 cards and their printings load through the same parser and merge path in under 10 seconds.
- **Given** the sample **When** it is inspected **Then** it includes at least one card of each kind used by later epics: double-faced, split, adventure, reversible, partner, background, a Commander-banned card, a Game Changer, Relentless Rats, a token, an emblem, an art-series card, an oversized card, and cards in all five colors plus colorless.
- **Given** the test suite **When** it runs **Then** it makes no network calls to Scryfall.

**Tasks**
- [ ] T-3.1.8.1 (api) Sample bulk files in Scryfall format, used as parser input rather than as database fixtures, plus a script that rebuilds them
- [ ] T-3.1.8.2 (api) `--sample` option on the sync command
- [ ] T-3.1.8.3 (api) pytest setting that blocks outgoing HTTP

## F-3.2 — Card search & lookup API

Read-only endpoints and service functions over the local card tables: lookup by id, name autocomplete, batch name resolution for imports, and filtered search. Art-series and oversized cards (`is_extra`) are left out of default search. From Phase 3, a worker-only service fetches foreign-language printings from Scryfall on demand and caches them in `printings` (decision G9).

Phase 1 · Priority: Must

#### S-3.2.1 — Look up a card and its printings

**As a** client app **I want** to fetch a card, its printings or a single printing by id **so that** I can show card details anywhere.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-3.1.6, F-1.4 |

**Acceptance criteria**
- **Given** a known `oracle_id` **When** `GET /cards/{oracle_id}` is called **Then** it returns the card with faces, color identity, legalities, `game_changer` and the default printing's images. An unknown id returns 404.
- **Given** a card with 60 printings **When** `GET /cards/{oracle_id}/printings` is called **Then** it returns 50 per page, newest release first, with a cursor for the next page.
- **Given** a Scryfall printing id **When** `GET /printings/{id}` is called **Then** it returns the printing and its card.
- **Given** a request with no token **When** any of these endpoints is called **Then** it returns 401. Responses carry `Cache-Control: private, max-age=3600`.

**Tasks**
- [ ] T-3.2.1.1 (api) Repository queries and service functions (`get_card`, `get_cards`, `get_printing`, `list_printings`)
- [ ] T-3.2.1.2 (api) Routes and response schemas
- [ ] T-3.2.1.3 (api) Tests

#### S-3.2.2 — Card name autocomplete

**As a** user typing a card name **I want** fast, forgiving suggestions **so that** I find the card even with accents, partial names or typos.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-3.2.1 |

**Acceptance criteria**
- **Given** `GET /cards/search?q=&limit=` **When** `q` has fewer than 2 characters **Then** the response is 422. The default `limit` is 10 and the maximum is 25.
- **Given** `q=sol` **When** the search runs **Then** prefix matches rank above substring matches, so "Sol Ring" ranks above "Consoling…".
- **Given** `q=lim dul` or `q=sol rng` **When** the search runs **Then** "Lim-Dûl's Vault" and "Sol Ring" respectively appear in the top 5 (accents ignored through `unaccent`, typos tolerated through `pg_trgm`).
- **Given** a double-faced, split or adventure card **When** someone searches for either face's name **Then** the card is returned, so "Insectile Aberration" finds Delver of Secrets.
- **Given** art-series and oversized cards **When** the default search runs **Then** they are left out. With `include_extras=true` they are included. Tokens and emblems are always searchable and each result carries its `layout`.
- **Given** the full dataset and 20 concurrent requests **When** the search is load-tested **Then** p95 latency is under 150 ms.

**Tasks**
- [ ] T-3.2.2.1 (api) Face-name search column or table, filled in during sync
- [ ] T-3.2.2.2 (api) Ranking query (prefix, then trigram similarity) and its GIN trigram indexes, with the `is_extra` filter
- [ ] T-3.2.2.3 (api) Route, tests and a small latency benchmark script

#### S-3.2.3 — Resolve a list of card names

**As the** deck and collection import services (F-4.1, F-9.2) **I want** to resolve hundreds of names in one call **so that** imports are fast and report exactly which lines they could not match.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 1 | S-3.2.2 |

**Acceptance criteria**
- **Given** up to 250 entries `{name, set_code?, collector_number?}` **When** `resolve_names` (or `POST /cards/resolve`) is called **Then** results come back in the same order as the input, each marked `resolved` (with `oracle_id` and an optional `printing_id`) or `unresolved` (with up to 3 suggestions).
- **Given** "Fire // Ice", "Fire" or "fire // ice" **When** they are resolved **Then** all three return the same card.
- **Given** a name that matches both a regular card and a token, emblem, art-series or oversized card **When** it is resolved without a set code **Then** the regular card wins. A set code and collector number still pick the exact printing.
- **Given** a set code and collector number that match a printing **When** the entry is resolved **Then** that printing is returned. If they do not match, the card is still resolved by name, with the flag `printing_not_found`.
- **Given** 250 entries **When** they are resolved **Then** the call uses no more than 3 database queries, no matter how many names there are.
- **Given** more than 250 entries **When** the endpoint is called **Then** it returns 422.

**Tasks**
- [ ] T-3.2.3.1 (api) Name normalizer shared with the search column (one function used at both write and read time)
- [ ] T-3.2.3.2 (api) Batched exact, face-name and printing lookups, plus fuzzy suggestions for names left over
- [ ] T-3.2.3.3 (api) `resolve_names` in the public services interface, and the route
- [ ] T-3.2.3.4 (api) Tests, including split, adventure and double-faced names, accents, a name shared with a token, a query-count assertion and order preservation

#### S-3.2.4 — Filtered card search

**As a** user looking for cards **I want** to filter by color identity, type, mana value and Commander legality **so that** I can find candidates such as possible commanders or cards to house-ban.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api | 1 | S-3.2.2 |

**Acceptance criteria**
- **Given** `color_identity=WU` **When** the search runs **Then** only cards whose color identity is a subset of {W, U} are returned, colorless cards included. Letters outside WUBRGC return 422.
- **Given** `type=legendary creature`, `cmc_min=2`, `cmc_max=4` and `commander_legality=legal` **When** they are combined with an optional `q` **Then** all of the filters apply together.
- **Given** no `include_extras` parameter **When** the search runs **Then** art-series and oversized cards are left out, as in S-3.2.2.
- **Given** a result set of more than 50 cards **When** results are returned **Then** they are paginated 50 per page with a cursor, sorted by name.

**Tasks**
- [ ] T-3.2.4.1 (api) Filter query builder in the cards repository (array containment for color identity, the `is_extra` filter)
- [ ] T-3.2.4.2 (api) Query parameter schema and route
- [ ] T-3.2.4.3 (api) Tests for each filter and for combinations

#### S-3.2.5 — Look up foreign-language printings on demand

**As the** collection import and quick add (F-9.1, F-9.2) **I want** a foreign-language printing fetched from Scryfall the first time it is needed and then kept locally **so that** collections can record the exact printing someone owns without loading the 2 GB `all_cards` file (decision G9).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | S-3.1.2, S-3.1.6 |

**Acceptance criteria**
- **Given** up to 500 Scryfall printing ids that are not in `printings` **When** `ensure_printings(ids)` is called **Then** they are fetched through the rate-limited client (S-3.1.2) with `POST /cards/collection` in groups of up to 75, stored in `printings` with their `lang`, `source = on_demand` and `fetched_at`, linked to the existing Card by `oracle_id`, and returned. Ids already stored are returned without any Scryfall call.
- **Given** a set code, collector number and language (for example `neo`, `123`, `ja`) with no matching row **When** `ensure_printing_by_number(set_code, collector_number, lang)` is called **Then** it fetches `GET /cards/{set}/{number}/{lang}` once and stores the result the same way.
- **Given** an id or set/number/language that Scryfall answers with 404 **When** it is looked up **Then** the result is `not_found`, and that answer is cached for 7 days so a re-import does not ask again.
- **Given** an on-demand row fetched more than 30 days ago **When** `ensure_printings` sees it again **Then** it is re-fetched and updated. If Scryfall fails, the old row is kept and returned. The daily bulk sync (S-3.1.6) never deletes or overwrites `on_demand` rows, because they are not in `default_cards`.
- **Given** a foreign-language printing **When** it is stored **Then** its `price_printing_id` points to the `default_cards` printing with the same set code and collector number, because language does not change value. Price tracking (S-3.4.2) tracks that printing instead, and `latest_prices` for the foreign id returns its prices.
- **Given** a printing whose `oracle_id` is not in `cards` **When** it comes back from Scryfall **Then** it is skipped and reported as `unknown_card` rather than creating a Card.
- **Given** any API route that handles a user request **When** it needs a foreign-language printing **Then** it never calls these functions directly: they run only in worker jobs (the F-9.2 import job, or a lookup job queued by quick add), and the S-3.1.2 architecture test covers them.

**Tasks**
- [ ] T-3.2.5.1 (api) `source`, `fetched_at` and `price_printing_id` columns on `printings` and a `printing_lookup_misses` table for cached 404s (migration)
- [ ] T-3.2.5.2 (api) `ensure_printings` and `ensure_printing_by_number` in `cards/services`, reusing the S-3.1.6 Printing mapping
- [ ] T-3.2.5.3 (api) arq job `lookup_printings` that F-9.1 and F-9.2 can queue
- [ ] T-3.2.5.4 (api) Tests with `httpx.MockTransport`: cache hit, batch of 75, 404 caching, 30-day refresh, refresh failure keeps the row, unknown card, bulk sync leaves `on_demand` rows alone

## F-3.3 — Card UI components

Reusable card components for web and mobile: the card image, name autocomplete, a preview popup, and mana and color symbols. Decks, house bans, collections, avatars and brackets all use them. Images are hotlinked from Scryfall. Shared TypeScript card helpers live in `packages/card-utils`, and mobile draws mana symbols with `react-native-svg`.

Phase 1 · Priority: Must

#### S-3.3.1 — Card image component

**As a** user **I want** card images that load quickly and never break the layout **so that** lists and previews look right on any connection.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web, mobile | 1 | S-3.2.1, F-1.6, F-1.7 |

**Acceptance criteria**
- **Given** a printing **When** the component renders at `size="normal"` or `size="small"` **Then** it shows the matching Scryfall image URI inside a box with a fixed 63:88 aspect ratio, so the layout does not shift while the image loads. `size="art_crop"` is also supported (used for avatars, S-2.2.2).
- **Given** any card image **When** it is requested **Then** the URL is the Scryfall image URI stored on the printing, loaded directly from Scryfall's image CDN. Our servers never proxy, re-host or cache image files.
- **Given** a list of 100 cards on web **When** it renders **Then** images load lazily (`loading="lazy"`), and only the images near the viewport are requested.
- **Given** an image that fails to load **When** the error happens **Then** a text fallback shows the card name, mana cost and type line.
- **Given** a double-faced card **When** the user activates the flip control by click, tap, Enter or Space **Then** the other face is shown. The control has an accessible label, and the image's alt text is the face name.
- **Given** any size other than `art_crop` **When** the image renders **Then** it is never cropped in a way that hides the artist credit or the copyright line (Scryfall image guidelines).

**Tasks**
- [ ] T-3.3.1.1 (web) `CardImage` component with lazy loading and a fallback
- [ ] T-3.3.1.2 (mobile) `CardImage` component (React Native `Image`) with a fallback
- [ ] T-3.3.1.3 (web) Vitest tests for the fallback, the flip control and the hotlinked URL

#### S-3.3.2 — Card name autocomplete input

**As a** user **I want** an input that suggests cards as I type **so that** I can add cards to decks, bans and collections without spelling them perfectly.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | web, mobile | 1 | S-3.2.2, S-3.3.4 |

**Acceptance criteria**
- **Given** the user types at least 2 characters **When** they pause for 200 ms **Then** up to 10 suggestions appear, each with its mana cost.
- **Given** quick typing that causes overlapping requests **When** the responses arrive out of order **Then** only the latest query's results are shown.
- **Given** a keyboard user on web **When** they use the input **Then** it follows the ARIA combobox pattern: the arrow keys move through suggestions, Enter selects and Escape closes.
- **Given** the API is unreachable **When** a request fails **Then** an inline "Couldn't load suggestions" message shows and the input stays usable.

**Tasks**
- [ ] T-3.3.2.1 (web) `CardAutocomplete` (TanStack Query, debounced, accessible combobox)
- [ ] T-3.3.2.2 (mobile) `CardAutocomplete` with a suggestion list under the field
- [ ] T-3.3.2.3 (web) Vitest tests for debouncing, stale responses and keyboard navigation

#### S-3.3.3 — Card preview on hover, focus or long-press

**As a** user scanning a list of card names **I want** to see a card's image without leaving the list **so that** I can recognise cards quickly.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 2 | web, mobile | 1 | S-3.3.1 |

**Acceptance criteria**
- **Given** a card name on web **When** the user hovers over it or moves keyboard focus to it **Then** the image preview appears within 150 ms, fully inside the viewport, and Escape or moving focus away closes it.
- **Given** a card name on mobile **When** the user long-presses it for 400 ms **Then** a full-screen preview opens, and a tap anywhere or the back gesture closes it.

**Tasks**
- [ ] T-3.3.3.1 (web) `CardPreview` popover that positions itself inside the viewport
- [ ] T-3.3.3.2 (mobile) `CardPreview` modal opened by a long-press (Gesture Handler)

#### S-3.3.4 — Mana cost and color identity symbols

**As a** user **I want** mana costs and color identities shown as the familiar symbols **so that** I can read cards at a glance.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile, shared | 1 | S-3.1.2, S-3.2.1 |

**Acceptance criteria**
- **Given** costs such as `{2}{W}{U}`, `{X}{R}{R}`, `{2/W}`, `{G/U/P}`, `{C}` and `{S}` **When** they render **Then** each symbol shows its Scryfall symbol image, in the order given.
- **Given** a symbol the client does not recognise **When** it renders **Then** it shows as the text in braces rather than crashing or disappearing.
- **Given** a screen reader **When** it reaches `{2}{W}{W}` **Then** it announces "2 generic, 2 white". A color identity of WUG is announced as "white, blue, green", always in WUBRG order.
- **Given** mobile **When** symbols render **Then** they are drawn from Scryfall's SVGs with `react-native-svg`, at the same size and order as on web.
- **Given** web and mobile **When** they tokenize a cost or build its label **Then** both import the same functions from `packages/card-utils`, which has its own Vitest suite.

**Tasks**
- [ ] T-3.3.4.1 (api) Sync Scryfall `/symbology` into a `Symbol` table during the card sync, and add `GET /cards/symbols`
- [ ] T-3.3.4.2 (shared) New `packages/card-utils` workspace package (package.json, tsconfig, Vitest) with the mana-cost tokenizer and screen-reader label builder, plus tests
- [ ] T-3.3.4.3 (web) `ManaCost` and `ColorIdentity` components
- [ ] T-3.3.4.4 (mobile) `ManaCost` and `ColorIdentity` components using `react-native-svg`

## F-3.4 — Price snapshots

Once a day, the `default_cards` bulk pass stores the usd, usd_foil, usd_etched, eur and eur_foil prices (no `tix`) in two places. Every printing gets its **latest price** on the printing row, so card search, card lookup and deck building can show a price for any card (decision "Latest prices", 2026-11-07). A **daily snapshot** is written only for printings that someone owns or that appear in a deck (decision G2). The snapshots build the price history that valuation and profit/loss (E-9) use; each user values things in their own currency, USD or EUR (G1), so both currencies are kept. Snapshots are kept daily for 2 years and then thinned to one per ISO week. Which printing a deck card uses is not decided here: after Phase 3 it is the copy assigned to that deck card, otherwise Scryfall's default printing (decision G4, E-9). E-3 only supplies `default_printing_id` and prices.

Phase 3 · Priority: Must

#### S-3.4.1 — Price snapshot schema

**As a** developer **I want** latest-price columns on every printing and a compact daily price table **so that** any card can show a price and price history is exact and cheap to query.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 3 | S-3.1.6 |

**Acceptance criteria**
- **Given** the migration **When** it is applied **Then** `price_snapshots` has `printing_id` (a foreign key), `snapshot_date`, and `usd`, `usd_foil`, `usd_etched`, `eur` and `eur_foil` as `numeric(10,2)`, with a unique key on (`printing_id`, `snapshot_date`). There is no `tix` column (decision G2).
- **Given** the migration **When** it is applied **Then** `printings` gains `usd`, `usd_foil`, `usd_etched`, `eur` and `eur_foil` as nullable `numeric(10,2)` plus `prices_updated_on` (a date), all NULL until the first priced sync. No `tix` column is added.
- **Given** Scryfall returns `null` for a price **When** it is stored, on the printing or in a snapshot **Then** the column is NULL, never 0.
- **Given** prices from the database to the API response **When** they are handled in Python **Then** they are `Decimal` all the way through, never `float`.
- **Given** the migration **When** it is downgraded **Then** it reverses cleanly.

**Tasks**
- [ ] T-3.4.1.1 (api) `PriceSnapshot` model, the latest-price columns on `Printing`, and the migration
- [ ] T-3.4.1.2 (api) Pydantic schemas that serialize Decimal as a string
- [ ] T-3.4.1.3 (api) Factory and tests

#### S-3.4.2 — Track only owned and in-deck printings

**As an** operator **I want** snapshot history only for printings someone cares about **so that** the price history table stays small, while every printing still carries its latest price.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 3 | S-3.4.1, F-4.4, F-9.1 |

**Acceptance criteria**
- **Given** collection items and decks **When** the tracked set is computed **Then** it is the union of printings in any CollectionItem with quantity greater than 0 and printings listed in any current DeckVersion. A foreign-language printing (S-3.2.5) is replaced by its `price_printing_id`.
- **Given** a deck card that has a collection copy assigned to it (decision G4) **When** the tracked set is computed **Then** that copy's printing is tracked (it is already a collection item). **Given** a deck card with no assigned copy and no listed printing **When** the tracked set is computed **Then** the card's `default_printing_id` is added. No price is needed to work out the set.
- **Given** a collection item marked as a proxy (G4: tracked but not owned) **When** the tracked set is computed **Then** it adds nothing on its own; its printing is tracked only if a deck lists it or a real copy exists.
- **Given** the tracked set is computed **When** the code is reviewed **Then** it calls only the public services of the collection and decks domains, never their models.
- **Given** a printing that has been removed from every collection and deck **When** the next run happens **Then** it gets no new snapshots, and its existing history is kept.
- **Given** a printing that is not in the tracked set **When** the daily run happens **Then** its latest price on the printing row is still updated (S-3.4.3); the tracked set limits only the snapshot history.

**Tasks**
- [ ] T-3.4.2.1 (api) `tracked_printing_ids()` in the prices service, built on `collection.services` and `decks.services` (ask E-4 and E-9 to expose the functions it needs)
- [ ] T-3.4.2.2 (api) Tests with factories across both domains, including a deck card with and without an assigned copy, a proxy-only printing and a foreign-language printing

#### S-3.4.3 — Daily snapshot from the bulk file

**As a** collector **I want** today's price stored on every printing and recorded in the history of every card I own or play **so that** any card shows a current price and valuation and profit/loss have a daily history.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | S-3.4.2, S-3.1.7 |

**Acceptance criteria**
- **Given** a successful `default_cards` sync **When** it processes the file **Then** snapshots are written during the same streaming pass, using the file's `usd`, `usd_foil`, `usd_etched`, `eur` and `eur_foil` prices, for every tracked printing that appears in the file. No per-card API calls are made.
- **Given** a successful `default_cards` sync **When** it processes the file **Then** every printing in the file, tracked or not, has its five latest-price columns and `prices_updated_on` set from the file in the same pass, and the full pass still meets the 15-minute and 512 MB limits (S-3.1.6).
- **Given** a printing missing from today's file, or an `on_demand` printing (S-3.2.5) **When** the run finishes **Then** its latest-price columns are left as they were.
- **Given** the run **When** snapshots are written **Then** `snapshot_date` is the UTC date of the bulk file's `updated_at`. Running again for the same date updates rows rather than duplicating them.
- **Given** 1,200 tracked printings **When** the run finishes **Then** 1,200 snapshot rows exist for that date and no untracked printing has a row. Both counts are recorded in the run summary.
- **Given** the `default_cards` file is unchanged and was skipped **When** the day passes **Then** no snapshot is written for that day. The gap is left as it is, with no values carried forward.
- **Given** snapshot writing fails **When** the failure happens **Then** the card and printing changes from that run are still committed, and the snapshot error is recorded on the run on its own.

**Tasks**
- [ ] T-3.4.3.1 (api) Load the tracked set into memory before the pass, and add a snapshot batch writer to the default-cards stream; extend the S-3.1.6 printing upsert with the five latest-price columns and `prices_updated_on`
- [ ] T-3.4.3.2 (api) Upsert keyed on (`printing_id`, `snapshot_date`), in a separate transaction from the card merge
- [ ] T-3.4.3.3 (api) Snapshot counts in `CardSyncRun`
- [ ] T-3.4.3.4 (api) Tests: tracked versus untracked (latest price on both, snapshot on tracked only), rerun on the same day, skipped day, snapshot failure kept separate

#### S-3.4.4 — Price lookup service and API

**As the** valuation views (F-9.5, F-9.6), card search and deck building **I want** current prices and price history in batches **so that** collection and deck values can be calculated quickly and any card can show its price.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 3 | S-3.4.3, S-3.2.1 |

**Acceptance criteria**
- **Given** up to 500 printing ids **When** `latest_prices(ids)` is called **Then** it returns each printing's latest price from the printing row (S-3.4.1) with all five price fields and `prices_updated_on`, tracked or not, using no more than 2 queries. A price older than 7 days is flagged `stale: true`, and a printing that has never been priced is returned as `null`. A foreign-language printing returns the prices of its `price_printing_id` (S-3.2.5).
- **Given** a caller that needs one currency (G1: each user values in USD or EUR) **When** it calls `latest_prices(ids, currency="EUR")` **Then** only `eur` and `eur_foil` are returned (`usd`, `usd_foil` and `usd_etched` for USD). No conversion between currencies is ever done.
- **Given** a printing **When** `GET /printings/{id}/prices?from=&to=` is called **Then** it returns rows in date order, each with a `granularity` of `daily`, or `weekly` for dates thinned by S-3.4.7. A range longer than 366 days returns 422.
- **Given** Phase 3 has shipped **When** `GET /cards/{oracle_id}`, `GET /cards/{oracle_id}/printings`, `GET /printings/{id}` (S-3.2.1) or filtered search (S-3.2.4) returns a printing, or a card with its default printing **Then** that printing includes a `prices` object with the five latest prices and `prices_updated_on`, read from the printing row with no extra query per result.
- **Given** a request with no token **When** either endpoint is called **Then** it returns 401.

**Tasks**
- [ ] T-3.4.4.1 (api) `latest_prices` (from the printing row) and `price_history` (from snapshots) in the public prices services
- [ ] T-3.4.4.2 (api) Routes and schemas, and the `prices` object on the S-3.2.1 and S-3.2.4 printing responses
- [ ] T-3.4.4.3 (api) Tests, including an untracked printing, the stale and never-priced cases, the currency filter, a foreign-language printing, prices on search results and a query-count assertion

#### S-3.4.5 — Fetch today's price when a printing starts being tracked

**As a** collector who just added a card **I want** to see its value the same day **so that** I don't wait for tomorrow's sync.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 3 | api | 3 | S-3.4.3, S-3.1.2 |

**Acceptance criteria**
- **Given** printings that became tracked today and have no snapshot yet **When** the hourly catch-up job runs **Then** it fetches them through Scryfall `POST /cards/collection` in groups of up to 75 ids, through the rate-limited client, and writes today's snapshots with the same five price fields.
- **Given** the next daily bulk run **When** it covers the same printing and date **Then** it overwrites the catch-up snapshot rather than adding a second row.
- **Given** Scryfall returns 429 or 5xx **When** the catch-up job runs **Then** it stops for this hour without failing the daily sync.

**Tasks**
- [ ] T-3.4.5.1 (api) arq job that finds newly tracked printings with no snapshot for today
- [ ] T-3.4.5.2 (api) `/cards/collection` batch call and the snapshot upsert
- [ ] T-3.4.5.3 (api) Tests with a mocked transport

#### S-3.4.6 — Preferred printing for a user

**As a** user who owns some of my cards **I want** decks and prices to show the copy I own when no printing was chosen **so that** images and values match what is in my binder.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Won't | 3 | api | 3 | S-3.4.4, F-9.1 |

**Won't:** superseded by decision G4. A deck card now uses the copy assigned to it, otherwise Scryfall's default printing; there is no "cheapest owned copy" choice and no price lookup. That rule lives in E-9 (S-9.6.6) and uses `default_printing_id` from S-3.1.6. The criteria below are kept only as the original record.

**Acceptance criteria**
- **Given** a user who owns one printing of a card **When** `preferred_printings(user_id, oracle_ids)` is called **Then** that printing is returned for the card.
- **Given** a user who owns several printings of a card **When** the function is called **Then** the cheapest is returned, using the latest snapshot price from S-3.4.4. Printings with no price rank after priced ones, and ties go to the newest release.
- **Given** a user who owns no printing of a card **When** the function is called **Then** the card's `default_printing_id` (Scryfall's default) is returned.
- **Given** up to 500 oracle ids **When** the function is called **Then** it uses no more than 3 queries and calls only the public services of the collection domain, never its models.
- **Given** Phase 3 has not shipped **When** any domain needs a default printing **Then** it uses `default_printing_id`. This function is the only place the owned-copy rule lives.

**Tasks**
- [ ] T-3.4.6.1 (api) `preferred_printings` in the public prices services, built on `collection.services` and `latest_prices`
- [ ] T-3.4.6.2 (api) Tests: one owned copy, several owned copies with and without prices, none owned, query-count assertion

#### S-3.4.7 — Thin price snapshots older than 2 years to weekly

**As an** operator **I want** old daily snapshots thinned to one per week **so that** the price table stays small while long-term history is kept (decision G2).

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api | 3 | S-3.4.3, F-1.9 |

**Acceptance criteria**
- **Given** snapshots dated within the last 730 days **When** the thinning job runs **Then** none of them are changed or deleted.
- **Given** an ISO week (Monday to Sunday, decision B8) that ended more than 730 days ago **When** the job runs **Then** for each printing only the latest snapshot in that week is kept, and the others are deleted.
- **Given** the job has already thinned a week **When** it runs again **Then** nothing changes (idempotent), and the number of rows deleted is recorded in the job's run history.
- **Given** the job **When** it is scheduled **Then** it runs weekly through an arq cron, deletes in batches of 10,000 rows (a setting) so it never holds a long lock, and never runs while a card sync holds the S-3.1.7 lock.

**Tasks**
- [ ] T-3.4.7.1 (api) `thin_price_snapshots` arq cron job with batched deletes, skipping while the sync lock is held
- [ ] T-3.4.7.2 (api) Retention settings (daily window 730 days, batch size)
- [ ] T-3.4.7.3 (api) Tests: the 730-day boundary, one row kept per printing and week, idempotency, lock skip

## Open questions

None open. Resolved: see OPEN-QUESTIONS.md decisions log.
