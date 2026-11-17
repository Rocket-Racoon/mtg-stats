"""ORM models of the ``system`` domain: ``JobRun`` (S-1.9.4)."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, UtcDateTime

ERROR_SUMMARY_MAX_BYTES = 2048
"""Upper bound of ``job_runs.error_summary`` in UTF-8 bytes (S-1.9.4)."""

JOB_RUN_STATUSES: tuple[str, ...] = ("success", "retry", "failed", "timeout", "cancelled")
"""How an attempt ended: the wrapper's outcomes (``app.workers.registry``)."""

_STATUS_LIST = ", ".join(f"'{s}'" for s in JOB_RUN_STATUSES)


class JobRun(IdMixin, Base):
    """One finished attempt of a background job.

    A job retried by arq produces one row per attempt, all with the same ``job_id``.
    """

    __tablename__ = "job_runs"
    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="status_valid"),
        CheckConstraint(
            f"octet_length(error_summary) <= {ERROR_SUMMARY_MAX_BYTES}", name="error_summary_size"
        ),
        Index("ix_job_runs_job_name_status_finished_at", "job_name", "status", "finished_at"),
        Index("ix_job_runs_finished_at", "finished_at"),
    )

    job_name: Mapped[str] = mapped_column(String(128))
    job_id: Mapped[str] = mapped_column(String(255), default="", server_default="")
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime] = mapped_column(UtcDateTime)
    status: Mapped[str] = mapped_column(String(16))
    error_summary: Mapped[str] = mapped_column(Text, default="", server_default="")
