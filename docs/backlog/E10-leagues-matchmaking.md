# E-10 — Leagues & Matchmaking

| Field | Value |
|---|---|
| Phase(s) | 4 |
| Goal | Run seasons inside a playgroup with an opt-in roster, a per-season scoring system that is versioned and logged, and live standings, and split the players at a game night into pods. |
| Success measures | <ul><li>Standings for a season can be rebuilt from its games and current scoring version and give the same result every time.</li><li>Standings update within 5 s of a counted game finishing or its results being amended, including in ended seasons.</li><li>A scoring change rescores the whole season and leaves a log entry every time.</li><li>Matchmaking for up to 32 attending players returns pods in under 2 s.</li><li>Generated pods have no more repeat pairings than the best of 100 random splits of the same players, and, with a balancing option turned on, no larger spread on that measure.</li></ul> |
| Out of scope | Cross-playgroup or public leagues; prizes and entry fees; Swiss or elimination tournament brackets; automatic deck selection for players. |

## F-10.1 — Seasons

A season belongs to a playgroup and has a start and end date. Seasons in the same playgroup can't overlap. Players **opt in to the season roster**, and a finished game **counts automatically** when it falls within the season's dates and every seated player was on the roster **when the game finished**. Joining or leaving the roster never changes games that already finished: games from before a player joined never count, a late joiner never changes earlier standings, and a player who leaves keeps their counted games (decision "Late roster joiners", 2026-11-07). The host of a game can exclude it (decision H1). Playgroup admins manage seasons. Guests take part like members (decision C4). Abandoned games never count (D8).

Phase 4 · Priority Must

#### S-10.1.1 — Season model and API

**As a** playgroup admin **I want** to create seasons with a name and start and end dates **so that** we can run leagues over a fixed period.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 4 | F-2.4 |

**Acceptance criteria**
- **Given** a playgroup admin **When** they create a season with name, start date 2027-01-01 and end date 2027-03-31 **Then** it is stored with status `upcoming` before the start date, `active` from the start date through the end date, and `ended` afterwards.
- **Given** an end date before the start date **When** the season is created **Then** the API returns 422.
- **Given** an existing season from 2027-01-01 to 2027-03-31 in the playgroup, archived or not **When** an admin creates or edits a season whose dates overlap it by even one day **Then** the API returns 422 naming the overlapping season (decision H1).
- **Given** a playgroup member who is not an admin **When** they try to create, edit or delete a season **Then** the API returns 403; they can still list and view seasons.
- **Given** an `ended` season **When** an admin edits its dates **Then** the API returns 409; name and description stay editable, and scoring changes follow S-10.2.7.
- **Given** a season with counted games **When** an admin tries to delete it **Then** the API returns 409 and asks them to archive it instead.

**Tasks**
- [ ] T-10.1.1.1 (api) `Season` SQLAlchemy model (playgroup, name, description, start/end date, archived flag) with an exclusion constraint against overlapping date ranges per playgroup, and Alembic migration
- [ ] T-10.1.1.2 (api) Status derived from dates in the playgroup's time zone
- [ ] T-10.1.1.3 (api) `leagues/services` CRUD with admin checks via the playgroups domain's public interface
- [ ] T-10.1.1.4 (api) pytest suite for status transitions, overlap, permissions and the delete guard

#### S-10.1.2 — Count games automatically for the season roster

**As a** league player **I want** every game I play with other roster players during the season to count without anyone having to tag it **so that** the standings are complete and nobody forgets a game.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 4 | S-10.1.1, S-10.1.6, S-6.7.4, S-6.7.6, S-6.2.8 |

**Acceptance criteria**
- **Given** a finished game in the playgroup, within the season's dates, where every seated player was on the roster at the moment the game finished **When** results are saved **Then** the game is linked to the season and marked `counted` automatically, with no opt-in by the host (decision H1).
- **Given** a finished game within the season's dates where one seated player was not on the roster when it finished **When** results are saved **Then** it is linked as `not counted` with the reason "not on roster: <player>".
- **Given** a player who joins the roster after a game finished **When** they join **Then** that game stays `not counted` and the season's standings do not change; only games that finish after they joined can count for them.
- **Given** a player who leaves the roster, or is removed by an admin **When** the change is saved **Then** games that already counted keep counting and standings do not change; games that finish after they left do not count while they are seated.
- **Given** a game with one or more guest seats whose guests are on the roster **When** results are saved **Then** it is counted, and its guests score like members (decision C4).
- **Given** a game that finished outside the season's dates or in another playgroup **When** results are saved **Then** it is never linked to the season.
- **Given** a game that was abandoned, by the host or automatically after idling **When** eligibility is evaluated **Then** it is never counted, with the reason "abandoned"; if the host resumes it and it finishes, it is evaluated like any other finished game (decision D8).
- **Given** a playgroup admin amends a game's results (S-6.7.6), for example its seats **When** the amendment is saved **Then** the game is re-evaluated against the roster as it was when the game finished (not as it is now) and standings are recomputed (S-10.3.1), also when the season has ended.

