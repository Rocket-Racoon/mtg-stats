"""Acceptance tests of S-6.1.5 (poison, player counters, commander cast, monarch and initiative, and
the hand-off of the two crowns), the Python twin of
``packages/game-core/src/acceptance-s615.test.ts``: same cases, same numbers.

Written from the four acceptance criteria of the story (issue #209) and CONTRACT.md 3.1 to 3.3,
4.1, 4.2, 4.4.7, 4.4.8, 4.4.10, 4.5.1, 4.5.2, 4.6, 4.7, 5, 6 and 8, not from the handlers. The
shared vectors (``test_vectors.py``) are the primary proof for everything reachable through events.
This file adds the criteria with their own numbers, built through real events from ``game_created``,
the check lists and example tables parsed from the contract (so the document and the code cannot
drift), the phases no log reaches yet, purity, the "only what this event raised" rule on hand-built
states, and an oracle written from the contract's words over seeded generated cases.
"""

from __future__ import annotations

import copy
import functools
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from app.games.core import (
    REDUCER_ERROR_CODES,
    ApplyError,
    GameState,
    apply,
    initial_state,
)
from app.games.core import canonical_json as canonical
from app.games.core.contract import Commander
from app.games.core.elimination import Elimination, eliminate
from app.games.tests.lobby_helpers import GAME_ID, accept, lobby, make_event
from app.games.tests.repo_files import VECTORS_DIR, contract_text, require

MAX_SAFE = 9007199254740991
ABSENT: Any = object()  # a payload key that is not written at all
ELIMINATION_REASONS = ("life", "commander_damage", "poison", "concession", "declared")
Event = dict[str, Any]


def ev(event_type: str, payload: dict[str, Any] | None = None) -> Event:
    return make_event(event_type, payload)


def pc(seat: Any, delta: Any, source: Any = ABSENT) -> Event:
    """``poison_changed {seat, delta, source_seat?}``."""
    payload: dict[str, Any] = {"seat": seat, "delta": delta}
    if source is not ABSENT:
        payload["source_seat"] = source
    return ev("poison_changed", payload)


def cc(seat: Any, counter: Any, delta: Any) -> Event:
    """``counter_changed {seat, counter, delta}``."""
    return ev("counter_changed", {"seat": seat, "counter": counter, "delta": delta})


def cast(seat: Any, commander: Any, delta: Any) -> Event:
    """``commander_cast {seat, commander, delta}``."""
    return ev("commander_cast", {"seat": seat, "commander": commander, "delta": delta})


def monarch_set(seat: Any) -> Event:
    return ev("monarch_set", {"seat": seat})


def initiative_set(seat: Any) -> Event:
    return ev("initiative_set", {"seat": seat})


def lc(seat: int, delta: int, source: Any = ABSENT) -> Event:
    payload: dict[str, Any] = {"seat": seat, "delta": delta}
    if source is not ABSENT:
        payload["source_seat"] = source
    return ev("life_changed", payload)


def concede(seat: int) -> Event:
    return ev("player_conceded", {"seat": seat})


def declare(seat: int, cause: Any = ABSENT) -> Event:
    payload: dict[str, Any] = {"seat": seat}
    if cause is not ABSENT:
        payload["cause"] = cause
    return ev("player_eliminated", payload)


def cd(source: int, commander: int, target: int, amount: int) -> Event:
    return ev(
        "commander_damage",
        {"source_seat": source, "commander": commander, "target_seat": target, "amount": amount},
    )


def turn() -> Event:
    return ev("turn_passed")


def joined(seat: int) -> Event:
    return ev("seat_joined", {"seat": seat, "player_id": None, "name": f"Seat {seat}"})


def started(first: int = 1) -> Event:
    return ev("game_started", {"first_seat": first, "started_at": "2026-11-21T12:00:00.000Z"})


def moved(from_seat: int, to_seat: int, correction: Any = ABSENT) -> Event:
    payload: dict[str, Any] = {"from": from_seat, "to": to_seat}
    if correction is not ABSENT:
        payload["correction"] = correction
    return ev("seat_moved", payload)


def hold_event(key: str) -> Callable[[Any], Event]:
    return monarch_set if key == "monarch" else initiative_set


PARTNER_A = {"card_id": None, "name": "Partner A"}
PARTNER_B = {"card_id": "018f0000-0000-7000-8000-0000000000e2", "name": "Partner B"}
PARTNERS = [PARTNER_A, PARTNER_B]
SOLO = [{"card_id": None, "name": ""}]
MONARCH_KEYS = ("monarch", "initiative")


def run(state: GameState, *events: Event) -> GameState:
    for event in events:
        state = accept(state, event)
    return state


def lobby_log(
    *,
    count: int = 4,
    poison_threshold: int = 10,
    life: int = 40,
    partners: tuple[int, ...] = (),
    decks: tuple[int, ...] = (),
) -> list[Event]:
    """The lobby of a game as a real log: ``game_created``, the seats, the commanders, no start."""
    settings = {
        "starting_life": life,
        "commander_damage_threshold": 21,
        "poison_threshold": poison_threshold,
        "trust_mode": "casual",
    }
    return [
        ev("game_created", {"settings": settings}),
        *(joined(i) for i in range(1, count + 1)),
        *(ev("commanders_set", {"seat": seat, "commanders": PARTNERS}) for seat in partners),
        *(
            ev(
                "deck_assigned",
                {"seat": seat, "deck_version_id": f"deck-{seat}", "commanders": PARTNERS},
            )
            for seat in decks
        ),
    ]


def lobby_of(**opts: Any) -> GameState:
    return run(lobby(), *lobby_log(**opts))


def game(*, first: int = 1, **opts: Any) -> GameState:
    """A started game built from ``game_created``, seat 1 first unless ``first`` says otherwise."""
    return run(lobby_of(**opts), started(first))


def raw_game(settings: dict[str, Any], count: int = 4) -> GameState:
    """The same game from the placeholder settings of ``initial_state`` (not validated)."""
    state = initial_state(GAME_ID, settings)
    return run(state, *(joined(i) for i in range(1, count + 1)), started(1))


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


def with_settings(state: GameState, **changes: Any) -> GameState:
    return state.model_copy(update={"settings": state.settings.model_copy(update=changes)})


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


def plain(state: GameState) -> dict[str, Any]:
    return copy.deepcopy(state.to_json_value())


def without(state: GameState, *keys: str) -> str:
    """The canonical state without the keys listed: compares "everything else"."""
    loaded = json.loads(canon(state))
    for key in keys:
        loaded.pop(key, None)
    return json.dumps(loaded, sort_keys=True)


def code_of(state: GameState, event: Event) -> str:
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
    """The first reducer error code in each numbered item of a text, in order."""
    out: list[str] = []
    for item in re.finditer(r"^\d+\. (.*)$", text, flags=re.MULTILINE):
        tokens = re.findall(r"`([a-z_]+)`", item.group(1))
        code = next((t for t in tokens if t in REDUCER_ERROR_CODES), None)
        if code is not None:
            out.append(code)
    return out


def table_rows(text: str) -> list[list[str]]:
    """The rows of the tables of a text whose first cell is a number, cells trimmed."""
    return [
        [cell.strip() for cell in line.split("|")[1:-1]]
        for line in text.split("\n")
        if re.match(r"^\| \d+ +\|", line)
    ]


@functools.cache
def checks_471() -> list[str]:
    return numbered_codes(section("#### 4.7.1 "))


@functools.cache
def checks_472() -> list[str]:
    return numbered_codes(section("#### 4.7.2 "))


@functools.cache
def checks_473() -> list[str]:
    text = section("#### 4.7.3 ")
    return numbered_codes(text[: text.index("Why check 5 comes before check 6")])


@functools.cache
def checks_474() -> list[str]:
    return numbered_codes(section("#### 4.7.4 "))


def rows(heading: str) -> list[list[str]]:
    return table_rows(section(heading))


# --- the contract text ----------------------------------------------------------------------------

SEVEN = ["invalid_payload", "invalid_phase", "unknown_seat", "seat_eliminated"]


def test_4_7_1_lists_six_checks_shape_phase_seat_eliminated_amount_numbers() -> None:
    assert checks_471() == [*SEVEN, "invalid_amount", "invalid_amount"]


def test_4_7_2_lists_the_same_six_checks() -> None:
    assert checks_472() == checks_471()


def test_4_7_3_lists_seven_checks_the_shared_four_then_amount_commander_numbers() -> None:
    assert checks_473() == [*SEVEN, "invalid_amount", "unknown_commander", "invalid_amount"]


def test_4_7_4_lists_four_checks_and_4_7_5_says_its_checks_1_to_4_are_the_same() -> None:
    assert checks_474() == SEVEN
    assert "the checks 1 to 4 are the same" in section("#### 4.7.5 ")


def test_every_list_of_4_7_starts_with_the_order_of_4_5_1() -> None:
    shared = numbered_codes(section("#### 4.5.1 "))[:4]
    assert shared == SEVEN
    for listed in (checks_471(), checks_472(), checks_473(), checks_474()):
        assert listed[:4] == shared


def test_4_7_gives_the_examples_these_tests_are_built_from_16_9_10_and_13_rows() -> None:
    for heading, count in (
        ("#### 4.7.1 ", 16),
        ("#### 4.7.2 ", 9),
        ("#### 4.7.3 ", 10),
        ("#### 4.7.6 ", 13),
    ):
        assert [int(r[0]) for r in rows(heading)] == list(range(1, count + 1))


def test_the_registry_rows_of_the_five_types_give_the_payload_keys_of_the_story() -> None:
    def row(event_type: str) -> str:
        return next(
            ln
            for ln in contract_text().split("\n")
            if ln.startswith(f"| `{event_type}`") and "S-6.1.5" in ln
        )

    assert "`seat`, `delta`, `source_seat?`" in row("poison_changed")
    assert "`seat`, `counter` (`energy` or `experience`), `delta`" in row("counter_changed")
    assert "`seat`, `commander` (index), `delta`" in row("commander_cast")
    assert "| `seat`" in row("monarch_set")
    assert "| `seat`" in row("initiative_set")


def test_4_5_2_gives_the_precedence_commander_damage_poison_life_and_4_7_1_uses_poison_alone() -> (
    None
):
    bullet = next(
        ln for ln in section("#### 4.5.2 ").split("\n") if ln.startswith("- **Reason precedence")
    )
    tail = bullet[bullet.index("in the order") + len("in the order") :]
    order = re.findall(r"`([a-z_]+)`", tail[: tail.index(".")])
    assert order == ["commander_damage", "poison", "life"]
    assert all(r in ELIMINATION_REASONS for r in order)
    assert "the entry `(seat, poison, by, null)`" in section("#### 4.7.1 ")


def test_3_2_gives_poison_threshold_the_range_1_to_99_and_the_default_10() -> None:
    row = next(
        ln for ln in section("### 3.2 ").split("\n") if ln.startswith("| `poison_threshold`")
    )
    cells = [c.strip() for c in row.split("|")]
    assert (cells[3], cells[4]) == ("1 to 99", "10")
    assert lobby().settings.poison_threshold == 10


def test_4_5_2_has_a_step_4_for_the_hand_off_with_three_outcomes_null_active_seat_next_seat() -> (
    None
):
    text = section("#### 4.5.2 ")
    start = text.index("4. **Monarch and initiative hand-off**")
    step = text[start : text.index("The routine changes nothing else")]
    items = re.findall(r"^\s+\d\. (.*)$", step, flags=re.MULTILINE)
    assert len(items) == 3
    assert "`null`" in items[0]
    assert "the `active_seat`" in items[1]
    assert "**next seat**" in items[2]


# --- criterion 1 ----------------------------------------------------------------------------------


def nine(**opts: Any) -> GameState:
    """Seat 2 raised to 9 poison by an event, as the criterion has it."""
    return run(game(**opts), pc(2, 9))


def test_criterion_1_seat_2_is_at_9_poison_after_an_event_of_plus_9_in_the_game() -> None:
    seat = seat_of(nine(), 2)
    assert (seat.poison, seat.eliminated_at_seq) == (9, None)


def test_criterion_1_plus_1_from_seat_4_gives_10_poison_and_eliminates_by_poison_by_4() -> None:
    before = nine()
    after = run(before, pc(2, 1, 4))
    seat = seat_of(after, 2)
    assert seat.poison == 10
    assert seat.elimination_reason == "poison"
    assert seat.eliminated_by == 4
    assert seat.eliminated_at_seq == before.last_seq + 1
    assert seat.eliminated_round == 1
    assert seat.elimination_cause is None
    assert seat.life == 40
    assert after.last_seq == before.last_seq + 1


def test_criterion_1_the_other_seats_the_active_seat_turn_and_round_are_untouched() -> None:
    before = nine()
    after = run(before, pc(2, 1, 4))
    for seat_id in (1, 3, 4):
        assert seat_of(after, seat_id) == seat_of(before, seat_id)
    assert in_game(after) == [1, 3, 4]
    assert (after.active_seat, after.turn, after.round) == (1, 1, 1)
    assert (after.no_winner, after.winner_seat, after.win_condition, after.phase) == (
        False,
        None,
        None,
        "active",
    )


def test_criterion_1_eliminated_at_seq_is_the_seq_the_event_carries_when_assigned() -> None:
    before = nine()
    seq = before.last_seq + 1
    event = make_event("poison_changed", {"seat": 2, "delta": 1, "source_seat": 4}, seq=seq)
    assert seat_of(run(before, event), 2).eliminated_at_seq == seq


def test_criterion_1_the_same_numbers_from_the_default_settings_of_initial_state() -> None:
    after = run(raw_game({}), pc(2, 9), pc(2, 1, 4))
    seat = seat_of(after, 2)
    assert (seat.poison, seat.elimination_reason, seat.eliminated_by) == (10, "poison", 4)


def test_criterion_1_with_the_default_threshold_9_stays_and_10_eliminates_one_at_a_time() -> None:
    state = game()
    assert state.settings.poison_threshold == 10
    for n in range(1, 10):
        state = run(state, pc(2, 1))
        assert (seat_of(state, 2).poison, seat_of(state, 2).eliminated_at_seq) == (n, None)
    state = run(state, pc(2, 1, 4))
    seat = seat_of(state, 2)
    assert (seat.poison, seat.elimination_reason, seat.eliminated_by) == (10, "poison", 4)


def via_game_created() -> GameState:
    return game(poison_threshold=15)


def via_settings_changed() -> GameState:
    return run(lobby_of(), ev("settings_changed", {"poison_threshold": 15}), started(1))


def via_initial_state() -> GameState:
    return raw_game({"poison_threshold": 15})


@pytest.mark.parametrize(
    "build",
    [via_game_created, via_settings_changed, via_initial_state],
    ids=["game_created", "settings_changed", "initial_state"],
)
def test_criterion_1_threshold_15_10_to_14_do_not_eliminate_and_15_does(
    build: Callable[[], GameState],
) -> None:
    state = build()
    assert state.settings.poison_threshold == 15
    state = run(state, pc(2, 9))
    for n in range(10, 15):
        state = run(state, pc(2, 1, 4))
        assert (seat_of(state, 2).poison, seat_of(state, 2).eliminated_at_seq) == (n, None)
    after = run(state, pc(2, 1, 4))
    seat = seat_of(after, 2)
    assert (seat.poison, seat.elimination_reason, seat.eliminated_by) == (15, "poison", 4)
    assert seat.eliminated_at_seq == after.last_seq


def test_criterion_1_threshold_15_10_poison_in_one_event_stays_and_15_in_one_event_eliminates() -> (
    None
):
    assert seat_of(run(via_game_created(), pc(2, 10)), 2).eliminated_at_seq is None
    assert seat_of(run(via_game_created(), pc(2, 15)), 2).elimination_reason == "poison"


