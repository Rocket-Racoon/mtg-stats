# Game state and event contract

Version: schema `v` = 1. Story: S-6.1.1. Architecture: `docs/architecture.md` section 6.

This file is the single written contract for the live game. Two reducers implement it: TypeScript in `packages/game-core/src` and Python in `apps/api/app/games/core/`. They must produce byte-identical canonical JSON for the same events. If this file and a reducer disagree, the reducer is wrong. The rules for changing it, and for when `v` bumps, are in 1.1. A change to the shape of the state, an event or an error code needs a matching vector in `vectors/`.

Words in capitals (MUST, MUST NOT, MAY) are normative.

## 1. Conventions

- All JSON is UTF-8. Every number in an envelope, a payload or the state is an **integer** in the range -(2^53 - 1) to 2^53 - 1. There are no floats and no money.
- Keys are `snake_case`. Enumerated values are lowercase `snake_case` strings.
- **Seat ids** are integers 1 to 8. A seat id is stable for the life of the game: reordering seats changes `order`, never a seat id. A seat id is not a turn position: the turn order is `order`, and `seat_joined` appends to it (4.4.4), so seats that join as 5, 2, 7 play in that order.
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

| Key                  | Type                          | Meaning                                                                                                                                                                                                                                                                                                                         |
| -------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `v`                  | integer                       | Schema version of the state shape: the current version, 1 (see 1.1).                                                                                                                                                                                                                                                            |
| `game_id`            | string                        | The game.                                                                                                                                                                                                                                                                                                                       |
| `phase`              | string                        | `lobby`, `active`, `ending`, `finished` or `abandoned`.                                                                                                                                                                                                                                                                         |
| `resume_phase`       | string or null                | The phase `game_resumed` returns to (`lobby`, `active` or `ending`). Non-null only while `phase` is `abandoned`.                                                                                                                                                                                                                |
| `settings`           | object                        | See 3.2.                                                                                                                                                                                                                                                                                                                        |
| `control_mode`       | string                        | `own_seat` (the default) or `host_all` (S-6.8.2).                                                                                                                                                                                                                                                                               |
| `seats`              | array of seat objects         | Sorted by ascending `seat`. At most 8. See 3.3.                                                                                                                                                                                                                                                                                 |
| `order`              | array of integers             | Seat ids in turn order. A permutation of the ids in `seats`. Index 0 is not the first player: a seat's **turn position** is 1 + its cyclic offset from `first_seat` in `order` (with order `[1,2,3,4]` and `first_seat` 3, seat 3 is position 1 and seat 2 is position 4). Stats read the final `order` this way (D9, S-6.7.4). |
| `active_seat`        | integer or null               | Seat whose turn it is. `null` outside `active` and `ending`.                                                                                                                                                                                                                                                                    |
| `first_seat`         | integer or null               | The seat that took the first turn: the `first_seat` of `game_started`, `null` before it. It never changes afterwards, whatever `seat_moved` does to `order`. It anchors the rounds (4.4.10).                                                                                                                                    |
| `turn`               | integer                       | Individual turn counter. 0 before `game_started`.                                                                                                                                                                                                                                                                               |
| `round`              | integer                       | The game's "turn count" for results and stats (D4). 0 before `game_started`.                                                                                                                                                                                                                                                    |
| `monarch`            | integer or null               | Seat id of the monarch.                                                                                                                                                                                                                                                                                                         |
| `initiative`         | integer or null               | Seat id holding the initiative.                                                                                                                                                                                                                                                                                                 |
| `commander_damage`   | object of objects of integers | `commander_damage[target seat id as a decimal string][source_key]`. `source_key` is `"<source seat id>:<commander index>"`, for example `"1:0"`. A target key and a source key are created by the first damage that reaches them and are never removed, even when a tally returns to 0 (S-6.1.4).                               |
| `no_winner`          | boolean                       | `true` only for a full draw (S-6.1.3, 4.5.2; S-6.7.1).                                                                                                                                                                                                                                                                          |
| `winner_seat`        | integer or null               | The provisional or final winner. `null` while none, and when `no_winner` is `true`.                                                                                                                                                                                                                                             |
| `win_condition`      | string or null                | A D5 code (section 3.4) or `null`.                                                                                                                                                                                                                                                                                              |
| `win_condition_note` | string                        | Free text, required by S-6.7 when `win_condition` is `other`. `""` when none.                                                                                                                                                                                                                                                   |
| `started_at`         | string or null                | The `started_at` carried in the `game_started` payload, otherwise `null`.                                                                                                                                                                                                                                                       |
| `voided`             | array of integers             | Seqs skipped when state was rebuilt after a revert because they became invalid (S-6.6.1). Ascending, no duplicates. `[]` until S-6.6.1.                                                                                                                                                                                         |
| `reverted`           | array of integers             | Seqs of the events currently excluded by an effective `event_reverted` (S-6.6.1, see 6.5): an event whose revert was itself reverted is not in it. Includes the seq of an `event_reverted` event that was itself reverted. Ascending, no duplicates. `[]` initially.                                                            |
| `last_seq`           | integer                       | `seq` of the last applied event. 0 before any event.                                                                                                                                                                                                                                                                            |

### 3.2 `settings`

Settings are game data, never constants (D3). They can change only in the `lobby` phase.

| Key                          | Type    | Range or values    | Default  |
| ---------------------------- | ------- | ------------------ | -------- |
| `starting_life`              | integer | 1 to 999           | 40       |
| `commander_damage_threshold` | integer | 1 to 99            | 21       |
| `poison_threshold`           | integer | 1 to 99            | 10       |
| `trust_mode`                 | string  | `casual`, `strict` | `casual` |

A value outside its range, or a `trust_mode` that is not `casual` or `strict`, is rejected with `invalid_setting`. That check belongs to the `game_created` and `settings_changed` handlers (S-6.1.6, rules in 4.4; S-6.1.3 to S-6.1.5 name the ranges). **`initialState` does not validate ranges**: it stores the values it is given, so both implementations keep an out-of-range value unchanged; a later `game_created` replaces the settings and validates them.

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
- Values are copied as given: **neither types nor ranges are validated**. An out-of-range value (3.2) is stored unchanged. Range validation is done by the `game_created` and `settings_changed` handlers (4.4).
- The `settings` given here are a **placeholder**. The log of a real game starts with `game_created`, which replaces them (4.3). They exist for vectors and tests that do not start with `game_created`.
- All keys of 3.1: `phase` `lobby`, `control_mode` `own_seat`, `seats` `[]`, `order` `[]`, every nullable key `null` (`first_seat` included), `turn` 0, `round` 0, `commander_damage` `{}`, `no_winner` `false`, `win_condition_note` `""`, `voided` `[]`, `reverted` `[]`, `last_seq` 0, `v` 1. `vectors/initial-state.json` is the reference.

## 4. Event registry

The registry is closed: a type that is not in this table is rejected with `unknown_event_type`. "Introduced by" names the story that defines the handler's behaviour; until then the handler is a no-op that only advances `last_seq`. The nine S-6.1.6 handlers are specified in 4.4 and the three S-6.1.3 handlers in 4.5. Payload keys marked `?` are optional (absent or `null`). Unknown payload keys MUST be ignored by reducers, and they stay in the log as sent. In the payloads below, "commanders" means an array of 1 or 2 `{"card_id": string or null, "name": string}` objects.

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
- `delta` is a non-zero integer (positive or negative); `amount` is a positive integer. **Type versus value:** a `delta` or `amount` that is not an integer at all (a string, a boolean, `null`, an array, an object, or absent) is `invalid_payload`, and a fraction or an integer outside the safe range is `invalid_payload` too (6.2, step 5). An integer that breaks the rule of the handler (zero, a magnitude above the limit, for example 1 to 999 for life, or a sign a counter may not take) is `invalid_amount`. The permitted magnitudes are part of each handler's story.
- `value_set.field` is one of `life`, `poison`, `energy`, `experience`, `commander_damage`, `commander_cast`. `commander_damage` needs `source_seat` and `commander`; `commander_cast` needs `commander`; the other fields use neither.
- `game_abandoned` records which phase it left, so `game_resumed` can restore it (`resume_phase`).
- `event_reverted` and `value_set`/`player_revived` follow the revert and correction rules of S-6.6.1 and S-6.6.5.

### 4.2 Error codes per handler

**Named by the stories** (the S-6.1.x and S-6.6.x acceptance criteria use these names):

