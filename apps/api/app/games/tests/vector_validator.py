"""Vector file validation and event-type coverage (CONTRACT.md sections 8.3, 8.3.1 and 8.4).

Pure functions over text and parsed values: no file access, so this module could be shared with a
loader that reads files elsewhere. It is test tooling and lives with the games tests, not in
``app/games/core`` (the shipped, purity-guarded reducer package). The TypeScript twin is
``packages/game-core/src/vector-validator.ts``; both are written from the contract text.

How raw text is inspected: ``json.loads`` first (with the non-standard ``NaN``/``Infinity``
constants refused) gives the parsed value and the ``invalid JSON`` message. A small recursive
scanner then walks the same text and lists every number token and every duplicate key with its
path. Python reads integer tokens exactly (``int`` has no size limit), so the scanner only needs
the token text, which it checks itself: no ``parse_float`` or ``parse_int`` hook is used.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.games.core.contract import REDUCER_ERROR_CODES

SAFE_MAX: Final = 9007199254740991
SETTING_NAMES: Final = (
    "starting_life",
    "commander_damage_threshold",
    "poison_threshold",
    "trust_mode",
)
CANONICAL_ERROR_CODES: Final = (
    "non_integer",
    "integer_out_of_range",
    "lone_surrogate",
    "unsupported_value",
)
INVALID_KINDS: Final = (
    "float",
    "nan",
    "infinity",
    "integer",
    "lone_surrogate",
    "deep_array",
    "deep_object",
)
_HEADER_KEYS: Final = ("kind", "format", "name", "description")
_REDUCER_KEYS: Final = (*_HEADER_KEYS, "game_id", "settings", "events", "expect")
_CANONICAL_KEYS: Final = (*_HEADER_KEYS, "cases")

_JSON_NUMBER: Final = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_INTEGER_TEXT: Final = re.compile(r"-?[1-9][0-9]*")
_POSITIVE_INTEGER_TEXT: Final = re.compile(r"[1-9][0-9]*")
_SURROGATE_HEX: Final = re.compile(r"[0-9a-fA-F]{4}")
_NUMBER_TOKEN: Final = re.compile(r"-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_STRING_TOKEN: Final = re.compile(r'"(?:[^"\\]|\\.)*"', re.DOTALL)
_WHITESPACE: Final = re.compile(r"[ \t\r\n]*")


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating one vector file: its messages and, when it parsed, its value."""

    messages: tuple[str, ...]
    value: dict[str, Any] | None

    @property
    def ok(self) -> bool:
        """True when the file has no problem (and so may be run)."""
        return not self.messages


# --------------------------------------------------------------------------- raw text scanning


@dataclass
class _Scan:
    numbers: list[tuple[str, str]]  # (path, token) in document order
    duplicates: list[str]  # path of each second occurrence, in document order


