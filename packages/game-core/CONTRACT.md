# Game state and event contract

Version: schema `v` = 1. Story: S-6.1.1. Architecture: `docs/architecture.md` section 6.

This file is the single written contract for the live game. Two reducers implement it: TypeScript in `packages/game-core/src` and Python in `apps/api/app/games/core/`. They must produce byte-identical canonical JSON for the same events. If this file and a reducer disagree, the reducer is wrong. The rules for changing it, and for when `v` bumps, are in 1.1. A change to the shape of the state, an event or an error code needs a matching vector in `vectors/`.

Words in capitals (MUST, MUST NOT, MAY) are normative.

## 1. Conventions

- All JSON is UTF-8. Every number in an envelope, a payload or the state is an **integer** in the range -(2^53 - 1) to 2^53 - 1. There are no floats and no money.
- Keys are `snake_case`. Enumerated values are lowercase `snake_case` strings.
- **Seat ids** are integers 1 to 8. A seat id is stable for the life of the game: reordering seats changes `order`, never a seat id. In a lobby that has never reordered, seat id equals turn position.
- **Ids** (`game_id`, `player_id`, `deck_version_id`, `card_id`) are opaque non-empty strings. The reducers never parse them. Only `event_id` has a checked format.
- **Absent versus null.** In the _state_, every key is always present and "no value" is `null`. In an _envelope_, `seq` and `ts` are **absent** (never `null`) until the server assigns them. In an event _payload_, a key marked optional (`name?`) may be absent or `null`, and both mean "not given"; a reducer MUST NOT distinguish them. The serializer never invents or drops keys.
- Timestamps are strings `YYYY-MM-DDTHH:MM:SS.mmmZ` (UTC, exactly 3 fractional digits). A reducer MUST NOT parse, compare or do arithmetic on them (section 7).

### 1.1 Versioning

`v` versions three things together: the **envelope** (section 2), the **registered payload shapes** (section 4) and the **state shape** (section 3). It does not version behaviour that the contract already registers. The aim is that every event ever stored stays replayable.

| Change                                                                             | Bumps `v`? | Why                                                                                                                                                         |
| ---------------------------------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Filling in the handler of an event type already registered in section 4            | No         | The type, its payload shape and the state shape are already fixed. Until the first release that stores logs, handlers may also be corrected freely.         |
| Changing what an already released handler does to existing logs                    | Yes        | A stored log would replay to a different state. The reducers keep the old behaviour for events with the old `v`.                                            |
| Adding an event type                                                               | No         | Old logs contain none. An older reducer rejects it with `unknown_event_type`, and the server is authoritative, so clients must update.                      |
| Adding an optional payload key                                                     | No         | Reducers ignore unknown payload keys, and old events simply lack it.                                                                                        |
| Adding a required payload key, or removing, renaming or retyping any payload key   | Yes        | Old events would become invalid or mean something else.                                                                                                     |
| Adding a state key                                                                 | No         | States are derived: the server rebuilds stored snapshots from the log (S-6.3.8) after a deploy, and canonical comparisons always use freshly folded states. |
| Removing, renaming or retyping a state key, or changing what an existing key means | Yes        | Stored snapshots and clients would misread it.                                                                                                              |
| Adding an error code                                                               | No         | Codes are strings; clients branch on the codes they know and treat the rest as a generic rejection.                                                         |
| Changing the envelope keys or their rules, or the canonical JSON rules             | Yes        | Every stored event and every comparison would change.                                                                                                       |

When `v` bumps to N, every reducer keeps accepting **every** version still present in a stored log (`SUPPORTED_VERSIONS` lists them all) and applies each event under the rules of its own `v`. The state's `v` is the current version, N. An integer `v` that is not in the supported list is `unsupported_version`.

## 2. Event envelope

Every event, on the wire and in the log, is a JSON object with exactly these keys. Keys not listed are rejected with `invalid_event`.

| Key                 | Type                       | Rule                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `event_id`          | string                     | UUID v4 generated by the client, lowercase hyphenated: `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`. It makes submission idempotent.                                                                                                                                                                                                                                      |
| `game_id`           | string                     | The game this event belongs to. Must equal the state's `game_id`.                                                                                                                                                                                                                                                                                                                                    |
| `seq`               | integer, optional (absent) | Server-assigned position in the game's log, starting at 1 and increasing by exactly 1. Absent until the event is acked or broadcast. When present it MUST be an integer of at least 1 and at most 2^53 - 1; `null`, `0`, a negative number, a fraction or a non-number is `invalid_event`. A well-formed `seq` that is not `last_seq + 1` is `out_of_order` (section 6.2).                           |
| `type`              | string                     | One of the registered types in section 4.                                                                                                                                                                                                                                                                                                                                                            |
| `payload`           | object                     | The payload for `type` (section 4). Never `null`, never an array (`invalid_event`). May be `{}`. Its content MUST be a canonical value (5.1); if not, the event is `invalid_payload` (section 6.2, step 5).                                                                                                                                                                                          |
| `actor`             | object                     | Exactly the two keys `kind` and `player_id`: `{"kind": "player", "player_id": <non-empty string>}` or `{"kind": "system", "player_id": null}`. `system` is used by server jobs, such as idle abandonment. Any other shape is `invalid_event`: a missing key, an extra key, another `kind`, a `player` whose `player_id` is `null`, `""` or not a string, a `system` whose `player_id` is not `null`. |
| `on_behalf_of_seat` | integer or null            | The seat the sender acted for when the host controls a seat that is not their own (S-6.8.2). `null` otherwise. Always present. 1 to 8 when not `null`.                                                                                                                                                                                                                                               |
| `ts`                | string, optional (absent)  | Server UTC time of acceptance, `YYYY-MM-DDTHH:MM:SS.mmmZ`. Absent until the server stamps it; when present it MUST be a string (`null` or any other type is `invalid_event`). **Reducers MUST NOT read, parse or validate it beyond "is a string"**: a string in the wrong format is accepted by `apply`. Checking the format is the server's job, when it stamps `ts`.                              |
| `v`                 | integer                    | Schema version. The only supported value is 1. It MUST be an integer in the safe range; a boolean, `null`, a string or a fraction is `invalid_event` (not `unsupported_version`). An integer other than 1, such as 0, 2 or -1, is `unsupported_version`. `v` is checked first (section 6.2).                                                                                                         |

