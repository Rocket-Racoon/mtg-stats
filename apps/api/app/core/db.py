"""Async database access (S-1.4.2): engine, sessions, declarative base, mixins, after-commit hooks.

The engine and session factory are created lazily from settings, so importing this module never
connects. The request-scoped dependency :func:`get_session` commits when the handler succeeds and
rolls back when it raises. Routers must declare it through :data:`SessionDep`, which uses
``scope="function"`` so the commit happens before the response is sent; a commit failure then
becomes an error response instead of a 200 with lost data.

Schema conventions for models built on :class:`Base`:

- ``Boolean`` and ``Enum`` columns created with ``create_constraint=True`` need an explicit
  ``name=`` because the ``ck`` naming convention uses the constraint name.
- Commit through ``session.commit()``. A commit that bypasses it (``async with session.begin():``,
  ``AsyncSessionTransaction.commit()``) does not run :func:`after_commit` callbacks; they are
  logged and dropped when the next transaction starts, or when the session is closed or rolled
  back.
- Only the :class:`TimestampMixin` columns validate at assignment. Any other ``Mapped[datetime]``
  column is typed :class:`UtcDateTime` and rejects naive values at flush time instead.
"""

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any, ClassVar
from uuid import UUID

from fastapi import Depends
from sqlalchemy import DateTime, MetaData, event
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    SessionTransaction,
    mapped_column,
    validates,
)
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.ids import uuid7

logger = logging.getLogger(__name__)

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

AfterCommitCallback = Callable[[], Awaitable[None] | None]
_AFTER_COMMIT_INFO_SLOT = "after_commit_callbacks"

_Entry = tuple[int, AfterCommitCallback]


@dataclass
class _CallbackState:
    """After-commit callbacks of one session, scoped to the (nested) transaction that owns them.

    Each callback is stored as ``(sequence, callback)``; the sequence number restores global
    registration order when a savepoint's callbacks are merged into its parent.
    """

    frames: dict[SessionTransaction, list[_Entry]] = field(default_factory=dict)
    unstarted: list[_Entry] = field(default_factory=list)
    committed: set[SessionTransaction] = field(default_factory=set)
    ready: list[_Entry] = field(default_factory=list)
    next_seq: int = 0


def _state(session: Session | AsyncSession) -> _CallbackState:
    state: _CallbackState | None = session.info.get(_AFTER_COMMIT_INFO_SLOT)
    if state is None:
        state = session.info[_AFTER_COMMIT_INFO_SLOT] = _CallbackState()
    return state


# The three hooks below scope after-commit callbacks to the transaction or savepoint they were
# registered in. SQLAlchemy offers no other single point that sees both
# ``async with session.begin_nested()`` and an explicit ``nested.rollback()``. They are session
# plumbing, not business logic, so the "no ORM event listeners" rule does not apply; they are
# attached per AppSession instance and live only here.
def _on_transaction_create(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None and not transaction.nested:
        state = _state(session)
        if state.ready:  # left by a commit that bypassed AppSession.commit
            logger.warning(
                "dropping %d after_commit callback(s) from a commit that did not go through "
                "session.commit()",
                len(state.ready),
            )
            state.ready.clear()
        if state.unstarted:  # registered before the session autobegan
            state.frames.setdefault(transaction, []).extend(state.unstarted)
            state.unstarted = []


def _on_commit(session: Session) -> None:
    # Fires for a savepoint release and for the root commit, while the committing transaction is
    # still the innermost one.
    committing = session.get_nested_transaction() or session.get_transaction()
    if committing is not None:
        _state(session).committed.add(committing)


def _on_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    state = _state(session)
    callbacks = state.frames.pop(transaction, [])
    committed = transaction in state.committed
    state.committed.discard(transaction)
    if not committed:
        return  # rolled back (or closed): drop its callbacks, children's included
    parent = transaction.parent
    if transaction.nested and parent is not None:
        state.frames.setdefault(parent, []).extend(callbacks)  # savepoint released
    else:
        state.ready.extend(callbacks)  # outermost commit: AppSession.commit runs them


class UtcDateTime(TypeDecorator[datetime]):
    """``timestamptz`` that rejects naive datetimes and always returns UTC-aware values."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Reject naive datetimes; normalise aware ones to UTC."""
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"expected a datetime, got {type(value).__name__}")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime is not allowed; use a timezone-aware datetime")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Return the stored value as UTC."""
        return None if value is None else value.astimezone(UTC)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base with the shared naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UtcDateTime,
        UUID: PG_UUID(as_uuid=True),
    }


class IdMixin:
    """Primary key ``id``: a UUID v7 generated by the application, stored as Postgres ``uuid``."""

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)


