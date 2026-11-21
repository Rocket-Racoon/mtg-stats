"""ORM models of the ``cards`` domain: ``Card`` (by Scryfall ``oracle_id``) and ``Printing``.

Primary keys are Scryfall's UUIDs, not UUID v7 (S-3.1.1): the sync upserts by those ids.
Legality values are stored exactly as Scryfall sends them (``legal``, ``not_legal``, ``banned``,
``restricted``). ``search_name``, ``commander_legality`` and ``is_extra`` are Postgres
generated columns, so every write path (ORM or bulk upsert) gets them right without extra code.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Computed,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UtcDateTime, _utcnow

EXTRA_LAYOUTS = ("art_series",)

# Run before the tables exist, by the migration and by the test schema fixture. ``unaccent`` is
# only STABLE, so index and generated-column expressions need this IMMUTABLE wrapper.
EXTENSIONS_SQL = (
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS unaccent",
)
IMMUTABLE_UNACCENT_SQL = """
CREATE OR REPLACE FUNCTION immutable_unaccent(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$
"""
DROP_IMMUTABLE_UNACCENT_SQL = "DROP FUNCTION IF EXISTS immutable_unaccent(text)"

# lower-case, accents removed, apostrophes dropped, other punctuation folded to single spaces.
SEARCH_NAME_SQL = (
    "btrim(regexp_replace(regexp_replace(lower(immutable_unaccent(name)), "
    "'[''\u2019]', '', 'g'), '[^[:alnum:]]+', ' ', 'g'))"
)
COMMANDER_LEGALITY_SQL = "coalesce(legalities ->> 'commander', '')"
IS_EXTRA_SQL = "oversized OR layout = 'art_series'"


def create_search_support(connection: Any) -> None:
    """Create the extensions and the ``immutable_unaccent`` wrapper on a sync connection."""
    for statement in (*EXTENSIONS_SQL, IMMUTABLE_UNACCENT_SQL):
        connection.execute(text(statement))


class Card(Base):
    """A card by oracle identity: the rules text shared by all of its printings."""

    __tablename__ = "cards"
    __table_args__ = (
        Index("ix_cards_commander_legality", "commander_legality"),
        Index(
            "ix_cards_search_name_trgm",
            "search_name",
            postgresql_using="gin",
            postgresql_ops={"search_name": "gin_trgm_ops"},
        ),
    )

    oracle_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="", server_default="")
    search_name: Mapped[str] = mapped_column(Text, Computed(SEARCH_NAME_SQL, persisted=True))
    mana_cost: Mapped[str] = mapped_column(String(128), default="", server_default="")
    cmc: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal(0), server_default="0")
    type_line: Mapped[str] = mapped_column(String(255), default="", server_default="")
    oracle_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    colors: Mapped[list[str]] = mapped_column(
        ARRAY(String(1)), default=list, server_default=text("'{}'")
    )
    color_identity: Mapped[list[str]] = mapped_column(
        ARRAY(String(1)), default=list, server_default=text("'{}'")
    )
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), default=list, server_default=text("'{}'")
    )
    layout: Mapped[str] = mapped_column(String(32), default="normal", server_default="normal")
    card_faces: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    legalities: Mapped[dict[str, str]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    commander_legality: Mapped[str] = mapped_column(
        String(16), Computed(COMMANDER_LEGALITY_SQL, persisted=True)
    )
    game_changer: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    oversized: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_extra: Mapped[bool] = mapped_column(Boolean, Computed(IS_EXTRA_SQL, persisted=True))
    # The printing shown by default; no FK because cards and printings reference each other.
    default_printing_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
    reserved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    missing_since: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow, onupdate=_utcnow)


class Printing(Base):
    """One printing of a card, keyed by Scryfall's printing ``id``."""

    __tablename__ = "printings"
    __table_args__ = (
        Index(
            "ux_printings_set_code_collector_number_lang",
            "set_code",
            "collector_number",
            "lang",
            unique=True,
        ),
        Index("ix_printings_oracle_id", "oracle_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    oracle_id: Mapped[UUID] = mapped_column(ForeignKey("cards.oracle_id"))
    set_code: Mapped[str] = mapped_column(String(16), default="", server_default="")
    set_name: Mapped[str] = mapped_column(String(255), default="", server_default="")
    collector_number: Mapped[str] = mapped_column(String(32), default="", server_default="")
    rarity: Mapped[str] = mapped_column(String(16), default="", server_default="")
    lang: Mapped[str] = mapped_column(String(8), default="en", server_default="en")
    released_at: Mapped[date | None] = mapped_column(Date, default=None)
    finishes: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), default=list, server_default=text("'{}'")
    )
    image_uris: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    digital: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    oversized: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow, onupdate=_utcnow)