**Well-formed strings.** Every string in the envelope (`event_id`, `game_id`, `type`, `ts`, `actor.player_id`) MUST be well-formed Unicode: it MUST NOT contain a lone surrogate, that is a code point in U+D800 to U+DFFF that is not half of a valid high-then-low surrogate pair (in Python, any `str` containing a code point in that range). A string that does is `invalid_event`. Such a string could not be serialized in canonical JSON (5.4) or stored as `jsonb`. The check sits in step 4 of 6.2, after the `type` checks of step 3, so a `type` with a lone surrogate, which cannot be a registered type, is `unknown_event_type`, not `invalid_event`.

The required keys are `event_id`, `game_id`, `type`, `payload`, `actor`, `on_behalf_of_seat` and `v`. Whether `actor` is allowed to send a given event is the server's job (trust mode, host and spectator rules, S-6.3.3), not the reducer's.

An event is an _optimistic_ event while `seq` is absent. A reducer applies it as if its `seq` were `last_seq + 1` (section 6).

## 3. State

The state is a JSON object. Every key below is always present.

### 3.1 Top level

| Key                  | Type                          | Meaning                                                                                                                                                                                                                                                                                           |
| -------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `v`                  | integer                       | Schema version of the state shape: the current version, 1 (see 1.1).                                                                                                                                                                                                                              |
| `game_id`            | string                        | The game.                                                                                                                                                                                                                                                                                         |
| `phase`              | string                        | `lobby`, `active`, `ending`, `finished` or `abandoned`.                                                                                                                                                                                                                                           |
| `resume_phase`       | string or null                | The phase `game_resumed` returns to (`lobby`, `active` or `ending`). Non-null only while `phase` is `abandoned`.                                                                                                                                                                                  |
| `settings`           | object                        | See 3.2.                                                                                                                                                                                                                                                                                          |
| `control_mode`       | string                        | `own_seat` (the default) or `host_all` (S-6.8.2).                                                                                                                                                                                                                                                 |
| `seats`              | array of seat objects         | Sorted by ascending `seat`. At most 8. See 3.3.                                                                                                                                                                                                                                                   |
| `order`              | array of integers             | Seat ids in turn order. A permutation of the ids in `seats`.                                                                                                                                                                                                                                      |
| `active_seat`        | integer or null               | Seat whose turn it is. `null` outside `active` and `ending`.                                                                                                                                                                                                                                      |
| `turn`               | integer                       | Individual turn counter. 0 before `game_started`.                                                                                                                                                                                                                                                 |
| `round`              | integer                       | The game's "turn count" for results and stats (D4). 0 before `game_started`.                                                                                                                                                                                                                      |
| `monarch`            | integer or null               | Seat id of the monarch.                                                                                                                                                                                                                                                                           |
| `initiative`         | integer or null               | Seat id holding the initiative.                                                                                                                                                                                                                                                                   |
| `commander_damage`   | object of objects of integers | `commander_damage[target seat id as a decimal string][source_key]`. `source_key` is `"<source seat id>:<commander index>"`, for example `"1:0"`. A target key and a source key are created by the first damage that reaches them and are never removed, even when a tally returns to 0 (S-6.1.4). |
| `no_winner`          | boolean                       | `true` only for a full draw (S-6.1.3, S-6.7.1).                                                                                                                                                                                                                                                   |
| `winner_seat`        | integer or null               | The provisional or final winner. `null` while none, and when `no_winner` is `true`.                                                                                                                                                                                                               |
| `win_condition`      | string or null                | A D5 code (section 3.4) or `null`.                                                                                                                                                                                                                                                                |
| `win_condition_note` | string                        | Free text, required by S-6.7 when `win_condition` is `other`. `""` when none.                                                                                                                                                                                                                     |
| `started_at`         | string or null                | The `started_at` carried in the `game_started` payload, otherwise `null`.                                                                                                                                                                                                                         |
| `voided`             | array of integers             | Seqs skipped when state was rebuilt after a revert because they became invalid (S-6.6.1). Ascending, no duplicates. `[]` until S-6.6.1.                                                                                                                                                           |
| `reverted`           | array of integers             | Seqs of the events currently excluded by an effective `event_reverted` (S-6.6.1, see 6.5): an event whose revert was itself reverted is not in it. Includes the seq of an `event_reverted` event that was itself reverted. Ascending, no duplicates. `[]` initially.                              |
| `last_seq`           | integer                       | `seq` of the last applied event. 0 before any event.                                                                                                                                                                                                                                              |

