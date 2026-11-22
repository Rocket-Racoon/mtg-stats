"""Queries on the ``games`` domain's own tables.

``game_events`` is append-only (architecture 6.1). The public functions that touch it are exactly:

- :func:`append` inserts a row;
- :func:`read_since` and :func:`get_by_event_id` read rows;
- :func:`reassign_player` and :func:`delete_playgroup_data`, the two audited exceptions
  (S-2.3.4, S-2.6.3). They are interface stubs until those stories define the audit table.

**Status of the audited operations:** :func:`reassign_player` and :func:`delete_playgroup_data`
are stubs that raise ``NotImplementedError`` until S-2.3.4 and S-2.6.3. They are not exposed
through ``games.services`` yet, and no audit table exists (the decision on its owner and shape is
pending), so neither can write its audit row today.

``tests/architecture/test_game_events_append_only.py`` enforces that list and that no other
module issues UPDATE or DELETE against ``game_events``. The rest of this module touches the
``games`` table: :func:`lock_game`, :func:`get_game`, :func:`add_game` and :func:`save_snapshot`.

Lock order: a transaction locks the game row (``lock_game``) after any other lock it needs
(for example the playgroups' ``lock_active_memberships``), and takes no other row lock while it
holds the game row. The append path locks nothing else, so it cannot deadlock with them.
"""

from datetime import datetime
from typing import Any, NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.games.models import Game, GameEvent


def add_game(session: AsyncSession, game: Game) -> None:
    """Add a new game to the session (S-6.2.1 creates games; the caller flushes)."""
    session.add(game)


async def get_game(session: AsyncSession, game_id: UUID) -> Game | None:
    """The game row without locking it, or ``None``."""
    return await session.get(Game, game_id)


async def lock_game(session: AsyncSession, game_id: UUID) -> Game | None:
    """The game row locked with ``SELECT ... FOR UPDATE``, or ``None``.

    The lock serialises every append to the game, so ``last_seq + 1`` is race-free.
    ``populate_existing`` refreshes an instance already in the session, so ``state`` and
    ``last_seq`` are the committed ones, not a stale copy read before the lock wait.
    """
    result = await session.execute(
        select(Game)
        .where(Game.id == game_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


def save_snapshot(game: Game, *, state: dict[str, Any], last_seq: int, now: datetime) -> None:
    """Set the snapshot columns of a locked game; the session's flush writes them."""
    game.state = state
    game.last_seq = last_seq
    game.updated_at = now


async def get_by_event_id(session: AsyncSession, game_id: UUID, event_id: UUID) -> GameEvent | None:
    """The stored event of ``game_id`` with this client ``event_id``, or ``None``."""
    result = await session.execute(
        select(GameEvent).where(GameEvent.game_id == game_id, GameEvent.event_id == event_id)
    )
    return result.scalar_one_or_none()


async def read_since(
    session: AsyncSession, game_id: UUID, seq: int, limit: int | None = None
) -> list[GameEvent]:
    """Events of ``game_id`` with ``seq`` greater than the given one, in ascending ``seq``."""
    statement = (
        select(GameEvent)
        .where(GameEvent.game_id == game_id, GameEvent.seq > seq)
        .order_by(GameEvent.seq)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list((await session.execute(statement)).scalars())


async def append(
    session: AsyncSession,
    *,
    game_id: UUID,
    seq: int,
    event_id: UUID,
    type: str,
    payload: dict[str, Any],
    actor: dict[str, Any],
    on_behalf_of_seat: int | None,
    ts: datetime,
    v: int,
) -> GameEvent:
    """Insert one event row and flush it. The only function that creates ``game_events`` rows."""
    event = GameEvent(
        id=uuid7(),
        game_id=game_id,
        seq=seq,
        event_id=event_id,
        type=type,
        payload=payload,
        actor=actor,
        on_behalf_of_seat=on_behalf_of_seat,
        ts=ts,
        v=v,
    )
    session.add(event)
    await session.flush()
    return event


async def reassign_player(
    session: AsyncSession, *, from_player_id: UUID, to_player_id: UUID, performed_by: UUID
) -> NoReturn:
    """Audited exception 1 (S-2.3.4): point a guest's events and seats at the merged player.

    Not implemented: it needs the audit table and the seat table, which S-2.3.4 and S-6.2.1
    define. The real operation must run ``set_config(AUDITED_MUTATION_SETTING, 'on', true)`` in
    its transaction (the database trigger rejects the UPDATE otherwise) and write its audit row
    in that same transaction.
    """
    raise NotImplementedError("games.reassign_player arrives with S-2.3.4")


async def delete_playgroup_data(
    session: AsyncSession, *, playgroup_id: UUID, performed_by: UUID
) -> NoReturn:
    """Audited exception 2 (S-2.6.3): delete a playgroup's games and their events.

    Not implemented: it needs the audit table, which S-2.6.3 defines. Like
    :func:`reassign_player`, the real operation opts in to the trigger guard with
    ``set_config(AUDITED_MUTATION_SETTING, 'on', true)`` and audits in the same transaction.
    """
    raise NotImplementedError("games.delete_playgroup_data arrives with S-2.6.3")
