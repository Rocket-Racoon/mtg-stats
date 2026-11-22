"""``GameState.to_json_value`` writes stored values exactly as stored (S-6.1.6 review follow-up).

``initial_state`` stores unvalidated settings (CONTRACT.md 3.5) and ``seat_joined`` /
``game_started`` / ``settings_changed`` copy ``starting_life`` into every seat's ``life``. The
typed pydantic serializer turned a stored ``True`` into ``1``; the TypeScript state keeps ``true``.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.games.core import CanonicalJsonError, GameState, canonical_json, initial_state
from app.games.core.contract import Seat
from app.games.tests.lobby_helpers import GAME_ID, accept, make_event

# (id, stored value, canonical JSON text of that value)
UNVALIDATED: list[tuple[str, Any, str]] = [
    ("true", True, "true"),
    ("false", False, "false"),
    ("string", "40", '"40"'),
    ("empty-string", "", '""'),
    ("null", None, "null"),
    ("zero", 0, "0"),
    ("negative", -5, "-5"),
    ("above-range", 1000, "1000"),
    ("list", [1, True, None], "[1,true,null]"),
    ("dict", {"a": True, "b": [False]}, '{"a":true,"b":[false]}'),
]
IDS = [u[0] for u in UNVALIDATED]


def _join(state: GameState, seat: int) -> GameState:
    return accept(state, make_event("seat_joined", {"seat": seat, "player_id": None, "name": "x"}))


@pytest.mark.parametrize(("_name", "value", "text"), UNVALIDATED, ids=IDS)
def test_an_unvalidated_starting_life_is_serialised_as_stored(
    _name: str, value: Any, text: str
) -> None:
    state = initial_state(GAME_ID, {"starting_life": value})
    plain = state.to_json_value()
    assert type(plain["settings"]["starting_life"]) is type(value)
    assert plain["settings"]["starting_life"] == value
    assert f'"starting_life":{text}' in canonical_json(plain)


@pytest.mark.parametrize(("_name", "value", "text"), UNVALIDATED, ids=IDS)
def test_the_life_a_seat_copies_from_an_unvalidated_setting_is_serialised_as_stored(
    _name: str, value: Any, text: str
) -> None:
    state = _join(_join(initial_state(GAME_ID, {"starting_life": value}), 1), 2)
    plain = state.to_json_value()
    for seat in plain["seats"]:
        assert type(seat["life"]) is type(value)
        assert seat["life"] == value
    assert canonical_json(plain).count(f'"life":{text}') == 2


@pytest.mark.parametrize(("_name", "value", "text"), UNVALIDATED, ids=IDS)
def test_game_started_copies_an_unvalidated_life_and_it_is_serialised_as_stored(
    _name: str, value: Any, text: str
) -> None:
    state = _join(_join(initial_state(GAME_ID, {"starting_life": value}), 1), 2)
    started = accept(state, make_event("game_started", {"first_seat": 1, "started_at": "x"}))
    assert canonical_json(started.to_json_value()).count(f'"life":{text}') == 2


def test_a_stored_float_is_returned_as_stored_and_canonical_json_refuses_it() -> None:
    plain = initial_state(GAME_ID, {"starting_life": 1.5}).to_json_value()
    assert plain["settings"]["starting_life"] == 1.5
    assert isinstance(plain["settings"]["starting_life"], float)
    with pytest.raises(CanonicalJsonError):
        canonical_json(plain)


def test_every_unvalidated_setting_type_is_kept() -> None:
    state = initial_state(
        GAME_ID,
        {
            "starting_life": True,
            "commander_damage_threshold": False,
            "poison_threshold": None,
            "trust_mode": "STRICT",
        },
    )
    assert state.to_json_value()["settings"] == {
        "starting_life": True,
        "commander_damage_threshold": False,
        "poison_threshold": None,
        "trust_mode": "STRICT",
    }
    assert (
        '"settings":{"commander_damage_threshold":false,"poison_threshold":null,'
        '"starting_life":true,"trust_mode":"STRICT"}'
    ) in canonical_json(state.to_json_value())


def test_a_boolean_is_not_turned_into_an_integer_for_a_seat_life_set_by_hand() -> None:
    state = _join(initial_state(GAME_ID), 1)
    seats = [s.model_copy(update={"life": True}) for s in state.seats]
    plain = state.model_copy(update={"seats": seats}).to_json_value()
    assert plain["seats"][0]["life"] is True


def test_valid_states_keep_the_model_field_order_at_every_level() -> None:
    state = _join(_join(initial_state(GAME_ID), 2), 1)
    plain = state.to_json_value()
    assert list(plain) == list(GameState.model_fields)
    assert list(plain["settings"]) == [
        "starting_life",
        "commander_damage_threshold",
        "poison_threshold",
        "trust_mode",
    ]
    assert list(plain["seats"][0]) == list(Seat.model_fields)
    assert list(plain["seats"][0]["commanders"][0]) == ["card_id", "name"]


def test_valid_states_serialise_the_same_as_the_typed_dump() -> None:
    state = _join(_join(initial_state(GAME_ID), 1), 2)
    assert state.to_json_value() == state.model_dump(mode="json")


def test_the_result_never_aliases_the_state() -> None:
    state = _join(initial_state(GAME_ID, {"starting_life": [1, 2]}), 1)
    plain = state.to_json_value()
    plain["settings"]["starting_life"].append(3)
    plain["seats"][0]["commanders"].append({"card_id": None, "name": "y"})
    plain["order"].append(9)
    stored: Any = state.settings.starting_life
    assert stored == [1, 2]
    assert len(state.seats[0].commanders) == 1
    assert state.order == [1]