### 3.2 `settings`

Settings are game data, never constants (D3). They can change only in the `lobby` phase.

| Key                          | Type    | Range or values    | Default  |
| ---------------------------- | ------- | ------------------ | -------- |
| `starting_life`              | integer | 1 to 999           | 40       |
| `commander_damage_threshold` | integer | 1 to 99            | 21       |
| `poison_threshold`           | integer | 1 to 99            | 10       |
| `trust_mode`                 | string  | `casual`, `strict` | `casual` |

A value outside its range, or a `trust_mode` that is not `casual` or `strict`, is rejected with `invalid_setting`. That check belongs to the `game_created` and `settings_changed` handlers, which arrive with story S-6.1.6 (S-6.1.3 to S-6.1.5 name the ranges). **`initialState` does not validate ranges**: it stores the values it is given, so both implementations keep an out-of-range value unchanged until S-6.1.6.

### 3.3 Seat object

| Key                  | Type                              | Meaning                                                                                                                                                                                           |
| -------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `seat`               | integer                           | Seat id, 1 to 8.                                                                                                                                                                                  |
| `player_id`          | string or null                    | The player at the seat. `null` for a guest seat the host created with no device bound yet (S-6.8.4).                                                                                              |
| `name`               | string                            | Display name. Any Unicode. `""` is allowed.                                                                                                                                                       |
| `deck_version_id`    | string or null                    | Assigned deck version, or `null`.                                                                                                                                                                 |
| `commanders`         | array of 1 or 2 commander objects | `{"card_id": string or null, "name": string}`. The unnamed default is `[{"card_id": null, "name": ""}]`. The index in this array is the "commander index" used in `commander_damage` source keys. |
| `life`               | integer                           | Not clamped: it can be negative.                                                                                                                                                                  |
| `poison`             | integer                           | At least 0.                                                                                                                                                                                       |
| `energy`             | integer                           | At least 0.                                                                                                                                                                                       |
| `experience`         | integer                           | At least 0.                                                                                                                                                                                       |
| `commander_cast`     | array of integers                 | Cast count per commander, same length and order as `commanders`. Commander tax is `2 * count` and is derived, never stored.                                                                       |
| `elimination_reason` | string or null                    | Why the seat was eliminated: `life`, `commander_damage`, `poison`, `concession` or `declared`. `null` while the seat is in the game.                                                              |
| `eliminated_by`      | integer or null                   | Seat id that caused the elimination, or `null` (no source, or a concession or declared elimination).                                                                                              |
| `eliminated_at_seq`  | integer or null                   | `seq` of the event that eliminated the seat. Equal values on several seats mean "eliminated at the same time" (D6). `null` while in the game.                                                     |
| `eliminated_round`   | integer or null                   | The state's `round` at the moment the event that eliminated the seat was applied. `null` while in the game.                                                                                       |
| `elimination_cause`  | string or null                    | The D5 code (3.4) given with a host `player_eliminated`, otherwise `null`.                                                                                                                        |

**Elimination fields.** A seat is eliminated if and only if `eliminated_at_seq` is not `null`. While the seat is in the game all five elimination fields are `null`. Once it is eliminated, `elimination_reason`, `eliminated_at_seq` and `eliminated_round` are non-null, and `eliminated_by` and `elimination_cause` may be `null`.

Where the names come from:

- `eliminated_by` and `eliminated_at_seq` are the names the acceptance criteria of S-6.1.3, S-6.1.4, S-6.1.5 and S-6.1.7 use ("eliminated with reason `life`, `eliminated_by = 1` and `eliminated_at_seq` equal to that event's seq"; "`eliminated_by = null`"; "the same `eliminated_at_seq`").
- `elimination_reason` is the name S-6.7.4 and S-6.7.2 use for the reason ("`elimination_reason`"). S-6.1.3 to S-6.1.5 say "eliminated with reason `life`" without naming a field, so the S-6.7 name is used. The values are the five listed above (`commander_damage` > `poison` > `life` is the precedence, S-6.1.4).
- `eliminated_round` is the name S-6.7.4 uses ("`eliminated_round`", and "elimination reason, eliminator and round" in S-6.7.2). S-6.7.4 also stores the eliminator as `eliminated_by_seat` on the saved `GameSeat`; the state keeps the S-6.1 name `eliminated_by`, and the results writer maps one to the other.
- `elimination_cause` is **not** named by any story: the stories only name the `cause` key of the `player_eliminated` payload. The name is this contract's design.

### 3.4 D5 win-condition codes

`combat_damage`, `commander_damage`, `combo`, `alternate_win`, `poison`, `mill`, `life_drain`, `concession`, `last_standing`, `other`. There is no "draw" code: a draw is `no_winner = true`.

### 3.5 Initial state

Before any event the state is an empty lobby built by `initialState(game_id, settings?)`:

