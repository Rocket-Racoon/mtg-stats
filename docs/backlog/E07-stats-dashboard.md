# E-7 — Stats & Dashboard

| | |
|---|---|
| **Phase(s)** | 1 (F-7.1, plus S-7.2.8, the mobile chart kit that F-4.6 needs) · 2 (F-7.2 – F-7.5) |
| **Goal** | Turn saved game results and the game event log into trustworthy, fast statistics: win rates, advanced breakdowns, game history with replay, card performance, and a per-user drag-and-drop dashboard on web and mobile. |
| **Success measures** | <ul><li>A completed game, or an admin's amendment to a saved result, is reflected in every stats endpoint within 10 s of being saved.</li><li>Every stats list endpoint responds in p95 < 300 ms on the 10,000-game performance fixture.</li><li>Every win-rate figure shown anywhere carries its sample size ("n games") and a small-sample flag (fewer than the playgroup's threshold, default 5 games).</li><li>The weekly full rebuild produces exactly the same fact rows as the incremental jobs.</li><li>Every drag interaction on the dashboard has a working tap or keyboard alternative.</li></ul> |
| **Out of scope** | Activity, streaks and meta shifts (E-8). Spend and collection value (E-9). League standings and matchmaking (E-10). Deck mana curve and color views (F-4.6; this epic only supplies the mobile chart kit, S-7.2.8). Season filter on stats views (tracked in E-10, decision F7). Predictive models or win-probability forecasts. |

Data sources: games and seats saved by F-6.7 (S-6.7.4: player, deck version, final seat order, placement, result, elimination cause, `no_winner`, win condition, round count, the life / commander-damage / poison thresholds the game used, start/end) and the game event log from F-6.3 (life, commander damage, poison, eliminations, turns), with corrections from F-6.6 (including mid-game seat changes) and result amendments by playgroup admins (S-6.7.6).

Working definitions used in this epic (decisions from [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) applied):
- **Counted game**: a game with status finished (completed) and a saved result. Lobbies, in-progress games and abandoned games (abandoned by the host or by the idle timeout) never count. A game the host resumes and then finishes counts normally. (D8)
- **Player**: a member or a guest. Guests are counted in every stat; when a guest is claimed or merged into an account, the affected stats are rebuilt (S-7.1.7). (C4)
- **Placement**: competition ranking. Players eliminated at the same time share a placement, and the next placement skips accordingly (1, 2, 3, 3). (D6, X1)
- **Win**: a seat with result `win`, i.e. placement 1 in a game with `no_winner=false`.
- **Draw**: a game with `no_winner=true` (a full draw). Every seat still in the game when it ended gets placement 1 with result `draw`; seats eliminated earlier are losses. A draw is never a win, and draws are always shown separately. "Draw" is not a win-condition category; the categories are exactly those of D5. (D6, X1)
- **Win rate**: wins ÷ counted games for the grouping, returned with `games`, `wins`, `draws` (shown separately), `win_rate` and a Wilson 95% interval.
- **Bracket at game time**: bracket stats use each seat's effective bracket as it stood when the game started, together with the rule-set version of that assessment, never a later re-assessment. (F2)
- **Days, weeks and time zone**: date filters and day buckets use the caller's time zone: their profile override if set, otherwise the playgroup's time zone (set by the owner). Weeks start on ISO Monday. (B8)
- **Former members and deleted players**: when a member leaves, their games stay in the playgroup's stats under their name, and they keep read-only access to those games from their own profile (C7, S-7.3.6). A deleted account's seats stay in stats and show as "Deleted player" (C9, anonymization owned by E-2).
- **Small sample**: a figure based on fewer games than the playgroup's threshold (default 5, configurable per playgroup) shows "n games" and a low-sample marker. (F1)
- **Rounds**: "turn count" in results and stats means rounds around the table. (D4)
- **Seat position**: the final seat order, after any mid-game seating fix by the host. (D9)
- **Custom thresholds**: a game whose recorded starting life, commander-damage or poison threshold differs from the standard 40 / 21 / 10. (D3)
- **Commander key**: the sorted set of commander `oracle_id`s of the deck version played, so a partner pair is one key. Stats are also available per individual partner, where a pair's seat counts once toward each partner (S-7.2.10, F3).

---

## F-7.1 — Basic stats (phase 1)

Win rate by player, deck and commander, filterable by playgroup and date range, backed by a precomputed per-seat fact table that the arq worker refreshes after each saved game or amendment, plus a weekly full rebuild (B5). Every figure shows its sample size and flags small samples against a per-playgroup threshold.

Phase 1 · Priority Must

#### S-7.1.1 — Per-seat stats facts refreshed after each saved game

**As a** playgroup member **I want** statistics to update automatically when a game is saved **so that** I never see stale or double-counted results.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 8 | api | 1 | S-6.7.4, S-6.7.6, F-1.9 |

**Acceptance criteria**
- **Given** a completed 4-player game is saved **When** the `refresh_game_stats` job runs **Then** exactly 4 rows exist in the seat fact table for that game, each holding playgroup, player, `is_guest`, deck, deck version, commander key, color identity, final seat order, pod size, placement, result (`win` | `loss` | `draw`), won flag, drew flag, the game's `no_winner` flag, round count, duration, game start and end time, and the starting life, commander-damage and poison thresholds the game recorded.
- **Given** a 4-player game with `no_winner=true` where 2 seats were eliminated earlier and the other 2 were still in when it ended **When** facts are built **Then** the 2 surviving seats have placement 1, result `draw`, `won=false` and `drew=true`, and the 2 eliminated seats have placements 3 and 4, result `loss`, `won=false` and `drew=false`. (X1)
- **Given** a 4-player game with a winner where the last 2 losers were eliminated at the same time **When** facts are built **Then** the placements are 1, 2, 3, 3 (competition ranking), only the placement-1 seat has `won=true`, and no seat has `drew=true`. (X1)
- **Given** the job has already run for a game **When** it runs again for the same game **Then** the fact rows are identical and no duplicates exist.
- **Given** a playgroup admin amends a saved result at any time after the game (S-6.7.6) **When** the amendment is saved **Then** the amendment service enqueues the refresh for that game and its fact rows are replaced within 10 s.
- **Given** the host moved players to different seats mid-game (F-6.6) **When** facts are built **Then** the seat order stored is the final order.
- **Given** a game that is abandoned (by the host or by the idle timeout) or still in progress **When** the job runs for it **Then** no fact rows exist for that game; **Given** the host resumes an abandoned game and finishes it **When** the result is saved **Then** its rows are created like any other game's.
- **Given** a seat played by a guest **When** facts are built **Then** the row is keyed by the guest's player id with `is_guest=true`, and the game counts like any other.
- **Given** the worker is stopped **When** a game result is saved **Then** the save succeeds and the queued job runs once the worker restarts, and the `rebuild-stats` command produces the same rows as the incremental jobs.

**Tasks**
- [ ] T-7.1.1.1 (api) `stats` domain: seat fact model and Alembic migration with indexes on (playgroup_id, ended_at), player_id, deck_id and commander_key
- [ ] T-7.1.1.2 (api) `stats.services.refresh_game_facts(game_id)`: delete-and-insert in one transaction, idempotent
- [ ] T-7.1.1.3 (api) Enqueue the arq job from the results service and the result-amendment service with explicit calls (no signals or ORM event hooks)
- [ ] T-7.1.1.4 (api) `rebuild-stats` CLI command that recomputes all facts in batches
- [ ] T-7.1.1.5 (api) Tests: idempotency, correction replacement, excluded statuses, rebuild equals incremental
- [ ] T-7.1.1.6 (api) Tests: competition-ranked shared placements, a full draw with earlier eliminations, an amendment long after the game, a resumed abandoned game, final seat order after a mid-game seating fix, guest seats

