# E-9 — Collection & Budget

| Field | Value |
|---|---|
| Phase(s) | 3 |
| Goal | Track every card a user owns, by exact printing and finish, together with what it cost, what it is worth today, and which deck each copy is assigned to. Log purchases of singles and sealed product and report spend over time. |
| Success measures | <ul><li>A user can import a 5,000-row collection CSV from any of the four supported sources and see an unmatched-rows report in under 2 minutes.</li><li>Re-importing the same file creates 0 duplicate collection items, and a re-import never deletes an item.</li><li>Collection value, cost basis and P/L agree with a hand calculation over the same price snapshot to the cent, in each currency separately.</li><li>For any deck, the owned/proxy/missing split and cost to complete load in under 2 s.</li><li>No copy is ever assigned to more than one deck.</li></ul> |
| Out of scope | Buying or selling cards through the app; marketplace integrations; price alerts; a price source other than the daily Scryfall snapshots from F-3.4 (sealed product is valued by hand, decision G7); currency conversion or exchange rates (decision G1); adjusting value for condition or grading individual cards (decision G6); recording shipping costs or taxes (decision G3 revised). |

## F-9.1 — Collection management

Users keep a collection of owned cards. Each collection item is one lot of a single printing (Scryfall `id`) in one finish, condition and language, with its quantity, acquisition cost and the currency it was paid in, acquired date, store/source and a proxy flag. Cards and printings come from F-3.1. Condition uses the TCGplayer scale NM/LP/MP/HP/DMG and is kept for records only; it never changes value (decision G6). Proxies are tracked but count as not owned (decision G4). Each copy in a lot can be assigned to one deck (F-9.6).

Phase 3 · Priority Must

#### S-9.1.1 — Collection item model and CRUD API

**As a** collector **I want** to record the cards I own with printing, finish, condition, language, quantity and what I paid **so that** the app knows exactly what is in my collection.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | F-3.1, F-2.1 |

**Acceptance criteria**
- **Given** a signed-in user and a valid Scryfall printing `id` **When** they POST an item with finish `foil`, condition `NM`, language `en`, quantity 2, acquisition cost 3.50 and currency `USD` **Then** the item is stored and returned with HTTP 201, the same values and `is_proxy` false.
- **Given** a printing that Scryfall lists only as `nonfoil` **When** the user creates an item with finish `etched` **Then** the API returns 422 with an error naming the finishes that printing supports.
- **Given** a condition other than `NM`, `LP`, `MP`, `HP` or `DMG` **When** an item is created or edited **Then** the API returns 422 listing the allowed values (decision G6).
- **Given** an acquisition cost in any ISO 4217 currency (for example `GBP`) **When** the item is saved **Then** the cost and currency are stored exactly as paid and never converted (decision G1).
- **Given** an item **When** the user PATCHes quantity to 0 or a negative number **Then** the API returns 422 and the item is unchanged (removal is done with DELETE).
- **Given** an item owned by user A **When** user B requests, edits or deletes it **Then** the API returns 404 and the item is unchanged.
- **Given** an item created with no acquisition cost **When** it is read back **Then** cost is returned as `null`, currency as empty, and the item is still valid.

**Tasks**
- [ ] T-9.1.1.1 (api) `CollectionItem` SQLAlchemy model (user, printing FK, finish enum, condition enum NM/LP/MP/HP/DMG, language, quantity, `Numeric` cost, ISO 4217 currency, acquired date, store/source, `is_proxy`) and Alembic migration
- [ ] T-9.1.1.2 (api) Pydantic schemas with finish validation against the printing's Scryfall `finishes` and the condition enum
- [ ] T-9.1.1.3 (api) `collection/services` create/update/delete with ownership checks; router stays thin
- [ ] T-9.1.1.4 (api) pytest suite for validation, ownership and CRUD behavior

#### S-9.1.2 — Search and filter the collection

**As a** collector **I want** to search and filter my collection **so that** I can find a card quickly in a large collection.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | S-9.1.1, F-3.2 |

**Acceptance criteria**
- **Given** a collection of 10,000 items **When** the user lists it with page size 50 **Then** the first page returns in under 500 ms with a total count and a cursor for the next page (keyset paging, decision B2).
- **Given** items across several sets **When** the user filters by name substring, set code, finish, color identity, condition, proxy flag and "assigned to a deck / unassigned" together **Then** only items matching every filter are returned.
- **Given** 3 items of the same card in different printings **When** the user requests the "by card" view **Then** one row per card (`oracle_id`) is returned with total quantity 3 and the list of printings.
- **Given** a list request **When** it is executed **Then** printing, card and deck-assignment data are loaded in a fixed number of queries regardless of page size (no N+1).

**Tasks**
- [ ] T-9.1.2.1 (api) Collection repository with composable filters and sort (name, set, value, acquired date) in a custom query class
- [ ] T-9.1.2.2 (api) "By card" aggregation query grouped by `oracle_id`
- [ ] T-9.1.2.3 (api) List endpoints with cursor (keyset) paging and eager loading of printing, card and assignments
- [ ] T-9.1.2.4 (api) Query-count and performance tests on a 10,000-item factory dataset

#### S-9.1.3 — Web collection page

**As a** collector on the web **I want** to browse, add, edit and remove collection items **so that** I can maintain my collection from a desktop.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 3 | S-9.1.1, S-9.1.2, F-3.3, F-1.6 |

**Acceptance criteria**
- **Given** the collection page **When** the user types a card name in "Add card" and picks a printing **Then** a form pre-filled with finish `nonfoil`, condition `NM`, language `en`, quantity 1 and "proxy" off opens, and saving adds the item to the list without a full reload.
- **Given** a printing that has only foil and etched finishes **When** the add form opens **Then** the finish selector offers only `foil` and `etched`.
- **Given** the condition selector **When** it opens **Then** it offers exactly NM, LP, MP, HP and DMG.
- **Given** an item in the list **When** the user edits its quantity inline and presses Enter **Then** the change is saved and shown; on a 422 or 409 the old value is restored and the error is shown.
- **Given** an item **When** the user clicks Delete **Then** a confirmation is shown (naming any decks its copies are assigned to), and the item is removed only after confirming.
- **Given** a keyboard-only user **When** they tab through the list and forms **Then** every action above is reachable without a mouse.

**Tasks**
- [ ] T-9.1.3.1 (web) Collection list with filters bound to the URL query string, using TanStack Query
- [ ] T-9.1.3.2 (web) Add/edit item form using the F-3.3 card search and printing picker
- [ ] T-9.1.3.3 (web) Inline quantity edit with optimistic update and rollback
- [ ] T-9.1.3.4 (web) Vitest tests for form validation and rollback

#### S-9.1.4 — Mobile collection browse and quick add

**As a** collector at the table or in a store **I want** to look up and add cards from my phone **so that** I can check what I own and record new cards on the spot.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | mobile | 3 | S-9.1.1, S-9.1.2, F-3.3, F-1.7 |

**Acceptance criteria**
- **Given** the mobile collection tab **When** the user searches for a card name **Then** matching owned items show with quantity, finish, condition and a proxy badge where it applies, and a "Not owned" state shows when there are none.
- **Given** a search result for an unowned card **When** the user taps "Add", picks a printing and saves **Then** the item is created with quantity 1 and appears in the list.
- **Given** an owned item **When** the user taps + or − on its quantity **Then** the quantity changes by 1; − at quantity 1 asks to delete the item instead.
- **Given** no network **When** the user tries to add an item **Then** a clear offline error is shown and nothing is silently lost.

**Tasks**
- [ ] T-9.1.4.1 (mobile) Collection list and search screen using the shared API client
- [ ] T-9.1.4.2 (mobile) Quick-add sheet with printing picker
- [ ] T-9.1.4.3 (mobile) Quantity stepper with delete confirmation
- [ ] T-9.1.4.4 (mobile) Jest tests for stepper and offline error state