- `game_id` MUST be a non-empty string without a lone surrogate. Otherwise `initialState` **throws** (TypeScript `TypeError`; Python `TypeError` or `ValueError`; the exact class is language-specific and not part of the contract). It is not a reducer, so it never returns a reducer error code.
- `settings` is optional and may be partial. A missing key takes its default (3.2). A `settings` that is not an object, or that has a key that is not one of the four setting names, also throws.
- Values are copied as given: **neither types nor ranges are validated**. An out-of-range value (3.2) is stored unchanged. Range validation arrives with S-6.1.6.
- The `settings` given here are a **placeholder**. The log of a real game starts with `game_created`, which replaces them (4.3). They exist for vectors and tests that do not start with `game_created`.
- All keys of 3.1: `phase` `lobby`, `control_mode` `own_seat`, `seats` `[]`, `order` `[]`, every nullable key `null`, `turn` 0, `round` 0, `commander_damage` `{}`, `no_winner` `false`, `win_condition_note` `""`, `voided` `[]`, `reverted` `[]`, `last_seq` 0, `v` 1. `vectors/initial-state.json` is the reference.

## 4. Event registry

The registry is closed: a type that is not in this table is rejected with `unknown_event_type`. "Introduced by" names the story that defines the handler's behaviour; until then the handler is a no-op that only advances `last_seq`. Payload keys marked `?` are optional (absent or `null`). Unknown payload keys MUST be ignored by reducers, and they stay in the log as sent. In the payloads below, "commanders" means an array of 1 or 2 `{"card_id": string or null, "name": string}` objects.

| Type                   | Payload                                                                                | Introduced by |
| ---------------------- | -------------------------------------------------------------------------------------- | ------------- |
| `game_created`         | `settings` (the four settings, 3.2)                                                    | S-6.1.6       |
| `settings_changed`     | any of `starting_life`, `commander_damage_threshold`, `poison_threshold`, `trust_mode` | S-6.1.6       |
| `seat_joined`          | `seat`, `player_id` (string or null), `name`                                           | S-6.1.6       |
| `seat_left`            | `seat`                                                                                 | S-6.1.6       |
| `seat_moved`           | `from`, `to` (seat ids), `correction?` (boolean)                                       | S-6.1.6       |
| `seat_claimed`         | `seat`, `player_id`                                                                    | S-6.8.4       |
| `deck_assigned`        | `seat`, `deck_version_id`, `commanders`                                                | S-6.1.6       |
| `commanders_set`       | `seat`, `commanders`                                                                   | S-6.1.6       |
| `control_mode_changed` | `mode` (`own_seat` or `host_all`)                                                      | S-6.8.2       |
| `game_started`         | `first_seat`, `started_at` (timestamp; the server overwrites it, see 4.3)              | S-6.1.6       |
| `turn_passed`          | none (`{}`)                                                                            | S-6.1.6       |
| `life_changed`         | `seat`, `delta`, `source_seat?`                                                        | S-6.1.3       |
| `commander_damage`     | `source_seat`, `commander` (index), `target_seat`, `amount`                            | S-6.1.4       |
| `poison_changed`       | `seat`, `delta`, `source_seat?`                                                        | S-6.1.5       |
| `counter_changed`      | `seat`, `counter` (`energy` or `experience`), `delta`                                  | S-6.1.5       |
| `commander_cast`       | `seat`, `commander` (index), `delta`                                                   | S-6.1.5       |
| `monarch_set`          | `seat`                                                                                 | S-6.1.5       |
| `initiative_set`       | `seat`                                                                                 | S-6.1.5       |
| `drain`                | `source_seat`, `target_seat`, `amount`                                                 | S-6.4.4       |
| `group_drain`          | `source_seat`, `amount`, `gain` (boolean)                                              | S-6.1.7       |
| `player_conceded`      | `seat`                                                                                 | S-6.1.3       |
| `player_eliminated`    | `seat`, `cause?` (a D5 code)                                                           | S-6.1.3       |
| `player_revived`       | `seat`, `reason` (string)                                                              | S-6.6.5       |
| `value_set`            | `seat`, `field`, `value`, `source_seat?`, `commander?`                                 | S-6.6.5       |
| `game_end_requested`   | `winner_seat?`, `win_condition?` (a D5 code), `win_condition_note?`, `no_winner?`      | S-6.7.1       |
| `game_ended`           | `summary` (object, shape owned by S-6.7.3)                                             | S-6.7.3       |
| `game_abandoned`       | `reason` (`host` or `idle`)                                                            | S-6.2.8       |
| `game_resumed`         | none (`{}`)                                                                            | S-6.2.9       |
| `event_reverted`       | `target_seq`                                                                           | S-6.6.1       |

### 4.1 Payload value rules

- `seat`, `from`, `to`, `source_seat`, `target_seat`, `first_seat`, `winner_seat`: seat ids, 1 to 8. A seat id that is an integer from 1 to 8 but is not at the table is `unknown_seat`. A seat id that is not an integer, or is outside 1 to 8, is `invalid_payload` (it can never be a seat; compare `on_behalf_of_seat` in section 2, which is `invalid_event` for the same reason).
- `delta` is a non-zero integer (positive or negative); `amount` is a positive integer. The permitted magnitudes (for example 1 to 999 for life) and which sign a counter may take are part of each handler's story, and a violation is `invalid_amount`.
- `value_set.field` is one of `life`, `poison`, `energy`, `experience`, `commander_damage`, `commander_cast`. `commander_damage` needs `source_seat` and `commander`; `commander_cast` needs `commander`; the other fields use neither.
- `game_abandoned` records which phase it left, so `game_resumed` can restore it (`resume_phase`).
- `event_reverted` and `value_set`/`player_revived` follow the revert and correction rules of S-6.6.1 and S-6.6.5.

