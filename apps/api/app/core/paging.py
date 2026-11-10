"""Cursor (keyset) paging shared by every list endpoint (S-1.4.7, decision B2).

A list endpoint takes :data:`PageParamsDep` (``?cursor=&limit=``), builds a ``Select`` and a
:class:`Sort`, and calls :func:`paginate`, which returns the items and the next cursor
(:func:`paginate_page` wraps them in a :class:`Page`).

Cursor format: an opaque base64url string (no padding) of the JSON list
``[sort_id, key_1, ..., key_n]`` where ``sort_id`` names the columns and directions
(``"created_at:d,id:d"``) and the keys are the last returned row's sort values, ending with the
unique ``id`` tie-breaker. Datetimes are ISO 8601 in UTC with microseconds, so they round-trip
exactly.

The cursor is validated, not signed. It carries only values the caller could already read from
the previous page, and it is used as bound query parameters, so forging one reveals nothing and
cannot reach rows the endpoint's own filters exclude (the filters stay in the ``Select``).
Signing would need a secret setting with no default (S-1.4.1 AC1) for no security gain.

Indexes: every list endpoint needs a composite index matching its :class:`Sort`, or keyset
paging degrades to a scan and sort. A ``(created_at, id)`` btree serves both ``created_at_asc``
and ``created_at_desc`` (an all-ascending or all-descending order can be read in either
direction). Mixed-direction sorts (e.g. ``name ASC, id DESC``) are expanded to an OR of ANDs and
are only partly index-assisted, even with an index declared in that exact order.

This module must not import any domain.
"""

import base64
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol, cast
from uuid import UUID

from fastapi import Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, Select, and_, literal, or_, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, Mapped
from sqlalchemy.types import BigInteger, Integer, SmallInteger, TypeDecorator, TypeEngine

from app.core.errors import InvalidCursorError

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_CURSOR_LENGTH = 4096


class Page[T](BaseModel):
    """One page of a list endpoint.

    Each list endpoint declares a named subclass as its response model, for example
    ``class DeckPage(Page[DeckOut]): pass``. The subclass name becomes the OpenAPI component
    (``DeckPage``); a bare ``Page[DeckOut]`` would be named ``Page_DeckOut_`` in the generated
    TypeScript client.
    """

    items: list[T]
    next_cursor: str | None = Field(
        description="Opaque cursor for the next page, or null on the last page."
    )


@dataclass(frozen=True, slots=True)
class PageParams:
    """Validated paging query parameters."""

    cursor: str | None
    limit: int


def page_params(
    cursor: Annotated[
        str | None,
        # No Query max_length: an over-long cursor must be `invalid_cursor` (decode_cursor checks
        # MAX_CURSOR_LENGTH first), not a generic validation_error.
        Query(description="Opaque cursor from `next_cursor`."),
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_LIMIT, description="Page size (1 to 200).")
    ] = DEFAULT_LIMIT,
) -> PageParams:
    """Dependency parsing ``?cursor=&limit=``; a ``limit`` outside 1..200 is a 422."""
    return PageParams(cursor=cursor, limit=limit)


PageParamsDep = Annotated[PageParams, Depends(page_params)]


@dataclass(frozen=True, slots=True)
class SortKey:
    """One sort column. ``attr`` is the entity attribute read back to build the next cursor."""

    column: InstrumentedAttribute[Any]
    descending: bool = False

    @property
    def attr(self) -> str:
        """Attribute name on the returned entity."""
        return self.column.key