def test_criterion_1_threshold_1_first_counter_eliminates_threshold_99_needs_99() -> None:
    first = seat_of(run(game(poison_threshold=1), pc(2, 1, 3)), 2)
    assert (first.poison, first.elimination_reason, first.eliminated_by) == (1, "poison", 3)
    held = run(game(poison_threshold=99), pc(2, 98))
    assert seat_of(held, 2).eliminated_at_seq is None
    last = seat_of(run(held, pc(2, 1)), 2)
    assert (last.poison, last.elimination_reason) == (99, "poison")


def settings_with(value: Any) -> dict[str, Any]:
    return {
        "starting_life": 40,
        "commander_damage_threshold": 21,
        "poison_threshold": value,
        "trust_mode": "casual",
    }


@pytest.mark.parametrize("value", [1, 10, 15, 99])
def test_criterion_1_game_created_and_settings_changed_accept_the_poison_threshold(
    value: int,
) -> None:
    assert code_of(lobby(), ev("game_created", {"settings": settings_with(value)})) == "ok"
    assert code_of(lobby(), ev("settings_changed", {"poison_threshold": value})) == "ok"


@pytest.mark.parametrize("value", [0, 100, -1, 101, 1000, MAX_SAFE, "10", True, None])
def test_criterion_1_the_poison_threshold_outside_1_to_99_is_invalid_setting(value: Any) -> None:
    created = ev("game_created", {"settings": settings_with(value)})
    assert code_of(lobby(), created) == "invalid_setting"
    assert (
        code_of(lobby(), ev("settings_changed", {"poison_threshold": value})) == "invalid_setting"
    )


def test_criterion_1_a_rejected_threshold_leaves_the_settings_as_they_were() -> None:
    state = lobby_of(poison_threshold=15)
    assert code_of(state, ev("settings_changed", {"poison_threshold": 100})) == "invalid_setting"
    assert code_of(state, ev("settings_changed", {"poison_threshold": 0})) == "invalid_setting"
    assert state.settings.poison_threshold == 15


def test_criterion_1_the_threshold_cannot_change_once_the_game_started() -> None:
    assert code_of(game(), ev("settings_changed", {"poison_threshold": 15})) == "invalid_phase"


def test_criterion_1_an_integer_threshold_outside_1_to_99_in_an_unvalidated_state_is_used() -> None:
    zero = seat_of(run(raw_game({"poison_threshold": 0}), pc(2, 1)), 2)
    assert (zero.poison, zero.elimination_reason) == (1, "poison")
    huge = seat_of(run(raw_game({"poison_threshold": 5000}), pc(2, 999), pc(2, 999)), 2)
    assert (huge.poison, huge.eliminated_at_seq) == (1998, None)


FIELDS = ("poison", "energy", "experience")


def bump(field: str, seat: int, delta: int) -> Event:
    return pc(seat, delta) if field == "poison" else cc(seat, field, delta)


@pytest.mark.parametrize("field", FIELDS)
def test_criterion_1_at_exactly_0_a_change_of_minus_1_is_invalid_amount(field: str) -> None:
    state = game()
    assert code_of(state, bump(field, 2, -1)) == "invalid_amount"
    assert code_of(state, bump(field, 2, -999)) == "invalid_amount"


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(("start", "delta"), [(1, -2), (2, -3), (5, -6), (9, -10), (3, -999)])
def test_criterion_1_below_0_from_a_small_positive_value_is_invalid_amount_and_changes_nothing(
    field: str, start: int, delta: int
) -> None:
    state = run(game(), bump(field, 2, start))
    snapshot = canon(state)
    assert code_of(state, bump(field, 2, delta)) == "invalid_amount"
    assert canon(state) == snapshot
    assert getattr(seat_of(state, 2), field) == start


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(("start", "delta"), [(1, -1), (2, -2), (5, -5), (9, -9)])
def test_criterion_1_down_to_exactly_0_is_allowed(field: str, start: int, delta: int) -> None:
    state = run(game(), bump(field, 2, start), bump(field, 2, delta))
    assert getattr(seat_of(state, 2), field) == 0
    assert seat_of(state, 2).eliminated_at_seq is None


@pytest.mark.parametrize("field", FIELDS)
def test_criterion_1_a_rejected_change_leaves_last_seq_alone(field: str) -> None:
    state = run(game(), bump(field, 3, 4))
    assert isinstance(apply(state, bump(field, 2, -1)), ApplyError)
    assert state.last_seq == game().last_seq + 1


@pytest.mark.parametrize("delta", [0, 1000, -1000, MAX_SAFE, -MAX_SAFE])
def test_criterion_1_a_poison_delta_of_0_or_over_999_is_invalid_amount(delta: int) -> None:
    assert code_of(game(), pc(2, delta)) == "invalid_amount"


@pytest.mark.parametrize("delta", [1, 2, 998, 999])
def test_criterion_1_a_poison_delta_up_to_999_is_accepted(delta: int) -> None:
    assert code_of(game(poison_threshold=99), pc(2, delta)) == "ok"


def test_criterion_1_poison_deltas_of_999_and_minus_999_give_the_exact_totals() -> None:
    up = run(raw_game({"poison_threshold": 5000}), pc(2, 999))
    assert seat_of(up, 2).poison == 999
    assert seat_of(run(up, pc(2, -999)), 2).poison == 0


@pytest.mark.parametrize("delta", ["1", True, False, None, [], {}, 1.5, -0.5])
def test_criterion_1_a_poison_delta_of_the_wrong_type_is_invalid_payload(delta: Any) -> None:
    assert code_of(game(), pc(2, delta)) == "invalid_payload"


def test_criterion_1_a_poison_delta_outside_the_safe_range_is_invalid_payload() -> None:
    assert code_of(game(), pc(2, MAX_SAFE + 1)) == "invalid_payload"
    assert code_of(game(), pc(2, -MAX_SAFE - 1)) == "invalid_payload"


def test_criterion_1_a_counter_delta_of_0_1000_and_minus_1000_is_invalid_amount_999_is_fine() -> (
    None
):
    for delta in (0, 1000, -1000):
        assert code_of(game(), cc(2, "energy", delta)) == "invalid_amount"
    assert code_of(game(), cc(2, "experience", 999)) == "ok"


def test_criterion_1_poison_changes_only_the_poison_and_last_seq_below_the_threshold() -> None:
    before = game(partners=(2,))
    after = run(before, pc(2, 3, 4))
    expected = plain(before)
    next(s for s in expected["seats"] if s["seat"] == 2)["poison"] = 3
    expected["last_seq"] += 1
    assert canon(after) == canonical(expected)


# --- criterion 2 ----------------------------------------------------------------------------------

ASSIGNMENTS = [("commanders_set", {"partners": (2,)}), ("deck_assigned", {"decks": (2,)})]


def tax(state: GameState, seat_id: int, index: int) -> int:
    return 2 * int(seat_of(state, seat_id).commander_cast[index])


def commanders_of(state: GameState, seat_id: int) -> list[dict[str, Any]]:
    return [{"card_id": c.card_id, "name": c.name} for c in seat_of(state, seat_id).commanders]


@pytest.mark.parametrize(("how", "opts"), ASSIGNMENTS)
def test_criterion_2_seat_2_has_partners_a_and_b_with_cast_counts_0_0(
    how: str, opts: dict[str, Any]
) -> None:
    state = game(**opts)
    assert commanders_of(state, 2) == PARTNERS, how
    assert list(seat_of(state, 2).commander_cast) == [0, 0]


@pytest.mark.parametrize(("how", "opts"), ASSIGNMENTS)
def test_criterion_2_casting_b_twice_gives_b_a_count_of_2_and_a_tax_of_4_and_a_stays_0(
    how: str, opts: dict[str, Any]
) -> None:
    before = game(**opts)
    once = run(before, cast(2, 1, 1))
    assert list(seat_of(once, 2).commander_cast) == [0, 1]
    assert tax(once, 2, 1) == 2
    twice = run(once, cast(2, 1, 1))
    assert list(seat_of(twice, 2).commander_cast) == [0, 2]
    assert tax(twice, 2, 1) == 4, how
    assert seat_of(twice, 2).commander_cast[0] == 0
    assert tax(twice, 2, 0) == 0
    assert twice.last_seq == before.last_seq + 2


def test_criterion_2_the_tax_is_derived_no_key_of_the_state_holds_it() -> None:
    state = run(game(partners=(2,)), cast(2, 1, 1), cast(2, 1, 1))
    assert "tax" not in canon(state)
    assert sorted(json.loads(canon(state))["seats"][1]) == sorted(
        [
            "commander_cast",
            "commanders",
            "deck_version_id",
            "eliminated_at_seq",
            "eliminated_by",
            "eliminated_round",
            "elimination_cause",
            "elimination_reason",
            "energy",
            "experience",
            "life",
            "name",
            "player_id",
            "poison",
            "seat",
        ]
    )


def test_criterion_2_casting_a_and_b_in_turn_counts_them_separately_tax_is_2_times_count() -> None:
    state = run(
        game(partners=(2,)),
        cast(2, 0, 1),
        cast(2, 1, 1),
        cast(2, 1, 1),
        cast(2, 0, 1),
        cast(2, 1, 1),
    )
    assert list(seat_of(state, 2).commander_cast) == [2, 3]
    assert [tax(state, 2, 0), tax(state, 2, 1)] == [4, 6]


def test_criterion_2_a_delta_of_minus_1_is_allowed_down_to_a_count_of_0() -> None:
    base = run(game(partners=(2,)), cast(2, 1, 1), cast(2, 1, 1))
    one = run(base, cast(2, 1, -1))
    assert list(seat_of(one, 2).commander_cast) == [0, 1]
    zero = run(one, cast(2, 1, -1))
    assert list(seat_of(zero, 2).commander_cast) == [0, 0]
    assert tax(zero, 2, 1) == 0


def test_criterion_2_a_delta_of_minus_1_at_a_count_of_0_is_invalid_amount_and_changes_nothing() -> (
    None
):
    state = game(partners=(2,))
    snapshot = canon(state)
    assert code_of(state, cast(2, 1, -1)) == "invalid_amount"
    assert code_of(state, cast(2, 0, -1)) == "invalid_amount"
    assert canon(state) == snapshot


def test_criterion_2_a_going_to_0_does_not_touch_b() -> None:
    state = run(game(partners=(2,)), cast(2, 0, 1), cast(2, 1, 1), cast(2, 0, -1))
    assert list(seat_of(state, 2).commander_cast) == [0, 1]


@pytest.mark.parametrize("delta", [0, 2, -2, 1000, -1000, MAX_SAFE])
def test_criterion_2_a_cast_delta_other_than_1_and_minus_1_is_invalid_amount(delta: int) -> None:
    assert code_of(game(partners=(2,)), cast(2, 0, delta)) == "invalid_amount"


@pytest.mark.parametrize("delta", ["1", True, None, [], {}, 1.5])
def test_criterion_2_a_cast_delta_of_the_wrong_type_is_invalid_payload(delta: Any) -> None:
    assert code_of(game(partners=(2,)), cast(2, 0, delta)) == "invalid_payload"


def test_criterion_2_a_seat_with_the_unnamed_default_commander_has_index_0_only() -> None:
    state = game()
    assert commanders_of(state, 3) == SOLO
    assert list(seat_of(run(state, cast(3, 0, 1)), 3).commander_cast) == [1]
    assert code_of(state, cast(3, 1, 1)) == "unknown_commander"


@pytest.mark.parametrize("index", [2, 3, -1, 1000000, MAX_SAFE])
def test_criterion_2_a_commander_index_a_partner_seat_does_not_have_is_unknown_commander(
    index: int,
) -> None:
    assert code_of(game(partners=(2,)), cast(2, index, 1)) == "unknown_commander"


def test_criterion_2_the_index_is_checked_against_the_seat_that_casts() -> None:
    state = game(partners=(2,))
    assert code_of(state, cast(2, 1, 1)) == "ok"
    assert code_of(state, cast(3, 1, 1)) == "unknown_commander"


def test_criterion_2_the_delta_is_judged_before_the_index_and_the_index_before_the_count() -> None:
    state = game()
    assert code_of(state, cast(3, 5, 2)) == "invalid_amount"
    assert code_of(state, cast(3, 1, -1)) == "unknown_commander"


def test_criterion_2_seats_count_their_casts_independently() -> None:
    state = run(game(), cast(1, 0, 1), cast(4, 0, 1), cast(4, 0, 1))
    assert [list(s.commander_cast) for s in state.seats] == [[1], [0], [0], [2]]


def test_criterion_2_a_cast_changes_only_the_count_and_last_seq() -> None:
    before = run(game(partners=(2,)), cd(1, 0, 2, 5), pc(2, 3))
    after = run(before, cast(2, 0, 1))
    expected = plain(before)
    next(s for s in expected["seats"] if s["seat"] == 2)["commander_cast"] = [1, 0]
    expected["last_seq"] += 1
    assert canon(after) == canonical(expected)


def test_criterion_2_commanders_replaced_in_the_lobby_reset_the_counts_to_zeros() -> None:
    lobby_state = run(
        lobby_of(partners=(2,)), ev("commanders_set", {"seat": 2, "commanders": SOLO})
    )
    state = run(lobby_state, started(1))
    assert list(seat_of(state, 2).commander_cast) == [0]
    assert code_of(state, cast(2, 1, 1)) == "unknown_commander"


# --- criterion 3 ----------------------------------------------------------------------------------

HOLD = ["monarch", "initiative"]


def test_criterion_3_nobody_holds_either_at_the_start_of_a_game() -> None:
    state = game()
    assert (state.monarch, state.initiative) == (None, None)


def test_criterion_3_seat_1_is_the_monarch_then_seat_3_is_the_only_monarch_nothing_else_moved() -> (
    None
):
    before = run(game(), monarch_set(1))
    assert before.monarch == 1
    after = run(before, monarch_set(3))
    assert after.monarch == 3
    assert after.last_seq == before.last_seq + 1
    assert without(after, "monarch", "last_seq") == without(before, "monarch", "last_seq")
    assert after.initiative is None


def test_criterion_3_initiative_set_works_the_same_way() -> None:
    before = run(game(), initiative_set(1))
    after = run(before, initiative_set(3))
    assert (before.initiative, after.initiative) == (1, 3)
    assert without(after, "initiative", "last_seq") == without(before, "initiative", "last_seq")
    assert after.monarch is None


@pytest.mark.parametrize("key", HOLD)
def test_criterion_3_one_key_holds_one_seat_id(key: str) -> None:
    set_ = hold_event(key)
    state = run(game(), set_(1), set_(2), set_(4), set_(3))
    assert getattr(state, key) == 3


@pytest.mark.parametrize(
    ("before", "key", "to", "expected"),
    [
        ((1, 1), "monarch", 3, (3, 1)),
        ((1, 1), "initiative", 3, (1, 3)),
        ((1, 2), "monarch", 2, (2, 2)),
        ((1, 2), "monarch", 3, (3, 2)),
        ((1, 2), "initiative", 1, (1, 1)),
        ((1, 2), "initiative", 3, (1, 3)),
    ],
)
def test_criterion_3_all_combinations_of_who_holds_what_and_which_one_moves(
    before: tuple[int, int], key: str, to: int, expected: tuple[int, int]
) -> None:
    start = run(game(), monarch_set(before[0]), initiative_set(before[1]))
    assert (start.monarch, start.initiative) == before
    after = run(start, hold_event(key)(to))
    assert (after.monarch, after.initiative) == expected


def test_criterion_3_one_seat_can_hold_both_and_the_two_move_independently() -> None:
    both = run(game(), monarch_set(2), initiative_set(2))
    assert (both.monarch, both.initiative) == (2, 2)
    monarch_moved = run(both, monarch_set(4))
    assert (monarch_moved.monarch, monarch_moved.initiative) == (4, 2)
    initiative_moved = run(both, initiative_set(4))
    assert (initiative_moved.monarch, initiative_moved.initiative) == (2, 4)