#### S-7.1.2 — Win-rate API by player, deck and commander

**As a** playgroup member **I want** win rates grouped by player, deck or commander **so that** I can see who and what is winning.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, shared | 1 | S-7.1.1, F-2.4, F-2.2 |

**Acceptance criteria**
- **Given** 10 counted games in playgroup P where player A won 3 **When** a member calls `GET /stats/win-rates?group_by=player&playgroup_id=P` **Then** A's row shows `games=10`, `wins=3`, `draws=0`, `win_rate=0.30`.
- **Given** A also played 1 counted game with `no_winner=true` in which A was still in at the end (result `draw`) **When** the same request runs **Then** A's row shows `games=11`, `wins=3`, `draws=1` and `win_rate` 3/11; the draw is not counted as a win.
- **Given** a guest G seated in 4 counted games in P **When** grouping by `player` **Then** G has its own row with `is_guest=true`.
- **Given** member X has left P (or was removed) **When** grouping by `player` in P **Then** X's games still count and X's row keeps X's name with `is_former_member=true`; **Given** a deleted account's seats **Then** they are grouped under "Deleted player". (C7, C9)
- **Given** a deck played in two versions across 6 games **When** grouping by `deck` **Then** one row covers all 6 games; **When** grouping by `commander` **Then** a partner pair appears as one row keyed by both commanders.
- **Given** `from=2026-03-01&to=2026-03-31` **When** the request runs **Then** only games that ended within that range (inclusive, by end date) are counted, with end dates taken in the caller's time zone (profile override, otherwise P's time zone, B8) and the time zone used stated in the response.
- **Given** no `playgroup_id` **When** the request runs **Then** only games from playgroups the caller belongs to are counted; **Given** a playgroup the caller is not a member of **Then** the response is 403.
- **Given** more rows than the page size (default 50, maximum 200) **When** the request runs **Then** the response returns a `next_cursor` (keyset on the sort value and row id) and there is no offset parameter. (B2)
- **Given** the 10,000-game performance fixture **When** any grouping is requested **Then** p95 response time is under 300 ms.

**Tasks**
- [ ] T-7.1.2.1 (api) Repository query builders for the three groupings, with filters and sort (`win_rate`, `games`, `name`)
- [ ] T-7.1.2.2 (api) Pydantic schemas and thin router; membership check through the playgroups service interface
- [ ] T-7.1.2.3 (api) factory_boy performance fixture (10,000 games, 2–8 seats) and a timing test marked `slow`
- [ ] T-7.1.2.4 (shared) Regenerate `packages/api-client` types
- [ ] T-7.1.2.5 (api) Cursor (keyset) paging on the sorted rows, with tests for ties in the sort value
- [ ] T-7.1.2.6 (api) Resolve the caller's time zone (profile override, then playgroup `time_zone`) through the players and playgroups service interfaces; former-member flag; tests for time-zone date boundaries and a member who left

#### S-7.1.3 — Sample-size rules and small-sample flags

**As a** playgroup member **I want** every win rate to show how many games it is based on and to warn me about small samples **so that** I don't read too much into three games.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-7.1.2 |

**Acceptance criteria**
- **Given** any win-rate row **When** it is returned **Then** it includes `games`, `wins`, `draws`, `win_rate`, `ci_low` and `ci_high` (Wilson 95% interval, computed with the standard library).
- **Given** a playgroup that has not changed its threshold **When** a row has fewer than 5 games **Then** it has `low_sample=true`, and rows with 5 or more games have `low_sample=false`.
- **Given** playgroup P's threshold is set to N games (S-7.1.8) **When** a row in P has fewer than N games **Then** it has `low_sample=true`, and the response states `low_sample_threshold=N`.
- **Given** a request that covers several playgroups with different thresholds **When** rows are flagged **Then** the highest of those thresholds is used and returned as `low_sample_threshold`.
- **Given** `min_games=5` **When** the request runs **Then** rows with fewer than 5 games are left out and the response reports how many rows were hidden.
- **Given** a 2-player row with 1 game and 1 win **When** returned **Then** `win_rate=1.0`, `low_sample=true` and the interval spans at least 0.2–1.0.

**Tasks**
- [ ] T-7.1.3.1 (api) Read each playgroup's small-sample threshold through the playgroups service interface (default 5 when unset), and the Wilson interval helper
- [ ] T-7.1.3.2 (api) `min_games` filter and `hidden_rows` count in the response envelope
- [ ] T-7.1.3.3 (api) Unit tests for the interval at 0, 1, N−1, N and large n

#### S-7.1.4 — Web basic stats page

**As a** playgroup member on the web **I want** a stats page with player, deck and commander tables **so that** I can compare win rates at a glance.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 1 | S-7.1.2, S-7.1.3, F-1.6 |

**Acceptance criteria**
- **Given** the stats page **When** it loads **Then** it shows Player, Deck and Commander tabs, each with columns for name, games ("n games"), wins, draws and win rate; guests are shown with a "Guest" label and members who left with a "Former member" label.
- **Given** the filter bar **When** the user picks a playgroup and a date preset (last 30 days, last 90 days, this year, all time) or a custom range **Then** the table refreshes and the filters are kept in the URL so a reload shows the same view.
- **Given** a row with `low_sample=true` **When** it is shown **Then** it has a visible "Small sample" badge and muted styling, and the badge has an accessible label that includes "n games".
- **Given** a keyboard-only user **When** they tab to a column header and press Enter **Then** the table sorts by that column.
- **Given** the response has a `next_cursor` **When** the user activates "Load more" **Then** the next page is appended to the table.
- **Given** a filter with no counted games **When** applied **Then** an empty state explains that no games match.

**Tasks**
- [ ] T-7.1.4.1 (web) Route, TanStack Query hooks and filter bar with URL state
- [ ] T-7.1.4.2 (web) Sortable stats table with sample-size and draws columns, guest label, small-sample badge and "Load more" (infinite query)
- [ ] T-7.1.4.3 (web) Vitest tests for filters, sorting, paging and the badge

#### S-7.1.5 — Mobile basic stats screen

**As a** playgroup member on my phone **I want** the same win-rate tables **so that** I can check stats at the table.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | mobile | 1 | S-7.1.2, S-7.1.3, F-1.7 |

**Acceptance criteria**
- **Given** the stats screen **When** it opens **Then** it shows Player, Deck and Commander segments, each listing name, "n games", draws (when there are any) and win rate; guests are shown with a "Guest" label and members who left with a "Former member" label.
- **Given** the filter sheet **When** the user selects a playgroup and a date preset **Then** the list refreshes and the filter stays applied while the app is open.
- **Given** a row with `low_sample=true` **When** it is shown **Then** it has a "Small sample" badge that screen readers announce.
- **Given** the user scrolls to the end of the list and the response has a `next_cursor` **When** the end is reached **Then** the next page loads.
- **Given** the user pulls down on the list **When** released **Then** the data is refetched.

**Tasks**
- [ ] T-7.1.5.1 (mobile) Stats screen with segmented control and TanStack Query infinite-query hooks
- [ ] T-7.1.5.2 (mobile) Filter sheet (playgroup, date preset)
- [ ] T-7.1.5.3 (mobile) Jest tests for list rendering, paging and the small-sample badge

#### S-7.1.6 — Weekly full stats rebuild

**As a** playgroup member **I want** all stats rebuilt from the saved results every week **so that** a missed or failed refresh job can never leave stats wrong for long.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 1 | S-7.1.1, F-1.9 |

