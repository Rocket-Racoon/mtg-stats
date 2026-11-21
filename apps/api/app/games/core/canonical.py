"""Canonical JSON (CONTRACT.md section 5) and strict wire parsing (section 5.6).

``canonical_json`` is the one byte-exact text form of a JSON value; the TypeScript package
implements the same rules. Pure: no clock, randomness or I/O.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

MAX_SAFE_INTEGER: Final = 2**53 - 1
"""Largest absolute value of a canonical integer (section 1)."""

MAX_DEPTH: Final = 64
"""A container with more than this many ancestors is rejected (section 5.4)."""

_NEEDS_ESCAPE = re.compile(r'["\\\x00-\x1f]')
_SURROGATE = re.compile(r"[\ud800-\udfff]")
_SHORT_ESCAPES: Final = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


class CanonicalJsonError(Exception):
    """A value cannot be written canonically; ``code`` is one of the section 5.4 codes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class WireParseError(Exception):
    """A request body is not acceptable wire JSON (section 5.6); ``code`` is ``invalid_event``."""

    code = "invalid_event"


def _escape_char(match: re.Match[str]) -> str:
    char = match.group()
    return _SHORT_ESCAPES.get(char) or "\\u" + format(ord(char), "04x")


def _write_string(text: str) -> str:
    if _SURROGATE.search(text):
        raise CanonicalJsonError("lone_surrogate", "string contains a surrogate code point")
    return '"' + _NEEDS_ESCAPE.sub(_escape_char, text) + '"'


def _write(value: Any, ancestors: int) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):  # before int: True is an int
        return "true" if value else "false"
    if isinstance(value, float):
        raise CanonicalJsonError("non_integer", f"floats are not allowed: {value!r}")
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalJsonError("integer_out_of_range", "integer outside the safe range")
        return str(value)
    if isinstance(value, str):
        return _write_string(value)
    if isinstance(value, (list, dict)):
        if ancestors > MAX_DEPTH:
            raise CanonicalJsonError("unsupported_value", f"nested more than {MAX_DEPTH} deep")
        if isinstance(value, list):
            return "[" + ",".join(_write(item, ancestors + 1) for item in value) + "]"
        for key in value:
            if not isinstance(key, str):
                raise CanonicalJsonError("unsupported_value", f"non-string key: {key!r}")
        parts = [
            f"{_write_string(key)}:{_write(value[key], ancestors + 1)}"
            for key in sorted(value)  # code point order
        ]
        return "{" + ",".join(parts) + "}"
    raise CanonicalJsonError("unsupported_value", f"cannot serialize {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return the canonical JSON text of ``value`` (section 5.2).

    Raises:
        CanonicalJsonError: with a section 5.4 ``code`` when the value is not canonical.
    """
    return _write(value, 0)


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical JSON of ``value`` as UTF-8 bytes, without a byte order mark."""
    return canonical_json(value).encode("utf-8")


def _reject_float(token: str) -> Any:
    raise WireParseError(f"number {token!r} is not an integer")


def _parse_int(token: str) -> int:
    value = int(token)
    if abs(value) > MAX_SAFE_INTEGER:
        raise WireParseError("integer outside the safe range")
    return value


def _reject_constant(token: str) -> Any:
    raise WireParseError(f"{token} is not allowed")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WireParseError(f"duplicate key {key!r}")
        result[key] = value
    return result


def loads_wire(data: str | bytes) -> Any:
    """Parse incoming JSON the way section 5.6 requires.

    Every number token with ``.``, ``e`` or ``E`` is rejected, as are integers outside the safe
    range, ``NaN``/``Infinity`` tokens, duplicate object keys and bytes that are not UTF-8.

    Raises:
        WireParseError: (``code`` ``invalid_event``) for any of the above or malformed JSON.
    """
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        return json.loads(
            text,
            parse_float=_reject_float,
            parse_int=_parse_int,
            parse_constant=_reject_constant,
            object_pairs_hook=_no_duplicate_keys,
        )
    except WireParseError:
        raise
    except (ValueError, RecursionError) as exc:  # bad UTF-8, malformed JSON, huge int token
        raise WireParseError(str(exc)) from exc
