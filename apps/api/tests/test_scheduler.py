"""Tests for the schedule registry and scheduler wiring (S-1.9.3).

The exactly-once tests use two in-process workers on a private queue of the test Redis; the
occurrence is in the past, so arq enqueues it without a deferral and no real waiting is needed.
"""

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from arq.connections import ArqRedis
from arq.utils import to_unix_ms
from arq.worker import Worker, create_worker

from app.core import jobs as core_jobs
from app.core.config import Settings, get_settings
from app.workers.registry import JOBS, build_functions
from app.workers.schedule import (
    SCHEDULES,
    Schedule,
    build_cron_jobs,
    log_schedule,
    scheduler_disabled_reason,
)
from app.workers.settings import WorkerSettings
from tests.jobs import JobQueue, run_job_in_process

HEARTBEAT = "system.heartbeat"


def _settings(env: str = "dev", enabled: bool = True) -> Settings:
    update: dict[str, Any] = {"app_env": env, "scheduler_enabled": enabled}
    return get_settings().model_copy(update=update)


def test_every_schedule_names_a_registered_job() -> None:
    assert set(SCHEDULES) <= set(JOBS)


def test_heartbeat_is_scheduled_every_five_minutes_on_the_utc_clock() -> None:
    assert SCHEDULES[HEARTBEAT].minute == set(range(0, 60, 5))
    assert SCHEDULES[HEARTBEAT].second == 0


def test_worker_uses_the_utc_clock_for_cron() -> None:
    assert WorkerSettings.timezone is UTC


# --- AC1: startup log --------------------------------------------------------------------------


def test_startup_log_lists_each_job_with_schedule_and_next_run_in_utc(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 3, 1, 12, 3, 30, tzinfo=UTC)
    with caplog.at_level(logging.INFO, logger="app.workers.schedule"):
        log_schedule(_settings(), now)
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == len(SCHEDULES)
    assert any(
        m.startswith("scheduled job system.heartbeat schedule=[minute=0,5,10,")
        and m.endswith("next_run=2026-03-01T12:05:00.123+00:00")
        for m in messages
    ), messages


def test_next_run_in_log_matches_arq_cron_job() -> None:
    now = datetime(2026, 3, 1, 23, 58, 0, tzinfo=UTC)
    (job,) = build_cron_jobs(_settings())
    job.calculate_next(now)
    assert job.next_run == SCHEDULES[HEARTBEAT].next_run(now)
    assert job.next_run == datetime(2026, 3, 2, 0, 0, 0, 123456, tzinfo=UTC)


def test_describe_shows_the_fields_that_are_set() -> None:
    assert Schedule(hour=3, minute=0).describe() == "hour=3 minute=0"
    assert Schedule().describe() == "every minute"
    assert Schedule(minute=0, second=30).describe() == "minute=0 second=30"


def test_startup_log_reads_the_cron_jobs_the_worker_built(
    caplog: pytest.LogCaptureFixture,
) -> None:
    jobs = build_cron_jobs(_settings())
    now = datetime(2026, 3, 1, 12, 3, 30, tzinfo=UTC)
    with caplog.at_level(logging.INFO, logger="app.workers.schedule"):
        log_schedule(_settings(), now, jobs)
    (line,) = [r.getMessage() for r in caplog.records]
    assert "scheduled job system.heartbeat" in line
    assert line.endswith("next_run=2026-03-01T12:05:00.123+00:00")
    assert jobs[0].next_run is None  # logging does not touch arq's own state


# --- AC3: enable / disable ---------------------------------------------------------------------


def test_scheduler_is_off_in_the_test_environment() -> None:
    assert scheduler_disabled_reason(_settings("test")) == "APP_ENV=test"
    assert build_cron_jobs(_settings("test")) == []


def test_scheduler_is_off_when_scheduler_enabled_is_false() -> None:
    assert scheduler_disabled_reason(_settings("dev", enabled=False)) == "SCHEDULER_ENABLED=false"
    assert build_cron_jobs(_settings("prod", enabled=False)) == []


def test_scheduler_is_on_in_dev_and_prod_by_default() -> None:
    assert scheduler_disabled_reason(_settings("dev")) is None
    assert [j.name for j in build_cron_jobs(_settings("prod"))] == list(SCHEDULES)