**Acceptance criteria**
- **Given** the arq cron schedule **When** the weekly slot arrives (configurable, default Monday 04:00 UTC) **Then** the `rebuild_stats` job recomputes every stats fact table in batches from the saved results.
- **Given** the incremental facts drifted (for example a refresh job was lost) **When** the weekly rebuild finishes **Then** the facts equal a clean rebuild and the job logs how many rows it changed.
- **Given** a result is saved or amended while the rebuild is running **When** both finish **Then** that game's facts reflect the latest result (no lost update).
- **Given** a rebuild is already running **When** the next slot arrives or `rebuild-stats` is started by hand **Then** the second run does not start and logs why.

**Tasks**
- [ ] T-7.1.6.1 (api) arq cron job that calls the same rebuild service as the `rebuild-stats` command
- [ ] T-7.1.6.2 (api) Redis lock against concurrent rebuilds; per-game refreshes queued during a rebuild run after it
- [ ] T-7.1.6.3 (api) Tests: drift is repaired, concurrent save during rebuild, lock prevents a second run

#### S-7.1.7 — Rebuild stats when a guest is claimed or merged

**As a** player who used to play as a guest **I want** my guest games added to my stats as soon as my account claims them **so that** my record is complete straight away.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 1 | S-7.1.1, S-2.3.3, S-2.3.4 |

**Acceptance criteria**
- **Given** guest G with 12 counted games is merged into member M (S-2.3.4) **When** the merge commits **Then** the merge service calls the stats service to rebuild those 12 games, and within 60 s M's win rates and head-to-head include them and no fact row refers to G.
- **Given** a guest claims a new account at registration (S-2.3.3, same player id) **When** the claim completes **Then** the fact rows for G's games are rebuilt with `is_guest=false`.
- **Given** the rebuild job fails partway **When** it is retried **Then** the result is the same as a single successful run.
- **Given** the merge is rejected (for example G and M sat in the same game) **When** it fails **Then** no stats rebuild is enqueued.

**Tasks**
- [ ] T-7.1.7.1 (api) `stats.services.rebuild_games_for_players(player_ids)` in the stats public interface, enqueued as an arq job
- [ ] T-7.1.7.2 (api) Explicit call from the guest claim and merge services after commit (coordinate with E-2; no signals)
- [ ] T-7.1.7.3 (api) Tests: merge, claim at registration, retry, rejected merge

#### S-7.1.8 — Set the small-sample threshold per playgroup

**As a** playgroup admin **I want** to set how many games make a win rate reliable **so that** the small-sample marker suits how often our group plays.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, web, mobile | 1 | S-7.1.3, S-7.1.4, S-7.1.5, S-2.4.7 |

**Acceptance criteria**
- **Given** a new playgroup **When** stats read its small-sample threshold **Then** it is 5 games.
- **Given** the owner or an admin of P **When** they set the threshold to a whole number from 1 to 50 **Then** it is saved and the next stats response for P uses it; any other value returns 422.
- **Given** a member who is not owner or admin **When** they try to change it **Then** the response is 403, but they can see the current value.
- **Given** the stats page (web) or stats screen (mobile) **When** a playgroup is selected **Then** an info hint says "Small sample: fewer than N games", and owners and admins get a link to change it.

**Tasks**
- [ ] T-7.1.8.1 (api) Read and write `low_sample_games` through E-2's `set_low_sample_games` / playgroup settings service (S-2.4.7 owns the column and migration)
- [ ] T-7.1.8.2 (api) `PATCH /playgroups/{id}/stats-settings` with owner/admin check and tests
- [ ] T-7.1.8.3 (web) Setting control in playgroup settings and the info hint on the stats page
- [ ] T-7.1.8.4 (mobile) Setting control in playgroup settings and the info hint on the stats screen
- [ ] T-7.1.8.5 (web, mobile) Vitest/Jest tests for the control and the hint

---

## F-7.2 — Advanced stats

Breakdowns beyond the basics: win rate by seat position, pod size and color identity, head-to-head records, win conditions, game length and rounds, and bracket vs. outcome. All are served from the precomputed facts with the same sample-size rules as F-7.1, drawn as charts on web (Recharts) and mobile (victory-native, A16), and can be filtered to games played with standard or custom thresholds. Commander stats are also available per individual partner (F3).

Phase 2 · Priority Should (except S-7.2.8, the mobile chart kit, which is phase 1 because the mobile mana curve in F-4.6 needs it, X3)

#### S-7.2.1 — Win rate by seat position and pod size

**As a** playgroup member **I want** win rate by seat position and by pod size **so that** I can see whether going first or playing in bigger pods matters.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api | 2 | S-7.1.1, S-7.1.3 |

**Acceptance criteria**
- **Given** 20 counted 4-player games where seat 1 won 8 **When** calling `group_by=seat_position&pod_size=4` **Then** seat 1 shows `games=20`, `wins=8`, `win_rate=0.40` and `expected=0.25`.
- **Given** no `pod_size` filter **When** grouping by seat position **Then** rows are returned per (pod size, seat) pair, never mixing pod sizes in one row.
- **Given** a game where the host changed seating mid-game **When** it is counted by seat position **Then** each player counts in their final seat.
- **Given** `group_by=pod_size` **When** the request runs **Then** one row per pod size from 2 to 8 that has games, each with `expected = 1/pod_size`.
- **Given** any row **When** returned **Then** it carries the same `draws`, `low_sample` and interval fields as S-7.1.3; draws count as not a win.

**Tasks**
- [ ] T-7.2.1.1 (api) Seat-position and pod-size query builders with expected baseline
- [ ] T-7.2.1.2 (api) Tests covering mixed pod sizes, 2- and 8-player pods, final seat order and draws

#### S-7.2.2 — Win rate by color identity

**As a** playgroup member **I want** win rate by color identity **so that** I can see which colors perform in our meta.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api | 2 | S-7.1.1, S-7.1.3 |

**Acceptance criteria**
- **Given** a seat whose commanders have color identity W, U and B **When** facts are built **Then** its identity is stored in WUBRG order as `WUB`, and a colorless commander is stored as `C`.
- **Given** `group_by=color_identity&mode=exact` **When** the request runs **Then** each of the up to 32 identities with games has its own row.
- **Given** `mode=per_color` **When** the request runs **Then** the `WUB` seat counts once toward each of W, U and B, and the response states that rows overlap.
- **Given** a partner pair W + R **When** facts are built **Then** the identity is the union, `WR`.

**Tasks**
- [ ] T-7.2.2.1 (api) Color identity column in seat facts, filled from the card data of the commanders
- [ ] T-7.2.2.2 (api) Exact and per-color query builders and tests

#### S-7.2.3 — Head-to-head records

**As a** player **I want** my record against each opponent **so that** I know who I beat and who beats me.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api | 2 | S-7.1.1, F-6.3 |

**Acceptance criteria**
- **Given** players A and B shared 6 counted games and A placed better in 4 **When** calling `GET /stats/head-to-head?player_id=A` **Then** B's row shows `shared_games=6`, `ahead=4`, `behind=2`, `tied=0`.
- **Given** A and B shared a placement (eliminated at the same time, or both in a draw) **When** the game is compared **Then** it counts in `tied`, not in `ahead` or `behind`.
- **Given** the event log records A eliminating B twice **When** the row is returned **Then** it shows `a_eliminated_b=2`; eliminations with no attributable player (for example a concession) count toward neither side.
- **Given** `GET /stats/head-to-head/matrix?playgroup_id=P` **When** called **Then** it returns every pair of players (guests included, marked `is_guest`) with at least 1 shared game, and each cell carries `shared_games` and `low_sample`.
- **Given** an admin amends a game's placements (D7) **When** the refresh job has run **Then** the head-to-head rows reflect the amended placements.
- **Given** a playgroup the caller is not a member of **When** requested **Then** the response is 403.

