"""Cursor paging (S-1.4.7): ``Page[T]``, ``PageParams``, the cursor codec and the keyset helper.

A test-only router over test-only models (own metadata, created inside the rolled-back test
transaction) exercises the helper through the real app's error envelope.
"""

import base64
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import httpx
import pytest
from conftest import StatementCounter
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel
from sqlalchemy import ForeignKey, MetaData, String, select
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from app.core.db import NAMING_CONVENTION, AppSession, Base, IdMixin, SessionDep, TimestampMixin
from app.core.errors import InvalidCursorError
from app.core.paging import (
    Page,
    PageParamsDep,
    Sort,
    SortKey,
    created_at_asc,
    created_at_desc,
    decode_cursor,
    encode_cursor,
    paginate_page,
)
from tests.factories import BaseFactory, create


class PagingBase(Base):
    """Separate metadata so these tables never leak into the real schema."""

    __abstract__ = True
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Gadget(IdMixin, TimestampMixin, PagingBase):
    """Test parent row."""

    __tablename__ = "paging_gadgets"
    name: Mapped[str] = mapped_column(String(50), default="")
    parts: Mapped[list["Sprocket"]] = relationship(back_populates="gadget")


class Sprocket(IdMixin, TimestampMixin, PagingBase):
    """Test child row, serialized with its gadget to exercise eager loading."""

    __tablename__ = "paging_sprockets"
    gadget_id: Mapped[UUID] = mapped_column(ForeignKey("paging_gadgets.id"))
    label: Mapped[str] = mapped_column(String(50), default="")
    gadget: Mapped[Gadget] = relationship(back_populates="parts")


class GadgetFactory(BaseFactory):
    """Factory for :class:`Gadget`."""

    class Meta:
        model = Gadget

    name = "g"


class SprocketOut(BaseModel):
    """Child in the response."""

    label: str


class GadgetOut(BaseModel):
    """Item of the test list endpoint."""

    id: UUID
    name: str
    created_at: datetime
    parts: list[SprocketOut]


SORTS: dict[str, Callable[[], Sort]] = {
    "new": lambda: created_at_desc(Gadget),
    "old": lambda: created_at_asc(Gadget),
    # mixed directions: exercises the expanded OR predicate
    "mixed": lambda: Sort((SortKey(Gadget.name), SortKey(Gadget.id, True))),
}

router = APIRouter(prefix="/api/v1/test-gadgets")


@router.get("")
async def list_gadgets(
    session: SessionDep, params: PageParamsDep, sort: str = "new", eager: bool = True
) -> Page[GadgetOut]:
    """Test-only list endpoint; ``eager=false`` is the N+1 negative control."""
    stmt = select(Gadget)
    if eager:
        stmt = stmt.options(selectinload(Gadget.parts))

    def to_schema(gadget: Gadget) -> GadgetOut:
        return GadgetOut(
            id=gadget.id,
            name=gadget.name,
            created_at=gadget.created_at,
            parts=[SprocketOut(label=p.label) for p in parts_of[gadget.id]]
            if not eager
            else [SprocketOut(label=p.label) for p in gadget.parts],
        )

    parts_of: dict[UUID, list[Sprocket]] = {}
    page = await paginate_page(session, stmt, SORTS[sort](), params, lambda g: g)
    if not eager:
        for gadget in page.items:  # deliberate N+1: one query per item
            rows = await session.scalars(select(Sprocket).where(Sprocket.gadget_id == gadget.id))
            parts_of[gadget.id] = list(rows)
    return Page[GadgetOut](items=[to_schema(g) for g in page.items], next_cursor=page.next_cursor)


BASE = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def paged(api_app: FastAPI, db_session: AppSession) -> AppSession:
    """Create the test tables in the test transaction and mount the test router."""
    connection = await db_session.connection()
    await connection.run_sync(PagingBase.metadata.create_all)
    api_app.include_router(router)
    return db_session


async def _gadgets(
    session: AppSession, n: int, *, step: float = 1.0, name: str = "g"
) -> list[Gadget]:
    """``n`` gadgets ``step`` seconds apart; ``step=0`` gives them all the same created_at."""
    return [
        await create(
            session, GadgetFactory, name=name, created_at=BASE + timedelta(seconds=step * i)
        )
        for i in range(n)
    ]


