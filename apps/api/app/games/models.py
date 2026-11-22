"""ORM models of the ``games`` domain: ``Game`` (append path columns only) and ``GameEvent``.

S-6.3.1 creates the ``games`` table with only what the event-log append path needs: the id, the
owning playgroup, the ``state`` snapshot, ``last_seq`` and the timestamps. The lifecycle columns
(status, the four settings columns, host, short code, results) and ``GameSeat`` belong to S-6.2.1
and later stories, which extend the table by migration.

``game_events`` is append-only (CONTRACT.md, architecture section 6.1). Only the repository's
``append`` inserts rows; the two audited operations (``games.reassign_player``,
``games.delete_playgroup_data``) are the only code that may ever change or delete them. A database
trigger (migration ``games_event_log``) rejects UPDATE and DELETE unless the transaction opted in
with :data:`AUDITED_MUTATION_SETTING`.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampMixin, UtcDateTime

# A guard against accidents, not a security boundary: any connection can set it. The audited
# operations must set it with ``is_local = true`` (transaction scope), so it can never stay on in
# a pooled connection.
AUDITED_MUTATION_SETTING = "app.game_events_audited_mutation"
"""Transaction-local setting (``set_config(..., true)``) that lets the audited operations pass."""


class Game(IdMixin, TimestampMixin, Base):
    """A game: the lock target of the append path and the holder of the state snapshot.

    ``state`` is the reducer's output after the event at ``last_seq`` (CONTRACT.md section 3);
    both are written in the same transaction as the event row. The snapshot is always derivable
    from the log, so a damaged one can be rebuilt (S-6.3.8).
    """

    __tablename__ = "games"
    __table_args__ = (
        CheckConstraint("last_seq >= 0", name="last_seq_nonnegative"),
        CheckConstraint("jsonb_typeof(state) = 'object'", name="state_is_object"),
        # Playgroup deletion finds its games by this column; it also covers the foreign key.
        Index("ix_games_playgroup_id", "playgroup_id"),
    )

    # The reference is downward (games sits above playgroups, architecture 4.2). It has no
    # ``ondelete``: deleting a playgroup removes its games first, through
    # ``games.delete_playgroup_data`` (S-2.6.3), which is audited.
    playgroup_id: Mapped[UUID] = mapped_column(ForeignKey("playgroups.id"))
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    last_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class GameEvent(IdMixin, Base):
    """One event of a game's log, as the server accepted it (CONTRACT.md section 2).

    ``event_id`` is the client's UUID v4 (idempotency key), not the primary key. ``seq``, ``ts``
    and ``actor`` are set by the server. ``actor`` is the contract's object
    ``{"kind", "player_id"}``, stored as JSONB so the log keeps the envelope's shape.
    """

    __tablename__ = "game_events"
    __table_args__ = (
        # Each also serves lookups by its leading column ``game_id`` (and the foreign key). The
        # naming convention gives them ``uq_game_events_game_id_seq`` and ``..._game_id_event_id``.
        UniqueConstraint("game_id", "seq"),
        UniqueConstraint("game_id", "event_id"),
        CheckConstraint("seq >= 1", name="seq_positive"),
        CheckConstraint("v >= 1", name="v_positive"),
        CheckConstraint(
            "on_behalf_of_seat IS NULL OR on_behalf_of_seat BETWEEN 1 AND 8",
            name="on_behalf_of_seat_range",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_is_object"),
        CheckConstraint("jsonb_typeof(actor) = 'object'", name="actor_is_object"),
    )

    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"))
    seq: Mapped[int] = mapped_column(BigInteger)
    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    actor: Mapped[dict[str, Any]] = mapped_column(JSONB)
    on_behalf_of_seat: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    ts: Mapped[datetime] = mapped_column(UtcDateTime)
    v: Mapped[int] = mapped_column(SmallInteger)