- `invalid_setting` (S-6.1.3, S-6.1.4, S-6.1.5, S-6.1.6). `settings_changed` outside `lobby`: `invalid_phase`.
- `invalid_amount` (S-6.1.3 to S-6.1.7). `unknown_commander` for a commander index the seat does not have (S-6.1.4).
- `seat_eliminated` for an eliminated target or source (S-6.1.3, S-6.1.7). Enforced from S-6.1.3 by `life_changed`, `player_conceded` and `player_eliminated`; its place in the check order is fixed in 4.5.1.
- `table_full` (`seat_joined` with 8 seats), `not_enough_players` (`game_started` with fewer than 2 seats), `invalid_phase` (for example `seat_moved` without `correction: true` in an active game), all S-6.1.6.
- `game_closed`: any event other than `game_resumed` on a `finished` or `abandoned` game (S-6.2.9, S-6.3.3). Handler-level, introduced by later stories; no-op handlers do not check it.
- `not_revertible` (`game_created`, `game_started`, `game_ended`) and `already_reverted` (S-6.6.1).

**Added by this contract** (no story names them; chosen so every outcome has a code):

- `unknown_seat` (a seat id in 1 to 8 that is not at the table), `seat_taken` (`seat_joined` for a seat id already in use), `seat_not_eliminated` (a revive of a seat still in the game).
- The structural codes `invalid_event`, `game_mismatch`, `out_of_order` and `invalid_payload` (6.2).

### 4.3 Specified but not yet enforced

These rules are part of the contract so that the later stories implement them without changing it. An item marked **enforced** is implemented by a reducer and has vectors; the others are not enforced yet.

- **`game_created` and the settings.** `game_created` is valid **only as the event at `seq` 1**, that is when `state.last_seq` is 0. It **replaces `settings` entirely** with `payload.settings` (all four keys, validated: a bad value is `invalid_setting`). S-6.2.1 says a game's log begins with `game_created`; the settings given to `initialState` are only a placeholder for it (3.5). A `game_created` at any later seq is rejected with `invalid_phase`: no new code is added, `out_of_order` is reserved for a bad `seq`, and `invalid_phase` already means "this event is not allowed at this point of the game". A first event that is not `game_created` is accepted (vectors rely on it). Owner: S-6.1.6. **Enforced** by S-6.1.6 (4.4.2).
- **`started_at`.** The server is authoritative: when it accepts `game_started` it **overwrites** a client-sent `started_at` with its own time before the event is stored and broadcast. Clients may send a placeholder. Owner: S-6.2.6 / S-6.3.3.
- **`first_seat` for "Random".** When the host picks a random first player, the **server** chooses the seat with a secure RNG and writes it into the payload's `first_seat` before the event is stored and broadcast (S-6.2.6). The reducer only ever sees a seat id. Owner: S-6.2.6.
- **`game_closed`.** See 4.2 and 6.2. Owner: S-6.2.8, S-6.2.9, S-6.7.3.
- **Reverts.** See 6.5. Owner: S-6.6.1.
- **Settings ranges.** See 3.2. Owner: S-6.1.6. **Enforced** by S-6.1.6 (4.4.2 and 4.4.3).
- **Eliminations and `seat_eliminated`.** An eliminated seat takes no more events, except a revert or a host correction. The elimination routine (4.5.2) sets the five elimination fields and the full draw. Owner: S-6.1.3. **Enforced** by S-6.1.3 for life, concession and declared eliminations (4.5). Eliminations by commander damage, poison and group events call the same routine in S-6.1.4, S-6.1.5 and S-6.1.7, which add the vectors for several seats eliminated by one event.
- **Message size.** The wire reader (5.6) has no size limit, and unknown payload keys are stored as sent. S-6.3.2 and S-6.3.3 MUST cap the size of a message (and may cap the payload) before the reader runs.

### 4.4 Lobby, seat and turn handlers (S-6.1.6)

These nine handlers replace the no-ops. Everything else in section 4 is still a no-op, except the three handlers of S-6.1.3 (4.5). They are pure (section 7): timestamps and random choices come in the payload.

#### 4.4.1 Rules shared by the nine handlers

**Order of the handler's checks.** After steps 1 to 7 of 6.2, a handler returns the **first** failure in this order:

1. **Payload shape** (`invalid_payload`): a required key is absent, a value has the wrong type, a seat id is not an integer from 1 to 8 (4.1), a string that must be non-empty is `""`, a `commanders` array has the wrong length or a bad item. An optional key (`?`) may be absent or `null`. Unknown payload keys are ignored.
2. **Phase** (`invalid_phase`): the table below.
3. **Seat references** (`unknown_seat`): every seat id the payload names (`seat`, `from`, `to`, `first_seat`) that is not at the table, checked in the order the payload table lists them.
4. **The handler's own rules**, in the order its subsection lists them.

`game_created` and `settings_changed` have no seat references. A rejected event changes nothing (6.1).

| Handler            | Allowed when                                                                   |
| ------------------ | ------------------------------------------------------------------------------ |
| `game_created`     | `state.last_seq` is 0 (the event is the first of the log)                      |
| `settings_changed` | `phase` is `lobby`                                                             |
| `seat_joined`      | `phase` is `lobby`                                                             |
| `seat_left`        | `phase` is `lobby`                                                             |
| `seat_moved`       | `phase` is `lobby`, or `phase` is `active` or `ending` with `correction: true` |
| `deck_assigned`    | `phase` is `lobby`                                                             |
| `commanders_set`   | `phase` is `lobby`                                                             |
| `game_started`     | `phase` is `lobby`                                                             |
| `turn_passed`      | `phase` is `active` or `ending`                                                |

In every other phase (`finished`, `abandoned`, and `lobby`, `active` or `ending` where the table says otherwise) the event is `invalid_phase`. `game_closed` is not produced by these handlers (6.2).

**`ending` follows `active`.** S-6.7.1 says events are still accepted while in `ending`, so the two handlers that are allowed in `active` are allowed in `ending` under the same conditions. No event reaches `ending` before S-6.7.1, which owns that phase and may narrow this; until then the rule is pinned with hand-built states.

**Values.**

- A **seat id** is an integer 1 to 8. A **player id**, a **deck version id** and a **card id** are non-empty strings when they are not `null`. A **name** is any string, `""` included. Strings are never trimmed, normalised or compared case-insensitively.
- **Commanders** (`deck_assigned`, `commanders_set`): an array of **1 or 2** items, each an object with the keys `card_id` (a non-empty string or `null`; the key MUST be present) and `name` (a string; the key MUST be present). Other keys of an item are ignored and are not stored. An array of 0 or of 3 or more items, a non-array, or a bad item is `invalid_payload`. The array order is kept: the index is the commander index (3.3).
- **A new seat** has: the `seat`, `player_id` and `name` of the payload; `deck_version_id` `null`; `commanders` `[{"card_id": null, "name": ""}]`; `life` equal to `settings.starting_life` at that moment; `poison`, `energy` and `experience` 0; `commander_cast` `[0]`; and the five elimination fields `null`.
- **Lobby invariant.** While `phase` is `lobby`, every seat's `life` equals `settings.starting_life`. `seat_joined` creates a seat that way and `settings_changed` of `starting_life` rewrites every seat's `life`. Nothing else in the lobby changes `life`, `poison`, `energy` or `experience` (the S-6.1.3 handlers are `invalid_phase` in the lobby, 4.5.1).
- `seats` stays sorted by ascending `seat`. `order` keeps the relative order of the seats that stay.

**Settings values (`invalid_setting`).** `starting_life` MUST be an integer from 1 to 999, `commander_damage_threshold` and `poison_threshold` integers from 1 to 99, and `trust_mode` the string `casual` or `strict`. Any other value is `invalid_setting`, whatever its type: `0`, `1000`, `"40"`, `true`, `null`, `"STRICT"`.

#### 4.4.2 `game_created`

Payload: `settings`, an object with **all four** setting keys (3.2). Unknown keys inside it are ignored.

1. `settings` is absent or is not an object, or one of the four keys is absent: `invalid_payload`.
2. `state.last_seq` is not 0: `invalid_phase`. A `game_created` is valid only as the first event of a log; a later one is never allowed and has no effect.
3. Any of the four values breaks "Settings values" in 4.4.1: `invalid_setting`.

State change: `settings` becomes exactly the four values of the payload. Nothing else changes (the table is empty at seq 1). A log that does not start with `game_created` is still accepted (4.3).

#### 4.4.3 `settings_changed`

