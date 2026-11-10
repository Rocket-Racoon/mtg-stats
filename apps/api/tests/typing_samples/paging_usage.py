"""How a domain uses ``app.core.paging``; ``tests/test_paging_typing.py`` runs mypy --strict on it.

This file is only analysed, never imported by the test suite. It has its own metadata, so its
model never lands on the real schema.
"""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import MetaData, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import NAMING_CONVENTION, Base, IdMixin, SessionDep, TimestampMixin
from app.core.paging import (
    Page,
    PageParamsDep,
    Sort,
    SortKey,
    created_at_asc,
    created_at_desc,
    paginate,
    paginate_page,
)


class SampleBase(Base):
    """Separate metadata."""

    __abstract__ = True
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Deck(IdMixin, TimestampMixin, SampleBase):
    """A model shaped like a real domain model."""

    __tablename__ = "sample_decks"
    name: Mapped[str] = mapped_column(String(100), default="")


class DeckOut(BaseModel):
    """Response item."""

    id: UUID
    name: str


class DeckPage(Page[DeckOut]):
    """Named page model."""


router = APIRouter()


@router.get("/decks")
async def list_decks(session: SessionDep, params: PageParamsDep) -> DeckPage:
    """List endpoint using the named page class."""
    stmt = select(Deck).where(Deck.name != "")
    return await paginate_page(
        session,
        stmt,
        created_at_desc(Deck),
        params,
        page_cls=DeckPage,
        to_schema=lambda d: DeckOut(id=d.id, name=d.name),
    )


@router.get("/decks-oldest")
async def list_oldest(session: SessionDep, params: PageParamsDep) -> DeckPage:
    """Same with the ascending order and a custom sort."""
    sort: Sort = Sort((SortKey(Deck.name), SortKey(Deck.id, True)))
    assert sort.ident
    items, cursor = await paginate(session, select(Deck), created_at_asc(Deck), params)
    return DeckPage(items=[DeckOut(id=d.id, name=d.name) for d in items], next_cursor=cursor)