**Tasks**
- [ ] T-10.1.2.1 (api) Eligibility service: playgroup, season dates, all seated players on the roster at the game's finish time (from roster join and leave times, S-10.1.6), not abandoned, not excluded
- [ ] T-10.1.2.2 (api) Explicit calls from the S-6.7.4 results flow and the S-6.7.6 amendment flow (no event hooks); roster changes never trigger a re-evaluation
- [ ] T-10.1.2.3 (api) `SeasonGame` link with status `counted`/`not_counted`/`excluded` and reason
- [ ] T-10.1.2.4 (api) Tests for roster and non-roster seats, guests, dates, abandoned and resumed games, amendments in active and ended seasons (evaluated against the roster at finish time), a late joiner's earlier games staying uncounted, and a leaver's counted games staying counted

#### S-10.1.3 — Exclude or include a game

**As a** game host or playgroup admin **I want** to exclude a specific game from the season with a reason, or include it again **so that** I can correct mistakes like a practice game.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 4 | S-10.1.2 |

**Acceptance criteria**
- **Given** a counted game **When** its host excludes it with the reason "practice game" **Then** it is marked `excluded`, the reason, who did it and the timestamp are recorded, and standings are recomputed (decision H1).
- **Given** a counted game **When** a playgroup admin who was not its host excludes it **Then** the same happens.
- **Given** a playgroup member who was neither the host nor an admin **When** they try to exclude or include the game **Then** the API returns 403.
- **Given** an excluded game **When** the host or an admin includes it again **Then** it is evaluated again under S-10.1.2 and the change is added to the audit history; the earlier entry is kept.
- **Given** an `ended` season **When** the host or an admin excludes or includes a game **Then** the change is accepted, logged, and the season is rescored (decision H2).

**Tasks**
- [ ] T-10.1.3.1 (api) Override endpoint and audit history table with Alembic migration
- [ ] T-10.1.3.2 (api) Host check through the games domain's public interface; recompute through the standings service
- [ ] T-10.1.3.3 (api) Tests for host, admin, other member, re-include and ended season

#### S-10.1.4 — Show the season in the game lobby

**As a** player in a lobby **I want** to see whether this game will count toward the season **so that** there are no surprises after the game.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | web, mobile | 4 | S-10.1.2, S-10.1.6, F-6.2 |

**Acceptance criteria**
- **Given** an active season and a lobby where every seated player is on the roster **When** the lobby is shown **Then** a "Counts for <season name>" badge is visible on web and mobile.
- **Given** a lobby with a seated player who is not on the roster **When** the lobby is shown **Then** the badge reads "Won't count" and names that player, and it updates within 1 s of the seats changing.
- **Given** a signed-in seated player who is not on the roster **When** they see the badge **Then** a "Join roster" button opts them in (S-10.1.6) and the badge updates.
- **Given** no active season in the playgroup **When** the lobby is shown **Then** no badge is shown.

**Tasks**
- [ ] T-10.1.4.1 (api) Eligibility preview endpoint for a lobby
- [ ] T-10.1.4.2 (web) Season badge and "Join roster" button in the lobby
- [ ] T-10.1.4.3 (mobile) Season badge and "Join roster" button in the lobby

#### S-10.1.5 — Web season management

**As a** playgroup admin on the web **I want** screens to create and manage seasons, see the roster and see which games count **so that** running a league needs no database access.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 4 | S-10.1.1, S-10.1.2, S-10.1.3, S-10.1.6, S-10.2.1, F-1.6 |

**Acceptance criteria**
- **Given** the playgroup's Seasons page **When** an admin creates a season with dates and a scoring preset **Then** it appears in the list with its status; overlapping dates show the API's error next to the date fields.
- **Given** a season detail page **When** it loads **Then** the roster is shown, and every linked game is listed with date, players, and `counted`/`not counted`/`excluded` status and reason.
- **Given** a counted game in the list **When** its host or an admin clicks "Exclude" and enters a reason **Then** the game shows as excluded and the standings link reflects the recomputed standings.
- **Given** the season detail page **When** it loads **Then** the scoring change log (S-10.2.7) is shown with who changed what and when.
- **Given** a member who is neither an admin nor the host of a game **When** they open the Seasons page **Then** they see the seasons, roster and games but no create, edit or exclude controls.

**Tasks**
- [ ] T-10.1.5.1 (web) Season list and create/edit form
- [ ] T-10.1.5.2 (web) Season detail with roster, game list, exclude/include dialog and scoring change log
- [ ] T-10.1.5.3 (web) Vitest tests for admin, host and member views

#### S-10.1.6 — Season roster opt-in

**As a** player **I want** to opt in to a season **so that** my games with other roster players count, and I am not entered into a league I don't want to play.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api, web, mobile | 4 | S-10.1.1, F-2.3, F-1.6, F-1.7 |

**Acceptance criteria**
- **Given** a member of the playgroup and an `upcoming` or `active` season **When** they tap "Join season" **Then** they are added to the roster (decision H1).
- **Given** a guest of the playgroup **When** a playgroup admin adds them to the roster **Then** they are on the roster; an admin can't add a member who has an account, because members opt in themselves.
- **Given** a player who joins an active season late **When** they join **Then** the join time is recorded, and games that finished before it are not re-evaluated and never count; earlier standings are unchanged (S-10.1.2).
- **Given** a player on the roster **When** they leave, at any time, or an admin removes them **Then** the leave time is recorded and they are off the roster; their games that already counted keep counting, and games that finish later stop counting for them (S-10.1.2).
- **Given** a player who left an `upcoming` or `active` season **When** they join again **Then** a new join time is recorded; games that finished while they were off the roster never count.
- **Given** an `ended` season **When** someone tries to join **Then** the API returns 409.
- **Given** any playgroup member **When** they view a season **Then** they see the roster.