@pytest.mark.parametrize("key", HOLD)
def test_criterion_3_naming_the_current_holder_again_changes_nothing_but_last_seq(key: str) -> None:
    set_ = hold_event(key)
    before = run(game(), set_(2))
    after = run(before, set_(2))
    assert getattr(after, key) == 2
    assert after.last_seq == before.last_seq + 1
    assert without(after, "last_seq") == without(before, "last_seq")


@pytest.mark.parametrize("key", HOLD)
def test_criterion_3_setting_a_holder_does_not_move_the_active_seat_turn_or_round(key: str) -> None:
    before = run(game(), turn(), turn())
    after = run(before, hold_event(key)(4))
    assert (after.active_seat, after.turn, after.round, after.order) == (
        before.active_seat,
        before.turn,
        before.round,
        before.order,
    )


@pytest.mark.parametrize("key", HOLD)
def test_criterion_3_there_is_no_event_to_clear_a_holder(key: str) -> None:
    state = run(game(), hold_event(key)(2))
    assert code_of(state, hold_event(key)(None)) == "invalid_payload"
    assert code_of(state, ev(f"{key}_set", {})) == "invalid_payload"


@pytest.mark.parametrize("key", HOLD)
def test_criterion_3_a_seat_that_is_not_an_integer_from_1_to_8_is_invalid_payload(key: str) -> None:
    candidates: list[Any] = ["2", True, 0, 9, -1, 1.5, []]
    for seat in candidates:
        assert code_of(game(), hold_event(key)(seat)) == "invalid_payload", repr(seat)


@pytest.mark.parametrize("key", HOLD)
def test_criterion_3_a_seat_id_that_is_not_at_the_table_is_unknown_seat(key: str) -> None:
    assert code_of(game(count=3), hold_event(key)(4)) == "unknown_seat"
    assert code_of(game(), hold_event(key)(8)) == "unknown_seat"


@pytest.mark.parametrize("key", HOLD)
def test_criterion_3_an_eliminated_seat_cannot_be_named_nor_the_one_that_held_it(key: str) -> None:
    set_ = hold_event(key)
    assert code_of(run(game(), concede(2)), set_(2)) == "seat_eliminated"
    lost = run(game(), set_(3), concede(3))
    assert getattr(lost, key) == 1
    assert code_of(lost, set_(3)) == "seat_eliminated"


def test_criterion_3_the_monarch_can_be_named_again_after_the_crown_moved_on() -> None:
    assert run(game(), monarch_set(3), concede(3), monarch_set(2)).monarch == 2


def test_criterion_3_the_two_keys_are_unaffected_by_every_other_counter_event() -> None:
    state = run(game(partners=(2,)), monarch_set(2), initiative_set(3))
    after = run(state, pc(2, 2), cc(2, "energy", 1), cast(2, 1, 1), lc(2, -5), turn())
    assert (after.monarch, after.initiative) == (2, 3)


# --- criterion 4 ----------------------------------------------------------------------------------


def source_for(state: GameState, seat: int) -> int:
    return next(
        i for i in (4, 3, 2, 1) if i != seat and seat_of(state, i).eliminated_at_seq is None
    )


@dataclass(frozen=True)
class Killer:
    name: str
    reason: str
    events: Callable[[int, int], list[Event]]
    by: Callable[[int], int | None]


# Every eliminating event that exists: life_changed to 0, player_conceded, player_eliminated,
# commander_damage by tally and by life, and poison_changed.
KILLERS = [
    Killer("life_changed to 0", "life", lambda s, src: [lc(s, -40, src)], lambda src: src),
    Killer("player_conceded", "concession", lambda s, src: [concede(s)], lambda src: None),
    Killer("player_eliminated", "declared", lambda s, src: [declare(s, "mill")], lambda src: None),
    Killer(
        "commander_damage by tally",
        "commander_damage",
        lambda s, src: [cd(src, 0, s, 21)],
        lambda src: src,
    ),
    Killer(
        "commander_damage by life",
        "life",
        lambda s, src: [lc(s, -35), cd(src, 0, s, 5)],
        lambda src: src,
    ),
    Killer("poison_changed", "poison", lambda s, src: [pc(s, 10, src)], lambda src: src),
]


@dataclass(frozen=True)
class Scenario:
    name: str
    build: Callable[[Callable[[int], Event]], GameState]
    seat: int
    expected: int | None
    self_source: bool = False


SCENARIOS = [
    Scenario(
        "seat 3 holds it and seat 1 is the active player: seat 1 takes it (criterion 4)",
        lambda hold: run(game(), hold(3)),
        3,
        1,
    ),
    Scenario(
        "the holder is the active player: the next seat in turn order takes it",
        lambda hold: run(game(first=3), hold(3)),
        3,
        4,
    ),
    Scenario(
        "the holder is the active player at the end of order: the scan wraps to the start",
        lambda hold: run(game(first=4), hold(4)),
        4,
        1,
    ),
    Scenario(
        "the next seat skips a seat eliminated earlier",
        lambda hold: run(game(), concede(2), hold(1)),
        1,
        3,
    ),
    Scenario(
        "the wrap-around skips a seat eliminated earlier",
        lambda hold: run(game(first=4), concede(1), hold(4)),
        4,
        2,
    ),
    Scenario(
        "the active seat was eliminated earlier (still active): the next seat after it takes it",
        lambda hold: run(game(), concede(1), hold(3)),
        3,
        2,
    ),
    Scenario(
        "a seat_moved correction changed order to [3,2,1,4]: the new order is read",
        lambda hold: run(game(), moved(1, 3, True), hold(1)),
        1,
        4,
    ),
    Scenario(
        "after the correction the standing active seat takes it wherever it sits in order",
        lambda hold: run(game(), moved(1, 3, True), hold(4)),
        4,
        1,
    ),
    Scenario(
        "after turn_passed the new active player takes it",
        lambda hold: run(game(), hold(3), turn()),
        3,
        2,
    ),
    Scenario(
        "in round 2 the active seat is seat 1 again and takes it from seat 3",
        lambda hold: run(game(), turn(), turn(), turn(), turn(), hold(3)),
        3,
        1,
    ),
    Scenario(
        "the last seat in the game is eliminated: nobody holds it",
        lambda hold: run(game(count=2), concede(1), hold(2)),
        2,
        None,
        self_source=True,
    ),
    Scenario(
        "a holder that is not the one eliminated keeps it",
        lambda hold: run(game(), hold(2)),
        3,
        2,
    ),
]

CASES = [
    pytest.param(key, killer, scenario, id=f"{key}-{killer.name.replace(' ', '_')}-s{index}")
    for index, scenario in enumerate(SCENARIOS)
    for key in MONARCH_KEYS
    for killer in KILLERS
]


@pytest.mark.parametrize(("key", "killer", "scenario"), CASES)
def test_criterion_4_the_hand_off_for_every_eliminating_event_and_scenario(
    key: str, killer: Killer, scenario: Scenario
) -> None:
    before = scenario.build(hold_event(key))
    source = scenario.seat if scenario.self_source else source_for(before, scenario.seat)
    after = run(before, *killer.events(scenario.seat, source))
    assert getattr(after, key) == scenario.expected, scenario.name
    other = "initiative" if key == "monarch" else "monarch"
    assert getattr(after, other) is None
    if scenario.expected is not None:
        assert seat_of(after, scenario.expected).eliminated_at_seq is None
    target = seat_of(after, scenario.seat)
    assert target.elimination_reason == killer.reason
    assert target.eliminated_by == killer.by(source)
    assert target.eliminated_at_seq == after.last_seq
    assert target.eliminated_round == before.round
    # Nothing else moved: not the phase, the active seat, the turn, the round, the order, the seats.
    skipped = ("seats", "monarch", "initiative", "last_seq", "no_winner", "commander_damage")
    assert without(after, *skipped) == without(before, *skipped)
    for old in before.seats:
        if old.seat != scenario.seat:
            assert seat_of(after, old.seat) == old
    assert after.no_winner is scenario.self_source


def kill(killer: Killer, state: GameState, seat: int) -> GameState:
    return run(state, *killer.events(seat, source_for(state, seat)))


@pytest.mark.parametrize("killer", KILLERS, ids=[k.name.replace(" ", "_") for k in KILLERS])
class TestBothKeysAtOnce:
    def test_one_seat_holds_both_and_both_pass_to_the_active_seat_together(
        self, killer: Killer
    ) -> None:
        after = kill(killer, run(game(), monarch_set(3), initiative_set(3)), 3)
        assert (after.monarch, after.initiative) == (1, 1)

    def test_one_seat_holds_both_and_is_the_active_player_both_go_to_the_next_seat(
        self, killer: Killer
    ) -> None:
        after = kill(killer, run(game(first=3), monarch_set(3), initiative_set(3)), 3)
        assert (after.monarch, after.initiative) == (4, 4)

    def test_monarch_3_initiative_4_eliminating_seat_3_moves_only_the_monarch(
        self, killer: Killer
    ) -> None:
        after = kill(killer, run(game(), monarch_set(3), initiative_set(4)), 3)
        assert (after.monarch, after.initiative) == (1, 4)

    def test_monarch_3_initiative_4_eliminating_seat_4_moves_only_the_initiative(
        self, killer: Killer
    ) -> None:
        after = kill(killer, run(game(), monarch_set(3), initiative_set(4)), 4)
        assert (after.monarch, after.initiative) == (3, 1)

    def test_monarch_2_initiative_4_eliminating_seat_3_changes_neither(
        self, killer: Killer
    ) -> None:
        after = kill(killer, run(game(), monarch_set(2), initiative_set(4)), 3)
        assert (after.monarch, after.initiative) == (2, 4)

    def test_nobody_holds_either_they_stay_null(self, killer: Killer) -> None:
        after = kill(killer, game(), 3)
        assert (after.monarch, after.initiative) == (None, None)

    def test_both_held_by_the_last_seat_which_is_eliminated_both_become_null(
        self, killer: Killer
    ) -> None:
        before = run(game(count=2), concede(1), monarch_set(2), initiative_set(2))
        after = run(before, *killer.events(2, 2))
        assert (after.monarch, after.initiative) == (None, None)
        assert after.no_winner is True


def test_criterion_4_as_worded_seat_3_monarch_seat_1_active_seat_3_eliminated_seat_1_monarch() -> (
    None
):
    before = run(game(), monarch_set(3))
    assert (before.monarch, before.active_seat) == (3, 1)
    after = run(before, lc(3, -40, 2))
    assert seat_of(after, 3).eliminated_at_seq is not None
    assert after.monarch == 1


def test_criterion_4_second_sentence_the_monarch_is_the_active_player_next_seat_takes_it() -> None:
    before = run(game(first=3), monarch_set(3), initiative_set(3))
    assert before.active_seat == 3
    after = run(before, concede(3))
    assert (after.monarch, after.initiative) == (4, 4)
    assert after.active_seat == 3


def test_criterion_4_then_turn_passed_and_further_events_behave() -> None:
    state = run(game(), monarch_set(3), concede(3))
    assert state.monarch == 1
    state = run(state, concede(1))
    assert (state.monarch, state.active_seat) == (2, 1)
    state = run(state, turn())
    assert (state.active_seat, state.turn, state.monarch) == (2, 2, 2)
    state = run(state, concede(2))
    assert (state.monarch, state.active_seat) == (4, 2)
    state = run(state, turn())
    assert (state.active_seat, state.turn, state.round, state.monarch) == (4, 3, 1, 4)
    assert code_of(state, monarch_set(3)) == "seat_eliminated"
    assert run(state, monarch_set(4)).monarch == 4
    last = run(state, concede(4))
    assert last.monarch is None
    assert last.no_winner is True


def test_criterion_4_the_hand_off_hands_the_crown_on_again_each_time() -> None:
    state = run(game(), monarch_set(3), concede(3), concede(1), concede(2))
    assert state.monarch == 4
    assert in_game(state) == [4]


def test_criterion_4_the_holder_is_eliminated_while_the_active_seat_holds_the_other_crown() -> None:
    after = run(game(), initiative_set(1), monarch_set(3), concede(3))
    assert (after.monarch, after.initiative) == (1, 1)


def test_criterion_4_a_monarch_set_after_the_hand_off_then_an_elimination_hands_off_again() -> None:
    state = run(game(), monarch_set(3), concede(3), monarch_set(2), lc(2, -40, 4))
    assert state.monarch == 1


def test_criterion_4_a_seat_moved_correction_changes_who_receives_it_not_who_holds_it() -> None:
    state = run(game(), monarch_set(1), moved(1, 3, True))
    assert state.monarch == 1
    assert state.order == [3, 2, 1, 4]
    assert run(state, concede(1)).monarch == 4


def life_entry(seat: int) -> Elimination:
    return Elimination(seat, "life", None, None)


def routine_base() -> GameState:
    return run(game(), monarch_set(2), initiative_set(3))


def test_criterion_4_the_routine_with_seats_1_active_2_and_3_out_together_gives_4_and_4() -> None:
    after = eliminate(routine_base(), 20, [life_entry(1), life_entry(2), life_entry(3)])
    assert (after.monarch, after.initiative) == (4, 4)


def test_criterion_4_the_routine_with_every_seat_out_gives_null_and_null() -> None:
    after = eliminate(routine_base(), 20, [life_entry(i) for i in (1, 2, 3, 4)])
    assert (after.monarch, after.initiative) == (None, None)
    assert after.no_winner is True


def test_criterion_4_the_routine_does_not_hand_off_twice_for_repeated_or_eliminated_entries() -> (
    None
):
    state = out_at(routine_base(), 4, 3)
    after = eliminate(state, 20, [life_entry(2), life_entry(2), life_entry(4)])
    assert (after.monarch, after.initiative) == (1, 3)


def test_criterion_4_the_routine_that_eliminates_nobody_leaves_the_crowns() -> None:
    after = eliminate(routine_base(), 20, [])
    assert (after.monarch, after.initiative) == (2, 3)


# --- check order ----------------------------------------------------------------------------------

Ctx = tuple[GameState, dict[str, Any]]
Maker = Callable[[GameState, dict[str, Any]], Ctx]


def _payload(**changes: Any) -> Maker:
    return lambda state, payload: (state, {**payload, **changes})


def _state(change: Callable[[GameState], GameState]) -> Maker:
    return lambda state, payload: (change(state), payload)


def check_order_failures(
    event_type: str, codes: list[str], makers: list[Maker], base: Callable[[], Ctx]
) -> list[str]:
    """Every set of simultaneous violations must give the earliest check of the list that fails.

    The violation of the latest check is applied first, so an earlier one that overwrites the same
    field wins.
    """
    failures: list[str] = []
    for mask in range(1, 1 << len(codes)):
        picked = [i for i in range(len(codes)) if mask & (1 << i)]
        state, payload = base()
        for i in reversed(picked):
            state, payload = makers[i](state, payload)
        actual = code_of(state, ev(event_type, payload))
        if actual != codes[picked[0]]:
            failures.append(f"{'+'.join(str(i + 1) for i in picked)} gave {actual}")
    return failures


def single_violations(
    event_type: str, codes: list[str], makers: list[Maker], base: Callable[[], Ctx]
) -> list[str]:
    state, payload = base()
    got = [code_of(state, ev(event_type, payload))]
    for maker in makers:
        state, payload = maker(*base())
        got.append(code_of(state, ev(event_type, payload)))
    return [] if got == ["ok", *codes] else [f"expected ok then {codes}, got {got}"]


def poison_base() -> Ctx:
    return game(), {"seat": 3, "delta": 5, "source_seat": 2}


POISON_MAKERS: list[Maker] = [
    _payload(delta="5"),
    _state(lambda s: in_phase(s, "finished")),
    _payload(source_seat=7),
    _state(lambda s: out_at(s, 3, 5)),
    _payload(delta=0),
    _state(lambda s: with_seat(s, 3, poison="3")),
]


def counter_base() -> Ctx:
    return game(), {"seat": 3, "counter": "energy", "delta": 5}


COUNTER_MAKERS: list[Maker] = [
    _payload(counter="poison"),
    _state(lambda s: in_phase(s, "abandoned")),
    _payload(seat=7),
    _state(lambda s: out_at(s, 3, 5)),
    _payload(delta=1000),
    _state(lambda s: with_seat(s, 3, energy="5")),
]


