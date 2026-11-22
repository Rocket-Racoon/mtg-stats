"""Ordered, idempotent, atomic append to a game's event log (S-6.3.1).

:func:`append_event` runs in the caller's session and never commits: the event row, ``games.state``
and ``games.last_seq`` are written in the caller's transaction, so a failure that rolls it back
leaves none of them changed. The caller owns the commit.

Follow-ups of a committed append (publishing to the game's Redis channel, enqueueing ``stats.*``
jobs) belong to later stories. The caller registers them with
``core.db.after_commit(session, callback)`` from the :class:`Appended` result; this module never
publishes or enqueues.

Ordering. The game row is locked with ``SELECT ... FOR UPDATE``, so appends to one game are
serialised, even across processes. Under the lock the service reads ``last_seq``, assigns
``last_seq + 1``, applies the reducer to the stored state and writes the event and the snapshot.
The unique constraints on ``(game_id, seq)`` and ``(game_id, event_id)`` are a backstop only.
The server sets ``seq``, ``ts`` and ``actor``; whatever the client sent for them is ignored.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.games import repository
from app.games.core import (
    Actor,
    ApplyError,
    CanonicalJsonError,
    GameState,
    apply,
    canonical_json,
)
from app.games.core.contract import EVENT_ID_PATTERN, ReducerErrorCode
from app.games.models import GameEvent

logger = logging.getLogger(__name__)

_SERVER_SET_KEYS = ("seq", "ts", "actor")


class GameLogCorruptError(RuntimeError):
    """The game's snapshot and its ``last_seq`` disagree; the snapshot needs a rebuild (S-6.3.8)."""


@dataclass(frozen=True)
class StoredEvent:
    """An event as stored, in the contract's envelope terms (CONTRACT.md section 2).

    ``ts`` is the wire string ``YYYY-MM-DDTHH:MM:SS.mmmZ``; ``event_id`` and ``game_id`` are
    lowercase hyphenated strings.
    """

    event_id: str
    game_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    actor: dict[str, Any]
    on_behalf_of_seat: int | None
    ts: str
    v: int

    def to_wire(self) -> dict[str, Any]:
        """The full envelope, with ``seq`` and ``ts`` present, ready for ``canonical_json``."""
        return {
            "event_id": self.event_id,
            "game_id": self.game_id,
            "seq": self.seq,
            "type": self.type,
            "payload": self.payload,
            "actor": self.actor,
            "on_behalf_of_seat": self.on_behalf_of_seat,
            "ts": self.ts,
            "v": self.v,
        }


@dataclass(frozen=True)
class Appended:
    """The event is in the log.

    ``duplicate`` is true when its ``event_id`` was already stored: ``event`` is the stored one
    with its original ``seq``, nothing was written, and the caller must not publish it again.
    ``body_differs`` is true when such a duplicate carries another type, payload or
    ``on_behalf_of_seat`` than the stored event (a client bug; the stored event still wins).
    ``state`` is the game's state after ``event`` for a new event, and the current state for a
    duplicate.
    """

    event: StoredEvent
    state: GameState
    duplicate: bool = False
    body_differs: bool = False


@dataclass(frozen=True)
class Rejected:
    """The reducer (or a storage limit) rejected the event: nothing was written.

    ``code`` is a CONTRACT.md section 6.2 code, ready for the ``reject`` message.
    """

    code: ReducerErrorCode


AppendResult = Appended | Rejected


def format_ts(moment: datetime) -> str:
    """The contract's ``ts`` string for ``moment``: UTC, exactly three fractional digits."""
    utc = moment.astimezone(UTC)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond // 1000:03d}Z"


