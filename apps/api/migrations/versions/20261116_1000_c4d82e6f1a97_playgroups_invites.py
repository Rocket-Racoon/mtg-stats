"""playgroups_invites

Revision ID: c4d82e6f1a97
Revises: b3a91c7e5d42
Create Date: 2026-11-16 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Autogenerate renders custom column types such as UtcDateTime as app.core.db.UtcDateTime().
import app.core.db

revision: str = "c4d82e6f1a97"
down_revision: str | None = "b3a91c7e5d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("expires_at", app.core.db.UtcDateTime(timezone=True), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("revoked_at", app.core.db.UtcDateTime(timezone=True), nullable=True),
        sa.Column("created_at", app.core.db.UtcDateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint("max_uses BETWEEN 1 AND 20", name=op.f("ck_invites_max_uses_range")),
        sa.CheckConstraint(
            "use_count >= 0 AND use_count <= max_uses", name=op.f("ck_invites_use_count_valid")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_invites_created_by_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invites")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invites_token_hash")),
    )
    op.create_index(
        "ix_invites_created_by_created_at_id",
        "invites",
        ["created_by", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_invites_created_by_created_at_id", table_name="invites")
    op.drop_table("invites")
