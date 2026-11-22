"""Vector file validation and event-type coverage (CONTRACT.md sections 8.3, 8.3.1 and 8.4).

Pure functions over text and parsed values: no file access, so this module could be shared with a
loader that reads files elsewhere. It is test tooling and lives with the games tests, not in
``app/games/core`` (the shipped, purity-guarded reducer package). The TypeScript twin is
``packages/game-core/src/vector-validator.ts``; both are written from the contract text.

How raw text is inspected, in this order: an iterative depth prepass (more than 512 levels is the
single message and nothing else); ``json.loads`` (the non-standard ``NaN``/``Infinity`` constants
refused, and a ``parse_int`` hook so an over-long integer token never meets the interpreter's
digit limit) for the parsed value and the ``invalid JSON`` message; then an iterative scanner
(explicit stack, never recursive) that lists every number token and every repeated key with its
path. Validation never raises: a failure of the validator itself is one ``$ internal error``
message.
"""

from __future__ import annotations

import json
import math
import re
import sys
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


MAX_DEPTH: Final = 512
MAX_INTEGER_DIGITS: Final = 300


class HugeInteger(int):
    """An integer token too long to convert with ``int(str)`` (the interpreter's digit limit).

    The limit is global state and is left alone. The value stands in as the nearest unsafe integer
    of the same sign (2**53 or -2**53), which every rule here and the reducer treat the same as
    the real number: any integer beyond the safe range is rejected. ``token`` keeps the text.
    """

    token: str

    def __new__(cls, token: str) -> HugeInteger:
        self = super().__new__(cls, -(SAFE_MAX + 1) if token.startswith("-") else SAFE_MAX + 1)
        self.token = token
        return self


def _parse_int(token: str) -> int:
    """``int(token)``, or a ``HugeInteger`` when the interpreter's digit limit would refuse it."""
    limit = sys.get_int_max_str_digits()  # 0 means unlimited; read each time, never changed here
    if limit and len(token.lstrip("-")) > limit:
        return HugeInteger(token)
    return int(token)


def _outside_safe_range(digits_text: str) -> bool:
    """Whether an integer's decimal text (optional ``-``, no leading zeros) is beyond 2**53-1.

    Decided from the text, so no ``int()`` call on a possibly huge string.
    """
    digits = digits_text.lstrip("-")
    return len(digits) > len(str(SAFE_MAX)) or (
        len(digits) == len(str(SAFE_MAX)) and digits > str(SAFE_MAX)
    )


def strip_huge_integers(value: Any) -> Any:
    """Copy of a parsed value with each ``HugeInteger`` replaced by a plain ``int``."""
    if isinstance(value, HugeInteger):
        return int(value)
    if isinstance(value, list):
        return [strip_huge_integers(item) for item in value]
    if isinstance(value, dict):
        return {key: strip_huge_integers(item) for key, item in value.items()}
    return value


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
    duplicates: list[str]  # path of every repeated occurrence of a key, in document order