#### S-9.1.5 — Track proxies

**As a** player who uses proxies **I want** to record proxies in my collection, marked as not owned **so that** I know which deck slots are proxied without inflating what I own or what it is worth.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, web, mobile | 3 | S-9.1.1, S-9.1.3 |

**Acceptance criteria**
- **Given** the add or edit form **When** the user turns on "Proxy" and saves **Then** the item is stored with `is_proxy` true and shows a "Proxy" badge on web and mobile.
- **Given** proxy items **When** collection value, cost basis, P/L or owned counts are computed **Then** proxies are left out of all of them and their count is reported separately as "proxies" (decision G4).
- **Given** a proxy item **When** spend reports run **Then** any purchase it came from is still counted as spend (spend is about money paid, not ownership).
- **Given** a deck card whose slot is filled by a proxy assigned to it (S-9.6.7) **When** deck ownership is computed **Then** the card shows as "proxy" and counts as not owned.

**Tasks**
- [ ] T-9.1.5.1 (api) Exclude `is_proxy` items from valuation, ownership and owned counts in `collection/services`; separate proxy count in totals
- [ ] T-9.1.5.2 (web) Proxy toggle in the item form and proxy badge and filter in the list
- [ ] T-9.1.5.3 (mobile) Proxy toggle in quick add and proxy badge
- [ ] T-9.1.5.4 (api) Tests: proxies left out of value, cost basis, P/L and owned counts

## F-9.2 — Collection CSV import

Users import their existing collection from CSV exports of Moxfield, Archidekt, ManaBox and Deckbox. Each importer is built from that site's public format documentation and fixed as users report problems (decision G8). The import shows a preview, lets the user adjust column mapping, and reports rows that could not be matched to a printing. Files are limited to 10 MB or 50,000 rows. A re-import updates and adds rows but never deletes; rows missing from the new file go to a review list (decision G5). Users can also export their collection as CSV, in our own format and a Moxfield-compatible one (G5). CSV import and export are web only; the mobile app has manual add and edit only (PO decision 2026-11-07).

Phase 3 · Priority Must

#### S-9.2.1 — Upload a CSV and detect its source format

**As a** collector **I want** to upload a CSV export and have the app recognise where it came from **so that** I don't have to map columns by hand for the common sources.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | S-9.1.1 |

**Acceptance criteria**
- **Given** a fixture export for each of Moxfield, Archidekt, ManaBox and Deckbox, written from that site's public format documentation **When** it is uploaded **Then** the detected source matches the file's origin and a column mapping preset for that source is proposed.
- **Given** a CSV whose headers match no preset **When** it is uploaded **Then** the source is reported as `unknown` and the user is asked to map columns manually.
- **Given** an upload **When** parsing finishes **Then** the response contains the total row count and the first 20 parsed rows, and no collection items have been created.
- **Given** a file larger than 10 MB, or with more than 50,000 data rows **When** it is uploaded **Then** the API returns 422 naming the limit that was exceeded, and nothing is stored (decision G5).
- **Given** a file that is not valid CSV (UTF-8 with or without BOM) **When** it is uploaded **Then** the API returns 422 with a reason and nothing is stored.

**Tasks**
- [ ] T-9.2.1.1 (api) `CollectionImport` model (user, file hash, detected source, status, mapping, counts) and Alembic migration
- [ ] T-9.2.1.2 (api) Streaming CSV parser using the standard library `csv` module, with BOM and delimiter handling, and the 10 MB / 50,000-row limits
- [ ] T-9.2.1.3 (api) Mapping presets stored as data (one per source) and header-based detection
- [ ] T-9.2.1.4 (api) Test fixtures written from each site's public format documentation; files users report as failing are added as regression fixtures (decision G8)

#### S-9.2.2 — Map columns and preview the import

**As a** collector **I want** to see and adjust how CSV columns map to collection fields before importing **so that** the import does what I expect, even for a changed or unknown format.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 3 | S-9.2.1 |

**Acceptance criteria**
- **Given** a parsed upload **When** the user submits a mapping **Then** the mapping is validated: quantity is required, plus either a Scryfall id column or a name column; otherwise the API returns 422 naming the missing fields.
- **Given** a valid mapping **When** a preview is requested **Then** the first 20 rows are returned as they would be imported (printing match, finish, condition, language, quantity, cost, proxy) with a per-row status of `matched`, `ambiguous` or `unmatched`.
- **Given** source values for finish and condition (for example `foil`, `Near Mint`, `Lightly Played`) **When** the preview is built **Then** they are translated to the app's enums using value maps from the preset, and untranslatable values mark the row `unmatched` with the reason.
- **Given** a Cardmarket-style condition grade **When** it is translated **Then** MT and NM map to NM, EX to LP, GD to MP, LP to HP, and PL and PO to DMG (decision G6).

**Tasks**
- [ ] T-9.2.2.1 (api) Mapping schema and validation endpoint
- [ ] T-9.2.2.2 (api) Value maps for finish, condition (TCGplayer scale, with the Cardmarket mapping) and language per preset
- [ ] T-9.2.2.3 (api) Preview endpoint reusing the row-matching service from S-9.2.3

#### S-9.2.3 — Match CSV rows to Scryfall printings

**As a** collector **I want** each row matched to the right printing **so that** my imported collection has the correct editions and prices.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | S-9.2.1, F-3.1, S-3.1.2 |

**Acceptance criteria**
- **Given** a row with a Scryfall id that exists in the card database **When** it is matched **Then** that printing is used and no other columns are consulted for identification.
- **Given** a row with a Scryfall id of a foreign-language printing that is not in the local database **When** it is matched **Then** the printing is looked up on demand from the Scryfall API, cached, and used (decision G9); if Scryfall does not know the id, the row falls back to set code and collector number.
- **Given** a row without a Scryfall id but with set code and collector number **When** it is matched **Then** the printing with that set and collector number is used.
- **Given** a row with only a name and set code that resolves to more than one printing **When** it is matched **Then** the row is marked `ambiguous` with the candidate printings listed, and it is not imported automatically.
- **Given** a row whose card name does not exist in the card table **When** it is matched **Then** it is marked `unmatched` with reason "card not found".
- **Given** a finished import **When** the user requests the unmatched report **Then** they can download a CSV of every `unmatched` and `ambiguous` row with its original line number, original values and reason.

**Tasks**
- [ ] T-9.2.3.1 (api) Row matcher service: Scryfall id (local, then on-demand lookup) → set + collector number → name + set → name, with match confidence
- [ ] T-9.2.3.2 (api) Batch lookups so a 5,000-row file uses a bounded number of queries; on-demand Scryfall lookups go through the E-3 client and respect its rate limit
- [ ] T-9.2.3.3 (api) Unmatched/ambiguous report endpoint (CSV download)
- [ ] T-9.2.3.4 (api) Tests for each match path, including double-faced card names, split cards and a foreign-language id

#### S-9.2.4 — Run the import as a background job

**As a** collector **I want** large imports to run in the background with progress **so that** a big file does not time out or block the app.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | S-9.2.2, S-9.2.3, F-1.9 |

**Acceptance criteria**
- **Given** a confirmed mapping **When** the user starts the import **Then** an arq job is queued, the API returns 202 with the import id, and the status endpoint reports `queued` → `running` (with processed row count) → `completed` or `failed`.
- **Given** a 5,000-row file **When** the job runs **Then** it completes in under 2 minutes in the dev Docker Compose environment and processes rows in batches without loading the whole file into memory at once.
- **Given** a 50,000-row file (the maximum) **When** the job runs **Then** it completes without exceeding the worker's memory limit.
- **Given** a completed import **When** the user views it **Then** counts of created, updated, skipped, unmatched and "held for review" rows are shown, and they add up to the file's row count.
- **Given** the job fails partway (for example the worker restarts) **When** it is retried **Then** rows already written are not written twice.

