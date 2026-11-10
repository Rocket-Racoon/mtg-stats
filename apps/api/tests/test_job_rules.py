"""Tests for the job rules of S-1.9.2: timeout, retries, de-duplication and the end-of-run log."""

import asyncio
import contextlib
import logging
from typing import Any

import pytest
from arq.jobs import Job
from arq.worker import Retry, create_worker

from app.core import jobs as core_jobs
from app.core.jobs import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_RETRIES,
    JobStatus,
    NonRetryableJobError,
    RetryableJobError,
    enqueue,
    job_id,
    job_status,
)
from app.workers import registry
from app.workers.registry import JobSpec, build_functions, retry_delay, wrap_job
from app.workers.settings import WorkerSettings
from tests.jobs import JobQueue, run_job_in_process

attempts: dict[str, int] = {}


async def flaky(ctx: dict[str, Any], key: str, fail_times: int) -> str:
    """Raise a retryable error for the first ``fail_times`` attempts."""
    attempts[key] = attempts.get(key, 0) + 1
    if attempts[key] <= fail_times:
        raise RetryableJobError("transient")
    return "ok"


async def fatal(ctx: dict[str, Any], key: str) -> None:
    attempts[key] = attempts.get(key, 0) + 1
    raise NonRetryableJobError("no")


async def plain_error(ctx: dict[str, Any], key: str) -> None:
    attempts[key] = attempts.get(key, 0) + 1
    raise ValueError("unexpected")


async def sleepy(ctx: dict[str, Any], seconds: float) -> str:
    await asyncio.sleep(seconds)
    return "done"


EXTRA = {
    "tests.flaky": flaky,
    "tests.fatal": fatal,
    "tests.plain_error": plain_error,
    "tests.sleepy": JobSpec(sleepy, timeout=0.3),
}


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts.clear()
    monkeypatch.setattr(registry, "RETRY_DELAYS_SECONDS", (0, 0, 0))


async def _drain(queue: JobQueue) -> Any:
    worker = create_worker(
        WorkerSettings,
        functions=build_functions(EXTRA),
        queue_name=queue.name,
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
    )
    try:
        await asyncio.wait_for(worker.async_run(), 30)
    finally:
        await worker.pool.aclose()
    return worker


async def _result(job_id_: str) -> Any:
    pool = await core_jobs.get_jobs_pool()
    return await Job(job_id_, pool, _deserializer=core_jobs.deserialize_job).result_info()


def _end_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "app.workers.registry"]


# --- constants and helpers ---------------------------------------------------------------------


def test_defaults_are_ten_minutes_and_three_retries() -> None:
    assert DEFAULT_TIMEOUT_SECONDS == 600
    assert WorkerSettings.job_timeout == 600
    assert MAX_RETRIES == 3
    assert JobSpec(flaky).timeout == 600
    assert JobSpec(flaky).max_retries == 3


def test_retry_delays_grow_and_the_last_one_repeats() -> None:
    assert core_jobs.RETRY_DELAYS_SECONDS == (10, 60, 300)
    assert [retry_delay(n) for n in (1, 2, 3, 4)] == [0, 0, 0, 0]  # patched fast by the fixture


def test_retry_delay_schedule_is_increasing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "RETRY_DELAYS_SECONDS", core_jobs.RETRY_DELAYS_SECONDS)
    delays = [retry_delay(n) for n in (1, 2, 3, 4)]
    assert delays == [10, 60, 300, 300]


def test_job_id_joins_name_and_parts() -> None:
    assert (
        job_id("workflows.scryfall_daily_sync", "2026-11-07")
        == "workflows.scryfall_daily_sync:2026-11-07"
    )
    assert job_id("system.ping") == "system.ping"
    assert job_id("a.b", "x", "y") == "a.b:x:y"


@pytest.mark.parametrize("name", ["ping", "Sys.ping", "a.b:c", ""])
def test_job_id_rejects_a_bad_name(name: str) -> None:
    with pytest.raises(ValueError, match="invalid job name"):
        job_id(name, "x")


@pytest.mark.parametrize("part", ["", "a:b"])
def test_job_id_rejects_empty_parts_and_colons(part: str) -> None:
    with pytest.raises(ValueError, match="invalid job id part"):
        job_id("a.b", part)


def test_job_id_rejects_non_string_parts() -> None:
    with pytest.raises(TypeError):
        job_id("a.b", 3)  # type: ignore[arg-type]


def test_build_functions_gives_arq_a_longer_timeout_than_the_wrapper() -> None:
    by_name = {f.name: f for f in build_functions(EXTRA)}
    assert by_name["system.ping"].timeout_s == 600 + registry.ARQ_TIMEOUT_MARGIN_SECONDS
    assert by_name["tests.sleepy"].timeout_s == 0.3 + registry.ARQ_TIMEOUT_MARGIN_SECONDS
    assert by_name["system.ping"].max_tries == MAX_RETRIES + 1 + registry.SHUTDOWN_SPARE_TRIES


# --- the wrapper in process --------------------------------------------------------------------


