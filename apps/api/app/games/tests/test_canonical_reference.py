"""Acceptance criterion 2 (S-6.1.1), Python side: byte-exact canonical JSON.

The serializer is compared with an independent reference (``json.dumps`` configured per
CONTRACT.md section 5), on generated values and on the expected outputs of the shared vectors,
without reading anything from the TypeScript side. Also: integers only, and no dependence on the
process locale.
"""

# ruff: noqa: S311  (seeded pseudo-random generation of test values, not security)
from __future__ import annotations

import json
import locale
import random
from collections.abc import Iterator
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest

from app.games.core import (
    CanonicalJsonError,
    GameState,
    WireParseError,
    canonical_bytes,
    canonical_json,
    loads_wire,
)
from app.games.tests.repo_files import VECTORS_DIR, load_vector, require


def reference(value: Any) -> str:
    """Independent reference: Python's own JSON writer, sorted by code point, no escaping of
    non-ASCII, compact separators. Valid for the values generated here (integers, valid text)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


RANGES = [
    (0x00, 0x1F),
    (0x20, 0x7E),
    (0x7F, 0x7F),
    (0x80, 0x9F),
    (0xA0, 0x7FF),
    (0x800, 0xD7FF),
    (0xE000, 0xFFFF),
    (0x10000, 0x10FFFF),
]


def random_string(rand: random.Random, max_len: int) -> str:
    chars: list[str] = []
    for _ in range(rand.randint(0, max_len)):
        if rand.random() < 0.1:
            chars.append(rand.choice('"\\/'))
        else:
            low, high = rand.choice(RANGES)
            chars.append(chr(rand.randint(low, high)))
    return "".join(chars)


def random_int(rand: random.Random) -> int:
    if rand.random() < 0.3:
        return rand.choice([0, 1, -1, 2**53 - 1, -(2**53 - 1), 2**31, -(2**31), 2**52])
    bits = rand.randint(0, 53)
    return rand.randint(-(2**bits) + 1, 2**bits - 1)


def random_value(rand: random.Random, depth: int) -> Any:
    roll = rand.random()
    if depth <= 0 or roll < 0.45:
        scalar = rand.random()
        if scalar < 0.15:
            return None
        if scalar < 0.3:
            return rand.random() < 0.5
        if scalar < 0.55:
            return random_int(rand)
        return random_string(rand, 8)
    if roll < 0.7:
        return [random_value(rand, depth - 1) for _ in range(rand.randint(0, 4))]
    return {
        random_string(rand, 5): random_value(rand, depth - 1) for _ in range(rand.randint(0, 5))
    }


def test_serializer_matches_the_reference_text_and_bytes_for_2000_generated_values() -> None:
    rand = random.Random(611)
    for _ in range(2000):
        value = random_value(rand, 5)
        expected = reference(value)
        assert canonical_json(value) == expected
        assert canonical_bytes(value) == expected.encode("utf-8")


def test_serializer_matches_the_reference_for_keys_mixing_bmp_high_and_astral_code_points() -> None:
    rand = random.Random(53)
    for _ in range(300):
        obj: dict[str, int] = {}
        for index in range(8):
            low, high = rand.choice(RANGES[5:])
            obj[chr(rand.randint(low, high))] = index
        assert canonical_json(obj) == reference(obj)


def test_key_order_is_utf8_byte_order() -> None:
    keys = [
        chr(0x10000),
        chr(0xFF5E),
        chr(0xE000),
        chr(0xFFFF),
        chr(0x10FFFF),
        "a",
        "",
        "a\x00",
        "aa",
    ]
    text = canonical_json(dict.fromkeys(keys, 0))
    expected_order = sorted(keys, key=lambda k: k.encode("utf-8"))
    assert text == "{" + ",".join(f"{canonical_json(k)}:0" for k in expected_order) + "}"
    assert expected_order.index(chr(0xFFFF)) < expected_order.index(chr(0x10000))


def test_output_is_independent_of_dict_insertion_order() -> None:
    forward = {"b": 1, "a": 2, chr(0x10000): 3, chr(0xFF5E): 4, "": 5, "10": 6, "9": 7}
    backward = dict(reversed(list(forward.items())))
    assert list(forward) != list(backward)
    assert canonical_json(forward) == canonical_json(backward)


def test_powers_of_ten_and_two_in_range_are_plain_base_ten_text() -> None:
    values = [s * 10**e for e in range(16) for s in (1, -1)]
    values += [s * 2**e + d for e in range(53) for s in (1, -1) for d in (0, -s)]
    values += [2**53 - 1, -(2**53 - 1)]
    for n in values:
        assert canonical_json(n) == str(n)
        assert "e" not in canonical_json(n)


@pytest.mark.parametrize(
    "name",
    [
        "canonical-json-key-order",
        "canonical-json-strings",
        "canonical-json-integers",
        "canonical-json-structure",
        "canonical-json-game-state",
    ],
)
def test_vector_outputs_equal_the_independent_reference(name: str) -> None:
    vector = load_vector(name)
    assert len(vector["cases"]) >= 3
    for case in vector["cases"]:
        assert reference(case["input"]) == case["output"], f"{name}: {case['name']}"


def test_game_state_vector_inputs_are_valid_states_and_serialize_to_their_output() -> None:
    for case in load_vector("canonical-json-game-state")["cases"]:
        state = GameState.model_validate(case["input"])
        assert canonical_json(state.to_json_value()) == case["output"], case["name"]
        assert canonical_json(case["input"]) == case["output"], case["name"]


# 3.5 allows partial settings; 8.1 says a vector has all four, so these opt out on purpose.
PARTIAL_SETTINGS_VECTORS = {
    "initial-state-partial-settings",
    "initial-state-partial-settings-trust-mode-only",
    "initial-state-empty-settings",
}


def test_every_vector_file_is_well_formed() -> None:
    files = sorted(require(VECTORS_DIR).glob("*.json"))
    assert len(files) >= 90
    for path in files:
        vector = json.loads(path.read_text(encoding="utf-8"))
        assert vector["format"] == 1
        assert vector["name"] == path.stem
        assert vector["kind"] in {"reducer", "canonical_json"}
        assert vector["description"]
        if vector["kind"] == "reducer":
            assert len(vector["expect"]) == 1
            names_of_settings = {
                "starting_life",
                "commander_damage_threshold",
                "poison_threshold",
                "trust_mode",
            }
            assert set(vector["settings"]) <= names_of_settings
            if path.stem not in PARTIAL_SETTINGS_VECTORS:  # 8.1: all four, except on purpose
                assert set(vector["settings"]) == names_of_settings
        else:
            names = [c["name"] for c in vector["cases"]]
            assert len(names) == len(set(names)), f"{path.stem} repeats a case name"


def test_depth_vectors_hold_the_documented_chain_lengths() -> None:
    def nested(value: Any) -> int:
        """Count the nested containers of a chain with an empty container at the bottom."""
        count = 0
        while isinstance(value, (dict, list)):
            count += 1
            if not value:
                break
            value = next(iter(value.values())) if isinstance(value, dict) else value[0]
        return count

    ok = load_vector("accept-payload-depth-limit")["events"][0]["payload"]
    bad = load_vector("reject-payload-too-deep")["events"][0]["payload"]
    assert nested(ok) == 65
    assert nested(bad) == 66


# --- rejections: integers only, no floats ---------------------------------------------------


def code_of(value: Any) -> str:
    with pytest.raises(CanonicalJsonError) as raised:
        canonical_json(value)
    return str(raised.value.code)


@pytest.mark.parametrize(
    "value",
    [
        *[1.0, -0.0, 0.0, 2.0, 0.1 + 0.2, -0.5, 1e300, 1e-7, 5e-324, 2.0**53],
        *[float("nan"), float("inf"), float("-inf")],
    ],
)
def test_every_python_float_is_rejected_as_non_integer_even_when_integral(value: float) -> None:
    assert code_of(value) == "non_integer"
    assert code_of({"a": [value]}) == "non_integer"
    assert code_of([[[value]]]) == "non_integer"


@pytest.mark.parametrize("value", [2**53, -(2**53), 2**63, 2**64, 10**30, -(10**30)])
def test_integers_outside_the_safe_range_are_rejected(value: int) -> None:
    assert code_of(value) == "integer_out_of_range"
    assert code_of({"k": value}) == "integer_out_of_range"


@pytest.mark.parametrize("value", [Decimal("1"), Decimal("1.5"), Fraction(1, 2), complex(1, 1)])
def test_other_number_types_are_not_canonical_values(value: Any) -> None:
    assert code_of(value) == "unsupported_value"


def test_booleans_are_literals_not_integers_at_every_position() -> None:
    assert canonical_json({"t": True, "f": False, "one": 1, "zero": 0}) == (
        '{"f":false,"one":1,"t":true,"zero":0}'
    )
    assert canonical_json([True, 1, False, 0]) == "[true,1,false,0]"


HIGH = chr(0xD83D)
LOW = chr(0xDE00)
SURROGATE_TEXTS = [HIGH + LOW, HIGH + HIGH, "a" + chr(0xD800), chr(0xDC00) + "a", chr(0xDFFF)]


@pytest.mark.parametrize("text", SURROGATE_TEXTS, ids=range(len(SURROGATE_TEXTS)))
def test_any_surrogate_code_point_is_rejected_in_values_and_keys(text: str) -> None:
    assert code_of(text) == "lone_surrogate"
    assert code_of({text: 1}) == "lone_surrogate"


def test_nesting_limit_matches_the_contract_for_arrays_objects_and_scalars() -> None:
    def arrays(wraps: int, bottom: Any) -> Any:
        value = bottom
        for _ in range(wraps):
            value = [value]
        return value

    def objects(wraps: int, bottom: Any) -> Any:
        value = bottom
        for _ in range(wraps):
            value = {"a": value}
        return value

    assert canonical_json(arrays(65, 1)) == "[" * 65 + "1" + "]" * 65
    assert code_of(arrays(66, 1)) == "unsupported_value"
    assert canonical_json(arrays(64, [])) == "[" * 65 + "]" * 65
    assert code_of(arrays(65, [])) == "unsupported_value"
    assert canonical_json(objects(64, {})) == '{"a":' * 64 + "{}" + "}" * 64
    assert code_of(objects(65, {})) == "unsupported_value"
    assert code_of({"a": arrays(65, 1)}) == "unsupported_value"
    assert code_of(arrays(5000, 1)) == "unsupported_value"


# --- wire reader (5.6): floats never reach the reducers ----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "1.0",
        "3.0",
        "-0.0",
        "1.5",
        "1e2",
        "1E2",
        "1e-2",
        '{"a":1.0}',
        '{"v":[1,2.0]}',
        "NaN",
        "Infinity",
        "-Infinity",
        "9007199254740992",
        "-9007199254740992",
        "100000000000000000000000",
        '{"a":1,"a":2}',
        '{"x":{"a":1,"a":1}}',
        "{",
        "",
        "[1,]",
    ],
)
def test_wire_reader_rejects_with_invalid_event(text: str) -> None:
    with pytest.raises(WireParseError) as raised:
        loads_wire(text)
    assert raised.value.code == "invalid_event"


def test_wire_reader_rejects_bytes_that_are_not_utf8() -> None:
    with pytest.raises(WireParseError):
        loads_wire(b'{"a":"\xff"}')


def test_wire_reader_accepts_integers_at_the_limit_and_negative_zero_as_zero() -> None:
    assert loads_wire("[9007199254740991,-9007199254740991,0]") == [2**53 - 1, -(2**53 - 1), 0]
    assert loads_wire("-0") == 0
    assert canonical_json(loads_wire("[-0]")) == "[0]"


def test_canonical_output_parses_back_with_the_wire_reader_to_the_same_value() -> None:
    rand = random.Random(7)
    for _ in range(300):
        value = random_value(rand, 4)
        assert loads_wire(canonical_json(value)) == value
        assert loads_wire(canonical_bytes(value)) == value


# --- no locale dependence -----------------------------------------------------------------------

SAMPLE: Any = {
    "z": [1, -2, 2**53 - 1],
    "I": "i̇ İ ß é é",
    chr(0x10000): {chr(0xFF5E): "Zoë 日本", "a": None},
    "10": True,
    "9": 1234567,
}

LOCALES = [
    "de_DE.UTF-8",
    "tr_TR.UTF-8",
    "fr_FR.UTF-8",
    "ar_SA.UTF-8",
    "sv_SE.UTF-8",
    "German_Germany.1252",
    "Turkish_Turkey.1254",
    "French_France.1252",
    "Swedish_Sweden.1252",
    "C",
]


@pytest.fixture
def restore_locale() -> Iterator[None]:
    saved = {
        c: locale.setlocale(c) for c in (locale.LC_COLLATE, locale.LC_NUMERIC, locale.LC_CTYPE)
    }
    try:
        yield
    finally:
        for category, value in saved.items():
            locale.setlocale(category, value)


def test_output_is_identical_under_every_available_process_locale(restore_locale: None) -> None:
    expected = reference(SAMPLE)
    tried = 0
    for name in LOCALES:
        try:
            locale.setlocale(locale.LC_ALL, name)
        except locale.Error:
            continue
        tried += 1
        assert canonical_json(SAMPLE) == expected, name
        assert canonical_bytes(SAMPLE) == expected.encode("utf-8"), name
        assert canonical_json([1234567, -1234567]) == "[1234567,-1234567]", name
    assert tried >= 1  # "C" is always available


def test_serializer_calls_no_locale_function(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a locale function was called")

    for attr in ("strxfrm", "strcoll", "format_string", "localeconv", "currency", "str", "atoi"):
        monkeypatch.setattr(locale, attr, boom)
    assert canonical_json(SAMPLE) == reference(SAMPLE)
    assert canonical_bytes(SAMPLE) == reference(SAMPLE).encode("utf-8")