def cast_base() -> Ctx:
    return game(partners=(2,)), {"seat": 2, "commander": 0, "delta": 1}


CAST_MAKERS: list[Maker] = [
    _payload(delta="1"),
    _state(lambda s: in_phase(s, "lobby")),
    _payload(seat=7),
    _state(lambda s: out_at(s, 2, 5)),
    _payload(delta=2),
    _payload(commander=5),
    _state(lambda s: with_seat(s, 2, commander_cast=["x", 0])),
]


def holder_base() -> Ctx:
    return game(), {"seat": 3}


HOLDER_MAKERS: list[Maker] = [
    _payload(seat="3"),
    _state(lambda s: in_phase(s, "finished")),
    _payload(seat=7),
    _state(lambda s: out_at(s, 3, 5)),
]

SUITES: list[tuple[str, Callable[[], list[str]], list[Maker], Callable[[], Ctx]]] = [
    ("poison_changed", checks_471, POISON_MAKERS, poison_base),
    ("counter_changed", checks_472, COUNTER_MAKERS, counter_base),
    ("commander_cast", checks_473, CAST_MAKERS, cast_base),
    ("monarch_set", checks_474, HOLDER_MAKERS, holder_base),
    ("initiative_set", checks_474, HOLDER_MAKERS, holder_base),
]
SUITE_IDS = [s[0] for s in SUITES]


@pytest.mark.parametrize(("event_type", "codes", "makers", "base"), SUITES, ids=SUITE_IDS)
def test_check_order_has_one_violation_maker_per_listed_check(
    event_type: str,
    codes: Callable[[], list[str]],
    makers: list[Maker],
    base: Callable[[], Ctx],
) -> None:
    assert len(makers) == len(codes()), event_type


@pytest.mark.parametrize(("event_type", "codes", "makers", "base"), SUITES, ids=SUITE_IDS)
def test_check_order_every_set_of_simultaneous_violations_gives_the_earliest_check(
    event_type: str,
    codes: Callable[[], list[str]],
    makers: list[Maker],
    base: Callable[[], Ctx],
) -> None:
    assert check_order_failures(event_type, codes(), makers, base) == []


@pytest.mark.parametrize(("event_type", "codes", "makers", "base"), SUITES, ids=SUITE_IDS)
def test_check_order_each_single_violation_gives_its_own_code_and_none_is_accepted(
    event_type: str,
    codes: Callable[[], list[str]],
    makers: list[Maker],
    base: Callable[[], Ctx],
) -> None:
    got = single_violations(event_type, codes(), makers, base)
    assert got == []


def test_check_order_a_bad_payload_in_a_closed_game_is_invalid_payload_an_unknown_seat_is_not() -> (
    None
):
    closed = in_phase(game(), "finished")
    assert code_of(closed, pc(2, "x")) == "invalid_payload"
    assert code_of(closed, pc(7, 1)) == "invalid_phase"
    assert code_of(closed, monarch_set(None)) == "invalid_payload"
    assert code_of(closed, monarch_set(7)) == "invalid_phase"


def test_check_order_seat_eliminated_covers_the_source_seat_whatever_the_sign_of_the_delta() -> (
    None
):
    state = run(game(), concede(3))
    assert code_of(state, pc(2, 1, 3)) == "seat_eliminated"
    assert code_of(state, pc(2, -1, 3)) == "seat_eliminated"
    assert code_of(state, pc(2, 0, 3)) == "seat_eliminated"


def test_check_order_unknown_seat_names_the_seat_before_the_source() -> None:
    state = game(count=3)
    assert code_of(state, pc(5, 1, 6)) == "unknown_seat"
    assert code_of(state, pc(1, 1, 6)) == "unknown_seat"


def test_check_order_a_rejected_event_changes_nothing_the_state_is_the_same_bytes() -> None:
    state = run(game(partners=(2,)), monarch_set(3), pc(2, 3))
    snapshot = canon(state)
    bad = [
        pc(2, 0),
        pc(2, -4),
        pc(7, 1),
        cc(2, "energy", -1),
        cc(2, "poison", 1),
        cast(2, 1, -1),
        cast(2, 5, 1),
        monarch_set(None),
        initiative_set(7),
    ]
    for event in bad:
        assert isinstance(apply(state, event), ApplyError)
        assert canon(state) == snapshot


# --- the worked examples of 4.7 -------------------------------------------------------------------

CODE_IN_CELL = re.compile(r"`(invalid_[a-z]+|seat_eliminated|unknown_[a-z]+)`")
Bad = list[tuple[GameState, Event]]


@dataclass(frozen=True)
class Spec:
    mentions: tuple[int, ...] = ()
    code: str | None = None
    bad: Callable[[], Bad] | None = None
    check: Callable[[], None] | None = None


def verify(heading: str, specs: dict[int, Spec], number: int) -> None:
    spec = specs[number]
    result = rows(heading)[number - 1][3]
    for m in spec.mentions:
        assert re.search(rf"(^|[^0-9])-?{m}([^0-9]|$)", result), (number, m)
    if spec.code is not None:
        assert f"`{spec.code}`" in result
        assert spec.bad is not None
        for state, event in spec.bad():
            assert code_of(state, event) == spec.code
    else:
        assert CODE_IN_CELL.search(result) is None
        assert spec.check is not None
        spec.check()


def is_out(state: GameState, seat_id: int, reason: str, by: int | None) -> None:
    seat = seat_of(state, seat_id)
    assert (seat.elimination_reason, seat.eliminated_by) == (reason, by)


def stays_in(state: GameState, seat_id: int) -> None:
    assert seat_of(state, seat_id).eliminated_at_seq is None


def check_471_1() -> None:
    before = run(game(), pc(2, 9))
    s = run(before, pc(2, 1, 4))
    seat = seat_of(s, 2)
    assert (seat.poison, seat.elimination_reason, seat.eliminated_by) == (10, "poison", 4)
    assert (seat.eliminated_at_seq, seat.eliminated_round) == (before.last_seq + 1, 1)


def check_471_2() -> None:
    at_14 = run(game(poison_threshold=15), pc(2, 14))
    stays_in(at_14, 2)
    s = run(at_14, pc(2, 1))
    assert seat_of(s, 2).poison == 15
    is_out(s, 2, "poison", None)


def check_471_3() -> None:
    s = run(game(), pc(2, 12))
    assert seat_of(s, 2).poison == 12
    is_out(s, 2, "poison", None)


def check_471_4() -> None:
    s = run(game(), pc(2, 4), pc(2, -3, 1))
    assert seat_of(s, 2).poison == 1
    stays_in(s, 2)


def check_471_7() -> None:
    s = run(with_seat(game(), 2, poison=12), pc(2, -1))
    assert seat_of(s, 2).poison == 11
    stays_in(s, 2)


def check_471_8() -> None:
    s = run(with_seat(game(), 2, poison=12), pc(2, 1))
    assert seat_of(s, 2).poison == 13
    is_out(s, 2, "poison", None)


def check_471_9() -> None:
    s = run(game(), pc(3, 10, 3))
    assert seat_of(s, 3).poison == 10
    is_out(s, 3, "poison", 3)


def check_471_14() -> None:
    s = run(raw_game({"poison_threshold": 0}), pc(2, 1))
    assert seat_of(s, 2).poison == 1
    is_out(s, 2, "poison", None)


SPECS_471: dict[int, Spec] = {
    1: Spec(mentions=(10, 4, 1), check=check_471_1),
    2: Spec(mentions=(15, 14), check=check_471_2),
    3: Spec(mentions=(12,), check=check_471_3),
    4: Spec(mentions=(1,), check=check_471_4),
    5: Spec(code="invalid_amount", bad=lambda: [(run(game(), pc(2, 2)), pc(2, -3))]),
    6: Spec(code="invalid_amount", bad=lambda: [(game(), pc(2, -1))]),
    7: Spec(mentions=(11,), check=check_471_7),
    8: Spec(mentions=(13,), check=check_471_8),
    9: Spec(mentions=(10, 3), check=check_471_9),
    10: Spec(code="invalid_amount", bad=lambda: [(game(), pc(2, d)) for d in (0, 1000, -1000)]),
    11: Spec(
        code="invalid_payload",
        bad=lambda: [
            *[(game(), pc(2, d)) for d in ("1", True, None, 1.5)],
            (game(), ev("poison_changed", {"seat": 2})),
        ],
    ),
    12: Spec(
        code="seat_eliminated",
        bad=lambda: [
            (run(game(), concede(2)), pc(2, 0)),
            (run(game(), concede(2)), pc(1, -1, 2)),
        ],
    ),
    13: Spec(
        code="invalid_amount",
        bad=lambda: [(raw_game({"poison_threshold": "10"}), pc(2, d)) for d in (1, -1)],
    ),
    14: Spec(mentions=(1,), check=check_471_14),
    15: Spec(
        code="invalid_amount",
        bad=lambda: [(with_seat(game(), 2, poison=MAX_SAFE), pc(2, 1))],
    ),
    16: Spec(code="invalid_phase", bad=lambda: [(lobby_of(), pc(2, 1))]),
}


def test_4_7_1_has_a_spec_for_each_of_the_sixteen_rows() -> None:
    assert sorted(SPECS_471) == list(range(1, 17))


@pytest.mark.parametrize("number", range(1, 17))
def test_4_7_1_worked_example_row(number: int) -> None:
    verify("#### 4.7.1 ", SPECS_471, number)


def check_472_1() -> None:
    s = run(game(), cc(2, "energy", 3))
    assert (seat_of(s, 2).energy, seat_of(s, 2).experience) == (3, 0)


def check_472_2() -> None:
    assert seat_of(run(game(), cc(2, "energy", 4), cc(2, "energy", -1)), 2).energy == 3


def check_472_3() -> None:
    s = run(game(), cc(3, "experience", 2), cc(3, "experience", -2))
    assert seat_of(s, 3).experience == 0


SPECS_472: dict[int, Spec] = {
    1: Spec(mentions=(3,), check=check_472_1),
    2: Spec(mentions=(3,), check=check_472_2),
    3: Spec(mentions=(0,), check=check_472_3),
    4: Spec(
        code="invalid_amount",
        bad=lambda: [(run(game(), cc(2, "energy", 1)), cc(2, "energy", -2))],
    ),
    5: Spec(
        code="invalid_amount",
        bad=lambda: [(game(), cc(2, "energy", d)) for d in (0, 1000)],
    ),
    6: Spec(code="invalid_payload", bad=lambda: [(game(), cc(2, "poison", 1))]),
    7: Spec(
        code="seat_eliminated",
        bad=lambda: [(run(game(), concede(2)), cc(2, "energy", 0))],
    ),
    8: Spec(
        code="invalid_amount",
        bad=lambda: [(with_seat(game(), 2, energy=MAX_SAFE), cc(2, "energy", 1))],
    ),
    9: Spec(
        code="invalid_amount",
        bad=lambda: [(with_seat(game(), 2, energy="3"), cc(2, "energy", 1))],
    ),
}


def test_4_7_2_has_a_spec_for_each_of_the_nine_rows() -> None:
    assert sorted(SPECS_472) == list(range(1, 10))


@pytest.mark.parametrize("number", range(1, 10))
def test_4_7_2_worked_example_row(number: int) -> None:
    verify("#### 4.7.2 ", SPECS_472, number)


def check_473_1() -> None:
    s1 = run(game(partners=(2,)), cast(2, 1, 1))
    s2 = run(s1, cast(2, 1, 1))
    assert list(seat_of(s1, 2).commander_cast) == [0, 1]
    assert list(seat_of(s2, 2).commander_cast) == [0, 2]
    assert tax(s2, 2, 1) == 4
    assert tax(s2, 2, 0) == 0


def check_473_2() -> None:
    base = run(game(partners=(2,)), cast(2, 1, 1), cast(2, 1, 1))
    assert list(seat_of(base, 2).commander_cast) == [0, 2]
    s1 = run(base, cast(2, 1, -1))
    s2 = run(s1, cast(2, 1, -1))
    assert list(seat_of(s1, 2).commander_cast) == [0, 1]
    assert list(seat_of(s2, 2).commander_cast) == [0, 0]


def check_473_4() -> None:
    assert list(seat_of(run(game(), cast(3, 0, 1)), 3).commander_cast) == [1]


SPECS_473: dict[int, Spec] = {
    1: Spec(mentions=(0, 1, 2, 4), check=check_473_1),
    2: Spec(mentions=(0, 1), check=check_473_2),
    3: Spec(code="invalid_amount", bad=lambda: [(game(partners=(2,)), cast(2, 1, -1))]),
    4: Spec(mentions=(1,), check=check_473_4),
    5: Spec(
        code="invalid_amount",
        bad=lambda: [(game(partners=(2,)), cast(2, 0, d)) for d in (0, 2, -2)],
    ),
    6: Spec(code="unknown_commander", bad=lambda: [(game(), cast(3, 1, 1))]),
    7: Spec(code="invalid_amount", bad=lambda: [(game(), cast(3, 5, 2))]),
    8: Spec(code="unknown_commander", bad=lambda: [(game(), cast(3, 1, -1))]),
    9: Spec(code="seat_eliminated", bad=lambda: [(run(game(), concede(2)), cast(2, 9, 0))]),
    10: Spec(
        code="invalid_payload",
        bad=lambda: [
            (game(), cast(2, "0", 1)),
            (game(), ev("commander_cast", {"seat": 2, "commander": 0})),
        ],
    ),
}


def test_4_7_3_has_a_spec_for_each_of_the_ten_rows() -> None:
    assert sorted(SPECS_473) == list(range(1, 11))


@pytest.mark.parametrize("number", range(1, 11))
def test_4_7_3_worked_example_row(number: int) -> None:
    verify("#### 4.7.3 ", SPECS_473, number)


def expected_pair(number: int) -> tuple[int | None, int | None]:
    """The two values the "after" cell starts with: ``null`` as None, a missing second as None."""
    cell = rows("#### 4.7.6 ")[number - 1][3]
    found = re.match(r"^((?:\d+|`null`)(?:, (?:\d+|`null`))?)", cell)
    assert found is not None
    tokens = found.group(1).split(", ")

    def value(token: str | None) -> int | None:
        return None if token is None or token == "`null`" else int(token)

    return value(tokens[0]), value(tokens[1] if len(tokens) > 1 else None)


SEAT_KILLERS: list[tuple[str, Callable[[int, int], list[Event]]]] = [
    ("life_changed", lambda seat, src: [lc(seat, -40, src)]),
    ("player_conceded", lambda seat, src: [concede(seat)]),
    ("player_eliminated", lambda seat, src: [declare(seat)]),
    ("commander_damage by tally", lambda seat, src: [cd(src, 0, seat, 21)]),
    ("commander_damage by life", lambda seat, src: [lc(seat, -35), cd(src, 0, seat, 5)]),
    ("poison_changed", lambda seat, src: [pc(seat, 10, src)]),
]


def row_9_before() -> GameState:
    return run(game(), moved(1, 3, True), monarch_set(1))


def row_12_before() -> GameState:
    return out_at(game(), 2, 3).model_copy(update={"monarch": 2})


def row_13_before() -> GameState:
    return run(game(), monarch_set(2)).model_copy(
        update={"order": [4, 3, 2, 1], "active_seat": None}
    )


# row -> (the state before the elimination, the seat that goes out, the source seat)
ROW_SETUPS: dict[int, tuple[Callable[[], GameState], int, int]] = {
    1: (lambda: run(game(), monarch_set(3)), 3, 2),
    2: (lambda: run(game(first=3), monarch_set(3), initiative_set(3)), 3, 2),
    3: (lambda: run(game(first=4), monarch_set(4)), 4, 2),
    4: (lambda: run(game(), concede(2), monarch_set(1)), 1, 3),
    5: (lambda: run(game(), concede(1), monarch_set(3)), 3, 2),
    6: (lambda: run(game(), monarch_set(2), initiative_set(4)), 3, 1),
    7: (lambda: run(game(), monarch_set(3), initiative_set(4)), 3, 1),
    8: (lambda: run(game(), monarch_set(3), initiative_set(3)), 3, 1),
    9: (row_9_before, 1, 2),
    12: (row_12_before, 4, 3),
    13: (row_13_before, 2, 1),
}