**Tasks**
- [ ] T-10.1.6.1 (api) `SeasonRosterEntry` model (season, player, joined at, left at, added by, removed by) keeping every join and leave as history, and Alembic migration
- [ ] T-10.1.6.2 (api) Join, leave, admin-add-guest and admin-remove services; none of them re-evaluates finished games
- [ ] T-10.1.6.3 (web) "Join season" / "Leave season" on the season page
- [ ] T-10.1.6.4 (mobile) "Join season" / "Leave season" on the season screen
- [ ] T-10.1.6.5 (api) Tests: member opt-in, guest added by admin, member can't be added by admin, late join leaves earlier games and standings unchanged, leave and admin removal keep counted games, rejoin after leaving, ended season

## F-10.2 — Scoring systems

Each season has its own scoring system, started from one of four presets (**win-only, placement, achievements and participation**) and editable per season. Scoring systems are stored as data and versioned. **If a season's scoring changes, the whole season is rescored and the change is logged**; this applies to ended seasons too (decision H2).

Results follow decision D6 and fix X1. Players eliminated at the same time share a placement (competition ranking: 1, 2, 3, 3), and a shared placement **splits the points of the places it covers**. In a full draw the game has `no_winner` set, every surviving seat gets placement 1 with outcome `draw`, and each of them receives the scoring system's **draw value** in place of placement points and the win bonus. Seats eliminated earlier are losses. A draw is never a win.

Phase 4 · Priority Must

#### S-10.2.1 — Versioned scoring system model

**As a** playgroup admin **I want** each season's scoring system stored as versioned data **so that** every change can be traced and the season can always be rebuilt.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 4 | S-10.1.1 |

**Acceptance criteria**
- **Given** a scoring definition with placement points (1st: 3, 2nd: 2, 3rd: 1, other: 0), a win bonus, a draw value of 1 and a participation point **When** an admin saves it for a season **Then** it is validated against the scoring schema and stored as version 1 of that season's scoring system.
- **Given** a definition missing points for a placement position or the draw value, or with negative points **When** it is saved **Then** the API returns 422 naming the invalid field.
- **Given** a season's scoring system **When** an admin edits it, in an `upcoming`, `active` or `ended` season **Then** a new version is created, the season uses the new version, every earlier version stays unchanged and readable, and the whole season is rescored (S-10.2.7).
- **Given** two seasons that started from the same preset **When** one season's scoring is edited **Then** the other season's scoring is unchanged.

**Tasks**
- [ ] T-10.2.1.1 (api) `ScoringSystem` (one per season) and `ScoringSystemVersion` models (definition as JSON validated by a Pydantic schema) and Alembic migration
- [ ] T-10.2.1.2 (api) Pydantic scoring schema: placement points by pod size (3–6), win bonus, draw value, participation, catalog achievement points and admin-awarded achievements; points stored as decimals so split placements can give fractions
- [ ] T-10.2.1.3 (api) Version-on-edit service that repoints the season and calls the rescore service explicitly
- [ ] T-10.2.1.4 (api) Tests for validation, immutability of versions and per-season independence

#### S-10.2.2 — Scoring engine

**As a** league player **I want** each counted game scored by the season's rules with a breakdown **so that** I can see exactly how I earned my points.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 4 | S-10.2.1, F-6.7 |

**Acceptance criteria**
- **Given** a 4-player game and a scoring version with placement points 3/2/1/0 and a 1-point participation award **When** it is scored **Then** players receive 4, 3, 2 and 1 points, and each award has an itemized line (for example "1st place: 3", "Participation: 1").
- **Given** a scoring version with placement points that differ by pod size **When** a 5-player game is scored **Then** the 5-player table is used.
- **Given** the same game and scoring version **When** it is scored twice **Then** the result is identical (the engine is a pure function of game result and version).
- **Given** a 4-player game where two players were eliminated at the same time and share 3rd place, with placement points 3/2/1/0 **When** it is scored **Then** they split the points of 3rd and 4th, each getting 0.5, and the itemized line reads "3rd place (shared): 0.5" (fix X1).
- **Given** a 4-player game with `no_winner` where two players were eliminated and the two survivors drew, with a draw value of 1 **When** it is scored **Then** no one receives the win bonus, each survivor gets the line "Draw: 1" instead of placement points, and the eliminated players get the points of their placements as losses.
- **Given** a game whose last 3 remaining players were all eliminated at the same time, so the result has `no_winner` and those 3 share placement 1 with outcome `draw` **When** it is scored **Then** each of them gets the draw value, exactly as for survivors of a drawn game.

**Tasks**
- [ ] T-10.2.2.1 (api) Pure scoring function `(game_result, scoring_version) -> list[PlayerScore]` with itemized lines, reading placements, outcomes and `no_winner` from the F-6.7 result
- [ ] T-10.2.2.2 (api) `GameScore` storage per season game and player, with the version id used
- [ ] T-10.2.2.3 (api) Table-driven test vectors for pod sizes 3–6, wins, draws and split shared placements

