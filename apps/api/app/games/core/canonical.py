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


def has_lone_surrogate(text: str) -> bool:
    """True when ``text`` has a code point in U+D800 to U+DFFF (section 2, well-formed strings)."""
    return _SURROGATE.search(text) is not None


def _write_string(text: str) -> str:
    if has_lone_surrogate(text):
        raise CanonicalJsonError("lone_surrogate", "string contains a surrogate code point")
    return '"' + _NEEDS_ESCAPE.sub(_escape_char, text) + '"'


def _write(value: Any, ancestors: int) -> str:
    # Exact types only (sections 5.1, 5.4): a subclass is a "class instance" and is rejected with
    # unsupported_value, so an overridden __str__, __getitem__ or __iter__ can never change the
    # bytes. A float subclass is still a float (non_integer).
    kind = type(value)
    if value is None:
        return "null"
    if kind is bool:  # before int: True is an int
        return "true" if value else "false"
    if isinstance(value, float):
        raise CanonicalJsonError("non_integer", f"floats are not allowed: {value!r}")
    if kind is int:
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalJsonError("integer_out_of_range", "integer outside the safe range")
        return int.__repr__(value)
    if kind is str:
        return _write_string(value)
    if kind is list or kind is dict:
        if ancestors > MAX_DEPTH:
            raise CanonicalJsonError("unsupported_value", f"nested more than {MAX_DEPTH} deep")
        if kind is list:
            return "[" + ",".join(_write(item, ancestors + 1) for item in value) + "]"
        for key in value:
            if type(key) is not str:
                raise CanonicalJsonError("unsupported_value", f"non-string key: {key!r}")
        parts = [
            f"{_write_string(key)}:{_write(value[key], ancestors + 1)}"
            for key in sorted(value)  # code point order
        ]
        return "{" + ",".join(parts) + "}"
    raise CanonicalJsonError("unsupported_value", f"cannot serialize {kind.__name__}")


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


def _reject_lone_surrogates(root: Any) -> None:
    """Reject a lone surrogate escape in any string or key of a parsed document (section 5.6).

    ``json`` joins a valid high-then-low escape pair into one code point, so any surrogate left
    in a parsed string is lone.
    """
    todo = [root]
    while todo:
        node = todo.pop()
        if isinstance(node, str):
            if has_lone_surrogate(node):
                raise WireParseError("lone surrogate escape in a string")
        elif isinstance(node, list):
            todo.extend(node)
        elif isinstance(node, dict):
            for key, item in node.items():
                todo.append(key)
                todo.append(item)


def loads_wire(data: str | bytes) -> Any:
    """Parse incoming JSON the way section 5.6 requires.

    Every number token with ``.``, ``e`` or ``E`` is rejected, as are integers outside the safe
    range, ``NaN``/``Infinity`` tokens, duplicate object keys, lone surrogate escapes (a high
    or low half of a pair without its partner) and bytes that are not UTF-8. There is no size
    limit: the transport caps message size (section 4.3).

    Raises:
        WireParseError: (``code`` ``invalid_event``) for any of the above or malformed JSON.
    """
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        parsed = json.loads(
            text,
            parse_float=_reject_float,
            parse_int=_parse_int,
            parse_constant=_reject_constant,
            object_pairs_hook=_no_duplicate_keys,
        )
        _reject_lone_surrogates(parsed)
        return parsed
    except WireParseError:
        raise
    except (ValueError, RecursionError) as exc:  # bad UTF-8, malformed JSON, huge int token
        raise WireParseError(str(exc)) from exc
