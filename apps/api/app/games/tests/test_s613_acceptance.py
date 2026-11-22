"""Acceptance tests of S-6.1.3 (life totals, concession and life-based elimination), the Python
twin of ``packages/game-core/src/acceptance-s613.test.ts``: same cases, same numbers.

Written from the story's seven acceptance criteria and CONTRACT.md 3.3, 4.1, 4.5 and 6.2, not from
the handlers. The shared vectors (``test_vectors.py``) are the primary proof for everything
reachable through events. This file adds what a vector cannot say: the elimination routine called
with several seats (no event eliminates two seats before S-6.1.7), phases a log cannot reach yet
(``ending``, ``finished``, ``abandoned``), every registered event type against an eliminated seat,
the check order parsed from the contract, and a brute-force oracle written from the contract's
words.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from typing import Any

import pytest

from app.games.core import (
    EVENT_TYPES,
    REDUCER_ERROR_CODES,
    ApplyError,
    GameState,
    apply,
    initial_state,
)
from app.games.core import canonical_json as canonical
from app.games.core.contract import WIN_CONDITION_CODES
from app.games.core.elimination import Elimination, eliminate
from app.games.tests.lobby_helpers import (
    GAME_ID,
    NO_OP_TYPES,
    accept,
    lobby,
    make_event,
    reject_code,
)
from app.games.tests.repo_files import CONTRACT_PATH, VECTORS_DIR, contract_text, require

MAX_SAFE = 9007199254740991
ABSENT: Any = object()  # a payload key that is not written at all


def ev(event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return make_event(event_type, payload)


def lc(seat: Any, delta: Any = ABSENT, source: Any = ABSENT) -> dict[str, Any]:
    payload: dict[str, Any] = {"seat": seat}
    if delta is not ABSENT:
        payload["delta"] = delta
    if source is not ABSENT:
        payload["source_seat"] = source
    return ev("life_changed", payload)


def concede(seat: Any) -> dict[str, Any]:
    return ev("player_conceded", {"seat": seat})


def declare(seat: Any, cause: Any = ABSENT) -> dict[str, Any]:
    payload: dict[str, Any] = {"seat": seat}
    if cause is not ABSENT:
        payload["cause"] = cause
    return ev("player_eliminated", payload)


def joined(seat: int) -> dict[str, Any]:
    return ev("seat_joined", {"seat": seat, "player_id": None, "name": f"Seat {seat}"})


def started(first: int) -> dict[str, Any]:
    return ev("game_started", {"first_seat": first, "started_at": "2026-11-21T12:00:00.000Z"})


def run(state: GameState, *events: dict[str, Any]) -> GameState:
    for event in events:
        state = accept(state, event)
    return state


def lobby_with(count: int, starting_life: Any = 40) -> GameState:
    state = initial_state(GAME_ID, {"starting_life": starting_life})
    return run(state, *(joined(i) for i in range(1, count + 1)))


def game(count: int = 4, starting_life: Any = 40) -> GameState:
    """A started game: seats 1 to ``count``, ``starting_life`` from the settings, seat 1 first."""
    return run(lobby_with(count, starting_life), started(1))


def seat_of(state: GameState, seat_id: int) -> Any:
    return next(s for s in state.seats if s.seat == seat_id)


def in_game(state: GameState) -> list[int]:
    return [s.seat for s in state.seats if s.eliminated_at_seq is None]


def in_phase(state: GameState, phase: str) -> GameState:
    return state.model_copy(
        update={"phase": phase, "resume_phase": "active" if phase == "abandoned" else None}
    )


def with_seat(state: GameState, seat_id: int, **changes: Any) -> GameState:
    seats = [s.model_copy(update=changes) if s.seat == seat_id else s for s in state.seats]
    return state.model_copy(update={"seats": seats})


def out_at(state: GameState, seat_id: int, at_seq: int, round_: int = 1) -> GameState:
    """A seat eliminated by life loss at ``at_seq``, built by hand."""
    return with_seat(
        state,
        seat_id,
        life=0,
        elimination_reason="life",
        eliminated_by=None,
        eliminated_at_seq=at_seq,
        eliminated_round=round_,
        elimination_cause=None,
    )


def canon(state: GameState) -> str:
    return canonical(state.to_json_value())


def code_of(state: GameState, event: dict[str, Any]) -> str:
    """``ok`` when the event is accepted, otherwise the error code."""
    result = apply(state, event)
    return str(result.error.code) if isinstance(result, ApplyError) else "ok"


# --- parsing the contract -------------------------------------------------------------------------


def section(heading_prefix: str) -> str:
    lines = contract_text().split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(heading_prefix))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^#{1,4} ", lines[i]):
            end = i
            break
    return "\n".join(lines[start:end])


def numbered_codes(text: str) -> list[str]:
    """The first reducer error code in each numbered item of a section, in order."""
    out: list[str] = []
    for item in re.finditer(r"^\d+\. (.*)$", text, flags=re.MULTILINE):
        tokens = re.findall(r"`([a-z_]+)`", item.group(1))
        code = next((t for t in tokens if t in REDUCER_ERROR_CODES), None)
        if code is not None:
            out.append(code)
    return out


def table_rows(text: str) -> list[list[str]]:
    """The rows of a table whose first cell is a number, cells trimmed."""
    return [
        [cell.strip() for cell in line.split("|")[1:-1]]
        for line in text.split("\n")
        if re.match(r"^\| \d+ +\|", line)
    ]


def ints(cell: str) -> list[int]:
    return [int(m) for m in re.findall(r"\d+", cell)]


def checks_4_5_1() -> list[str]:
    return numbered_codes(section("#### 4.5.1 "))


def checks_life() -> list[str]:
    return numbered_codes(section("#### 4.5.3 "))


def checks_concede() -> list[str]:
    return numbered_codes(section("#### 4.5.4 "))


def checks_declare() -> list[str]:
    return numbered_codes(section("#### 4.5.5 "))


def d5_codes() -> list[str]:
    line = next(ln for ln in section("### 3.4 ").split("\n") if ln.startswith("`combat_damage`"))
    return re.findall(r"`([a-z_]+)`", line)


# --- the contract text -------------------------------------------------------------------------


def test_the_contract_lists_the_ten_d5_codes_the_code_knows() -> None:
    assert len(d5_codes()) == 10
    assert sorted(d5_codes()) == sorted(WIN_CONDITION_CODES)


def test_4_5_1_fixes_the_order_payload_phase_unknown_seat_seat_eliminated() -> None:
    assert checks_4_5_1() == ["invalid_payload", "invalid_phase", "unknown_seat", "seat_eliminated"]


def test_4_5_3_life_changed_checks_in_that_order_then_the_amount_rules() -> None:
    assert checks_life() == [
        "invalid_payload",
        "invalid_phase",
        "unknown_seat",
        "seat_eliminated",
        "invalid_amount",
        "invalid_amount",
    ]


def test_4_5_4_and_4_5_5_use_the_shared_order_and_nothing_more() -> None:
    assert checks_concede() == checks_4_5_1()
    assert checks_declare() == checks_4_5_1()


# --- criterion 1 ----------------------------------------------------------------------------------

SETTINGS_30 = {
    "starting_life": 30,
    "commander_damage_threshold": 21,
    "poison_threshold": 10,
    "trust_mode": "casual",
}


def settings_with(value: Any) -> dict[str, Any]:
    return {**SETTINGS_30, "starting_life": value}


def test_criterion_1_default_settings_give_every_seat_40_life() -> None:
    state = run(lobby(), joined(1), joined(2), joined(3), joined(4), started(1))
    assert state.settings.starting_life == 40
    assert [s.life for s in state.seats] == [40, 40, 40, 40]


def test_criterion_1_starting_life_30_from_the_initial_settings_gives_every_seat_30() -> None:
    assert [s.life for s in game(4, 30).seats] == [30, 30, 30, 30]


def test_criterion_1_starting_life_30_from_game_created_gives_every_seat_30() -> None:
    created = ev("game_created", {"settings": SETTINGS_30})
    state = run(lobby(), created, joined(1), joined(2), started(2))
    assert [s.life for s in state.seats] == [30, 30]


def test_criterion_1_a_lobby_override_gives_every_seat_30_before_and_after_it_joined() -> None:
    state = run(
        lobby(),
        joined(1),
        ev("settings_changed", {"starting_life": 30}),
        joined(2),
        started(1),
    )
    assert [s.life for s in state.seats] == [30, 30]


@pytest.mark.parametrize("value", [1, 999])
def test_criterion_1_starting_life_at_the_bounds_is_accepted(value: int) -> None:
    created = ev("game_created", {"settings": settings_with(value)})
    assert code_of(lobby(), created) == "ok"
    assert code_of(lobby(), ev("settings_changed", {"starting_life": value})) == "ok"


@pytest.mark.parametrize("value", [0, -1, 1000, 1001, MAX_SAFE, "40", True, None])
def test_criterion_1_starting_life_outside_1_to_999_is_invalid_setting(value: Any) -> None:
    created = ev("game_created", {"settings": settings_with(value)})
    assert code_of(lobby(), created) == "invalid_setting"
    assert code_of(lobby(), ev("settings_changed", {"starting_life": value})) == "invalid_setting"


def test_criterion_1_a_rejected_starting_life_leaves_every_seat_at_its_life() -> None:
    state = run(lobby(), joined(1), joined(2))
    assert reject_code(state, ev("settings_changed", {"starting_life": 1000})) == "invalid_setting"
    assert [s.life for s in state.seats] == [40, 40]


# --- criterion 2 ----------------------------------------------------------------------------------


def hit() -> dict[str, Any]:
    return lc(3, -3, 1)


def test_criterion_2_seat_3_at_3_life_minus_3_from_seat_1_is_eliminated_by_life() -> None:
    before = game(4, 3)  # seat 3 at 3 life
    assert seat_of(before, 3).life == 3
    seat = seat_of(accept(before, hit()), 3)
    assert seat.life == 0
    assert seat.elimination_reason == "life"
    assert seat.eliminated_by == 1
    assert seat.elimination_cause is None


def test_criterion_2_eliminated_at_seq_is_the_seq_of_that_event() -> None:
    before = game(4, 3)
    after = accept(before, hit())
    assert after.last_seq == before.last_seq + 1
    assert seat_of(after, 3).eliminated_at_seq == after.last_seq


def test_criterion_2_eliminated_at_seq_is_the_seq_the_event_carries() -> None:
    before = game(4, 3)
    seq = before.last_seq + 1
    event = make_event("life_changed", {"seat": 3, "delta": -3, "source_seat": 1}, seq=seq)
    assert seat_of(accept(before, event), 3).eliminated_at_seq == seq


def test_criterion_2_after_other_events_the_seq_is_later_not_a_constant() -> None:
    before = run(game(4, 3), lc(1, -1), lc(2, -1), lc(4, -1))
    after = accept(before, hit())
    assert seat_of(after, 3).eliminated_at_seq == before.last_seq + 1 == 9


def test_criterion_2_the_elimination_records_the_round_and_does_not_change_it() -> None:
    state = game(4, 3)
    for _ in range(4):
        state = accept(state, ev("turn_passed"))
    assert (state.round, state.turn) == (2, 5)
    after = accept(state, hit())
    assert seat_of(after, 3).eliminated_round == 2
    assert (after.round, after.turn) == (2, 5)


def test_criterion_2_other_seats_phase_and_active_seat_are_untouched_and_nobody_wins() -> None:
    before = game(4, 3)
    after = accept(before, hit())
    for seat_id in (1, 2, 4):
        assert seat_of(after, seat_id) == seat_of(before, seat_id)
    assert (after.phase, after.active_seat, after.no_winner, after.winner_seat) == (
        "active",
        1,
        False,
        None,
    )
    assert in_game(after) == [1, 2, 4]


def test_criterion_2_the_same_numbers_reached_from_40_life() -> None:
    after = run(game(4), lc(3, -37), lc(3, -3, 1))
    seat = seat_of(after, 3)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by, seat.eliminated_at_seq) == (
        0,
        "life",
        1,
        7,
    )


def test_criterion_2_one_life_left_is_still_in_the_game() -> None:
    after = accept(game(4, 3), lc(3, -2, 1))
    assert (seat_of(after, 3).life, seat_of(after, 3).eliminated_at_seq) == (1, None)
    assert in_game(after) == [1, 2, 3, 4]


# --- criterion 3 ----------------------------------------------------------------------------------


def test_criterion_3_seat_2_at_5_minus_8_is_minus_3_and_eliminated_with_no_eliminator() -> None:
    seat = seat_of(accept(game(4, 5), lc(2, -8)), 2)
    assert seat.life == -3
    assert seat.elimination_reason == "life"
    assert seat.eliminated_by is None
    assert seat.eliminated_at_seq == 6
    assert seat.eliminated_round == 1


def test_criterion_3_a_null_source_seat_is_the_same_as_none() -> None:
    seat = seat_of(accept(game(4, 5), lc(2, -8, None)), 2)
    assert (seat.life, seat.eliminated_by) == (-3, None)


def test_criterion_3_a_source_seat_that_is_the_target_is_stored_as_eliminated_by() -> None:
    assert seat_of(accept(game(4, 5), lc(2, -8, 2)), 2).eliminated_by == 2


def test_criterion_3_there_is_no_upper_clamp_either() -> None:
    assert seat_of(accept(game(4, 5), lc(2, 999)), 2).life == 1004


def test_criterion_3_the_biggest_single_hit_is_minus_999() -> None:
    assert seat_of(accept(game(4, 5), lc(2, -999)), 2).life == -994


# --- criterion 4 ----------------------------------------------------------------------------------


def base4() -> GameState:
    return out_at(game(4), 2, 5)


def test_criterion_4_life_changed_on_seat_2_gain_or_loss_is_seat_eliminated() -> None:
    assert code_of(base4(), lc(2, -1)) == "seat_eliminated"
    assert code_of(base4(), lc(2, 1)) == "seat_eliminated"


def test_criterion_4_life_changed_from_seat_2_as_source_gain_or_loss_is_seat_eliminated() -> None:
    assert code_of(base4(), lc(1, -1, 2)) == "seat_eliminated"
    assert code_of(base4(), lc(1, 1, 2)) == "seat_eliminated"


def test_criterion_4_conceding_or_being_declared_out_again_is_seat_eliminated() -> None:
    assert code_of(base4(), concede(2)) == "seat_eliminated"
    assert code_of(base4(), declare(2)) == "seat_eliminated"
    assert code_of(base4(), declare(2, "mill")) == "seat_eliminated"


def test_criterion_4_a_seat_eliminated_by_an_event_is_closed_to_the_three_events() -> None:
    state = accept(game(4), lc(2, -40, 1))
    assert code_of(state, lc(2, -1)) == "seat_eliminated"
    assert code_of(state, concede(2)) == "seat_eliminated"
    assert code_of(state, declare(2, "combo")) == "seat_eliminated"
    assert code_of(accept(game(4), concede(2)), lc(2, -1)) == "seat_eliminated"
    assert code_of(accept(game(4), declare(2)), concede(2)) == "seat_eliminated"


def test_criterion_4_a_rejected_event_changes_nothing() -> None:
    before = base4()
    snapshot = canon(before)
    assert isinstance(apply(before, lc(2, -1)), ApplyError)
    assert canon(before) == snapshot


# What each of the 29 registered types does when it names eliminated seat 2 in an active game.
# kind: seat_eliminated = rejected by 4.5; exception = a revert or host correction (4.5.1);
# phase = a documented rejection (lobby-only handlers); no_seat = names no seat;
# pending = a no-op until its story lands (that story inherits criterion 4, see the report).
SETTINGS = {
    "starting_life": 40,
    "commander_damage_threshold": 21,
    "poison_threshold": 10,
    "trust_mode": "casual",
}
ONE_COMMANDER = [{"card_id": None, "name": ""}]
ROWS: dict[str, tuple[str, list[dict[str, Any]], str]] = {
    "game_created": ("phase", [{"settings": SETTINGS}], "invalid_phase"),
    "settings_changed": ("phase", [{"starting_life": 30}], "invalid_phase"),
    "seat_joined": ("phase", [{"seat": 2, "player_id": None, "name": "x"}], "invalid_phase"),
    "seat_left": ("phase", [{"seat": 2}], "invalid_phase"),
    "seat_moved": (
        "exception",
        [{"from": 2, "to": 3, "correction": True}, {"from": 3, "to": 2, "correction": True}],
        "ok",
    ),
    "seat_claimed": ("pending", [{"seat": 2, "player_id": "p"}], "ok"),
    "deck_assigned": (
        "phase",
        [{"seat": 2, "deck_version_id": "d", "commanders": ONE_COMMANDER}],
        "invalid_phase",
    ),
    "commanders_set": ("phase", [{"seat": 2, "commanders": ONE_COMMANDER}], "invalid_phase"),
    "control_mode_changed": ("pending", [{"mode": "host_all"}], "ok"),
    "game_started": (
        "phase",
        [{"first_seat": 2, "started_at": "2026-11-21T12:00:00.000Z"}],
        "invalid_phase",
    ),
    "turn_passed": ("no_seat", [{}], "ok"),
    "life_changed": (
        "seat_eliminated",
        [{"seat": 2, "delta": -1}, {"seat": 1, "delta": -1, "source_seat": 2}],
        "seat_eliminated",
    ),
    "commander_damage": (
        "seat_eliminated",
        [
            {"source_seat": 2, "commander": 0, "target_seat": 3, "amount": 1},
            {"source_seat": 3, "commander": 0, "target_seat": 2, "amount": 1},
        ],
        "seat_eliminated",
    ),
    "poison_changed": ("pending", [{"seat": 2, "delta": 1}], "ok"),
    "counter_changed": ("pending", [{"seat": 2, "counter": "energy", "delta": 1}], "ok"),
    "commander_cast": ("pending", [{"seat": 2, "commander": 0, "delta": 1}], "ok"),
    "monarch_set": ("pending", [{"seat": 2}], "ok"),
    "initiative_set": ("pending", [{"seat": 2}], "ok"),
    "drain": (
        "pending",
        [
            {"source_seat": 2, "target_seat": 3, "amount": 1},
            {"source_seat": 3, "target_seat": 2, "amount": 1},
        ],
        "ok",
    ),
    "group_drain": ("pending", [{"source_seat": 2, "amount": 1, "gain": True}], "ok"),
    "player_conceded": ("seat_eliminated", [{"seat": 2}], "seat_eliminated"),
    "player_eliminated": (
        "seat_eliminated",
        [{"seat": 2}, {"seat": 2, "cause": "mill"}],
        "seat_eliminated",
    ),
    "player_revived": ("exception", [{"seat": 2, "reason": "misclick"}], "ok"),
    "value_set": ("exception", [{"seat": 2, "field": "life", "value": 10}], "ok"),
    "game_end_requested": ("pending", [{}], "ok"),
    "game_ended": ("pending", [{"summary": {}}], "ok"),
    "game_abandoned": ("pending", [{"reason": "host"}], "ok"),
    "game_resumed": ("pending", [{}], "ok"),
    "event_reverted": ("exception", [{"target_seq": 6}], "ok"),
}


def test_criterion_4_the_table_covers_every_registered_type_once() -> None:
    assert sorted(ROWS) == sorted(EVENT_TYPES)


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_criterion_4_every_registered_type_against_an_eliminated_seat_2(event_type: str) -> None:
    _, payloads, code = ROWS[event_type]
    for payload in payloads:
        assert code_of(base4(), ev(event_type, payload)) == code


def test_criterion_4_the_no_op_types_are_the_pending_rows_and_the_unspecified_exceptions() -> None:
    unspecified = {"player_revived", "value_set", "event_reverted"}
    no_ops = sorted(t for t, (kind, _, _) in ROWS.items() if kind == "pending" or t in unspecified)
    assert no_ops == sorted(NO_OP_TYPES)


def test_criterion_4_a_no_op_type_accepts_an_eliminated_seat_and_changes_only_last_seq() -> None:
    for event_type, (_, payloads, _) in ROWS.items():
        if event_type not in NO_OP_TYPES:
            continue
        for payload in payloads:
            before = base4()
            after = accept(before, ev(event_type, payload))
            assert canon(after) == canon(
                before.model_copy(update={"last_seq": before.last_seq + 1})
            )


def test_criterion_4_seat_moved_with_correction_may_name_an_eliminated_seat() -> None:
    before = base4()
    after = accept(before, ev("seat_moved", {"from": 2, "to": 4, "correction": True}))
    assert after.order == [1, 4, 3, 2]
    assert after.seats == before.seats


def test_criterion_4_seat_moved_without_a_correction_in_an_active_game_is_invalid_phase() -> None:
    assert code_of(base4(), ev("seat_moved", {"from": 2, "to": 4})) == "invalid_phase"


def test_criterion_4_turn_passed_skips_the_eliminated_seat() -> None:
    assert accept(out_at(game(4), 2, 5), ev("turn_passed")).active_seat == 3


def test_criterion_4_the_contract_names_the_same_exceptions_as_the_table() -> None:
    text = section("#### 4.5.1 ")
    start = text.index("**What may still target an eliminated seat.**")
    end = text.index("No other S-6.1.6 handler")
    types: set[str] = set()
    for line in text[start:end].split("\n"):
        if line.startswith("- "):
            types.update(t for t in re.findall(r"`([a-z_]+)`", line) if t in EVENT_TYPES)
    assert sorted(types) == sorted(t for t, (kind, _, _) in ROWS.items() if kind == "exception")


# --- criterion 5 ----------------------------------------------------------------------------------


def test_criterion_5_player_conceded_seat_4_is_eliminated_with_reason_concession() -> None:
    after = accept(game(4), concede(4))
    seat = seat_of(after, 4)
    assert seat.life == 40
    assert seat.elimination_reason == "concession"
    assert seat.eliminated_by is None
    assert seat.eliminated_at_seq == after.last_seq
    assert seat.eliminated_round == 1
    assert seat.elimination_cause is None
    assert in_game(after) == [1, 2, 3]


@pytest.mark.parametrize("life", [40, 1, 999, -3])
def test_criterion_5_a_concession_works_whatever_the_life(life: int) -> None:
    after = accept(with_seat(game(4), 4, life=life), concede(4))
    assert (seat_of(after, 4).life, seat_of(after, 4).elimination_reason) == (life, "concession")


def test_criterion_5_a_concession_by_the_active_seat_keeps_it_active_until_the_turn_passes() -> (
    None
):
    after = accept(game(4), concede(1))
    assert after.active_seat == 1
    assert accept(after, ev("turn_passed")).active_seat == 2


@pytest.mark.parametrize("cause", d5_codes() if CONTRACT_PATH.exists() else [])
def test_criterion_5_a_host_elimination_records_reason_declared_and_the_cause(cause: str) -> None:
    after = accept(game(4), declare(4, cause))
    seat = seat_of(after, 4)
    assert seat.life == 40
    assert seat.elimination_reason == "declared"
    assert seat.eliminated_by is None
    assert seat.elimination_cause == cause
    assert seat.eliminated_at_seq == after.last_seq


@pytest.mark.parametrize("event", [declare(4), declare(4, None)])
def test_criterion_5_a_host_elimination_without_a_cause_records_cause_null(
    event: dict[str, Any],
) -> None:
    seat = seat_of(accept(game(4), event), 4)
    assert (seat.elimination_reason, seat.elimination_cause) == ("declared", None)


def test_criterion_5_a_host_elimination_works_whatever_the_life_and_leaves_it_alone() -> None:
    before = with_seat(game(4), 4, life=17)
    assert seat_of(accept(before, declare(4, "mill")), 4).life == 17


def test_criterion_5_a_host_elimination_names_no_winner_and_no_win_condition() -> None:
    after = accept(game(4), declare(4, "mill"))
    assert (after.winner_seat, after.win_condition, after.no_winner) == (None, None, False)


@pytest.mark.parametrize(
    "cause", ["Mill", "MILL", "mill ", "", "draw", "win", "life", 7, False, True, [], {}, ["mill"]]
)
def test_criterion_5_a_cause_that_is_not_a_d5_code_is_invalid_payload(cause: Any) -> None:
    assert code_of(game(4), declare(4, cause)) == "invalid_payload"


@pytest.mark.parametrize("delta", [0, 1000, -1000, 1001, -1001, MAX_SAFE, -MAX_SAFE])
def test_criterion_5_a_delta_of_zero_or_above_999_is_invalid_amount(delta: int) -> None:
    assert code_of(game(4), lc(1, delta)) == "invalid_amount"


@pytest.mark.parametrize("delta", [1, -1, 2, -2, 998, -998, 999, -999])
def test_criterion_5_a_delta_up_to_999_is_accepted(delta: int) -> None:
    assert code_of(game(4), lc(1, delta)) == "ok"


@pytest.mark.parametrize("delta", ["3", True, False, None, [], {}, 1.5, -0.5])
def test_criterion_5_a_delta_of_the_wrong_type_is_invalid_payload_not_invalid_amount(
    delta: Any,
) -> None:
    assert code_of(game(4), lc(1, delta)) == "invalid_payload"


def test_criterion_5_no_delta_is_invalid_payload() -> None:
    assert code_of(game(4), lc(1)) == "invalid_payload"


def test_criterion_5_a_delta_outside_the_safe_range_is_invalid_payload() -> None:
    assert code_of(game(4), lc(1, MAX_SAFE + 1)) == "invalid_payload"
    assert code_of(game(4), lc(1, -MAX_SAFE - 1)) == "invalid_payload"


def test_criterion_5_a_result_outside_the_safe_range_is_invalid_amount_in_both_directions() -> None:
    high = with_seat(game(4), 1, life=MAX_SAFE)
    assert code_of(high, lc(1, 1)) == "invalid_amount"
    assert code_of(high, lc(1, -1)) == "ok"
    low = with_seat(game(4), 1, life=-MAX_SAFE)
    assert code_of(low, lc(1, -1)) == "invalid_amount"
    assert code_of(low, lc(1, 1)) == "ok"


@pytest.mark.parametrize("value", ["40", None, True, False, 1.5, [40], {"life": 40}])
def test_criterion_5_life_changed_on_a_life_that_is_not_an_integer_is_invalid_amount(
    value: Any,
) -> None:
    before = with_seat(game(4), 1, life=value)
    assert code_of(before, lc(1, -3)) == "invalid_amount"
    assert code_of(before, lc(1, 3)) == "invalid_amount"


def test_criterion_5_a_concession_and_a_declaration_work_on_a_life_that_is_not_an_integer() -> None:
    before = with_seat(game(4), 1, life="40")
    assert code_of(before, concede(1)) == "ok"
    assert code_of(before, declare(1, "mill")) == "ok"


def test_criterion_5_a_guest_seat_with_no_player_concedes_too() -> None:
    state = run(lobby(), joined(1), joined(2), started(1))
    assert code_of(state, concede(2)) == "ok"


# --- criteria 6 and 7: the routine called directly ----------------------------------------------

SEQ = 21


def entry(seat: int, reason: Any = "life", by: int | None = None, cause: Any = None) -> Elimination:
    return Elimination(seat, reason, by, cause)


def table(standing: list[int]) -> GameState:
    """A 4-seat game in round 3 where the seats not in ``standing`` were eliminated earlier."""
    state = game(4).model_copy(update={"round": 3, "turn": 9, "last_seq": 20})
    for i, seat_id in enumerate(s for s in (1, 2, 3, 4) if s not in standing):
        state = out_at(state, seat_id, 6 + i, 2)
    return state


def test_criterion_6_two_seats_in_one_call_get_the_same_eliminated_at_seq() -> None:
    after = eliminate(table([1, 2, 3, 4]), SEQ, [entry(2), entry(3)])
    assert [seat_of(after, 2).eliminated_at_seq, seat_of(after, 3).eliminated_at_seq] == [SEQ, SEQ]
    assert in_game(after) == [1, 4]
    assert after.no_winner is False


def test_criterion_6_three_seats_keep_their_own_reasons_and_share_seq_and_round() -> None:
    after = eliminate(
        table([1, 2, 3, 4]),
        SEQ,
        [
            entry(1, "poison", 4),
            entry(2, "commander_damage", 3),
            entry(3, "declared", None, "combo"),
        ],
    )
    first, second, third = seat_of(after, 1), seat_of(after, 2), seat_of(after, 3)
    assert (first.elimination_reason, first.eliminated_by, first.elimination_cause) == (
        "poison",
        4,
        None,
    )
    assert (second.elimination_reason, second.eliminated_by) == ("commander_damage", 3)
    assert (third.elimination_reason, third.eliminated_by, third.elimination_cause) == (
        "declared",
        None,
        "combo",
    )
    for seat in (first, second, third):
        assert (seat.eliminated_at_seq, seat.eliminated_round) == (SEQ, 3)
    assert seat_of(after, 4).eliminated_at_seq is None


def test_criterion_7_every_seat_still_in_the_game_at_once_is_a_full_draw() -> None:
    after = eliminate(table([1, 2, 3, 4]), SEQ, [entry(1), entry(2), entry(3), entry(4)])
    assert in_game(after) == []
    assert after.no_winner is True
    assert [s.eliminated_at_seq for s in after.seats] == [SEQ, SEQ, SEQ, SEQ]


def test_criterion_7_drawing_seats_are_those_of_that_event_and_earlier_seats_keep_their_seq() -> (
    None
):
    before = table([3, 4])  # seat 1 out at 6, seat 2 out at 7
    after = eliminate(before, SEQ, [entry(3), entry(4)])
    assert after.no_winner is True
    assert [s.eliminated_at_seq for s in after.seats] == [6, 7, SEQ, SEQ]
    last = max(s.eliminated_at_seq for s in after.seats if s.eliminated_at_seq is not None)
    assert [s.seat for s in after.seats if s.eliminated_at_seq == last] == [3, 4]
    assert seat_of(after, 1) == seat_of(before, 1)
    assert seat_of(after, 2) == seat_of(before, 2)


def test_criterion_7_literal_rule_one_seat_left_and_eliminated_is_a_full_draw() -> None:
    after = eliminate(table([4]), SEQ, [entry(4, "concession")])
    assert after.no_winner is True
    assert seat_of(after, 4).eliminated_at_seq == SEQ
    assert [s.eliminated_at_seq for s in after.seats[:3]] == [6, 7, 8]


def test_a_partial_elimination_leaves_no_winner_as_it_is_true_or_false() -> None:
    assert eliminate(table([1, 2, 3, 4]), SEQ, [entry(2)]).no_winner is False
    drawn = table([1, 2, 3, 4]).model_copy(update={"no_winner": True})
    assert eliminate(drawn, SEQ, [entry(2)]).no_winner is True


def test_a_seat_named_twice_takes_its_first_entry() -> None:
    after = eliminate(table([1, 2, 3, 4]), SEQ, [entry(2, "poison", 1), entry(2, "life", 3)])
    assert (seat_of(after, 2).elimination_reason, seat_of(after, 2).eliminated_by) == ("poison", 1)
    assert in_game(after) == [1, 3, 4]


def test_a_seat_named_twice_among_the_last_two_still_gives_a_full_draw() -> None:
    assert eliminate(table([3, 4]), SEQ, [entry(3), entry(3), entry(4)]).no_winner is True


def test_an_entry_for_a_seat_not_at_the_table_or_already_eliminated_is_skipped() -> None:
    before = table([2, 3, 4])  # seat 1 out at 6
    after = eliminate(before, SEQ, [entry(5), entry(1, "poison", 2), entry(3)])
    assert seat_of(after, 1) == seat_of(before, 1)
    assert in_game(after) == [2, 4]
    assert after.no_winner is False


def test_no_entries_or_only_skipped_ones_return_the_state_as_it_is() -> None:
    before = table([1, 2])
    assert canon(eliminate(before, SEQ, [])) == canon(before)
    assert canon(eliminate(before, SEQ, [entry(5)])) == canon(before)


def test_with_nobody_standing_an_entry_is_skipped_and_no_winner_is_not_set() -> None:
    before = table([])
    assert before.no_winner is False
    assert canon(eliminate(before, SEQ, [entry(1)])) == canon(before)


def test_applying_only_some_of_the_last_two_seats_does_not_draw() -> None:
    after = eliminate(table([3, 4]), SEQ, [entry(4)])
    assert after.no_winner is False
    assert in_game(after) == [3]
    assert after.winner_seat is None


ELIMINATION_KEYS = (
    "elimination_reason",
    "eliminated_by",
    "eliminated_at_seq",
    "eliminated_round",
    "elimination_cause",
)


def test_the_routine_changes_only_the_elimination_fields_and_no_winner() -> None:
    before = table([1, 2, 3, 4]).model_copy(
        update={"monarch": 2, "initiative": 3, "commander_damage": {"2": {"1:0": 7}}}
    )
    after = eliminate(before, SEQ, [entry(1), entry(2), entry(3), entry(4)])
    blank = {"seats": [], "no_winner": False}
    assert after.model_copy(update=blank) == before.model_copy(update=blank)
    assert (after.phase, after.active_seat, after.last_seq, after.round, after.turn) == (
        "active",
        1,
        20,
        3,
        9,
    )
    assert (after.monarch, after.initiative, after.winner_seat, after.win_condition) == (
        2,
        3,
        None,
        None,
    )
    nulls = dict.fromkeys(ELIMINATION_KEYS)
    for new, old in zip(after.seats, before.seats, strict=True):
        assert new.model_copy(update=nulls) == old.model_copy(update=nulls)


def test_the_routine_does_not_mutate_its_input_and_returns_a_new_state() -> None:
    before = table([1, 2, 3, 4])
    snapshot = canon(before)
    entries = [entry(2), entry(3)]
    after = eliminate(before, SEQ, entries)
    assert after is not before
    assert canon(before) == snapshot
    assert entries == [entry(2), entry(3)]


# The worked examples of 4.5.2, parsed so that the table and the code cannot drift.
EXAMPLES: list[tuple[int, list[int], list[list[Elimination]], list[int], bool]] = [
    (1, [1, 2, 3, 4], [[entry(2)]], [2], False),
    (2, [1, 2, 3, 4], [[entry(2), entry(3)]], [2, 3], False),
    (3, [1, 2, 3, 4], [[entry(1), entry(2), entry(3), entry(4)]], [1, 2, 3, 4], True),
    (4, [3, 4], [[entry(3), entry(4)]], [3, 4], True),
    (5, [4], [[entry(4)]], [4], True),
    (6, [1, 2, 3, 4], [[entry(2, "poison"), entry(2, "life")]], [2], False),
    (7, [1, 2], [[], [entry(5)]], [], False),
    (8, [], [[entry(1)]], [], False),
]


def example_rows() -> list[list[str]]:
    text = section("#### 4.5.2 ")
    return table_rows(text[text.index("Worked examples") :])


def test_the_worked_example_table_has_the_eight_rows_these_cases_are_built_from() -> None:
    assert [int(r[0]) for r in example_rows()] == [1, 2, 3, 4, 5, 6, 7, 8]


@pytest.mark.parametrize(
    ("number", "standing", "lists", "applied", "draw"),
    EXAMPLES,
    ids=[f"row-{e[0]}" for e in EXAMPLES],
)
def test_4_5_2_example_matches_the_table(
    number: int,
    standing: list[int],
    lists: list[list[Elimination]],
    applied: list[int],
    draw: bool,
) -> None:
    row = example_rows()[number - 1]
    assert ([] if row[1] == "none" else ints(row[1])) == standing
    assert (standing if row[3].startswith("all") else ints(row[3])) == applied
    assert ("true" in row[4]) is draw
    for entries in lists:
        before = table(standing)
        after = eliminate(before, SEQ, entries)
        changed = [
            n.seat
            for n, o in zip(after.seats, before.seats, strict=True)
            if n.eliminated_at_seq != o.eliminated_at_seq
        ]
        assert changed == applied
        assert all(s.eliminated_at_seq == SEQ for s in after.seats if s.seat in applied)
        assert after.no_winner is draw


# --- criterion 7 through events -------------------------------------------------------------------


def test_with_two_seats_left_one_eliminated_nobody_wins_yet() -> None:
    state = accept(game(2), lc(2, -40, 1))
    assert state.no_winner is False
    assert in_game(state) == [1]
    assert state.winner_seat is None
    assert state.phase == "active"


def test_eliminating_the_last_seat_after_earlier_eliminations_is_a_full_draw() -> None:
    state = run(game(3), lc(1, -40), concede(2), lc(3, -40, 3))
    assert state.no_winner is True
    assert [s.eliminated_at_seq for s in state.seats] == [5, 6, 7]
    assert state.phase == "active"
    assert state.winner_seat is None


def test_the_last_seat_conceding_or_declared_out_is_a_full_draw_too() -> None:
    assert run(game(2), concede(1), concede(2)).no_winner is True
    assert run(game(2), declare(1, "mill"), declare(2)).no_winner is True


def test_after_a_full_draw_seat_naming_events_are_seat_eliminated_and_turn_passed_invalid() -> None:
    state = run(game(2), concede(1), concede(2))
    assert code_of(state, lc(1, -1)) == "seat_eliminated"
    assert code_of(state, concede(2)) == "seat_eliminated"
    assert code_of(state, declare(1)) == "seat_eliminated"
    assert code_of(state, ev("turn_passed")) == "invalid_phase"


# --- phases ---------------------------------------------------------------------------------------

THREE_EVENTS: list[Callable[[], dict[str, Any]]] = [
    lambda: lc(2, -40),
    lambda: concede(2),
    lambda: declare(2, "mill"),
]


@pytest.mark.parametrize("phase", ["lobby", "finished", "abandoned"])
def test_lobby_finished_and_abandoned_are_invalid_phase_for_the_three_events(phase: str) -> None:
    state = run(lobby(), joined(1), joined(2)) if phase == "lobby" else in_phase(game(2), phase)
    for build in THREE_EVENTS:
        assert code_of(state, build()) == "invalid_phase"


def test_nothing_changes_in_the_lobby_life_stays_the_starting_life() -> None:
    state = run(lobby(), joined(1), joined(2))
    assert reject_code(state, lc(1, -1)) == "invalid_phase"
    assert [s.life for s in state.seats] == [40, 40]


def test_active_accepts_the_three_events() -> None:
    for build in THREE_EVENTS:
        assert code_of(game(4), build()) == "ok"


def ending() -> GameState:
    return in_phase(game(4), "ending")


def test_ending_accepts_the_three_events_and_stays_in_ending() -> None:
    for build in THREE_EVENTS:
        after = accept(ending(), build())
        assert after.phase == "ending"
        assert seat_of(after, 2).eliminated_at_seq is not None


def test_a_life_loss_in_ending_records_life_reason_eliminator_and_seq_as_in_active() -> None:
    before = ending()
    seat = seat_of(accept(before, lc(2, -45, 3)), 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by, seat.eliminated_at_seq) == (
        -5,
        "life",
        3,
        before.last_seq + 1,
    )


def test_the_other_rules_hold_in_ending() -> None:
    assert code_of(out_at(ending(), 2, 5), lc(2, -1)) == "seat_eliminated"
    assert code_of(ending(), lc(5, -1)) == "unknown_seat"
    assert code_of(ending(), lc(2, 0)) == "invalid_amount"
    assert code_of(ending(), lc(2, "1")) == "invalid_payload"


def test_a_last_seat_eliminated_in_ending_is_a_full_draw_and_the_phase_stays_ending() -> None:
    state = run(in_phase(game(2), "ending"), concede(1), concede(2))
    assert (state.phase, state.no_winner) == ("ending", True)


# --- check order ----------------------------------------------------------------------------------


def test_an_eliminated_seat_with_an_invalid_amount_is_seat_eliminated() -> None:
    state = out_at(game(4), 2, 5)
    for delta in (0, 1000, -1000):
        assert code_of(state, lc(2, delta)) == "seat_eliminated"


def test_an_eliminated_source_with_an_invalid_amount_is_seat_eliminated_and_so_is_a_gain() -> None:
    state = out_at(game(4), 2, 5)
    for delta in (0, 1000, 1, -1):
        assert code_of(state, lc(1, delta, 2)) == "seat_eliminated"


def test_an_eliminated_seat_with_a_delta_of_the_wrong_type_is_invalid_payload() -> None:
    assert code_of(out_at(game(4), 2, 5), lc(2, "5")) == "invalid_payload"


def test_an_unknown_seat_with_a_wrong_phase_is_invalid_phase() -> None:
    assert code_of(in_phase(game(4), "finished"), lc(5, -1)) == "invalid_phase"
    assert code_of(in_phase(game(4), "finished"), concede(5)) == "invalid_phase"
    assert code_of(in_phase(game(4), "abandoned"), declare(5)) == "invalid_phase"


def test_an_eliminated_seat_in_a_finished_game_is_invalid_phase() -> None:
    assert code_of(in_phase(out_at(game(4), 2, 5), "finished"), lc(2, -1)) == "invalid_phase"


def test_an_unknown_seat_with_an_invalid_amount_is_unknown_seat() -> None:
    assert code_of(game(4), lc(5, 0)) == "unknown_seat"
    assert code_of(game(4), lc(1, 0, 5)) == "unknown_seat"


def test_an_unknown_source_with_an_eliminated_target_is_unknown_seat_and_target_comes_first() -> (
    None
):
    assert code_of(out_at(game(4), 2, 5), lc(2, -1, 5)) == "unknown_seat"
    assert code_of(game(4), lc(5, -1, 6)) == "unknown_seat"


def test_a_wrong_payload_with_a_wrong_phase_is_invalid_payload() -> None:
    assert code_of(in_phase(game(4), "finished"), lc(1)) == "invalid_payload"
    assert code_of(run(lobby(), joined(1)), ev("player_conceded", {})) == "invalid_payload"
    assert code_of(in_phase(game(4), "finished"), declare(1, "nope")) == "invalid_payload"


def test_a_bad_cause_with_an_unknown_seat_is_invalid_payload_and_a_good_one_is_unknown_seat() -> (
    None
):
    assert code_of(game(4), declare(6, "nope")) == "invalid_payload"
    assert code_of(game(4), declare(6, "mill")) == "unknown_seat"


def violation(code: str, handler: str, state: GameState, payload: dict[str, Any]) -> Any:
    """Make one more check fail: returns the new (state, payload)."""
    if code == "invalid_payload":
        bad_field = "delta" if handler == "life_changed" else "seat"
        return state, {**payload, bad_field: "5" if bad_field == "delta" else "2"}
    if code == "invalid_phase":
        return in_phase(state, "finished"), payload
    if code == "unknown_seat":
        unknown_field = "source_seat" if handler == "life_changed" else "seat"
        return state, {**payload, unknown_field: 5}
    if code == "seat_eliminated":
        return out_at(state, 2, 5), payload
    return state, {**payload, "delta": 0}  # invalid_amount


def order_for(handler: str) -> list[str]:
    parsed = {
        "life_changed": checks_life(),
        "player_conceded": checks_concede(),
        "player_eliminated": checks_declare(),
    }[handler]
    return list(dict.fromkeys(parsed))


@pytest.mark.parametrize("handler", ["life_changed", "player_conceded", "player_eliminated"])
def test_every_set_of_violations_gives_the_earliest_check_that_fails(handler: str) -> None:
    order = order_for(handler)
    failures: list[str] = []
    for mask in range(1, 1 << len(order)):
        picked = [code for i, code in enumerate(order) if mask & (1 << i)]
        state = game(4)
        payload: dict[str, Any] = (
            {"seat": 2, "delta": -1} if handler == "life_changed" else {"seat": 2}
        )
        # Apply the latest check's violation first, so an earlier one that overwrites it wins.
        for code in reversed(picked):
            state, payload = violation(code, handler, state, payload)
        actual = code_of(state, ev(handler, payload))
        if actual != picked[0]:
            failures.append(f"{'+'.join(picked)} gave {actual}")
    assert failures == []


# --- the 4.5.3 worked examples, row by row -----------------------------------------------------


def life_rows() -> list[list[str]]:
    return table_rows(section("#### 4.5.3 "))


def test_the_life_changed_table_has_sixteen_rows() -> None:
    assert [int(r[0]) for r in life_rows()] == list(range(1, 17))


def row_check(number: int, state: GameState) -> None:
    if number == 1:
        assert (seat_of(state, 2).life, seat_of(state, 2).eliminated_at_seq) == (35, None)
    elif number == 2:
        assert (seat_of(state, 1).life, seat_of(state, 1).eliminated_at_seq) == (50, None)
    elif number == 3:
        seat = seat_of(state, 3)
        assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (0, "life", 1)
        assert (seat.eliminated_at_seq, seat.eliminated_round) == (state.last_seq, 1)
    elif number == 4:
        assert (seat_of(state, 2).life, seat_of(state, 2).eliminated_by) == (-3, None)
    elif number == 5:
        assert (seat_of(state, 1).life, seat_of(state, 1).eliminated_at_seq) == (1, None)
    elif number == 6:
        assert (seat_of(state, 2).life, seat_of(state, 2).eliminated_by) == (0, 2)
    elif number == 7:
        assert (seat_of(state, 2).life, seat_of(state, 2).eliminated_by) == (0, None)
    elif number == 8:
        assert (seat_of(state, 1).life, seat_of(state, 1).eliminated_at_seq) == (-2, None)
    elif number == 9:
        assert (seat_of(state, 1).life, seat_of(state, 1).elimination_reason) == (-6, "life")
    else:
        assert number == 16
        assert seat_of(state, 3).eliminated_at_seq == state.last_seq
        assert state.no_winner is True


# (row, code or None, state builder, events)
ROW_SPECS: list[tuple[int, str | None, Any, list[dict[str, Any]]]] = [
    (1, None, lambda: game(4), [lc(2, -5)]),
    (2, None, lambda: game(4), [lc(1, 10, 2)]),
    (3, None, lambda: game(4, 3), [lc(3, -3, 1)]),
    (4, None, lambda: game(4, 5), [lc(2, -8)]),
    (5, None, lambda: game(4, 5), [lc(1, -4)]),
    (6, None, lambda: game(4), [lc(2, -40, 2)]),
    (7, None, lambda: game(4), [lc(2, -40, None)]),
    (8, None, lambda: game(4, -5), [lc(1, 3)]),
    (9, None, lambda: game(4, -5), [lc(1, -1)]),
    (10, "invalid_amount", lambda: game(4), [lc(1, 0), lc(1, 1000), lc(1, -1000)]),
    (
        11,
        "invalid_payload",
        lambda: game(4),
        [lc(1, "3"), lc(1, True), lc(1, None), lc(1, 1.5), lc(1)],
    ),
    (12, "invalid_amount", lambda: game(4, "40"), [lc(1, -3)]),
    (13, "invalid_amount", lambda: game(4, MAX_SAFE), [lc(1, 1)]),
    (14, "seat_eliminated", lambda: out_at(game(4), 2, 5), [lc(2, 0), lc(1, -1, 2)]),
    (15, "invalid_phase", lambda: run(lobby(), joined(1), joined(2)), [lc(1, -1)]),
    (16, None, lambda: run(game(3), concede(1), concede(2)), [lc(3, -40)]),
]


@pytest.mark.parametrize(
    ("number", "code", "build", "events"), ROW_SPECS, ids=[f"row-{r[0]}" for r in ROW_SPECS]
)
def test_4_5_3_example_row(
    number: int, code: str | None, build: Any, events: list[dict[str, Any]]
) -> None:
    result = life_rows()[number - 1][3]
    if code is not None:
        assert f"`{code}`" in result
        for event in events:
            assert code_of(build(), event) == code
    else:
        assert re.search(r"`(invalid_[a-z]+|seat_eliminated|unknown_seat)`", result) is None
        for event in events:
            row_check(number, accept(build(), event))


# --- purity ---------------------------------------------------------------------------------------

PURITY_EVENTS = [
    lc(2, -40, 1),
    lc(2, -3),
    lc(1, 5, 2),
    concede(3),
    declare(3, "mill"),
    declare(3),
    lc(2, 0),
    concede(5),
]


@pytest.mark.parametrize(
    "event", PURITY_EVENTS, ids=[f"event-{i}" for i in range(len(PURITY_EVENTS))]
)
def test_neither_the_state_nor_the_event_is_mutated_accepted_or_rejected(
    event: dict[str, Any],
) -> None:
    state = game(4)
    snapshot = canon(state)
    sent = copy.deepcopy(event)
    result = apply(state, sent)
    assert canon(state) == snapshot
    assert sent == event
    if not isinstance(result, ApplyError):
        assert result.state is not state


def test_the_same_event_on_the_same_state_gives_the_same_result_twice() -> None:
    state = game(4)
    first, second = apply(state, lc(2, -40, 1)), apply(state, lc(2, -40, 1))
    assert not isinstance(first, ApplyError)
    assert not isinstance(second, ApplyError)
    assert canon(first.state) == canon(second.state)


def test_a_successful_elimination_does_not_alias_the_seats_of_the_input() -> None:
    state = game(4)
    after = accept(state, lc(2, -40, 1))
    assert after.seats is not state.seats
    assert seat_of(state, 2).eliminated_at_seq is None


# --- the oracle -----------------------------------------------------------------------------------

CASES = 6000
PHASES = ["active", "active", "ending", "lobby", "finished", "abandoned"]
TARGETS: list[Any] = [2, 2, 2, 2, 1, 3, 4, 0, 9, "2", None]
SOURCES: list[Any] = [ABSENT, ABSENT, None, 1, 2, 3, 3, 4, 9, "x"]
DELTAS: list[Any] = [
    -MAX_SAFE, -1000, -999, -998, -41, -40, -39, -5, -2, -1, -1, 0, 1, 1, 2, 5, 998, 999, 1000,
    MAX_SAFE, "5", None, True, 1.5, ABSENT,
]  # fmt: skip
LIVES: list[Any] = [
    -MAX_SAFE, -MAX_SAFE + 1, -1000, -999, -5, -1, 0, 1, 2, 5, 40, 40, 40, 998, 999, 1000,
    MAX_SAFE - 999, MAX_SAFE - 1, MAX_SAFE, "40", None, True,
]  # fmt: skip


class Generator:
    """Park-Miller: the same numbers in the TypeScript twin, so both run the same cases."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def next(self) -> int:
        self.seed = (self.seed * 48271) % 2147483647
        return self.seed

    def pick(self, items: list[Any]) -> Any:
        return items[self.next() % len(items)]


