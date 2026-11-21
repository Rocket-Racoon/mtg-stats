"""Unit tests of the canonical serializer and the wire parser (CONTRACT.md section 5)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.games.core import CanonicalJsonError, WireParseError, canonical_bytes, canonical_json
from app.games.core.canonical import MAX_DEPTH, MAX_SAFE_INTEGER, loads_wire


def test_bool_is_written_as_a_literal_not_an_integer() -> None:
    assert canonical_json([True, False]) == "[true,false]"


def test_float_is_rejected_even_when_integral() -> None:
    for value in (1.0, 1.5, float("nan"), float("inf"), -float("inf")):
        with pytest.raises(CanonicalJsonError) as raised:
            canonical_json(value)
        assert raised.value.code == "non_integer"


def test_integer_range_is_inclusive_at_the_safe_limit() -> None:
    assert canonical_json(MAX_SAFE_INTEGER) == "9007199254740991"
    assert canonical_json(-MAX_SAFE_INTEGER) == "-9007199254740991"
    for value in (MAX_SAFE_INTEGER + 1, -MAX_SAFE_INTEGER - 1):
        with pytest.raises(CanonicalJsonError) as raised:
            canonical_json(value)
        assert raised.value.code == "integer_out_of_range"


def test_lone_surrogate_is_rejected_in_values_and_keys() -> None:
    for value in ("\ud800", "a\udfffb", {"\ud83d": 1}, ["x", "\udc00"]):
        with pytest.raises(CanonicalJsonError) as raised:
            canonical_json(value)
        assert raised.value.code == "lone_surrogate"


def test_valid_surrogate_pair_as_one_code_point_is_written_raw() -> None:
    assert canonical_json("\U0001f600") == '"\U0001f600"'
    assert canonical_bytes("\U0001f600") == b'"\xf0\x9f\x98\x80"'


@pytest.mark.parametrize(
    "value", [(1, 2), {1, 2}, b"x", Decimal(1), object(), {1: 2}, {None: 1}, lambda: 1]
)
def test_unsupported_values_are_rejected(value: Any) -> None:
    with pytest.raises(CanonicalJsonError) as raised:
        canonical_json(value)
    assert raised.value.code == "unsupported_value"


def _nest(ancestors_of_innermost: int) -> list[Any]:
    value: list[Any] = []
    for _ in range(ancestors_of_innermost):
        value = [value]
    return value


def test_depth_limit_counts_ancestors() -> None:
    # The innermost container of _nest(n) has n ancestors; 64 is allowed, 65 is not.
    assert canonical_json(_nest(MAX_DEPTH)) == "[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1)
    with pytest.raises(CanonicalJsonError) as raised:
        canonical_json(_nest(MAX_DEPTH + 1))
    assert raised.value.code == "unsupported_value"


def test_cyclic_value_is_rejected_not_recursed_forever() -> None:
    loop: list[Any] = []
    loop.append(loop)
    with pytest.raises(CanonicalJsonError):
        canonical_json(loop)


def test_control_characters_use_short_or_lowercase_u_escapes() -> None:
    assert (
        canonical_json("\x00\x1f\x0b\b\t\n\f\r\x7f") == '"\\u0000\\u001f\\u000b\\b\\t\\n\\f\\r\x7f"'
    )


def test_keys_sort_by_code_point() -> None:
    tilde = chr(0xFF5E)  # sorts before U+10000 by code point, after it by UTF-16 code unit
    astral = chr(0x10000)
    expected = f'{{"":4,"a":3,"ab":5,"{tilde}":2,"{astral}":1}}'
    assert canonical_json({astral: 1, tilde: 2, "a": 3, "": 4, "ab": 5}) == expected


def test_wire_parse_accepts_plain_integers_and_structures() -> None:
    assert loads_wire('{"a":[1,-2,0,null,true,"x"]}') == {"a": [1, -2, 0, None, True, "x"]}
    assert loads_wire(b'{"a":9007199254740991}') == {"a": MAX_SAFE_INTEGER}


@pytest.mark.parametrize(
    "text",
    [
        "3.0",
        "[1e2]",
        "[1E2]",
        "[-0.5]",
        "[9007199254740992]",
        "[-9007199254740992]",
        "[NaN]",
        "[Infinity]",
        "[-Infinity]",
        '{"a":1,"a":2}',
        '{"a":{"b":1,"b":1}}',
        "{",
        "",
        "1" * 5000,
    ],
)
def test_wire_parse_rejects_floats_big_integers_duplicates_and_malformed_text(text: str) -> None:
    with pytest.raises(WireParseError) as raised:
        loads_wire(text)
    assert raised.value.code == "invalid_event"


def test_wire_parse_rejects_bytes_that_are_not_utf8() -> None:
    with pytest.raises(WireParseError):
        loads_wire(b"\xff\xfe")


class _OddInt(int):
    def __str__(self) -> str:
        return "forged"


class _Odd(dict):  # type: ignore[type-arg]
    pass


def test_subclasses_of_json_types_are_unsupported_values() -> None:
    # Exact types only (sections 5.1, 5.4): an overridden __str__ or __getitem__ cannot leak.
    for value in (_OddInt(7), _Odd(a=1), ["x", _Odd()]):
        with pytest.raises(CanonicalJsonError) as raised:
            canonical_json(value)
        assert raised.value.code == "unsupported_value"


def test_integers_are_always_written_as_plain_decimal() -> None:
    assert canonical_json([0, -1, 10**15]) == "[0,-1,1000000000000000]"


_BS = chr(92)  # a backslash: these texts hold JSON escape sequences, not surrogate characters


def _esc(*units: str) -> str:
    return "".join(_BS + "u" + unit for unit in units)


@pytest.mark.parametrize(
    "text",
    [
        '"' + _esc("d800") + '"',
        '"' + _esc("dc00") + '"',
        '"a' + _esc("d83d") + 'z"',
        '"' + _esc("de00", "d83d") + '"',  # low before high
        '"' + _esc("d83d", "d83d") + '"',  # two highs
        '{"' + _esc("d800") + '":1}',
        '["x",{"k":["' + _esc("dfff") + '"]}]',
    ],
)
def test_wire_reader_rejects_a_lone_surrogate_escape_anywhere(text: str) -> None:
    with pytest.raises(WireParseError) as raised:
        loads_wire(text)
    assert raised.value.code == "invalid_event"


def test_wire_reader_accepts_a_valid_surrogate_pair_escape() -> None:
    assert loads_wire('"' + _esc("d83d", "de00") + '"') == "😀"