def _join(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else key


_SCAN_TOKEN: Final = re.compile(
    r"""[ \t\r\n]*(?:
        (?P<string>"(?:[^"\\]|\\.)*")
      | (?P<number>-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)
      | (?P<punct>[\[\]{}:,])
      | (?P<word>true|false|null)
    )""",
    re.VERBOSE | re.DOTALL,
)
_DEPTH_TOKEN: Final = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*(?:"|\\?\Z)|[\[\]{}]', re.DOTALL)


def nesting_depth(text: str) -> int:
    """Deepest level of ``text`` measured on the raw characters (section 8.3), without recursion.

    Strings are skipped (a backslash skips the next character, an unescaped quote ends the string,
    an unterminated string runs to the end); ``[`` and ``{`` open a level, ``]`` and ``}`` close
    one, never below 0. The top-level container is level 1.
    """
    level = deepest = 0
    for match in _DEPTH_TOKEN.finditer(text):
        char = match.group()
        if char in "[{":
            level += 1
            deepest = max(deepest, level)
        elif char in "]}":
            level = max(0, level - 1)
    return deepest


def _scan_text(text: str) -> _Scan:
    """Walk JSON text that ``json.loads`` already accepted, with an explicit stack."""
    result = _Scan([], [])
    # frame: ["o", path, seen_keys, child_path or None, expecting_key] or ["a", path, next_index]
    stack: list[list[Any]] = []

    def value_path() -> str:
        if not stack:
            return ""
        top = stack[-1]
        if top[0] == "a":
            index = top[2]
            top[2] += 1
            return f"{top[1]}[{index}]"
        return str(top[3])

    pos = 0
    while True:
        match = _SCAN_TOKEN.match(text, pos)
        if match is None:
            break
        pos = match.end()
        kind = match.lastgroup
        token = match.group(kind) if kind else ""
        if kind == "string":
            top = stack[-1] if stack else None
            if top is not None and top[0] == "o" and top[4]:
                key = json.loads(token)
                child = _join(top[1], key)
                if key in top[2]:
                    result.duplicates.append(child)
                top[2].add(key)
                top[3] = child
                top[4] = False
        elif kind == "number":
            result.numbers.append((value_path(), token))
        elif kind == "punct":
            if token == "{":
                stack.append(["o", value_path(), set(), None, True])
            elif token == "[":
                stack.append(["a", value_path(), 0])
            elif token in "]}":
                stack.pop()
            elif token == "," and stack and stack[-1][0] == "o":
                stack[-1][4] = True
    return result


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
        elif _outside_safe_range(token):
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
        if text is None or not _INTEGER_TEXT.fullmatch(text) or not _outside_safe_range(text):
            return "must be decimal text of an integer outside the safe range"
        if len(text.lstrip("-")) > MAX_INTEGER_DIGITS:
            return f"must have at most {MAX_INTEGER_DIGITS} digits"
        return None
    if kind == "lone_surrogate":
        if (
            text is not None
            and _SURROGATE_HEX.fullmatch(text)
            and 0xD800 <= int(text, 16) <= 0xDFFF
        ):
            return None
        return "must be the 4-digit hex code of a surrogate code unit (d800 to dfff)"
    if (
        text is not None
        and _POSITIVE_INTEGER_TEXT.fullmatch(text)
        and len(text) <= 5
        and int(text) <= 10000
    ):
        return None
    return "must be decimal text of an integer from 1 to 10000"


# ------------------------------------------------------------------------------ entry points


def _validate(file: str, text: str) -> ValidationResult:
    """Validate the raw text of one vector file (sections 8.3 and 8.3.1).

    ``file`` is the base name with ``.json``. Every problem is reported, in the contract's check
    order, each as ``<file>: <path> <message>``.
    """
    try:
        parsed = json.loads(text, parse_constant=_reject_constant, parse_int=_parse_int)
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
    scan = _scan_text(text)
    messages.extend(f"{file}: {m}" for m in _number_token_messages(scan.numbers))
    messages.extend(f"{file}: {path} is a duplicate key" for path in scan.duplicates)
    return ValidationResult(tuple(messages), parsed)


def validate_vector_text(file: str, text: str) -> ValidationResult:
    """Validate the raw text of one vector file (sections 8.3 and 8.3.1). Never raises.

    ``file`` is the base name with ``.json``. Every problem is reported, in the contract's check
    order, each as ``<file>: <path> <message>``. Text nested more than 512 levels gives only
    ``<file>: $ is nested more than 512 levels deep``; a failure of the validator itself gives
    only ``<file>: $ internal error: <detail>``.
    """
    try:
        if nesting_depth(text) > MAX_DEPTH:
            return ValidationResult(
                (f"{file}: $ is nested more than {MAX_DEPTH} levels deep",), None
            )
        return _validate(file, text)
    except Exception as error:  # the contract: validation never throws
        return ValidationResult(
            (f"{file}: $ internal error: {type(error).__name__}: {error}",), None
        )


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
