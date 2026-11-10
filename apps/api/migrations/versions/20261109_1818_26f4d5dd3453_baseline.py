"""baseline

Revision ID: 26f4d5dd3453
Revises:
Create Date: 2026-11-09 18:18:51.657116
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.db

revision: str = "26f4d5dd3453"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