@pytest.mark.parametrize("number", sorted(ROW_SETUPS))
@pytest.mark.parametrize(("name", "events"), SEAT_KILLERS, ids=[k[0] for k in SEAT_KILLERS])
def test_4_7_6_worked_example_row_with_each_eliminating_event(
    number: int, name: str, events: Callable[[int, int], list[Event]]
) -> None:
    build, seat, source = ROW_SETUPS[number]
    after = run(build(), *events(seat, source))
    assert (after.monarch, after.initiative) == expected_pair(number), name


def test_4_7_6_row_10_seats_1_2_and_3_out_together_the_routine_is_called_directly() -> None:
    after = eliminate(
        run(game(), monarch_set(2), initiative_set(3)),
        20,
        [Elimination(seat, "life", None, None) for seat in (1, 2, 3)],
    )
    assert (after.monarch, after.initiative) == expected_pair(10)


def test_4_7_6_row_11_every_seat_out_the_routine_is_called_directly() -> None:
    after = eliminate(
        run(game(), monarch_set(2), initiative_set(4)),
        20,
        [Elimination(seat, "life", None, None) for seat in (1, 2, 3, 4)],
    )
    assert (after.monarch, after.initiative) == expected_pair(11)


def test_4_7_6_the_table_gives_the_expected_pairs_the_scenarios_are_compared_with() -> None:
    assert [expected_pair(n) for n in range(1, 14)] == [
        (1, None),
        (4, 4),
        (1, None),
        (3, None),
        (2, None),
        (2, 4),
        (1, 4),
        (1, 1),
        (4, None),
        (4, 4),
        (None, None),
        (2, None),
        (4, None),
    ]


# --- phases ---------------------------------------------------------------------------------------

HITS: list[tuple[str, Callable[[], Event]]] = [
    ("poison_changed", lambda: pc(2, 1)),
    ("counter_changed", lambda: cc(2, "energy", 1)),
    ("commander_cast", lambda: cast(2, 0, 1)),
    ("monarch_set", lambda: monarch_set(2)),
    ("initiative_set", lambda: initiative_set(2)),
]
HIT_IDS = [h[0] for h in HITS]


@pytest.mark.parametrize(("event_type", "hit"), HITS, ids=HIT_IDS)
def test_phases_the_lobby_is_invalid_phase_and_nothing_changes(
    event_type: str, hit: Callable[[], Event]
) -> None:
    state = lobby_of()
    snapshot = canon(state)
    result = apply(state, hit())
    assert isinstance(result, ApplyError)
    assert result.error.code == "invalid_phase", event_type
    assert canon(state) == snapshot
    assert [(s.poison, s.energy, s.experience, list(s.commander_cast)) for s in state.seats] == [
        (0, 0, 0, [0])
    ] * 4
    assert (state.monarch, state.initiative) == (None, None)


@pytest.mark.parametrize("phase", ["finished", "abandoned"])
@pytest.mark.parametrize(("event_type", "hit"), HITS, ids=HIT_IDS)
def test_phases_finished_and_abandoned_are_invalid_phase(
    event_type: str, hit: Callable[[], Event], phase: str
) -> None:
    assert code_of(in_phase(game(), phase), hit()) == "invalid_phase", event_type


@pytest.mark.parametrize(("event_type", "hit"), HITS, ids=HIT_IDS)
def test_phases_active_is_accepted(event_type: str, hit: Callable[[], Event]) -> None:
    assert code_of(game(), hit()) == "ok", event_type


@pytest.mark.parametrize(("event_type", "hit"), HITS, ids=HIT_IDS)
def test_phases_ending_accepts_stays_in_ending_and_has_the_effect_it_has_in_active(
    event_type: str, hit: Callable[[], Event]
) -> None:
    in_active = run(game(), hit())
    in_ending = run(in_phase(game(), "ending"), hit())
    assert in_ending.phase == "ending", event_type
    assert canon(in_ending) == canon(in_active.model_copy(update={"phase": "ending"}))


def test_phases_ending_poison_eliminates_and_the_crown_moves_on_as_in_active() -> None:
    start = in_phase(run(game(), monarch_set(3), initiative_set(4)), "ending")
    after = run(start, pc(3, 10, 2))
    seat = seat_of(after, 3)
    assert (seat.elimination_reason, seat.eliminated_by) == ("poison", 2)
    assert (after.monarch, after.initiative, after.phase) == (1, 4, "ending")


def test_phases_ending_the_other_rules_hold() -> None:
    ending = in_phase(game(), "ending")
    assert code_of(ending, pc(7, 1)) == "unknown_seat"
    assert code_of(ending, pc(2, 0)) == "invalid_amount"
    assert code_of(ending, cc(2, "poison", 1)) == "invalid_payload"
    assert code_of(ending, cast(2, 1, 1)) == "unknown_commander"
    assert code_of(run(ending, concede(2)), monarch_set(2)) == "seat_eliminated"


def test_phases_a_closed_game_takes_no_hand_off_the_causing_events_are_invalid_phase() -> None:
    state = in_phase(run(game(), monarch_set(3)), "finished")
    assert code_of(state, concede(3)) == "invalid_phase"
    assert code_of(state, pc(3, 10)) == "invalid_phase"


# --- only what this event raised ------------------------------------------------------------------


def poisoned() -> GameState:
    """Seat 2 sits at 12 poison, above the threshold of 10, and is still in the game."""
    return with_seat(game(partners=(2,)), 2, poison=12)


def test_only_raised_a_negative_poison_delta_never_eliminates_even_above_the_threshold() -> None:
    seat = seat_of(run(poisoned(), pc(2, -1)), 2)
    assert (seat.poison, seat.eliminated_at_seq) == (11, None)


def test_only_raised_removing_2_leaves_a_seat_exactly_at_the_threshold_in_the_game() -> None:
    seat = seat_of(run(poisoned(), pc(2, -2)), 2)
    assert (seat.poison, seat.eliminated_at_seq) == (10, None)


def test_only_raised_a_positive_poison_delta_eliminates_it_because_this_event_raised_it() -> None:
    seat = seat_of(run(poisoned(), pc(2, 1, 4)), 2)
    assert (seat.poison, seat.elimination_reason, seat.eliminated_by) == (13, "poison", 4)


@pytest.mark.parametrize("counter", ["energy", "experience"])
def test_only_raised_counter_changed_in_either_direction_does_not_eliminate_it(
    counter: str,
) -> None:
    up = run(poisoned(), cc(2, counter, 3))
    assert seat_of(up, 2).eliminated_at_seq is None
    assert seat_of(run(up, cc(2, counter, -3)), 2).eliminated_at_seq is None


def test_only_raised_commander_cast_in_either_direction_does_not_eliminate_it() -> None:
    up = run(poisoned(), cast(2, 0, 1), cast(2, 1, 1))
    assert seat_of(up, 2).eliminated_at_seq is None
    assert seat_of(run(up, cast(2, 0, -1)), 2).eliminated_at_seq is None


def test_only_raised_monarch_set_and_initiative_set_naming_it_do_not_eliminate_it() -> None:
    after = run(poisoned(), monarch_set(2), initiative_set(2))
    assert seat_of(after, 2).eliminated_at_seq is None
    assert (after.monarch, after.initiative) == (2, 2)


def test_only_raised_a_life_gain_does_not_eliminate_it() -> None:
    seat = seat_of(run(poisoned(), lc(2, 5)), 2)
    assert (seat.life, seat.poison, seat.eliminated_at_seq) == (45, 12, None)


def test_only_raised_a_life_loss_that_leaves_life_above_0_does_not_eliminate_it_either() -> None:
    seat = seat_of(run(poisoned(), lc(2, -5, 3)), 2)
    assert (seat.life, seat.poison, seat.eliminated_at_seq) == (35, 12, None)


def test_only_raised_commander_damage_that_leaves_life_above_0_does_not_eliminate_it() -> None:
    seat = seat_of(run(poisoned(), cd(1, 0, 2, 5)), 2)
    assert (seat.life, seat.poison, seat.eliminated_at_seq) == (35, 12, None)


def test_only_raised_other_events_on_seats_below_the_threshold_leave_them_in_the_game() -> None:
    after = run(poisoned(), cc(2, "energy", 1), pc(3, 1, 2))
    assert seat_of(after, 2).eliminated_at_seq is None
    assert (seat_of(after, 3).poison, seat_of(after, 3).eliminated_at_seq) == (1, None)


def test_only_raised_a_seat_at_life_0_in_the_game_is_not_eliminated_by_a_small_poison_delta() -> (
    None
):
    state = with_seat(game(), 2, life=0)
    seat = seat_of(run(state, pc(2, 3), cc(2, "energy", 2), cast(2, 0, 1)), 2)
    assert (seat.life, seat.poison, seat.eliminated_at_seq) == (0, 3, None)


def test_only_raised_a_tally_at_the_threshold_in_the_game_is_not_eliminated_by_poison() -> None:
    state = game().model_copy(update={"commander_damage": {"2": {"1:0": 25}}})
    seat = seat_of(run(state, pc(2, 3)), 2)
    assert (seat.poison, seat.eliminated_at_seq) == (3, None)


def test_only_raised_a_seat_above_the_threshold_keeps_its_crown_when_untouched() -> None:
    state = with_seat(run(game(), monarch_set(2)), 2, poison=12)
    assert run(state, pc(2, -1)).monarch == 2


# --- stored numbers that are not integers ---------------------------------------------------------


@pytest.mark.parametrize("stored", ["3", None, True, 1.5, [3]])
def test_stored_poison_that_is_not_an_integer_is_invalid_amount_for_a_gain_and_a_removal(
    stored: Any,
) -> None:
    state = with_seat(game(), 2, poison=stored)
    assert code_of(state, pc(2, 1)) == "invalid_amount"
    assert code_of(state, pc(2, -1)) == "invalid_amount"


@pytest.mark.parametrize("stored", ["10", None, True, 10.5])
def test_stored_poison_threshold_that_is_not_an_integer_is_invalid_amount_gain_and_removal(
    stored: Any,
) -> None:
    state = with_settings(run(game(), pc(2, 3)), poison_threshold=stored)
    assert code_of(state, pc(2, 1)) == "invalid_amount"
    assert code_of(state, pc(2, -1)) == "invalid_amount"


def test_stored_a_threshold_stored_wrongly_does_not_matter_to_the_other_handlers() -> None:
    state = with_settings(game(), poison_threshold="10")
    assert code_of(state, cc(2, "energy", 1)) == "ok"
    assert code_of(state, cast(2, 0, 1)) == "ok"
    assert code_of(state, monarch_set(2)) == "ok"


@pytest.mark.parametrize("name", ["energy", "experience"])
def test_stored_a_counter_that_is_a_string_is_invalid_amount_and_the_other_counter_works(
    name: str,
) -> None:
    other = "experience" if name == "energy" else "energy"
    state = with_seat(game(), 2, **{name: "3"})
    assert code_of(state, cc(2, name, 1)) == "invalid_amount"
    assert code_of(state, cc(2, other, 1)) == "ok"


@pytest.mark.parametrize("field", FIELDS)
def test_stored_a_value_at_2_53_minus_1_plus_1_is_invalid_amount_one_below_is_accepted(
    field: str,
) -> None:
    def hit() -> Event:
        return pc(2, 1) if field == "poison" else cc(2, field, 1)

    assert code_of(with_seat(game(), 2, **{field: MAX_SAFE}), hit()) == "invalid_amount"
    edge = with_seat(with_settings(game(), poison_threshold=99), 2, **{field: MAX_SAFE - 1})
    assert code_of(edge, hit()) == "ok"


def test_stored_a_cast_count_at_2_53_minus_1_or_a_non_integer_entry_is_invalid_amount() -> None:
    assert (
        code_of(with_seat(game(), 2, commander_cast=[MAX_SAFE]), cast(2, 0, 1)) == "invalid_amount"
    )
    assert code_of(with_seat(game(), 2, commander_cast=["1"]), cast(2, 0, 1)) == "invalid_amount"
    assert code_of(with_seat(game(), 2, commander_cast=[MAX_SAFE - 1]), cast(2, 0, 1)) == "ok"


def test_stored_a_cast_entry_missing_for_the_index_is_invalid_amount() -> None:
    state = with_seat(game(partners=(2,)), 2, commander_cast=[0])
    assert code_of(state, cast(2, 1, 1)) == "invalid_amount"


# --- purity ---------------------------------------------------------------------------------------


def holding() -> GameState:
    return run(game(partners=(2,)), monarch_set(3), initiative_set(3), pc(4, 2))


PURITY_EVENTS: list[Event] = [
    pc(2, 3),
    pc(3, 10, 2),
    pc(2, -1),
    pc(2, 0),
    pc(7, 1),
    cc(2, "energy", 4),
    cc(2, "experience", -1),
    cc(2, "poison", 1),
    cast(2, 1, 1),
    cast(2, 1, -1),
    cast(3, 1, 1),
    monarch_set(2),
    initiative_set(4),
    monarch_set(None),
    concede(3),
    lc(3, -40, 2),
]


@pytest.mark.parametrize(
    "event", PURITY_EVENTS, ids=[f"event-{i}" for i in range(len(PURITY_EVENTS))]
)
def test_purity_neither_the_state_nor_the_event_is_mutated_accepted_or_rejected(
    event: Event,
) -> None:
    state = holding()
    snapshot = canon(state)
    before_holders = (state.monarch, state.initiative)
    sent = copy.deepcopy(event)
    result = apply(state, sent)
    assert canon(state) == snapshot
    assert (state.monarch, state.initiative) == before_holders
    assert sent == event
    if not isinstance(result, ApplyError):
        assert result.state is not state


def test_purity_the_same_event_on_the_same_state_gives_the_same_result_twice() -> None:
    state = holding()
    first, second = apply(state, pc(3, 10, 2)), apply(state, pc(3, 10, 2))
    assert not isinstance(first, ApplyError)
    assert not isinstance(second, ApplyError)
    assert canon(first.state) == canon(second.state)


def test_purity_commander_cast_does_not_alias_the_commander_cast_list_of_the_input() -> None:
    state = game(partners=(2,))
    result = apply(state, cast(2, 1, 1))
    assert not isinstance(result, ApplyError)
    before = seat_of(state, 2).commander_cast
    now = seat_of(result.state, 2).commander_cast
    assert now is not before
    assert list(before) == [0, 0]
    assert list(now) == [0, 1]
    now[0] = 99  # writing into the new list later cannot reach the input
    assert list(seat_of(state, 2).commander_cast) == [0, 0]


def test_purity_a_successful_event_does_not_alias_the_seats_list_or_the_seat_of_the_input() -> None:
    state = game()
    for event in (pc(2, 3), cc(2, "energy", 1), cast(2, 0, 1), concede(2)):
        result = apply(state, event)
        assert not isinstance(result, ApplyError)
        assert result.state.seats is not state.seats
        assert seat_of(result.state, 2) is not seat_of(state, 2)
    seat = seat_of(state, 2)
    assert (seat.poison, seat.energy, seat.experience, list(seat.commander_cast)) == (0, 0, 0, [0])


def test_purity_a_hand_off_does_not_change_the_order_list_or_the_input_holders() -> None:
    state = holding()
    order = list(state.order)
    result = apply(state, concede(3))
    assert not isinstance(result, ApplyError)
    assert result.state.order == order
    assert (state.monarch, state.initiative) == (3, 3)


# --- the oracle -----------------------------------------------------------------------------------