#### S-10.2.3 — Achievements from the game log

**As a** league player **I want** bonus points for notable achievements, detected automatically from the game **so that** memorable plays are rewarded without manual bookkeeping.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 4 | S-10.2.2, F-6.3, F-6.6, F-6.7 |

**Acceptance criteria**
- **Given** a scoring version that awards 1 point for "First blood" (first player to eliminate an opponent) **When** a game's event log shows player B eliminating player D first **Then** B receives the achievement line "First blood: 1".
- **Given** achievements defined as data (an achievement type from a fixed catalog plus parameters, for example "win by round ≤ 6", where turn count means rounds per decision D4) **When** a definition uses a type that is not in the catalog **Then** saving the scoring version returns 422.
- **Given** the initial catalog **When** it is listed **Then** it contains `first_blood`, `fast_win` (win by round ≤ N), `commander_damage_kill` and `win_by_condition` (win with a chosen D5 win condition, for example poison).
- **Given** a game event that was reverted with a compensating event (F-6.6) **When** achievements are evaluated **Then** the reverted event is not counted.
- **Given** a scoring version with no achievements **When** a game is scored **Then** no achievement lines are produced.

**Tasks**
- [ ] T-10.2.3.1 (api) Achievement catalog: one evaluator per type, reading the game event log through the games domain's public interface
- [ ] T-10.2.3.2 (api) Initial catalog types: `first_blood`, `fast_win`, `commander_damage_kill`, `win_by_condition`
- [ ] T-10.2.3.3 (api) Tests per achievement type using recorded event logs, including reverted events

#### S-10.2.4 — Achievements awarded by an admin

**As a** playgroup admin **I want** to award an achievement by hand after a game **so that** things the log can't detect (like "best play of the night") can still score.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api, web | 4 | S-10.2.3 |

**Acceptance criteria**
- **Given** a scoring version with an admin-awarded achievement "Table favorite: 1" **When** an admin awards it to a player in a counted game **Then** the player's game score gains that line, the award is logged with who and when, and standings are recomputed (decision H2).
- **Given** an admin-awarded achievement limited to once per game **When** an admin awards it a second time in the same game **Then** the API returns 409.
- **Given** an award **When** an admin revokes it **Then** the line is removed, the revocation is logged and standings are recomputed.
- **Given** a counted game in an `ended` season **When** an admin awards or revokes an achievement **Then** it is accepted and the season is rescored.
- **Given** a non-admin **When** they try to award an achievement **Then** the API returns 403.

**Tasks**
- [ ] T-10.2.4.1 (api) Award and revoke endpoints with per-game limit and audit record
- [ ] T-10.2.4.2 (web) Award dialog on the season game detail

#### S-10.2.5 — Scoring presets

**As a** playgroup admin **I want** to start from ready-made scoring presets **so that** I can set up a league without designing a points system from scratch.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 4 | S-10.2.1, S-10.2.3, S-10.2.4 |

**Acceptance criteria**
- **Given** a fresh database **When** seed data loads **Then** exactly four presets exist, `win-only`, `placement`, `achievements` (including an admin-awarded achievement) and `participation`, each valid against the scoring schema (decision H2).
- **Given** the default values **When** the presets are read **Then** `win-only` gives a win 3 and a draw 1; `placement` gives 3/2/1/0 for 4 players (3/1/0 for 3, 4/3/2/1/0 for 5, 5/4/3/2/1/0 for 6) and a draw 1; `achievements` gives a win 2, a draw 1, 1 per catalog achievement and an admin-awarded "Play of the night: 1" once per game; `participation` gives 1 per counted game, a win bonus 1 and a draw 1.
- **Given** a preset **When** an admin chooses it for a season **Then** the season gets its own scoring system as a copy, and later edits to it never change the preset or other seasons.

**Tasks**
- [ ] T-10.2.5.1 (api) Seed data for the four presets with the default values above
- [ ] T-10.2.5.2 (api) Copy-from-preset service and a schema validation test for every preset

#### S-10.2.6 — Web scoring system editor

**As a** playgroup admin on the web **I want** a form to build and preview a season's scoring system **so that** I don't have to edit JSON.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 4 | S-10.2.1, S-10.2.2, S-10.2.7, F-1.6 |

**Acceptance criteria**
- **Given** the editor **When** the admin sets placement points per pod size, win bonus, draw value, participation and achievements **Then** the form validates live and shows the same errors the API would return.
- **Given** the placement points **When** they change **Then** a short example (two players sharing 3rd in a 4-player game) shows the split points each would get.
- **Given** a valid definition **When** the admin opens "Preview" with a sample 4-player result **Then** the points for each player are shown with itemized lines from the scoring engine.
- **Given** a season with counted games, active or ended **When** the admin saves a change **Then** a warning explains that the whole season will be rescored and the change logged, and they must confirm (decision H2).
- **Given** a keyboard-only user **When** they use the editor **Then** every field, the preview and save are operable without a mouse.

**Tasks**
- [ ] T-10.2.6.1 (web) Scoring form with schema-driven validation from the generated API types
- [ ] T-10.2.6.2 (api) Dry-run scoring endpoint for previews
- [ ] T-10.2.6.3 (web) Preview panel and rescore warning
- [ ] T-10.2.6.4 (web) Vitest tests for validation and preview rendering

