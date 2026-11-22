"""The Python serializer accepts exact built-in types only (review decision), and a non-string
payload key is ``invalid_payload``. CONTRACT.md 5.4 lists "a class instance" as
``unsupported_value`` but does not mention subclasses of ``int``, ``str``, ``list`` or ``dict``;
the TypeScript serializer rejects class instances too but accepts an ``Array`` subclass
(``contract-review.test.ts``).
"""

from __future__ import annotations

from collections import OrderedDict, UserDict, UserList
from enum import IntEnum, StrEnum
from typing import Any

import pytest

from app.games.core import (
    ApplyError,
    ApplyOk,
    CanonicalJsonError,
    GameState,
    apply,
    canonical_json,
    initial_state,
)


class MyStr(str):
    __slots__ = ()


class MyInt(int):
    __slots__ = ()


class MyList(list[Any]):
    pass


class MyDict(dict[Any, Any]):
    pass


class MyFloat(float):
    pass


class Color(StrEnum):
    RED = "red"


class Level(IntEnum):
    ONE = 1


def code_of(value: Any) -> str:
    with pytest.raises(CanonicalJsonError) as raised:
        canonical_json(value)
    return str(raised.value.code)


@pytest.mark.parametrize(
    "value",
    [
        MyStr("x"),
        MyInt(1),
        MyList([1]),
        MyDict(a=1),
        Color.RED,
        Level.ONE,
        OrderedDict(a=1),
        UserList([1]),
        UserDict(a=1),
    ],
    ids=lambda v: type(v).__name__,
)
def test_subclasses_and_wrappers_are_unsupported_values_at_any_depth(value: Any) -> None:
    assert code_of(value) == "unsupported_value"
    assert code_of([value]) == "unsupported_value"
    assert code_of({"a": [{"b": value}]}) == "unsupported_value"


def test_a_str_subclass_as_a_dict_key_is_unsupported() -> None:
    assert code_of({MyStr("k"): 1}) == "unsupported_value"


def test_a_float_subclass_is_still_a_non_integer() -> None:
    assert code_of(MyFloat(1.0)) == "non_integer"


def test_subclasses_cannot_change_the_bytes_by_overriding_dunders() -> None:
    class Sneaky(str):
        def __str__(self) -> str:
            return "other"

    assert code_of(Sneaky("x")) == "unsupported_value"


def test_exact_built_ins_are_still_accepted() -> None:
    assert (
        canonical_json({"a": [1, "x", None, True, {"b": []}]}) == '{"a":[1,"x",null,true,{"b":[]}]}'
    )
    assert canonical_json(True) == "true"
    assert canonical_json(0) == "0"


# --- through apply: the payload ------------------------------------------------------------------

GAME_ID = "018f0000-0000-7000-8000-000000000001"


def event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event_id": "00000000-0000-4000-8000-000000000001",
        "game_id": GAME_ID,
        "type": "seat_claimed",
        "payload": {},
        "actor": {"kind": "player", "player_id": "p1"},
        "on_behalf_of_seat": None,
        "v": 1,
    }
    base.update(overrides)
    return base


def code(state: GameState, raw: Any) -> str:
    result = apply(state, raw)
    assert isinstance(result, ApplyError), result
    return str(result.error.code)


@pytest.mark.parametrize(
    "payload",
    [
        {1: "x"},
        {None: 1},
        {True: 1},
        {b"k": 1},
        {(1, 2): 1},
        {MyStr("k"): 1},
        {"a": {2: 3}},
        {"a": [{4: 5}]},
    ],
    ids=repr,
)
def test_a_non_string_payload_key_is_invalid_payload(payload: dict[Any, Any]) -> None:
    assert code(initial_state(GAME_ID), event(payload=payload)) == "invalid_payload"


@pytest.mark.parametrize(
    "payload",
    [{"a": MyStr("x")}, {"a": MyInt(1)}, {"a": MyList()}, {"a": MyDict()}, {"a": Color.RED}],
    ids=lambda p: type(p["a"]).__name__,
)
def test_a_subclass_value_in_a_payload_is_invalid_payload(payload: dict[str, Any]) -> None:
    assert code(initial_state(GAME_ID), event(payload=payload)) == "invalid_payload"


def test_a_dict_subclass_as_the_whole_payload_is_copied_into_a_plain_dict_and_accepted() -> None:
    # Pinned current behaviour, reported to the coordinator: the envelope model copies the payload
    # into a plain dict before the canonical test, so "exact types only" applies below the
    # payload, not to the payload object itself. 5.4 does not say which is right.
    state = initial_state(GAME_ID)
    result = apply(state, event(payload=MyDict(a=1)))
    assert isinstance(result, ApplyOk)