**Tasks**
- [ ] T-7.2.3.1 (api) Elimination fact table (game, eliminated player, eliminator, cause, round), filled by the refresh job from elimination events, with reverted events ignored
- [ ] T-7.2.3.2 (api) Pairwise query on seat facts (self-join on game) and matrix endpoint
- [ ] T-7.2.3.3 (api) Tests for placement comparison, shared placements, unattributed eliminations, reverted elimination events and amended results

#### S-7.2.4 — Win conditions, game length and turns

**As a** playgroup member **I want** to see how games are won and how long they take **so that** we understand our pace and threats.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api | 2 | S-7.1.1, S-6.7.4 |

**Acceptance criteria**
- **Given** 10 counted games with 6 combat-damage wins and 4 combo wins **When** calling `GET /stats/win-conditions?playgroup_id=P` **Then** it returns counts 6 and 4 with shares 0.6 and 0.4; the same endpoint filters by player or deck.
- **Given** any request **When** win conditions are returned **Then** there is one row for each category, including those with 0 games: combat damage, commander damage, combo, alternate-win card, poison, mill/decking, life drain/burn, concession, last standing, and other. (D5)
- **Given** games won by "other" **When** the row is returned **Then** it includes the notes recorded with those games (most recent 10).
- **Given** counted games with `no_winner=true` (full draws) **When** win conditions are computed **Then** they are not in any category ("Draw" is not a win condition, X1) and are counted in a separate `no_winner_games` field, so category counts plus `no_winner_games` equal the counted games.
- **Given** counted games **When** calling `GET /stats/game-length` **Then** it returns average and median duration in minutes and average rounds, overall and per pod size.
- **Given** a game with `turn_passed` events **When** facts are built **Then** its average turn duration is the mean gap between consecutive `turn_passed` events, ignoring reverted events.
- **Given** a counted game with no end time or no round count **When** aggregates are computed **Then** it is left out of that metric and counted in an `excluded` field.

**Tasks**
- [ ] T-7.2.4.1 (api) Turn-duration calculation in the refresh job from the event log
- [ ] T-7.2.4.2 (api) Win-condition (fixed category enum shared with F-6.7) and game-length query builders, schemas and router
- [ ] T-7.2.4.3 (api) Tests including excluded games, zero-count categories, "other" notes and draws

#### S-7.2.5 — Bracket vs. outcome

**As a** playgroup member **I want** to compare win rate with deck bracket **so that** we can see whether bracket mismatches decide games.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api | 2 | S-7.1.1, F-5.3, S-5.3.3, S-6.2.10 |

