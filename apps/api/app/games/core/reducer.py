"""The reducer entry point ``apply(state, event)`` (CONTRACT.md section 6).

Pure: no clock, randomness or I/O. ``ts`` is never read; time and chance arrive in payloads.
Every handler is a no-op that only advances ``last_seq`` until the story that introduces it
(section 4, "Introduced by") fills it in.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError

from app.games.core.canonical import MAX_SAFE_INTEGER, CanonicalJsonError, canonical_json
from app.games.core.contract import (
    EVENT_TYPES,
    SUPPORTED_VERSIONS,
    EventEnvelope,
    GameState,
    ReducerErrorCode,
)


@dataclass(frozen=True)
class ReducerError:
    """A rejected event; the caller keeps the previous state."""

    code: ReducerErrorCode


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

Handler = Callable[[GameState, EventEnvelope, int], GameState | ReducerError]
"""A handler gets the state, the validated envelope and the seq to record; returns the new state."""


def _noop(state: GameState, event: EventEnvelope, seq: int) -> GameState:
    return state.model_copy(update={"last_seq": seq})


REGISTRY: Final[Mapping[str, Handler]] = dict.fromkeys(EVENT_TYPES, _noop)
"""One handler per registered type (section 4)."""


def _reject(code: ReducerErrorCode) -> ApplyError:
    return ApplyError(ReducerError(code))


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and abs(value) <= MAX_SAFE_INTEGER


def apply(state: GameState, event: Any) -> ApplyResult:
    """Apply one event to ``state`` and return the new state or the first rejection.

    ``event`` is a raw envelope (a ``dict`` as parsed from the wire). Checks run in the order of
    section 6.2. Neither argument is mutated, and a rejected event changes nothing.
    """
    if not isinstance(event, dict) or not _is_integer(event.get("v")):
        return _reject("invalid_event")
    if event["v"] not in SUPPORTED_VERSIONS:
        return _reject("unsupported_version")
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return _reject("invalid_event")
    if event_type not in REGISTRY:
        return _reject("unknown_event_type")
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