def build_case(gen: Generator) -> tuple[GameState, dict[str, Any], str]:
    phase = gen.pick(PHASES)
    target = gen.pick(TARGETS)
    out1 = gen.next() % 4 == 0
    out2 = gen.next() % 3 == 0
    seat3 = gen.pick(["alive", "alive", "out", "absent"])
    life = gen.pick(LIVES)
    source = gen.pick(SOURCES)
    delta = gen.pick(DELTAS)
    state = game(3)
    if seat3 == "absent":
        state = state.model_copy(update={"seats": state.seats[:2], "order": [1, 2]})
    if out1:
        state = out_at(state, 1, 3)
    if out2:
        state = out_at(state, 2, 3)
    if seat3 == "out":
        state = out_at(state, 3, 3)
    if isinstance(target, int) and any(s.seat == target for s in state.seats):
        state = with_seat(state, target, life=life)
    state = in_phase(state, phase)
    payload: dict[str, Any] = {"seat": target}
    if delta is not ABSENT:
        payload["delta"] = delta
    if source is not ABSENT:
        payload["source_seat"] = source
    return (
        state,
        payload,
        f"{phase} seat={target!r} out={out1}/{out2}/{seat3} life={life!r} {payload!r}",
    )


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and abs(value) <= MAX_SAFE