#### S-10.2.7 — Rescore the whole season when its scoring changes

**As a** league player **I want** a scoring change applied to every game in the season, with a record of the change **so that** everyone is scored by the same rules and nobody wonders why points moved.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 4 | S-10.2.1, S-10.2.2, S-10.3.1 |

**Acceptance criteria**
- **Given** an active season with 40 counted games **When** its scoring changes **Then** every counted game is rescored with the new version and the standings are rebuilt within 30 s (decision H2).
- **Given** an `ended` season **When** its scoring changes **Then** it is rescored the same way; no 409 blocks the change.
- **Given** any scoring change **When** it is saved **Then** a log entry records who made it, when, the old and new version ids, and a summary of the changed fields; the log is readable by every member of the playgroup.
- **Given** a rescore in progress **When** standings are read **Then** the previous standings are returned until the rescore completes, never a half-rescored table.

**Tasks**
- [ ] T-10.2.7.1 (api) `ScoringChangeLog` table and Alembic migration
- [ ] T-10.2.7.2 (api) Rescore service that rescores all counted games and swaps standings in one transaction, queued as an arq job
- [ ] T-10.2.7.3 (api) Change-log endpoint
- [ ] T-10.2.7.4 (api) Tests: active and ended seasons, log contents, consistent reads during a rescore

## F-10.3 — Standings

Season standings ranked by points, kept up to date as counted games finish, with a per-player breakdown. Ties are broken by **win rate, then head-to-head, then games played**. A player needs **at least 5 counted games** to be ranked; others are listed as "provisional" (decision H3).

Phase 4 · Priority Must

#### S-10.3.1 — Compute season standings

**As a** league player **I want** standings that update after every counted game **so that** I always know where I stand.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 4 | S-10.2.2, S-10.1.2, S-6.7.6, F-1.9 |

**Acceptance criteria**
- **Given** a counted game finishes **When** its results are saved **Then** the season standings reflect it within 5 s.
- **Given** standings **When** they are read **Then** each row has rank, player (members and guests, with guests labelled), total points, games played, wins, draws, losses, win rate, average placement and points per game; a draw is not counted as a win, and a shared placement counts as that placement in the average.
- **Given** a playgroup admin amends the results of a counted game, at any time (decision D7; the audit trail is kept by S-6.7.6) **When** the amendment is saved **Then** that game is rescored with the season's current scoring version and the standings reflect it within 5 s.
- **Given** an amended game that belongs to an `ended` season **When** the amendment is saved **Then** that season's standings are recomputed as well (decision H2).
- **Given** a season **When** a full recompute is run **Then** the standings are rebuilt from scratch from its counted games and current scoring version, and the result is identical to the incrementally updated standings.
- **Given** two recomputes triggered at the same time **When** both run **Then** the stored standings are consistent (no duplicate or missing rows).

**Tasks**
- [ ] T-10.3.1.1 (api) `SeasonStanding` table and Alembic migration
- [ ] T-10.3.1.2 (api) Standings service: incremental update and full recompute, with a per-season lock
- [ ] T-10.3.1.3 (api) arq job for recompute, queued explicitly from the results, result-amendment, exclusion, scoring-change, admin-award and guest-merge flows (roster changes never change standings)
- [ ] T-10.3.1.4 (api) Test: incremental and full recompute give identical results over a generated season

#### S-10.3.2 — Tiebreakers

**As a** league player **I want** ties in points broken by clear, published rules **so that** final positions are fair and predictable.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 4 | S-10.3.1 |

**Acceptance criteria**
- **Given** two players with equal points **When** A has a higher win rate (draws count as games but not wins) **Then** A ranks above B, and the row shows "decided by: win rate" (decision H3).
- **Given** two players equal on points and win rate **When** A finished ahead of B in more of the counted games they both played **Then** A ranks above B ("decided by: head-to-head"); games where they shared a placement or both drew count for neither.
- **Given** three players equal on points and win rate **When** head-to-head is applied **Then** each player's number of finishes ahead of the other tied players, in counted games they shared, decides the order among them.
- **Given** players still equal after head-to-head **When** games played differs **Then** the player with more counted games ranks higher ("decided by: games played").
- **Given** two players equal on points and every tiebreaker **When** standings are computed **Then** they share the same rank, and the next rank is skipped (for example 1, 2, 2, 4).

**Tasks**
- [ ] T-10.3.2.1 (api) Fixed tiebreaker chain: win rate, head-to-head, games played
- [ ] T-10.3.2.2 (api) Head-to-head computation over the season's counted games, shared-rank logic and "decided by" field
- [ ] T-10.3.2.3 (api) Tests for each tiebreaker, a three-way head-to-head and a full tie

#### S-10.3.3 — Provisional players below 5 games

**As a** league player **I want** players with fewer than 5 games shown as provisional **so that** one lucky game doesn't top the table.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 2 | api | 4 | S-10.3.1 |

**Acceptance criteria**
- **Given** a player with 4 counted games in the season **When** standings are computed **Then** they appear in a separate "provisional" section with their points and games played, and ranked players are numbered without them (decision H3).
- **Given** a provisional player whose 5th counted game finishes **When** standings update **Then** they move into the ranked table at their position.
- **Given** an amendment or exclusion that drops a ranked player to 4 counted games **When** standings update **Then** they move back to the provisional section.

