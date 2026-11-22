"""Rebuild a game's state snapshot from its event log (S-6.3.8).

The log is the truth and ``games.state`` is a cache of its fold (CONTRACT.md section 6.5), so a
damaged or out-of-date snapshot is repaired by replaying the log. :func:`rebuild_state` runs in
the caller's session and **never commits**: the caller owns the transaction, exactly as for
:func:`app.games.services.events.append_event`.

The snapshot is not trusted and is not even parsed. The replay starts from
``initial_state(str(game_id))`` and folds every stored event with the reducer. That is complete
because a real game's log starts with ``game_created``, which replaces the placeholder settings
(CONTRACT.md 3.5, 4.4.2); the game row holds no other input to the state. The one thing the log
and the row cannot say is the placeholder settings of a game whose log does not start with
``game_created`` (or has no events): those are rebuilt with the contract defaults.

Locking. The game row is taken with :func:`repository.lock_game` (``FOR NO KEY UPDATE``), the lock
``append_event`` takes, before the log is read, and it is held until the caller's transaction
ends. No event can be appended between the read of the log and the write of the snapshot. Do no
network I/O while holding it.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.games import repository
from app.games.core import (
    CanonicalJsonError,
    GameState,
    ReplayError,
    canonical_bytes,
    canonical_json,
    initial_state,
    replay,
)
from app.games.services.events import read_events_since

logger = logging.getLogger(__name__)

MAX_LOGGED_KEYS = 20
MAX_LOGGED_KEY_LENGTH = 64
_MISSING = object()


class RebuildError(RuntimeError):
    """The log cannot be replayed into a state; nothing was written (needs a human)."""

    def __init__(self, game_id: UUID, message: str) -> None:
        super().__init__(f"game {game_id}: {message}")
        self.game_id = game_id


class LogGapError(RebuildError):
    """The stored seqs are not ``1..n`` without a hole: an event is missing from the log."""

    def __init__(self, game_id: UUID, expected: int, found: int) -> None:
        super().__init__(game_id, f"the log has seq {found} where seq {expected} was expected")
        self.expected = expected
        self.found = found


class EventRejectedError(RebuildError):
    """The reducer rejects a stored event, so the log has no state (a corrupt log, or rules that
    changed since the event was stored). ``seq`` is the event's seq and ``code`` the contract's
    reducer error code.
    """

    def __init__(self, game_id: UUID, seq: int, code: str) -> None:
        super().__init__(game_id, f"replay rejects the event at seq {seq} with {code}")
        self.seq = seq
        self.code = code


@dataclass(frozen=True)
class Rebuilt:
    """What :func:`rebuild_state` found and did.

    ``changed`` is true when the stored snapshot or ``games.last_seq`` differed from the replay.
    ``written`` is true when the replay was written (``changed`` and not a dry run). ``state`` is
    the replayed state. ``old_last_seq`` is the ``games.last_seq`` column before the rebuild.
    ``differing_keys`` are the top-level state keys whose stored value was not the replayed one
    (sorted; empty when only the ``games.last_seq`` column differed).
    ``settings_from_log`` is false when the log is empty or does not start with ``game_created``:
    the settings then are the contract defaults, not anything the log says.
    ``log_behind_snapshot`` is true when the old column or the old snapshot's integer ``last_seq``
    is greater than the event count: events may have been lost after the last stored seq, and
    their seqs may already have been acknowledged to clients.
    """

    game_id: UUID
    changed: bool
    written: bool
    event_count: int
    old_last_seq: int
    new_last_seq: int
    differing_keys: tuple[str, ...]
    state: GameState
    settings_from_log: bool
    log_behind_snapshot: bool


def _int_or_none(value: Any) -> int | None:
    """``value`` if it is an ``int`` (not a ``bool``), else ``None``."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _logged_last_seq(snapshot: Any) -> str:
    """The snapshot's ``last_seq`` for the log: an integer, else only the name of its type."""
    if not isinstance(snapshot, dict):
        return type(snapshot).__name__
    if "last_seq" not in snapshot:
        return "missing"
    value = snapshot["last_seq"]
    number = _int_or_none(value)
    return str(number) if number is not None else type(value).__name__


def _canon(value: Any) -> str | None:
    try:
        return canonical_json(value)
    except CanonicalJsonError:
        return None


def _fingerprint(value: Any) -> str:
    """A short hash of ``value``'s canonical JSON, for the log; never the value itself."""
    try:
        return hashlib.sha256(canonical_bytes(value)).hexdigest()[:12]
    except CanonicalJsonError:
        return "unreadable"


def _differing_keys(stored: Any, rebuilt: dict[str, Any]) -> tuple[str, ...]:
    """Top-level keys where the stored snapshot is not the rebuilt state (all, if not an object)."""
    if not isinstance(stored, dict):
        return tuple(sorted(rebuilt))
    differing = []
    for key in {*stored, *rebuilt}:
        old = stored.get(key, _MISSING)
        new = rebuilt.get(key, _MISSING)
        if old is _MISSING or new is _MISSING or _canon(old) is None or _canon(old) != _canon(new):
            differing.append(key)
    return tuple(sorted(differing, key=str))


