"""Acceptance tests for S-1.9.3 (scheduled jobs), written from the four acceptance criteria.

AC1 and AC3 also run the real worker as a subprocess (``tests.slow_worker``) and read its stderr.
AC2 uses real, concurrently running in-process workers on a private queue (two real subprocesses
would need to wait for a real 5-minute boundary, so the live race uses an every-second schedule).
"""

import asyncio
import dataclasses
import logging
import random
import re
import signal
from contextlib import suppress
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from arq.cron import next_cron
from arq.utils import to_unix_ms
from arq.worker import Worker, create_worker

from app.core import jobs as core_jobs
from app.core.config import Settings, get_settings
from app.core.jobs import JobStatus, enqueue, job_status
from app.workers import schedule as schedule_mod
from app.workers.registry import JOBS, build_functions
from app.workers.schedule import (
    SCHEDULES,
    Schedule,
    build_cron_jobs,
    log_schedule,
    scheduler_disabled_reason,
)
from app.workers.settings import WorkerSettings
from tests.jobs import JobQueue, make_drain_worker, run_job_in_process
from tests.test_worker_e2e import _start_worker, _terminate, _wait_for

HEARTBEAT = "system.heartbeat"
ISO_UTC = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00"


def _settings(env: str = "dev", enabled: bool = True) -> Settings:
    update: dict[str, Any] = {"app_env": env, "scheduler_enabled": enabled}
    return get_settings().model_copy(update=update)


def _next_run_of(line: str) -> datetime:
    match = re.search(rf"next_run=({ISO_UTC})", line)
    assert match, line
    return datetime.fromisoformat(match.group(1))


# --- AC1: startup log --------------------------------------------------------------------------


def test_startup_log_has_a_line_for_every_registered_schedule_with_future_utc_next_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    before = datetime.now(UTC)
    with caplog.at_level(logging.INFO, logger="app.workers.schedule"):
        log_schedule(_settings("dev"))
    messages = [r.getMessage() for r in caplog.records]
    for name in SCHEDULES:
        (line,) = [m for m in messages if f"scheduled job {name} " in m]
        next_run = _next_run_of(line)
        assert next_run.utcoffset() == timedelta(0)
        assert next_run > before


def test_heartbeat_next_run_in_log_is_on_a_five_minute_utc_boundary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.workers.schedule"):
        log_schedule(_settings("dev"), datetime(2026, 7, 1, 12, 3, 30, tzinfo=UTC))
    (line,) = [r.getMessage() for r in caplog.records if HEARTBEAT in r.getMessage()]
    next_run = _next_run_of(line)
    assert (next_run.hour, next_run.minute, next_run.second) == (12, 5, 0)


def test_a_schedule_registered_by_a_domain_is_logged_with_its_own_next_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        schedule_mod, "SCHEDULES", {**SCHEDULES, "tests.hourly": Schedule(minute=30)}
    )
    now = datetime(2026, 7, 1, 12, 45, 0, tzinfo=UTC)
    with caplog.at_level(logging.INFO, logger="app.workers.schedule"):
        log_schedule(_settings("dev"), now)
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == len(SCHEDULES) + 1
    (line,) = [m for m in messages if "scheduled job tests.hourly " in m]
    assert "minute=30" in line
    assert _next_run_of(line).replace(microsecond=0) == datetime(2026, 7, 1, 13, 30, tzinfo=UTC)


def test_next_run_is_correct_for_a_non_utc_input() -> None:
    plus_two = timezone(timedelta(hours=2))
    after = datetime(2026, 7, 1, 14, 3, 0, tzinfo=plus_two)  # 12:03 UTC
    result = SCHEDULES[HEARTBEAT].next_run(after)
    assert result.astimezone(UTC).replace(microsecond=0) == datetime(
        2026, 7, 1, 12, 5, 0, tzinfo=UTC
    )


def test_a_real_worker_evaluates_schedules_in_utc() -> None:
    # Building a worker opens no connection (the pool is made in ``main``), so nothing to close.
    worker = create_worker(WorkerSettings, queue_name="arq:queue:test-tz", handle_signals=False)
    assert worker.timezone is UTC


def test_worker_timezone_is_utc() -> None:
    assert WorkerSettings.timezone is UTC


async def _stderr_of_worker(job_queue: JobQueue, env: dict[str, str]) -> tuple[str, JobStatus]:
    """Start the real worker with ``env``, run one ping through it, stop it; return stderr."""
    proc = _start_worker(job_queue, grace=5, extra_env=env)
    try:
        ping_id = await enqueue("system.ping", _job_id=job_queue.job_id("ping"))
        await _wait_for(ping_id, JobStatus.COMPLETE)
        status = await job_status(ping_id)
        _terminate(proc)
        _, stderr = await asyncio.to_thread(proc.communicate, None, 40)
    finally:
        if proc.poll() is None:
            proc.kill()
            with suppress(Exception):
                proc.communicate()
    return stderr, status


