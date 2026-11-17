"""system_job_runs

Revision ID: 4a01b3f4681e
Revises: c4d82e6f1a97
Create Date: 2026-11-16 19:04:30.250705+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Autogenerate renders custom column types such as UtcDateTime as app.core.db.UtcDateTime().
import app.core.db

revision: str = "4a01b3f4681e"
down_revision: str | None = "c4d82e6f1a97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_runs",
        sa.Column("job_name", sa.String(length=128), nullable=False),
        sa.Column("job_id", sa.String(length=255), server_default="", nullable=False),
        sa.Column("started_at", app.core.db.UtcDateTime(timezone=True), nullable=False),
        sa.Column("finished_at", app.core.db.UtcDateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_summary", sa.Text(), server_default="", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('success', 'retry', 'failed', 'timeout', 'cancelled')",
            name=op.f("ck_job_runs_status_valid"),
        ),
        sa.CheckConstraint(
            "octet_length(error_summary) <= 2048", name=op.f("ck_job_runs_error_summary_size")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_runs")),
    )
    op.create_index("ix_job_runs_finished_at", "job_runs", ["finished_at"], unique=False)
    op.create_index(
        "ix_job_runs_job_name_status_finished_at",
        "job_runs",
        ["job_name", "status", "finished_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_runs_job_name_status_finished_at", table_name="job_runs")
    op.drop_index("ix_job_runs_finished_at", table_name="job_runs")
    op.drop_table("job_runs")