def test_disabled_scheduler_logs_once_with_the_reason(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.workers.schedule"):
        log_schedule(_settings("dev", enabled=False))
    (record,) = caplog.records
    assert "scheduler disabled (SCHEDULER_ENABLED=false)" in record.getMessage()


def test_worker_settings_have_no_cron_jobs_under_app_env_test() -> None:
    assert get_settings().app_env == "test"
    assert WorkerSettings.cron_jobs == []


def test_scheduler_enabled_defaults_to_true_and_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().scheduler_enabled is True
        monkeypatch.setenv("SCHEDULER_ENABLED", "false")
        get_settings.cache_clear()
        assert get_settings().scheduler_enabled is False
    finally:
        get_settings.cache_clear()


# --- AC4: heartbeat every 5 minutes ------------------------------------------------------------


@pytest.mark.parametrize("offset_seconds", [0, 1, 61, 150, 299, 300, 301, 1234])
def test_heartbeat_runs_twice_in_any_ten_minute_window(offset_seconds: int) -> None:
    start = datetime(2026, 5, 6, 7, 8, 9, tzinfo=UTC) + timedelta(seconds=offset_seconds)
    end = start + timedelta(minutes=10)
    runs = 0
    at = SCHEDULES[HEARTBEAT].next_run(start)
    while at <= end:
        runs += 1
        at = SCHEDULES[HEARTBEAT].next_run(at)
    assert runs == 2


async def test_heartbeat_logs_one_line_and_returns_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        assert await run_job_in_process(HEARTBEAT) is None
    assert [r.getMessage() for r in caplog.records if r.name == "app.system.services.jobs"] == [
        "heartbeat"
    ]


# --- AC2: exactly once across workers ----------------------------------------------------------


def _worker(queue: JobQueue, pool: ArqRedis) -> Worker:
    return create_worker(
        WorkerSettings,
        functions=build_functions(),
        cron_jobs=build_cron_jobs(_settings()),
        queue_name=queue.name,
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
        timezone=UTC,
        redis_pool=pool,
    )


async def _scheduler_tick(worker: Worker, now: datetime) -> None:
    """Two ticks, as arq does: the first computes ``next_run``, the second enqueues it."""
    await worker.run_cron(now, 0.5)
    await worker.run_cron(now + timedelta(seconds=0.5), 0.5)


async def _drain(queue: JobQueue) -> int:
    """Run a burst worker (with the cron jobs registered, so their options apply) until empty."""
    worker = create_worker(
        WorkerSettings,
        functions=build_functions(),
        cron_jobs=build_cron_jobs(_settings()),
        queue_name=queue.name,
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
    )
    try:
        await asyncio.wait_for(worker.async_run(), 20)
    finally:
        await worker.pool.aclose()
    return worker.jobs_complete


@pytest.fixture
async def occurrence(job_queue: JobQueue) -> Any:
    """A past occurrence (random, so parallel runs never share a job id) and its cleanup."""
    minute = random.randrange(0, 2_000_000)  # noqa: S311  (2020-2023: always in the past)
    run_at = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(minutes=minute - minute % 5)
    run_at += timedelta(microseconds=123456)
    job_id = f"{HEARTBEAT}:{to_unix_ms(run_at)}"
    pool = await core_jobs.get_jobs_pool()
    try:
        yield run_at - timedelta(seconds=1), job_id, pool
    finally:
        await pool.delete(*[f"arq:{k}:{job_id}" for k in ("job", "result", "in-progress", "retry")])


async def test_two_workers_scheduling_the_same_occurrence_run_it_once(
    job_queue: JobQueue, occurrence: Any
) -> None:
    now, job_id, pool = occurrence
    await asyncio.gather(
        _scheduler_tick(_worker(job_queue, pool), now),
        _scheduler_tick(_worker(job_queue, pool), now),
    )
    assert await pool.zcard(job_queue.name) == 1
    assert await _drain(job_queue) == 1
    assert await pool.exists(f"arq:result:{job_id}") == 1


async def test_a_lagging_worker_cannot_rerun_an_occurrence_that_already_finished(
    job_queue: JobQueue, occurrence: Any
) -> None:
    now, _job_id, pool = occurrence
    await _scheduler_tick(_worker(job_queue, pool), now)
    assert await _drain(job_queue) == 1

    await _scheduler_tick(_worker(job_queue, pool), now)  # the slow second worker

    assert await pool.zcard(job_queue.name) == 0
    assert await _drain(job_queue) == 0


async def test_cron_results_are_kept_for_a_day(job_queue: JobQueue, occurrence: Any) -> None:
    now, job_id, pool = occurrence
    await _scheduler_tick(_worker(job_queue, pool), now)
    await _drain(job_queue)
    ttl = await pool.pttl(f"arq:result:{job_id}")
    assert 23 * 3600 * 1000 < ttl <= 24 * 3600 * 1000
