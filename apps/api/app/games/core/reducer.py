"""The reducer entry point ``apply(state, event)`` (CONTRACT.md section 6).

Pure: no clock, randomness or I/O. ``ts`` is never read; time and chance arrive in payloads.
The lobby, seat and turn handlers (S-6.1.6, section 4.4) and the life, concession and
declared-elimination handlers (S-6.1.3, section 4.5) are in ``handlers.py``. Every other
handler is a no-op that only advances ``last_seq`` until the story that introduces it
(section 4, "Introduced by") fills it in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError

from app.games.core.canonical import (
    MAX_SAFE_INTEGER,
    CanonicalJsonError,
    canonical_json,
    has_lone_surrogate,
)
from app.games.core.contract import (
    EVENT_TYPES,
    SUPPORTED_VERSIONS,
    EventEnvelope,
    GameState,
    Handler,
    ReducerError,
    ReducerErrorCode,
)
from app.games.core.handlers import HANDLERS


@dataclass(frozen=True)
class ApplyOk:
    """The event was applied; ``state`` is a new state."""

    state: GameState
    ok: bool = True


@dataclass(frozen=True)
class ApplyError:
    """The event was rejected with ``error``."""

    error: ReducerError
    ok: bool = False


ApplyResult = ApplyOk | ApplyError


@dataclass(frozen=True)
class ReplayOk:
    """Every event of a replay was applied."""

    state: GameState
    ok: bool = True


@dataclass(frozen=True)
class ReplayError:
    """A replay stopped at the event at ``index`` with ``error``."""

    index: int
    error: ReducerError
    ok: bool = False


ReplayResult = ReplayOk | ReplayError


def _noop(state: GameState, event: EventEnvelope, seq: int) -> GameState:
    return state.model_copy(update={"last_seq": seq})


REGISTRY: Final[Mapping[str, Handler]] = {**dict.fromkeys(EVENT_TYPES, _noop), **HANDLERS}
"""One handler per registered type (section 4); the twelve real ones are in ``handlers.py``."""


def _reject(code: ReducerErrorCode) -> ApplyError:
    return ApplyError(ReducerError(code))


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and abs(value) <= MAX_SAFE_INTEGER


def _has_malformed_string(event: dict[str, Any]) -> bool:
    """True when an envelope string has a lone surrogate (section 2, well-formed strings)."""
    actor = event.get("actor")
    candidates = [event.get("event_id"), event.get("game_id"), event.get("ts")]
    if isinstance(actor, dict):
        candidates.append(actor.get("player_id"))
    return any(isinstance(c, str) and has_lone_surrogate(c) for c in candidates)


def _envelope_error(event: Any) -> ReducerErrorCode | None:
    """Steps 1 to 4 of section 6.2, which need no state: the code of the first failure, if any."""
    if not isinstance(event, dict) or not _is_integer(event.get("v")):
        return "invalid_event"
    if event["v"] not in SUPPORTED_VERSIONS:
        return "unsupported_version"
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return "invalid_event"
    if event_type not in REGISTRY:
        return "unknown_event_type"
    if _has_malformed_string(event):  # before the model, so no Pydantic accident decides
        return "invalid_event"
    return None


def apply(state: GameState, event: Any) -> ApplyResult:
    """Apply one event to ``state`` and return the new state or the first rejection.

    ``event`` is a raw envelope (a ``dict`` as parsed from the wire). Checks run in the order of
    section 6.2. Neither argument is mutated, and a rejected event changes nothing.
    """
    code = _envelope_error(event)
    if code is not None:
        return _reject(code)
    try:
        envelope = EventEnvelope.model_validate(event)
    except ValidationError:
        return _reject("invalid_event")
    try:
        canonical_json(envelope.payload)  # the payload is the top-level value (0 ancestors)
    except CanonicalJsonError:
        return _reject("invalid_payload")
    if envelope.game_id != state.game_id:
        return _reject("game_mismatch")
    expected_seq = state.last_seq + 1
    if envelope.seq is not None and envelope.seq != expected_seq:
        return _reject("out_of_order")
    outcome = REGISTRY[envelope.type](state, envelope, expected_seq)
    if isinstance(outcome, ReducerError):
        return ApplyError(outcome)
    return ApplyOk(outcome)


reduce = apply
"""Alias of :func:`apply` (section 6.1)."""


def replay(initial: GameState, events: Iterable[Any]) -> ReplayResult:
    """Fold :func:`apply` over ``events``; stop at the first rejection with its index."""
    state = initial
    for index, event in enumerate(events):
        result = apply(state, event)
        if isinstance(result, ApplyError):
            return ReplayError(index, result.error)
        state = result.state
    return ReplayOk(state)
