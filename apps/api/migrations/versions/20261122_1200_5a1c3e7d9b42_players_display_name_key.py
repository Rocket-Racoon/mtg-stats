"""players display name key

Stores ``display_name_key(display_name)`` on ``players`` (S-2.2.4) so the picker's search and
order run in SQL with the same answers as the guest-name uniqueness check, and backfills it.

The migration holds an exclusive lock on ``players`` for the whole backfill (fine at this size).
After the backfill the column keeps no database default, so a raw insert that omits the key fails
instead of storing ``''``.

Revision ID: 5a1c3e7d9b42
Revises: 0c0eb7757b9f
Create Date: 2026-11-22 12:00:00.000000+00:00
"""

import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5a1c3e7d9b42"
down_revision: str | None = "0c0eb7757b9f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCH = 1000


def _key(value: str) -> str:
    """A frozen copy of ``app.identity.names.display_name_key`` as of this revision."""
    decomposed = unicodedata.normalize("NFKD", value)
    kept = "".join(ch for ch in decomposed if unicodedata.category(ch) not in ("Mn", "Cf"))
    return " ".join(kept.casefold().split())


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("display_name_key", sa.Text(), server_default="", nullable=False),
    )
    players = sa.table(
        "players",
        sa.column("id", sa.Uuid()),
        sa.column("display_name", sa.String()),
        sa.column("display_name_key", sa.Text()),
    )
    connection = op.get_bind()
    last = None
    while True:
        query = sa.select(players.c.id, players.c.display_name).order_by(players.c.id)
        if last is not None:
            query = query.where(players.c.id > last)
        rows = connection.execute(query.limit(BATCH)).all()
        if not rows:
            break
        connection.execute(
            sa.text(
                "UPDATE players SET display_name_key = v.key "
                "FROM (SELECT unnest(CAST(:ids AS uuid[])) AS id, "
                "unnest(CAST(:keys AS text[])) AS key) AS v WHERE players.id = v.id"
            ),
            {"ids": [row.id for row in rows], "keys": [_key(row.display_name) for row in rows]},
        )
        last = rows[-1].id
    op.alter_column("players", "display_name_key", server_default=None)


def downgrade() -> None:
    # The key is derived data (recomputable from ``display_name``), so dropping it loses nothing.
    op.drop_column("players", "display_name_key")
