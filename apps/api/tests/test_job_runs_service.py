"""S-1.9.4: recording, truncating, purging and reporting ``job_runs`` (service level).

``record_job_run`` opens its own session through ``get_sessionmaker``; the ``recorded`` fixture
points it at the rolled-back test session, so nothing is really committed.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import AppSession
from app.system import services
from app.system.models import JobRun
from app.workers.registry import JOBS
from app.workers.schedule import SCHEDULES, schedule_interval
from tests.factories import create
from tests.job_run_support import JobRunFactory

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


@pytest.fixture
async def recorded(
    real_job_run_recording: None, app_schema: AppSession, monkeypatch: pytest.MonkeyPatch
) -> AppSession:
    """The real ``record_job_run`` writing into the rolled-back test session."""

    @asynccontextmanager
    async def _session() -> AsyncIterator[AppSession]:
        yield app_schema

    monkeypatch.setattr("app.system.services.job_runs.get_sessionmaker", lambda: _session)
    return app_schema


async def _rows(session: AppSession) -> list[JobRun]:
    result = await session.execute(select(JobRun).order_by(JobRun.finished_at))
    return list(result.scalars())


async def _record(error: str = "", status: str = "failed") -> None:
    await services.record_job_run(
        job_name="system.ping",
        job_id="j-1",
        started_at=NOW - timedelta(seconds=2),
        finished_at=NOW,
        status=status,
        error=error,
    )


# --- recording ---------------------------------------------------------------------------------


async def test_a_recorded_run_stores_name_id_times_status_and_error(recorded: AppSession) -> None:
    await services.record_job_run(
        job_name="system.ping",
        job_id="job-abc",
        started_at=NOW - timedelta(seconds=3),
        finished_at=NOW,
        status="failed",
        error="ValueError: boom",
    )

    (row,) = await _rows(recorded)
    assert row.job_name == "system.ping"
    assert row.job_id == "job-abc"
    assert row.started_at == NOW - timedelta(seconds=3)
    assert row.finished_at == NOW
    assert row.started_at.utcoffset() == timedelta(0)
    assert row.finished_at.utcoffset() == timedelta(0)
    assert row.status == "failed"
    assert row.error_summary == "ValueError: boom"


@pytest.mark.parametrize("status", ["success", "retry", "failed", "timeout", "cancelled"])
async def test_every_wrapper_outcome_is_a_storable_status(
    recorded: AppSession, status: str
) -> None:
    await _record(status=status)
    (row,) = await _rows(recorded)
    assert row.status == status


async def test_a_success_run_has_an_empty_error_summary(recorded: AppSession) -> None:
    await _record(status="success")
    (row,) = await _rows(recorded)
    assert row.error_summary == ""


async def test_an_error_of_exactly_2048_bytes_is_kept_whole(recorded: AppSession) -> None:
    await _record("x" * 2048)
    (row,) = await _rows(recorded)
    assert row.error_summary == "x" * 2048


async def test_an_ascii_error_over_2048_bytes_is_cut_to_2048_bytes(recorded: AppSession) -> None:
    await _record("x" * 5000)
    (row,) = await _rows(recorded)
    assert len(row.error_summary.encode("utf-8")) == 2048


async def test_a_two_byte_character_error_is_cut_by_bytes_not_characters(
    recorded: AppSession,
) -> None:
    await _record("é" * 3000)  # 6000 bytes
    (row,) = await _rows(recorded)
    assert len(row.error_summary.encode("utf-8")) == 2048
    assert row.error_summary == "é" * 1024


async def test_a_cut_never_splits_a_three_byte_character(recorded: AppSession) -> None:
    await _record("a" + "€" * 1000)  # 1 + 3 * 1000 bytes; byte 2048 falls inside a character
    (row,) = await _rows(recorded)
    assert row.error_summary == "a" + "€" * 682  # 2047 bytes, a valid string
    assert len(row.error_summary.encode("utf-8")) == 2047


async def test_a_cut_never_splits_a_four_byte_character(recorded: AppSession) -> None:
    await _record("😀" * 600)
    (row,) = await _rows(recorded)
    assert row.error_summary == "😀" * 512
    assert len(row.error_summary.encode("utf-8")) == 2048


async def test_a_cut_in_the_middle_of_a_four_byte_character_drops_it_whole(
    recorded: AppSession,
) -> None:
    await _record("ab" + "😀" * 600)  # 2 + 4k: byte 2048 falls inside the 512th emoji
    (row,) = await _rows(recorded)
    assert row.error_summary == "ab" + "😀" * 511
    assert len(row.error_summary.encode("utf-8")) == 2046


# --- the database CHECK constraints ------------------------------------------------------------


async def test_the_database_accepts_an_error_summary_of_2048_bytes(app_schema: AppSession) -> None:
    await create(app_schema, JobRunFactory, error_summary="é" * 1024)  # 1024 chars, 2048 bytes


async def test_the_database_rejects_an_error_summary_over_2048_bytes(
    app_schema: AppSession,
) -> None:
    with pytest.raises(IntegrityError) as caught:
        async with app_schema.begin_nested():
            await create(app_schema, JobRunFactory, error_summary="é" * 1025)  # 1025 chars, 2050 B
    assert "error_summary_size" in str(caught.value)


async def test_the_database_rejects_an_unknown_status(app_schema: AppSession) -> None:
    with pytest.raises(IntegrityError) as caught:
        async with app_schema.begin_nested():
            await create(app_schema, JobRunFactory, status="exploded")
    assert "status_valid" in str(caught.value)


# --- purge -------------------------------------------------------------------------------------


async def test_purge_deletes_runs_that_ended_31_days_ago_and_keeps_those_from_29_days_ago(
    app_schema: AppSession,
) -> None:
    old: JobRun = await create(app_schema, JobRunFactory, finished_at=NOW - timedelta(days=31))
    recent: JobRun = await create(app_schema, JobRunFactory, finished_at=NOW - timedelta(days=29))

    deleted = await services.purge_job_runs(app_schema, now=NOW)

    assert deleted == 1
    remaining = {row.id for row in await _rows(app_schema)}
    assert remaining == {recent.id}
    assert old.id not in remaining


async def test_purge_removes_old_runs_of_every_status(app_schema: AppSession) -> None:
    for status in ("success", "failed", "timeout", "cancelled", "retry"):
        await create(app_schema, JobRunFactory, status=status, finished_at=NOW - timedelta(days=45))

    assert await services.purge_job_runs(app_schema, now=NOW) == 5
    assert await _rows(app_schema) == []


async def test_purge_is_idempotent(app_schema: AppSession) -> None:
    await create(app_schema, JobRunFactory, finished_at=NOW - timedelta(days=40))
    await create(app_schema, JobRunFactory, finished_at=NOW - timedelta(days=1))

    assert await services.purge_job_runs(app_schema, now=NOW) == 1
    assert await services.purge_job_runs(app_schema, now=NOW) == 0
    assert len(await _rows(app_schema)) == 1


async def test_purge_without_a_clock_argument_uses_the_current_time(
    app_schema: AppSession,
) -> None:
    await create(app_schema, JobRunFactory, finished_at=datetime.now(UTC) - timedelta(days=31))
    keep: JobRun = await create(
        app_schema, JobRunFactory, finished_at=datetime.now(UTC) - timedelta(days=29)
    )

    assert await services.purge_job_runs(app_schema) == 1
    assert [row.id for row in await _rows(app_schema)] == [keep.id]


def test_the_purge_job_is_registered_and_runs_once_a_day() -> None:
    assert "system.purge_job_runs" in JOBS
    assert "system.purge_job_runs" in SCHEDULES
    assert schedule_interval(SCHEDULES["system.purge_job_runs"]) == timedelta(days=1)


async def test_the_purge_job_deletes_old_rows_and_returns_how_many(recorded: AppSession) -> None:
    now = datetime.now(UTC)
    await create(recorded, JobRunFactory, finished_at=now - timedelta(days=31))
    await create(recorded, JobRunFactory, finished_at=now - timedelta(days=35))
    keep: JobRun = await create(recorded, JobRunFactory, finished_at=now - timedelta(days=29))

    assert await services.purge_job_runs_job({}) == 2
    assert [row.id for row in await _rows(recorded)] == [keep.id]


# --- jobs_health -------------------------------------------------------------------------------

INTERVAL = timedelta(hours=1)
INTERVALS = {"a.job": INTERVAL}


async def _health(session: AppSession) -> services.JobHealth:
    (item,) = await services.jobs_health(session, INTERVALS, now=NOW)
    return item


async def test_a_job_that_never_ran_is_stale_with_no_last_success(app_schema: AppSession) -> None:
    item = await _health(app_schema)
    assert item.name == "a.job"
    assert item.interval_seconds == 3600
    assert item.last_success_at is None
    assert item.stale is True


async def test_a_job_with_only_failed_runs_has_no_last_success_and_is_stale(
    app_schema: AppSession,
) -> None:
    for status in ("failed", "timeout", "cancelled", "retry"):
        await create(
            app_schema,
            JobRunFactory,
            job_name="a.job",
            status=status,
            finished_at=NOW - timedelta(minutes=1),
        )

    item = await _health(app_schema)
    assert item.last_success_at is None
    assert item.stale is True


async def test_a_job_whose_last_success_is_recent_is_not_stale(app_schema: AppSession) -> None:
    finished = NOW - timedelta(minutes=5)
    await create(app_schema, JobRunFactory, job_name="a.job", finished_at=finished)

    item = await _health(app_schema)
    assert item.last_success_at == finished
    assert item.stale is False


async def test_a_success_just_inside_twice_the_interval_is_not_stale(
    app_schema: AppSession,
) -> None:
    await create(
        app_schema,
        JobRunFactory,
        job_name="a.job",
        finished_at=NOW - 2 * INTERVAL + timedelta(seconds=1),
    )
    assert (await _health(app_schema)).stale is False


async def test_a_success_just_outside_twice_the_interval_is_stale(app_schema: AppSession) -> None:
    finished = NOW - 2 * INTERVAL - timedelta(seconds=1)
    await create(app_schema, JobRunFactory, job_name="a.job", finished_at=finished)

    item = await _health(app_schema)
    assert item.last_success_at == finished
    assert item.stale is True


async def test_a_failure_after_the_last_success_does_not_replace_it_nor_make_it_stale(
    app_schema: AppSession,
) -> None:
    success = NOW - timedelta(minutes=30)
    await create(app_schema, JobRunFactory, job_name="a.job", finished_at=success)
    await create(
        app_schema,
        JobRunFactory,
        job_name="a.job",
        status="failed",
        finished_at=NOW - timedelta(minutes=1),
    )

    item = await _health(app_schema)
    assert item.last_success_at == success
    assert item.stale is False


async def test_the_most_recent_success_is_reported(app_schema: AppSession) -> None:
    newest = NOW - timedelta(minutes=10)
    for finished in (NOW - timedelta(days=3), newest, NOW - timedelta(hours=2)):
        await create(app_schema, JobRunFactory, job_name="a.job", finished_at=finished)

    assert (await _health(app_schema)).last_success_at == newest


async def test_each_job_is_judged_on_its_own_runs_and_interval(app_schema: AppSession) -> None:
    await create(
        app_schema, JobRunFactory, job_name="fast.job", finished_at=NOW - timedelta(minutes=11)
    )
    await create(
        app_schema, JobRunFactory, job_name="slow.job", finished_at=NOW - timedelta(hours=30)
    )
    report = await services.jobs_health(
        app_schema,
        {"fast.job": timedelta(minutes=5), "slow.job": timedelta(days=1), "idle.job": INTERVAL},
        now=NOW,
    )

    assert {item.name: item.stale for item in report} == {
        "fast.job": True,  # 11 min > 2 x 5 min
        "slow.job": False,  # 30 h < 2 x 24 h
        "idle.job": True,  # never ran
    }
