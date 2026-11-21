"""Queries on the ``cards`` domain's own tables."""

from collections.abc import Collection
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cards.models import Card, Printing


def fold_name(value: Any) -> ColumnElement[str]:
    """SQL expression folding ``value`` like ``cards.search_name`` (see ``SEARCH_NAME_SQL``)."""
    lowered = func.lower(func.immutable_unaccent(value))
    no_apostrophes = func.regexp_replace(lowered, "['\u2019]", "", "g")
    return func.btrim(func.regexp_replace(no_apostrophes, "[^[:alnum:]]+", " ", "g"))


async def get_card(session: AsyncSession, oracle_id: UUID) -> Card | None:
    """Return the card with this oracle id, or None."""
    return await session.get(Card, oracle_id)


async def get_printing(session: AsyncSession, printing_id: UUID) -> Printing | None:
    """Return the printing with this Scryfall id, or None."""
    return await session.get(Printing, printing_id)


def printings_of(oracle_id: UUID) -> Select[Printing]:
    """A select of one card's printings, newest first (ties broken by id for stable order)."""
    return (
        select(Printing)
        .where(Printing.oracle_id == oracle_id)
        .order_by(Printing.released_at.desc().nulls_last(), Printing.id)
    )


async def cards_by_names(session: AsyncSession, names: Collection[str]) -> list[Card]:
    """Cards whose folded name equals the folded form of any of ``names``."""
    if not names:
        return []
    folded = [fold_name(name) for name in names]
    stmt = select(Card).where(Card.search_name.in_(folded)).order_by(Card.name)
    return list((await session.execute(stmt)).scalars())