Payload: any of `starting_life`, `commander_damage_threshold`, `poison_threshold`, `trust_mode`. Every key may be absent and the event carries a **partial** change. `{}` is valid and changes nothing but `last_seq`. These keys are not optional keys in the sense of section 1: `null` is a value here, not "not given", so a key that is present with the value `null` is `invalid_setting`.

1. `phase` is not `lobby`: `invalid_phase`. This comes before the value check: a bad value after `game_started` is `invalid_phase`.
2. A present setting breaks "Settings values" in 4.4.1: `invalid_setting`. One bad key rejects the whole event, and no key is applied.

State change: each present key replaces the setting. When `starting_life` is present, every seat's `life` becomes the new `starting_life`.

#### 4.4.4 `seat_joined`

Payload: `seat` (the seat id the sender picked; the reducer never picks one), `player_id` (a non-empty string or `null`; the key MUST be present), `name` (a string; the key MUST be present). Choosing the next free seat is the sender's and the server's job (S-6.2.2).

1. Payload shape (4.4.1): `invalid_payload`.
2. `phase` is not `lobby`: `invalid_phase`.
3. The table already has 8 seats: `table_full`. This comes before `seat_taken`, so a full table always says `table_full`.
4. `seat` is already in `seats`: `seat_taken`.

State change: a new seat (4.4.1) is added to `seats` in seat-id order, and its id is **appended to the end of `order`**. The same non-null `player_id` in two seats is not checked here (the server decides who may take a second seat).

#### 4.4.5 `seat_left`

Payload: `seat`.

1. Payload shape: `invalid_payload`.
2. `phase` is not `lobby`: `invalid_phase`. A seat cannot leave after the game has started; a player who quits a running game concedes (S-6.1.3).
3. `seat` is not at the table: `unknown_seat`.

State change: the seat is removed from `seats` and its id from `order`. The other seats keep their ids and their relative order. A later `seat_joined` can reuse the id; it is a new seat (4.4.1).

#### 4.4.6 `seat_moved`

Payload: `from`, `to` (seat ids) and `correction?` (a boolean, or absent or `null`, which both mean `false`; any other type is `invalid_payload`).

1. Payload shape: `invalid_payload`.
2. Phase: in `lobby` the event is allowed whether `correction` is `true` or not. In `active` and in `ending` it is allowed only when `correction` is `true`; without it, `invalid_phase`. In every other phase, `invalid_phase`. (`ending` is the review screen, where the host can still fix a wrong seat order before the results are saved, D9.)
3. `from`, then `to`, not at the table: `unknown_seat`.

State change: the positions of the seat ids `from` and `to` **in `order` are exchanged**. `seats` is unchanged: seat ids, players, life and every other field stay with their seat id. `from` equal to `to` is a valid no-op (`last_seq` only). `active_seat`, `turn`, `round`, `monarch`, `initiative` and `commander_damage` never change, so after a correction in an `active` or `ending` game **the same seat id (and so the same player) stays active**, even if it is `from` or `to`, and the counters stay. Later `turn_passed` events (4.4.10) read the new `order`. Example: order `[1,2,3,4]` with `seat_moved {from: 4, to: 2}` gives `[1,4,3,2]`, and with `{from: 1, to: 2}` gives `[2,1,3,4]`.

#### 4.4.7 `deck_assigned`

Payload: `seat`, `deck_version_id` (a non-empty string), `commanders` (4.4.1).

1. Payload shape: `invalid_payload`.
2. `phase` is not `lobby`: `invalid_phase`.
3. `seat` is not at the table: `unknown_seat`.

State change, on that seat only: `deck_version_id` is set, `commanders` is **replaced** by the payload's array, and `commander_cast` becomes an array of 0s of the same length. Whether the deck belongs to the player, or its commanders match it, is not checked (S-6.2.4, server).

#### 4.4.8 `commanders_set`

Payload: `seat`, `commanders` (4.4.1). Checks 1 to 3 are those of `deck_assigned`.

State change, on that seat only: `commanders` is **replaced** by the payload's array and `commander_cast` becomes 0s of the same length. `deck_version_id` becomes **`null`**: `commanders_set` is the event for a seat that plays without a deck (S-6.2.4), so a seat never shows a deck together with commanders that were not that deck's. To change a seat back to a deck, send `deck_assigned`.

#### 4.4.9 `game_started`

Payload: `first_seat` (a seat id), `started_at` (a string; any string is accepted and stored as sent, the reducer never parses it, and the server overwrites it, 4.3).

1. Payload shape: `invalid_payload`.
2. `phase` is not `lobby`: `invalid_phase`.
3. `first_seat` is not at the table: `unknown_seat`.
4. Fewer than 2 seats: `not_enough_players`.

Check 3 comes before check 4: a table with 0 seats gives `unknown_seat`, and a table with a single seat gives `not_enough_players` when `first_seat` is that seat.

State change: `phase` `active`; `active_seat` `first_seat`; `turn` 1; `round` 1; `first_seat` the payload's `first_seat`; `started_at` the payload's; every seat's `life` `settings.starting_life`. `order`, the seats' other fields, the settings and `control_mode` are unchanged. From now on `settings_changed` is `invalid_phase` (D3).

#### 4.4.10 `turn_passed`

Payload: `{}` (keys are ignored). An **eliminated** seat is one whose `eliminated_at_seq` is not `null` (3.3).

1. `phase` is not `active` or `ending`: `invalid_phase`.
2. Every seat is eliminated: `invalid_phase`.

Let `order` be the state's `order`, `a` the active seat and `F` the state's `first_seat` (3.1). Then:

- **Next seat** `n`: scan `order` cyclically **after** the position of `a` (the positions after it to the end, then from the start up to and including `a` itself) and take the first seat that is not eliminated. `a` is scanned last, so when `a` is the only seat left, `n = a`. `a` may itself be eliminated (it was eliminated during its own turn); it is then simply never chosen.
- **Positions walked**: the positions of `order` from the one after `a`, cyclically, up to **and including** the position of `n`. They are the eliminated seats the pass skips, then `n`. When `n = a`, the walk covers every position of `order`.
- **State change**: `active_seat` becomes `n`, `turn` goes up by 1, and `round` goes up by 1 **if and only if the position of `F` is one of the positions walked**, whether `F` is eliminated or not. `first_seat` never changes.

This is the story's rule: the round goes up when play returns to the first player, or, when the first player is out, when play passes their place and reaches the next surviving seat after them.

The rule is evaluated on the state and the `order` **as they are when `turn_passed` is applied**. Nothing is remembered about who has already played in the current round, so the counter can only follow the order as it stands. In detail:

- **`F` not at the head of `order`:** round 1 is a full round. With order `[1,2,3,4]` and `F` 3 the turns of round 1 are 3, 4, 1, 2, and round 2 starts at 3.
- **`F` eliminated between turns:** the round goes up when play passes the place of `F`, so the next surviving seat after `F` starts each round (rows 9, 10, 12 and 13).
- **`F` eliminated on its own turn:** `F` is then `a`, and the walk starts **after** it, so the next pass does **not** go up a round: the other seats still have their turn of the current round (row 11). The round goes up when play next passes the place of `F` (row 10).
- **One seat left:** `n = a` and the walk covers every position, so **every** `turn_passed` goes up a round. This follows the rule literally; the stories do not say what a round is with one seat left, and the game is normally in `ending` by then (S-6.7.1). Open point for S-6.7.1.
- **`seat_moved` correction** (4.4.6) changes `order` and nothing else, not `F`, `a`, `turn` or `round`. A seat can then act twice in a round (a seat that has already played is moved after `a`) or not at all (a seat that has not played is moved before `a`), and the counter does not compensate: `round` goes up exactly when play next reaches or passes the place of `F`. Examples 17 to 19 below.
- The seat ids of seats do not matter for this rule, only positions in `order` and the ids `F` and `a`.

Worked examples (every seat is alive unless it is listed as eliminated; "seats walked" lists the seat ids at the positions walked; `turn` and `round` are those of the state before and after the pass). Rows 1 to 16 pass with no correction; rows 17 to 19 are the state right after a `seat_moved` correction.