def is_seat_id(value: Any) -> bool:
    return is_int(value) and 1 <= value <= 8


def given(value: Any) -> bool:
    return value is not None


def oracle(state: GameState, payload: dict[str, Any]) -> str | dict[str, Any]:
    """The expected outcome, from the contract's words: an error code, or the expected state."""
    plain = copy.deepcopy(state.to_json_value())
    seat: Any = payload.get("seat")
    delta: Any = payload.get("delta")
    source: Any = payload.get("source_seat")
    if not is_seat_id(seat) or not is_int(delta):
        return "invalid_payload"
    if given(source) and not is_seat_id(source):
        return "invalid_payload"
    if plain["phase"] not in ("active", "ending"):
        return "invalid_phase"
    seats = {s["seat"]: s for s in plain["seats"]}
    if seat not in seats or (given(source) and source not in seats):
        return "unknown_seat"
    if seats[seat]["eliminated_at_seq"] is not None or (
        given(source) and seats[source]["eliminated_at_seq"] is not None
    ):
        return "seat_eliminated"
    if delta == 0 or abs(delta) > 999:
        return "invalid_amount"
    target = seats[seat]
    if not is_int(target["life"]) or abs(target["life"] + delta) > MAX_SAFE:
        return "invalid_amount"
    life = target["life"] + delta
    target["life"] = life
    if delta < 0 and life <= 0:
        standing = [s for s in plain["seats"] if s["eliminated_at_seq"] is None]
        target["elimination_reason"] = "life"
        target["eliminated_by"] = source if given(source) else None
        target["eliminated_at_seq"] = plain["last_seq"] + 1
        target["eliminated_round"] = plain["round"]
        target["elimination_cause"] = None
        if len(standing) == 1:
            plain["no_winner"] = True
    plain["last_seq"] += 1
    return plain