### 4.2 Error codes per handler

**Named by the stories** (the S-6.1.x and S-6.6.x acceptance criteria use these names):

- `invalid_setting` (S-6.1.3, S-6.1.4, S-6.1.5, S-6.1.6). `settings_changed` outside `lobby`: `invalid_phase`.
- `invalid_amount` (S-6.1.3 to S-6.1.7). `unknown_commander` for a commander index the seat does not have (S-6.1.4).
- `seat_eliminated` for an eliminated target or source (S-6.1.3, S-6.1.7).
- `table_full` (`seat_joined` with 8 seats), `not_enough_players` (`game_started` with fewer than 2 seats), `invalid_phase` (for example `seat_moved` without `correction: true` in an active game), all S-6.1.6.
- `game_closed`: any event other than `game_resumed` on a `finished` or `abandoned` game (S-6.2.9, S-6.3.3). Handler-level, introduced by later stories; no-op handlers do not check it.
- `not_revertible` (`game_created`, `game_started`, `game_ended`) and `already_reverted` (S-6.6.1).

**Added by this contract** (no story names them; chosen so every outcome has a code):

- `unknown_seat` (a seat id in 1 to 8 that is not at the table), `seat_taken` (`seat_joined` for a seat id already in use), `seat_not_eliminated` (a revive of a seat still in the game).
- The structural codes `invalid_event`, `game_mismatch`, `out_of_order` and `invalid_payload` (6.2).

### 4.3 Specified but not yet enforced

Handlers are no-ops in S-6.1.1. These rules are part of the contract now, so the later stories implement them without changing the contract; no reducer enforces them yet.

- **`game_created` and the settings.** `game_created` is valid **only as the event at `seq` 1**, that is when `state.last_seq` is 0. It **replaces `settings` entirely** with `payload.settings` (all four keys, validated: a bad value is `invalid_setting`). S-6.2.1 says a game's log begins with `game_created`; the settings given to `initialState` are only a placeholder for it (3.5). A `game_created` at any later seq is rejected with `invalid_phase`: no new code is added, `out_of_order` is reserved for a bad `seq`, and `invalid_phase` already means "this event is not allowed at this point of the game". A first event that is not `game_created` is accepted (vectors rely on it). Owner: S-6.1.6.
- **`started_at`.** The server is authoritative: when it accepts `game_started` it **overwrites** a client-sent `started_at` with its own time before the event is stored and broadcast. Clients may send a placeholder. Owner: S-6.2.6 / S-6.3.3.
- **`game_closed`.** See 4.2 and 6.2. Owner: S-6.2.8, S-6.2.9, S-6.7.3.
- **Reverts.** See 6.5. Owner: S-6.6.1.
- **Settings ranges.** See 3.2. Owner: S-6.1.6.
- **Message size.** The wire reader (5.6) has no size limit, and unknown payload keys are stored as sent. S-6.3.2 and S-6.3.3 MUST cap the size of a message (and may cap the payload) before the reader runs.

## 5. Canonical JSON

Canonical JSON is the one byte-exact text form of a JSON value. It is used to compare states between the two reducers, in vectors, and for any hash or snapshot comparison. Both implementations MUST produce the same bytes for the same value.

### 5.1 Value model

A canonical value is `null`, a boolean, an integer, a string, an array of canonical values, or an object whose keys are strings and whose values are canonical values. Nothing else is serializable (section 5.5).

### 5.2 Output grammar

