"""Queries on the ``system`` domain's own tables (and the connectivity probe)."""

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.system.models import JobRun


async def ping_database(session: AsyncSession) -> None:
    """Run ``SELECT 1``; raises if the database cannot answer."""
    await session.execute(text("SELECT 1"))


async def add_job_run(
    session: AsyncSession,
    *,
    job_name: str,
    job_id: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    error_summary: str,
) -> JobRun:
    """Insert one ``job_runs`` row (the caller commits)."""
    run = JobRun(
        job_name=job_name,
        job_id=job_id,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        error_summary=error_summary,
    )
    session.add(run)
    await session.flush()
    return run


async def last_success_by_job(
    session: AsyncSession, job_names: Collection[str]
) -> dict[str, datetime]:
    """Return the latest ``finished_at`` of a successful run for each of ``job_names``.

    Jobs with no successful run are absent from the result.
    """
    if not job_names:
        return {}
    rows = await session.execute(
        select(JobRun.job_name, func.max(JobRun.finished_at))
        .where(JobRun.status == "success", JobRun.job_name.in_(list(job_names)))
        .group_by(JobRun.job_name)
    )
    return dict(rows.all())


async def delete_job_runs_finished_before(session: AsyncSession, cutoff: datetime) -> int:
    """Delete the rows whose ``finished_at`` is before ``cutoff``; return how many."""
    result = await session.execute(delete(JobRun).where(JobRun.finished_at < cutoff))
    return int(result.rowcount)  # type: ignore[attr-defined]
