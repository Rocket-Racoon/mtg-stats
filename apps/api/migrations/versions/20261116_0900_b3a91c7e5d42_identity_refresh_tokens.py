"""identity_refresh_tokens

Revision ID: b3a91c7e5d42
Revises: 80d7f2564c67
Create Date: 2026-11-16 09:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Autogenerate renders custom column types such as UtcDateTime as app.core.db.UtcDateTime().
import app.core.db

revision: str = "b3a91c7e5d42"
down_revision: str | None = "80d7f2564c67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", app.core.db.UtcDateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", app.core.db.UtcDateTime(timezone=True), nullable=True),
        sa.Column("replaced_by", sa.UUID(), nullable=True),
        sa.Column("created_at", app.core.db.UtcDateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["replaced_by"],
            ["refresh_tokens.id"],
            name=op.f("fk_refresh_tokens_replaced_by_refresh_tokens"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_refresh_tokens_family_id"), "refresh_tokens", ["family_id"], unique=False
    )
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_family_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