- **No insignificant whitespace.** No spaces, tabs or newlines outside strings, and no trailing newline. Separators are `,` and `:` only. An object is `{"k":v,"k2":v2}` and an array is `[v,v2]`. Empty containers are `{}` and `[]`.
- **Literals** are `null`, `true` and `false`, lowercase.
- **Object keys** are sorted by **Unicode code point** in ascending order, which is the same as comparing the UTF-8 bytes. Do **not** sort by UTF-16 code unit: JavaScript's default `Array.prototype.sort` does, and it orders a character above U+FFFF (a surrogate pair in UTF-16) before characters from U+E000 to U+FFFF. Python's default `str` ordering is already by code point. A shorter string that is a prefix of a longer one sorts first. Keys are unique. Array order is preserved as given.
- **Integers**: base 10, no leading zeros, no `+`, a `-` for negative values, no decimal point, no exponent. Zero is `0` and negative zero is written `0`. The allowed range is -(2^53 - 1) to 2^53 - 1 inclusive (-9007199254740991 to 9007199254740991).
- **Strings** are written between `"` and `"`, as follows. The string is a sequence of Unicode code points, with no normalization (NFC and NFD forms stay different).
  - `"` becomes `\"` and `\` becomes `\\`.
  - U+0008, U+0009, U+000A, U+000C and U+000D become `\b`, `\t`, `\n`, `\f` and `\r`.
  - Every other code point from U+0000 to U+001F becomes `\u00xx` with **lowercase** hex digits and exactly 4 digits (for example `\u0000`, `\u001f`, `\u000b`).
  - **Nothing else is escaped.** `/` is written as `/`. U+007F, U+2028, U+2029 and every non-ASCII character, including characters above U+FFFF, are written as themselves. There are no `\uXXXX` escapes for non-ASCII and no surrogate-pair escapes.
  - Object keys are strings and follow the same rules.
- **Encoding**: the output text is encoded as UTF-8, without a byte order mark. A string that contains a lone surrogate cannot be encoded and is rejected (5.4).

### 5.3 Null versus absent

`null` is a value and is written. An absent key is not written. The serializer MUST NOT add, drop or convert keys. A value that is "undefined" in JavaScript (an `undefined` property value, or a hole in an array) is not a canonical value and is rejected, so an optional field must be omitted from the object, not set to `undefined`.

### 5.4 Rejections

The serializer MUST fail, with one of these codes, instead of writing an approximation. TypeScript throws `CanonicalJsonError` with `code`; Python raises an exception with the same `code` attribute.

| Code                   | When                                                                                                                                                                                                                                                                  |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `non_integer`          | A number that is not an integer: a fraction, `NaN`, `Infinity`, `-Infinity`. In Python, any `float` value, even `1.0`. (JavaScript cannot tell `1.0` from `1`; see 5.6.)                                                                                              |
| `integer_out_of_range` | An integer whose absolute value is greater than 9007199254740991.                                                                                                                                                                                                     |
| `lone_surrogate`       | A string or key containing a UTF-16 surrogate code point (U+D800 to U+DFFF) that is not part of a valid high-then-low pair. In Python, any `str` containing a code point in U+D800 to U+DFFF is rejected, because Python strings hold surrogates as lone code points. |
| `unsupported_value`    | Anything else: `undefined`, a function, a symbol, a bigint, a `Date`, a `Map`, a class instance, a non-string key, or a container with more than 64 ancestors (nesting limit, below).                                                                                 |

**Nesting limit (the single rule).** A container's _ancestors_ are the arrays and objects that enclose it; the top-level value has 0. A container with **64 or fewer** ancestors is allowed. A container with **65 or more** ancestors is rejected with `unsupported_value`. Worked example: a chain of 65 nested arrays, `[[[...[]...]]]`, passes (the innermost array has 64 ancestors), and a chain of 66 fails. The same holds for objects, `{"a":{"a":...{}...}}`. Scalars are not containers, so a scalar at the bottom of 65 arrays is fine.

In Python, `bool` is checked **before** `int`, because `True` is an `int`: a boolean serializes as `true` or `false`, never as `1` or `0`.

### 5.5 Examples

| Input                                              | Canonical output                        |
| -------------------------------------------------- | --------------------------------------- |
| `{"b": 1, "a": [3, 1, {"z": null, "y": true}]}`    | `{"a":[3,1,{"y":true,"z":null}],"b":1}` |
| the string `a"b\c`                                 | `"a\"b\\c"`                             |
| the string with U+0000, U+001F, newline            | `"\u0000\u001f\n"`                      |
| the string `Zoe` with a diaeresis, U+65E5, U+1F600 | written raw, unescaped                  |
| keys U+FF5E and U+10000                            | U+FF5E first, then U+10000              |

`vectors/canonical-json.json` holds the executable cases.

### 5.6 Reading JSON into the model (wire parsing)

The reducers only ever see integers, but a parser can produce floats from `3.0` or `1e2`. The server and any Python reader MUST parse incoming JSON so that every number token containing `.`, `e` or `E` is **rejected** (for example `json.loads(text, parse_float=reject)`), and so that integers outside the safe range are rejected. A rejected body is `invalid_event`. A JavaScript client cannot produce such tokens because it never serializes a float; a JavaScript `JSON.parse` of `3.0` yields the integer 3, and that asymmetry is accepted because TypeScript never receives wire data the server did not canonicalize. Duplicate keys in an object are rejected by the Python reader. The wire reader MUST also reject a lone surrogate escape, `\uD800` to `\uDFFF` that is not half of a valid high-then-low pair (a high escape not directly followed by a low one, or a low escape not directly preceded by a high one), in any string or key, as `invalid_event`. JavaScript's `JSON.parse` accepts such an escape and yields a lone surrogate, so `apply` (step 4 for envelope strings, step 5 for the payload) is the second line of defence. The reader as specified has no size limit: the transport caps message size (4.3).

## 6. Reducer

### 6.1 Entry point

```
apply(state, event) -> { ok: true, state } | { ok: false, error: { code } }
```

`reduce` is an alias. Both languages expose the same function under the same behaviour. `replay(initial, events)` folds `apply` and, on the first rejection, returns the index of the event and the error. The input state and event MUST NOT be mutated; a handler returns a new state. A rejected event changes nothing: the caller keeps the previous state.

The state before the first event comes from `initialState(game_id, settings?)` (3.5), which throws on a bad `game_id` or an unknown setting key and validates no ranges; the `game_created` and `settings_changed` handlers validate values (S-6.1.6). `replay` is the entry point for a whole log; S-6.1.6 and S-6.6.1 extend it where an event needs the log (6.5).

### 6.2 Reducer error codes

`apply` runs these checks **in this order** and returns the first failure:

1. The event is not an object, or `v` is missing or is not an integer in the safe range (a boolean, `null`, a string, a fraction): `invalid_event`. Because this is first, `"v": true` is `invalid_event`, never `unsupported_version`.
2. `v` is an integer that is not a supported version (only 1): `unsupported_version`. This comes **before** the type check, so an event with both a bad `v` and a bad `type` gets `unsupported_version`.
3. `type` is not a string: `invalid_event`. `type` is not in the registry: `unknown_event_type`; this includes a `type` with a lone surrogate, which is checked here, before the well-formed-string check of step 4.
4. The envelope is malformed (section 2: unknown or missing keys, key types, `event_id` format, `payload` not an object, `actor` shape, `on_behalf_of_seat`, and `seq` or `ts` present but invalid, including `null`, and `seq` below 1, and any envelope string with a lone surrogate, section 2): `invalid_event`.
5. `payload` is not a canonical value (5.1): a non-integer number, `NaN` or an infinity, an integer outside the safe range, a string with a lone surrogate, nesting beyond the limit (5.4), or an unsupported type: `invalid_payload`. This is the test the canonical serializer applies, and it runs before the handler and before `game_mismatch` and `out_of_order`, because it depends only on the event and not on the state. The payload is the top-level value for the nesting count (0 ancestors).
6. `game_id` differs from the state's `game_id`: `game_mismatch`.
7. `seq` (when present and valid) is not exactly `state.last_seq + 1`: `out_of_order`. With `seq` absent the event is applied as `last_seq + 1`.
8. The type's handler runs, may return its own codes, and on success sets `last_seq` to the event's seq.

In this version every handler is a no-op that only sets `last_seq`, whatever the phase. **`game_closed` is a handler-level code, not a generic check in `apply`**: later stories (S-6.2.8, S-6.2.9, S-6.7.3) introduce it in their handlers. Until then an event is accepted in a `finished` or `abandoned` game.

A fractional number can never be in a vector's accept path, because the state and the payloads hold integers only. Python's wire reader (5.6) rejects float tokens as `invalid_event` before `apply` is called; an in-memory Python `float` given to `apply` is `invalid_payload`.

| Code                  | Meaning                                                                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `unsupported_version` | `v` is an integer the reducer does not support.                                                                                          |
| `unknown_event_type`  | `type` is not registered.                                                                                                                |
| `invalid_event`       | The envelope is malformed.                                                                                                               |
| `game_mismatch`       | The event belongs to another game.                                                                                                       |
| `out_of_order`        | `seq` is not the next one (a gap, a repeat or a rewind).                                                                                 |
| `invalid_payload`     | The payload is not a canonical value (step 5), or a handler finds a missing key, a wrong type or an unknown enumerated value.            |
| `game_closed`         | Handler-level, from S-6.2.8 on: the game is `finished` or `abandoned` and the event is not `game_resumed`. Not produced by this version. |
| `invalid_phase`       | The event is not allowed in the current phase.                                                                                           |
| `invalid_setting`     | A setting is outside its range or is not an allowed value.                                                                               |
| `invalid_amount`      | A `delta`, `amount` or `value` is out of range, zero where not allowed, or would make a counter negative.                                |
| `unknown_seat`        | A seat id in the payload is not at the table.                                                                                            |
| `unknown_commander`   | A commander index that the seat does not have.                                                                                           |
| `seat_eliminated`     | The event targets or originates from an eliminated seat.                                                                                 |
| `seat_not_eliminated` | A revive for a seat that is still in the game.                                                                                           |
| `seat_taken`          | `seat_joined` for a seat id that is already in use.                                                                                      |
| `table_full`          | `seat_joined` when 8 seats are taken.                                                                                                    |
| `not_enough_players`  | `game_started` with fewer than 2 seats.                                                                                                  |
| `not_revertible`      | `event_reverted` targets a `game_created`, `game_started` or `game_ended` event.                                                         |
| `already_reverted`    | `event_reverted` targets an event that is already reverted.                                                                              |

### 6.3 Server-only reject codes

The server's authorization layer rejects these before the reducer runs (S-6.3.3, S-6.6.2). A reducer MUST NOT return them.

| Code                  | Meaning                                                                             |
| --------------------- | ----------------------------------------------------------------------------------- |
| `forbidden`           | The actor may not send this event (role, trust mode, host-only events, spectators). |
| `undo_window_expired` | A revert targets an event outside the actor's undo window (D2).                     |

### 6.4 Seq and optimistic events

`seq` is assigned by the server and increases by exactly 1. A client applying its own unacked event applies it with `seq` absent, so `last_seq` advances by 1 and any `eliminated_at_seq` records that predicted value. When the server's broadcast arrives, the client rebuilds from the last confirmed state, applies the confirmed events by `seq`, then re-applies the pending events (S-6.3.5). The state is therefore never trusted across a rebuild.

### 6.5 Reverts are resolved by the fold over the log

`event_reverted {target_seq}` cannot be handled by `apply(state, event)` alone with the state of section 3. `not_revertible` needs the **type of the event at `target_seq`**, which a state does not keep, and the state after a revert is not "the old state plus a change": S-6.6.1 says the state is rebuilt by folding the log while excluding reverted events. So reverts are resolved by the fold over the log (`replay`, and the server's rebuild of a game's state from its log, S-6.3.8), not by a single `apply`. The rules, from S-6.6.1:

- Events are folded in `seq` order. An `event_reverted` is **effective** unless a later effective `event_reverted` targets it (that is the redo: reverting a revert brings the original effect back).
- The state is the result of folding the log **without** the events excluded by effective reverts. Those seqs are the state's `reverted` array.
- An event that is no longer valid once the excluded events are removed (for example poison -2 after the +3 is reverted) is **skipped** and its seq is added to `voided`; the fold continues with the next event.
- Checks that need the log: `not_revertible` (the target is a `game_created`, `game_started` or `game_ended` event) and a target that is not an earlier event of this log (`invalid_payload`; provisional, S-6.6.1 confirms the code). `already_reverted` is decidable from the state alone, because the target is already in `reverted`.
- Who may revert and the undo window are server rules (`forbidden`, `undo_window_expired`, S-6.6.2), not reducer rules.

In this version the `event_reverted` handler is a no-op and `reverted` and `voided` stay `[]`. Both keys exist now so that S-6.6.1 does not change the state shape (and so does not bump `v`, 1.1).

## 7. Purity

A reducer, the canonical serializer and everything they call MUST NOT:

- read a clock (`Date`, `time`, `datetime.now`), including the envelope's `ts`;
- read randomness (`Math.random`, `random`, `crypto`, `uuid4`);
- read the environment, the locale, the network, the file system or any global state;
- depend on iteration order of an unordered collection, or on locale-aware formatting or sorting.

Anything that looks like time or chance arrives **inside an event payload**: `game_started` carries `started_at` and the chosen `first_seat` (a random first player is picked by the sender and sent as `first_seat`). The same events applied to the same state always give the same result. The TypeScript package enforces this with `src/purity.test.ts`; the Python package must have an equivalent test.

## 8. Test vector format

Shared vectors live in `packages/game-core/vectors/*.json`, one case or case group per file. Both reducers must run every file. A file is a JSON object with `format` (the integer 1), `kind`, `name` (the file name without `.json`) and `description`.

Vector files are test data, not wire data. A runner loads them with a plain JSON parser, without the wire reader's number checks (5.6), and passes the parsed events to `apply` as they are. That is how a vector can hold a value the wire reader would refuse, such as an integer outside the safe range, and expect the reducer's own code for it (`invalid_payload`).

### 8.1 `kind: "reducer"`

```json
{
  "format": 1,
  "kind": "reducer",
  "name": "reject-unknown-event-type",
  "description": "text",
  "game_id": "018f0000-0000-7000-8000-000000000001",
  "settings": {
    "starting_life": 40,
    "commander_damage_threshold": 21,
    "poison_threshold": 10,
    "trust_mode": "casual"
  },
  "events": [
    {
      "event_id": "...",
      "type": "...",
      "payload": {},
      "actor": { "kind": "player", "player_id": "018f0000-0000-7000-8000-0000000000a1" },
      "on_behalf_of_seat": null,
      "v": 1
    }
  ],
  "expect": { "state": {} }
}
```

- `settings` has all four settings. The runner builds the state with `initialState(game_id, settings)`.
- Each event is an envelope. `game_id` MAY be omitted, and the runner fills in the vector's `game_id`. `seq` and `ts` MAY be omitted, which makes the event optimistic (applied as `last_seq + 1`).
- `expect` has **exactly one** of:
  - `{"state": <full state>}`: every event is applied successfully, and the canonical JSON of the final state equals the canonical JSON of `expect.state`.
  - `{"error": {"index": <integer>, "code": "<reducer error code>"}}`: events before `index` succeed, the event at `index` is rejected with `code`, and no later event is applied.
- A runner compares canonical JSON text, never parsed objects, so key order inside the file does not matter.

### 8.2 `kind: "canonical_json"`

```json
{ "format": 1, "kind": "canonical_json", "name": "...", "description": "...", "cases": [] }
```

Each case has a `name` and one of:

- `{"input": <JSON value>, "output": "<canonical text>"}`: serializing `input` yields exactly `output`. Every number in `input` is an integer.
- `{"invalid": {"kind": ..., "value": ...}, "error": "<code>"}`: the runner builds the value in its own language and puts it inside a one-element array, `[value]`, then expects the serializer to fail with `error` (5.4). The kinds are `float` (`value` is decimal text such as `"1.5"`, never an integral float), `nan`, `infinity`, `integer` (`value` is decimal text of an integer outside the safe range), and `lone_surrogate` (`value` is the hex code of one surrogate code unit, such as `"d800"`). Two more kinds, `deep_array` and `deep_object`, build a chain of `value` (decimal text) nested containers, arrays or objects with the key `"a"`, with an empty container at the bottom. For these two the runner serializes the chain itself, **not** wrapped in `[value]`, so `value` is exactly the number of nested containers (65 pass, 66 fail, see 5.4). The passing boundary cases give the same chain as `input`.

### 8.3 The vectors

Reducer vectors are named `accept-*` (the events are accepted and the final state is compared) and `reject-*` (one event is rejected with a given code). They cover every envelope and payload rule of sections 2 and 6.2, including the check order (`reject-precedence-*`). `canonical-json*.json` hold the serializer cases, `initial-state*.json` the initial state, and `canonical-json-game-state.json` realistic full states as serialization cases (seats cannot be built through no-op handlers). Both runners, the TypeScript one in `packages/game-core/src/vectors.test.ts` and the Python one in `apps/api` (both ship with S-6.1.1), check the header (`format` is 1, `name` equals the file name without `.json`, `expect` has exactly one of `state` and `error`) and run every file. S-6.1.2 adds the full validator and the coverage check (an event type that no vector exercises fails the runner with `uncovered event type: <type>`).