**Tasks**
- [ ] T-10.3.3.1 (api) Ranked/provisional split at 5 counted games
- [ ] T-10.3.3.2 (api) Tests for crossing the threshold in both directions

#### S-10.3.4 — Web standings page

**As a** league player on the web **I want** a standings table with a breakdown for each player **so that** I can see the race and check my points.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web | 4 | S-10.3.1, S-10.3.2, S-10.3.3, F-1.6 |

**Acceptance criteria**
- **Given** the standings page **When** it loads **Then** the selected season's table is shown (the active season by default), with a season selector for the playgroup's other seasons, and provisional players listed below the ranked table.
- **Given** a player row **When** the user opens it **Then** each counted game is listed with date, placement and itemized points, and the lines add up to the total shown.
- **Given** a new counted game finishes while the page is open **When** the standings change **Then** the table updates within 30 s without a manual reload.
- **Given** a Recharts "points over time" chart for the top players **When** a keyboard or screen-reader user opens "Show as table" **Then** the same data is available as a table.

**Tasks**
- [ ] T-10.3.4.1 (web) Standings table with season selector and provisional section
- [ ] T-10.3.4.2 (web) Player breakdown drawer
- [ ] T-10.3.4.3 (web) Points-over-time chart with table toggle
- [ ] T-10.3.4.4 (api) Per-player breakdown and points-over-time endpoints with eager loading

#### S-10.3.5 — Mobile standings

**As a** league player on mobile **I want** to see the standings and my own breakdown **so that** I can check them at game night.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | mobile | 4 | S-10.3.1, S-10.3.2, S-10.3.3, F-1.7 |

**Acceptance criteria**
- **Given** the mobile standings screen **When** it opens **Then** the active season's standings are shown, with the signed-in player's row highlighted and provisional players listed below.
- **Given** a tap on a player **When** the breakdown opens **Then** each counted game is listed with placement and itemized points.
- **Given** a pull-to-refresh **When** the user releases **Then** the standings reload.

**Tasks**
- [ ] T-10.3.5.1 (mobile) Standings list with own-row highlight and provisional section
- [ ] T-10.3.5.2 (mobile) Player breakdown screen

#### S-10.3.6 — Standings dashboard widget

**As a** league player **I want** a standings widget on my dashboard **so that** I see my rank without opening the league page.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 2 | web | 4 | S-10.3.1, F-7.4 |

**Acceptance criteria**
- **Given** the dashboard widget catalog **When** the user adds "Season standings" **Then** a widget shows the top 5 and the user's own rank (or "provisional") and points, and its placement is saved with the layout.
- **Given** no active season **When** the widget renders **Then** it shows "No active season".

**Tasks**
- [ ] T-10.3.6.1 (web) Standings widget registered in the F-7.4 widget catalog
- [ ] T-10.3.6.2 (web) Vitest render tests for both states

#### S-10.3.7 — Merge league history when a guest claims an account

**As a** player who played league games as a guest **I want** my guest results to move to my account when I claim it **so that** my standings include every game I played.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 4 | S-10.3.1, S-2.3.4, S-10.4.1, S-10.1.6 |

**Acceptance criteria**
- **Given** guest G with 6 counted games and member M with 4 counted games in an active season **When** G is merged into M (by claiming an account or by an admin merge, S-2.3.4) **Then** the season's standings are recomputed, G's row is gone, and M's row shows 10 games with the combined points.
- **Given** G was on a season roster **When** the merge completes **Then** M is on that roster (once, even if M was already on it), and G's join and leave times move to M so that no game's counted status changes.
- **Given** G's stored game scores **When** the merge completes **Then** they refer to M and their itemized lines are unchanged.
- **Given** G has counted games in several seasons, including an `ended` one **When** the merge completes **Then** the standings of every one of those seasons are recomputed.
- **Given** G was checked in at game nights **When** the merge completes **Then** those attendances and the repeat-pairing history used by matchmaking refer to M.
- **Given** the merge fails and rolls back **When** standings are read **Then** they are unchanged, because the recompute is queued only after the merge commits.

**Tasks**
- [ ] T-10.3.7.1 (api) Reassign-player function in the leagues domain's public service interface (roster entries, game scores, game night attendees), added to the merge service's explicit list of domains (no signals)
- [ ] T-10.3.7.2 (api) Queue a standings recompute for each affected season after the merge commits
- [ ] T-10.3.7.3 (api) Tests: merge with and without existing season games, roster de-duplication, ended season, rollback leaves standings unchanged

#### S-10.3.8 — Season filter on stats views

**As a** player **I want** to filter my stats to one season **so that** I can compare how I did in this league with earlier ones.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api, web, mobile | 4 | S-10.1.2, F-7.1, F-7.2 |

**Acceptance criteria**
- **Given** the basic and advanced stats views (F-7.1, F-7.2) on web and mobile **When** a playgroup has seasons **Then** a season selector is shown, defaulting to "All games" (decision F7).
- **Given** a season selected **When** stats load **Then** only that season's `counted` games are included; games marked `not counted` or `excluded` are left out.
- **Given** a season filter **When** the small-sample rule applies (decision F1) **Then** the low-sample markers are based on the games inside the season.
- **Given** a selected season on web **When** the user shares or reloads the URL **Then** the same season filter is applied.
- **Given** a playgroup with no seasons **When** the stats views load **Then** no season selector is shown.