class TimestampMixin:
    """``created_at`` and ``updated_at`` as UTC ``timestamptz``; naive datetimes are rejected."""

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow, onupdate=_utcnow)

    @validates("created_at", "updated_at")
    def _require_aware(self, key: str, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError(f"{key}: naive datetime is not allowed; use a timezone-aware datetime")
        return value


class AppSession(AsyncSession):
    """``AsyncSession`` whose ``commit`` runs :func:`after_commit` callbacks.

    Guarantees:

    - ``commit`` commits and then runs the registered callbacks in order, as one inner task.
    - If the commit fails, or ``rollback``/``close`` is called, the callbacks are dropped.
    - Callbacks are scoped to the transaction they were registered in. Rolling back a savepoint
      (``begin_nested()``) drops the callbacks registered inside it, nested savepoints included;
      releasing it hands them to the enclosing transaction. Only the outermost commit runs them.
      (In tests, "outermost" is the session's own transaction, even when its connection is
      joined to an outer transaction with ``create_savepoint``.)
    - Cancelling the caller of ``commit`` (``task.cancel()`` or an anyio cancel scope) does not
      interrupt that inner task. ``commit`` keeps waiting until the commit and all callbacks have
      finished, and only then re-raises ``CancelledError``. A commit failure after cancellation is
      logged, since the caller can no longer receive it.
    - An ``AppSession`` must not be used concurrently by two tasks (SQLAlchemy does not support
      it); ``commit`` never returns while its inner task runs, so ``rollback`` and ``close``
      cannot overlap a commit in normal use.
    """

    _pending_commit: asyncio.Task[None] | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        event.listen(self.sync_session, "after_transaction_create", _on_transaction_create)
        event.listen(self.sync_session, "after_commit", _on_commit)
        event.listen(self.sync_session, "after_transaction_end", _on_transaction_end)

    async def commit(self) -> None:
        """Commit, then run the after-commit callbacks; both complete even if cancelled."""
        task = asyncio.create_task(self._commit_and_run_callbacks())
        self._pending_commit = task  # strong reference, as the asyncio docs require
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done() and not task.cancelled() and task.exception() is None:
                raise
            task.add_done_callback(_log_abandoned_commit)
            await _wait_until_done(task)
            raise
        finally:
            if task.done() and self._pending_commit is task:
                self._pending_commit = None

    async def _commit_and_run_callbacks(self) -> None:
        try:
            await super().commit()
        except BaseException:
            _drop_callbacks(self)
            raise
        state = _state(self)
        ready, state.ready = state.ready, []
        for _, callback in sorted(ready, key=lambda item: item[0]):
            try:
                result: Any = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("after_commit callback failed")

    async def rollback(self) -> None:
        """Roll back and drop the after-commit callbacks."""
        _drop_callbacks(self)
        await super().rollback()

    async def close(self) -> None:
        """Close and drop any after-commit callbacks that never ran."""
        _drop_callbacks(self)
        await super().close()


def _drop_callbacks(session: AsyncSession) -> None:
    state = _state(session)
    if state.ready:  # only a commit that bypassed AppSession.commit leaves callbacks here
        logger.warning(
            "after-commit callbacks dropped: commit bypassed AppSession.commit (%d callback(s))",
            len(state.ready),
        )
    state.frames.clear()
    state.unstarted.clear()
    state.ready.clear()


async def _wait_until_done(task: asyncio.Task[None]) -> bool:
    """Wait for ``task`` to finish, absorbing cancellation; return True if cancelled meanwhile."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True  # also the level-triggered cancel of an anyio scope: keep waiting
        except Exception:
            break  # the task failed; it is done and reported by the caller or the logger
    return cancelled


def _log_abandoned_commit(task: asyncio.Task[None]) -> None:
    """Log a commit failure that happened after its caller was cancelled."""
    if task.cancelled():
        logger.error("commit task was cancelled after its caller was cancelled")
    elif (exc := task.exception()) is not None:
        logger.error("commit failed after its caller was cancelled", exc_info=exc)


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, created on first use from settings."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": settings.db_connect_timeout},
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AppSession]:
    """Return the process-wide session factory (:class:`AppSession`, ``expire_on_commit=False``)."""
    return async_sessionmaker(get_engine(), class_=AppSession, expire_on_commit=False)


async def dispose_engine() -> None:
    """Dispose the engine if one was created, and reset the caches (app shutdown)."""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
    get_sessionmaker.cache_clear()
    get_engine.cache_clear()


def after_commit(session: AsyncSession, callback: AfterCommitCallback) -> None:
    """Register ``callback`` to run after ``session`` commits; it is dropped on rollback.

    Callbacks run, in registration order, inside :meth:`AppSession.commit`. Services running in a
    caller's session use this instead of enqueueing or publishing directly. A failing callback is
    logged and does not stop the others, because the transaction is already committed.

    Callbacks are scoped to the transaction or savepoint they are registered in: if that savepoint
    is rolled back they are dropped; if it is released they pass to the enclosing transaction and
    run after the outermost commit.

    Commit through ``session.commit()``: a commit that bypasses it (``session.begin()`` as a
    context manager, ``AsyncSessionTransaction.commit()``) never runs the callbacks, which are
    logged and dropped when the next transaction starts, or when the session is closed or
    rolled back.

    Raises:
        TypeError: if ``session`` is not an :class:`AppSession`, since the callback would never run.
    """
    if not isinstance(session, AppSession):
        raise TypeError(
            "after_commit requires an AppSession; build sessions with get_sessionmaker()"
        )
    state = _state(session)
    entry = (state.next_seq, callback)
    state.next_seq += 1
    owner = session.sync_session.get_nested_transaction() or session.sync_session.get_transaction()
    if owner is None:
        state.unstarted.append(entry)
    else:
        state.frames.setdefault(owner, []).append(entry)


async def get_session() -> AsyncIterator[AppSession]:
    """Request-scoped session: commit on success, roll back on error, always close.

    The one exception: a :class:`~app.core.errors.DomainError` with ``persist_changes = True``
    commits first, then is re-raised (the response is still the error).

    Declare it with :data:`SessionDep`, not a bare ``Depends(get_session)``.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except DomainError as exc:
            if exc.persist_changes:
                await session.commit()
            else:
                await session.rollback()
            raise
        except BaseException:
            await session.rollback()
            raise


SessionDep = Annotated[AppSession, Depends(get_session, scope="function")]
"""Router parameter type. ``scope="function"`` ends the dependency (the commit) before the response
is sent, so commit-time failures reach the client as errors."""