@dataclass(frozen=True, slots=True)
class Sort:
    """A total order for keyset paging. The last key must be the unique ``id``."""

    keys: tuple[SortKey, ...]

    def __post_init__(self) -> None:
        if len(self.keys) < 2 or self.keys[-1].attr != "id":
            raise ValueError("a Sort needs at least two keys and must end with the id column")
        for key in self.keys:
            if getattr(key.column.expression, "nullable", None) is not False:
                # NULLs have no total order under keyset comparison (NULL < x is never true).
                raise ValueError(f"sort key {key.attr!r} must be a non-nullable column")
            try:
                python_type = _python_type(key.column.type)
            except NotImplementedError:
                python_type = object
            if python_type not in _SORT_KEY_TYPES:
                raise ValueError(
                    f"sort key {key.attr!r} has an unsupported type; "
                    "use datetime, UUID, int or str columns"
                )

    @property
    def ident(self) -> str:
        """Stable identifier of the order, embedded in cursors (e.g. ``created_at:d,id:d``)."""
        return ",".join(f"{k.attr}:{'d' if k.descending else 'a'}" for k in self.keys)

    def order_by(self) -> list[ColumnElement[Any]]:
        """ORDER BY clauses."""
        return [k.column.desc() if k.descending else k.column.asc() for k in self.keys]


class _Timestamped(Protocol):
    """A model with the ``IdMixin`` and ``TimestampMixin`` columns."""

    created_at: Mapped[datetime]
    id: Mapped[UUID]


def _attr(value: object) -> InstrumentedAttribute[Any]:
    return cast("InstrumentedAttribute[Any]", value)


def created_at_desc(model: type[_Timestamped]) -> Sort:
    """The newest-first order ``(created_at DESC, id DESC)``."""
    return Sort((SortKey(_attr(model.created_at), True), SortKey(_attr(model.id), True)))


def created_at_asc(model: type[_Timestamped]) -> Sort:
    """The oldest-first order ``(created_at, id)``, the default order of architecture section 5."""
    return Sort((SortKey(_attr(model.created_at)), SortKey(_attr(model.id))))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _encode_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime in sort key")
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise TypeError(f"unsupported sort key type {type(value).__name__}")
    return value


def _python_type(sql_type: TypeEngine[Any]) -> type:
    """Python type of a column type, looking through ``TypeDecorator`` wrappers."""
    while isinstance(sql_type, TypeDecorator):
        sql_type = sql_type.impl_instance
    python_type: type = sql_type.python_type
    return python_type


_SORT_KEY_TYPES: tuple[type, ...] = (datetime, UUID, int, str)


def _int_bounds(sql_type: TypeEngine[Any]) -> tuple[int, int]:
    """Inclusive range the database column accepts (a wider bound is a Postgres 500)."""
    while isinstance(sql_type, TypeDecorator):
        sql_type = sql_type.impl_instance
    if isinstance(sql_type, SmallInteger):
        bits = 16
    elif isinstance(sql_type, Integer) and not isinstance(sql_type, BigInteger):
        bits = 32
    else:
        bits = 64
    return -(2 ** (bits - 1)), 2 ** (bits - 1) - 1


def _decode_value(raw: Any, sql_type: TypeEngine[Any]) -> Any:
    python_type = _python_type(sql_type)
    if python_type is datetime:
        if not isinstance(raw, str):
            raise ValueError("expected a string")
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            raise ValueError("naive datetime")
        return parsed.astimezone(UTC)
    if python_type is UUID:
        if not isinstance(raw, str):
            raise ValueError("expected a string")
        return UUID(raw)
    if python_type is int:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError("expected an integer")
        low, high = _int_bounds(sql_type)
        if not low <= raw <= high:
            raise ValueError("integer out of range for the column")
        return raw
    if python_type is str:
        if not isinstance(raw, str):
            raise ValueError("expected a string")
        if "\x00" in raw:
            raise ValueError("NUL in string")  # Postgres text cannot hold it
        raw.encode("utf-8")  # a lone surrogate raises UnicodeEncodeError (a ValueError)
        return raw
    raise AssertionError(f"unsupported sort key type {python_type.__name__}")  # Sort checks


def encode_cursor(sort: Sort, values: Sequence[Any]) -> str:
    """Encode the sort values of the last item as an opaque cursor."""
    payload = [sort.ident, *(_encode_value(v) for v in values)]
    # ensure_ascii=False keeps non-ASCII strings at their UTF-8 size (ASCII escapes would inflate
    # them 6 to 12 times and could push a legitimate cursor over the limit).
    cursor = _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    if len(cursor) > MAX_CURSOR_LENGTH:
        # Server-side bug (an oversized sort key), not a client error: decode would reject it.
        raise ValueError(f"cursor of {len(cursor)} chars exceeds MAX_CURSOR_LENGTH")
    return cursor