def _join(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else key


class _Scanner:
    """Walks JSON text that ``json.loads`` already accepted, recording tokens and duplicates."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.result = _Scan([], [])

    def _skip(self) -> None:
        match = _WHITESPACE.match(self.text, self.pos)
        assert match is not None
        self.pos = match.end()

    def run(self) -> _Scan:
        self._skip()
        self._value("")
        return self.result

    def _value(self, path: str) -> None:
        self._skip()
        char = self.text[self.pos]
        if char == "{":
            self._object(path)
        elif char == "[":
            self._array(path)
        elif char == '"':
            self._string()
        elif char in "-0123456789":
            match = _NUMBER_TOKEN.match(self.text, self.pos)
            assert match is not None
            self.result.numbers.append((path, match.group()))
            self.pos = match.end()
        else:  # true, false, null
            for literal in ("true", "false", "null"):
                if self.text.startswith(literal, self.pos):
                    self.pos += len(literal)
                    return
            raise AssertionError(f"unexpected character at {self.pos}")

    def _string(self) -> str:
        match = _STRING_TOKEN.match(self.text, self.pos)
        assert match is not None
        self.pos = match.end()
        decoded = json.loads(match.group())
        assert isinstance(decoded, str)
        return decoded

    def _object(self, path: str) -> None:
        self.pos += 1
        seen: set[str] = set()
        self._skip()
        if self.text[self.pos] == "}":
            self.pos += 1
            return
        while True:
            self._skip()
            key = self._string()
            child = _join(path, key)
            if key in seen:
                self.result.duplicates.append(child)
            seen.add(key)
            self._skip()
            self.pos += 1  # the colon
            self._value(child)
            self._skip()
            separator = self.text[self.pos]
            self.pos += 1
            if separator == "}":
                return

    def _array(self, path: str) -> None:
        self.pos += 1
        self._skip()
        if self.text[self.pos] == "]":
            self.pos += 1
            return
        index = 0
        while True:
            self._value(f"{path}[{index}]")
            index += 1
            self._skip()
            separator = self.text[self.pos]
            self.pos += 1
            if separator == "]":
                return


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _number_token_messages(numbers: Iterable[tuple[str, str]]) -> list[str]:
    """Section 8.3.1, in document order (``-0`` is accepted everywhere)."""
    found: list[str] = []
    for path, token in numbers:
        fractional = any(mark in token for mark in ".eE")
        if path.startswith("events["):
            if fractional:
                value = float(token)  # value as a double, as JSON.parse reads it
                if math.isfinite(value) and not value.is_integer():
                    continue
                found.append(
                    f"{path} has the number token {token}; write an integral value as an integer"
                )
        elif fractional:
            found.append(f"{path} must be an integer (found the number token {token})")
        elif abs(int(token)) > SAFE_MAX:
            found.append(f"{path} must be an integer in the safe range (found {token})")
    return found


# ------------------------------------------------------------------------------ value helpers


def _integer_value(value: object) -> int | None:
    """The integer a parsed number stands for, or None.

    A float with an integral value counts (``1.0``): TypeScript cannot tell it from ``1``, and
    the number-token rule reports the token itself, so the field rule does not repeat it.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _is_object(value: object) -> bool:
    return isinstance(value, dict)


def _header_messages(file: str, data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    stem = file.removesuffix(".json")
    if "kind" not in data:
        out.append(f"{file}: kind is required")
    elif data["kind"] not in ("reducer", "canonical_json"):
        out.append(f'{file}: kind must be "reducer" or "canonical_json"')
    if "format" not in data:
        out.append(f"{file}: format is required")
    elif _integer_value(data["format"]) != 1:
        out.append(f"{file}: format must be the integer 1")
    if "name" not in data:
        out.append(f"{file}: name is required")
    elif data["name"] != stem or not isinstance(data["name"], str):
        out.append(f'{file}: name must equal "{stem}"')
    if "description" not in data:
        out.append(f"{file}: description is required")
    elif not isinstance(data["description"], str) or not data["description"]:
        out.append(f"{file}: description must be a non-empty string")
    return out


def _applied_limit(data: dict[str, Any], count: int) -> int:
    """How many leading events are applied successfully (section 8.3 ``events[i].type``)."""
    expect = data.get("expect")
    if isinstance(expect, dict) and "error" in expect:
        error = expect["error"]
        index = _integer_value(error.get("index")) if isinstance(error, dict) else None
        if index is not None and index >= 0:
            return min(index, count)
    return count


def _game_settings_messages(file: str, data: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def add(path: str, message: str) -> None:
        out.append(f"{file}: {path} {message}")

    if "game_id" not in data:
        add("game_id", "is required")
    elif not isinstance(data["game_id"], str) or not data["game_id"]:
        add("game_id", "must be a non-empty string")

    if "settings" not in data:
        add("settings", "is required")
    elif not _is_object(data["settings"]):
        add("settings", "must be an object")
    else:
        for key in data["settings"]:
            if key not in SETTING_NAMES:
                add(f"settings.{key}", "is not a known key")
    return out


def _reducer_messages(file: str, data: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def add(path: str, message: str) -> None:
        out.append(f"{file}: {path} {message}")

    out.extend(_game_settings_messages(file, data))

    events = data.get("events")
    if "events" not in data:
        add("events", "is required")
    elif not isinstance(events, list):
        add("events", "must be an array")
    else:
        for i, item in enumerate(events):
            if not _is_object(item):
                add(f"events[{i}]", "must be an object")

    if "expect" not in data:
        add("expect", "is required")
    elif not _is_object(data["expect"]):
        add("expect", "must be an object")
    else:
        expect = data["expect"]
        for key in expect:
            if key not in ("state", "error"):
                add(f"expect.{key}", "is not a known key")
        if ("state" in expect) == ("error" in expect):
            add("expect", 'must have exactly one of "state" or "error"')
        if "state" in expect and not _is_object(expect["state"]):
            add("expect.state", "must be an object")
        if "error" in expect:
            out.extend(_expect_error_messages(file, expect["error"], events))

    if isinstance(events, list):
        for i in range(_applied_limit(data, len(events))):
            item = events[i]
            if _is_object(item) and not isinstance(item.get("type"), str):
                add(f"events[{i}].type", "must be a string")
    return out


def _expect_error_messages(file: str, error: object, events: object) -> list[str]:
    out: list[str] = []

    def add(path: str, message: str) -> None:
        out.append(f"{file}: {path} {message}")

    if not isinstance(error, dict):
        add("expect.error", "must be an object")
        return out
    for key in error:
        if key not in ("index", "code"):
            add(f"expect.error.{key}", "is not a known key")
    if "index" not in error:
        add("expect.error.index", "is required")
    else:
        index = _integer_value(error["index"])
        if index is None or index < 0 or index > SAFE_MAX:
            add("expect.error.index", "must be a non-negative integer")
        elif isinstance(events, list) and index >= len(events):
            add("expect.error.index", f"must be less than the number of events ({len(events)})")
    if "code" not in error:
        add("expect.error.code", "is required")
    else:
        code = error["code"]
        if not isinstance(code, str) or code not in REDUCER_ERROR_CODES:
            add("expect.error.code", "must be a known reducer error code")
    return out


def _canonical_messages(file: str, data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if "cases" not in data:
        return [f"{file}: cases is required"]
    cases = data["cases"]
    if not isinstance(cases, list) or not cases:
        return [f"{file}: cases must be a non-empty array"]
    first_named: dict[str, int] = {}
    for i, case in enumerate(cases):
        out.extend(_case_messages(file, i, case, first_named))
    return out


def _case_messages(file: str, i: int, case: object, first_named: dict[str, int]) -> list[str]:
    out: list[str] = []
    base = f"cases[{i}]"

    def add(path: str, message: str) -> None:
        out.append(f"{file}: {path} {message}")

    if not isinstance(case, dict):
        add(base, "must be an object")
        return out
    if "name" not in case:
        add(f"{base}.name", "is required")
    elif not isinstance(case["name"], str) or not case["name"]:
        add(f"{base}.name", "must be a non-empty string")
    else:
        name = case["name"]
        if name in first_named:
            add(f"{base}.name", f"duplicates the name of cases[{first_named[name]}]")
        else:
            first_named[name] = i
    if ("input" in case) == ("invalid" in case):
        add(base, 'must have exactly one of "input" or "invalid"')
        return out
    is_input = "input" in case
    allowed = ("name", "input", "output") if is_input else ("name", "invalid", "error")
    for key in case:
        if key not in allowed:
            add(f"{base}.{key}", "is not a known key")
    if is_input:
        if "output" not in case:
            add(f"{base}.output", "is required")
        elif not isinstance(case["output"], str):
            add(f"{base}.output", "must be a string")
        return out
    if "error" not in case:
        add(f"{base}.error", "is required")
    elif not isinstance(case["error"], str) or case["error"] not in CANONICAL_ERROR_CODES:
        add(f"{base}.error", "must be a canonical JSON error code")
    out.extend(_invalid_messages(file, base, case["invalid"]))
    return out


def _invalid_messages(file: str, base: str, invalid: object) -> list[str]:
    out: list[str] = []

    def add(path: str, message: str) -> None:
        out.append(f"{file}: {path} {message}")

    path = f"{base}.invalid"
    if not isinstance(invalid, dict):
        add(path, "must be an object")
        return out
    for key in invalid:
        if key not in ("kind", "value"):
            add(f"{path}.{key}", "is not a known key")
    kind = invalid.get("kind")
    if "kind" not in invalid:
        add(f"{path}.kind", "is required")
        return out
    if not isinstance(kind, str) or kind not in INVALID_KINDS:
        add(f"{path}.kind", f"must be one of {', '.join(INVALID_KINDS)}")
        return out
    if kind in ("nan", "infinity"):
        if "value" in invalid:
            add(f"{path}.value", "must be absent")
        return out
    if "value" not in invalid:
        add(f"{path}.value", "is required")
        return out
    problem = _value_problem(kind, invalid["value"])
    if problem:
        add(f"{path}.value", problem)
    return out


def _value_problem(kind: str, value: object) -> str | None:
    text = value if isinstance(value, str) else None
    if kind == "float":
        if text is not None and _JSON_NUMBER.fullmatch(text):
            number = float(text)
            if math.isfinite(number) and not number.is_integer():
                return None
        return "must be decimal text of a non-integer number"
    if kind == "integer":
        if text is not None and _INTEGER_TEXT.fullmatch(text) and abs(int(text)) > SAFE_MAX:
            return None
        return "must be decimal text of an integer outside the safe range"
    if kind == "lone_surrogate":
        if (
            text is not None
            and _SURROGATE_HEX.fullmatch(text)
            and 0xD800 <= int(text, 16) <= 0xDFFF
        ):
            return None
        return "must be the 4-digit hex code of a surrogate code unit (d800 to dfff)"
    if text is not None and _POSITIVE_INTEGER_TEXT.fullmatch(text) and int(text) <= 10000:
        return None
    return "must be decimal text of an integer from 1 to 10000"


# ------------------------------------------------------------------------------ entry points


def validate_vector_text(file: str, text: str) -> ValidationResult:
    """Validate the raw text of one vector file (sections 8.3 and 8.3.1).

    ``file`` is the base name with ``.json``. Every problem is reported, in the contract's check
    order, each as ``<file>: <path> <message>``.
    """
    try:
        parsed = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        return ValidationResult((f"{file}: invalid JSON: {error}",), None)
    if not isinstance(parsed, dict):
        return ValidationResult((f"{file}: $ must be an object",), None)

    messages = _header_messages(file, parsed)
    kind = parsed.get("kind")
    if kind == "reducer":
        messages.extend(_reducer_messages(file, parsed))
        known: Sequence[str] | None = _REDUCER_KEYS
    elif kind == "canonical_json":
        messages.extend(_canonical_messages(file, parsed))
        known = _CANONICAL_KEYS
    else:
        known = None
    if known is not None:
        messages.extend(f"{file}: {key} is not a known key" for key in parsed if key not in known)
    scan = _Scanner(text).run()
    messages.extend(f"{file}: {m}" for m in _number_token_messages(scan.numbers))
    messages.extend(f"{file}: {path} is a duplicate key" for path in scan.duplicates)
    return ValidationResult(tuple(messages), parsed)


def exercised_event_types(vectors: Iterable[dict[str, Any]]) -> set[str]:
    """Types applied successfully by the given (already validated) reducer vectors (8.4)."""
    seen: set[str] = set()
    for vector in vectors:
        if vector.get("kind") != "reducer":
            continue
        events = vector["events"]
        for event in events[: _applied_limit(vector, len(events))]:
            event_type = event.get("type")
            if isinstance(event_type, str):
                seen.add(event_type)
    return seen


def uncovered_event_type_messages(
    vectors: Iterable[dict[str, Any]], registry: Sequence[str]
) -> list[str]:
    """One ``uncovered event type: <type>`` per registry type no vector exercises, in order."""
    seen = exercised_event_types(vectors)
    return [f"uncovered event type: {t}" for t in registry if t not in seen]
