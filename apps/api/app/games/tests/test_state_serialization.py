"""``GameState.to_json_value`` writes stored values exactly as stored (S-6.1.6 review follow-up).

``initial_state`` stores unvalidated settings (CONTRACT.md 3.5) and ``seat_joined`` /
``game_started`` / ``settings_changed`` copy ``starting_life`` into every seat's ``life``. The
typed pydantic serializer turned a stored ``True`` into ``1``; the TypeScript state keeps ``true``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.games.core import (
    ApplyOk,
    CanonicalJsonError,
    GameState,
    apply,
    canonical_json,
    initial_state,
)
from app.games.core.contract import DEFAULT_SETTINGS, Seat, Settings
from app.games.tests.lobby_helpers import GAME_ID, accept, make_event
from app.games.tests.repo_files import VECTORS_DIR, require
from app.games.tests.test_state_shape import UNVALIDATED as UNVALIDATED_VECTORS

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


def _vector_states() -> list[tuple[str, GameState]]:
    """The initial state and every state reached by every valid-settings reducer vector."""
    found: list[tuple[str, GameState]] = []
    for path in sorted(VECTORS_DIR.glob("*.json")):
        vector = json.loads(path.read_text(encoding="utf-8"))
        if vector["kind"] != "reducer" or path.stem in UNVALIDATED_VECTORS:
            continue
        try:  # newer vectors (rejections, which have no final state) are not on that list
            Settings.model_validate({**DEFAULT_SETTINGS, **vector["settings"]})
        except ValidationError:
            continue
        state = initial_state(vector["game_id"], vector["settings"])
        found.append((f"{path.stem}#initial", state))
        for index, raw in enumerate(vector["events"]):
            result = apply(state, {"game_id": vector["game_id"], **raw})
            if not isinstance(result, ApplyOk):
                break
            state = result.state
            found.append((f"{path.stem}#{index}", state))
    return found


def test_valid_states_serialise_to_the_same_bytes_as_the_typed_dump() -> None:
    """``==`` would hide the fix (``True == 1``), so compare canonical bytes, plus a round trip."""
    require(VECTORS_DIR)
    states = _vector_states()
    assert len(states) > 1000
    for name, state in states:
        plain = canonical_json(state.to_json_value())
        assert plain == canonical_json(state.model_dump(mode="json")), name
        assert canonical_json(GameState.model_validate(state.to_json_value()).to_json_value()) == (
            plain
        ), name


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