def test_the_reducer_agrees_with_the_oracle_on_6000_generated_cases() -> None:
    gen = Generator(613)
    seen: dict[str, int] = {}
    mismatches: list[str] = []
    for _ in range(CASES):
        state, payload, label = build_case(gen)
        expected = oracle(state, payload)
        result = apply(state, ev("life_changed", payload))
        actual = str(result.error.code) if isinstance(result, ApplyError) else canon(result.state)
        want = expected if isinstance(expected, str) else canonical(expected)
        kind = expected if isinstance(expected, str) else "ok"
        if not isinstance(expected, str):
            hit_seat = next(s for s in expected["seats"] if s["seat"] == payload["seat"])
            if hit_seat["eliminated_at_seq"] == expected["last_seq"]:
                kind = "ok_full_draw" if expected["no_winner"] else "ok_eliminated"
        seen[kind] = seen.get(kind, 0) + 1
        if actual != want and len(mismatches) < 5:
            mismatches.append(f"{label}\n  want {want}\n  got  {actual}")
    assert mismatches == []
    for kind in (
        "invalid_payload",
        "invalid_phase",
        "unknown_seat",
        "seat_eliminated",
        "invalid_amount",
        "ok",
        "ok_eliminated",
        "ok_full_draw",
    ):
        assert seen.get(kind, 0) > 0, f"the generator never produced {kind}"


# --- vector names match their content (8.6) ---------------------------------------------------

VECTOR_NAME = re.compile(r"^(accept|reject)-(life-changed|player-conceded|player-eliminated)-")


def vector_files() -> list[str]:
    if not VECTORS_DIR.is_dir():
        return []
    return sorted(p.name for p in VECTORS_DIR.glob("*.json") if VECTOR_NAME.match(p.name))


def test_there_are_at_least_14_vectors_for_the_three_types() -> None:
    require(VECTORS_DIR)
    assert len(vector_files()) >= 14


@pytest.mark.parametrize("file", vector_files())
def test_a_vector_is_named_after_the_type_and_the_outcome_it_has(file: str) -> None:
    match = VECTOR_NAME.match(file)
    assert match is not None
    event_type = match.group(2).replace("-", "_")
    vector = json.loads((VECTORS_DIR / file).read_text(encoding="utf-8"))
    if match.group(1) == "accept":
        assert "state" in vector["expect"]
        assert any(e["type"] == event_type for e in vector["events"])
    else:
        assert "error" in vector["expect"]
        assert vector["events"][vector["expect"]["error"]["index"]]["type"] == event_type