async def test_wrapper_turns_a_retryable_error_into_arq_retry_with_the_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "RETRY_DELAYS_SECONDS", (10, 60, 300))
    wrapped = wrap_job("tests.flaky", JobSpec(flaky))
    with pytest.raises(Retry) as info:
        await wrapped({"job_id": "j", "job_try": 2}, key="k", fail_times=9)
    assert info.value.defer_score == 60_000


async def test_wrapper_stops_retrying_after_max_retries() -> None:
    wrapped = wrap_job("tests.flaky", JobSpec(flaky))
    with pytest.raises(RetryableJobError):
        await wrapped({"job_id": "j", "job_try": MAX_RETRIES + 1}, key="k", fail_times=9)


async def test_wrapper_does_not_retry_other_errors() -> None:
    wrapped = wrap_job("tests.plain_error", JobSpec(plain_error))
    with pytest.raises(ValueError, match="unexpected"):
        await wrapped({"job_id": "j", "job_try": 1}, key="k")


async def test_wrapper_timeout_raises_timeout_error_and_cancels_the_job() -> None:
    wrapped = wrap_job("tests.sleepy", JobSpec(sleepy, timeout=0.05))
    with pytest.raises(TimeoutError):
        await wrapped({"job_id": "j", "job_try": 1}, seconds=5)


async def test_wrapper_logs_one_line_with_outcome_try_and_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    wrapped = wrap_job("tests.sleepy", JobSpec(sleepy))
    with caplog.at_level("INFO", logger="app.workers.registry"):
        assert await wrapped({"job_id": "j1", "job_try": 1}, seconds=0.05) == "done"
    (record,) = _end_lines(caplog)
    assert record.levelno == logging.INFO
    assert record.__dict__["outcome"] == "success"
    assert record.__dict__["try"] == 1
    assert record.__dict__["duration_ms"] >= 30


async def test_wrapper_logs_cancelled_when_the_task_is_cancelled_from_outside(
    caplog: pytest.LogCaptureFixture,
) -> None:
    wrapped = wrap_job("tests.sleepy", JobSpec(sleepy))
    with caplog.at_level("INFO", logger="app.workers.registry"):
        task = asyncio.create_task(wrapped({"job_id": "j", "job_try": 1}, seconds=5))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    (record,) = _end_lines(caplog)
    assert record.__dict__["outcome"] == "cancelled"


# --- through a real (burst) worker -------------------------------------------------------------


async def test_job_running_past_its_timeout_is_failed_not_retried_and_logged_timeout(
    job_queue: JobQueue, caplog: pytest.LogCaptureFixture
) -> None:
    jid = await enqueue("tests.sleepy", _job_id=job_queue.job_id("t"), seconds=5)
    with caplog.at_level("INFO", logger="app.workers.registry"):
        worker = await _drain(job_queue)
    assert worker.jobs_failed == 1
    assert worker.jobs_retried == 0
    assert await job_status(jid) is JobStatus.FAILED
    info = await _result(jid)
    assert info is not None
    assert info.job_try == 1
    assert [r.__dict__["outcome"] for r in _end_lines(caplog)] == ["timeout"]


async def test_retryable_error_is_retried_three_times_then_succeeds(
    job_queue: JobQueue, caplog: pytest.LogCaptureFixture
) -> None:
    jid = await enqueue("tests.flaky", _job_id=job_queue.job_id("r"), key="ok", fail_times=3)
    with caplog.at_level("INFO", logger="app.workers.registry"):
        worker = await _drain(job_queue)
    assert attempts["ok"] == 4
    assert worker.jobs_retried == 3
    assert await job_status(jid) is JobStatus.COMPLETE
    outcomes = [r.__dict__["outcome"] for r in _end_lines(caplog)]
    assert outcomes == ["retry", "retry", "retry", "success"]
    assert [r.__dict__["try"] for r in _end_lines(caplog)] == [1, 2, 3, 4]


async def test_retryable_error_is_failed_after_three_retries(job_queue: JobQueue) -> None:
    jid = await enqueue("tests.flaky", _job_id=job_queue.job_id("r"), key="bad", fail_times=99)
    worker = await _drain(job_queue)
    assert attempts["bad"] == 4  # the first attempt plus 3 retries
    assert worker.jobs_failed == 1
    assert await job_status(jid) is JobStatus.FAILED


@pytest.mark.parametrize("name", ["tests.fatal", "tests.plain_error"])
async def test_non_retryable_error_runs_once_and_fails(job_queue: JobQueue, name: str) -> None:
    jid = await enqueue(name, _job_id=job_queue.job_id("n"), key=name)
    worker = await _drain(job_queue)
    assert attempts[name] == 1
    assert worker.jobs_retried == 0
    assert await job_status(jid) is JobStatus.FAILED


async def test_second_enqueue_with_the_same_deterministic_id_while_queued_is_a_no_op(
    job_queue: JobQueue,
) -> None:
    name = "tests.sleepy"
    deterministic = job_queue.job_id(job_id(name, "2026-11-07"))
    first = await enqueue(name, _job_id=deterministic, seconds=0.01)
    second = await enqueue(name, _job_id=deterministic, seconds=0.01)
    assert first == second == deterministic
    pool = await core_jobs.get_jobs_pool()
    assert await pool.zcard(job_queue.name) == 1