**Tasks**
- [ ] T-10.3.8.1 (api) `counted_game_ids(season)` in the leagues domain's public service interface
- [ ] T-10.3.8.2 (api) `season_id` filter on the stats endpoints, joining the per-seat fact table to the season's counted games through that interface
- [ ] T-10.3.8.3 (web) Season selector on the stats pages, bound to the URL query string
- [ ] T-10.3.8.4 (mobile) Season selector on the stats screens
- [ ] T-10.3.8.5 (api) Tests: counted only, excluded left out, small-sample markers, no seasons

## F-10.4 — Pod matchmaking

At a game night, attending players are split into pods. **Pods of 4 are preferred; 3–6 players are allowed.** Any bracket mix is allowed. Two balancing options are **optional and off by default**: keeping a small bracket spread within pods, and balancing win rates over each player's last 20 games. Pairings from the playgroup's **last 3 game nights** are always avoided, and **seat order is random** (decision H4). A deck in the bracket 1–2 range with no declared intent counts as Bracket 2 (decision E8). A win rate counts only when it rests on at least the playgroup's small-sample threshold of games (default 5, decision F1, S-7.1.3), and a draw is not a win (D6).

Phase 4 · Priority Should

#### S-10.4.1 — Game night check-in

**As a** playgroup admin **I want** to record who is attending tonight and which deck each person brings **so that** matchmaking works from the real attendance.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 4 | F-2.4, F-2.3, F-4.4 |

**Acceptance criteria**
- **Given** a playgroup admin **When** they create a game night for today **Then** it is stored with date, optional season and status `check_in`.
- **Given** a game night in `check_in` **When** a member checks in, or an admin checks in a member or guest, optionally choosing a deck **Then** the player appears in the attendee list with that deck's current bracket (F-5.3), or "no deck" if none was chosen; a bracket 1–2 deck with no declared intent is listed as Bracket 2 with a "not declared" badge (decision E8).
- **Given** a player who is already checked in **When** they check in again **Then** no duplicate is created and the deck choice is updated.
- **Given** a game night whose pods are already generated **When** someone checks in late **Then** they are added as "unassigned" and the admin is told the pods need regenerating or manual placement.

**Tasks**
- [ ] T-10.4.1.1 (api) `GameNight` and `GameNightAttendee` models and Alembic migration
- [ ] T-10.4.1.2 (api) Check-in service with idempotent upsert and deck bracket lookup through the brackets domain's public interface
- [ ] T-10.4.1.3 (api) Tests for check-in, re-check-in and late arrival

#### S-10.4.2 — Split attendees into pod sizes

**As a** playgroup admin **I want** attendees split into pods of 3–6, as many of them 4 as possible **so that** every table has a playable number of players.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 3 | api | 4 | S-10.4.1 |

**Acceptance criteria**
- **Given** N attendees from 3 through 32 **When** pod sizes are requested **Then** every pod is between 3 and 6 and the sizes add up to N (decision H4); a table-driven test covers every N.
- **Given** several valid splits **When** one is chosen **Then** the split with the smallest total distance from 4 (the sum of |size − 4|) wins, then the one with the most pods of 4, then the one with the smallest largest pod; the result is the same every time.
- **Given** that rule **When** N is 5, 6, 7, 9 or 10 **Then** the sizes are [5], [3, 3], [4, 3], [5, 4] and [4, 3, 3].
- **Given** 1 or 2 attendees **When** pod sizes are requested **Then** the API returns 422 "not enough players for one pod".

**Tasks**
- [ ] T-10.4.2.1 (api) Pod size partition function implementing the H4 preference order
- [ ] T-10.4.2.2 (api) Table-driven tests for N = 1–32

#### S-10.4.3 — Pod quality score

**As a** playgroup admin **I want** each possible pod assignment scored **so that** the app can pick pods that avoid recent pairings and, when I ask, balance brackets or win rates, and explain why.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 4 | S-10.4.1, F-5.3, F-7.1, S-5.3.3, S-7.1.3 |

**Acceptance criteria**
- **Given** an assignment of players to pods **When** it is scored **Then** the score reports the number of repeat pairings, plus the bracket-spread part and the win-rate-spread part when their options are on, combined with weights stored as configuration.
- **Given** two players who shared a pod at any of the playgroup's last 3 game nights **When** an assignment puts them together again **Then** its repeat-pairing count increases by 1 for that pair; pairings from the 4th most recent game night or earlier don't count (decision H4).
- **Given** a game night with "keep bracket spread small" off (the default) **When** an assignment puts a Bracket 2 deck in a pod of Bracket 4 decks **Then** the score is not affected (any bracket mix is allowed).
- **Given** "keep bracket spread small" on **When** two assignments differ only in that one puts a Bracket 2 deck in a pod of Bracket 4 decks **Then** the mixed one has a worse bracket-spread part.
- **Given** a deck assessed in the bracket 1–2 range with no declared intent **When** bracket spread is scored **Then** it counts as Bracket 2 (decision E8) and the explanation says "bracket not declared, treated as 2".
- **Given** "balance win rates" on **When** a player's win rate is computed **Then** it uses their last 20 finished games in the playgroup, counting draws as games but not wins (decision D6).
- **Given** "balance win rates" on and a playgroup small-sample threshold of 5 (decision F1) **When** a player has fewer than 5 games in that window **Then** their win rate is left out of the win-rate part and the explanation says "fewer than 5 games"; a player with 5 uses their win rate.
- **Given** a player with no deck chosen **When** bracket spread is scored **Then** that player is left out of the bracket part and the explanation says "no deck chosen".

