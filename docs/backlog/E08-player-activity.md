# E-8 — Player Activity & Habits

| | |
|---|---|
| **Phase(s)** | 2 (two stories, S-8.3.4 and S-8.3.6, wait for phase 3 / F-9.4 and F-9.5) |
| **Goal** | Show how and how often people play: game frequency, streaks and a calendar heatmap; how commanders, colors and brackets shift over time for a player and the playgroup; and a personal dashboard on each player's profile that brings activity and stats together. |
| **Success measures** | <ul><li>A completed game shows up in activity, streaks and meta shares within 10 s of the result being saved.</li><li>Activity and meta endpoints respond in p95 < 300 ms on the 10,000-game performance fixture.</li><li>Every meta share and profile win rate shows its sample size.</li><li>The profile page works fully by keyboard on web and with screen readers on mobile, including rearranging widgets.</li></ul> |
| **Out of scope** | Core win-rate and advanced stats (E-7). Spend and collection value calculations (E-9; this epic only shows them on the profile, subject to the owner's sharing choice). League seasons, standings and the season filter on stats (E-10, decision F7). Notifications or reminders to play. |

Data sources: the per-seat stats facts built by F-7.1 (player, `is_guest`, deck, commander key, color identity, bracket at game start with its rule-set version (F2), pod size, placement, won and drew flags, `no_winner`, game end time), which are refreshed by the arq worker (F-1.9) after each saved game or admin amendment (S-6.7.6) and rebuilt in full every week (S-7.1.6). "Counted game", "win", "draw", "small sample" and "player" have the same meaning as in E-7. In particular: abandoned games never count (D8); a draw is not a win and draws are shown separately (D6, X1); guests are counted, and their history moves to their account when claimed or merged (C4, S-7.1.7); the small-sample threshold is the playgroup's (default 5, F1).

Decisions applied in this epic:
- **Weeks and time zone (B8)**: weeks start on ISO Monday. Days and weeks are taken in the caller's time zone: their profile override if set, otherwise the playgroup's time zone (set by the owner; X8). For a player scope, where no single playgroup applies, the fallback is the viewed player's home playgroup, then the playgroup of their most recent counted game, then UTC. Every response states the time zone used.
- **Streaks (F6)**: a play streak counts weeks, and the current week is a grace period.
- **Personal activity across playgroups (F6)**: a player's personal activity, streaks and profile totals count games from all of that player's playgroups, including playgroups they have left (C7). Games from playgroups the caller doesn't share are only ever counted, never listed.
- **Meta shares (F6)**: the top 8 values plus an "Other" bucket that can be expanded.
- **Profile privacy (C8)**: visitors see the owner's profile layout; the owner can hide sections (S-8.3.5). Spend and collection value are private by default and shown to a playgroup's members only if the owner opts in (S-8.3.6).

---

## F-8.1 — Activity tracking

How often each player and playgroup plays: games per day, week and month, play and win streaks, and a calendar heatmap.

Phase 2 · Priority Should

#### S-8.1.1 — Activity aggregates API

**As a** player **I want** game counts per day, week or month **so that** I can see how often I and my playgroup play.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, shared | 2 | F-7.1, F-2.4, F-2.2 |

**Acceptance criteria**
- **Given** `GET /activity?player_id=A&period=week&from=2026-01-05&to=2026-03-29` **When** called **Then** it returns one bucket per ISO week (Monday to Sunday) in the range, including weeks with 0 games, each with games played and total minutes played. (B8)
- **Given** a caller whose profile time zone override is America/New_York and a game that ended at 2026-03-02 04:30 UTC **When** bucketed with `period=day` **Then** it counts on 2026-03-01; **Given** the caller has no override and the request is `playgroup_id=P` with P's time zone Europe/Madrid **Then** it counts on 2026-03-02. The response states the time zone used. (B8)
- **Given** `period=day` and no range **When** called **Then** it returns the last 365 days (the calendar heatmap data), one entry per day.
- **Given** `player_id=A` where A plays in playgroups P and Q and the caller shares only P with A **When** called **Then** the buckets count A's games from both P and Q (and from any playgroup A has left), but only counts and minutes are returned, never game details. (F6, C7)
- **Given** `playgroup_id=P` instead of a player **When** called **Then** buckets count distinct games in P, including games with guests and games of former members; **Given** a playgroup the caller is not a member of **Then** the response is 403; **Given** a player who shares no playgroup with the caller **Then** the response is 404, so the player's existence is not revealed.
- **Given** A has hidden the "Activity & streaks" section of their profile (S-8.3.5) **When** another player calls `player_id=A` **Then** the response is 403 `section_hidden`; playgroup-scope activity still counts A's games.
- **Given** `player_id` of a guest in a playgroup the caller belongs to **When** called **Then** the guest's activity is returned the same way as a member's.
- **Given** a game abandoned by the host or the idle timeout **When** activity is computed **Then** it is not counted.

**Tasks**
- [ ] T-8.1.1.1 (api) `activity` service using the seat facts, with time-zone-aware bucketing in SQL (`date_trunc` … `AT TIME ZONE`, ISO weeks) and zero-filled buckets
- [ ] T-8.1.1.2 (api) Schemas, router and membership check through the playgroups service interface
- [ ] T-8.1.1.3 (api) Tests for time-zone boundaries, empty buckets and Monday week starts
- [ ] T-8.1.1.4 (shared) Regenerate api-client types
- [ ] T-8.1.1.5 (api) Time-zone resolution (profile override, playgroup `time_zone`, player-scope fallbacks, UTC) read through the players and playgroups service interfaces; cross-playgroup counting for a player scope; tests for each fallback and for a playgroup the caller doesn't share

#### S-8.1.2 — Play and win streaks

**As a** player **I want** to see my current and longest streaks **so that** I can track my habits and hot runs.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api | 2 | S-8.1.1, S-7.1.7 |

**Acceptance criteria**
- **Given** a player who played at least one counted game in each of the last 5 ISO weeks (Monday to Sunday, in the time zone resolved as in S-8.1.1) **When** calling `GET /activity/streaks?player_id=A` **Then** `play_streak.current=5` (weeks), with start and end dates. (B8, F6)
- **Given** the current week has no games yet but last week did **When** streaks are computed **Then** the current play streak is still active; it breaks only after a full week passes without a game. (F6)
- **Given** A played in week 1 only in playgroup P and in week 2 only in playgroup Q **When** streaks are computed **Then** both weeks count toward one play streak, because personal streaks combine all of A's playgroups; the win streak likewise orders A's games from every playgroup by end time. (F6)
- **Given** game results in end-time order W, W, L, W, W, W **When** streaks are computed **Then** `win_streak.current=3`, `win_streak.longest=3` and `games_since_last_win=0`.
- **Given** game results in end-time order W, W, D (a draw) **When** streaks are computed **Then** `win_streak.current=0`, `win_streak.longest=2` and `games_since_last_win=1`, because a draw is not a win; the draw still counts toward the play streak.
- **Given** a guest's history was merged into A (S-7.1.7) **When** streaks are computed **Then** the merged games are included in A's streaks.
- **Given** a player with no counted games **When** called **Then** every streak is 0 and no error is returned.

**Tasks**
- [ ] T-8.1.2.1 (api) Streak calculations (play by week, win, games since last win) in the activity service
- [ ] T-8.1.2.2 (api) Router and tests for the grace week, ties in end time, draws, merged guest history, games across playgroups and empty history

#### S-8.1.3 — Web activity view with calendar heatmap

**As a** web user **I want** an activity page with a calendar heatmap, streaks and a frequency chart **so that** I can see my playing habits at a glance.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | web, api | 2 | S-8.1.1, S-8.1.2, F-7.4 |

**Acceptance criteria**
- **Given** the activity page **When** it loads **Then** it shows tiles for current and longest play streak and win streak, a Recharts bar chart of games per week or month (user's choice), and a 365-day calendar heatmap.
- **Given** the heatmap **When** shown **Then** it uses 5 intensity levels with a legend, and every cell has an accessible label such as "3 games on 12 Mar 2026", so color is never the only cue.
- **Given** a keyboard user **When** they focus the heatmap and press the Arrow keys **Then** focus moves day by day and week by week and the focused cell's label is shown and announced.
- **Given** the dashboard widget catalog (F-7.4) **When** this story ships **Then** "Activity heatmap", "Streaks" and "Games per period" widgets are marked available, and "Activity heatmap" joins the curated default layouts (F4) for users who have not saved their own.
- **Given** the heatmap's weeks **When** drawn **Then** each column runs Monday to Sunday (ISO weeks, B8) in the time zone stated by the API.

**Tasks**
- [ ] T-8.1.3.1 (web) Heatmap component built with CSS grid/SVG (no new dependency), with roving focus
- [ ] T-8.1.3.2 (web) Streak tiles and Recharts frequency chart with table alternative
- [ ] T-8.1.3.3 (web, api) Register the three activity widgets in the catalog and the web widget registry
- [ ] T-8.1.3.4 (web) Vitest tests for cell labels, keyboard navigation and intensity levels

#### S-8.1.4 — Mobile activity view

**As a** mobile user **I want** my streaks and heatmap on my phone **so that** I can check my activity anywhere.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | mobile | 2 | S-8.1.1, S-8.1.2, F-7.4, S-7.2.8 |

**Acceptance criteria**
- **Given** the activity screen **When** it opens **Then** it shows streak tiles and a calendar heatmap of the last 26 weeks that can be scrolled back to 52 weeks.
- **Given** a heatmap cell **When** tapped **Then** a label shows the date and number of games; screen readers read the same label on focus.
- **Given** games per week or month (user's choice) **When** shown **Then** they appear as a victory-native bar chart from the S-7.2.8 kit, with its "Show as list" alternative.
- **Given** the mobile dashboard (F-7.4) **When** this story ships **Then** the activity widgets can be added to it, and the "Games per period" widget renders as a compact chart.

**Tasks**
- [ ] T-8.1.4.1 (mobile) Heatmap built from plain React Native views, horizontally scrollable
- [ ] T-8.1.4.2 (mobile) Streak tiles and games-per-period chart (S-7.2.8 kit); mobile renderers for the activity widgets
- [ ] T-8.1.4.3 (mobile) Jest tests for cell labels, streak tiles and the chart's list toggle

---

## F-8.2 — Meta shift analysis

How the commanders, colors and brackets played change over time, for one player and across the whole playgroup, including which ones are rising or falling.

Phase 2 · Priority Should

#### S-8.2.1 — Meta share over time API

**As a** playgroup member **I want** the share of commanders, colors and brackets played per period **so that** I can see how our meta changes.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api, shared | 2 | F-7.1, F-7.2, S-7.2.5 |

**Acceptance criteria**
- **Given** playgroup P had 10 seats in March 2026, 4 of them on Atraxa **When** calling `GET /meta/share?scope=playgroup&id=P&dimension=commander&period=month` **Then** March shows Atraxa with `seats=4` and `share=0.40`.
- **Given** `dimension` is one of `commander`, `color_identity`, `color` or `bracket` **When** called **Then** each period lists the top 8 values (`top` can be 3–20) plus an "Other" bucket, and shares in each period add up to 1.0 (±0.001), except `color`, where a seat counts toward each of its colors and the response says shares overlap. (F6)
- **Given** the "Other" bucket **When** returned **Then** it carries its total seats and share and the number of values in it; **Given** `expand_other=true` **Then** it also lists each value inside "Other" with its seats and share, so clients can expand it. (F6)
- **Given** `scope=player&id=A` **When** called **Then** shares are computed over A's seats only, from all of A's playgroups (F6); **Given** A has hidden the "Meta" section of their profile (S-8.3.5) and the caller is someone else **Then** the response is 403 `section_hidden`.
- **Given** `scope=playgroup` **When** shares are computed **Then** seats played by guests are included.
- **Given** a period with fewer seats than the playgroup's small-sample threshold (default 5; for a player scope that spans several playgroups, the highest of their thresholds) **When** returned **Then** that period has `low_sample=true` and the response states the threshold used.
- **Given** `dimension=bracket` **When** a seat is counted **Then** it uses the bracket recorded at game start in the seat facts (S-7.2.5, F2), not the deck's current bracket; a seat with no assessment at game start counts in an `unassessed` value.

**Tasks**
- [ ] T-8.2.1.1 (api) `meta` service on seat facts: per-period grouping, top-N and "Other"
- [ ] T-8.2.1.2 (api) Schemas, router (month and quarter periods) and membership check
- [ ] T-8.2.1.3 (api) Tests for share sums, overlapping color shares, unassessed brackets, top-N cut and the expanded "Other" list
- [ ] T-8.2.1.4 (shared) Regenerate api-client types

#### S-8.2.2 — Meta movers

**As a** playgroup member **I want** to see which commanders, colors and brackets are rising or falling **so that** I notice meta shifts without reading charts.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 3 | api | 2 | S-8.2.1 |

**Acceptance criteria**
- **Given** the last complete month and the month before **When** calling `GET /meta/movers?scope=playgroup&id=P&dimension=commander` **Then** it returns the 5 largest share increases and the 5 largest decreases, each with both shares and the change in percentage points.
- **Given** a value that appears for the first time ever in that scope **When** movers are computed **Then** it is listed under `new_this_period`.
- **Given** either compared period has fewer seats than the small-sample threshold used by S-8.2.1 **When** called **Then** the response sets `low_sample=true` and still returns the data.

**Tasks**
- [ ] T-8.2.2.1 (api) Period comparison and first-appearance detection in the meta service
- [ ] T-8.2.2.2 (api) Router and tests

#### S-8.2.3 — Web meta shift view

**As a** web user **I want** a chart of meta share over time **so that** I can see trends in what we play.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | web, api | 2 | S-8.2.1, S-8.2.2, F-7.4 |

**Acceptance criteria**
- **Given** the meta page **When** it loads **Then** it shows a Recharts 100% stacked chart of share per period, with toggles for dimension (commander, color identity, color, bracket), scope (me or playgroup) and period (month or quarter).
- **Given** the legend **When** the user clicks or presses Enter on an item **Then** that series is hidden or shown; legend items are reachable by Tab.
- **Given** "Show as table" **When** toggled **Then** the same data appears as a table with seat counts per period.
- **Given** the "Other" series **When** the user activates "Expand Other" (by click or keyboard) **Then** the values inside "Other" are listed for the selected period with seats and share, and "Collapse" hides them again. (F6)
- **Given** the movers panel **When** shown **Then** it lists rising, falling and new values with the change in percentage points and a small-sample badge where flagged.
- **Given** the dashboard catalog (F-7.4) **When** this story ships **Then** "Meta share" and "Meta movers" widgets are marked available.

**Tasks**
- [ ] T-8.2.3.1 (web) Meta page with stacked chart, toggles and table alternative
- [ ] T-8.2.3.2 (web) Movers panel
- [ ] T-8.2.3.3 (web, api) Register meta widgets in the catalog and web widget registry
- [ ] T-8.2.3.4 (web) Vitest tests for toggles, legend keyboard use and table view

#### S-8.2.4 — Mobile meta view

**As a** mobile user **I want** meta shares and movers on my phone **so that** I can check the meta between games.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 5 | mobile | 2 | S-8.2.1, S-8.2.2, S-7.2.8 |

**Acceptance criteria**
- **Given** the meta screen **When** it opens **Then** it shows a victory-native 100% stacked bar chart of share per period (S-7.2.8 kit) for the chosen dimension and scope, followed by the selected period's top values with share and seat count.
- **Given** the chart **When** the user taps a period's bar or uses the period picker **Then** that period becomes the selected one and the list below updates; the picker remains the accessible alternative to tapping bars.
- **Given** the chart's "Show as list" toggle **When** turned on **Then** the same data is shown as a list per period with seat counts, and periods with `low_sample=true` carry the small-sample badge.
- **Given** the movers section **When** shown **Then** rising and falling values show an up or down marker plus the change in percentage points as text (not color alone).
- **Given** the selected period's list **When** the user taps "Other" **Then** it expands to list the values inside it with share and seat count. (F6)

**Tasks**
- [ ] T-8.2.4.1 (mobile) Meta screen with dimension, scope and period pickers
- [ ] T-8.2.4.2 (mobile) Movers section and Jest tests
- [ ] T-8.2.4.3 (mobile) Stacked share chart using the S-7.2.8 kit, with bar tap selecting the period
- [ ] T-8.2.4.4 (mobile) Jest tests for period selection and the list toggle

---

## F-8.3 — Personal dashboard

Each player's profile page: a summary of their activity and stats, laid out with the F-7.4 widget system. Visitors see the owner's layout minus any sections the owner hides; spend and collection value widgets arrive with E-9 (F-9.4, F-9.5) and are private unless the owner shares them with a playgroup (C8).

Phase 2 · Priority Should

#### S-8.3.1 — Profile summary API

**As a** player **I want** a single summary of a player's record **so that** a profile page loads in one request.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, shared | 2 | S-8.1.2, F-7.1, F-2.2 |

**Acceptance criteria**
- **Given** player A with counted games **When** calling `GET /players/A/summary` **Then** it returns total games, wins, draws, win rate (with `games`, `low_sample` and interval as in F-7.1; a draw is not a win), current and longest play streak, current win streak, most-played commander and deck, last played date and the last 5 games.
- **Given** A plays in playgroups P and Q and the caller shares only P **When** the summary is built **Then** total games, wins, draws, win rate, streaks, most-played commander and last played date count A's games from all of A's playgroups (F6), while the last 5 games and the most-played deck link include only games and decks the caller can see (shared playgroups, C5).
- **Given** A's games span several playgroups **When** `low_sample` is computed **Then** the highest small-sample threshold among A's playgroups is used (default 5).
- **Given** A left playgroup R **When** A requests their own summary **Then** R's games are included in every figure and in A's last 5 games, read-only (C7).
- **Given** two commanders tied for most played **When** the summary is built **Then** the one played most recently wins the tie.
- **Given** the caller shares a playgroup with A **When** the summary includes A's most-played deck **Then** the deck's name and link are included, since decks are visible to members of a shared playgroup (C5).
- **Given** A claimed or absorbed a guest's history (S-7.1.7) **When** the summary is built **Then** those games are included.
- **Given** a caller who shares no playgroup with A **When** requesting **Then** the response is 404, so the player's existence is not revealed.
- **Given** A has hidden profile sections (S-8.3.5) **When** someone else requests the summary **Then** the fields of hidden sections are left out of the response, not just hidden in the UI.
- **Given** the 10,000-game performance fixture **When** requested **Then** p95 response time is under 300 ms.

**Tasks**
- [ ] T-8.3.1.1 (api) Summary service composing the stats and activity services through their public interfaces
- [ ] T-8.3.1.2 (api) Schemas, router, visibility rule and tests
- [ ] T-8.3.1.3 (shared) Regenerate api-client types

#### S-8.3.2 — Web personal dashboard

**As a** web user **I want** a profile page that shows my activity and stats as widgets I can arrange **so that** my profile highlights what I care about.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | web, api | 2 | S-8.3.1, S-8.1.3, S-8.2.3, F-7.4 |

**Acceptance criteria**
- **Given** a player's profile page **When** it loads **Then** it shows a summary header (from S-8.3.1) and a widget grid whose widgets are filtered to that player.
- **Given** I am viewing my own profile **When** I enter edit mode **Then** I can drag, resize, pin, add and remove widgets with the same mouse and keyboard controls as the F-7.4 dashboard, and the layout is saved to the `profile_web` surface.
- **Given** another member views my profile **When** it loads **Then** they see my arrangement read-only, with no edit controls, minus any sections I have hidden (S-8.3.5) and any spend or collection-value widget I haven't shared with their playgroup (S-8.3.6). (C8)
- **Given** a web profile **When** it is shown at the `lg`, `md` or `sm` breakpoint **Then** the owner's layout for that breakpoint is used (one layout per breakpoint, F4), falling back to the default positions for a breakpoint never saved.
- **Given** a widget that needs data not available yet (such as spend before F-9.4) **When** the profile renders **Then** that widget is not shown, rather than shown empty.

**Tasks**
- [ ] T-8.3.2.1 (web) Profile route with summary header and widget grid in player context
- [ ] T-8.3.2.2 (api) Read-only `GET /players/{id}/layouts/profile_web` for visitors, with the same visibility rule as S-8.3.1
- [ ] T-8.3.2.3 (web) Reuse the F-7.4 grid, keyboard handler and catalog drawer with a player filter preset
- [ ] T-8.3.2.4 (web) Vitest tests for owner edit vs. visitor read-only

#### S-8.3.3 — Mobile personal dashboard

**As a** mobile user **I want** my profile as a reorderable list of widgets **so that** my phone shows my own record the way I like it.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | mobile | 2 | S-8.3.1, S-8.1.4, F-7.4 |

**Acceptance criteria**
- **Given** a player's profile screen **When** it opens **Then** it shows the summary header and a list of widgets filtered to that player.
- **Given** my own profile in edit mode **When** I long-press and drag a widget **Then** it reorders and saves to `profile_mobile`; **Given** I use the "Move up"/"Move down" buttons or the screen reader's accessibility actions instead **Then** the result is the same.
- **Given** another member's profile **When** viewed **Then** it shows the owner's `profile_mobile` arrangement read-only, minus hidden sections (S-8.3.5) and unshared spend or collection-value widgets (S-8.3.6). (C8)
- **Given** "Reset to default" **When** confirmed **Then** the saved profile layout is deleted and the default is shown.

**Tasks**
- [ ] T-8.3.3.1 (mobile) Profile screen with summary header and widget list in player context
- [ ] T-8.3.3.2 (mobile) Reuse the F-7.4 reorderable list, move buttons and catalog sheet for `profile_mobile`
- [ ] T-8.3.3.3 (mobile) Jest tests for owner edit vs. visitor read-only

#### S-8.3.4 — Spend and collection value widgets on the personal dashboard

**As a** player who tracks purchases and my collection **I want** my recent spend and my collection value on my profile **so that** I see my budget next to my play.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Could | 5 | api, web, mobile | 3 | S-8.3.2, S-8.3.3, S-8.3.6, F-9.4, F-9.5 |

**Acceptance criteria**
- **Given** F-9.4 and F-9.5 are shipped **When** the catalog is listed **Then** "Spend" and "Collection value" widgets are marked available for the `profile_web` and `profile_mobile` surfaces.
- **Given** I view my own profile with the Spend widget **When** it renders **Then** it shows spend this month and over the last 3 months, one total per currency paid when currencies are mixed (G1), with a link to the full spend report.
- **Given** I view my own profile with the Collection value widget **When** it renders **Then** it shows the current collection value in my chosen currency (G1) with a link to the valuation page.
- **Given** another member of playgroup P views my profile **When** it renders **Then** the Spend and Collection value widgets are shown only if I have shared that figure with P (S-8.3.6); by default they are not shown, and the API returns 403 for their data. (C8)
- **Given** I have no purchases logged **When** the Spend widget renders **Then** it shows "No purchases logged yet" with a link to the purchase log.

**Tasks**
- [ ] T-8.3.4.1 (api) Catalog entries for the Spend and Collection value widgets, with their data read through the collection domain's public service interface and gated by the S-8.3.6 sharing rule
- [ ] T-8.3.4.2 (web) Spend and Collection value widget renderers
- [ ] T-8.3.4.3 (mobile) Spend and Collection value widget renderers
- [ ] T-8.3.4.4 (api, web, mobile) Tests for owner vs. visitor visibility with sharing off and on, mixed currencies and the empty state

#### S-8.3.5 — Hide profile sections from visitors

**As a** player **I want** to hide parts of my profile from other members **so that** I choose what others see about my play.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 5 | api, web, mobile | 2 | S-8.3.1, S-8.3.2, S-8.3.3, S-2.2.5 |

**Acceptance criteria**
- **Given** my profile settings **When** I open "Profile privacy" **Then** I can hide each of these sections from visitors: Record (games, wins, draws, win rate), Activity & streaks, Recent games and Meta; every section is visible by default except spend and collection value, which S-8.3.6 governs. (C8)
- **Given** I hide a section **When** another member opens my profile **Then** that part of the summary header and every widget belonging to that section (each catalog widget declares its profile section) are absent, and the API leaves their data out of the summary and returns 403 `section_hidden` for the player-scoped activity and meta endpoints; it is not just hidden in the UI.
- **Given** I hide a section **When** I view my own profile **Then** I still see it, marked "Hidden from others".
- **Given** a hidden section **When** playgroup-scoped stats, activity, meta or standings are shown **Then** my games still count there under my name; hiding a profile section never removes me from playgroup stats.
- **Given** a keyboard or screen-reader user **When** they use the privacy settings **Then** each section is a labelled switch that works without a mouse.

**Tasks**
- [ ] T-8.3.5.1 (api) Read hidden sections through E-2's `profile_visibility()` (S-2.2.5 owns the settings storage and migration)
- [ ] T-8.3.5.2 (api) Enforce hidden sections in the summary, visitor layout, activity and meta services, plus a `profile_section` field on catalog widgets; tests for owner vs. visitor
- [ ] T-8.3.5.3 (web) Privacy settings page and "Hidden from others" markers on the own profile
- [ ] T-8.3.5.4 (mobile) Privacy settings screen and the same markers
- [ ] T-8.3.5.5 (web, mobile) Vitest/Jest tests for the switches and visitor rendering

#### S-8.3.6 — Opt in to share spend and collection value with a playgroup

**As a** player **I want** my spend and collection value private unless I choose to share them with a playgroup **so that** money figures are never shown without my consent.

| Priority | Points | Platform | Phase | Depends on |
|---|---|---|---|---|
| Should | 3 | api, web, mobile | 3 | S-8.3.5, F-9.4, F-9.5 |

**Acceptance criteria**
- **Given** a new or existing player **When** their privacy settings are read **Then** spend and collection value are shared with no playgroup. (C8)
- **Given** I belong to playgroups P and Q **When** I turn on "Share spend" and "Share collection value" for P only **Then** members of P can see those figures on my profile and members of Q (who aren't also in P) cannot.
- **Given** any endpoint that returns my spend or collection value **When** someone other than me calls it **Then** it allows the call only through `financials_visible_to(viewer_id, owner_id, kind)` in the players service interface, and otherwise returns 403.
- **Given** I turn sharing off again **When** the change is saved **Then** visitors stop seeing the figures on their next request.

**Tasks**
- [ ] T-8.3.6.1 (api) Per-playgroup sharing flags for spend and collection value (default off), with an Alembic migration, and `financials_visible_to` in the players service interface
- [ ] T-8.3.6.2 (web) Sharing switches per playgroup in the privacy settings page
- [ ] T-8.3.6.3 (mobile) Sharing switches per playgroup in the privacy settings screen
- [ ] T-8.3.6.4 (api) Tests: default private, shared with one playgroup only, turning sharing off

---

## Open questions

None open. Resolved: see OPEN-QUESTIONS.md decisions log.