| #   | `order`     | `F` | eliminated | active `a`                 | `n` | seats walked | `F` walked | before | after |
| --- | ----------- | --- | ---------- | -------------------------- | --- | ------------ | ---------- | ------ | ----- |
| 1   | `[1,2,3,4]` | 1   | none       | 1                          | 2   | 2            | no         | 1, 1   | 2, 1  |
| 2   | `[1,2,3,4]` | 1   | none       | 4                          | 1   | 1            | yes        | 4, 1   | 5, 2  |
| 3   | `[1,2,3,4]` | 3   | none       | 3 (the first turn)         | 4   | 4            | no         | 1, 1   | 2, 1  |
| 4   | `[1,2,3,4]` | 3   | none       | 4                          | 1   | 1            | no         | 2, 1   | 3, 1  |
| 5   | `[1,2,3,4]` | 3   | none       | 2 (last turn of round 1)   | 3   | 3            | yes        | 4, 1   | 5, 2  |
| 6   | `[1,2,3,4]` | 3   | none       | 2 (round 3, turn 12)       | 3   | 3            | yes        | 12, 3  | 13, 4 |
| 7   | `[1,2,3,4]` | 1   | 4          | 3                          | 1   | 4, 1         | yes        | 3, 1   | 4, 2  |
| 8   | `[1,2,3,4]` | 3   | 4          | 3                          | 1   | 4, 1         | no         | 1, 1   | 2, 1  |
| 9   | `[1,2,3,4]` | 1   | 1          | 2 (`F` out between turns)  | 3   | 3            | no         | 2, 1   | 3, 1  |
| 10  | `[1,2,3,4]` | 1   | 1          | 4                          | 2   | 1, 2         | yes        | 4, 1   | 5, 2  |
| 11  | `[1,2,3,4]` | 1   | 1          | 1 (`F` out on its turn)    | 2   | 2            | no         | 1, 1   | 2, 1  |
| 12  | `[1,2,3,4]` | 3   | 3          | 2                          | 4   | 3, 4         | yes        | 4, 1   | 5, 2  |
| 13  | `[1,2,3,4]` | 4   | 4          | 3 (the walk wraps)         | 1   | 4, 1         | yes        | 4, 1   | 5, 2  |
| 14  | `[3,2,1,4]` | 1   | none       | 4                          | 3   | 3            | no         | 2, 1   | 3, 1  |
| 15  | `[1,2,3,4]` | 1   | 2, 3, 4    | 1 (one seat left)          | 1   | 2, 3, 4, 1   | yes        | 1, 1   | 2, 2  |
| 16  | `[1,2,3,4]` | 1   | 1, 2, 3    | 4 (one seat left, `F` out) | 4   | 1, 2, 3, 4   | yes        | 4, 3   | 5, 4  |
| 17  | `[3,2,1,4]` | 1   | none       | 2                          | 1   | 1            | yes        | 2, 1   | 3, 2  |
| 18  | `[1,3,2,4]` | 1   | none       | 3                          | 2   | 2            | no         | 3, 1   | 4, 1  |
| 19  | `[4,2,3,1]` | 1   | none       | 3                          | 1   | 1            | yes        | 3, 1   | 4, 2  |

Rows 17 to 19 come from `[1,2,3,4]` with `F` 1:

- **Row 17:** seat 2 is active (turn 2) and `seat_moved {from: 1, to: 3, correction: true}` gives `[3,2,1,4]`. The next pass goes 2 to 1 and starts round 2 at turn 3, so seats 3 and 4 do not act in round 1 at all.
- **Row 18:** seat 3 is active (turn 3) and the correction `{from: 3, to: 2}` gives `[1,3,2,4]`. The next seat is 2, which already played in round 1, so it acts twice in the round, and the counter stays at 1; round 2 starts when play reaches seat 1, two passes later.
- **Row 19:** seat 3 is active (turn 3) and the correction `{from: 1, to: 4}` gives `[4,2,3,1]`. `F` is still seat 1 (the id), now last in `order`. The next seat is 1 and starts round 2 at turn 4, so seat 4 did not act in round 1.

Rows 7 to 13, 15 and 16 have eliminated seats. S-6.1.3 added vectors for them, built with real events (`player_conceded`, 4.5.4): the `accept-turn-passed-*` vectors named after the rule of the row. `src/lobby.test.ts` and `src/acceptance-s616.test.ts` also pin every row, and every order, first seat and eliminated set, with hand-built states.

### 4.5 Life, concession and elimination handlers (S-6.1.3)

These three handlers replace the no-ops of `life_changed`, `player_conceded` and `player_eliminated`. They are pure (section 7). Every type of section 4 that 4.4 and this section do not name is still a no-op. The elimination bookkeeping is one routine (4.5.2) that the later stories call too.

#### 4.5.1 Rules shared by the three handlers

**Order of the handler's checks.** After steps 1 to 7 of 6.2, a handler returns the **first** failure in this order:

1. **Payload shape** (`invalid_payload`), as in 4.4.1, and in addition:
   - `delta` MUST be an integer. A string, a boolean, `null`, an array, an object or an absent key is `invalid_payload`. A fraction, or an integer outside the safe range, never reaches the handler: it is `invalid_payload` at step 5 of 6.2.
   - `source_seat` is optional (absent or `null` mean "no source") and otherwise a seat id.
   - `cause` is optional and otherwise one of the ten D5 codes of 3.4, compared exactly. `"Mill"`, `""`, `"draw"`, a number or `false` are `invalid_payload`, because 6.2 gives that code to an unknown enumerated value.
2. **Phase** (`invalid_phase`): the event is allowed when `phase` is `active` or `ending`, and in no other phase. In the `lobby` nothing changes `life` (lobby invariant, 4.4.1) and nobody has been eliminated; a player who leaves a lobby uses `seat_left`. `finished` and `abandoned` are closed (`game_closed` is not produced yet, 6.2). `ending` follows `active` (4.4.1): S-6.7.1 says events are still accepted there, and it may narrow this.
3. **Seat references** (`unknown_seat`): `seat`, then `source_seat` when given, that is not at the table.
4. **`seat_eliminated`**: `seat` is eliminated, then `source_seat` (when given) is eliminated. An eliminated seat is one whose `eliminated_at_seq` is not `null` (3.3). `source_seat` is checked whatever the sign of `delta`.
5. **The handler's own rules**, in the order its subsection lists them (for `life_changed`, the amount rules).

`seat_eliminated` comes after `unknown_seat` because only a seat at the table can be eliminated. It comes **before** the amount rules because criterion 4 of S-6.1.3 says that an event aimed at an eliminated seat is rejected with `seat_eliminated`: a client that sends a bad amount to an eliminated seat is told that the seat is out. It also gives S-6.1.4, S-6.1.5 and S-6.1.7 one order to follow: shape, phase, `unknown_seat`, `seat_eliminated`, then their own rules (`invalid_amount`, `unknown_commander`).

**What may still target an eliminated seat.** Criterion 4 exempts "a revert or a host correction". In the registry these are:

- `event_reverted` (S-6.6.1), and `value_set` and `player_revived` (S-6.6.5): not specified yet.
- `seat_moved` with `correction: true` (4.4.6): it reorders `order` and does not touch a seat's values, so it may name an eliminated seat.

No other S-6.1.6 handler can reach an eliminated seat: `turn_passed` names no seat, and every other one needs the `lobby`, where no seat is eliminated.

Who may send these events (the seat's owner, the host, trust mode) is the server's rule (S-6.3.3), not the reducer's.

**A life that is not an integer.** `initialState` does not validate the settings (3.5), and `seat_joined` and `game_started` copy `settings.starting_life` into `life` as it is (4.4.1). A seat's `life` can therefore be a string, `null`, a boolean or an out-of-range number (vector `accept-seat-joined-with-unvalidated-starting-life`). "An integer" here is the one of section 1: from -(2^53 - 1) to 2^53 - 1. A boolean, a float of any value, a string, `null`, an array and an object are not integers (in Python `bool` is checked before `int`, as in 5.4). A handler MUST NOT throw and MUST NOT guess. The rule is: **`life_changed` on a seat whose `life` is not an integer is `invalid_amount`**. A concession and a declared elimination do not read `life`, so they work on such a seat. No new code is added: `invalid_amount` already means that the amount cannot be applied (6.2).

#### 4.5.2 The elimination routine

Every event that eliminates seats calls this routine, and no handler writes the elimination fields (3.3) itself. S-6.1.3 calls it for one seat at a time. S-6.1.4 (commander damage), S-6.1.5 (poison) and S-6.1.7 (group events) call it with one entry or several. In TypeScript it is `eliminate(state, seq, eliminations)` in `src/elimination.ts`; the Python reducer has the same routine. It is not an event and has no payload.

**Input.** The state **after** the event's own changes (the new life, the new counters), the `seq` the event is applied with (`last_seq + 1`, 6.4), and a list of entries `(seat, reason, by, cause)`: `reason` is one of the five of 3.3, `by` a seat id or `null`, `cause` a D5 code or `null`. The caller decides which seats to eliminate and with which reason (the precedence `commander_damage` > `poison` > `life` is S-6.1.4's rule), and has already returned `seat_eliminated` for an eliminated target.