CASES_GENERATED = 3000
PHASES = [
    "active",
    "active",
    "active",
    "active",
    "active",
    "active",
    "ending",
    "ending",
    "lobby",
    "finished",
    "abandoned",
]
COUNTS = [3, 4, 4, 5, 6]
POISONS: list[Any] = [0, 0, 0, 0, 2, 5, 9, 9, 12, MAX_SAFE - 2, "3"]
ENERGIES: list[Any] = [0, 0, 0, 1, 3, 7, MAX_SAFE - 1, None]
CASTS: list[Any] = [0, 0, 0, 1, 1, 3, MAX_SAFE - 1, "x"]
LIVES: list[Any] = [40, 40, 40, 25, 10, 5, 1]
POISON_THRESHOLDS: list[Any] = [10, 10, 10, 10, 10, 5, 15, 1, 0, 99, "10"]
CD_THRESHOLDS: list[Any] = [21, 21, 21, 21, 5, 1, "21"]
TYPES = [
    "poison_changed",
    "poison_changed",
    "poison_changed",
    "poison_changed",
    "counter_changed",
    "counter_changed",
    "commander_cast",
    "commander_cast",
    "monarch_set",
    "monarch_set",
    "initiative_set",
    "initiative_set",
    "life_changed",
    "life_changed",
    "player_conceded",
    "player_eliminated",
    "commander_damage",
    "commander_damage",
    "turn_passed",
    "seat_moved",
]
POISON_DELTAS: list[Any] = [
    1, 1, 2, 3, 5, 8, 9, 10, 12, -1, -2, -3, -5, 0, 999, 1000, -999, -1000, "1", None, True, 1.5,
    ABSENT,
]  # fmt: skip
COUNTER_DELTAS: list[Any] = [1, 2, 3, 5, -1, -2, -3, 0, 999, 1000, -1000, "1", None, ABSENT]
COUNTERS: list[Any] = [
    "energy", "energy", "experience", "experience", "poison", "Energy", "", 1, None, ABSENT,
]  # fmt: skip
CAST_DELTAS: list[Any] = [1, 1, 1, -1, -1, 0, 2, -2, "1", None, ABSENT]
CAST_INDEXES: list[Any] = [0, 0, 0, 1, 1, 2, -1, "0", None, ABSENT]
LIFE_DELTAS: list[Any] = [-1, -3, -5, -10, -40, -35, 5, 10, 0, 1000, "1", ABSENT]
AMOUNTS: list[Any] = [1, 2, 5, 7, 12, 21, 25, 0, -1, 1000, "5", ABSENT]
CAUSES: list[Any] = [ABSENT, ABSENT, "mill", "combo", "Mill", None]
CORRECTIONS: list[Any] = [True, True, True, ABSENT, False]
D5 = [
    "combat_damage",
    "commander_damage",
    "combo",
    "alternate_win",
    "poison",
    "mill",
    "life_drain",
    "concession",
    "last_standing",
    "other",
]


class Generator:
    """Park-Miller: the same numbers in the TypeScript twin, so both run the same cases."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def next(self) -> int:
        self.seed = (self.seed * 48271) % 2147483647
        return self.seed

    def pick(self, items: list[Any]) -> Any:
        return items[self.next() % len(items)]


def ids(n: int) -> list[int]:
    return list(range(1, n + 1))


def seat_pool(n: int) -> list[Any]:
    return [*ids(n), *ids(n), *ids(n), 7, 0, 9, "2", None, ABSENT]


def source_pool(n: int) -> list[Any]:
    return [ABSENT, ABSENT, ABSENT, None, *ids(n), 7, 0, "1"]


def holder_pool(n: int) -> list[Any]:
    return [None, None, *ids(n)]


def put(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is not ABSENT:
        payload[key] = value


# The payload keys of each event type in the order the generator picks them, with their pools.
EVENT_FIELDS: dict[str, list[tuple[str, str | list[Any]]]] = {
    "poison_changed": [("seat", "seat"), ("delta", POISON_DELTAS), ("source_seat", "source")],
    "counter_changed": [("seat", "seat"), ("counter", COUNTERS), ("delta", COUNTER_DELTAS)],
    "commander_cast": [("seat", "seat"), ("commander", CAST_INDEXES), ("delta", CAST_DELTAS)],
    "monarch_set": [("seat", "seat")],
    "initiative_set": [("seat", "seat")],
    "life_changed": [("seat", "seat"), ("delta", LIFE_DELTAS), ("source_seat", "source")],
    "player_conceded": [("seat", "seat")],
    "player_eliminated": [("seat", "seat"), ("cause", CAUSES)],
    "commander_damage": [
        ("source_seat", "seat"),
        ("commander", CAST_INDEXES),
        ("target_seat", "seat"),
        ("amount", AMOUNTS),
    ],
    "turn_passed": [],
    "seat_moved": [("from", "seat"), ("to", "seat"), ("correction", CORRECTIONS)],
}


def gen_event(gen: Generator, count: int) -> tuple[str, dict[str, Any]]:
    event_type: str = gen.pick(TYPES)
    picked: list[tuple[str, Any]] = []
    for key, pool in EVENT_FIELDS[event_type]:
        items = (
            seat_pool(count) if pool == "seat" else source_pool(count) if pool == "source" else pool
        )
        assert isinstance(items, list)
        picked.append((key, gen.pick(items)))
    payload: dict[str, Any] = {}
    for key, value in picked:
        put(payload, key, value)
    return event_type, payload


def build_case(
    gen: Generator, bases: dict[int, GameState]
) -> tuple[GameState, list[tuple[str, dict[str, Any]]], str]:
    phase = gen.pick(PHASES)
    count = gen.pick(COUNTS)
    order = ids(count)
    for _ in range(gen.next() % 3):
        a = gen.next() % count
        b = gen.next() % count
        order[a], order[b] = order[b], order[a]
    lone = gen.next() % 5 == 0
    lone_seat = 1 + gen.next() % count
    out = [gen.next() % 4 == 0 for _ in ids(count)]
    partner = [gen.next() % 3 == 0 for _ in ids(count)]
    active = 1 + gen.next() % count
    first = 1 + gen.next() % count
    round_ = 1 + gen.next() % 3
    turn_number = 1 + gen.next() % 9
    monarch = gen.pick(holder_pool(count))
    initiative = gen.pick(holder_pool(count))
    lone_monarch = gen.next() % 2 == 0
    lone_initiative = gen.next() % 2 == 0
    poisons = [gen.pick(POISONS) for _ in ids(count)]
    energies = [gen.pick(ENERGIES) for _ in ids(count)]
    experiences = [gen.pick(ENERGIES) for _ in ids(count)]
    casts = [[gen.pick(CASTS), gen.pick(CASTS)] for _ in ids(count)]
    lives = [gen.pick(LIVES) for _ in ids(count)]
    poison_threshold = gen.pick(POISON_THRESHOLDS)
    cd_threshold = gen.pick(CD_THRESHOLDS)
    if lone and lone_monarch:
        monarch = lone_seat
    if lone and lone_initiative:
        initiative = lone_seat
    base = bases[count]
    pair = [
        Commander(card_id=None, name="Partner A"),
        Commander(card_id=str(PARTNER_B["card_id"]), name="Partner B"),
    ]
    seats = []
    for i, seat in enumerate(base.seats):
        is_out = seat.seat != lone_seat if lone else out[i]
        changes: dict[str, Any] = {
            "life": 0 if is_out else lives[i],
            "poison": poisons[i],
            "energy": energies[i],
            "experience": experiences[i],
            "commanders": pair if partner[i] else [Commander(card_id=None, name="")],
            "commander_cast": casts[i] if partner[i] else [casts[i][0]],
        }
        if is_out:
            changes.update(
                elimination_reason="life",
                eliminated_by=None,
                eliminated_at_seq=3,
                eliminated_round=1,
                elimination_cause=None,
            )
        seats.append(seat.model_copy(update=changes))
    state = in_phase(
        base.model_copy(
            update={
                "order": order,
                "seats": seats,
                "active_seat": active,
                "first_seat": first,
                "round": round_,
                "turn": turn_number,
                "monarch": monarch,
                "initiative": initiative,
                "settings": base.settings.model_copy(
                    update={
                        "poison_threshold": poison_threshold,
                        "commander_damage_threshold": cd_threshold,
                    }
                ),
            }
        ),
        phase,
    )
    steps = [gen_event(gen, count) for _ in range(2 + gen.next() % 5)]
    label = (
        f"{phase} n={count} order={''.join(map(str, order))} active={active} first={first} "
        f"monarch={monarch} initiative={initiative} pt={poison_threshold!r}"
    )
    return state, steps, label


def pget(payload: dict[str, Any], key: str) -> Any:
    return payload.get(key)


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and abs(value) <= MAX_SAFE


def is_seat_id(value: Any) -> bool:
    return is_int(value) and 1 <= value <= 8


def is_absent(value: Any) -> bool:
    return value is None


def scan_after(p: dict[str, Any], start: int | None) -> list[int]:
    """The seats after ``start`` in ``order``, cyclically, ``start`` itself last (4.4.10)."""
    order: list[int] = p["order"]
    at = -1 if start is None else order.index(start)
    return [*order[at + 1 :], *order[: at + 1]]


def standing_ids(p: dict[str, Any]) -> list[int]:
    return [s["seat"] for s in p["seats"] if s["eliminated_at_seq"] is None]


def eliminate_in(p: dict[str, Any], entries: list[tuple[int, str, int | None, str | None]]) -> None:
    """4.5.2 steps 1 to 4 on ``p`` after the event's own changes (``last_seq`` is the seq)."""
    standing = standing_ids(p)
    applied: list[int] = []
    for seat, reason, by, cause in entries:
        if seat not in standing or seat in applied:
            continue
        applied.append(seat)
        row = next(s for s in p["seats"] if s["seat"] == seat)
        row["elimination_reason"] = reason
        row["eliminated_by"] = by
        row["elimination_cause"] = cause
        row["eliminated_at_seq"] = p["last_seq"]
        row["eliminated_round"] = p["round"]
    if applied and len(applied) == len(standing):
        p["no_winner"] = True
    survivors = [i for i in standing if i not in applied]
    for key in MONARCH_KEYS:
        holder = p[key]
        if holder is None or holder not in applied:
            continue
        if not survivors:
            p[key] = None
        elif p["active_seat"] is not None and p["active_seat"] in survivors:
            p[key] = p["active_seat"]
        else:
            p[key] = next((i for i in scan_after(p, p["active_seat"]) if i in survivors), None)


class Oracle:
    """The expected outcome of one event on a plain state, from the contract's words."""

    def __init__(self, before: dict[str, Any]) -> None:
        self.p = copy.deepcopy(before)
        self.live = self.p["phase"] in ("active", "ending")
        self.p["last_seq"] += 1

    def row(self, seat_id: Any) -> dict[str, Any] | None:
        return next((s for s in self.p["seats"] if s["seat"] == seat_id), None)

    def out(self, seat_id: Any) -> bool:
        row = self.row(seat_id)
        assert row is not None
        return row["eliminated_at_seq"] is not None

    def source_given(self, source: Any) -> bool:
        return not is_absent(source)

    def seat_checks(self, seat: Any, source: Any = None) -> str | None:
        """Checks 3 and 4 of 4.5.1: unknown_seat, then seat_eliminated (``seat`` then source)."""
        if self.row(seat) is None or (self.source_given(source) and self.row(source) is None):
            return "unknown_seat"
        if self.out(seat) or (self.source_given(source) and self.out(source)):
            return "seat_eliminated"
        return None

    def poison_changed(self, payload: dict[str, Any]) -> str | dict[str, Any]:
        seat, delta, source = (
            pget(payload, "seat"),
            pget(payload, "delta"),
            pget(payload, "source_seat"),
        )
        if not is_seat_id(seat) or not is_int(delta):
            return "invalid_payload"
        if not (is_absent(source) or is_seat_id(source)):
            return "invalid_payload"
        if not self.live:
            return "invalid_phase"
        failed = self.seat_checks(seat, source)
        if failed is not None:
            return failed
        if delta == 0 or abs(delta) > 999:
            return "invalid_amount"
        target = self.row(seat)
        assert target is not None
        threshold = self.p["settings"]["poison_threshold"]
        if not is_int(target["poison"]) or not is_int(threshold):
            return "invalid_amount"
        poison = target["poison"] + delta
        if poison < 0 or poison > MAX_SAFE:
            return "invalid_amount"
        target["poison"] = poison
        if delta > 0 and poison >= threshold:
            by = source if self.source_given(source) else None
            eliminate_in(self.p, [(seat, "poison", by, None)])
        return self.p

    def counter_changed(self, payload: dict[str, Any]) -> str | dict[str, Any]:
        seat, counter, delta = (
            pget(payload, "seat"),
            pget(payload, "counter"),
            pget(payload, "delta"),
        )
        if not is_seat_id(seat) or not is_int(delta):
            return "invalid_payload"
        if counter not in ("energy", "experience") or not isinstance(counter, str):
            return "invalid_payload"
        if not self.live:
            return "invalid_phase"
        failed = self.seat_checks(seat)
        if failed is not None:
            return failed
        if delta == 0 or abs(delta) > 999:
            return "invalid_amount"
        target = self.row(seat)
        assert target is not None
        stored = target[counter]
        if not is_int(stored) or stored + delta < 0 or stored + delta > MAX_SAFE:
            return "invalid_amount"
        target[counter] = stored + delta
        return self.p

    def commander_cast(self, payload: dict[str, Any]) -> str | dict[str, Any]:
        seat, commander = pget(payload, "seat"), pget(payload, "commander")
        delta = pget(payload, "delta")
        if not is_seat_id(seat) or not is_int(commander) or not is_int(delta):
            return "invalid_payload"
        if not self.live:
            return "invalid_phase"
        failed = self.seat_checks(seat)
        if failed is not None:
            return failed
        if delta not in (1, -1):
            return "invalid_amount"
        target = self.row(seat)
        assert target is not None
        if commander < 0 or commander >= len(target["commanders"]):
            return "unknown_commander"
        counts = target["commander_cast"]
        count = counts[commander] if commander < len(counts) else None
        if not is_int(count) or count + delta < 0 or count + delta > MAX_SAFE:
            return "invalid_amount"
        counts[commander] = count + delta
        return self.p

    def holder_set(self, key: str, payload: dict[str, Any]) -> str | dict[str, Any]:
        seat = pget(payload, "seat")
        if not is_seat_id(seat):
            return "invalid_payload"
        if not self.live:
            return "invalid_phase"
        failed = self.seat_checks(seat)
        if failed is not None:
            return failed
        self.p[key] = seat
        return self.p

    def life_changed(self, payload: dict[str, Any]) -> str | dict[str, Any]:
        seat, delta, source = (
            pget(payload, "seat"),
            pget(payload, "delta"),
            pget(payload, "source_seat"),
        )
        if not is_seat_id(seat) or not is_int(delta):
            return "invalid_payload"
        if not (is_absent(source) or is_seat_id(source)):
            return "invalid_payload"
        if not self.live:
            return "invalid_phase"
        failed = self.seat_checks(seat, source)
        if failed is not None:
            return failed
        if delta == 0 or abs(delta) > 999:
            return "invalid_amount"
        target = self.row(seat)
        assert target is not None
        if not is_int(target["life"]) or abs(target["life"] + delta) > MAX_SAFE:
            return "invalid_amount"
        target["life"] += delta
        if delta < 0 and target["life"] <= 0:
            by = source if self.source_given(source) else None
            eliminate_in(self.p, [(seat, "life", by, None)])
        return self.p

    def player_conceded(self, payload: dict[str, Any]) -> str | dict[str, Any]:
        seat = pget(payload, "seat")
        if not is_seat_id(seat):
            return "invalid_payload"
        if not self.live:
            return "invalid_phase"
        failed = self.seat_checks(seat)
        if failed is not None:
            return failed
        eliminate_in(self.p, [(seat, "concession", None, None)])
        return self.p

    def player_eliminated(self, payload: dict[str, Any]) -> str | dict[str, Any]:
        seat, cause = pget(payload, "seat"), pget(payload, "cause")
        if not is_seat_id(seat):
            return "invalid_payload"
        if not is_absent(cause) and cause not in D5:
            return "invalid_payload"
        if not self.live:
            return "invalid_phase"
        failed = self.seat_checks(seat)
        if failed is not None:
            return failed
        eliminate_in(self.p, [(seat, "declared", None, None if is_absent(cause) else cause)])
        return self.p

    def commander_damage(self, payload: dict[str, Any]) -> str | dict[str, Any]:
        s, c = pget(payload, "source_seat"), pget(payload, "commander")
        t, a = pget(payload, "target_seat"), pget(payload, "amount")
        if not (is_seat_id(s) and is_seat_id(t) and is_int(c) and is_int(a)):
            return "invalid_payload"
        if not self.live:
            return "invalid_phase"
        if self.row(s) is None or self.row(t) is None:
            return "unknown_seat"
        if self.out(t) or self.out(s):
            return "seat_eliminated"
        if a < 1 or a > 999:
            return "invalid_amount"
        source, target = self.row(s), self.row(t)
        assert source is not None
        assert target is not None
        if c < 0 or c >= len(source["commanders"]):
            return "unknown_commander"
        threshold = self.p["settings"]["commander_damage_threshold"]
        key = f"{s}:{c}"
        row = self.p["commander_damage"].get(str(t), {})
        tally = row.get(key, 0) + a
        if not is_int(target["life"]) or not is_int(threshold):
            return "invalid_amount"
        if abs(target["life"] - a) > MAX_SAFE or tally > MAX_SAFE:
            return "invalid_amount"
        self.p["commander_damage"][str(t)] = {**row, key: tally}
        target["life"] -= a
        if tally >= threshold:
            eliminate_in(self.p, [(t, "commander_damage", s, None)])
        elif target["life"] <= 0:
            eliminate_in(self.p, [(t, "life", s, None)])
        return self.p

    def turn_passed(self, payload: dict[str, Any]) -> str | dict[str, Any]:
        if not self.live or not standing_ids(self.p):
            return "invalid_phase"
        scan = scan_after(self.p, self.p["active_seat"])
        n = next(i for i in scan if not self.out(i))
        walked = scan[: scan.index(n) + 1]
        self.p["active_seat"] = n
        self.p["turn"] += 1
        if self.p["first_seat"] in walked:
            self.p["round"] += 1
        return self.p

    def seat_moved(self, payload: dict[str, Any]) -> str | dict[str, Any]:
        from_seat, to_seat, correction = (
            pget(payload, "from"),
            pget(payload, "to"),
            pget(payload, "correction"),
        )
        if not is_seat_id(from_seat) or not is_seat_id(to_seat):
            return "invalid_payload"
        if not is_absent(correction) and not isinstance(correction, bool):
            return "invalid_payload"
        if not (self.p["phase"] == "lobby" or (self.live and correction is True)):
            return "invalid_phase"
        if self.row(from_seat) is None or self.row(to_seat) is None:
            return "unknown_seat"
        order: list[int] = self.p["order"]
        a, b = order.index(from_seat), order.index(to_seat)
        order[a], order[b] = order[b], order[a]
        return self.p

    def apply(self, event_type: str, payload: dict[str, Any]) -> str | dict[str, Any]:
        if event_type in ("monarch_set", "initiative_set"):
            return self.holder_set(event_type.removesuffix("_set"), payload)
        method: Callable[[dict[str, Any]], str | dict[str, Any]] = getattr(self, event_type)
        return method(payload)


