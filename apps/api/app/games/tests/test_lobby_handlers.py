"""Unit tests of the nine S-6.1.6 handlers (CONTRACT.md 4.4), the Python twin of ``lobby.test.ts``.

The shared vectors (``test_vectors.py``) prove the event-reachable behaviour. Here: each rejection
and its order, the state changes, and the 19-row table of 4.4.10, whose rows with eliminated seats
cannot be reached through events until S-6.1.3 (so the states are built through the models).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.games.core import GameState, initial_state
from app.games.tests.lobby_helpers import (
    DEFAULT_SETTINGS,
    GAME_ID,
    accept,
    active_game,
    lobby,
    make_event,
    reject_code,
    seated,
)

CMD = {"card_id": "c1", "name": "Atraxa"}


def _ids(state: GameState) -> list[int]:
    return [s.seat for s in state.seats]


# --- game_created ---------------------------------------------------------------------------------


def _settings(**changes: Any) -> dict[str, Any]:
    return {**DEFAULT_SETTINGS.model_dump(), **changes}


def test_game_created_replaces_the_settings_with_the_payload_values() -> None:
    state = accept(lobby(), make_event("game_created", {"settings": _settings(starting_life=20)}))
    assert state.settings.starting_life == 20
    assert state.last_seq == 1
    assert state.seats == []
    assert state.phase == "lobby"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"settings": None},
        {"settings": []},
        {"settings": {"starting_life": 40}},
        {"settings": {k: v for k, v in _settings().items() if k != "trust_mode"}},
    ],
)
def test_game_created_needs_an_object_with_all_four_settings(payload: dict[str, Any]) -> None:
    assert reject_code(lobby(), make_event("game_created", payload)) == "invalid_payload"


def test_game_created_after_the_first_event_is_invalid_phase_and_comes_before_values() -> None:
    state = lobby().model_copy(update={"last_seq": 1})
    ok = make_event("game_created", {"settings": _settings()}, seq=2)
    assert reject_code(state, ok) == "invalid_phase"
    bad = make_event("game_created", {"settings": _settings(starting_life=0)}, seq=2)
    assert reject_code(state, bad) == "invalid_phase"
    shape = make_event("game_created", {}, seq=2)
    assert reject_code(state, shape) == "invalid_payload"


@pytest.mark.parametrize(
    "bad",
    [
        {"starting_life": 0},
        {"starting_life": 1000},
        {"starting_life": "40"},
        {"commander_damage_threshold": 100},
        {"poison_threshold": 0},
        {"poison_threshold": True},
        {"trust_mode": "STRICT"},
        {"trust_mode": None},
    ],
)
def test_game_created_rejects_a_bad_setting_value(bad: dict[str, Any]) -> None:
    event = make_event("game_created", {"settings": _settings(**bad)})
    assert reject_code(lobby(), event) == "invalid_setting"


@pytest.mark.parametrize(
    "edge", [{"starting_life": 1}, {"starting_life": 999}, {"poison_threshold": 99}]
)
def test_game_created_accepts_the_edges_of_the_ranges(edge: dict[str, Any]) -> None:
    state = accept(lobby(), make_event("game_created", {"settings": _settings(**edge)}))
    assert state.settings.model_dump() == _settings(**edge)


# --- settings_changed -----------------------------------------------------------------------------


def test_settings_changed_sets_strict_trust_mode() -> None:
    state = accept(lobby(), make_event("settings_changed", {"trust_mode": "strict"}))
    assert state.settings.trust_mode == "strict"
    assert state.settings.starting_life == 40


def test_settings_changed_empty_payload_changes_only_last_seq() -> None:
    state = seated(2)
    assert accept(state, make_event("settings_changed", {})) == state.model_copy(
        update={"last_seq": 1}
    )


def test_settings_changed_starting_life_rewrites_every_seat_life() -> None:
    state = accept(seated(3), make_event("settings_changed", {"starting_life": 25}))
    assert [s.life for s in state.seats] == [25, 25, 25]
    assert state.settings.starting_life == 25


@pytest.mark.parametrize("bad", ["relaxed", None, 3, "Casual", True])
def test_settings_changed_rejects_a_bad_trust_mode_with_invalid_setting(bad: Any) -> None:
    assert reject_code(lobby(), make_event("settings_changed", {"trust_mode": bad})) == (
        "invalid_setting"
    )


def test_settings_changed_null_value_is_invalid_setting_not_absent() -> None:
    event = make_event("settings_changed", {"starting_life": None})
    assert reject_code(lobby(), event) == "invalid_setting"


def test_settings_changed_one_bad_key_applies_no_key() -> None:
    event = make_event("settings_changed", {"starting_life": 20, "poison_threshold": 0})
    assert reject_code(seated(2), event) == "invalid_setting"


def test_settings_changed_after_game_started_is_invalid_phase_even_with_a_bad_value() -> None:
    state = active_game([1, 2], 1, 1)
    assert reject_code(state, make_event("settings_changed", {"trust_mode": "strict"})) == (
        "invalid_phase"
    )
    assert reject_code(state, make_event("settings_changed", {"trust_mode": "x"})) == (
        "invalid_phase"
    )


# --- seat_joined ----------------------------------------------------------------------------------


def test_seat_joined_adds_a_seat_with_starting_life_and_appends_to_order() -> None:
    state = lobby()
    for seat_id, player in ((3, "p3"), (1, None), (2, "p2")):
        state = accept(
            state, make_event("seat_joined", {"seat": seat_id, "player_id": player, "name": "N"})
        )
    assert _ids(state) == [1, 2, 3]
    assert state.order == [3, 1, 2]
    seat = state.seats[0]
    assert (seat.player_id, seat.name, seat.life, seat.deck_version_id) == (None, "N", 40, None)
    assert seat.commanders[0].model_dump() == {"card_id": None, "name": ""}
    assert seat.commander_cast == [0]
    assert (seat.poison, seat.energy, seat.experience) == (0, 0, 0)


def test_seat_joined_uses_the_current_starting_life() -> None:
    state = accept(lobby(), make_event("settings_changed", {"starting_life": 30}))
    state = accept(state, make_event("seat_joined", {"seat": 2, "player_id": "p", "name": ""}))
    assert state.seats[0].life == 30


@pytest.mark.parametrize(
    "payload",
    [
        {"player_id": None, "name": "A"},
        {"seat": 1, "name": "A"},
        {"seat": 1, "player_id": None},
        {"seat": 0, "player_id": None, "name": "A"},
        {"seat": 9, "player_id": None, "name": "A"},
        {"seat": "1", "player_id": None, "name": "A"},
        {"seat": True, "player_id": None, "name": "A"},
        {"seat": 1, "player_id": "", "name": "A"},
        {"seat": 1, "player_id": 5, "name": "A"},
        {"seat": 1, "player_id": None, "name": None},
    ],
)
def test_seat_joined_rejects_a_bad_payload(payload: dict[str, Any]) -> None:
    assert reject_code(lobby(), make_event("seat_joined", payload)) == "invalid_payload"


def test_seat_joined_table_full_comes_before_seat_taken() -> None:
    state = seated(8)
    assert reject_code(
        state, make_event("seat_joined", {"seat": 3, "player_id": None, "name": ""})
    ) == ("table_full")


def test_seat_joined_seat_taken_and_phase_order() -> None:
    state = seated(2)
    joined = make_event("seat_joined", {"seat": 2, "player_id": None, "name": ""})
    assert reject_code(state, joined) == "seat_taken"
    started = active_game([1, 2], 1, 1)
    assert reject_code(started, joined) == "invalid_phase"
    assert reject_code(started, make_event("seat_joined", {})) == "invalid_payload"


def test_seat_joined_allows_the_same_player_in_two_seats() -> None:
    state = accept(seated(1), make_event("seat_joined", {"seat": 2, "player_id": "p1", "name": ""}))
    assert [s.player_id for s in state.seats] == ["p1", "p1"]


# --- seat_left ------------------------------------------------------------------------------------


def test_seat_left_removes_the_seat_and_keeps_the_rest_in_order() -> None:
    state = seated(4).model_copy(update={"order": [3, 1, 4, 2]})
    state = accept(state, make_event("seat_left", {"seat": 4}))
    assert _ids(state) == [1, 2, 3]
    assert state.order == [3, 1, 2]


def test_seat_left_id_can_be_reused_as_a_new_seat() -> None:
    state = seated(2)
    state = accept(state, make_event("seat_left", {"seat": 1}))
    state = accept(state, make_event("seat_joined", {"seat": 1, "player_id": "new", "name": "N"}))
    assert _ids(state) == [1, 2]
    assert state.order == [2, 1]


def test_seat_left_rejections_in_order() -> None:
    assert reject_code(seated(2), make_event("seat_left", {})) == "invalid_payload"
    assert reject_code(seated(2), make_event("seat_left", {"seat": 9})) == "invalid_payload"
    assert reject_code(seated(2), make_event("seat_left", {"seat": 5})) == "unknown_seat"
    started = active_game([1, 2], 1, 1)
    assert reject_code(started, make_event("seat_left", {"seat": 1})) == "invalid_phase"
    assert reject_code(started, make_event("seat_left", {"seat": 7})) == "invalid_phase"


# --- seat_moved -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("move", "expected"),
    [((4, 2), [1, 4, 3, 2]), ((1, 2), [2, 1, 3, 4]), ((3, 3), [1, 2, 3, 4])],
)
def test_seat_moved_exchanges_two_positions_in_order(
    move: tuple[int, int], expected: list[int]
) -> None:
    state = accept(seated(4), make_event("seat_moved", {"from": move[0], "to": move[1]}))
    assert state.order == expected
    assert state == seated(4).model_copy(update={"order": expected, "last_seq": 1})


def test_seat_moved_correction_keeps_the_active_seat_and_the_counters() -> None:
    state = active_game([1, 2, 3, 4], 1, 3, turn=3)
    moved = accept(state, make_event("seat_moved", {"from": 4, "to": 2, "correction": True}))
    assert moved.order == [1, 4, 3, 2]
    assert (moved.active_seat, moved.turn, moved.round, moved.first_seat) == (3, 3, 1, 1)
    assert moved.seats == state.seats
    # the next pass reads the new order: 3 is followed by 2
    assert accept(moved, make_event("turn_passed")).active_seat == 2


def test_seat_moved_in_an_active_game_needs_correction_true() -> None:
    state = active_game([1, 2, 3, 4], 1, 3)
    for payload in ({"from": 4, "to": 2}, {"from": 4, "to": 2, "correction": False}):
        assert reject_code(state, make_event("seat_moved", payload)) == "invalid_phase"
    null = {"from": 4, "to": 2, "correction": None}
    assert reject_code(state, make_event("seat_moved", null)) == "invalid_phase"


def test_seat_moved_correction_is_accepted_in_the_lobby_too() -> None:
    state = accept(seated(2), make_event("seat_moved", {"from": 1, "to": 2, "correction": True}))
    assert state.order == [2, 1]


@pytest.mark.parametrize("phase", ["ending", "finished", "abandoned"])
def test_seat_moved_is_invalid_phase_in_every_other_phase(phase: str) -> None:
    state = active_game([1, 2], 1, 1).model_copy(update={"phase": phase})
    event = make_event("seat_moved", {"from": 1, "to": 2, "correction": True})
    assert reject_code(state, event) == "invalid_phase"


@pytest.mark.parametrize(
    "payload",
    [
        {"to": 2},
        {"from": 1},
        {"from": 0, "to": 2},
        {"from": 1, "to": 9},
        {"from": "1", "to": 2},
        {"from": 1, "to": 2, "correction": "yes"},
        {"from": 1, "to": 2, "correction": 1},
    ],
)
def test_seat_moved_rejects_a_bad_payload_before_the_phase(payload: dict[str, Any]) -> None:
    assert reject_code(active_game([1, 2], 1, 1), make_event("seat_moved", payload)) == (
        "invalid_payload"
    )


def test_seat_moved_checks_from_then_to_for_unknown_seats() -> None:
    state = seated(2)
    assert reject_code(state, make_event("seat_moved", {"from": 3, "to": 4})) == "unknown_seat"
    assert reject_code(state, make_event("seat_moved", {"from": 1, "to": 4})) == "unknown_seat"


# --- deck_assigned and commanders_set -------------------------------------------------------------


def test_deck_assigned_sets_deck_replaces_commanders_and_zeroes_the_cast_counts() -> None:
    state = accept(
        seated(2),
        make_event(
            "deck_assigned",
            {
                "seat": 2,
                "deck_version_id": "d1",
                "commanders": [CMD, {"card_id": None, "name": "B"}],
            },
        ),
    )
    seat = state.seats[1]
    assert seat.deck_version_id == "d1"
    assert [c.model_dump() for c in seat.commanders] == [CMD, {"card_id": None, "name": "B"}]
    assert seat.commander_cast == [0, 0]
    assert state.seats[0] == seated(2).seats[0]


def test_commanders_set_keeps_the_deck_and_resets_the_cast_counts() -> None:
    base = accept(
        seated(2),
        make_event("deck_assigned", {"seat": 1, "deck_version_id": "d1", "commanders": [CMD, CMD]}),
    )
    state = accept(base, make_event("commanders_set", {"seat": 1, "commanders": [CMD]}))
    assert state.seats[0].deck_version_id == "d1"
    assert len(state.seats[0].commanders) == 1
    assert state.seats[0].commander_cast == [0]


def test_commander_items_ignore_extra_keys_and_keep_array_order() -> None:
    event = make_event(
        "commanders_set",
        {"seat": 1, "commanders": [{"card_id": "z", "name": "Z", "extra": 1}, CMD]},
    )
    state = accept(seated(1), event)
    assert [c.model_dump() for c in state.seats[0].commanders] == [
        {"card_id": "z", "name": "Z"},
        CMD,
    ]


@pytest.mark.parametrize(
    "commanders",
    [
        [],
        [CMD, CMD, CMD],
        {"card_id": "c", "name": "n"},
        "x",
        None,
        [5],
        [{"name": "n"}],
        [{"card_id": "c"}],
        [{"card_id": "", "name": "n"}],
        [{"card_id": 1, "name": "n"}],
        [{"card_id": "c", "name": None}],
    ],
)
@pytest.mark.parametrize("event_type", ["deck_assigned", "commanders_set"])
def test_commanders_payload_rejections(event_type: str, commanders: Any) -> None:
    payload = {"seat": 1, "deck_version_id": "d", "commanders": commanders}
    assert reject_code(seated(1), make_event(event_type, payload)) == "invalid_payload"


@pytest.mark.parametrize("deck", [None, "", 3])
def test_deck_assigned_needs_a_non_empty_deck_version_id(deck: Any) -> None:
    payload = {"seat": 1, "deck_version_id": deck, "commanders": [CMD]}
    assert reject_code(seated(1), make_event("deck_assigned", payload)) == "invalid_payload"
    # commanders_set has no deck key to check
    assert accept(seated(1), make_event("commanders_set", {"seat": 1, "commanders": [CMD]}))


@pytest.mark.parametrize("event_type", ["deck_assigned", "commanders_set"])
def test_deck_and_commander_events_check_phase_before_seat(event_type: str) -> None:
    payload = {"seat": 5, "deck_version_id": "d", "commanders": [CMD]}
    assert reject_code(seated(2), make_event(event_type, payload)) == "unknown_seat"
    started = active_game([1, 2], 1, 1)
    assert reject_code(started, make_event(event_type, payload)) == "invalid_phase"


# --- game_started ---------------------------------------------------------------------------------


def test_game_started_activates_the_first_seat_at_starting_life() -> None:
    state = seated(4)
    state = state.model_copy(
        update={"seats": [s.model_copy(update={"life": 17}) for s in state.seats]}
    )
    started = accept(state, make_event("game_started", {"first_seat": 3, "started_at": "T"}))
    assert (started.phase, started.active_seat, started.first_seat) == ("active", 3, 3)
    assert (started.turn, started.round, started.started_at) == (1, 1, "T")
    assert [s.life for s in started.seats] == [40, 40, 40, 40]
    assert started.order == [1, 2, 3, 4]
    assert started.settings == state.settings


@pytest.mark.parametrize(
    "payload",
    [
        {"started_at": "T"},
        {"first_seat": 1},
        {"first_seat": 1, "started_at": None},
        {"first_seat": 1, "started_at": 5},
        {"first_seat": 0, "started_at": "T"},
        {"first_seat": "1", "started_at": "T"},
    ],
)
def test_game_started_rejects_a_bad_payload(payload: dict[str, Any]) -> None:
    assert reject_code(seated(2), make_event("game_started", payload)) == "invalid_payload"


def test_game_started_accepts_any_string_as_started_at() -> None:
    for text in ("", "not a time", "2026-11-21T12:00:00.000Z"):
        state = accept(seated(2), make_event("game_started", {"first_seat": 1, "started_at": text}))
        assert state.started_at == text


def test_game_started_with_one_seat_is_not_enough_players() -> None:
    event = make_event("game_started", {"first_seat": 1, "started_at": "T"})
    assert reject_code(seated(1), event) == "not_enough_players"


def test_game_started_unknown_seat_comes_before_not_enough_players() -> None:
    event = make_event("game_started", {"first_seat": 3, "started_at": "T"})
    assert reject_code(seated(1), event) == "unknown_seat"
    assert reject_code(lobby(), event) == "unknown_seat"
    assert reject_code(seated(2), event) == "unknown_seat"


def test_game_started_twice_is_invalid_phase() -> None:
    event = make_event("game_started", {"first_seat": 1, "started_at": "T"})
    assert reject_code(active_game([1, 2], 1, 1), event) == "invalid_phase"


# --- turn_passed ----------------------------------------------------------------------------------


def test_turn_passed_outside_active_is_invalid_phase() -> None:
    assert reject_code(seated(2), make_event("turn_passed")) == "invalid_phase"
    ended = active_game([1, 2], 1, 1).model_copy(update={"phase": "ending"})
    assert reject_code(ended, make_event("turn_passed")) == "invalid_phase"


def test_turn_passed_with_every_seat_eliminated_is_invalid_phase() -> None:
    assert reject_code(active_game([1, 2], 1, 1, out=(1, 2)), make_event("turn_passed")) == (
        "invalid_phase"
    )


def test_turn_passed_ignores_payload_keys() -> None:
    state = accept(active_game([1, 2], 1, 1), make_event("turn_passed", {"junk": 1}))
    assert state.active_seat == 2


def test_turn_passed_skips_an_eliminated_seat_and_wraps_the_round() -> None:
    state = active_game([1, 2, 3, 4], 1, 3, turn=3, out=(4,))
    passed = accept(state, make_event("turn_passed"))
    assert (passed.active_seat, passed.turn, passed.round) == (1, 4, 2)
    assert passed.first_seat == 1


def test_a_full_game_from_events_follows_first_seat() -> None:
    state = initial_state(GAME_ID, DEFAULT_SETTINGS)
    for seat_id in (1, 2, 3, 4):
        state = accept(
            state, make_event("seat_joined", {"seat": seat_id, "player_id": None, "name": ""})
        )
    state = accept(state, make_event("game_started", {"first_seat": 3, "started_at": "T"}))
    seen = []
    for _ in range(5):
        state = accept(state, make_event("turn_passed"))
        seen.append((state.active_seat, state.turn, state.round))
    assert seen == [(4, 2, 1), (1, 3, 1), (2, 4, 1), (3, 5, 2), (4, 6, 2)]


# The 19 rows of the table in CONTRACT.md 4.4.10: (order, F, eliminated, a, n, before, after).
Row = tuple[list[int], int, tuple[int, ...], int, int, tuple[int, int], tuple[int, int]]
TABLE: list[Row] = [
    ([1, 2, 3, 4], 1, (), 1, 2, (1, 1), (2, 1)),
    ([1, 2, 3, 4], 1, (), 4, 1, (4, 1), (5, 2)),
    ([1, 2, 3, 4], 3, (), 3, 4, (1, 1), (2, 1)),
    ([1, 2, 3, 4], 3, (), 4, 1, (2, 1), (3, 1)),
    ([1, 2, 3, 4], 3, (), 2, 3, (4, 1), (5, 2)),
    ([1, 2, 3, 4], 3, (), 2, 3, (12, 3), (13, 4)),
    ([1, 2, 3, 4], 1, (4,), 3, 1, (3, 1), (4, 2)),
    ([1, 2, 3, 4], 3, (4,), 3, 1, (1, 1), (2, 1)),
    ([1, 2, 3, 4], 1, (1,), 2, 3, (2, 1), (3, 1)),
    ([1, 2, 3, 4], 1, (1,), 4, 2, (4, 1), (5, 2)),
    ([1, 2, 3, 4], 1, (1,), 1, 2, (1, 1), (2, 2)),
    ([1, 2, 3, 4], 3, (3,), 2, 4, (4, 1), (5, 2)),
    ([1, 2, 3, 4], 4, (4,), 3, 1, (4, 1), (5, 2)),
    ([3, 2, 1, 4], 1, (), 4, 3, (2, 1), (3, 1)),
    ([1, 2, 3, 4], 1, (2, 3, 4), 1, 1, (1, 1), (2, 2)),
    ([1, 2, 3, 4], 1, (1, 2, 3), 4, 4, (4, 3), (5, 4)),
]


@pytest.mark.parametrize("row", TABLE, ids=[f"row-{i}" for i in range(1, len(TABLE) + 1)])
def test_turn_passed_worked_examples_rows_1_to_16(row: Row) -> None:
    order, first, out, active, expected, before, after = row
    state = active_game(order, first, active, turn=before[0], round_=before[1], out=out)
    passed = accept(state, make_event("turn_passed"))
    assert (passed.active_seat, passed.turn, passed.round) == (expected, *after)
    assert passed.first_seat == first
    assert passed.order == order


@pytest.mark.parametrize(
    ("start_active", "start_turn", "move", "expected", "after"),
    [
        (2, 2, (1, 3), 1, (3, 2)),  # row 17: [3,2,1,4]
        (3, 3, (3, 2), 2, (4, 1)),  # row 18: [1,3,2,4]
        (3, 3, (1, 4), 1, (4, 2)),  # row 19: [4,2,3,1]
    ],
    ids=["row-17", "row-18", "row-19"],
)
def test_turn_passed_worked_examples_rows_17_to_19_after_a_correction(
    start_active: int,
    start_turn: int,
    move: tuple[int, int],
    expected: int,
    after: tuple[int, int],
) -> None:
    state = active_game([1, 2, 3, 4], 1, start_active, turn=start_turn)
    state = accept(
        state, make_event("seat_moved", {"from": move[0], "to": move[1], "correction": True})
    )
    assert (state.active_seat, state.turn, state.round, state.first_seat) == (
        start_active,
        start_turn,
        1,
        1,
    )
    passed = accept(state, make_event("turn_passed"))
    assert (passed.active_seat, passed.turn, passed.round) == (expected, *after)


def test_the_orders_of_rows_17_to_19_are_the_ones_the_contract_gives() -> None:
    for start_active, move, order in (
        (2, (1, 3), [3, 2, 1, 4]),
        (3, (3, 2), [1, 3, 2, 4]),
        (3, (1, 4), [4, 2, 3, 1]),
    ):
        state = active_game([1, 2, 3, 4], 1, start_active)
        moved = accept(
            state, make_event("seat_moved", {"from": move[0], "to": move[1], "correction": True})
        )
        assert moved.order == order


def test_a_handler_never_mutates_its_input_state() -> None:
    state = seated(3)
    snapshot = state.model_dump(mode="json")
    for event in (
        make_event("seat_moved", {"from": 1, "to": 3}),
        make_event("seat_left", {"seat": 2}),
        make_event("settings_changed", {"starting_life": 10}),
        make_event("commanders_set", {"seat": 1, "commanders": [CMD]}),
        make_event("game_started", {"first_seat": 1, "started_at": "T"}),
    ):
        accept(state, event)
    assert state.model_dump(mode="json") == snapshot