**Tasks**
- [ ] T-9.2.4.1 (api) arq task that matches and writes rows in batches inside transactions
- [ ] T-9.2.4.2 (api) Import status endpoint with progress counts
- [ ] T-9.2.4.3 (api) Retry-safe checkpointing per batch
- [ ] T-9.2.4.4 (api) Integration tests on generated 5,000-row and 50,000-row files

#### S-9.2.5 — Re-import updates and adds, never deletes

**As a** collector who exports from another app regularly **I want** to re-import an updated export **so that** my collection stays current without duplicates and without losing anything.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | S-9.2.4, S-9.6.7 |

**Acceptance criteria**
- **Given** a file that was already imported **When** the exact same file is imported again **Then** 0 items are created or changed and the report says "already imported" for every row.
- **Given** a re-import where one row's quantity changed from 2 to 3 **When** it completes **Then** the matching item's quantity becomes 3, it is counted as `updated`, and no new item is created.
- **Given** a re-import with a row that was not in the earlier file **When** it completes **Then** a new item is created and counted as `created`.
- **Given** items the user added by hand **When** any import runs **Then** those items are never modified or merged into imported rows.
- **Given** a re-import where rows from the previous import of the same source are missing **When** it completes **Then** no item is deleted or set to 0, and each missing row is added to the review list (S-9.2.8) (decision G5).
- **Given** a re-import row that would lower an item's quantity below the number of its copies assigned to decks **When** it completes **Then** the item is not changed, and the row is added to the review list with the reason "copies assigned to decks".

**Tasks**
- [ ] T-9.2.5.1 (api) Import source key and row natural key (printing, finish, condition, language, source) stored on imported items
- [ ] T-9.2.5.2 (api) Upsert logic keyed on the natural key, scoped to the import source; no delete path
- [ ] T-9.2.5.3 (api) Missing-row detection against the previous import of the same source, writing review entries
- [ ] T-9.2.5.4 (api) Tests: same file twice, changed quantity, new row, missing rows kept, manual items untouched, quantity below assigned copies

#### S-9.2.6 — Web import wizard

**As a** collector **I want** a step-by-step import screen **so that** I can upload, check the mapping, preview, import and review problems in one place.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 3 | S-9.2.2, S-9.2.4, S-9.2.3, S-9.2.8, F-1.6 |

**Acceptance criteria**
- **Given** the import page **When** the user selects a file **Then** the detected source and proposed mapping are shown, each target field with a dropdown of CSV columns; a file over 10 MB is refused before upload with the limit shown.
- **Given** a changed mapping **When** the user clicks "Preview" **Then** the 20-row preview refreshes with per-row status badges.
- **Given** a running import **When** the user stays on or returns to the page **Then** a progress bar shows processed/total rows, updated at least every 2 s.
- **Given** a completed import with unmatched rows **When** the result step shows **Then** the created/updated/skipped/unmatched/held-for-review counts are visible and the unmatched report can be downloaded.
- **Given** a completed re-import with rows held for review **When** the result step shows **Then** a link opens the review list (S-9.2.8).
- **Given** a keyboard-only user **When** they go through the wizard **Then** every step, including file selection and mapping dropdowns, is operable without a mouse.

**Tasks**
- [ ] T-9.2.6.1 (web) Wizard steps: upload, mapping, preview, progress, result
- [ ] T-9.2.6.2 (web) Polling of import status with TanStack Query
- [ ] T-9.2.6.3 (web) Vitest tests for mapping step validation and result rendering

#### S-9.2.7 — Export the collection as CSV

**As a** collector **I want** to export my collection as CSV **so that** I have a backup and can move my collection to another app.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, web | 3 | S-9.1.2, S-9.2.2 |

**Acceptance criteria**
- **Given** a collection **When** the user exports it in "MTG Stats" format **Then** the CSV has one row per item with Scryfall id, card name, set code, collector number, finish, condition, language, quantity, acquisition cost, currency, acquired date, store/source and proxy flag.
- **Given** an export in "MTG Stats" format **When** it is imported into an empty collection of another account **Then** the same items are recreated with the same values (deck assignments are not part of the file).
- **Given** a collection **When** the user exports it in "Moxfield-compatible" format **Then** the columns and values follow Moxfield's public collection import format, and proxies are left out with the number left out shown in the export summary (decision G5).
- **Given** a 50,000-item collection **When** it is exported **Then** the file is streamed and the download starts in under 2 s.

**Tasks**
- [ ] T-9.2.7.1 (api) Streaming CSV export endpoint with `format` = `mtgstats` or `moxfield`
- [ ] T-9.2.7.2 (api) Moxfield column and value mapping built from Moxfield's public format documentation
- [ ] T-9.2.7.3 (web) Export button with format choice on the collection page
- [ ] T-9.2.7.4 (api) Round-trip test: export in our format, import into a fresh account, compare

#### S-9.2.8 — Review rows missing from a re-import

**As a** collector **I want** to review the items that were missing from my latest re-import **so that** I decide myself whether to keep, change or remove them.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web | 3 | S-9.2.5, F-1.6 |

**Acceptance criteria**
- **Given** a re-import that held 5 rows for review **When** the user opens the review list **Then** the 5 items are listed with card, printing, finish, condition, quantity, the reason ("missing from new file" or "copies assigned to decks"), and any decks their copies are assigned to.
- **Given** an item in the review list **When** the user chooses "Keep" **Then** the item is unchanged and leaves the list.
- **Given** an item in the review list **When** the user chooses "Remove" and confirms **Then** the item is deleted, its deck assignments are released, and it leaves the list; nothing is deleted without that confirmation.
- **Given** a "copies assigned to decks" entry **When** the user chooses "Apply new quantity" **Then** they must first pick which deck assignments to release, and the quantity is then updated.
- **Given** unresolved review entries **When** the user returns later **Then** the entries are still there, and a later import of the same source replaces them with a fresh list.

**Tasks**
- [ ] T-9.2.8.1 (api) `ImportReviewEntry` model (import, item, reason) and Alembic migration
- [ ] T-9.2.8.2 (api) Review list and keep/remove/apply endpoints in `collection/services`, releasing assignments explicitly
- [ ] T-9.2.8.3 (web) Review list page with bulk keep/remove and confirmation
- [ ] T-9.2.8.4 (api, web) Tests for each action and for replacement by a later import

## F-9.3 — Purchase log

Users log purchases of singles and sealed product (booster box, precon, collector booster and so on) with line items, store, date and totals, in the currency paid (never converted, decision G1). Shipping costs and taxes are not recorded anywhere (decision G3 revised, 2026-11-07), so a card's cost basis is its purchase price only. Single line items can create collection items; sealed units can later be "opened" into collection items. The cost of opened sealed product is split across the pulled cards by market value on the opening date (decision G3).

Phase 3 · Priority Must

#### S-9.3.1 — Purchase and line item model and API

**As a** collector **I want** to record a purchase with its store, date and line items **so that** I have a full log of what I spent.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | F-2.1, F-3.1 |

**Acceptance criteria**
- **Given** a purchase with date, store, currency `USD`, one single line item (printing, finish, condition, quantity 4, unit price 0.25) and one sealed line item (type `collector_booster`, name, set code, quantity 2, unit price 25.00) **When** it is saved **Then** it returns 201 with subtotal 51.00 and total 51.00 (the total is the sum of the lines; there are no shipping or tax fields, decision G3 revised).
- **Given** a purchase **When** a line item has quantity < 1, or a line amount is negative **Then** the API returns 422.- **Given** a sealed line item **When** its product type is not one of the defined types (booster box, collector booster box, set/play booster box, single booster, precon/Commander deck, bundle, other) **Then** the API returns 422.
- **Given** a purchase paid in `EUR` by a user whose valuation currency is `USD` **When** it is saved **Then** every amount is stored in `EUR` and never converted (decision G1).
- **Given** a purchase owned by another user **When** it is requested or edited **Then** the API returns 404.