FIVE = ("poison_changed", "counter_changed", "commander_cast", "monarch_set", "initiative_set")
REJECTIONS = ("invalid_payload", "invalid_phase", "unknown_seat", "seat_eliminated")
REQUIRED = [
    "ok:poison_changed",
    "ok:counter_changed",
    "ok:commander_cast",
    "ok:monarch_set",
    "ok:initiative_set",
    "ok:life_changed",
    "ok:player_conceded",
    "ok:player_eliminated",
    "ok:commander_damage",
    "ok:turn_passed",
    "ok:seat_moved",
    *(
        f"{t}:{c}"
        for t in ("poison_changed", "counter_changed")
        for c in (*REJECTIONS, "invalid_amount")
    ),
    *(f"commander_cast:{c}" for c in (*REJECTIONS, "invalid_amount", "unknown_commander")),
    *(f"{t}:{c}" for t in ("monarch_set", "initiative_set") for c in REJECTIONS),
    "poison_elimination",
    "eliminated_by_poison",
    "full_draw",
    "handoff_active",
    "handoff_walk",
    "handoff_null",
    "handoff_monarch",
    "handoff_initiative",
    "handoff_by_life",
    "handoff_by_concession",
    "handoff_by_declared",
    "handoff_by_commander_damage",
    "handoff_by_poison",
]


def classify(
    seen: dict[str, int], step_type: str, before: dict[str, Any], expected: dict[str, Any]
) -> None:
    def bump_seen(kind: str) -> None:
        seen[kind] = seen.get(kind, 0) + 1

    bump_seen(f"ok:{step_type}")
    for seat in expected["seats"]:
        if seat["eliminated_at_seq"] == expected["last_seq"]:
            bump_seen(f"eliminated_by_{seat['elimination_reason']}")
            if step_type == "poison_changed":
                bump_seen("poison_elimination")
    if expected["no_winner"] and not before["no_winner"]:
        bump_seen("full_draw")
    for key in MONARCH_KEYS:
        was = before[key]
        if was is None or expected[key] == was:
            continue
        lost = next(s for s in expected["seats"] if s["seat"] == was)
        if lost["eliminated_at_seq"] != expected["last_seq"]:
            continue
        if expected[key] is None:
            kind = "handoff_null"
        elif expected[key] == expected["active_seat"]:
            kind = "handoff_active"
        else:
            kind = "handoff_walk"
        bump_seen(kind)
        bump_seen(f"handoff_by_{lost['elimination_reason']}")
        bump_seen(f"handoff_{key}")


def test_the_reducer_agrees_with_the_oracle_on_3000_generated_cases_whole_states_each_step() -> (
    None
):
    gen = Generator(615)
    bases = {n: game(count=n) for n in COUNTS}
    seen: dict[str, int] = {}
    mismatches: list[str] = []
    for _ in range(CASES_GENERATED):
        state, steps, label = build_case(gen, bases)
        for step_type, payload in steps:
            before = plain(state)
            expected = Oracle(before).apply(step_type, payload)
            result = apply(state, ev(step_type, payload))
            actual = (
                str(result.error.code) if isinstance(result, ApplyError) else canon(result.state)
            )
            want = expected if isinstance(expected, str) else canonical(expected)
            if actual != want:
                if len(mismatches) < 5:
                    mismatches.append(
                        f"{label} {step_type} {payload!r}\n  want {want}\n  got  {actual}"
                    )
                break
            if isinstance(expected, str):
                kind = f"{step_type}:{expected}" if step_type in FIVE else f"other:{expected}"
                seen[kind] = seen.get(kind, 0) + 1
                continue
            classify(seen, step_type, before, expected)
            assert not isinstance(result, ApplyError)
            state = result.state
    assert mismatches == []
    for kind in REQUIRED:
        assert seen.get(kind, 0) > 0, f"the generator never produced {kind}"


# --- the vectors are named after their content (8.6) and hold the criteria ------------------------

TYPE_NAMES = (
    "poison-changed",
    "counter-changed",
    "commander-cast",
    "monarch-set",
    "initiative-set",
)
VECTOR_NAME = re.compile(
    rf"^(accept|reject)-({'|'.join(TYPE_NAMES)})-.*\.json$|^accept-hand-off-.*\.json$"
)
TYPE_OF = {name: name.replace("-", "_") for name in TYPE_NAMES}
REASON_OF_NAME = {
    "life": "life",
    "concession": "concession",
    "declared": "declared",
    "commander-damage": "commander_damage",
    "poison": "poison",
}
REJECT_PREFIX = re.compile(rf"^reject-({'|'.join(TYPE_NAMES)})-")
REJECT_NAME_CODES: list[tuple[str, str]] = [
    (r"^(seat|source-seat)-unknown$", "unknown_seat"),
    (r"^(seat|source-seat)-(nine|zero|missing|null|a-string|a-boolean)$", "invalid_payload"),
    (
        r"^(on-an-eliminated-seat|on-a-seat-that-conceded|an-eliminated-seat"
        r"|from-an-eliminated-source-seat|removal-from-an-eliminated-source-seat"
        r"|the-seat-that-held-it-before-its-elimination)$",
        "seat_eliminated",
    ),
    (r"^eliminated-(seat|source)$", "seat_eliminated"),
    (r"^delta-(zero|1000|minus-1000|2|minus-2)$", "invalid_amount"),
    (r"^delta-", "invalid_payload"),
    (r"^counter-", "invalid_payload"),
    (
        r"^commander-(1-of-a-single|2-of-a-partner|1-after-the-partners-were-replaced"
        r"|far-too-large|negative)",
        "unknown_commander",
    ),
    (r"^commander-", "invalid_payload"),
    (
        r"^(below-zero|removal-at-zero|energy-below-zero|experience-at-zero"
        r"|experience-below-zero-after-a-gain|minus-one-at-zero"
        r"|minus-one-below-zero-on-a-partner)$",
        "invalid_amount",
    ),
    (
        r"^(threshold-a-string|threshold-null|removal-with-a-placeholder-threshold)$",
        "invalid_amount",
    ),
    (r"^in-the-lobby$", "invalid_phase"),
    (r"^unknown-commander$", "unknown_commander"),
    (r"^invalid-amount$", "invalid_amount"),
    (r"^invalid-phase$", "invalid_phase"),
    (r"^unknown-seat$", "unknown_seat"),
    (r"^bad-payload$", "invalid_payload"),
]
BEFORE = re.compile(r"-before-(?!its-)")


def reject_head(stem: str) -> str:
    """The part of a reject vector's name that promises the code (before ``-before-``)."""
    return BEFORE.split(REJECT_PREFIX.sub("", stem))[0]


def code_from_name(stem: str) -> str | None:
    head = reject_head(stem)
    return next((code for pattern, code in REJECT_NAME_CODES if re.search(pattern, head)), None)


def vector_files() -> list[str]:
    if not VECTORS_DIR.is_dir():
        return []
    return sorted(p.name for p in VECTORS_DIR.glob("*.json") if VECTOR_NAME.match(p.name))


