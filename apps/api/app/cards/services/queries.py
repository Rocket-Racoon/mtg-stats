"""Typed read functions of the ``cards`` domain.

They return frozen dataclasses, never ORM rows, so other domains depend on no model.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.cards import repository
from app.cards.models import Card, Printing


@dataclass(frozen=True, slots=True)
class CardInfo:
    """A card as other domains see it."""

    oracle_id: UUID
    name: str
    mana_cost: str
    cmc: Decimal
    type_line: str
    oracle_text: str
    colors: tuple[str, ...]
    color_identity: tuple[str, ...]
    keywords: tuple[str, ...]
    layout: str
    card_faces: tuple[dict[str, Any], ...]
    legalities: dict[str, str]
    commander_legality: str
    game_changer: bool
    oversized: bool
    is_extra: bool
    default_printing_id: UUID | None
    reserved: bool
    missing_since: datetime | None


@dataclass(frozen=True, slots=True)
class PrintingInfo:
    """A printing as other domains see it."""

    id: UUID
    oracle_id: UUID
    set_code: str
    set_name: str
    collector_number: str
    rarity: str
    lang: str
    released_at: date | None
    finishes: tuple[str, ...]
    image_uris: dict[str, Any]
    digital: bool
    oversized: bool


def _card_info(card: Card) -> CardInfo:
    return CardInfo(
        oracle_id=card.oracle_id,
        name=card.name,
        mana_cost=card.mana_cost,
        cmc=card.cmc,
        type_line=card.type_line,
        oracle_text=card.oracle_text,
        colors=tuple(card.colors),
        color_identity=tuple(card.color_identity),
        keywords=tuple(card.keywords),
        layout=card.layout,
        card_faces=tuple(card.card_faces),
        legalities=dict(card.legalities),
        commander_legality=card.commander_legality,
        game_changer=card.game_changer,
        oversized=card.oversized,
        is_extra=card.is_extra,
        default_printing_id=card.default_printing_id,
        reserved=card.reserved,
        missing_since=card.missing_since,
    )


def _printing_info(printing: Printing) -> PrintingInfo:
    return PrintingInfo(
        id=printing.id,
        oracle_id=printing.oracle_id,
        set_code=printing.set_code,
        set_name=printing.set_name,
        collector_number=printing.collector_number,
        rarity=printing.rarity,
        lang=printing.lang,
        released_at=printing.released_at,
        finishes=tuple(printing.finishes),
        image_uris=dict(printing.image_uris),
        digital=printing.digital,
        oversized=printing.oversized,
    )


async def get_card(session: AsyncSession, oracle_id: UUID) -> CardInfo | None:
    """Return the card with this oracle id, or None."""
    card = await repository.get_card(session, oracle_id)
    return _card_info(card) if card else None


async def get_printing(session: AsyncSession, printing_id: UUID) -> PrintingInfo | None:
    """Return the printing with this Scryfall id, or None."""
    printing = await repository.get_printing(session, printing_id)
    return _printing_info(printing) if printing else None


async def list_printings(session: AsyncSession, oracle_id: UUID) -> list[PrintingInfo]:
    """Return every printing of a card, newest release first."""
    rows = await session.scalars(repository.printings_of(oracle_id))
    return [_printing_info(printing) for printing in rows]


async def find_cards_by_names(session: AsyncSession, names: Collection[str]) -> list[CardInfo]:
    """Return the cards matching any of ``names``, ignoring case, accents and punctuation."""
    return [_card_info(card) for card in await repository.cards_by_names(session, names)]