**Tasks**
- [ ] T-9.3.1.1 (api) `Purchase` and `PurchaseLine` models (line kind `single` or `sealed`; `Numeric` amounts; currency on the purchase; no shipping or tax columns) and Alembic migration
- [ ] T-9.3.1.2 (api) Sealed product type enum and Pydantic schemas with totals computed server-side
- [ ] T-9.3.1.3 (api) `collection/services` purchase CRUD and list with date-range filter
- [ ] T-9.3.1.4 (api) pytest suite for totals, validation and ownership

#### S-9.3.2 — Add purchased singles to the collection

**As a** collector **I want** singles from a purchase to go straight into my collection with their cost **so that** I don't enter the same cards twice.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 3 | S-9.3.1, S-9.1.1 |

**Acceptance criteria**
- **Given** a new purchase with "add singles to collection" on **When** it is saved **Then** each single line creates one collection item with the line's printing, finish, condition, quantity, purchase date, store and currency, linked to the line, and its unit acquisition cost is the line's unit price (decision G3 revised).
- **Given** a linked line **When** its unit price is edited **Then** the linked collection items' acquisition costs are updated to the new unit price in the same transaction.
- **Given** a purchase with linked items **When** the user deletes it and chooses "keep cards" **Then** the purchase is removed and the items remain with their cost; choosing "remove cards" deletes the linked items too and releases their deck assignments.

**Tasks**
- [ ] T-9.3.2.1 (api) Service call from purchase save to collection item creation (explicit call, no event hooks)
- [ ] T-9.3.2.2 (api) Line ↔ item link and cascade rules for edit and delete
- [ ] T-9.3.2.3 (api) Tests for create, unit price edit, and both delete choices

#### S-9.3.3 — Open sealed product into collection items

**As a** collector **I want** to mark sealed product as opened and add the cards I pulled **so that** my collection reflects what came out of the box.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api | 3 | S-9.3.1, S-9.1.1, F-4.1 |

**Acceptance criteria**
- **Given** a sealed line with quantity 6 **When** the user opens 1 unit **Then** the line shows 5 unopened and 1 opened, and opening more than the unopened count returns 422.
- **Given** an opening **When** the user adds pulled cards (by printing and finish, or by pasting a list in a format F-4.1 accepts) **Then** collection items are created in the purchase's currency and linked to that opening.
- **Given** an opened unit **When** spend reports run **Then** the sealed line is still counted once, at its purchase price, under "sealed" (opening never changes spend).
- **Given** an opening **When** it is undone **Then** the unit returns to unopened and its linked collection items are removed after confirmation, releasing any deck assignments.

**Tasks**
- [ ] T-9.3.3.1 (api) `SealedOpening` model (line, units opened, opened date) and Alembic migration
- [ ] T-9.3.3.2 (api) Open/undo service and linking of created collection items
- [ ] T-9.3.3.3 (api) Reuse the F-4.1 text parser for pasted pull lists
- [ ] T-9.3.3.4 (api) Tests for partial opening, over-opening and undo

#### S-9.3.4 — Allocate sealed cost to opened cards by market value

**As a** collector **I want** the cost of opened sealed product spread across the cards I pulled by what each card is worth **so that** profit/loss on those cards is meaningful.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api | 3 | S-9.3.3, S-9.5.1, F-3.4 |