async def _get(client: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get("/api/v1/test-gadgets", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _walk(client: httpx.AsyncClient, **params: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(1000):
        page = await _get(client, **params, **({"cursor": cursor} if cursor else {}))
        items += page["items"]
        cursor = page["next_cursor"]
        if cursor is None:
            return items
    raise AssertionError("paging did not terminate")


# AC1


async def test_default_page_has_at_most_50_items_in_page_shape(
    paged: AppSession, client: httpx.AsyncClient
) -> None:
    await _gadgets(paged, 60)
    body = await _get(client)
    assert set(body) == {"items", "next_cursor"}
    assert len(body["items"]) == 50
    assert isinstance(body["next_cursor"], str)


@pytest.mark.parametrize("limit", ["201", "0", "-1", "abc"])
async def test_limit_outside_1_to_200_is_rejected_with_422(
    paged: AppSession, client: httpx.AsyncClient, limit: str
) -> None:
    response = await client.get("/api/v1/test-gadgets", params={"limit": limit})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["fields"][0]["loc"] == ["query", "limit"]


async def test_limit_200_is_accepted(paged: AppSession, client: httpx.AsyncClient) -> None:
    await _gadgets(paged, 3)
    assert len((await _get(client, limit=200))["items"]) == 3


# AC2


@pytest.mark.parametrize("sort", ["new", "old", "mixed"])
@pytest.mark.parametrize("step", [1.0, 0])
async def test_walking_all_pages_returns_every_row_once_in_sort_order(
    paged: AppSession, client: httpx.AsyncClient, sort: str, step: float
) -> None:
    # step=0 -> many rows share one created_at, so the id tie-breaker decides everything.
    rows = await _gadgets(paged, 17, step=step, name="same")
    items = await _walk(client, sort=sort, limit=4)
    expected = {
        "new": sorted(rows, key=lambda g: (g.created_at, g.id), reverse=True),
        "old": sorted(rows, key=lambda g: (g.created_at, g.id)),
        "mixed": sorted(rows, key=lambda g: g.id, reverse=True),
    }[sort]
    assert [UUID(i["id"]) for i in items] == [g.id for g in expected]


async def test_mixed_sort_orders_by_name_then_id_descending(
    paged: AppSession, client: httpx.AsyncClient
) -> None:
    rows = [await create(paged, GadgetFactory, name=n, created_at=BASE) for n in "bacbbacab"]
    items = await _walk(client, sort="mixed", limit=2)
    expected = sorted(rows, key=lambda g: g.id, reverse=True)
    expected.sort(key=lambda g: g.name)  # stable: name asc, id desc within a name
    assert [UUID(i["id"]) for i in items] == [g.id for g in expected]


@pytest.mark.parametrize("sort", ["new", "old"])
async def test_rows_inserted_between_pages_are_never_repeated_or_skipped(
    paged: AppSession, client: httpx.AsyncClient, sort: str
) -> None:
    originals = await _gadgets(paged, 12)
    # Give the page-boundary timestamp a tie so the id tie-breaker matters.
    originals += [
        await create(paged, GadgetFactory, created_at=BASE + timedelta(seconds=5, microseconds=7))
        for _ in range(3)
    ]
    first = await _get(client, sort=sort, limit=6)
    boundary = first["items"][-1]
    boundary_at = datetime.fromisoformat(boundary["created_at"])
    boundary_id = UUID(boundary["id"])

    def after_boundary(g: Gadget) -> bool:
        key, edge = (g.created_at, g.id), (boundary_at, boundary_id)
        return key < edge if sort == "new" else key > edge

    inserted = [
        await create(paged, GadgetFactory, created_at=BASE - timedelta(days=1)),  # oldest
        await create(paged, GadgetFactory, created_at=BASE + timedelta(days=1)),  # newest
        await create(
            paged, GadgetFactory, created_at=boundary_at, id=UUID(int=boundary_id.int + 1)
        ),
        await create(
            paged, GadgetFactory, created_at=boundary_at, id=UUID(int=boundary_id.int - 1)
        ),
        await create(paged, GadgetFactory, created_at=boundary_at - timedelta(microseconds=1)),
        await create(paged, GadgetFactory, created_at=boundary_at + timedelta(microseconds=1)),
    ]
    rest: list[dict[str, Any]] = []
    cursor = first["next_cursor"]
    while cursor:
        page = await _get(client, sort=sort, limit=6, cursor=cursor)
        rest += page["items"]
        cursor = page["next_cursor"]

    seen = [UUID(i["id"]) for i in first["items"] + rest]
    assert len(seen) == len(set(seen)), "an item was repeated"
    assert {g.id for g in originals} <= set(seen), "an original item was skipped"
    expected_new = {g.id for g in inserted if after_boundary(g)}
    assert expected_new <= {UUID(i["id"]) for i in rest}
    assert not ({g.id for g in inserted if not after_boundary(g)} & {UUID(i["id"]) for i in rest})


# AC3


async def test_last_page_has_null_next_cursor(paged: AppSession, client: httpx.AsyncClient) -> None:
    await _gadgets(paged, 10)
    first = await _get(client, limit=5)
    assert first["next_cursor"] is not None
    last = await _get(client, limit=5, cursor=first["next_cursor"])
    assert len(last["items"]) == 5
    assert last["next_cursor"] is None


async def test_empty_result_has_null_next_cursor(
    paged: AppSession, client: httpx.AsyncClient
) -> None:
    assert await _get(client) == {"items": [], "next_cursor": None}


def _b64(raw: bytes | str) -> str:
    data = raw.encode() if isinstance(raw, str) else raw
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _payload(*values: Any) -> str:
    return _b64(json.dumps(list(values)))


GOOD_ID = "0194c2ef-7f3b-7a31-8d2d-8c1a0b3f9e10"
GOOD_AT = "2026-01-01T00:00:00.000000+00:00"


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "!!!",
        "é",
        "abc",  # valid base64 alphabet but not JSON
        _b64("not json"),
        _b64("{}"),
        _b64('"a string"'),
        _payload(),
        _payload("created_at:d,id:d"),  # no keys
        _payload("created_at:d,id:d", GOOD_AT),  # too few keys
        _payload("created_at:d,id:d", GOOD_AT, GOOD_ID, GOOD_ID),  # too many keys
        _payload("created_at:d,id:d", 5, GOOD_ID),  # wrong type
        _payload("created_at:d,id:d", GOOD_AT, 5),  # wrong type
        _payload("created_at:d,id:d", "2026-01-01T00:00:00", GOOD_ID),  # naive datetime
        _payload("created_at:d,id:d", "yesterday", GOOD_ID),
        _payload("created_at:d,id:d", GOOD_AT, "not-a-uuid"),
        _payload("created_at:a,id:a", GOOD_AT, GOOD_ID),  # made for a different sort
        _payload(None, GOOD_AT, GOOD_ID),
        _payload("created_at:d,id:d", GOOD_AT, GOOD_ID) + "=",  # non-canonical padding
    ],
)
async def test_invalid_cursor_is_422_with_invalid_cursor_code(
    paged: AppSession, client: httpx.AsyncClient, cursor: str
) -> None:
    response = await client.get("/api/v1/test-gadgets", params={"cursor": cursor})
    assert response.status_code == 422, cursor
    error = response.json()["error"]
    assert error["code"] == "invalid_cursor"
    assert error["request_id"]


async def test_cursor_from_one_sort_is_rejected_by_another(
    paged: AppSession, client: httpx.AsyncClient
) -> None:
    await _gadgets(paged, 4)
    cursor = (await _get(client, sort="new", limit=2))["next_cursor"]
    for other in ("old", "mixed"):
        response = await client.get(
            "/api/v1/test-gadgets", params={"sort": other, "cursor": cursor}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_cursor"


# Codec unit tests


def test_cursor_round_trips_microsecond_datetimes_and_uuids_exactly() -> None:
    sort = created_at_desc(Gadget)
    at = datetime(2026, 3, 4, 5, 6, 7, 123456, tzinfo=UTC)
    uid = UUID(GOOD_ID)
    cursor = encode_cursor(sort, [at, uid])
    assert "=" not in cursor
    assert decode_cursor(sort, cursor) == [at, uid]


def test_cursor_normalises_non_utc_datetimes_to_utc() -> None:
    sort = created_at_asc(Gadget)
    offset = datetime(2026, 1, 1, 12, 0, 0, 5, tzinfo=timezone(timedelta(hours=2)))
    decoded = decode_cursor(sort, encode_cursor(sort, [offset, UUID(GOOD_ID)]))
    assert decoded[0] == offset
    assert decoded[0].utcoffset() == timedelta(0)


def test_sort_must_end_with_id_and_have_two_keys() -> None:
    with pytest.raises(ValueError, match="id"):
        Sort((SortKey(Gadget.created_at),))
    with pytest.raises(ValueError, match="id"):
        Sort((SortKey(Gadget.id), SortKey(Gadget.created_at)))


def test_decode_raises_invalid_cursor_error() -> None:
    with pytest.raises(InvalidCursorError) as info:
        decode_cursor(created_at_desc(Gadget), "!!!")
    assert info.value.code == "invalid_cursor"
    assert info.value.status_code == 422


# AC4


def _parts(db: AppSession, gadgets: list[Gadget]) -> Any:
    db.add_all(Sprocket(gadget_id=g.id, label=f"p{i}") for g in gadgets for i in range(2))
    return db.flush()


async def _count(
    client: httpx.AsyncClient,
    counter_factory: Callable[[], AbstractContextManager[StatementCounter]],
    db: AppSession,
    **params: Any,
) -> int:
    db.expire_all()  # force real loads, not identity-map hits
    with counter_factory() as counter:
        body = await _get(client, **params)
    assert all(len(item["parts"]) == 2 for item in body["items"])
    return counter.count


async def test_list_statement_count_is_the_same_for_5_and_50_items(
    paged: AppSession,
    client: httpx.AsyncClient,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    five = await _gadgets(paged, 5)
    await _parts(paged, five)
    with_five = await _count(client, count_statements, paged)
    more = await _gadgets(paged, 45)
    await _parts(paged, more)
    with_fifty = await _count(client, count_statements, paged)
    assert with_five == with_fifty


async def test_statement_counter_detects_n_plus_one_without_eager_loading(
    paged: AppSession,
    client: httpx.AsyncClient,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    five = await _gadgets(paged, 5)
    await _parts(paged, five)
    with_five = await _count(client, count_statements, paged, eager="false")
    more = await _gadgets(paged, 45)
    await _parts(paged, more)
    with_fifty = await _count(client, count_statements, paged, eager="false")
    assert with_fifty == with_five + 45