**Tasks**
- [ ] T-10.4.3.1 (api) Player inputs: bracket from the chosen deck (Bracket 2 for an undeclared 1–2 deck), last-20-games win rate and the playgroup's small-sample threshold from the stats domain's public interface, and pairings from the last 3 game nights
- [ ] T-10.4.3.2 (api) Per-game-night options (bracket spread, win-rate balance; both off by default) and the scoring function with configurable weights and a per-pod explanation
- [ ] T-10.4.3.3 (api) Unit tests for each part of the score, each option on and off, and the fallbacks

#### S-10.4.4 — Generate pods

**As a** playgroup admin **I want** one click to produce pods **so that** game night starts quickly and fairly.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | api | 4 | S-10.4.2, S-10.4.3 |

**Acceptance criteria**
- **Given** 32 checked-in players **When** pods are generated **Then** a result is returned in under 2 s.
- **Given** the same attendees, inputs, options and random seed **When** pods are generated twice **Then** the result is identical; a different seed may give a different result with an equal or better score.
- **Given** any test scenario with at least 8 attendees **When** pods are generated **Then** the result's score is no worse than the best of 100 random assignments with the same pod sizes and options.
- **Given** a generated result **When** it is returned **Then** it includes each pod's members, the score parts per pod and the overall score, and is saved on the game night as a draft.

**Tasks**
- [ ] T-10.4.4.1 (api) Search algorithm (exhaustive for small groups, seeded local search for larger ones)
- [ ] T-10.4.4.2 (api) Generate endpoint storing the draft assignment on the game night
- [ ] T-10.4.4.3 (api) Benchmark and random-baseline tests

#### S-10.4.5 — Adjust pods and start games (web)

**As a** playgroup admin on the web **I want** to review the proposed pods, move players between them and start the games **so that** I can account for things the algorithm doesn't know.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Must | 5 | web, api | 4 | S-10.4.4, F-6.2, F-1.6 |

**Acceptance criteria**
- **Given** a draft assignment **When** the admin drags a player from pod 1 to pod 2 **Then** the move is saved and the pods' score parts and the overall score update within 1 s.
- **Given** a keyboard-only user **When** they focus a player, press Space and use the arrow keys to choose a pod **Then** the player moves exactly as with dragging; a "Move to…" menu offers the same action with a tap or click.
- **Given** a move that leaves a pod with fewer than 3 or more than 6 players **When** the admin tries to confirm the pods **Then** confirmation is blocked and the pod is highlighted with the reason.
- **Given** the game night options **When** the admin toggles "keep bracket spread small" or "balance win rates" **Then** the score is recomputed and "Regenerate" uses the new options.
- **Given** confirmed pods **When** the admin clicks "Start games" **Then** one game lobby per pod is created (F-6.2), with its players seated in a random order (decision H4) and their chosen decks pre-selected; the random seed is stored with the game night.
- **Given** a draft **When** the admin clicks "Regenerate" **Then** a new assignment is generated, keeping any pods the admin has locked.

**Tasks**
- [ ] T-10.4.5.1 (web) Pod board with dnd-kit and the keyboard sensor enabled, a "Move to…" menu and the option toggles
- [ ] T-10.4.5.2 (api) Move, lock, option and confirm endpoints that re-score the assignment
- [ ] T-10.4.5.3 (api) "Start games" service creating lobbies with a seeded random seat order through the games domain's public interface
- [ ] T-10.4.5.4 (web) Vitest tests for move, lock and size-violation states

#### S-10.4.6 — Matchmaking on mobile

**As a** player or admin at game night **I want** to check in and see my pod on my phone **so that** I know where to sit without crowding around one screen.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | mobile | 4 | S-10.4.1, S-10.4.4, S-10.4.5, F-1.7 |

**Acceptance criteria**
- **Given** an open game night **When** a member taps "Check in" and picks a deck **Then** they appear in the attendee list on every device within 5 s.
- **Given** confirmed pods **When** a checked-in player opens the game night **Then** their pod, its members and their seat are shown first, and "Join game" opens that pod's lobby.
- **Given** an admin on mobile with a draft assignment **When** they long-press and drag a player onto another pod **Then** the player moves; the same move is also possible by tapping the player and then tapping "Move to pod N".
- **Given** an admin on mobile **When** they tap "Generate pods" **Then** the draft assignment is shown with the overall score.

**Tasks**
- [ ] T-10.4.6.1 (mobile) Check-in screen with deck picker
- [ ] T-10.4.6.2 (mobile) "My pod" view with join-lobby action
- [ ] T-10.4.6.3 (mobile) Admin pod editor using Gesture Handler and Reanimated, with a tap-to-move alternative
- [ ] T-10.4.6.4 (mobile) Jest tests for tap-to-move

## Open questions

None open. Resolved: see OPEN-QUESTIONS.md decisions log.