**Steps.**

1. Let `standing` be the seats of the state that are not eliminated.
2. Go through the entries in order. An entry is **applied** unless its seat is not in `standing` (not at the table, or eliminated before this call) or an earlier entry of the list names the same seat. A skipped entry has no effect and is not an error. For every applied entry, and for no other seat:
   - `elimination_reason` is the entry's `reason`, `eliminated_by` its `by` and `elimination_cause` its `cause`;
   - `eliminated_at_seq` is `seq`, **the same value for every applied entry** (criterion 6: equal values mean "eliminated at the same time", and the seats share a placement, D6);
   - `eliminated_round` is the state's `round` (the routine never changes `round`).
3. **Full draw.** If at least one entry was applied and the applied seats are **every** seat of `standing` (nobody is left in the game), `no_winner` becomes `true`. In every other case `no_winner` is left as it is (criterion 7). A seat eliminated by an earlier event keeps its own `eliminated_at_seq` and stays a loss (D6, X1).

The routine changes nothing else: not `last_seq` (the handler sets it), `phase`, `active_seat`, `turn`, `round`, `winner_seat`, `win_condition`, `monarch`, `initiative` or `commander_damage`. In particular:

- **The phase does not move.** A full draw leaves `phase` at `active` (or `ending`). With every seat eliminated, `turn_passed` is `invalid_phase` (4.4.10) and every event naming a seat is `seat_eliminated`. S-6.7.1 owns the `ending` phase and the provisional result.
- **`winner_seat` stays `null`** when one seat is left standing. Choosing the provisional winner is S-6.7.1's.
- **The drawing seats are derived, not stored.** When `no_winner` is `true` because of this routine, the drawing seats are the seats whose `eliminated_at_seq` equals the greatest `eliminated_at_seq` of the table, that is every seat eliminated by the event that ended the game. Seats eliminated earlier are losses. No state key is added for it. S-6.7.1 may refine this when it reads the final state.
- **Nothing in S-6.1.3 sets `no_winner` back to `false`.** A revert rebuilds the state by folding the log (6.5), so it needs no such rule. Whether a revive (`player_revived`, S-6.6.5) clears it is that story's decision.
- **The single-seat case.** With one seat left, an event that eliminates it "eliminates every seat still in the game", so the literal rule makes it a full draw: the last seat conceding gives `no_winner` `true`. The criteria do not exclude it. S-6.7.1 decides what a game with one seat left is (4.4.10 has the same open point), and may change when the winner is picked. S-6.1.3 implements the literal rule and pins it with vectors.
- **Monarch and initiative.** The routine leaves them as they are for now. S-6.1.5 owns the hand-off: its criterion 4 says that when the monarch is eliminated the active player becomes the monarch, or the next non-eliminated seat in turn order if the monarch was the active player, and that the initiative follows the same rule. This routine is where S-6.1.5 hooks that in, so S-6.1.5 will amend the list above.

Worked examples (`standing` before the call; every applied seat gets `seq`):

| #   | `standing` | entries                        | applied   | `no_winner` after | note                                                          |
| --- | ---------- | ------------------------------ | --------- | ----------------- | ------------------------------------------------------------- |
| 1   | 1, 2, 3, 4 | seat 2 `life` by 1             | 2         | unchanged         | one seat, the case of S-6.1.3                                 |
| 2   | 1, 2, 3, 4 | seats 2, 3                     | 2, 3      | unchanged         | the same `eliminated_at_seq` for both (criterion 6)           |
| 3   | 1, 2, 3, 4 | seats 1, 2, 3, 4               | all four  | `true`            | nobody is left (criterion 7)                                  |
| 4   | 3, 4       | seats 3, 4                     | 3, 4      | `true`            | seats 1 and 2 were out earlier and keep their own seq: losses |
| 5   | 4          | seat 4                         | 4         | `true`            | the single-seat case (literal rule)                           |
| 6   | 1, 2, 3, 4 | seat 2 `poison`, seat 2 `life` | 2 (first) | unchanged         | a seat named twice takes its first entry                      |
| 7   | 1, 2       | none, or seat 5                | none      | unchanged         | the state is returned as it is                                |
| 8   | none       | seat 1                         | none      | unchanged         | nobody to eliminate: skipped, and `no_winner` is not set      |

**Vectors.** A single event cannot eliminate several seats before S-6.1.7 (`group_drain`), so criterion 6 and the "every seat at once" half of criterion 7 have no shared vector yet. They are pinned by direct unit tests of the routine with several seats (`src/elimination.test.ts`, and its Python twin), as the eliminated-seat rows of 4.4.10 were before this story. **S-6.1.7 adds the vectors** for them. The shared vectors cover the one-seat cases, including the full draw when the last seat in the game is eliminated (examples 4 and 5, built with consecutive events).

#### 4.5.3 `life_changed`

Payload: `seat`, `delta` (a non-zero integer with |delta| at most 999) and `source_seat?`.

1. Payload shape (4.5.1): `invalid_payload`.
2. Phase (4.5.1): `invalid_phase`.
3. `seat`, then `source_seat`, not at the table: `unknown_seat`.
4. `seat`, then `source_seat`, eliminated: `seat_eliminated`.
5. `delta` is `0`, or its absolute value is more than 999: `invalid_amount`.
6. The `life` of `seat` is not an integer (4.5.1), or `life + delta` is outside -(2^53 - 1) to 2^53 - 1: `invalid_amount`. Python integers do not overflow, so the bound is checked explicitly.

State change: the seat's `life` becomes `life + delta`.

- **Life is not clamped**, in either direction: it can go below 0, and above any earlier total. There is no upper bound and no lower bound other than the safe-integer range of check 6. A gain above the starting life is normal.
- **Elimination.** When `delta` is negative and the new `life` is 0 or less, the seat is eliminated by the routine (4.5.2) with the entry `(seat, life, by, null)`, where `by` is `source_seat` when given and `null` otherwise. `source_seat` may equal `seat` (a self-inflicted loss), and `eliminated_by` is then that seat. `source_seat` is stored nowhere else, so it only matters for an elimination.
- **A positive `delta` never eliminates.** With valid settings a seat cannot be at 0 life or less and still in the game, but a log that starts from unvalidated settings (3.5) can hold one (starting life 0 or -5). A gain on it adds to its life, and it stays in the game even when the total is still 0 or less. Only a later negative `delta` can eliminate it. Nothing in S-6.1.3 revives a seat (that is `player_revived`, S-6.6.5).
- The active seat, the turn, the round, the order and the other seats do not change. An eliminated active seat stays `active_seat` until the next `turn_passed` (4.4.10).

Worked examples (4 seats, default settings, all alive, round 1, unless stated):

| #   | situation before                        | event                                                            | result                                                                                       |
| --- | --------------------------------------- | ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| 1   | seat 2 at 40                            | `{seat: 2, delta: -5}`                                           | life 35, in the game                                                                         |
| 2   | seat 1 at 40                            | `{seat: 1, delta: 10, source_seat: 2}`                           | life 50, in the game                                                                         |
| 3   | seat 3 at 3 (criterion 2)               | `{seat: 3, delta: -3, source_seat: 1}`                           | life 0; reason `life`, `eliminated_by` 1, `eliminated_at_seq` this seq, `eliminated_round` 1 |
| 4   | seat 2 at 5 (criterion 3)               | `{seat: 2, delta: -8}`                                           | life -3; reason `life`, `eliminated_by` `null`                                               |
| 5   | seat 1 at 5                             | `{seat: 1, delta: -4}`                                           | life 1, in the game                                                                          |
| 6   | seat 2 at 40                            | `{seat: 2, delta: -40, source_seat: 2}`                          | life 0; `eliminated_by` 2                                                                    |
| 7   | seat 2 at 40                            | `{seat: 2, delta: -40, source_seat: null}`                       | life 0; `eliminated_by` `null`                                                               |
| 8   | seat 1 at -5, in the game (unvalidated) | `{seat: 1, delta: 3}`                                            | life -2, in the game                                                                         |
| 9   | seat 1 at -5, in the game (unvalidated) | `{seat: 1, delta: -1}`                                           | life -6, eliminated                                                                          |
| 10  | seat 1 at 40                            | `{seat: 1, delta: 0}`, `1000` or `-1000`                         | `invalid_amount`                                                                             |
| 11  | seat 1 at 40                            | `{seat: 1, delta: "3"}`, `true`, `null`, `1.5`, or no `delta`    | `invalid_payload`                                                                            |
| 12  | seat 1's life is the string `"40"`      | `{seat: 1, delta: -3}`                                           | `invalid_amount` (4.5.1)                                                                     |
| 13  | seat 1 at 9007199254740991              | `{seat: 1, delta: 1}`                                            | `invalid_amount`                                                                             |
| 14  | seat 2 eliminated                       | `{seat: 2, delta: 0}`, or `{seat: 1, delta: -1, source_seat: 2}` | `seat_eliminated` (before the amount check)                                                  |
| 15  | lobby                                   | `{seat: 1, delta: -1}`                                           | `invalid_phase`                                                                              |
| 16  | seats 1 and 2 out earlier, seat 3 alone | `{seat: 3, delta: -40}`                                          | seat 3 eliminated and `no_winner` `true`: the single-seat case (4.5.2)                       |