def _truncate_to_ms(moment: datetime) -> datetime:
    """Drop microseconds below a millisecond, so the stored ``ts`` equals its wire string."""
    utc = moment.astimezone(UTC)
    return utc.replace(microsecond=utc.microsecond // 1000 * 1000)


def _to_stored(row: GameEvent) -> StoredEvent:
    return StoredEvent(
        event_id=str(row.event_id),
        game_id=str(row.game_id),
        seq=row.seq,
        type=row.type,
        payload=row.payload,
        actor=row.actor,
        on_behalf_of_seat=row.on_behalf_of_seat,
        ts=format_ts(row.ts),
        v=row.v,
    )


def _contains_nul(value: Any) -> bool:
    """True when a string or key anywhere in ``value`` has U+0000, which ``jsonb`` cannot store."""
    todo = [value]
    while todo:
        node = todo.pop()
        if isinstance(node, str):
            if "\x00" in node:
                return True
        elif isinstance(node, list):
            todo.extend(node)
        elif isinstance(node, dict):
            for key, item in node.items():
                todo.append(key)
                todo.append(item)
    return False


def _parse_event_id(value: Any) -> UUID | None:
    """The UUID of a well-formed ``event_id`` string, else ``None`` (the reducer rejects it)."""
    if isinstance(value, str) and re.fullmatch(EVENT_ID_PATTERN, value):
        return UUID(value)
    return None


def _same_body(stored: GameEvent, submission: Mapping[str, Any]) -> bool:
    try:
        return (
            stored.type == submission.get("type")
            and stored.on_behalf_of_seat == submission.get("on_behalf_of_seat")
            and canonical_json(stored.payload) == canonical_json(submission.get("payload"))
        )
    except CanonicalJsonError:
        return False


def _load_state(game_id: UUID, snapshot: dict[str, Any], last_seq: int) -> GameState:
    try:
        state = GameState.model_validate(snapshot)
    except ValidationError as exc:
        raise GameLogCorruptError(f"game {game_id}: the stored snapshot is invalid") from exc
    if state.last_seq != last_seq or state.game_id != str(game_id):
        raise GameLogCorruptError(
            f"game {game_id}: snapshot has last_seq {state.last_seq} and game_id "
            f"{state.game_id}, the row has last_seq {last_seq}"
        )
    return state


def _duplicate(game_id: UUID, stored: GameEvent, submission: Any, state: GameState) -> Appended:
    differs = not (isinstance(submission, Mapping) and _same_body(stored, submission))
    if differs:
        logger.warning(
            "event_id %s of game %s was resubmitted with a different body", stored.event_id, game_id
        )
    return Appended(event=_to_stored(stored), state=state, duplicate=True, body_differs=differs)


async def append_event(
    session: AsyncSession,
    game_id: UUID,
    submission: Any,
    *,
    actor: Actor,
    now: datetime | None = None,
) -> AppendResult:
    """Append one client event to the game's log, in the caller's transaction.

    ``submission`` is the raw envelope as the client sent it (a ``dict`` from
    ``loads_wire``). ``actor`` is who the server authenticated; the client's ``actor``, ``seq``
    and ``ts`` are replaced. ``now`` stamps ``ts`` (default: the current UTC time).

    - A new event the reducer accepts is stored at ``last_seq + 1`` together with the new
      ``games.state`` and ``games.last_seq``, and returned as :class:`Appended`.
    - An ``event_id`` already stored in this game returns the stored event (original ``seq``),
      writes nothing, and is marked ``duplicate``. The same id in another game is a different
      event: ids are unique per game. A duplicate is detected before the reducer runs, so it is
      answered even if the game has since closed.
    - An event the reducer rejects returns :class:`Rejected` with its code, and so does one whose
      payload has a U+0000 character (``jsonb`` cannot store it; the code is ``invalid_payload``).
      Nothing is written.

    Raises:
        NotFoundError: there is no game ``game_id``.
        GameLogCorruptError: the stored snapshot disagrees with ``last_seq``.
    """
    game = await repository.lock_game(session, game_id)
    if game is None:
        raise NotFoundError("Game not found.")
    state = _load_state(game_id, game.state, game.last_seq)

    event_id = _parse_event_id(submission.get("event_id")) if isinstance(submission, dict) else None
    if event_id is not None:
        stored = await repository.get_by_event_id(session, game_id, event_id)
        if stored is not None:
            return _duplicate(game_id, stored, submission, state)

    seq = game.last_seq + 1
    stamp = _truncate_to_ms(now or datetime.now(UTC))
    candidate: Any = submission
    if isinstance(submission, dict):
        candidate = {k: v for k, v in submission.items() if k not in _SERVER_SET_KEYS}
        candidate.update(seq=seq, ts=format_ts(stamp), actor=actor.model_dump(mode="json"))
    outcome = apply(state, candidate)
    if isinstance(outcome, ApplyError):
        return Rejected(outcome.error.code)
    if _contains_nul(candidate["payload"]):
        return Rejected("invalid_payload")

    event_id = UUID(candidate["event_id"])  # accepted by the reducer, so well formed
    try:
        async with session.begin_nested():  # a unique violation must not poison the caller
            row = await repository.append(
                session,
                game_id=game_id,
                seq=seq,
                event_id=event_id,
                type=candidate["type"],
                payload=candidate["payload"],
                actor=candidate["actor"],
                on_behalf_of_seat=candidate["on_behalf_of_seat"],
                ts=stamp,
                v=candidate["v"],
            )
    except IntegrityError:
        stored = await repository.get_by_event_id(session, game_id, event_id)
        if stored is None:  # not the event_id constraint: a real fault
            raise
        return _duplicate(game_id, stored, submission, state)
    repository.save_snapshot(game, state=outcome.state.to_json_value(), last_seq=seq, now=stamp)
    await session.flush()
    return Appended(event=_to_stored(row), state=outcome.state)


async def read_events_since(
    session: AsyncSession, game_id: UUID, seq: int, limit: int | None = None
) -> list[StoredEvent]:
    """Events of the game with ``seq`` greater than ``seq``, in ascending order (``sync``)."""
    return [_to_stored(row) for row in await repository.read_since(session, game_id, seq, limit)]


async def get_event(session: AsyncSession, game_id: UUID, event_id: UUID) -> StoredEvent | None:
    """The stored event with this client ``event_id`` in the game, or ``None``."""
    row = await repository.get_by_event_id(session, game_id, event_id)
    return None if row is None else _to_stored(row)


async def get_snapshot(session: AsyncSession, game_id: UUID) -> tuple[GameState, int]:
    """The game's stored state and ``last_seq`` (the ``welcome`` payload), without locking.

    Raises:
        NotFoundError: there is no game ``game_id``.
    """
    game = await repository.get_game(session, game_id)
    if game is None:
        raise NotFoundError("Game not found.")
    return _load_state(game_id, game.state, game.last_seq), game.last_seq