def decode_cursor(sort: Sort, cursor: str) -> list[Any]:
    """Decode ``cursor`` for ``sort``; raise :class:`InvalidCursorError` if it is not valid."""
    try:
        if len(cursor) > MAX_CURSOR_LENGTH:
            raise ValueError("cursor too long")
        raw = base64.urlsafe_b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4))
        if _b64(raw) != cursor:  # reject non-canonical encodings
            raise ValueError("non-canonical base64")
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) != len(sort.keys) + 1:
            raise ValueError("wrong shape")
        if payload[0] != sort.ident:
            raise ValueError("cursor made for a different sort")
        return [
            _decode_value(item, key.column.type)
            for item, key in zip(payload[1:], sort.keys, strict=True)
        ]
    except (ValueError, OverflowError, RecursionError) as exc:
        # What a forged cursor can trigger: ValueError (bad base64, JSON, UUID, datetime,
        # integer range, NUL and surrogates; binascii.Error, UnicodeError and JSONDecodeError
        # are subclasses), OverflowError (datetime beyond year 1..9999 after the UTC shift) and
        # RecursionError (deeply nested JSON; defensive, json handles the 4096-char maximum on
        # CPython today). TypeError/NotImplementedError would be
        # programming errors, which Sort now rejects at construction, so they stay 500s.
        raise InvalidCursorError from exc


def _after(sort: Sort, values: Sequence[Any]) -> ColumnElement[bool]:
    """Predicate selecting rows strictly after ``values`` in ``sort`` order."""
    keys = sort.keys
    bound = [literal(v, type_=k.column.type) for k, v in zip(keys, values, strict=True)]
    if len({k.descending for k in keys}) == 1:
        # Row-value comparison: one index range scan on a matching composite index.
        cols = tuple_(*(k.column for k in keys))
        rhs = tuple_(*bound)
        return cols < rhs if keys[0].descending else cols > rhs
    # Mixed directions cannot use a row comparison: expand to OR-of-ANDs.
    branches: list[ColumnElement[bool]] = []
    for i, key in enumerate(keys):
        equal = [keys[j].column == bound[j] for j in range(i)]
        cmp = key.column < bound[i] if key.descending else key.column > bound[i]
        branches.append(and_(*equal, cmp))
    return or_(*branches)


async def paginate[E](
    session: AsyncSession,
    stmt: Select[tuple[E]],
    sort: Sort,
    params: PageParams,
) -> tuple[list[E], str | None]:
    """Run ``stmt`` ordered by ``sort`` from ``params.cursor``; return ``(items, next_cursor)``.

    ``stmt`` carries the endpoint's filters and eager-loading options. It selects one entity.
    Fetches ``limit + 1`` rows to learn whether another page exists. Raises
    :class:`InvalidCursorError` for a malformed cursor or one made for a different sort.
    """
    if params.cursor is not None:
        stmt = stmt.where(_after(sort, decode_cursor(sort, params.cursor)))
    # order_by(None) drops any ordering already on stmt so it cannot precede the sort's keys;
    # unique() lets a joinedload of a collection be used (it raises otherwise).
    ordered = stmt.order_by(None).order_by(*sort.order_by()).limit(params.limit + 1)
    rows = cast(list[E], list((await session.scalars(ordered)).unique()))
    if len(rows) <= params.limit:
        return rows, None
    items = rows[: params.limit]
    last = items[-1]
    return items, encode_cursor(sort, [getattr(last, k.attr) for k in sort.keys])


async def paginate_page[E, T](
    session: AsyncSession,
    stmt: Select[tuple[E]],
    sort: Sort,
    params: PageParams,
    to_schema: Callable[[E], T],
) -> Page[T]:
    """:func:`paginate`, then map each entity with ``to_schema`` into a :class:`Page`."""
    items, next_cursor = await paginate(session, stmt, sort, params)
    return Page[T](items=[to_schema(i) for i in items], next_cursor=next_cursor)