**Acceptance criteria**
- **Given** an opening whose cost is the opened units' purchase price only (no shipping or tax, decision G3 revised) **When** cost allocation runs **Then** each pulled card's acquisition cost is that cost × (the card's market value ÷ the total market value of all pulled cards), using prices on the opening date (decision G3).
- **Given** a fixed test opening of 100.00 with three pulled cards valued 30.00, 15.00 and 5.00 on the opening date **When** allocation runs **Then** their costs are 60.00, 30.00 and 10.00.
- **Given** any allocation **When** it is stored **Then** the sum of allocated costs equals the opening's cost exactly; the rounding remainder goes to the highest-valued card, ties broken by item id.
- **Given** market value **When** it is looked up **Then** it uses the user's valuation currency (S-9.5.6) and the price for each card's finish from the snapshot on the opening date, or the nearest earlier one, or the first one after if none is earlier.
- **Given** a pulled card with no price on the opening date **When** allocation runs **Then** it gets a cost of 0.00 and is flagged "no price at opening"; if no pulled card has a price, the cost is split evenly by card count.
- **Given** an allocation **When** the user later adds or removes a pulled card **Then** allocation is recomputed for that opening.

**Tasks**
- [ ] T-9.3.4.1 (api) Allocation service: value-weighted split with exact-sum rounding, through the prices domain's public interface
- [ ] T-9.3.4.2 (api) Recompute on change to the opening's items (explicit service call)
- [ ] T-9.3.4.3 (api) Tests: fixed example, exact-sum rounding, no-price card, no prices at all, backdated opening

#### S-9.3.5 — Web purchase log

**As a** collector on the web **I want** to list, add and edit purchases with line items **so that** keeping the log is quick.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 3 | S-9.3.1, S-9.3.2, F-3.3, F-1.6 |

**Acceptance criteria**
- **Given** the purchase log **When** it loads **Then** purchases are listed newest first with date, store, number of lines and total in the currency paid.
- **Given** the purchase form **When** the user adds, edits or removes line items **Then** subtotal and total update immediately and match the totals the API returns after saving.
- **Given** a sealed line **When** the user clicks "Open" **Then** they can choose how many units to open and add pulled cards (S-9.3.3).
- **Given** a keyboard-only user **When** they add a line item **Then** card search, printing selection and the line fields are all reachable and submittable from the keyboard.

**Tasks**
- [ ] T-9.3.5.1 (web) Purchase list with date-range and store filters
- [ ] T-9.3.5.2 (web) Purchase form with dynamic single/sealed line items (no shipping or tax fields)
- [ ] T-9.3.5.3 (web) Open-sealed dialog
- [ ] T-9.3.5.4 (web) Vitest tests for client totals and form validation

#### S-9.3.6 — Log a purchase from the phone

**As a** collector in a store **I want** to log a purchase from my phone **so that** I record it before I forget.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | mobile | 3 | S-9.3.1, S-9.3.2, F-1.7 |

**Acceptance criteria**
- **Given** the mobile purchase screen **When** the user enters store, adds 3 line items and saves **Then** the purchase appears in the web log with the same total.
- **Given** a store the user has used before **When** they start typing it **Then** previously used store names are suggested.
- **Given** a save that fails because the device is offline **When** the error shows **Then** the form keeps everything the user entered.

**Tasks**
- [ ] T-9.3.6.1 (mobile) Purchase entry form with single and sealed line items (no shipping or tax fields)
- [ ] T-9.3.6.2 (api) Endpoint listing the user's distinct store names
- [ ] T-9.3.6.3 (mobile) Jest tests for form state retention on error

#### S-9.3.7 — Spread shipping and tax over a purchase's lines

**As a** collector **I want** shipping and tax included in what my cards cost me **so that** my cost basis reflects what I really paid.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Won't | 3 | api | 3 | S-9.3.1 |

Won't: PO decision 2026-11-07: shipping and tax are not recorded. The criteria below are kept only as the original record.

**Acceptance criteria**
- **Given** a purchase with a single line of 40.00, a sealed line of 10.00, shipping 4.00 and tax 6.00 **When** it is saved **Then** the shipping and tax (10.00) are split in proportion to each line's subtotal: 8.00 to the single line and 2.00 to the sealed line (decision G3).
- **Given** a single line with quantity 4 **When** its share is applied **Then** each unit's acquisition cost includes a quarter of the line's share.
- **Given** a sealed line's share **When** units of it are opened **Then** each opened unit's cost includes its part of that share, and S-9.3.4 splits it across the pulled cards by market value.
- **Given** any split **When** it is stored **Then** the shares add up exactly to shipping plus tax; the rounding remainder goes to the line with the largest subtotal, ties broken by line id.
- **Given** spend reports **When** they run **Then** shipping and tax are counted once in the purchase total, never twice (allocation changes cost basis only).

**Tasks**
- [ ] T-9.3.7.1 (api) Allocation of shipping and tax to lines in `collection/services`, using `Decimal` and exact-sum rounding
- [ ] T-9.3.7.2 (api) Recompute linked items' and openings' costs when shipping, tax or lines change (explicit service calls)
- [ ] T-9.3.7.3 (api) Tests: proportional split, rounding remainder, edit and recompute, spend unchanged

## F-9.4 — Spend reports

Spend over a date range (week, month, year or custom) and by category (singles vs. sealed, and by store), shown as charts on web with Recharts and on mobile with victory-native (decision A16). Spend is reported in the currency paid; when a user has paid in more than one currency, each currency gets its own totals and they are never added together or converted (decision G1). Days follow the user's time zone and weeks start on ISO Monday (decision B8). Spend is private by default (decision C8).

Phase 3 · Priority Should

#### S-9.4.1 — Spend aggregation API

**As a** collector **I want** my spending summed by period and category **so that** I can see where my money goes.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | S-9.3.1 |

**Acceptance criteria**
- **Given** purchases across 3 months **When** the user requests range `month` for a given month **Then** the response contains the total for that month and a per-day series whose values sum to the total.
- **Given** range presets `week`, `month`, `year` and `custom` (start and end date, inclusive) **When** each is requested **Then** the buckets are day, day, month and an automatically chosen size (day up to 62 days, else month) respectively, and `week` runs Monday to Sunday (ISO).
- **Given** a user whose time zone is `Europe/Madrid` **When** a purchase is dated on a given local day **Then** it falls in that day's bucket, whatever the server's time zone (decision B8).
- **Given** purchases with singles and sealed lines at 2 stores **When** the user groups by category **Then** totals are returned for `singles` and `sealed`, and grouping by store returns one total per store; each grouping sums to the overall total.
- **Given** purchases in `USD` and `EUR` in the same range **When** the report runs **Then** a separate set of totals and series is returned for each currency, and amounts are never added across currencies or converted (decision G1).
- **Given** a range with no purchases **When** it is requested **Then** the totals are 0 and every bucket is present with 0.
- **Given** another user calls the report for this user **When** the request is handled **Then** it returns 403 unless `financials_visible_to(viewer_id, owner_id, "spend")` from the players service interface allows it (decision C8, S-8.3.6); spend is private by default.

**Tasks**
- [ ] T-9.4.1.1 (api) Spend repository queries grouped by bucket, category, store and currency
- [ ] T-9.4.1.2 (api) Range preset resolution in the user's time zone with ISO Monday weeks
- [ ] T-9.4.1.3 (api) Report endpoint and schemas with one result block per currency
- [ ] T-9.4.1.4 (api) Tests that every grouping sums to the total in each currency
- [ ] T-9.4.1.5 (api) Owner-only access by default; non-owner calls go through `financials_visible_to` (wired by S-8.3.6)

#### S-9.4.2 — Web spend charts

**As a** collector on the web **I want** spend charts with range and grouping controls **so that** I can spot trends at a glance.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | web | 3 | S-9.4.1, F-1.6 |

**Acceptance criteria**
- **Given** the spend page **When** it loads **Then** a Recharts bar chart of the current month's spend per day is shown, stacked by singles and sealed, with the period total above it.
- **Given** the range selector **When** the user picks week, month, year or a custom range **Then** the chart and totals update to match the API response for that range.
- **Given** "by store" **When** selected **Then** a chart of spend per store is shown, sorted by amount, highest first.
- **Given** spend in more than one currency in the range **When** the page shows **Then** a currency switcher shows one currency's chart and total at a time, and every currency's total is listed above it; no combined total is shown.
- **Given** any chart **When** a screen-reader or keyboard user opens "Show as table" **Then** the same data is shown in an accessible table.

**Tasks**
- [ ] T-9.4.2.1 (web) Range selector (presets plus custom date picker)
- [ ] T-9.4.2.2 (web) Recharts stacked bar and per-store charts with a table toggle and currency switcher
- [ ] T-9.4.2.3 (web) Vitest tests for range-to-query mapping and multi-currency rendering

#### S-9.4.3 — Mobile spend summary

**As a** collector on mobile **I want** to see this week's, month's and year's spend, with a chart of spend over time **so that** I can check my budget and its trend away from a computer.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 5 | mobile | 3 | S-9.4.1, F-1.7, S-7.2.8 |

**Acceptance criteria**
- **Given** the mobile spend screen **When** it loads **Then** totals for the current week, month and year are shown, each split into singles and sealed, with one line per currency paid.
- **Given** a tap on a period **When** the detail opens **Then** a victory-native bar chart of spend per bucket, stacked by singles and sealed, is shown with the period total above it, and the values match the API response for that range and currency.
- **Given** the period detail **When** the user switches to "by store" **Then** a bar chart of spend per store is shown, highest first.
- **Given** a screen-reader user **When** they reach a chart **Then** the same data is available as a list of totals per bucket and per store.

**Tasks**
- [ ] T-9.4.3.1 (mobile) Spend summary screen with period cards
- [ ] T-9.4.3.2 (mobile) Period detail with victory-native stacked bar and per-store charts from the shared chart kit
- [ ] T-9.4.3.3 (mobile) Accessible list alternative for each chart
- [ ] T-9.4.3.4 (mobile) Jest tests for period-to-query mapping and the list alternative

#### S-9.4.4 — Spend dashboard widget

**As a** collector **I want** a spend widget on my dashboard **so that** spending sits next to my other stats.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 2 | web | 3 | S-9.4.1, F-7.4 |

**Acceptance criteria**
- **Given** the dashboard widget catalog **When** the user adds "Spend this month" **Then** a widget with the month total (one line per currency paid) and a small singles/sealed bar is shown and its placement is saved with the layout.
- **Given** the widget **When** the user clicks it **Then** they go to the spend page with the same range selected.

**Tasks**
- [ ] T-9.4.4.1 (web) Spend widget registered in the F-7.4 widget catalog
- [ ] T-9.4.4.2 (web) Vitest render test

## F-9.5 — Valuation & P/L

Current market value, cost basis and profit/loss per collection item and in total, based on the latest daily price snapshot from F-3.4. Each user picks one valuation currency, USD or EUR, and values use Scryfall prices in that currency (decision G1). Costs stay in the currency paid; P/L is shown only where the cost currency matches the valuation currency, and cost totals are kept per currency. Condition never changes value (G6). Proxies have no value (G4). Unopened sealed product is shown at purchase cost, with a value the user can enter by hand (G7).

Phase 3 · Priority Must

#### S-9.5.1 — Current value per collection item

**As a** collector **I want** each item valued at today's market price for its finish **so that** I know what my cards are worth.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | S-9.1.1, F-3.4, S-9.5.6 |

**Acceptance criteria**
- **Given** a USD user's nonfoil item with quantity 3 and a latest snapshot `usd` of 2.00 **When** it is valued **Then** unit value is 2.00 and item value is 6.00 USD, with the snapshot date returned.
- **Given** a USD user **When** items are valued **Then** nonfoil uses `usd`, foil uses `usd_foil` and etched uses `usd_etched`; a foil item never uses the nonfoil price.
- **Given** a EUR user **When** items are valued **Then** nonfoil uses `eur` and foil uses `eur_foil`; an etched item has no EUR price and is valued `null` with reason "no price".
- **Given** two items of the same printing and finish in conditions NM and HP **When** they are valued **Then** their unit values are identical (decision G6).
- **Given** an item whose printing has no price for its finish in the latest snapshot **When** it is valued **Then** its value is `null` with reason "no price", and it is excluded from totals.
- **Given** a proxy item **When** it is valued **Then** its value is `null` with reason "proxy", and it is excluded from totals.
- **Given** a collection total **When** it is computed **Then** the response includes the total value in the valuation currency, the number of items with no price, the number of proxies and the snapshot date used.

**Tasks**
- [ ] T-9.5.1.1 (api) Valuation service mapping (valuation currency, finish) → snapshot price field (`usd`, `usd_foil`, `usd_etched`, `eur`, `eur_foil`)
- [ ] T-9.5.1.2 (api) Latest-snapshot lookup in one query for a page of items, through the prices domain's public interface
- [ ] T-9.5.1.3 (api) Tests for each currency and finish, condition not affecting value, proxies and the no-price case

#### S-9.5.2 — Cost basis and profit/loss

**As a** collector **I want** cost basis and profit/loss per item and in total **so that** I can see which cards gained or lost value.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 3 | S-9.5.1 |

**Acceptance criteria**
- **Given** a USD user's item with quantity 2, unit cost 5.00 USD and unit value 7.50 USD **When** P/L is computed **Then** cost basis is 10.00, value 15.00, P/L +5.00 and P/L % +50.0.
- **Given** an item with no acquisition cost **When** totals are computed **Then** it is excluded from cost basis and P/L totals and counted as "unknown cost".
- **Given** an item whose cost currency differs from the user's valuation currency **When** P/L is computed **Then** P/L is `null` with reason "currency mismatch", and its cost is never converted (decision G1).
- **Given** items with costs in USD and EUR **When** totals are computed **Then** cost basis is returned per currency, and total P/L covers only items whose cost currency matches the valuation currency, with the number of mismatched items returned.
- **Given** the collection list **When** sorted by P/L descending **Then** the item with the highest P/L comes first and items with `null` P/L come last.
- **Given** another user calls a value, cost basis or P/L endpoint for this user **When** the request is handled **Then** it returns 403 unless `financials_visible_to(viewer_id, owner_id, "collection_value")` from the players service interface allows it (decision C8, S-8.3.6); collection value is private by default.

**Tasks**
- [ ] T-9.5.2.1 (api) P/L calculation in the valuation service using `Decimal`
- [ ] T-9.5.2.2 (api) Value and P/L fields and sorts on collection list endpoints
- [ ] T-9.5.2.3 (api) Totals endpoint (value, cost basis per currency, P/L, unknown-cost, currency-mismatch, proxy and no-price counts)
- [ ] T-9.5.2.4 (api) Owner-only access by default on value, P/L and totals endpoints; non-owner calls go through `financials_visible_to` (wired by S-8.3.6)

#### S-9.5.3 — Collection value over time

**As a** collector **I want** to see how my collection's total value has changed **so that** I can follow trends.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api | 3 | S-9.5.1, F-1.9 |

**Acceptance criteria**
- **Given** a new daily price snapshot **When** the valuation job runs after it **Then** one row per user per day is stored with total value, cost basis in the valuation currency and the currency used, and running it again for the same day replaces that row rather than adding another.
- **Given** 90 days of history **When** the user requests the value series for the last 90 days **Then** 90 points are returned in date order in under 500 ms.
- **Given** a day with no snapshot **When** the series is returned **Then** that day is absent and not filled with a guessed value.
- **Given** a user who switched valuation currency 10 days ago **When** they request the series **Then** only rows in the current currency are returned, and the earlier rows are kept and return if they switch back.
- **Given** another user requests this user's value series **When** the request is handled **Then** the same `financials_visible_to(viewer_id, owner_id, "collection_value")` check as S-9.5.2 applies, and it returns 403 by default.

**Tasks**
- [ ] T-9.5.3.1 (api) `CollectionValueDaily` model (with currency) and Alembic migration
- [ ] T-9.5.3.2 (api) arq job chained after the F-3.4 snapshot job, upserting per user and day
- [ ] T-9.5.3.3 (api) Series endpoint with date range, filtered to the current valuation currency

#### S-9.5.4 — Web valuation view

**As a** collector on the web **I want** a valuation page with totals, top movers and a value chart **so that** I understand my collection's worth.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 3 | S-9.5.2, S-9.5.3, F-1.6 |

**Acceptance criteria**
- **Given** the valuation page **When** it loads **Then** total value, cost basis and P/L are shown with the snapshot date and currency, and counts of no-price, unknown-cost, currency-mismatch and proxy items are shown with a link to filter to them.
- **Given** cost basis in more than one currency **When** the summary shows **Then** each currency's cost basis is listed separately and never summed.
- **Given** the top movers section **When** it loads **Then** the 10 items with the highest P/L and the 10 with the lowest are listed.
- **Given** the value chart **When** the user picks 30, 90 or 365 days **Then** a Recharts line chart of total value (and cost basis as a second line) is shown for that range, with a table alternative.

**Tasks**
- [ ] T-9.5.4.1 (web) Valuation summary header with per-currency cost basis and filter links
- [ ] T-9.5.4.2 (web) Top movers lists
- [ ] T-9.5.4.3 (web) Recharts value-over-time chart with table toggle

#### S-9.5.5 — Mobile valuation summary

**As a** collector on mobile **I want** to see my collection's value, P/L and how its value has changed **so that** I can check it anywhere.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 3 | mobile | 3 | S-9.5.2, S-9.5.3, F-1.7, S-7.2.8 |

**Acceptance criteria**
- **Given** the mobile collection tab **When** it loads **Then** a header shows total value, cost basis and P/L with the snapshot date and currency.
- **Given** an item detail screen **When** it opens **Then** unit value, item value, cost basis and P/L for that item are shown, or "no price", "unknown cost", "currency mismatch" or "proxy" where they apply.
- **Given** a tap on the valuation header **When** the user picks 30, 90 or 365 days **Then** a victory-native line chart of total value (and cost basis as a second line) is shown for that range, with days that have no snapshot left out rather than filled in.
- **Given** a screen-reader user **When** they reach the value chart **Then** the first and last values in the range and the change between them are announced, and the full series is available as a list.

**Tasks**
- [ ] T-9.5.5.1 (mobile) Valuation header on the collection tab
- [ ] T-9.5.5.2 (mobile) Value and P/L on the item detail screen
- [ ] T-9.5.5.3 (mobile) Value-over-time screen with a victory-native line chart from the shared chart kit and a list alternative

#### S-9.5.6 — Choose my valuation currency

**As a** collector **I want** to pick USD or EUR as my currency **so that** my collection is valued with the prices of the market I buy in.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api, web, mobile | 3 | F-2.1, F-1.6, F-1.7 |

**Acceptance criteria**
- **Given** a user who has never chosen **When** values are computed **Then** the valuation currency is USD.
- **Given** the collection settings on web or mobile **When** the user picks EUR and saves **Then** every value, P/L and cost-to-complete figure is computed with Scryfall EUR prices from then on (decision G1).
- **Given** a currency other than USD or EUR **When** it is submitted **Then** the API returns 422.
- **Given** a currency change **When** it is saved **Then** no stored purchase amount or acquisition cost changes.

**Tasks**
- [ ] T-9.5.6.1 (api) Per-user collection settings with `valuation_currency` (USD/EUR) and Alembic migration, exposed through `collection/services`
- [ ] T-9.5.6.2 (web) Currency setting on the collection settings page
- [ ] T-9.5.6.3 (mobile) Currency setting on the collection tab settings
- [ ] T-9.5.6.4 (api) Tests: default, change, invalid value, stored amounts unchanged

#### S-9.5.7 — Value unopened sealed product

**As a** collector who keeps sealed product **I want** unopened product shown at what I paid, with a value I can enter myself **so that** sealed product is part of my collection's picture without an extra price source.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, web | 3 | S-9.3.3, S-9.5.2 |

**Acceptance criteria**
- **Given** a sealed line with 5 unopened units bought at 25.00 USD each **When** the sealed section loads **Then** it shows 5 units at a cost of 25.00 each, 125.00 in total (decision G7; no shipping or tax, decision G3 revised).
- **Given** an unopened sealed line **When** the user enters a unit value of 30.00 with today's date **Then** the value is stored in the user's valuation currency with that date, and the line shows value 150.00 and P/L +25.00.
- **Given** a sealed line with no value entered, or whose cost currency differs from the valuation currency **When** totals are computed **Then** its P/L is `null` with the reason "no value entered" or "currency mismatch".
- **Given** collection totals **When** they are shown **Then** sealed product has its own line (cost, value entered, P/L) and is never mixed into the singles' market value.
- **Given** any sealed line **When** it is valued **Then** no external price source is called.

**Tasks**
- [ ] T-9.5.7.1 (api) Manual unit value and value date on sealed lines, and Alembic migration
- [ ] T-9.5.7.2 (api) Sealed totals in the valuation service
- [ ] T-9.5.7.3 (web) Sealed section on the valuation page with an "Enter value" dialog
- [ ] T-9.5.7.4 (api) Tests: cost only, value entered, currency mismatch, opened units excluded

## F-9.6 — Deck ownership & cost to complete

For each deck (from F-4.4), show which cards are owned, proxied or missing, what it would cost to complete, and for any card, which decks use it.

Ownership counts the **exact printing**, and **each copy is assigned to one deck** (decision G4). A deck card is owned only when a non-proxy copy of the printing listed in the deck is assigned to it. Proxies can be assigned to show which slots they fill, but they count as not owned. Cost to complete prices the printing listed in the deck.

From Phase 3, a deck card uses the copy assigned to it, otherwise Scryfall's default printing (G4 replaces E3; X9). When a card is added to a deck without a printing (by import or in-app editing), an unassigned copy the owner holds is assigned to the deck and its printing is used; if there is none, Scryfall's default printing is used as before. Prices never decide which copy is chosen. See S-9.6.6 and S-9.6.7.

Phase 3 · Priority Should

#### S-9.6.1 — Owned, proxied and missing cards per deck

**As a** deck builder **I want** to see which cards in a deck I already own **so that** I know what I still need.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | S-9.1.1, F-4.4, S-9.6.7 |

**Acceptance criteria**
- **Given** a deck's current version with 100 cards, of which 80 have a non-proxy copy of the listed printing assigned **When** ownership is computed **Then** the response lists 80 owned and 20 not-owned cards, each with required, assigned-owned, proxy and missing quantity.
- **Given** a deck needing 10 Forests of one printing with 6 copies of that printing assigned **When** ownership is computed **Then** Forest shows required 10, owned 6, missing 4.
- **Given** a deck card listing printing P and an owned copy of another printing Q of the same card **When** ownership is computed **Then** the card is missing (exact printing, decision G4) and the response notes "you own another printing" with the copy's id.
- **Given** a deck card listing printing P and an unassigned copy of P **When** ownership is computed **Then** the card counts as missing until the copy is assigned, and the response notes "1 unassigned copy available".
- **Given** a copy of P assigned to another deck **When** ownership is computed for this deck **Then** it is not counted here; the response names the deck it is assigned to.
- **Given** a proxy assigned to a deck card **When** ownership is computed **Then** the card shows as "proxy" and is not counted as owned.
- **Given** a specific deck version (not the current one) **When** the user requests ownership for it **Then** that version's card list is used against the deck's current assignments.
- **Given** a playgroup member who can see the deck but is not its owner **When** they request ownership **Then** the API returns 403 unless `financials_visible_to(viewer_id, owner_id, "collection_value")` allows it, because the response carries values and cost to complete (decision C8, S-8.3.6).

**Tasks**
- [ ] T-9.6.1.1 (api) Ownership service comparing deck version cards (by exact printing) with assigned copies, through the decks domain's public service interface
- [ ] T-9.6.1.2 (api) Endpoint with a `version` parameter, owner-only
- [ ] T-9.6.1.3 (api) Tests for basics, multiples, other printing owned, unassigned copies, copies in other decks, proxies and an older version

#### S-9.6.2 — Deck value and cost to complete

**As a** deck builder **I want** the deck's total value, the value of what I own, and the cost of the missing cards **so that** I can budget for finishing it.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 3 | S-9.6.1, S-9.5.1 |

**Acceptance criteria**
- **Given** a deck with 20 missing cards that all have prices **When** cost to complete is computed **Then** it equals the sum of missing quantity × unit price of the printing listed in the deck, in the user's valuation currency, never a cheaper printing (decision G4).
- **Given** a deck card filled by a proxy **When** cost to complete is computed **Then** it is included in cost to complete, and the response also returns the "of which proxied" amount.
- **Given** a deck card with a recorded finish **When** it is priced **Then** that finish's price is used; with no recorded finish, the nonfoil price is used.
- **Given** missing cards with no price **When** cost to complete is computed **Then** they are listed separately as "no price" and their count is returned with the total.
- **Given** a deck **When** values are computed **Then** total deck value = owned part value (the assigned copies' values) + cost to complete, for cards that have prices.

**Tasks**
- [ ] T-9.6.2.1 (api) Price lookup for missing and proxied cards at the listed printing and finish
- [ ] T-9.6.2.2 (api) Value breakdown in the ownership response
- [ ] T-9.6.2.3 (api) Tests that the parts sum to the total, listed printing used, proxies included

#### S-9.6.3 — Which decks use this card

**As a** collector **I want** to see which of my decks use a card and where my copies are assigned **so that** I know where my copies are needed before I trade or sell them.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web | 3 | S-9.1.1, F-4.4, S-9.6.7 |

**Acceptance criteria**
- **Given** a card used in 3 of the user's decks **When** the user opens the card's collection detail **Then** the 3 decks are listed with deck name, commander, quantity and printing in each deck's current version, and the number of copies assigned to each.
- **Given** a card owned 2 times and needed by 3 decks **When** the detail is shown **Then** owned 2, assigned 2 and total needed 3 are visible, with the deck still missing a copy highlighted.
- **Given** a card that no deck uses **When** the detail is shown **Then** "Not used in any deck" is shown.

**Tasks**
- [ ] T-9.6.3.1 (api) "Decks using card" query by `oracle_id` over current deck versions with assignment counts, with eager loading
- [ ] T-9.6.3.2 (web) "Used in decks" section on the collection item detail
- [ ] T-9.6.3.3 (api) Tests including decks owned by other users being excluded

#### S-9.6.4 — Web deck ownership panel

**As a** deck builder on the web **I want** an ownership panel on the deck page **so that** I can see owned, proxied and missing cards and the cost to complete.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 3 | S-9.6.1, S-9.6.2, F-4.4 |

**Acceptance criteria**
- **Given** the deck page **When** the "Ownership" tab opens **Then** owned, proxy and missing counts, the owned value and the cost to complete are shown, with owned/proxy/missing card lists below.
- **Given** the missing list **When** the user clicks "Copy missing" **Then** a plain-text list (`<qty> <card name> (<set code>) <collector number>` per line, the listed printing) of missing cards is copied to the clipboard and a confirmation shows.
- **Given** a missing card with an unassigned copy available, or with another printing owned **When** the row shows **Then** it offers "Assign" or "Use my copy" (S-9.6.8).
- **Given** another member viewing the deck **When** the deck page loads **Then** no Ownership tab is shown, unless the owner shares collection value with that playgroup (S-8.3.6).

**Tasks**
- [ ] T-9.6.4.1 (web) Ownership tab with summary and lists
- [ ] T-9.6.4.2 (web) Copy-missing action
- [ ] T-9.6.4.3 (web) Vitest tests for summary rendering and copy format

#### S-9.6.5 — Mobile deck ownership

**As a** deck builder on mobile **I want** to see what I am missing for a deck **so that** I can shop for it in a store.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | mobile | 3 | S-9.6.1, S-9.6.2, F-1.7 |

**Acceptance criteria**
- **Given** a deck on mobile **When** the user opens "Missing cards" **Then** missing and proxied cards are listed with quantity, listed printing and current unit price, and the cost to complete is shown at the top.
- **Given** the missing list **When** the user taps a card and marks it "bought" **Then** the quick-add flow from S-9.1.4 opens pre-filled with the listed printing, and after saving the new copy is assigned to this deck and the card moves to owned.

**Tasks**
- [ ] T-9.6.5.1 (mobile) Missing cards screen
- [ ] T-9.6.5.2 (mobile) "Bought" shortcut into quick add, followed by assignment to the deck

#### S-9.6.6 — Assigned copies set the printing for deck cards

**As a** deck builder **I want** cards added to my decks without a printing to use a copy I own **so that** my decks show the cards I actually have.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | S-9.1.1, S-9.6.7, S-4.1.2, S-4.4.6 |

**Acceptance criteria**
- **Given** a deck owner with one unassigned non-proxy copy of Sol Ring (printing P) **When** a decklist line "1 Sol Ring" with no set or collector number is imported **Then** the deck card uses printing P and that copy is assigned to the deck (decision G4, X9).
- **Given** an owner with unassigned copies of three printings of a card **When** the card is added without a printing (by import or by in-app editing) **Then** the copy acquired earliest is assigned (ties broken by item id), prices are never consulted, and the choice is the same every time for the same data; the owner can swap it later (S-9.6.8).
- **Given** an owner whose copies of a card are all assigned to other decks, or who owns none **When** the card is added without a printing **Then** Scryfall's default printing is used and no copy is assigned, exactly as before Phase 3.
- **Given** an owner whose only unassigned copies of a card are proxies **When** the card is added without a printing **Then** no proxy is assigned automatically and Scryfall's default printing is used.
- **Given** a decklist line that names a set and collector number **When** it is imported **Then** that printing is kept; an unassigned copy of that exact printing is assigned if there is one, and copies of other printings are never assigned.
- **Given** existing deck versions created before this story ships or before the owner bought a card **When** the owner's collection changes **Then** those versions are not changed (a printing choice only applies when cards are added or a new version is saved).
- **Given** a deck added to or edited by its owner **When** printings are chosen **Then** only the deck owner's collection is consulted, never the collection of another playgroup member who can see the deck.

**Tasks**
- [ ] T-9.6.6.1 (api) `claim_copies_for_deck(user, deck, lines)` in the collection domain's public service interface: assigns an unassigned non-proxy copy per line (earliest acquired) and returns its printing, otherwise the card's `default_printing_id`
- [ ] T-9.6.6.2 (api) The decks domain's printing resolver (S-4.1.2, S-4.4.6) calls it explicitly when saving a version, through the public interface only
- [ ] T-9.6.6.3 (api) Tests: one unassigned copy, several printings, all copies assigned elsewhere, none owned, proxies only, explicit printing kept, existing versions unchanged

#### S-9.6.7 — Assign copies to decks

**As a** collector with several decks **I want** each physical copy assigned to exactly one deck **so that** I know which deck holds which card and never count one copy twice.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 3 | S-9.1.1, F-4.4, S-4.4.3 |

**Acceptance criteria**
- **Given** an item of printing P with quantity 3 and a deck whose current version lists P twice **When** the owner assigns 2 copies to the deck **Then** the assignment is stored and the item shows 2 assigned, 1 unassigned.
- **Given** an item whose copies are all assigned **When** the owner tries to assign another copy to a different deck **Then** the API returns 409 naming the decks holding the copies (decision G4).
- **Given** a deck card listing printing P **When** the owner tries to assign a copy of another printing **Then** the API returns 422 "printing differs"; the deck's printing must be changed first (S-9.6.8).
- **Given** a deck that needs 2 copies of P **When** the owner tries to assign a third **Then** the API returns 422.
- **Given** a copy assigned to deck A **When** the owner moves it to deck B **Then** it is unassigned from A and assigned to B in one transaction.
- **Given** a new deck version that drops a card or lowers its quantity **When** it is saved **Then** the surplus assignments are released by an explicit call from the decks service and listed in the save response as "copies released".
- **Given** a deck that is archived or deleted **When** the change is saved **Then** all its assignments are released.
- **Given** an item with assigned copies **When** the owner lowers its quantity below the assigned count or deletes it **Then** the API returns 409 listing the decks, unless the request names which assignments to release.
- **Given** a deck owned by another user **When** the owner tries to assign a copy to it **Then** the API returns 404.

**Tasks**
- [ ] T-9.6.7.1 (api) `CopyAssignment` model (collection item, deck, printing, quantity) with a check that assigned copies never exceed the item's quantity, and Alembic migration
- [ ] T-9.6.7.2 (api) Assign, move and release services in `collection/services`, plus `release_for_deck_version(deck, version)` called explicitly by the decks domain on version save, archive and delete
- [ ] T-9.6.7.3 (api) Endpoints for assign, move and release, owner-only
- [ ] T-9.6.7.4 (api) Tests: over-assignment, wrong printing, surplus, move, release on new version, archive, quantity guard, concurrency (two assignments of the last copy at once)

#### S-9.6.8 — Assign copies on web and mobile

**As a** deck builder **I want** to assign, move and swap copies from the deck page and the collection **so that** keeping assignments current is quick.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web, mobile | 3 | S-9.6.7, S-9.6.4, S-4.4.6, F-1.7 |

**Acceptance criteria**
- **Given** a missing card with unassigned copies of the listed printing **When** the owner clicks "Assign" **Then** a copy is assigned and the card moves to owned without a full reload.
- **Given** the ownership panel **When** the owner clicks "Assign all available" **Then** every missing card with an unassigned copy of its listed printing is assigned in one request, and a summary shows how many were assigned.
- **Given** a missing card for which the owner has a copy of another printing **When** they choose "Use my copy" and confirm **Then** a new deck version is saved with that printing (S-4.4.6) and the copy is assigned to it.
- **Given** a copy assigned to another deck **When** the owner chooses "Move here" **Then** a confirmation names the deck that will lose it, and after confirming the copy moves.
- **Given** a save of a new deck version that released copies **When** the save completes **Then** a notice lists the released copies.
- **Given** the mobile deck screen **When** the owner taps a missing card **Then** the same assign, use-my-copy and move-here actions are offered.
- **Given** a keyboard-only user on web **When** they use the ownership panel **Then** every action above is reachable without a mouse.

**Tasks**
- [ ] T-9.6.8.1 (web) Assign, assign-all, use-my-copy and move-here actions in the ownership panel
- [ ] T-9.6.8.2 (web) Released-copies notice after a deck version save
- [ ] T-9.6.8.3 (mobile) Assign actions on the missing-cards screen
- [ ] T-9.6.8.4 (web, mobile) Vitest and Jest tests for each action and the move confirmation

## Open questions

None open. Resolved: see OPEN-QUESTIONS.md decisions log.
