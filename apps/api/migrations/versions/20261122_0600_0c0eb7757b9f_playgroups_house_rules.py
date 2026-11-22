"""playgroups house rules

Revision ID: 0c0eb7757b9f
Revises: 92321b9b300c
Create Date: 2026-11-22 06:00:15.436874+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Autogenerate renders custom column types such as UtcDateTime as app.core.db.UtcDateTime().
import app.core.db

revision: str = "0c0eb7757b9f"
down_revision: str | None = "92321b9b300c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "playgroups",
        sa.Column("house_rules_text", sa.String(length=5000), server_default="", nullable=False),
    )
    op.add_column("playgroups", sa.Column("house_rules_edited_by", sa.UUID(), nullable=True))
    op.add_column(
        "playgroups",
        sa.Column("house_rules_edited_at", app.core.db.UtcDateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_playgroups_house_rules_length"),
        "playgroups",
        "char_length(house_rules_text) <= 5000",
    )
    op.create_foreign_key(
        op.f("fk_playgroups_house_rules_edited_by_players"),
        "playgroups",
        "players",
        ["house_rules_edited_by"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_playgroups_house_rules_edited_by_players"), "playgroups", type_="foreignkey"
    )
    op.drop_constraint(op.f("ck_playgroups_house_rules_length"), "playgroups", type_="check")
    op.drop_column("playgroups", "house_rules_edited_at")
    op.drop_column("playgroups", "house_rules_edited_by")
    op.drop_column("playgroups", "house_rules_text")