#### 4.5.4 `player_conceded`

Payload: `seat`.

1. Payload shape: `invalid_payload`.
2. Phase: `invalid_phase`.
3. `seat` not at the table: `unknown_seat`.
4. `seat` eliminated: `seat_eliminated`.

State change: the routine (4.5.2) eliminates `seat` with the entry `(seat, concession, null, null)`, **whatever its life** (the life is neither read nor changed). `eliminated_by` and `elimination_cause` are `null`. A concession by the active seat leaves it the `active_seat` until the next `turn_passed`.

#### 4.5.5 `player_eliminated`

Payload: `seat` and `cause?` (a D5 code, 3.4). This is the host's declaration that a seat is out by a rule the tracker does not model (milled out, a combo, an alternate win condition).

1. Payload shape: `invalid_payload`, including a `cause` that is not one of the ten codes. Every D5 code is accepted, `concession`, `last_standing` and `other` included: the reducer does not judge which cause makes sense.
2. Phase: `invalid_phase`.
3. `seat` not at the table: `unknown_seat`.
4. `seat` eliminated: `seat_eliminated`.

State change: the routine (4.5.2) eliminates `seat` with the entry `(seat, declared, null, cause)`, where `cause` is the payload's, or `null` when it is absent or `null`. The life is neither read nor changed. Nothing else changes: this event does not set `winner_seat` or `win_condition`, which are S-6.7.1's.

#### 4.5.6 Vectors of S-6.1.3

The vectors of this story are named `accept-life-changed-*`, `reject-life-changed-*`, `accept-player-conceded-*`, `reject-player-conceded-*`, `accept-player-eliminated-*` and `reject-player-eliminated-*`. Each one builds its table with `seat_joined` and `game_started`. They cover criteria 1 to 5 and 7 (the S-6.1.6 vectors already pinned criterion 1 with the default, with a `game_created` and with a changed `starting_life`), the check order, the non-integer life and the safe-range rule. The `accept-turn-passed-*` vectors with eliminated seats build rows 7 to 13, 15 and 16 of the table in 4.4.10 with `player_conceded`. For criterion 6 and the multi-seat half of criterion 7, see 4.5.2.

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

**Subclasses of the built-in types.** A value parsed from JSON is always a plain string, integer, array or object, so the rules above are complete for anything that arrives over the wire or in a vector. A subclass instance can only be built in memory, and there the two implementations differ in details that this contract does not fix: Python rejects an instance of any subclass of `str`, `int`, `list` or `dict` with `unsupported_value`, except that a `dict` subclass given as the whole `payload` is copied into a plain `dict` first; TypeScript serializes an `Array` subclass as an array and rejects other class instances. Code MUST NOT rely on either behaviour: pass plain values only.

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

The nine handlers of S-6.1.6 (`game_created`, `settings_changed`, `seat_joined`, `seat_left`, `seat_moved`, `deck_assigned`, `commanders_set`, `game_started`, `turn_passed`) follow section 4.4, and the three of S-6.1.3 (`life_changed`, `player_conceded`, `player_eliminated`) follow section 4.5. Every other handler is still a no-op that only sets `last_seq`, whatever the phase. **`game_closed` is a handler-level code, not a generic check in `apply`**: later stories (S-6.2.8, S-6.2.9, S-6.7.3) introduce it in their handlers. Until then an event is accepted in a `finished` or `abandoned` game.

A fractional number can never be in a vector's accept path, because the state and the payloads hold integers only. Python's wire reader (5.6) rejects float tokens as `invalid_event` before `apply` is called; an in-memory Python `float` given to `apply` is `invalid_payload`.

| Code                  | Meaning                                                                                                                                                                                                     |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `unsupported_version` | `v` is an integer the reducer does not support.                                                                                                                                                             |
| `unknown_event_type`  | `type` is not registered.                                                                                                                                                                                   |
| `invalid_event`       | The envelope is malformed.                                                                                                                                                                                  |
| `game_mismatch`       | The event belongs to another game.                                                                                                                                                                          |
| `out_of_order`        | `seq` is not the next one (a gap, a repeat or a rewind).                                                                                                                                                    |
| `invalid_payload`     | The payload is not a canonical value (step 5), or a handler finds a missing key, a wrong type or an unknown enumerated value.                                                                               |
| `game_closed`         | Handler-level, from S-6.2.8 on: the game is `finished` or `abandoned` and the event is not `game_resumed`. Not produced by this version.                                                                    |
| `invalid_phase`       | The event is not allowed in the current phase.                                                                                                                                                              |
| `invalid_setting`     | A setting is outside its range or is not an allowed value.                                                                                                                                                  |
| `invalid_amount`      | A `delta`, `amount` or `value` is out of range, zero where not allowed, or would make a counter negative or a life leave the safe range, or a `life_changed` meets a `life` that is not an integer (4.5.1). |
| `unknown_seat`        | A seat id in the payload is not at the table.                                                                                                                                                               |
| `unknown_commander`   | A commander index that the seat does not have.                                                                                                                                                              |
| `seat_eliminated`     | The event targets or originates from an eliminated seat.                                                                                                                                                    |
| `seat_not_eliminated` | A revive for a seat that is still in the game.                                                                                                                                                              |
| `seat_taken`          | `seat_joined` for a seat id that is already in use.                                                                                                                                                         |
| `table_full`          | `seat_joined` when 8 seats are taken.                                                                                                                                                                       |
| `not_enough_players`  | `game_started` with fewer than 2 seats.                                                                                                                                                                     |
| `not_revertible`      | `event_reverted` targets a `game_created`, `game_started` or `game_ended` event.                                                                                                                            |
| `already_reverted`    | `event_reverted` targets an event that is already reverted.                                                                                                                                                 |

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

**Open point for S-6.6.1 (not a rule yet).** As written, reverting a lobby event after the start can void `game_started` itself: with `seat_joined {seat: 3}` at seq 2 and `game_started {first_seat: 3}` at seq 5, `event_reverted {target_seq: 2}` makes the start `unknown_seat` during the fold, so the game folds back to `lobby` and every later event is voided. Reverting a lobby `settings_changed` would likewise change settings that the start froze (D3). S-6.6.1 must decide this before it implements the fold; the suggested rule is that once `game_started` is in the log, the events before it are `not_revertible`, and `game_started` is never voided.

In this version the `event_reverted` handler is a no-op and `reverted` and `voided` stay `[]`. Both keys exist now so that S-6.6.1 does not change the state shape (and so does not bump `v`, 1.1).

## 7. Purity

A reducer, the canonical serializer and everything they call MUST NOT:

- read a clock (`Date`, `time`, `datetime.now`), including the envelope's `ts`;
- read randomness (`Math.random`, `random`, `crypto`, `uuid4`);
- read the environment, the locale, the network, the file system or any global state;
- depend on iteration order of an unordered collection, or on locale-aware formatting or sorting.

Anything that looks like time or chance arrives **inside an event payload**: `game_started` carries `started_at` and the chosen `first_seat` (a random first player is picked by the server and written into `first_seat`, 4.3). The same events applied to the same state always give the same result. The TypeScript package enforces this with `src/purity.test.ts`; the Python package must have an equivalent test.

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

- `settings` MAY be partial or empty, as in 3.5: a missing setting takes its default. The runner builds the state with `initialState(game_id, settings)`.
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

### 8.3 Validation (every runner, before it runs a file)