def _log_keys(keys: tuple[str, ...]) -> str:
    shown = ",".join(key[:MAX_LOGGED_KEY_LENGTH] for key in keys[:MAX_LOGGED_KEYS])
    return shown + (",..." if len(keys) > MAX_LOGGED_KEYS else "")


async def rebuild_state(
    session: AsyncSession,
    game_id: UUID,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> Rebuilt:
    """Replace the game's snapshot and ``last_seq`` with the replay of its log.

    Steps, in the caller's transaction: lock the game row (the lock of ``append_event``), read the
    whole log in seq order, check the seqs are ``1..n``, fold the events with the reducer from
    ``initial_state(str(game_id))``, compare the result with the stored ``games.state`` and
    ``games.last_seq`` and, when they differ and this is not a ``dry_run``, write the replay.

    The stored snapshot is never parsed, so a snapshot of the wrong shape, with missing or extra
    keys, or of another game is repaired like any other. A mismatch is logged as
    ``games.state_rebuild_mismatch`` (game id, ``last_seq`` before and after, the differing
    top-level key names, a short hash of each state and the two flags of :class:`Rebuilt`; never a
    state, a payload or a raw value of the damaged snapshot) at WARNING, or at ERROR when the log
    is behind the snapshot. A snapshot that already matches is logged at INFO as
    ``games.state_rebuild_match`` and nothing is written. ``game_events`` is only read.

    **Rules for the caller** (those of :func:`append_event`, which takes the same lock).

    - This function never commits. The game row stays locked (``FOR NO KEY UPDATE``) until the
      caller's transaction ends: commit promptly (roll back for a dry run) and do no network I/O
      in between.
    - Lock order: take any other explicit row lock first and none while holding the game row.
    - The game row is loaded with ``populate_existing``, which overwrites pending changes to it:
      the caller must have no unflushed changes to the game row.
    - On any exception nothing was written, and the caller rolls back.

    Raises:
        NotFoundError: there is no game ``game_id``.
        LogGapError: the log has a hole in its seqs (an event was lost).
        EventRejectedError: the reducer rejects a stored event, so no state exists.
        RebuildError: the replay ended at a ``last_seq`` other than the event count (a bug).
        ValueError: ``now`` is a naive datetime.
    """
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("now must be a timezone-aware datetime")
    game = await repository.lock_game(session, game_id)
    if game is None:
        raise NotFoundError("Game not found.")
    stored = await read_events_since(session, game_id, 0)
    for index, event in enumerate(stored, start=1):
        if event.seq != index:
            raise LogGapError(game_id, index, event.seq)
    events = [event.to_wire() for event in stored]
    outcome = replay(initial_state(str(game_id)), events)
    if isinstance(outcome, ReplayError):
        raise EventRejectedError(game_id, outcome.index + 1, outcome.error.code)
    state = outcome.state
    count = len(stored)
    if state.last_seq != count:  # every handler sets last_seq; a bug if it does not
        raise RebuildError(game_id, f"replay ended at last_seq {state.last_seq} for {count} events")

    value = state.to_json_value()
    keys = _differing_keys(game.state, value)
    old_last_seq = game.last_seq
    changed = bool(keys) or old_last_seq != count
    settings_from_log = bool(stored) and stored[0].type == "game_created"
    snapshot_seq = (
        _int_or_none(game.state.get("last_seq")) if isinstance(game.state, dict) else None
    )
    behind = old_last_seq > count or (snapshot_seq is not None and snapshot_seq > count)
    if not changed:
        logger.info(
            "games.state_rebuild_match game=%s last_seq=%s events=%s settings_from_log=%s",
            game_id,
            count,
            count,
            settings_from_log,
            extra={"game_id": str(game_id)},
        )
    else:
        logger.log(
            logging.ERROR if behind else logging.WARNING,
            "games.state_rebuild_mismatch game=%s old_last_seq=%s snapshot_last_seq=%s "
            "new_last_seq=%s events=%s differing_keys=%s old_hash=%s new_hash=%s written=%s "
            "settings_from_log=%s log_behind_snapshot=%s",
            game_id,
            old_last_seq,
            _logged_last_seq(game.state),
            count,
            count,
            _log_keys(keys),
            _fingerprint(game.state),
            _fingerprint(value),
            not dry_run,
            settings_from_log,
            behind,
            extra={"game_id": str(game_id)},
        )
        if not dry_run:
            repository.save_snapshot(
                game, state=value, last_seq=count, now=now or datetime.now(UTC)
            )
            await session.flush()
    return Rebuilt(
        game_id=game_id,
        changed=changed,
        written=changed and not dry_run,
        event_count=count,
        old_last_seq=old_last_seq,
        new_last_seq=count,
        differing_keys=keys,
        state=state,
        settings_from_log=settings_from_log,
        log_behind_snapshot=behind,
    )