**Acceptance criteria**
- **Given** a seat whose deck version had a bracket assessment when the game started (F-5.3) **When** facts are built **Then** the seat records the `effective_bracket` of the assessment current at the game's start time, whether it was assumed (E8), and that assessment's rule-set version; a version with no assessment at that time records `unassessed`. (F2)
- **Given** the deck version is re-assessed after the game (a new rule-set version, or the owner declaring intent) **When** facts are rebuilt (refresh, amendment or the weekly rebuild) **Then** the seat still records the bracket and rule-set version from game start, so stats for past games never change because of a later re-assessment. (F2)
- **Given** `group_by=bracket` **When** called **Then** it returns rows for brackets 1–5 and `unassessed`, with the usual sample fields, and each row lists the rule-set versions it covers; `rules_version` filters to games assessed under one version.
- **Given** a bracket 4 deck in a pod with three bracket 2 decks **When** facts are built **Then** its bracket gap is +2 (its bracket minus the mean of the other seats' brackets at game start, rounded to the nearest whole number, halves away from zero).
- **Given** a pod where any seat is unassessed **When** gap stats are computed **Then** that game is left out of gap stats and counted in `excluded`.

**Tasks**
- [ ] T-7.2.5.1 (api) Read the assessment current at a given time (`assessment_as_of(deck_version_id, at)`) through the bracket domain's public service interface, not its models (coordinate with E-5, which keeps every assessment with its run time and rule-set version)
- [ ] T-7.2.5.2 (api) Bracket-at-start, assumed flag, rule-set version and bracket-gap columns in seat facts; query builders by bracket, rule-set version and gap
- [ ] T-7.2.5.3 (api) Tests for unassessed seats, gap rounding, an assumed Bracket 2 deck, and a re-assessment after the game that leaves the facts unchanged

#### S-7.2.6 — Web advanced stats views

**As a** playgroup member on the web **I want** charts for the advanced breakdowns **so that** patterns are easy to spot.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | web | 2 | S-7.2.1, S-7.2.2, S-7.2.3, S-7.2.4, S-7.2.5 |

**Acceptance criteria**
- **Given** the advanced stats page **When** it loads **Then** it shows Recharts charts for seat position (with the expected baseline as a reference line), pod size, color identity, win conditions (all 10 categories, with the number of games without a winner shown beside the chart), game length and rounds, and bracket, plus a head-to-head matrix table.
- **Given** any chart **When** the user toggles "Show as table" **Then** the same data appears in an accessible table, including "n games" for each bar.
- **Given** a bar or matrix cell with `low_sample=true` **When** shown **Then** it is visually de-emphasised and its tooltip says "Small sample: n games".
- **Given** the shared filter bar from S-7.1.4 **When** filters change **Then** every chart refetches with the same playgroup and date range.

**Tasks**
- [ ] T-7.2.6.1 (web) Chart components per breakdown with Recharts, plus a table toggle
- [ ] T-7.2.6.2 (web) Head-to-head matrix with keyboard-navigable cells
- [ ] T-7.2.6.3 (web) Vitest tests for table toggle and small-sample styling

#### S-7.2.7 — Mobile advanced stats views

**As a** playgroup member on my phone **I want** the advanced breakdowns **so that** I can settle arguments at the table.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | mobile | 2 | S-7.2.1, S-7.2.2, S-7.2.3, S-7.2.4, S-7.2.5, S-7.2.8 |

**Acceptance criteria**
- **Given** the advanced stats screen **When** it opens **Then** each breakdown (seat, pod size, color identity, head-to-head, win conditions, game length, bracket) is available as a collapsible section.
- **Given** the seat position, pod size, color identity, win conditions, game length and bracket sections **When** expanded **Then** each shows a victory-native bar chart from the S-7.2.8 kit (seat position with the expected baseline as a reference line), and a "Show as list" toggle lists the same values with "n games" and the small-sample badge.
- **Given** a bar with `low_sample=true` **When** shown **Then** it is visually de-emphasised, and tapping it shows "Small sample: n games".
- **Given** the head-to-head section **When** shown **Then** it is a list (no chart), and tapping an opponent shows the full record (shared games, ahead, behind, tied, eliminations each way).

**Tasks**
- [ ] T-7.2.7.1 (mobile) Advanced stats screen with collapsible sections and shared filter sheet
- [ ] T-7.2.7.2 (mobile) Head-to-head opponent detail view
- [ ] T-7.2.7.3 (mobile) Jest tests for section rendering
- [ ] T-7.2.7.4 (mobile) Breakdown charts built on the S-7.2.8 chart kit, each with its list toggle

#### S-7.2.8 — Mobile chart kit with victory-native

**As a** mobile user **I want** stats and deck views drawn as clear, accessible charts on my phone **so that** I can spot patterns as easily as on the web.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | mobile | 1 | F-1.7 |

**Acceptance criteria**
- **Given** victory-native and @shopify/react-native-skia (approved in A16) **When** the mobile app is built for iOS and Android **Then** the kit's bar chart (with an optional reference line), line chart and 100% stacked bar chart render with the app's light and dark theme.
- **Given** rows that carry `games` and `low_sample` **When** a chart draws them **Then** low-sample bars or points are de-emphasised, and tapping one shows its value and "n games".
- **Given** any kit chart **When** a screen-reader user focuses it **Then** it announces a summary (title and the top values), and a "Show as list" toggle shows the same data as an accessible list; color is never the only cue.
- **Given** a chart with no data **When** rendered **Then** it shows an empty message instead of empty axes.
- **Given** the kit is in phase 1 (X3) **When** E-4's mobile mana curve and color charts (F-4.6) are built **Then** they use the kit's bar and 100% stacked bar charts and need nothing from the phase 2 stats endpoints; `games` and `low_sample` are optional props.

**Tasks**
- [ ] T-7.2.8.1 (mobile) Add victory-native and @shopify/react-native-skia to `apps/mobile` and confirm Expo build config
- [ ] T-7.2.8.2 (mobile) Bar, line and 100% stacked bar wrappers with theme tokens and tap tooltips
- [ ] T-7.2.8.3 (mobile) List toggle, accessibility summary, low-sample styling and empty state
- [ ] T-7.2.8.4 (mobile) Jest tests with Skia mocked: list toggle, summary label, low-sample styling

#### S-7.2.9 — Mark and filter games played with custom thresholds

**As a** playgroup member **I want** to see which games used non-standard life, commander-damage or poison thresholds and filter them **so that** house-rule games don't skew comparisons.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, web, mobile | 2 | S-7.1.1, S-7.1.4, S-7.1.5, S-7.3.1, S-7.3.2 |

**Acceptance criteria**
- **Given** a game whose recorded thresholds differ from 40 life, 21 commander damage or 10 poison **When** facts are built **Then** its rows have `custom_thresholds=true`; otherwise `false`.
- **Given** `thresholds=standard`, `custom` or `all` (default `all`) on any F-7.1 or F-7.2 stats endpoint or on the game history list **When** the request runs **Then** only matching games are counted or listed.
- **Given** the stats filter bar (web) or filter sheet (mobile) **When** the user picks "Standard thresholds only" **Then** every stats view refetches with that filter; on web the filter is kept in the URL.
- **Given** a game with custom thresholds in the history list (web and mobile) **When** shown **Then** it has a "House thresholds" marker listing the values used (for example "30 life · 21 cmdr · 10 poison") with an accessible label.

**Tasks**
- [ ] T-7.2.9.1 (api) `custom_thresholds` flag computed in the refresh job from the thresholds stored in S-7.1.1
- [ ] T-7.2.9.2 (api) Shared `thresholds` filter in the stats query builders and the history query, with tests
- [ ] T-7.2.9.3 (web) Filter control and "House thresholds" marker
- [ ] T-7.2.9.4 (mobile) Filter option and "House thresholds" marker
- [ ] T-7.2.9.5 (web, mobile) Vitest/Jest tests for the filter and the marker label

#### S-7.2.10 — Stats per individual partner commander

**As a** playgroup member **I want** commander stats for each partner on its own as well as for the pair **so that** I can see how a partner performs across the different pairings it is played in.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, web, mobile | 2 | S-7.1.2, S-7.1.4, S-7.1.5 |

**Acceptance criteria**
- **Given** `GET /stats/win-rates?group_by=commander&commander_mode=individual` **When** the request runs **Then** a seat whose deck has two commanders (partners, a background, or a companion-style pairing) counts once toward each commander's row, single-commander seats count toward their commander as usual, and the response states that rows overlap. (F3)
- **Given** `commander_mode=pair` (the default) **When** the request runs **Then** the result is unchanged from S-7.1.2: one row per commander key.
- **Given** partner P appears in 3 counted games with partner Q and 2 with partner R, winning 2 of the 5 **When** grouped individually **Then** P's row shows `games=5`, `wins=2`, and the row offers a breakdown by pairing (P + Q: 3 games, P + R: 2 games) with the usual sample fields.
- **Given** the Commander tab (web) or segment (mobile) **When** the user switches "Pairs / Individual commanders" **Then** the table refetches in that mode; on web the choice is kept in the URL.

**Tasks**
- [ ] T-7.2.10.1 (api) `commander_mode` on the commander grouping (unnest the commander key) and the per-pairing breakdown, with tests including overlap and single-commander decks
- [ ] T-7.2.10.2 (web) Pairs/Individual toggle in the Commander tab
- [ ] T-7.2.10.3 (mobile) Pairs/Individual toggle in the Commander segment
- [ ] T-7.2.10.4 (web, mobile) Vitest/Jest tests for the toggle

---

## F-7.3 — Game history & replay

A filterable list of past games and an event-timeline replay of a single game, rebuilt from the game event log with corrections applied. Players who leave a playgroup keep read-only access to the games they played there (C7).

Phase 2 · Priority Should

#### S-7.3.1 — Game history list API

**As a** playgroup member **I want** a list of past games **so that** I can look up any game we played.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api | 2 | S-6.7.4, S-6.7.6, F-2.4 |

**Acceptance criteria**
- **Given** 60 completed games **When** calling `GET /games/history?playgroup_id=P` **Then** it returns 25 games per page, newest first, with a cursor for the next page (keyset paging; no offset parameter).
- **Given** each item **When** returned **Then** it includes date (in the caller's time zone, B8), duration, rounds, pod size, `no_winner`, win condition and note (null when `no_winner=true`), the life / commander-damage / poison thresholds used, whether the result was amended (S-6.7.6), and every seat's player (with `is_guest` and `is_former_member`), deck, commander(s), placement (competition ranking, shared placements allowed) and result (`win`, `loss` or `draw`). (X1)
- **Given** abandoned games **When** the history is listed **Then** they are not included.
- **Given** filters for player, deck, commander and date range **When** applied **Then** only matching games are returned.
- **Given** any page size up to 100 **When** the request runs **Then** the number of SQL queries stays constant (at most 4) thanks to `selectinload`/joined loading.
- **Given** a playgroup the caller is not a member of **When** requested **Then** the response is 403.

**Tasks**
- [ ] T-7.3.1.1 (api) History query builder with eager loading and cursor pagination
- [ ] T-7.3.1.2 (api) Schemas, router and query-count test

#### S-7.3.2 — Game history list on web and mobile

**As a** playgroup member **I want** to browse and filter past games on web and phone **so that** I can find a game and open its replay.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | web, mobile | 2 | S-7.3.1 |

**Acceptance criteria**
- **Given** the history view **When** it loads **Then** each game shows date, winner (or "Draw" with the drawn players when `no_winner` is true), commanders, pod size and rounds, plus an "Amended" marker when an admin changed the result.
- **Given** the end of the loaded list **When** the user scrolls there or activates the "Load more" button **Then** the next page loads.
- **Given** filters for player, deck, commander and date range **When** applied **Then** the list refreshes; on web the filters are kept in the URL.
- **Given** a game in the list **When** the user clicks, taps or presses Enter on it **Then** its replay opens.

**Tasks**
- [ ] T-7.3.2.1 (web) History page with TanStack Query infinite query and URL filters
- [ ] T-7.3.2.2 (mobile) History screen with infinite list and filter sheet
- [ ] T-7.3.2.3 (web, mobile) Vitest/Jest tests for pagination and navigation to replay

#### S-7.3.3 — Game timeline API

**As a** playgroup member **I want** the full event timeline of a finished game **so that** clients can replay it step by step.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api | 2 | F-6.1, F-6.3, F-6.6, S-6.7.6 |

**Acceptance criteria**
- **Given** a completed game **When** calling `GET /games/{id}/timeline` **Then** it returns events in sequence order with sequence number, round, turn, actor, type, payload and timestamp, plus the thresholds the game used.
- **Given** an event that was later reverted (F-6.6) **When** returned **Then** it is flagged `reverted=true` with the id of the compensating event, and it does not change the derived state.
- **Given** the host fixed seating mid-game **When** the timeline is returned **Then** the seating correction appears as its own event and later steps use the new seat order.
- **Given** the timeline **When** derived states are requested (`with_states=true`) **Then** each step includes life, commander damage, poison and eliminated status per seat, computed by the Python game reducer, and the last step matches the result confirmed at the end of the game.
- **Given** an admin later amended the result (D7) **When** the timeline is returned **Then** it includes an `amendments` list (who, when, what changed) and the amended final result, separate from the replayed steps.
- **Given** a game still in progress **When** requested **Then** the response is 409.
- **Given** a game with 2,000 events **When** requested with states **Then** the response arrives in under 1 s.

**Tasks**
- [ ] T-7.3.3.1 (api) Timeline service that replays events through the F-6.1 Python reducer
- [ ] T-7.3.3.2 (api) Schemas and router with membership check
- [ ] T-7.3.3.3 (api) Test: the final derived state equals the saved result for every game in the test vectors
- [ ] T-7.3.3.4 (api) Read result amendments through the games service interface; tests for an amended game and a mid-game seating fix

#### S-7.3.4 — Web replay timeline

**As a** playgroup member on the web **I want** to step through a past game **so that** I can see how it unfolded.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | web | 2 | S-7.3.3 |

**Acceptance criteria**
- **Given** the replay view **When** it opens **Then** it shows a read-only table of seats with life, commander damage and poison at the current step, starting at the first event.
- **Given** the timeline scrubber **When** the user drags it **Then** the table updates to that step; **Given** a keyboard user **When** they press Left/Right **Then** it moves one event, and Page Up/Page Down moves one round.
- **Given** the "Jump to round" control **When** the user picks round 7 **Then** the view moves to the first event of round 7.
- **Given** the life chart **When** shown **Then** a Recharts line chart plots each player's life by round, with a matching table alternative.
- **Given** "Show reverted events" is on **When** the list is shown **Then** reverted events appear struck through; when off they are hidden.
- **Given** a game whose result was amended **When** the replay opens **Then** a banner says who amended it and when, and shows the amended result.

**Tasks**
- [ ] T-7.3.4.1 (web) Replay route with scrubber, step buttons and keyboard shortcuts
- [ ] T-7.3.4.2 (web) Read-only seat state panel (reuse F-6.4 display components where possible)
- [ ] T-7.3.4.3 (web) Life-by-round chart, event list with reverted toggle, and amendment banner
- [ ] T-7.3.4.4 (web) Vitest tests for stepping and keyboard controls

#### S-7.3.5 — Mobile replay timeline

**As a** playgroup member on my phone **I want** to step through a past game **so that** I can review it away from a computer.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 5 | mobile | 2 | S-7.3.3, S-7.2.8 |

**Acceptance criteria**
- **Given** the replay screen **When** it opens **Then** it shows each seat's life, commander damage and poison at the current step.
- **Given** the previous/next buttons **When** tapped **Then** the replay moves one event; a round picker jumps to the start of any round.
- **Given** a swipe left or right on the state panel **When** performed **Then** it does the same as the next/previous buttons, which remain the accessible alternative.
- **Given** the event list **When** shown **Then** reverted events are marked and screen readers announce them as reverted.
- **Given** the life chart section **When** expanded **Then** a victory-native line chart (S-7.2.8 kit) plots each player's life by round, with the kit's list alternative.
- **Given** a game whose result was amended **When** the replay opens **Then** a notice says who amended it and when.

**Tasks**
- [ ] T-7.3.5.1 (mobile) Replay screen with step buttons, round picker and swipe (Gesture Handler)
- [ ] T-7.3.5.2 (mobile) Event list with reverted markers and amendment notice
- [ ] T-7.3.5.3 (mobile) Jest tests for stepping
- [ ] T-7.3.5.4 (mobile) Life-by-round chart using the S-7.2.8 kit

#### S-7.3.6 — Read-only access to my past games after leaving a playgroup

**As a** player who left or was removed from a playgroup **I want** to keep seeing the games I played there from my own profile **so that** my history doesn't disappear with my membership.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api, web, mobile | 2 | S-7.3.1, S-7.3.2, S-7.3.3, S-2.4.4, S-2.2.3 |

**Acceptance criteria**
- **Given** player X played 30 counted games in P and then left P (S-2.4.4) **When** X calls `GET /players/me/games` **Then** all 30 games are listed alongside X's games in current playgroups, with the same item fields and cursor paging as S-7.3.1, and games from playgroups X left are marked `former_playgroup=true`. (C7)
- **Given** X opens one of those games **When** X requests its detail or `GET /games/{id}/timeline` **Then** it is returned read-only, because X was seated in it; **Given** a game in P that X was not seated in **Then** the response is 404.
- **Given** X is no longer a member of P **When** X calls P-scoped stats or history (`playgroup_id=P`) **Then** the response is 403, while P's members still see X's games under X's name (S-7.1.2).
- **Given** X's own profile (S-2.2.3) on web or mobile **When** X opens "My games" **Then** the list from this story is shown, with a "Former playgroup" label on games from groups X left, and each game opens the replay (F-7.3).
- **Given** a guest history that was merged into X (S-7.1.7) **When** X lists their games **Then** the merged games are included.

**Tasks**
- [ ] T-7.3.6.1 (api) `GET /players/me/games` reusing the history query builder, filtered to the caller's seats, with eager loading and cursor paging
- [ ] T-7.3.6.2 (api) "Seated in the game" access rule for game detail and timeline, read through the playgroups service interface for membership; tests for leaving, removal, rejoining and unseated games
- [ ] T-7.3.6.3 (web) "My games" list on the own-profile page, with the former-playgroup label
- [ ] T-7.3.6.4 (mobile) "My games" list on the own-profile screen
- [ ] T-7.3.6.5 (web, mobile) Vitest/Jest tests for the label and navigation to replay

---

## F-7.4 — Drag-and-drop dashboard

A per-user widget dashboard: a draggable, resizable grid with pinning on web (react-grid-layout) and a reorderable list on mobile (Gesture Handler + Reanimated), with chart widgets drawn by Recharts on web and victory-native on mobile. Widgets come from a server-defined catalog, layouts are saved per user on the server (on web, one layout per breakpoint), start from a curated default set (win rate, recent games, top commanders, activity heatmap, seat position; F4), can be reset to default, and every drag has a tap or keyboard alternative.

Phase 2 · Priority Should

#### S-7.4.1 — Widget catalog API

**As a** client developer **I want** one server-defined catalog of dashboard widgets **so that** web and mobile offer the same widgets with the same settings.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, shared | 2 | S-7.1.2 |

**Acceptance criteria**
- **Given** `GET /dashboard/widgets` **When** called **Then** each widget type has an id, title, description, data endpoint, supported platforms, default/min/max grid size (web) and a config schema (playgroup, date preset, player).
- **Given** the phase 2 catalog **When** listed **Then** it includes at least: win rate by player, deck and commander; recent games; top commanders (the 5 most-played commanders with games and win rate); seat position; pod size; color identity; head-to-head; win conditions; game length.
- **Given** a widget whose backing feature is not shipped yet **When** listed **Then** it has `available=false` and clients do not offer it.
- **Given** the catalog **When** listed **Then** it also returns the curated default set, in this order: win rate, recent games, top commanders, activity heatmap, seat position (F4), with a default size per web breakpoint; the activity heatmap is registered by S-8.1.3 and stays out of defaults while `available=false`.

**Tasks**
- [ ] T-7.4.1.1 (api) Catalog defined as versioned data in the `stats` domain, with schemas and router
- [ ] T-7.4.1.2 (shared) Regenerate api-client types for widget definitions and config
- [ ] T-7.4.1.3 (api) Tests for availability flags and config schema validation

#### S-7.4.2 — Saved layout API

**As a** user **I want** my dashboard layout stored on the server **so that** it follows me across devices.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api | 2 | S-7.4.1 |

**Acceptance criteria**
- **Given** a user with no saved layout **When** calling `GET /me/layouts/{surface}` (surfaces `dashboard_web`, `dashboard_mobile`, `profile_web`, `profile_mobile`) **Then** the default layout for that surface is returned with `is_default=true`, built from the curated default set (S-7.4.1); profile surfaces use the same set filtered to the player.
- **Given** a web surface (`dashboard_web`, `profile_web`) **When** it is stored or returned **Then** it holds one layout per breakpoint (`lg` ≥ 1200 px, `md` ≥ 768 px, `sm` < 768 px), each with its own positions and sizes over the same set of widgets; a breakpoint never saved falls back to that breakpoint's default positions. (F4)
- **Given** a valid layout **When** `PUT /me/layouts/dashboard_web` is called **Then** it is stored for that user only, with a schema version, and a later GET from another device returns it.
- **Given** a layout with an unknown widget id, overlapping grid items in any breakpoint, a size outside the widget's min/max, or more than 30 widgets **When** PUT is called **Then** the response is 422 with the reason and the breakpoint concerned.
- **Given** `DELETE /me/layouts/dashboard_web` **When** called **Then** the saved layout is removed and the next GET returns the default.
- **Given** a saved layout that references a widget later removed from the catalog **When** it is loaded **Then** that widget is skipped and the rest of the layout is returned.

**Tasks**
- [ ] T-7.4.2.1 (api) Layout model (user, surface, schema version, JSON items keyed by breakpoint on web surfaces) and Alembic migration
- [ ] T-7.4.2.2 (api) Validation service: catalog ids, grid overlap per breakpoint, size bounds, item limit; default layouts built from the curated set
- [ ] T-7.4.2.3 (api) Router for GET/PUT/DELETE and tests, including per-user isolation

#### S-7.4.3 — Web grid: drag, resize and pin with keyboard alternatives

**As a** web user **I want** to drag, resize and pin widgets **so that** my dashboard shows what I care about where I want it.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 8 | web | 2 | S-7.4.2, F-1.6 |

**Acceptance criteria**
- **Given** edit mode **When** the user drags a widget by its handle to a new position **Then** the other widgets reflow and the layout is saved within 2 s of the last change.
- **Given** the window is at the `md` breakpoint **When** the user moves or resizes a widget **Then** only the `md` layout changes, and the `lg` and `sm` layouts keep their own arrangement; adding or removing a widget applies to every breakpoint. (F4)
- **Given** edit mode **When** the user drags a widget's resize corner **Then** the widget resizes in grid steps within its catalog min/max size.
- **Given** a pinned widget **When** another widget is dragged onto it **Then** the pinned widget does not move or resize; unpinning restores normal behavior.
- **Given** a keyboard-only user in edit mode **When** they focus a widget and press Arrow keys **Then** it moves one grid cell; Shift+Arrow resizes it by one cell; P toggles pin; and each action is also available from the widget's menu button.
- **Given** a save that fails **When** it fails **Then** the local layout is kept, an error with "Retry" is shown, and nothing is lost on retry.

**Tasks**
- [ ] T-7.4.3.1 (web) Dashboard route with react-grid-layout's responsive grid (one layout per breakpoint), edit mode toggle and drag handles
- [ ] T-7.4.3.2 (web) Pin support using static grid items
- [ ] T-7.4.3.3 (web) Keyboard move/resize/pin handler and widget menu actions, with screen-reader announcements
- [ ] T-7.4.3.4 (web) Debounced autosave via TanStack Query mutation with retry state
- [ ] T-7.4.3.5 (web) Vitest tests for keyboard actions, pin behavior and failed save

#### S-7.4.4 — Web widget catalog drawer and reset to default

**As a** web user **I want** to add, configure and remove widgets and reset my dashboard **so that** I can shape it and recover if I break it.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | web | 2 | S-7.4.3 |

**Acceptance criteria**
- **Given** the catalog drawer **When** the user clicks "Add" on a widget **Then** it is placed in the first free slot at its default size, without needing to drag.
- **Given** a widget's settings **When** the user changes its playgroup or date preset **Then** only that widget's data changes and the setting is saved with the layout.
- **Given** "Remove" in a widget's menu **When** chosen **Then** the widget disappears and an "Undo" toast is shown for 5 s.
- **Given** "Reset to default" **When** the user confirms the dialog **Then** the saved layout is deleted and the default layout is shown.

**Tasks**
- [ ] T-7.4.4.1 (web) Catalog drawer listing available widgets only
- [ ] T-7.4.4.2 (web) Per-widget settings popover, remove with undo, reset with confirmation
- [ ] T-7.4.4.3 (web) Vitest tests for add, remove/undo and reset

#### S-7.4.5 — Web widget renderers

**As a** web user **I want** each widget to render its data cleanly at any size **so that** the dashboard is readable however I arrange it.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | web | 2 | S-7.4.1, S-7.2.6 |

**Acceptance criteria**
- **Given** each catalog widget **When** placed on the dashboard **Then** it renders its data with Recharts or a table, reusing the F-7.1/F-7.2 components.
- **Given** a widget being loaded, empty or failed **When** shown **Then** it shows a skeleton, an empty message or an error with "Retry" respectively, without breaking the grid.
- **Given** a widget resized to its minimum size **When** shown **Then** it switches to a compact view (headline figure plus "n games") instead of clipping.
- **Given** a figure with `low_sample=true` **When** shown in a widget **Then** the small-sample badge is visible.

**Tasks**
- [ ] T-7.4.5.1 (web) Widget registry mapping catalog ids to renderers
- [ ] T-7.4.5.2 (web) Shared loading, empty, error and compact states
- [ ] T-7.4.5.3 (web) Vitest tests for each state

#### S-7.4.6 — Mobile reorderable dashboard

**As a** mobile user **I want** a dashboard list I can reorder, extend and reset **so that** my phone shows my most useful stats first.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 8 | mobile | 2 | S-7.4.2, S-7.2.8, F-1.7 |

**Acceptance criteria**
- **Given** edit mode **When** the user long-presses a widget and drags it **Then** it moves in the list with haptic feedback and the new order is saved to `dashboard_mobile` within 2 s.
- **Given** edit mode **When** the user taps a widget's "Move up" or "Move down" button **Then** it moves one position, the same as dragging; VoiceOver and TalkBack offer the same moves as accessibility actions.
- **Given** "Add widget" **When** the user picks a widget from the catalog sheet **Then** it is added to the end of the list; "Remove" removes it with an "Undo" option for 5 s.
- **Given** "Reset to default" **When** confirmed **Then** the saved mobile layout is deleted and the default is shown: the curated set (win rate, recent games, top commanders, activity heatmap once available, seat position) in that order. (F4)
- **Given** a chart-type widget (seat position, pod size, color identity, win conditions, game length) **When** shown **Then** it renders a compact victory-native chart from the S-7.2.8 kit with its "Show as list" alternative; widgets that are a single figure or a list (win rate tables, recent games, top commanders) render as a stat tile or list, and every figure carries "n games" and the small-sample badge where flagged.
- **Given** edit mode is on **When** the user drags a widget that contains a chart **Then** the chart does not capture the drag gesture.

**Tasks**
- [ ] T-7.4.6.1 (mobile) Reorderable list with Gesture Handler + Reanimated, plus expo-haptics feedback
- [ ] T-7.4.6.2 (mobile) Move up/down buttons and accessibility actions
- [ ] T-7.4.6.3 (mobile) Catalog sheet, remove with undo, reset with confirmation
- [ ] T-7.4.6.4 (mobile) Widget renderers: compact victory-native charts (S-7.2.8 kit) for chart-type widgets, stat tiles and lists for the rest
- [ ] T-7.4.6.5 (mobile) Jest tests for reorder via buttons, add/remove and reset

---

## F-7.5 — Card performance stats

Win rate of decks when a given card is in the deck version played, compared against those decks' overall results, with clear sample-size caveats. It answers "does this card show up in winning decks?", not "does this card win games". Aggregates are stored per day so card stats can be filtered by day, and basic lands and staples are hidden from the leaderboard by default, with a toggle to show them (F5).

Phase 2 · Priority Could

#### S-7.5.1 — Card performance aggregates

**As a** playgroup member **I want** card-level results precomputed **so that** card stats load fast even with thousands of games.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 5 | api | 2 | S-7.1.1, F-4.4, F-2.4 |

**Acceptance criteria**
- **Given** a saved or amended game **When** the refresh job runs **Then** card aggregates are recomputed for every `oracle_id` in the main deck of each deck version played, per playgroup and day (the game's end date in the playgroup's time zone): games, wins, draws, distinct decks and distinct players. (F5)
- **Given** the owner changes the playgroup's time zone **When** the change is saved **Then** a rebuild of that playgroup's card aggregates is enqueued, so every game falls on its day in the new time zone.
- **Given** a card whose type line has the Basic supertype (including Wastes and snow basics) **When** aggregated **Then** it is flagged `is_basic=true`.
- **Given** a card present in 2 copies (for example a Relentless Rats deck) **When** aggregated **Then** the game counts once for that card.
- **Given** the job runs twice for the same game **When** finished **Then** the aggregates are unchanged; `rebuild-stats` rebuilds them to the same values.
- **Given** commanders **When** aggregated **Then** they are flagged as commanders so they can be shown or left out separately.

**Tasks**
- [ ] T-7.5.1.1 (api) Card aggregate table (playgroup, oracle_id, day) with an `is_basic` flag, and Alembic migration
- [ ] T-7.5.1.4 (api) Rebuild a playgroup's card aggregates when its time zone changes, enqueued by an explicit call from the playgroups settings service (coordinate with E-2; no signals), with tests
- [ ] T-7.5.1.2 (api) Recompute affected rows in the refresh job, reading deck contents through the decks service interface
- [ ] T-7.5.1.3 (api) Tests for idempotency, duplicates and rebuild

#### S-7.5.2 — Card performance API

**As a** playgroup member **I want** a ranked list of cards by win rate **so that** I can see which cards appear in winning decks.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 5 | api | 2 | S-7.5.1, S-7.1.3 |

**Acceptance criteria**
- **Given** a card played in 2 decks over 12 counted games with 6 wins **When** calling `GET /stats/cards?playgroup_id=P` **Then** its row shows `games=12`, `wins=6`, `win_rate=0.50`, `distinct_decks=2`.
- **Given** each row **When** returned **Then** it includes `baseline_win_rate` (all counted games of the same decks) and `delta` = win_rate − baseline_win_rate.
- **Given** the playgroup's small-sample threshold is N games (default 5, S-7.1.3) and the card distinct-deck minimum is M (setting, default 2) **When** a row has fewer than N games or fewer than M decks **Then** `low_sample=true`.
- **Given** a date range **When** applied **Then** it is applied by day (inclusive, days in the playgroup's time zone, which the stored aggregates use), and the response states the range and the time zone. (F5)
- **Given** no `include_staples` parameter **When** the request runs **Then** basic lands and staples are left out and the response reports how many rows were hidden; a staple is a non-basic card that is in at least the playgroup's staple share (setting, default 75%) of the distinct decks counted in the requested range. **Given** `include_staples=true` **Then** they are included and flagged `is_basic` or `is_staple`. (F5)
- **Given** more rows than the page size (default 50, maximum 200) **When** the request runs **Then** the response returns a `next_cursor` (keyset on the sort value and `oracle_id`); there is no offset parameter.

**Tasks**
- [ ] T-7.5.2.1 (api) Query builder with baseline and delta, sort and `min_games`
- [ ] T-7.5.2.2 (api) Card distinct-deck setting, reuse of the playgroup game threshold; schemas and router with cursor paging; tests
- [ ] T-7.5.2.3 (api) Day-range filter, staple-share setting and `include_staples` filter, with tests for basics, the staple cut-off and the hidden-row count

#### S-7.5.3 — Card performance within one deck

**As a** deck owner **I want** to compare my deck's results with and without a card across its versions **so that** I can judge cuts and additions.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 3 | api | 2 | S-7.5.1, F-4.4 |

**Acceptance criteria**
- **Given** a deck with 3 versions and 15 counted games **When** calling `GET /decks/{id}/card-performance` **Then** each card that is in some but not all played versions shows games and win rate with it and without it.
- **Given** a card present in every played version **When** listed **Then** it shows "in every version" and no comparison.
- **Given** either side of a comparison has fewer than the threshold games **When** returned **Then** that side has `low_sample=true`.
- **Given** a caller who shares a playgroup with the deck's owner (C5) **When** they request the comparison **Then** it is returned read-only; **Given** a caller who shares no playgroup with the owner **Then** the response is 404.

**Tasks**
- [ ] T-7.5.3.1 (api) With/without comparison from seat facts and deck version contents
- [ ] T-7.5.3.2 (api) Router with the decks domain's visibility check (shared playgroup) and tests

#### S-7.5.4 — Card performance views on web and mobile

**As a** playgroup member **I want** card performance on the card page and the deck page **so that** I see it where I make deck decisions.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 5 | web, mobile | 2 | S-7.5.2, S-7.5.3, F-3.3, F-4.6 |

**Acceptance criteria**
- **Given** a card's detail view **When** it has card performance data **Then** it shows games, win rate, baseline and delta with "n games", and a caveat: "Shows results of decks that ran this card; it does not prove the card caused the wins."
- **Given** the card leaderboard (web) **When** sorted by delta **Then** low-sample rows are hidden by default and a "Show small samples" toggle reveals them.
- **Given** the card leaderboard (web and mobile) **When** it opens **Then** basic lands and staples are hidden, a note says how many were hidden, and a "Show basics and staples" toggle reveals them with a "Basic" or "Staple" label. (F5)
- **Given** the leaderboard's date filter **When** the user picks a custom range **Then** any start and end day can be chosen (not only whole months).
- **Given** a deck page **When** opened **Then** the with/without comparison from S-7.5.3 is listed, sortable on web via keyboard-accessible headers.
- **Given** mobile **When** the same views are opened **Then** they appear as lists with the same figures and caveat.

**Tasks**
- [ ] T-7.5.4.1 (web) Card leaderboard page and card-detail section
- [ ] T-7.5.4.2 (web) Deck page with/without table
- [ ] T-7.5.4.3 (mobile) Card-detail and deck card-performance lists
- [ ] T-7.5.4.4 (web, mobile) Vitest/Jest tests for caveat text, small-sample toggle and the basics-and-staples toggle
- [ ] T-7.5.4.5 (mobile) Card leaderboard list with the basics-and-staples toggle and day-range filter

---

## Open questions

None open. Resolved: see OPEN-QUESTIONS.md decisions log.