A runner validates each file before running it. Validation reports **every** problem of the file (it does not stop at the first), each as one message of the exact form `<file>: <path> <message>`. `<file>` is the base name with `.json` (for example `reject-foo.json`). `<path>` names the bad field: top-level keys by name, nested keys with `.`, array items with `[i]` (`expect.error.index`, `events[2].type`, `cases[0].invalid.kind`). A file with problems fails its own test with all its messages, one per line, and its vector is not run.

**Nesting depth.** Before anything else, including the JSON check, a runner measures the nesting depth of the raw text without parsing it: walk the characters; inside a string a backslash skips the next character and an unescaped `"` ends the string; outside a string `[` and `{` open a level and `]` and `}` close one (the level never goes below 0). The top-level container is level 1. A text whose deepest level is **more than 512** gives the single message `<file>: $ is nested more than 512 levels deep`, and nothing else is reported for the file (not even invalid JSON). A text at exactly 512 levels is accepted. The limit is far above what the vectors use (the deepest literal nesting is about 300 levels; chains such as the 1000-container `deep_array` cases are built by the runner from a count, not written in the file), and it keeps every parser and scanner within its stack: the scanner for number tokens and keys MUST be iterative or otherwise safe at this depth.

**Never throws.** Validation of any text, whatever it holds, returns messages and never raises an error out of the validator: a text nested very deep, a huge number token, an unbalanced or truncated text. A failure of the validator itself is reported as the single message `<file>: $ internal error: <detail>` (the detail is not compared). A runner turns any other unexpected error while running a file into one message for that file starting with the file name, and one broken file never stops the other files, or the coverage check, from running.

**Whole-file failures.** Text that is not valid JSON gives the single message `<file>: invalid JSON: <parser detail>` (the detail is the parser's and is not compared). A top-level value that is not an object gives `<file>: $ must be an object`.

**Encoding.** A vector file is UTF-8 **without** a byte order mark. A file whose bytes are not valid UTF-8 (decoded strictly, never with replacement characters), and a file that starts with a byte order mark (U+FEFF), is not valid JSON text: the runner reports the single message `<file>: invalid JSON: <detail>` (the detail is not compared). The same holds for the TypeScript loader, which decodes strictly and keeps the BOM, and for any Python loader. The nesting-depth check comes first: a text that starts with a byte order mark and is nested more than 512 levels deep gives only the depth message.

**Order of the checks.** A message is reported only when its condition holds; "required" means the key is absent.

1. Header, in this order:
   - `kind`: `kind is required`, or `kind must be "reducer" or "canonical_json"`.
   - `format`: `format is required`, or `format must be the integer 1`.
   - `name`: `name is required`, or `name must equal "<file name without .json>"`.
   - `description`: `description is required`, or `description must be a non-empty string`.
2. The checks of the file's kind (only for a valid `kind`), in the order of the tables below.
3. Unknown top-level keys: `<key> is not a known key`. The known keys are the header keys plus `game_id settings events expect` for `reducer`, or `cases` for `canonical_json`. (Skipped when `kind` is invalid.)

   **Key order and key names, for this and every other per-key message** (unknown keys of `settings`, `expect`, `expect.error`, a case and `invalid`): the messages of one object come in **document order**, the order in which the keys first appear in the raw text (a repeated key stays at its first position). They never follow the order of a parsed object: integer-like keys (`"10"`, `"2"`) are not moved to the front or sorted. A key is known only if it is one of the listed names, compared as a plain string: names such as `constructor`, `toString`, `__proto__` and `hasOwnProperty` are unknown keys, in any object, like any other name. The same holds for `kind`, `invalid.kind` and the error codes: only the listed values are known.

4. Number tokens of the raw text, in document order (8.3.1).
5. Duplicate keys of the raw text, in document order: `<path> is a duplicate key`, **one message for every repeated occurrence** (a key written three times gives two messages, each with the same path). The other rules see the **last** value of a repeated key, as `JSON.parse` and Python's `json` do.

**Reducer files**, in order:

| Field            | Rule and message                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `game_id`        | `game_id is required`; `game_id must be a non-empty string`                                                                                                                                                                                                                                                                                                                                                                                            |
| `settings`       | `settings is required`; `settings must be an object`; each key that is not one of the four setting names: `settings.<key> is not a known key`. Values are not checked (3.5).                                                                                                                                                                                                                                                                           |
| `events`         | `events is required`; `events must be an array` (an empty array is allowed, as the `initial-state*` files use); each item that is not an object: `events[i] must be an object`                                                                                                                                                                                                                                                                         |
| `expect`         | `expect is required`; `expect must be an object`; an unknown key: `expect.<key> is not a known key`; both or neither of `state` and `error`: `expect must have exactly one of "state" or "error"`; `expect.state must be an object`                                                                                                                                                                                                                    |
| `expect.error`   | `expect.error must be an object`; unknown key: `expect.error.<key> is not a known key`; `expect.error.index is required`; `expect.error.index must be a non-negative integer` (a safe integer, 0 or more); `expect.error.index must be less than the number of events (<N>)`; `expect.error.code is required`; `expect.error.code must be a known reducer error code` (one of the codes of 6.2, never a server-only code of 6.3, and not a non-string) |
| `events[i].type` | For every event that is **applied** (an index below `expect.error.index`, or every index when `expect.state` is used): `events[i].type must be a string`. The rejected event and the events after it may have any `type`, or none, which is how the `reject-type-*` vectors build a non-string type.                                                                                                                                                   |

The registry membership of a `type` is **not** validated: the `reject-unknown-event-type*` vectors use unregistered types on purpose. Only the events of a reducer vector may break the envelope or payload rules, and the structural checks never look inside `payload`, `actor` or the other envelope keys.

**Details that fix the rules above** (both implementations apply them):

- **A token message replaces the field rule for an integral float.** A header or integer field written as an integral float (`"format": 1.0`, `"index": 1.0`) gets only the 8.3.1 token message. The field rules treat a value as the integer it equals, so `format must be the integer 1` and `expect.error.index must be a non-negative integer` do not fire for `1.0`. When the integer it equals breaks the field rule, both messages are given: `"format": 2.0` gets the token message and `format must be the integer 1`, and `"index": -1.0` gets the token message and `expect.error.index must be a non-negative integer`.
- **Events not an array.** When `events` is not an array, the check `expect.error.index must be less than the number of events (<N>)` is skipped.
- **Which events are "applied" for the `events[i].type must be a string` rule.** When `expect.error.index` is a valid index (a non-negative integer below the number of events), the applied events are those below it, even if `expect.state` is also present. In every other case, including a missing, invalid or out-of-range index, an `expect.error` that is not an object, an `expect` with neither key, and an `expect` that is missing or is not an object (a number, `null`, a string, an array), all events are applied. The `type` messages come **last** among the reducer checks (after every `expect` message, and before the unknown top-level keys of check 3). An applied item that is not an object gets only `events[i] must be an object`, no `type` message.
- **Case names.** A case whose `name` is missing or is not a non-empty string does not take part in the duplicate-name check.
- **"Non-empty" is a length check.** `description` and `cases[i].name` must have length greater than zero; a whitespace-only value is accepted. A `name` that is not a string gives `name must equal "<stem>"`.
- **A missing `invalid.kind`** gives only `cases[i].invalid.kind is required`; the `invalid.value` rules are skipped.

**Canonical-JSON files**: `cases is required`; `cases must be a non-empty array`. Then for each case `i`, in order:

| Rule                                                                                  | Message                                                                                                                                                                                                                                            |
| ------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| not an object                                                                         | `cases[i] must be an object` (the rest of this case is skipped)                                                                                                                                                                                    |
| `name`                                                                                | `cases[i].name is required`; `cases[i].name must be a non-empty string`; `cases[i].name duplicates the name of cases[j]` (j is the first case with that name)                                                                                      |
| neither or both of `input` and `invalid`                                              | `cases[i] must have exactly one of "input" or "invalid"` (the rest of this case is skipped)                                                                                                                                                        |
| a key outside `name input output` (input case) or `name invalid error` (invalid case) | `cases[i].<key> is not a known key`                                                                                                                                                                                                                |
| input case                                                                            | `cases[i].output is required`; `cases[i].output must be a string`                                                                                                                                                                                  |
| invalid case, `error`                                                                 | `cases[i].error is required`; `cases[i].error must be a canonical JSON error code` (one of the four codes of 5.4)                                                                                                                                  |
| invalid case, `invalid`                                                               | `cases[i].invalid must be an object`; unknown key: `cases[i].invalid.<key> is not a known key`; `cases[i].invalid.kind is required`; `cases[i].invalid.kind must be one of float, nan, infinity, integer, lone_surrogate, deep_array, deep_object` |
| `invalid.value`                                                                       | for `nan` and `infinity`: `cases[i].invalid.value must be absent`. For the other kinds: `cases[i].invalid.value is required`, then one of the messages below.                                                                                      |

The `invalid.value` formats (a string in every case). Each message below follows the path `cases[i].invalid.value `:

- `float`: JSON number syntax whose value is finite and **not** an integer (`1.5`, `1e-7`, `5e-324`; never `1.0` or `1e2`). Message: `must be decimal text of a non-integer number`.
- `integer`: an integer in decimal, with an optional `-`, no leading zeros, an absolute value above 9007199254740991, and **at most 300 digits** (the `-` is not a digit). Checks in this order, one message at most: text that is not such an integer, or whose absolute value is 9007199254740991 or less: `must be decimal text of an integer outside the safe range`; more than 300 digits: `must have at most 300 digits`. The cap keeps every allowed value a finite double in TypeScript and well inside the integer-text limits of Python. The TypeScript runner turns the text into a number with `Number(value)`: every allowed value is then a finite double of magnitude above 2^53 - 1 (at most about 1e300), which the serializer rejects as `integer_out_of_range` (5.4), the error every `integer` case expects. A runner may use any exact conversion that gives an integer outside the safe range (for example `int()` in Python).
- `lone_surrogate`: exactly 4 hex digits, `d800` to `dfff` (either case). Message: `must be the 4-digit hex code of a surrogate code unit (d800 to dfff)`.
- `deep_array`, `deep_object`: a positive integer in decimal without leading zeros, at most 10000 (so a runner never builds an unbounded chain). Message: `must be decimal text of an integer from 1 to 10000`.

#### 8.3.1 Number tokens

TypeScript cannot tell `1.0` from `1` after parsing, and Python reads `1.0` as a float, so a vector must never rely on an integral float. A runner therefore checks the **raw text**, not the parsed value, with a scanner that lists every number token with the path of the value it is (`events[0].v`, `cases[2].input[1].a`; text inside strings is not a token). Python can use `parse_float` and `parse_int` hooks that record the token text with the path, or the same small scanner as the TypeScript one (`scanText` in `src/vector-validator.ts`). The rules, by where the token is:

- **Inside `events`** (the path starts with `events[`): an integer token is accepted as written, whatever its size (a vector may hold an integer outside the safe range and expect `invalid_payload`; in TypeScript such a token loses precision when parsed, but every integer beyond the safe range is rejected in both languages, so the outcome is the same). A token with a fraction or exponent is accepted **only if** its value, **as an IEEE-754 double** (the value both languages parse it to), is finite and not an integer (`1.5`, `1e-7`). So `1e-400` (a double 0) and `1.0000000000000001` (a double 1) count as integral and are rejected. The same double rule decides `invalid.value` of kind `float` in 8.3 (`1e-400` and `1.0000000000000001` are not valid there). Any other token with a fraction or exponent (`1.0`, `1e2`, `1e999`) gives `<path> has the number token <token>; write an integral value as an integer`.
- **Everywhere else** (`settings`, `expect`, `cases`, the header): a token with a fraction or exponent gives `<path> must be an integer (found the number token <token>)`, and an integer token with an absolute value above 9007199254740991 gives `<path> must be an integer in the safe range (found <token>)`.
- `-0` is accepted everywhere (canonical JSON writes it as `0`, 5.2, and the integer vectors rely on it).

### 8.4 Event-type coverage

Every type in the contract registry (the table of section 4, 29 types) must be **exercised** by at least one vector, or the runner fails with the exact message `uncovered event type: <type>`. There is one message per uncovered type, in registry (table) order. The registry is the runner's own list (`EVENT_TYPES` in TypeScript, the reducer's registry in Python), and each side has a test that its list equals the table of section 4.