async def test_second_enqueue_while_the_first_is_running_does_not_start_another_run(
    job_queue: JobQueue,
) -> None:
    jid = job_queue.job_id("running")
    await enqueue("tests.flaky", _job_id=jid, key="dedup", fail_times=0)
    # Run it with a worker whose job blocks, so the ID is "running" while we enqueue again.
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocker(ctx: dict[str, Any], **kwargs: Any) -> str:
        started.set()
        await release.wait()
        return "done"

    worker = create_worker(
        WorkerSettings,
        functions=build_functions({"tests.flaky": blocker}),
        queue_name=job_queue.name,
        poll_delay=0.05,
        handle_signals=False,
    )
    task = asyncio.create_task(worker.async_run())
    try:
        await asyncio.wait_for(started.wait(), 10)
        assert await job_status(jid) is JobStatus.IN_PROGRESS
        assert await enqueue("tests.flaky", _job_id=jid, key="dedup", fail_times=0) == jid
        release.set()
        async with asyncio.timeout(10):
            while await job_status(jid) is not JobStatus.COMPLETE:  # noqa: ASYNC110
                await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)  # a second run, if one had been queued, would start by now
        pool = await core_jobs.get_jobs_pool()
        assert await pool.zcard(job_queue.name) == 0
        assert worker.jobs_complete == 1
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*worker.tasks.values(), return_exceptions=True)
        await worker.pool.aclose()


# --- review fixes: timeout always wins, arq.Retry budget, helpers ------------------------------


async def _swallow(ctx: dict[str, Any]) -> str:
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.sleep(5)
    return "swallowed"


async def _retry_on_cancel(ctx: dict[str, Any]) -> None:
    try:
        await asyncio.sleep(5)
    except asyncio.CancelledError:
        raise RetryableJobError("cleanup") from None


async def _arq_retry(ctx: dict[str, Any]) -> None:
    raise Retry(defer=1)


@pytest.mark.parametrize("fn", [_swallow, _retry_on_cancel])
async def test_timed_out_job_is_failed_and_logged_timeout_whatever_it_does_on_cancel(
    fn: Any, caplog: pytest.LogCaptureFixture
) -> None:
    wrapped = wrap_job("tests.x", JobSpec(fn, timeout=0.05))
    with caplog.at_level("INFO", logger="app.workers.registry"), pytest.raises(TimeoutError):
        await wrapped({"job_id": "j", "job_try": 1})
    assert [r.__dict__["outcome"] for r in _end_lines(caplog)] == ["timeout"]


async def test_cleanup_outliving_the_timeout_is_still_logged_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def stubborn(ctx: dict[str, Any]) -> None:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            await asyncio.sleep(5)

    wrapped = wrap_job("tests.x", JobSpec(stubborn, timeout=0.05))
    # arq's backstop cancels it from outside while its cleanup is still running.
    with caplog.at_level("INFO", logger="app.workers.registry"), pytest.raises(TimeoutError):
        await asyncio.wait_for(wrapped({"job_id": "j", "job_try": 1}), 0.2)
    assert [r.__dict__["outcome"] for r in _end_lines(caplog)] == ["timeout"]


async def test_a_job_raising_its_own_timeout_error_is_failed_not_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def own(ctx: dict[str, Any]) -> None:
        raise TimeoutError("http")

    wrapped = wrap_job("tests.x", JobSpec(own))
    with caplog.at_level("INFO", logger="app.workers.registry"), pytest.raises(TimeoutError):
        await wrapped({"job_id": "j", "job_try": 1})
    assert [r.__dict__["outcome"] for r in _end_lines(caplog)] == ["failed"]


async def test_arq_retry_raised_by_a_job_obeys_the_retry_budget() -> None:
    wrapped = wrap_job("tests.x", JobSpec(_arq_retry))
    with pytest.raises(Retry):
        await wrapped({"job_id": "j", "job_try": MAX_RETRIES})
    with pytest.raises(RetryableJobError, match="out of retries"):
        await wrapped({"job_id": "j", "job_try": MAX_RETRIES + 1})


def test_arq_options_match_the_spec() -> None:
    opts = registry.arq_options(JobSpec(flaky, timeout=30, max_retries=1))
    assert opts == {"timeout": 30 + registry.ARQ_TIMEOUT_MARGIN_SECONDS, "max_tries": 1 + 1 + 3}
    assert registry.SHUTDOWN_SPARE_TRIES == 3
    assert WorkerSettings.max_tries == MAX_RETRIES + 1 + registry.SHUTDOWN_SPARE_TRIES


async def test_run_job_in_process_can_run_through_the_wrapper(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger="app.workers.registry"):
        assert await run_job_in_process("system.ping", wrapped=True) == "pong"
        assert await run_job_in_process("system.ping") == "pong"
    assert [r.__dict__["outcome"] for r in _end_lines(caplog)] == ["success"]