def load_vector(file: str) -> dict[str, Any]:
    loaded = json.loads((VECTORS_DIR / file).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def type_of_stem(stem: str) -> str | None:
    return next((v for k, v in TYPE_OF.items() if f"-{k}-" in stem), None)


def eliminated_seats(final: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in final["seats"] if s["eliminated_at_seq"] is not None]


def seat_in(final: dict[str, Any], seat_id: Any) -> dict[str, Any] | None:
    return next((s for s in final["seats"] if s["seat"] == seat_id), None)


def standing_in(final: dict[str, Any], seat_id: Any) -> bool:
    row = seat_in(final, seat_id)
    assert row is not None
    return row["eliminated_at_seq"] is None


def test_vectors_there_are_at_least_200_for_the_five_types_and_the_hand_off() -> None:
    require(VECTORS_DIR)
    assert len(vector_files()) >= 200


def test_vectors_every_vector_the_contract_names_exists() -> None:
    names = "|".join([*TYPE_NAMES, "hand-off"])
    named = [
        f"{m}.json"
        for m in re.findall(rf"`((?:accept|reject)-(?:{names})-[a-z0-9-]+)`", contract_text())
    ]
    for file in named:
        assert (VECTORS_DIR / file).is_file(), file


def check_accept_poison_and_counter(
    stem: str, vector: dict[str, Any], event_type: str | None, of_type: list[dict[str, Any]]
) -> None:
    final = vector["expect"]["state"]
    gone = eliminated_seats(final)
    created = next((e for e in vector["events"] if e["type"] == "game_created"), None)
    changed = next((e for e in vector["events"] if e["type"] == "settings_changed"), None)
    if re.search(r"-stays?-in-the-game|-stay-in-the-game|never-eliminates", stem):
        assert gone == []
    elif (
        re.search(r"-(eliminates|eliminating)-|-eliminates$|-eliminates-", stem)
        and event_type == "poison_changed"
        and not re.search(r"eliminating-one-of-four|eliminating-the-last-seat", stem)
    ):
        assert any(s["elimination_reason"] == "poison" for s in gone)
    if "eliminating-the-last-seat-is-a-full-draw" in stem:
        assert final["no_winner"] is True
        assert len(gone) == len(final["seats"])
    if "eliminating-one-of-four-leaves-three-and-no-draw" in stem:
        assert len(final["seats"]) == 4
        assert len(gone) == 1
        assert final["no_winner"] is False
    given = re.search(r"threshold-(\d+)", stem)
    if given is not None and event_type == "poison_changed":
        number = int(given.group(1))
        assert final["settings"]["poison_threshold"] == number
        via = [
            vector["settings"].get("poison_threshold"),
            created["payload"]["settings"]["poison_threshold"] if created else None,
            changed["payload"]["poison_threshold"] if changed else None,
        ]
        assert number in via
    if "from-game-created" in stem:
        assert created is not None
    if "from-settings-changed" in stem:
        assert changed is not None
    if re.search(r"delta-999-in-both-directions|delta-999-both-ways", stem):
        deltas = [e["payload"].get("delta") for e in of_type]
        assert 999 in deltas
        assert -999 in deltas
    if "down-to-exactly-zero" in stem:
        field = (
            "poison"
            if event_type == "poison_changed"
            else "energy"
            if "energy" in stem
            else "experience"
        )
        assert [s[field] for s in final["seats"]] == [0] * len(final["seats"])
        assert any(e["payload"]["delta"] < 0 for e in of_type)


def check_accept_cast_and_holders(
    stem: str, vector: dict[str, Any], event_type: str | None, of_type: list[dict[str, Any]]
) -> None:
    final = vector["expect"]["state"]
    events = vector["events"]
    if "minus-one-goes-down-to-zero" in stem:
        assert any(e["payload"].get("delta") == -1 for e in of_type)
        assert any(0 in s["commander_cast"] and len(s["commanders"]) == 2 for s in final["seats"])
    if "partner" in stem and event_type == "commander_cast":
        assert any(len(s["commanders"]) == 2 for s in final["seats"])
        assert any(e["payload"].get("commander") == 1 for e in of_type)
    if "from-a-deck-assigned" in stem:
        assert any(e["type"] == "deck_assigned" for e in events)
        assert not any(e["type"] == "commanders_set" for e in events)
    if "ten-casts" in stem:
        assert len(of_type) == 10
    if "seats-are-independent" in stem:
        assert len({e["payload"]["seat"] for e in of_type}) >= 2
    if "ignores-unknown-payload-keys" in stem:
        known = {"seat", "delta", "source_seat", "counter", "commander"}
        assert any(set(e["payload"]) - known for e in of_type)
    if "the-current-holder-again-is-a-no-op" in stem:
        seats = [e["payload"]["seat"] for e in of_type]
        assert len(seats) == 2
        assert seats[0] == seats[1]
    if "moves-to-the-new-seat-and-only-one-seat-holds-it" in stem:
        seats = [e["payload"]["seat"] for e in of_type]
        assert len(set(seats)) >= 2
        assert final["monarch" if event_type == "monarch_set" else "initiative"] == seats[-1]
    if "-is-independent-of-the-" in stem:
        assert any(e["type"] == "monarch_set" for e in events)
        assert any(e["type"] == "initiative_set" for e in events)
        assert final["monarch"] is not None
        assert final["initiative"] is not None
    if stem.endswith("-do-not-change"):
        assert any(e["type"] == "turn_passed" for e in events)
        assert final["active_seat"] == 2


HAND_OFF_PASSES = re.compile(
    r"^accept-hand-off-(monarch|initiative)-passes-to-the-active-seat-when-eliminated-by-"
    r"(life|concession|declared|commander-damage|poison)$"
)
HAND_OFF_NEXT = re.compile(
    r"^accept-hand-off-(monarch|initiative)-of-the-active-seat-goes-to-the-next-seat-when-"
    r"eliminated-by-(life|concession|declared|commander-damage|poison)$"
)


def check_hand_off_by_event(stem: str, final: dict[str, Any]) -> None:
    passes, nxt = HAND_OFF_PASSES.match(stem), HAND_OFF_NEXT.match(stem)
    found = passes or nxt
    if found is None:
        return
    key = found.group(1)
    other = "initiative" if key == "monarch" else "monarch"
    gone = eliminated_seats(final)
    assert final[other] is None
    assert len(gone) == 1
    assert gone[0]["elimination_reason"] == REASON_OF_NAME[found.group(2)]
    assert final[key] is not None
    assert final[key] != gone[0]["seat"]
    assert standing_in(final, final[key])
    if passes is not None:
        assert final[key] == final["active_seat"]
        return
    assert gone[0]["seat"] == final["active_seat"]  # the holder was the active player
    order = final["order"]
    at = order.index(final["active_seat"])
    scan = [*order[at + 1 :], *order[: at + 1]]
    assert final[key] == next(i for i in scan if standing_in(final, i))


def check_hand_off_special(stem: str, vector: dict[str, Any]) -> None:
    final = vector["expect"]["state"]
    events = vector["events"]
    gone = eliminated_seats(final)

    def standing(seat_id: Any) -> bool:
        return standing_in(final, seat_id)

    if "wraps-around-the-end-of-the-order" in stem:
        assert final["active_seat"] == final["order"][-1]
        assert final["active_seat"] == gone[-1]["seat"]
        holder = final["initiative"] if "initiative" in stem else final["monarch"]
        assert holder == next(i for i in final["order"] if standing(i))
    if "wrap-around-skips-a-seat-that-is-already-out" in stem:
        assert final["active_seat"] == final["order"][-1]
        assert not standing(final["order"][0])
        assert final["monarch"] == final["order"][1]
    if "the-next-seat-skips-eliminated-seats" in stem:
        at = final["order"].index(final["active_seat"])
        assert not standing(final["order"][(at + 1) % len(final["order"])])
    if "a-seat-moved-correction" in stem:
        swap = next(e for e in events if e["type"] == "seat_moved")
        assert swap["payload"]["correction"] is True
        assert final["order"] != sorted(final["order"])
    if "a-holder-that-is-not-eliminated-keeps-it" in stem:
        for e in (e for e in events if e["type"] in ("monarch_set", "initiative_set")):
            holder = final["monarch" if e["type"] == "monarch_set" else "initiative"]
            assert holder == e["payload"]["seat"]
            assert standing(e["payload"]["seat"])
        assert len(gone) == 1
    if stem.startswith("accept-hand-off-the-last-seat"):
        assert all(s["eliminated_at_seq"] is not None for s in final["seats"])
        assert final["no_winner"] is True
        assert final["monarch"] is None
    if re.search(
        r"the-last-seat-leaves-nobody-to-hold-the-monarch-and-the-initiative"
        r"|the-last-seat-eliminated-by-life-leaves-no-monarch-and-no-initiative",
        stem,
    ):
        assert final["initiative"] is None
        assert any(e["type"] == "initiative_set" for e in events)
        assert any(e["type"] == "monarch_set" for e in events)
    if "one-seat-holds-both" in stem:
        assert final["monarch"] is not None
        assert final["monarch"] == final["initiative"]
    if "the-self-eliminated-active-holder" in stem:
        hit = next(e for e in events if e["type"] == "life_changed")
        assert hit["payload"]["seat"] == hit["payload"]["source_seat"]
        assert hit["payload"]["seat"] == final["active_seat"]
    if "the-turn-passed-before-the-elimination" in stem:
        assert any(e["type"] == "turn_passed" for e in events)
        assert final["monarch"] == final["active_seat"]
    if "the-active-seat-is-the-first-seat-of-round-2" in stem:
        assert len([e for e in events if e["type"] == "turn_passed"]) == 4
    if "when-a-commander-hit-takes-the-life-to-zero" in stem:
        assert [s["elimination_reason"] for s in gone] == ["life"]
        assert any(e["type"] == "commander_damage" for e in events)


def check_accept(stem: str, vector: dict[str, Any], event_type: str | None) -> None:
    final = vector["expect"]["state"]
    assert final is not None
    of_type = [e for e in vector["events"] if e["type"] == event_type]
    if event_type is not None:
        assert of_type, "an event of the named type"
    if stem.startswith("accept-hand-off-"):
        check_hand_off_by_event(stem, final)
        check_hand_off_special(stem, vector)
        return
    check_accept_poison_and_counter(stem, vector, event_type, of_type)
    check_accept_cast_and_holders(stem, vector, event_type, of_type)


def field_probes(field: str) -> dict[str, Callable[[Any, bool], bool]]:
    def number(v: Any) -> bool:
        return isinstance(v, int | float) and not isinstance(v, bool)

    def unknown(v: Any, _has: bool) -> bool:
        if field == "counter":
            return isinstance(v, str) and v not in ("energy", "experience")
        return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 8

    return {
        "nine": lambda v, h: v == 9 and number(v),
        "zero": lambda v, h: v == 0 and number(v),
        "missing": lambda v, h: not h,
        "null": lambda v, h: h and v is None,
        "a-string": lambda v, h: isinstance(v, str),
        "a-boolean": lambda v, h: isinstance(v, bool),
        "a-fraction": lambda v, h: isinstance(v, float) and not v.is_integer(),
        "a-number": lambda v, h: number(v),
        "1000": lambda v, h: v == 1000 and number(v),
        "minus-1000": lambda v, h: v == -1000 and number(v),
        "2": lambda v, h: v == 2 and number(v),
        "minus-2": lambda v, h: v == -2 and number(v),
        "negative": lambda v, h: number(v) and v < 0,
        "far-too-large": lambda v, h: number(v) and v >= 1000,
        "empty": lambda v, h: v == "",
        "unknown": unknown,
        "wrong-case": lambda v, h: isinstance(v, str) and v != v.lower(),
        "beyond-the-safe-range": lambda v, h: number(v) and abs(v) > MAX_SAFE,
    }


def eliminated_earlier(before: list[dict[str, Any]], seat: Any, kinds: tuple[str, ...]) -> bool:
    return any(e["type"] in kinds and e["payload"].get("seat") == seat for e in before)


def check_reject(stem: str, vector: dict[str, Any], event_type: str | None) -> None:
    error = vector["expect"]["error"]
    index, code = error["index"], error["code"]
    rejected = vector["events"][index]
    assert rejected["type"] == event_type
    promised = code_from_name(stem)
    assert promised is not None, f"no code is promised by the name {stem}"
    assert code == promised
    head = reject_head(stem)
    payload = rejected["payload"]
    before = vector["events"][:index]
    field = None
    kind = ""
    for prefix, name in (
        ("source-seat-", "source_seat"),
        ("seat-", "seat"),
        ("delta-", "delta"),
        ("commander-", "commander"),
        ("counter-", "counter"),
    ):
        if head.startswith(prefix):
            field = name
            kind = head.removeprefix(prefix)
            break
    if field is not None:
        probes = field_probes(field)
        if kind in probes:
            assert probes[kind](pget(payload, field), field in payload), f"{field} of {stem}"
    if head in ("seat-unknown", "source-seat-unknown"):
        assert field is not None
        joined_seats = [
            e["payload"]["seat"] for e in vector["events"] if e["type"] == "seat_joined"
        ]
        assert payload[field] not in joined_seats
    if head == "in-the-lobby":
        assert not any(e["type"] == "game_started" for e in before)
    if head in (
        "on-an-eliminated-seat",
        "on-a-seat-that-conceded",
        "an-eliminated-seat",
        "the-seat-that-held-it-before-its-elimination",
        "eliminated-seat",
    ):
        killers = ("player_conceded", "player_eliminated", "poison_changed", "life_changed")
        assert eliminated_earlier(before, pget(payload, "seat"), killers)
    if head in (
        "from-an-eliminated-source-seat",
        "removal-from-an-eliminated-source-seat",
        "eliminated-source",
    ):
        assert any(
            e["type"] in ("player_conceded", "player_eliminated")
            and e["payload"].get("seat") == pget(payload, "source_seat")
            for e in before
        )
    if head in (
        "below-zero",
        "removal-at-zero",
        "minus-one-at-zero",
        "energy-below-zero",
        "experience-at-zero",
        "experience-below-zero-after-a-gain",
        "minus-one-below-zero-on-a-partner",
    ):
        assert isinstance(payload["delta"], int)
        assert payload["delta"] < 0
    if head in ("threshold-a-string", "removal-with-a-placeholder-threshold"):
        assert isinstance(vector["settings"]["poison_threshold"], str)
    if head == "threshold-null":
        assert vector["settings"]["poison_threshold"] is None
    if head == "commander-1-after-the-partners-were-replaced":
        assert len([e for e in before if e["type"] == "commanders_set"]) == 2


@pytest.mark.parametrize("file", vector_files())
def test_vectors_a_vector_is_named_after_its_content(file: str) -> None:
    vector = load_vector(file)
    stem = file.removesuffix(".json")
    assert vector["name"] == stem
    event_type = None if stem.startswith("accept-hand-off-") else type_of_stem(stem)
    if not stem.startswith("accept-hand-off-"):
        assert event_type is not None, f"no type in the name {stem}"
    if stem.startswith("accept-"):
        check_accept(stem, vector, event_type)
    else:
        check_reject(stem, vector, event_type)


def final_state(file: str) -> dict[str, Any]:
    state = load_vector(file)["expect"]["state"]
    assert isinstance(state, dict)
    return state


def payloads(file: str, event_type: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in load_vector(file)["events"] if e["type"] == event_type]


def test_vectors_criterion_1_9_poison_then_plus_1_from_seat_4_eliminates() -> None:
    file = "accept-poison-changed-ninth-counter-then-the-tenth-eliminates-with-the-source.json"
    assert payloads(file, "poison_changed") == [
        {"seat": 2, "delta": 9},
        {"seat": 2, "delta": 1, "source_seat": 4},
    ]
    final = final_state(file)
    seat = seat_in(final, 2)
    assert seat is not None
    assert (seat["poison"], seat["elimination_reason"], seat["eliminated_by"]) == (10, "poison", 4)
    assert seat["eliminated_at_seq"] == final["last_seq"]


@pytest.mark.parametrize(
    "stem",
    [
        "accept-poison-changed-threshold-15-from-game-created-eliminates-only-at-15",
        "accept-poison-changed-threshold-15-from-settings-changed-eliminates-only-at-15",
    ],
)
def test_vectors_criterion_1_threshold_15_eliminates_only_at_15(stem: str) -> None:
    file = f"{stem}.json"
    assert [p["delta"] for p in payloads(file, "poison_changed")] == [14, 1]
    final = final_state(file)
    assert final["settings"]["poison_threshold"] == 15
    seat = seat_in(final, 2)
    assert seat is not None
    assert (seat["poison"], seat["elimination_reason"]) == (15, "poison")


@pytest.mark.parametrize(
    ("stem", "code"),
    [
        ("reject-game-created-poison-threshold-zero", "invalid_setting"),
        ("reject-game-created-poison-threshold-100", "invalid_setting"),
        ("reject-settings-changed-poison-threshold-zero", "invalid_setting"),
        ("reject-settings-changed-poison-threshold-100", "invalid_setting"),
        ("reject-poison-changed-below-zero", "invalid_amount"),
        ("reject-counter-changed-energy-below-zero", "invalid_amount"),
        ("reject-counter-changed-experience-at-zero", "invalid_amount"),
    ],
)
def test_vectors_criterion_1_the_rejections_have_the_story_codes(stem: str, code: str) -> None:
    assert load_vector(f"{stem}.json")["expect"]["error"]["code"] == code


@pytest.mark.parametrize(
    "stem",
    [
        "accept-commander-cast-partner-b-twice-has-tax-4-and-partner-a-stays-0",
        "accept-commander-cast-partner-b-twice-from-a-deck-assigned",
    ],
)
def test_vectors_criterion_2_partner_b_cast_twice_has_counts_0_2(stem: str) -> None:
    file = f"{stem}.json"
    assert payloads(file, "commander_cast") == [
        {"seat": 2, "commander": 1, "delta": 1},
        {"seat": 2, "commander": 1, "delta": 1},
    ]
    seat = seat_in(final_state(file), 2)
    assert seat is not None
    assert seat["commander_cast"] == [0, 2]


def test_vectors_criterion_2_minus_1_goes_down_to_zero_and_below_is_rejected() -> None:
    down = load_vector("accept-commander-cast-minus-one-goes-down-to-zero.json")
    assert any(e["payload"].get("delta") == -1 for e in down["events"])
    assert (
        load_vector("reject-commander-cast-minus-one-at-zero.json")["expect"]["error"]["code"]
        == "invalid_amount"
    )


def test_vectors_criterion_3_seat_1_is_the_monarch_then_seat_3_gives_monarch_3() -> None:
    file = "accept-monarch-set-moves-to-the-new-seat-and-only-one-seat-holds-it.json"
    assert payloads(file, "monarch_set") == [{"seat": 1}, {"seat": 3}]
    final = final_state(file)
    assert (final["monarch"], final["initiative"]) == (3, None)
    other = final_state("accept-initiative-set-is-independent-of-the-monarch.json")
    assert (other["monarch"], other["initiative"]) == (4, 3)


def test_vectors_criterion_4_seat_3_monarch_seat_1_active_seat_3_out_seat_1_monarch() -> None:
    final = final_state(
        "accept-hand-off-monarch-passes-to-the-active-seat-when-eliminated-by-life.json"
    )
    assert (final["monarch"], final["active_seat"]) == (1, 1)
    other = final_state(
        "accept-hand-off-monarch-of-the-active-seat-goes-to-the-next-seat-when-eliminated-by-life.json"
    )
    assert (other["monarch"], other["active_seat"]) == (4, 3)


def test_vectors_the_hand_off_has_a_vector_for_each_eliminating_event_and_both_keys() -> None:
    files = vector_files()
    for key in ("monarch", "initiative"):
        for how in ("life", "concession", "declared", "commander-damage", "poison"):
            passes = (
                f"accept-hand-off-{key}-passes-to-the-active-seat-when-eliminated-by-{how}.json"
            )
            nxt = (
                f"accept-hand-off-{key}-of-the-active-seat-goes-to-the-next-seat-when-"
                f"eliminated-by-{how}.json"
            )
            assert passes in files
            assert nxt in files
    assert any(re.search(r"^accept-hand-off-.*wrap", f) for f in files)
    assert any(re.search(r"^accept-hand-off-.*correction", f) for f in files)
    assert any(re.search(r"^accept-hand-off-.*last-seat", f) for f in files)


def test_vectors_the_kinds_of_task_t_6_1_5_3_each_have_a_vector() -> None:
    files = vector_files()

    def has(pattern: str) -> bool:
        return any(re.search(pattern, f) for f in files)

    assert has(r"^accept-poison-changed-")
    assert has(r"^accept-counter-changed-energy-")
    assert has(r"^accept-counter-changed-experience-")
    assert has(r"^accept-poison-changed-threshold-15")
    assert has(r"^accept-poison-changed-ninth-counter")
    assert has(r"^accept-commander-cast-partner")
    assert has(r"^accept-monarch-set-")
    assert has(r"^accept-initiative-set-")
    assert has(r"^accept-hand-off-")


@pytest.mark.parametrize("type_name", TYPE_NAMES)
def test_vectors_each_of_the_five_types_has_an_accept_and_a_reject_vector(type_name: str) -> None:
    files = vector_files()
    assert any(f.startswith(f"accept-{type_name}-") for f in files)
    assert any(f.startswith(f"reject-{type_name}-") for f in files)