A vector **exercises** a type when the type is the `type` (a string) of an event that is **applied successfully**:

- in a vector with `expect.state`: every event of the vector;
- in a vector with `expect.error`: only the events at an index **below** `expect.error.index`. The rejected event and the events after it do not count, so a `reject-*` vector never covers the type it rejects.

Only reducer vectors that pass validation count; a file that fails validation fails its own test and contributes nothing. The check runs when the runner starts, whether or not other tests fail. In TypeScript it is one test per registered type (`event-type coverage > <type> is exercised by a vector`) that throws the message; the Python runner may report it per type or as one failure, as long as each message appears verbatim.

#### 8.4.1 Note for the handler stories

A type that is covered only as an **applied prefix event of a rejecting vector** is run through its handler, but its effect is never asserted: the vector ends in an error and compares no state. While handlers are no-ops this does not matter. Once a handler gains behaviour, each registered type should also appear in at least one vector that expects a **state**, so that the handler's effect is pinned. The coverage rule of this section does not require it. The S-6.1.6 and S-6.1.3 types all do: `life_changed`, `player_conceded` and `player_eliminated` have `accept-*` vectors with a state. The types that are still no-ops (17 of the 29) only have to be applied successfully by the `accept-all-registered-event-types*.json` vectors.

### 8.5 Runners

- **Loading.** A runner reads every **regular file** whose name ends in exactly `.json` (lower case, compared case-sensitively) directly in `packages/game-core/vectors/`, sorted by name. Anything else is **ignored, silently and without an error**: a directory (even one named `x.json`; nested directories are not searched), a **symbolic link** (it is not followed, so a link to a file, to a directory or a dangling link is simply not a vector file), and a file with another spelling of the extension (`x.JSON`, `x.Json`) or none. Only regular files count towards "no `.json` file" below. A missing directory, or one with no `.json` file, fails loudly (TypeScript: `vectors directory not found: <dir>` and `vectors directory has no .json files: <dir>`) instead of passing with zero tests.
- **One test per file, named by the file**, so a failure names the vector file: Vitest `shared vectors > <file>.json`; pytest parametrized with the file name as the test id (`test_vector[<file>.json]`). A validation problem (including an unknown `kind`) or a mismatch fails that file's test, and the message starts with the file name. For a canonical file the one test runs **every** case and reports **every** failing case in its one failure, one line per case, in case order: `<file>: case "<name>": <detail>` (a runner does not stop at the first failing case). A reducer file has one expectation, so one failure. For a reducer file the detail is the expected and actual index and code, or the expected and actual canonical final state.
- **A vector that passes in one language and fails in the other** fails only the suite of the language that disagrees, and that suite names the file. The gate (`pnpm verify`, which the `api` and `ts` jobs of CI call) runs both suites, so the gate fails and the output names the file.
- **Where the code lives (TypeScript).** The validator and the coverage rule are pure functions in `src/vector-validator.ts` (no file access, not exported from `index.ts`, so no bundle ships it). The file loader and the per-file runner are in `src/vector-loader.test.helpers.ts` (Node file access; the `.test.` in the name keeps it under the purity guard's test-code rule and out of any bundle). The runner is `src/vectors.test.ts`, and the unit tests of the rules are `src/vector-validator.test.ts`.

### 8.6 The vectors

The shared conformance corpus for the validator and the coverage rule is `packages/game-core/vector-cases/` (format in its `README.md`): the TypeScript and Python suites both run every case there and must return exactly the messages it lists. A case states one list of messages (there are no alternative readings), and a suite fails if a case carries `alternatives` or `gap`.

Reducer vectors are named `accept-*` (the events are accepted and the final state is compared) and `reject-*` (one event is rejected with a given code). They cover every envelope and payload rule of sections 2 and 6.2, including the check order (`reject-precedence-*`). `canonical-json*.json` hold the serializer cases, `initial-state*.json` the initial state, and `canonical-json-game-state.json` realistic full states as serialization cases (seats cannot be built through no-op handlers). `accept-all-registered-event-types*.json` apply every registered type in one legal log (a lobby with seats, the start, then the types whose handlers are still no-ops), which satisfies the coverage rule of 8.4. When handlers gain behaviour, such a vector must still apply every type successfully, or the coverage rule fails. The S-6.1.6 vectors are named `accept-<event type>-*` and `reject-<event type>-*` with the type's underscores written as hyphens (`accept-seat-moved-*`, `reject-game-started-*`); each one starts from an empty lobby and builds its seats with `seat_joined`. The S-6.1.3 vectors follow the same naming (`accept-life-changed-*`, `reject-player-conceded-*`, 4.5.6). `accept-all-registered-event-types*.json` give `life_changed`, `player_conceded` and `player_eliminated` real payloads, and apply the 17 other types that are still no-ops with an empty payload.
