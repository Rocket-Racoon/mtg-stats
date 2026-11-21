"""cards_and_printings

Revision ID: 40e9501d90da
Revises: 4a01b3f4681e
Create Date: 2026-11-21 21:05:11.544599+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Autogenerate renders custom column types such as UtcDateTime as app.core.db.UtcDateTime().
import app.core.db

revision: str = "40e9501d90da"
down_revision: str | None = "4a01b3f4681e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The wrapper is IMMUTABLE so the generated search_name column and its index can use it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(
        "CREATE OR REPLACE FUNCTION public.immutable_unaccent(text) RETURNS text "
        "LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT "
        "AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$"
    )
    op.create_table(
        "cards",
        sa.Column("oracle_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), server_default="", nullable=False),
        sa.Column(
            "search_name",
            sa.Text(),
            sa.Computed(
                "btrim(regexp_replace(regexp_replace(lower(public.immutable_unaccent(name)), "
                "'[''\u2019]', '', 'g'), '[^[:alnum:]]+', ' ', 'g'))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("mana_cost", sa.String(length=128), server_default="", nullable=False),
        sa.Column("cmc", sa.Numeric(precision=10, scale=2), server_default="0", nullable=False),
        sa.Column("type_line", sa.String(length=255), server_default="", nullable=False),
        sa.Column("oracle_text", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "colors",
            postgresql.ARRAY(sa.String(length=1)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "color_identity",
            postgresql.ARRAY(sa.String(length=1)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "keywords",
            postgresql.ARRAY(sa.String(length=64)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("layout", sa.String(length=32), server_default="normal", nullable=False),
        sa.Column(
            "card_faces",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "legalities",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "commander_legality",
            sa.String(length=16),
            sa.Computed("coalesce(legalities ->> 'commander', '')", persisted=True),
            nullable=False,
        ),
        sa.Column("game_changer", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("oversized", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "is_extra",
            sa.Boolean(),
            sa.Computed("oversized OR layout = 'art_series'", persisted=True),
            nullable=False,
        ),
        sa.Column("default_printing_id", sa.UUID(), nullable=True),
        sa.Column("reserved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("missing_since", app.core.db.UtcDateTime(timezone=True), nullable=True),
        sa.Column("updated_at", app.core.db.UtcDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("oracle_id", name=op.f("pk_cards")),
    )
    op.create_index("ix_cards_commander_legality", "cards", ["commander_legality"], unique=False)
    op.create_index(
        "ix_cards_search_name_trgm",
        "cards",
        ["search_name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"search_name": "gin_trgm_ops"},
    )
    op.create_table(
        "printings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("oracle_id", sa.UUID(), nullable=False),
        sa.Column("set_code", sa.String(length=16), server_default="", nullable=False),
        sa.Column("set_name", sa.String(length=255), server_default="", nullable=False),
        sa.Column("collector_number", sa.String(length=32), server_default="", nullable=False),
        sa.Column("rarity", sa.String(length=16), server_default="", nullable=False),
        sa.Column("lang", sa.String(length=8), server_default="en", nullable=False),
        sa.Column("released_at", sa.Date(), nullable=True),
        sa.Column(
            "finishes",
            postgresql.ARRAY(sa.String(length=16)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "image_uris",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("digital", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("oversized", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_at", app.core.db.UtcDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["oracle_id"], ["cards.oracle_id"], name=op.f("fk_printings_oracle_id_cards")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_printings")),
    )
    op.create_index("ix_printings_oracle_id", "printings", ["oracle_id"], unique=False)
    op.create_index(
        "ux_printings_set_code_collector_number_lang",
        "printings",
        ["set_code", "collector_number", "lang"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_printings_set_code_collector_number_lang", table_name="printings")
    op.drop_index("ix_printings_oracle_id", table_name="printings")
    op.drop_table("printings")
    op.drop_index(
        "ix_cards_search_name_trgm",
        table_name="cards",
        postgresql_using="gin",
        postgresql_ops={"search_name": "gin_trgm_ops"},
    )
    op.drop_index("ix_cards_commander_legality", table_name="cards")
    op.drop_table("cards")
    # The extensions stay: other objects may use them, and CREATE ... IF NOT EXISTS is idempotent.
    op.execute("DROP FUNCTION IF EXISTS public.immutable_unaccent(text)")
