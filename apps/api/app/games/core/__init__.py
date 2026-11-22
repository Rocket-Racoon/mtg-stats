"""Pure game core: contract models, canonical JSON and the reducer (S-6.1.1).

Nothing in this package reads a clock, randomness, the environment or any I/O (CONTRACT.md
section 7); ``app/games/tests/test_purity.py`` guards that.
"""

from app.games.core.canonical import (
    CanonicalJsonError,
    WireParseError,
    canonical_bytes,
    canonical_json,
    loads_wire,
)
from app.games.core.contract import (
    EVENT_TYPES,
    REDUCER_ERROR_CODES,
    SCHEMA_VERSION,
    Actor,
    Commander,
    EventEnvelope,
    GameState,
    ReducerError,
    Seat,
    Settings,
)
from app.games.core.reducer import (
    ApplyError,
    ApplyOk,
    ApplyResult,
    ReplayError,
    ReplayOk,
    ReplayResult,
    apply,
    reduce,
    replay,
)
from app.games.core.state import initial_state

__all__ = [
    "EVENT_TYPES",
    "REDUCER_ERROR_CODES",
    "SCHEMA_VERSION",
    "Actor",
    "ApplyError",
    "ApplyOk",
    "ApplyResult",
    "CanonicalJsonError",
    "Commander",
    "EventEnvelope",
    "GameState",
    "ReducerError",
    "ReplayError",
    "ReplayOk",
    "ReplayResult",
    "Seat",
    "Settings",
    "WireParseError",
    "apply",
    "canonical_bytes",
    "canonical_json",
    "initial_state",
    "loads_wire",
    "reduce",
    "replay",
]
