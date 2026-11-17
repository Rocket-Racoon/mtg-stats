"""Recording, purging and reporting job runs (S-1.9.4, architecture §7)."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.system import repository
from app.system.models import ERROR_SUMMARY_MAX_BYTES, JOB_RUN_STATUSES

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
"""``job_runs`` rows whose run ended more than this many days ago are purged daily."""
STALE_FACTOR = 2
"""A job is stale when its last success is older than ``STALE_FACTOR`` times its interval."""


def truncate_error_summary(text: str, max_bytes: int = ERROR_SUMMARY_MAX_BYTES) -> str:
    """Cut ``text`` to at most ``max_bytes`` UTF-8 bytes without splitting a character."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


async def record_job_run(
    *,
    job_name: str,
    job_id: str | None,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    error: str = "",
) -> None:
    """Write one ``job_runs`` row for a finished attempt, in its own session and transaction.

    ``error`` is truncated to 2048 UTF-8 bytes. Raises on database errors: the job wrapper
    catches and logs them so that recording never fails a job.
    """
    if status not in JOB_RUN_STATUSES:
        raise ValueError(f"unknown job run status {status!r}")
    async with get_sessionmaker()() as session:
        await repository.add_job_run(
            session,
            job_name=job_name,
            job_id=(job_id or "")[:255],
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            error_summary=truncate_error_summary(error),
        )
        await session.commit()


async def purge_job_runs(session: AsyncSession, now: datetime | None = None) -> int:
    """Delete runs that ended more than :data:`RETENTION_DAYS` days ago; return how many.

    Does not commit. Idempotent.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=RETENTION_DAYS)
    return await repository.delete_job_runs_finished_before(session, cutoff)


async def purge_job_runs_job(ctx: dict[str, Any]) -> int:
    """Daily job ``system.purge_job_runs``: purge old ``job_runs`` rows; return how many.

    Opens its own session and commits. Idempotent.
    """
    async with get_sessionmaker()() as session:
        deleted = await purge_job_runs(session)
        await session.commit()
    logger.info("purged %d old job run(s)", deleted)
    return deleted


@dataclass(frozen=True)
class JobHealth:
    """Health of one scheduled job.

    ``last_success_at`` is ``None`` when no successful run is on record (never ran, or only
    older than the 30-day retention). Such a job is reported ``stale``: with no proof that it
    works, the uptime monitor should look at it. A freshly deployed daily job is therefore stale
    until its first run.
    """

    name: str
    interval_seconds: int
    last_success_at: datetime | None
    stale: bool


async def jobs_health(
    session: AsyncSession, intervals: Mapping[str, timedelta], now: datetime | None = None
) -> list[JobHealth]:
    """Report every scheduled job (``intervals``: name -> longest gap between its runs).

    A job is stale when it has no successful run, or its last one ended more than
    :data:`STALE_FACTOR` times its interval ago. Sorted by name.
    """
    current = now or datetime.now(UTC)
    last = await repository.last_success_by_job(session, list(intervals))
    report = []
    for name in sorted(intervals):
        interval = intervals[name]
        finished = last.get(name)
        stale = finished is None or current - finished > STALE_FACTOR * interval
        report.append(JobHealth(name, int(interval.total_seconds()), finished, stale))
    return report
