"""S-1.9.4 acceptance: every job run is recorded, and ``GET /api/v1/health/jobs`` reports health.

Part 1 drives the real job wrapper (``wrap_job``) and reads back ``job_runs``. Part 2 drives the
endpoint through the whole app: authorization, the report and the stale rule.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from arq.worker import Retry
from sqlalchemy import select

from app.core.db import AppSession
from app.core.jobs import NonRetryableJobError, RetryableJobError
from app.system.models import JobRun
from app.system.services.job_runs import RETENTION_DAYS, STALE_FACTOR
from app.workers import registry
from app.workers.registry import JobSpec, wrap_job
from app.workers.schedule import SCHEDULES, Schedule, schedule_interval
from tests.factories import create
from tests.invite_support import make_account
from tests.job_run_support import JobRunFactory
from tests.statement_counter import StatementCounter

URL = "/api/v1/health/jobs"


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
    result = await session.execute(select(JobRun).order_by(JobRun.finished_at, JobRun.id))
    return list(result.scalars())


async def _ok(ctx: dict[str, Any]) -> str:
    return "done"


async def _boom(ctx: dict[str, Any], message: str = "boom") -> None:
    raise ValueError(message)


async def _fatal(ctx: dict[str, Any]) -> None:
    raise NonRetryableJobError("no")


async def _transient(ctx: dict[str, Any]) -> None:
    raise RetryableJobError("try again")


async def _sleepy(ctx: dict[str, Any]) -> None:
    await asyncio.sleep(30)


# --- every job run is recorded ----------------------------------------------------------------


async def test_a_successful_job_records_a_success_row_with_name_id_and_utc_times(
    recorded: AppSession,
) -> None:
    before = datetime.now(UTC)
    result = await wrap_job("tests.ok", JobSpec(_ok))({"job_id": "tests.ok:1", "job_try": 1})
    after = datetime.now(UTC)

    assert result == "done"
    (row,) = await _rows(recorded)
    assert row.job_name == "tests.ok"
    assert row.job_id == "tests.ok:1"
    assert row.status == "success"
    assert row.error_summary == ""
    assert row.started_at.utcoffset() == timedelta(0)
    assert row.finished_at.utcoffset() == timedelta(0)
    assert before <= row.started_at <= row.finished_at <= after


async def test_a_failing_job_records_a_failed_row_with_the_error_and_still_raises_it(
    recorded: AppSession,
) -> None:
    with pytest.raises(ValueError, match="kaboom"):
        await wrap_job("tests.boom", JobSpec(_boom))(
            {"job_id": "tests.boom:1", "job_try": 1}, "kaboom"
        )

    (row,) = await _rows(recorded)
    assert row.status == "failed"
    assert row.job_name == "tests.boom"
    assert "ValueError" in row.error_summary
    assert "kaboom" in row.error_summary


async def test_a_non_retryable_failure_records_a_failed_row(recorded: AppSession) -> None:
    with pytest.raises(NonRetryableJobError):
        await wrap_job("tests.fatal", JobSpec(_fatal))({"job_id": "f-1", "job_try": 1})

    (row,) = await _rows(recorded)
    assert row.status == "failed"
    assert "NonRetryableJobError" in row.error_summary


async def test_a_huge_error_message_is_stored_in_at_most_2048_bytes(recorded: AppSession) -> None:
    with pytest.raises(ValueError, match="é"):
        await wrap_job("tests.boom", JobSpec(_boom))({"job_id": "b-1", "job_try": 1}, "é" * 5000)

    (row,) = await _rows(recorded)
    assert 0 < len(row.error_summary.encode("utf-8")) <= 2048


async def test_a_timed_out_job_records_a_timeout_row(recorded: AppSession) -> None:
    with pytest.raises(TimeoutError):
        await wrap_job("tests.sleepy", JobSpec(_sleepy, timeout=0.1))(
            {"job_id": "s-1", "job_try": 1}
        )

    (row,) = await _rows(recorded)
    assert row.status == "timeout"
    assert row.job_id == "s-1"
    assert row.error_summary != ""
    assert (row.finished_at - row.started_at) >= timedelta(milliseconds=90)


async def test_a_job_cancelled_by_shutdown_records_a_cancelled_row_and_stays_cancelled(
    recorded: AppSession,
) -> None:
    task = asyncio.ensure_future(
        wrap_job("tests.sleepy", JobSpec(_sleepy))({"job_id": "c-1", "job_try": 1})
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    (row,) = await _rows(recorded)
    assert row.status == "cancelled"
    assert row.job_id == "c-1"
    assert row.error_summary != ""


async def test_each_retry_attempt_records_its_own_row_sharing_the_job_id(
    recorded: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry, "RETRY_DELAYS_SECONDS", (0, 0, 0))
    wrapped = wrap_job("tests.transient", JobSpec(_transient, max_retries=2))

    for attempt in (1, 2):
        with pytest.raises(Retry):
            await wrapped({"job_id": "shared-id", "job_try": attempt})
    with pytest.raises(RetryableJobError):  # attempt 3: out of retries, so it fails
        await wrapped({"job_id": "shared-id", "job_try": 3})

    rows = await _rows(recorded)
    assert [row.status for row in rows] == ["retry", "retry", "failed"]
    assert {row.job_id for row in rows} == {"shared-id"}
    assert {row.job_name for row in rows} == {"tests.transient"}
    assert all("try again" in row.error_summary for row in rows)


async def test_a_job_that_fails_then_succeeds_on_retry_leaves_a_retry_row_and_a_success_row(
    recorded: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry, "RETRY_DELAYS_SECONDS", (0, 0, 0))
    calls = {"n": 0}

    async def flaky(ctx: dict[str, Any]) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryableJobError("once")
        return "ok"

    wrapped = wrap_job("tests.flaky", JobSpec(flaky))
    with pytest.raises(Retry):
        await wrapped({"job_id": "same", "job_try": 1})
    assert await wrapped({"job_id": "same", "job_try": 2}) == "ok"

    rows = await _rows(recorded)
    assert sorted(row.status for row in rows) == ["retry", "success"]
    assert {row.job_id for row in rows} == {"same"}


# --- recording can never hurt the job ---------------------------------------------------------


async def test_a_recording_failure_does_not_fail_a_successful_job(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(**_kwargs: object) -> None:
        raise RuntimeError("job_runs table is gone")

    monkeypatch.setattr("app.system.services.record_job_run", broken)

    with caplog.at_level(logging.INFO):
        result = await wrap_job("tests.ok", JobSpec(_ok))({"job_id": "r-1", "job_try": 1})

    assert result == "done"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a recording failure is logged"


async def test_a_recording_failure_does_not_mask_the_jobs_own_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(**_kwargs: object) -> None:
        raise RuntimeError("job_runs table is gone")

    monkeypatch.setattr("app.system.services.record_job_run", broken)

    with pytest.raises(ValueError, match="original"):
        await wrap_job("tests.boom", JobSpec(_boom))({"job_id": "r-2", "job_try": 1}, "original")


async def test_a_recording_failure_does_not_stop_a_retry_from_being_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(**_kwargs: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr("app.system.services.record_job_run", broken)

    with pytest.raises(Retry):
        await wrap_job("tests.transient", JobSpec(_transient))({"job_id": "r-3", "job_try": 1})


async def test_a_recording_failure_does_not_replace_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(**_kwargs: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr("app.system.services.record_job_run", broken)

    with pytest.raises(TimeoutError):
        await wrap_job("tests.sleepy", JobSpec(_sleepy, timeout=0.05))({"job_id": "r-4"})


async def test_a_hanging_recording_does_not_hang_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang(**_kwargs: object) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr("app.system.services.record_job_run", hang)
    monkeypatch.setattr(registry, "RECORD_TIMEOUT_SECONDS", 0.1)

    result = await asyncio.wait_for(
        wrap_job("tests.ok", JobSpec(_ok))({"job_id": "r-5", "job_try": 1}), timeout=5
    )

    assert result == "done"


# --- GET /api/v1/health/jobs: authorization ---------------------------------------------------


async def test_anonymous_callers_get_401_unauthenticated(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    response = await client.get(URL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_a_signed_in_non_admin_gets_403_forbidden(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    """A playgroup admin has no site-wide role, so it is an ordinary user here."""
    _, headers = await make_account(app_schema, admin=False)
    response = await client.get(URL, headers=headers)
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "forbidden"
    assert set(body["error"]) >= {"code", "message", "details", "request_id"}


async def test_an_inactive_site_admin_gets_403_forbidden(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True, active=False)
    response = await client.get(URL, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_the_jobs_report_is_not_leaked_to_non_admins_even_when_runs_exist(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await create(app_schema, JobRunFactory)
    _, headers = await make_account(app_schema, admin=False)
    response = await client.get(URL, headers=headers)
    assert response.status_code == 403
    assert "jobs" not in response.json()


# --- GET /api/v1/health/jobs: the report ------------------------------------------------------


async def test_a_site_admin_gets_every_scheduled_job_with_its_interval(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)

    response = await client.get(URL, headers=headers)

    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert [job["name"] for job in jobs] == sorted(SCHEDULES)
    for job in jobs:
        assert set(job) == {"name", "interval_seconds", "last_success_at", "stale"}
        assert job["interval_seconds"] == int(
            schedule_interval(SCHEDULES[job["name"]]).total_seconds()
        )
    by_name = {job["name"]: job for job in jobs}
    assert by_name["system.heartbeat"]["interval_seconds"] == 300
    assert by_name["system.purge_job_runs"]["interval_seconds"] == 86400


async def test_jobs_that_never_ran_are_stale_with_a_null_last_success(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)

    jobs = (await client.get(URL, headers=headers)).json()["jobs"]

    assert jobs
    assert all(job["last_success_at"] is None and job["stale"] is True for job in jobs)


async def test_a_recent_success_is_reported_with_its_time_and_is_not_stale(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    finished = datetime.now(UTC) - timedelta(minutes=1)
    await create(app_schema, JobRunFactory, job_name="system.heartbeat", finished_at=finished)
    _, headers = await make_account(app_schema, admin=True)

    jobs = (await client.get(URL, headers=headers)).json()["jobs"]

    heartbeat = next(job for job in jobs if job["name"] == "system.heartbeat")
    assert heartbeat["stale"] is False
    reported = datetime.fromisoformat(heartbeat["last_success_at"])
    assert reported.utcoffset() == timedelta(0)
    assert reported == finished
    others = [job for job in jobs if job["name"] != "system.heartbeat"]
    assert all(job["stale"] is True for job in others)


async def test_the_last_success_is_the_newest_success_not_a_later_failure(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    now = datetime.now(UTC)
    success = now - timedelta(minutes=3)
    await create(app_schema, JobRunFactory, job_name="system.heartbeat", finished_at=success)
    await create(
        app_schema,
        JobRunFactory,
        job_name="system.heartbeat",
        status="failed",
        finished_at=now - timedelta(minutes=1),
    )
    _, headers = await make_account(app_schema, admin=True)

    jobs = (await client.get(URL, headers=headers)).json()["jobs"]

    heartbeat = next(job for job in jobs if job["name"] == "system.heartbeat")
    assert datetime.fromisoformat(heartbeat["last_success_at"]) == success
    assert heartbeat["stale"] is False


async def test_a_job_with_only_failed_runs_is_stale_and_has_no_last_success(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    for status in ("failed", "timeout", "cancelled", "retry"):
        await create(
            app_schema,
            JobRunFactory,
            job_name="system.heartbeat",
            status=status,
            finished_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    _, headers = await make_account(app_schema, admin=True)

    jobs = (await client.get(URL, headers=headers)).json()["jobs"]

    heartbeat = next(job for job in jobs if job["name"] == "system.heartbeat")
    assert heartbeat["last_success_at"] is None
    assert heartbeat["stale"] is True


@pytest.mark.parametrize(("minutes", "stale"), [(9, False), (11, True)])
async def test_a_5_minute_job_is_stale_only_when_its_success_is_older_than_10_minutes(
    client: httpx.AsyncClient, app_schema: AppSession, minutes: int, stale: bool
) -> None:
    await create(
        app_schema,
        JobRunFactory,
        job_name="system.heartbeat",
        finished_at=datetime.now(UTC) - timedelta(minutes=minutes),
    )
    _, headers = await make_account(app_schema, admin=True)

    jobs = (await client.get(URL, headers=headers)).json()["jobs"]

    assert next(j for j in jobs if j["name"] == "system.heartbeat")["stale"] is stale


async def test_a_daily_job_is_fresh_after_47_hours_and_stale_after_49_hours(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    now = datetime.now(UTC)
    name = "system.purge_job_runs"
    run: JobRun = await create(
        app_schema, JobRunFactory, job_name=name, finished_at=now - timedelta(hours=47)
    )
    _, headers = await make_account(app_schema, admin=True)

    jobs = (await client.get(URL, headers=headers)).json()["jobs"]
    assert next(j for j in jobs if j["name"] == name)["stale"] is False

    run.finished_at = now - timedelta(hours=49)
    await app_schema.flush()
    jobs = (await client.get(URL, headers=headers)).json()["jobs"]
    assert next(j for j in jobs if j["name"] == name)["stale"] is True


async def test_the_report_runs_the_same_number_of_statements_whatever_the_history_size(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    await app_schema.commit()  # both measurements start from the same session state
    with count_statements() as empty:
        assert (await client.get(URL, headers=headers)).status_code == 200

    now = datetime.now(UTC)
    for name in SCHEDULES:
        for minutes in range(1, 11):
            await create(
                app_schema,
                JobRunFactory,
                job_name=name,
                finished_at=now - timedelta(minutes=minutes),
            )
    await app_schema.commit()
    with count_statements() as full:
        assert (await client.get(URL, headers=headers)).status_code == 200

    assert full.count == empty.count


def test_the_record_budget_fits_inside_arqs_backstop_margin() -> None:
    assert registry.RECORD_TIMEOUT_SECONDS < registry.ARQ_TIMEOUT_MARGIN_SECONDS


async def test_a_job_finishing_at_the_end_of_its_budget_still_succeeds_under_arqs_backstop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def finishes_late(_ctx: dict[str, Any]) -> str:
        await asyncio.sleep(0.8)
        return "done"

    async def hang(**_kwargs: object) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr("app.system.services.record_job_run", hang)
    monkeypatch.setattr(registry, "RECORD_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(registry, "ARQ_TIMEOUT_MARGIN_SECONDS", 0.5)
    spec = JobSpec(finishes_late, timeout=1.0)

    result = await asyncio.wait_for(
        wrap_job("tests.late", spec)({"job_id": "r-6", "job_try": 1}),
        timeout=registry.arq_options(spec)["timeout"],
    )

    assert result == "done"


def test_a_schedule_whose_longest_gap_cannot_be_measured_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be measured"):
        schedule_interval(Schedule(weekday={0, 1, 2, 3, 4}, hour=set(range(9, 18))))


def test_every_schedule_keeps_its_last_success_for_the_whole_stale_window() -> None:
    for name, schedule in SCHEDULES.items():
        assert STALE_FACTOR * schedule_interval(schedule) <= timedelta(days=RETENTION_DAYS), name