async def test_real_worker_in_dev_logs_every_schedule_with_its_next_run_at_startup(
    job_queue: JobQueue,
) -> None:
    before = datetime.now(UTC)
    pool = await core_jobs.get_jobs_pool()
    pattern = "arq:*:system.heartbeat:*"
    existing = {k async for k in pool.scan_iter(match=pattern)}
    try:
        stderr, _ = await _stderr_of_worker(
            job_queue, {"APP_ENV": "dev", "SCHEDULER_ENABLED": "true", "LOG_LEVEL": "INFO"}
        )
    finally:
        created = [k async for k in pool.scan_iter(match=pattern) if k not in existing]
        if created:
            await pool.delete(*created)
    for name in SCHEDULES:
        (line,) = [ln for ln in stderr.splitlines() if f"scheduled job {name} " in ln]
        next_run = _next_run_of(line)
        assert next_run > before
        if name == HEARTBEAT:
            assert next_run <= before + timedelta(minutes=5, seconds=30)
            assert next_run.minute % 5 == 0
            assert next_run.second == 0
        else:  # the daily jobs (S-2.1.5 adds identity.cleanup_refresh_tokens)
            assert next_run <= before + timedelta(days=1, seconds=30)


# --- AC3: disabled -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "enabled", "reason"),
    [
        ("test", True, "APP_ENV=test"),
        ("dev", False, "SCHEDULER_ENABLED=false"),
        ("test", False, None),  # both triggers at once: either reason is acceptable
        ("prod", False, "SCHEDULER_ENABLED=false"),
    ],
)
def test_scheduler_is_off_for_each_trigger_and_for_both_together(
    env: str, enabled: bool, reason: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(env, enabled)
    assert build_cron_jobs(settings) == []
    found = scheduler_disabled_reason(settings)
    assert found is not None
    if reason:
        assert found == reason
    with caplog.at_level(logging.INFO, logger="app.workers.schedule"):
        log_schedule(settings)
    (record,) = caplog.records
    assert found in record.getMessage()
    assert not re.search(r"scheduled job \w+\.", record.getMessage())


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_scheduler_is_on_when_enabled_outside_the_test_env(env: str) -> None:
    assert scheduler_disabled_reason(_settings(env, True)) is None
    assert len(build_cron_jobs(_settings(env, True))) == len(SCHEDULES)


def test_cron_jobs_are_unique_and_keep_their_result_for_a_day() -> None:
    for job in build_cron_jobs(_settings("dev")):
        assert job.unique is True
        assert job.keep_result_s == 24 * 3600
        assert job.name in SCHEDULES


@pytest.mark.parametrize(
    ("env", "enabled", "reason"),
    [("dev", "false", "SCHEDULER_ENABLED=false"), ("test", "true", "APP_ENV=test")],
)
async def test_real_worker_with_scheduler_disabled_logs_the_reason_and_still_runs_jobs(
    job_queue: JobQueue, env: str, enabled: str, reason: str
) -> None:
    stderr, status = await _stderr_of_worker(
        job_queue, {"APP_ENV": env, "SCHEDULER_ENABLED": enabled, "LOG_LEVEL": "INFO"}
    )
    assert status is JobStatus.COMPLETE  # normal enqueued jobs still run
    assert not re.search(r"scheduled job \w+\.", stderr)
    assert reason in stderr


# --- AC4: heartbeat every 5 minutes ------------------------------------------------------------


def _runs_in_window(start: datetime, minutes: int = 10) -> list[datetime]:
    """Occurrences arq itself computes in ``(start, start + minutes]``."""
    (job,) = [j for j in build_cron_jobs(_settings("dev")) if j.name == HEARTBEAT]
    end = start + timedelta(minutes=minutes)
    out: list[datetime] = []
    job.calculate_next(start)
    # arq adds a fixed 123456 microseconds to every occurrence; compare on whole seconds.
    while job.next_run.replace(microsecond=0) <= end:
        out.append(job.next_run)
        job.calculate_next(job.next_run)
    return out


@pytest.mark.parametrize(
    "start",
    [
        datetime(2026, 5, 6, 7, 0, 0, tzinfo=UTC),  # exactly on a boundary
        datetime(2026, 5, 6, 7, 5, 0, tzinfo=UTC),
        datetime(2026, 5, 6, 7, 4, 59, tzinfo=UTC),
        datetime(2026, 5, 6, 7, 0, 1, tzinfo=UTC),
        datetime(2026, 5, 6, 7, 57, 31, tzinfo=UTC),  # across the hour
        datetime(2026, 5, 6, 23, 52, 0, tzinfo=UTC),  # across midnight
        datetime(2026, 12, 31, 23, 56, 10, tzinfo=UTC),  # across the year
        datetime(2028, 2, 28, 23, 59, 59, tzinfo=UTC),  # across a leap day
    ],
)
def test_heartbeat_runs_exactly_twice_in_ten_minutes_five_minutes_apart(start: datetime) -> None:
    runs = _runs_in_window(start)
    assert len(runs) == 2
    assert runs[1] - runs[0] == timedelta(minutes=5)
    assert all(r.minute % 5 == 0 for r in runs)


async def test_worker_ticking_across_ten_minutes_runs_the_heartbeat_twice(
    job_queue: JobQueue, caplog: pytest.LogCaptureFixture
) -> None:
    """Drive arq's own scheduler loop over a past 10-minute window, then drain the queue."""
    base = datetime(2021, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * random.randrange(0, 400_000))  # noqa: S311
    ids = [
        f"{HEARTBEAT}:{to_unix_ms(base + timedelta(minutes=m, microseconds=123456))}"
        for m in (5, 10)
    ]
    pool = await core_jobs.get_jobs_pool()
    worker = create_worker(
        WorkerSettings,
        functions=build_functions(),
        cron_jobs=[j for j in build_cron_jobs(_settings("dev")) if j.name == HEARTBEAT],
        queue_name=job_queue.name,
        handle_signals=False,
        timezone=UTC,
        redis_pool=pool,
    )
    drain = make_drain_worker(job_queue.name, _settings("dev"))
    try:
        for tick in range(1201):  # base .. base + 10 min, every 0.5 s
            await worker.run_cron(base + timedelta(seconds=tick * 0.5), 0.5)
        with caplog.at_level(logging.INFO, logger="app.system.services.jobs"):
            await asyncio.wait_for(drain.async_run(), 20)
    finally:
        await drain.pool.aclose()
        await pool.delete(*[f"arq:{k}:{i}" for i in ids for k in ("job", "result", "in-progress")])
    assert [r.getMessage() for r in caplog.records].count("heartbeat") == 2


def test_heartbeat_occurrences_match_arqs_own_next_cron() -> None:
    start = datetime(2026, 5, 6, 7, 8, 9, tzinfo=UTC)
    minutes = set(range(0, 60, 5))
    first = next_cron(start, minute=minutes, second=0)
    second = next_cron(first, minute=minutes, second=0)
    got = _runs_in_window(start)
    assert [g.replace(microsecond=0) for g in got] == [
        first.replace(microsecond=0),
        second.replace(microsecond=0),
    ]


async def test_heartbeat_job_logs_exactly_one_heartbeat_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        await run_job_in_process(HEARTBEAT)
    lines = [r for r in caplog.records if r.getMessage() == "heartbeat"]
    assert len(lines) == 1
    assert lines[0].levelno == logging.INFO


def test_heartbeat_is_a_registered_job() -> None:
    assert HEARTBEAT in JOBS


# --- AC2: exactly once with real concurrency ---------------------------------------------------


async def test_two_live_workers_run_every_scheduled_second_exactly_once(
    job_queue: JobQueue,
) -> None:
    """Two workers run side by side for a few seconds on an every-second schedule."""
    name = f"tests.tick-{job_queue.token}"
    runs: list[str] = []

    async def tick(ctx: dict[str, Any]) -> None:
        runs.append(ctx["job_id"])

    (real,) = [j for j in build_cron_jobs(_settings("dev")) if j.name == HEARTBEAT]
    job = dataclasses.replace(
        real, name=name, coroutine=tick, minute=None, second=set(range(60)), next_run=None
    )

    def make() -> Worker:
        return create_worker(
            WorkerSettings,
            functions=build_functions(),
            cron_jobs=[job],
            queue_name=job_queue.name,
            poll_delay=0.05,
            handle_signals=False,
            timezone=UTC,
        )

    workers = [make(), make()]
    tasks = [asyncio.create_task(w.async_run()) for w in workers]
    pool = await core_jobs.get_jobs_pool()
    try:
        await asyncio.sleep(5)
        for w in workers:
            w.handle_sig_wait_for_completion(signal.SIGINT)
        for t in tasks:
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(t, 20)
        keys = [k async for k in pool.scan_iter(match=f"arq:result:{name}:*")]
        assert len(runs) >= 3, runs
        assert len(runs) == len(set(runs)), "an occurrence ran more than once"
        assert len(keys) == len(runs)
    finally:
        for t in tasks:
            t.cancel()
        for w in workers:
            with suppress(Exception):
                await w.close()
        leftovers = [k async for k in pool.scan_iter(match=f"arq:*:{name}:*")]
        if leftovers:
            await pool.delete(*leftovers)
