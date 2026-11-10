"""Acceptance tests for S-1.9.2 (T-1.9.2.3): timeout, retry count, de-duplication, end-of-run log.

Complements ``test_job_rules.py``: per-job timeouts, custom retry budgets, the delays actually
stored in Redis, the exact log lines, and the story's deterministic-ID example.
"""

import asyncio
import json
import logging
import time
from collections.abc import Iterator
from typing import Any

import pytest
from arq.jobs import Job
from arq.worker import Retry, create_worker

from app.core import jobs as core_jobs
from app.core.jobs import (
    JobStatus,
    NonRetryableJobError,
    RetryableJobError,
    enqueue,
    job_id,
    job_status,
)
from app.core.logging import JsonFormatter
from app.workers import registry
from app.workers.registry import JobSpec, build_functions, wrap_job
from app.workers.settings import WorkerSettings
from tests.jobs import JobQueue

attempts: dict[str, int] = {}


async def flaky(ctx: dict[str, Any], key: str, fail_times: int) -> str:
    attempts[key] = attempts.get(key, 0) + 1
    if attempts[key] <= fail_times:
        try:
            raise ConnectionError("upstream down")
        except ConnectionError as cause:
            raise RetryableJobError("transient") from cause
    return "ok"


async def count_run(ctx: dict[str, Any], key: str) -> str:
    attempts[key] = attempts.get(key, 0) + 1
    return "ok"


async def fatal(ctx: dict[str, Any], key: str) -> None:
    attempts[key] = attempts.get(key, 0) + 1
    raise NonRetryableJobError("no")


async def sleepy(ctx: dict[str, Any], seconds: float) -> str:
    await asyncio.sleep(seconds)
    return "done"


EXTRA: dict[str, Any] = {
    "tests.flaky": flaky,
    "tests.flaky1": JobSpec(flaky, max_retries=1),
    "tests.flaky5": JobSpec(flaky, max_retries=5),
    "tests.flaky0": JobSpec(flaky, max_retries=0),
    "tests.count": count_run,
    "tests.fatal": fatal,
    "tests.short": JobSpec(sleepy, timeout=0.2),
    "tests.tight": JobSpec(sleepy, timeout=2.0),
}


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts.clear()
    monkeypatch.setattr(registry, "RETRY_DELAYS_SECONDS", (0, 0, 0))


def _worker(queue: JobQueue, *, burst: bool) -> Any:
    return create_worker(
        WorkerSettings,
        functions=build_functions(EXTRA),
        queue_name=queue.name,
        burst=burst,
        poll_delay=0.05,
        handle_signals=False,
    )


async def _drain(queue: JobQueue) -> Any:
    worker = _worker(queue, burst=True)
    try:
        await asyncio.wait_for(worker.async_run(), 30)
    finally:
        await worker.pool.aclose()
    return worker


async def _info(job_id_: str) -> Any:
    pool = await core_jobs.get_jobs_pool()
    return await Job(job_id_, pool, _deserializer=core_jobs.deserialize_job).result_info()


class _Capture(logging.Handler):
    """Collects each record of the registry logger as the JSON line the app would print."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.lines: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


@pytest.fixture
def end_lines() -> Iterator[list[dict[str, Any]]]:
    handler = _Capture()
    log = logging.getLogger("app.workers.registry")
    old_level = log.level
    log.setLevel(logging.DEBUG)
    log.addHandler(handler)
    yield handler.lines
    log.removeHandler(handler)
    log.setLevel(old_level)


# --- AC1: timeout ------------------------------------------------------------------------------


def test_default_spec_timeout_is_600_seconds_and_arq_backstop_is_longer() -> None:
    assert JobSpec(count_run).timeout == 600
    by_name = {f.name: f for f in build_functions(EXTRA)}
    assert by_name["tests.count"].timeout_s > 600
    assert by_name["system.ping"].timeout_s > 600
    assert by_name["tests.short"].timeout_s > 0.2


async def test_wrapper_applies_the_default_600_second_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[float | None] = []
    real = asyncio.timeout

    def spy(delay: float | None) -> Any:
        seen.append(delay)
        return real(delay)

    monkeypatch.setattr(asyncio, "timeout", spy)
    wrapped = wrap_job("tests.count", JobSpec(count_run))
    assert await wrapped({"job_id": "j", "job_try": 1}, key="k") == "ok"
    assert seen == [600]


async def test_job_with_a_shorter_own_timeout_is_cancelled_promptly_and_failed(
    job_queue: JobQueue, end_lines: list[dict[str, Any]]
) -> None:
    jid = await enqueue("tests.short", _job_id=job_queue.job_id("s"), seconds=30)
    started = time.monotonic()
    worker = await _drain(job_queue)
    assert time.monotonic() - started < 10
    assert worker.jobs_failed == 1
    assert worker.jobs_retried == 0
    assert await job_status(jid) is JobStatus.FAILED
    assert [line["outcome"] for line in end_lines] == ["timeout"]


async def test_job_finishing_just_under_its_timeout_succeeds(
    job_queue: JobQueue, end_lines: list[dict[str, Any]]
) -> None:
    jid = await enqueue("tests.tight", _job_id=job_queue.job_id("u"), seconds=1.0)
    worker = await _drain(job_queue)
    assert worker.jobs_complete == 1
    assert await job_status(jid) is JobStatus.COMPLETE
    assert [line["outcome"] for line in end_lines] == ["success"]


async def test_timed_out_job_is_not_retried_even_though_it_could_be(
    job_queue: JobQueue,
) -> None:
    jid = await enqueue("tests.short", _job_id=job_queue.job_id("nr"), seconds=30)
    await _drain(job_queue)
    info = await _info(jid)
    assert info.job_try == 1
    assert info.success is False


# --- AC2: retries ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "retries"),
    [("tests.flaky0", 0), ("tests.flaky1", 1), ("tests.flaky", 3), ("tests.flaky5", 5)],
)
async def test_job_gets_exactly_its_own_max_retries(
    job_queue: JobQueue, name: str, retries: int
) -> None:
    jid = await enqueue(name, _job_id=job_queue.job_id("m"), key=name, fail_times=99)
    worker = await _drain(job_queue)
    assert attempts[name] == retries + 1
    assert worker.jobs_retried == retries
    assert worker.jobs_failed == 1
    assert await job_status(jid) is JobStatus.FAILED


async def test_failed_job_after_the_last_retry_keeps_the_error_type_and_message(
    job_queue: JobQueue,
) -> None:
    jid = await enqueue("tests.flaky", _job_id=job_queue.job_id("e"), key="e", fail_times=99)
    await _drain(job_queue)
    info = await _info(jid)
    assert info.success is False
    assert info.job_try == 4
    assert info.result == {"error_type": "RetryableJobError", "error": "transient"}


async def test_retryable_error_keeps_its_cause_in_process() -> None:
    wrapped = wrap_job("tests.flaky", JobSpec(flaky))
    with pytest.raises(RetryableJobError) as final:
        await wrapped({"job_id": "j", "job_try": 4}, key="c", fail_times=99)
    assert isinstance(final.value.__cause__, ConnectionError)
    with pytest.raises(Retry) as again:
        await wrapped({"job_id": "j", "job_try": 1}, key="c2", fail_times=99)
    assert isinstance(again.value.__cause__, RetryableJobError)
    assert isinstance(again.value.__cause__.__cause__, ConnectionError)


async def test_non_retryable_error_is_failed_at_once_with_one_failed_line(
    job_queue: JobQueue, end_lines: list[dict[str, Any]]
) -> None:
    jid = await enqueue("tests.fatal", _job_id=job_queue.job_id("f"), key="f")
    worker = await _drain(job_queue)
    assert attempts["f"] == 1
    assert worker.jobs_retried == 0
    assert await job_status(jid) is JobStatus.FAILED
    assert [line["outcome"] for line in end_lines] == ["failed"]


async def test_retry_delays_stored_in_redis_grow_10_60_300_seconds(
    job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry, "RETRY_DELAYS_SECONDS", core_jobs.RETRY_DELAYS_SECONDS)
    jid = await enqueue("tests.flaky", _job_id=job_queue.job_id("d"), key="d", fail_times=99)
    pool = await core_jobs.get_jobs_pool()
    worker = _worker(job_queue, burst=False)
    task = asyncio.create_task(worker.async_run())
    delays: list[float] = []
    try:
        for expected_try in (1, 2, 3):
            async with asyncio.timeout(10):
                while attempts.get("d", 0) < expected_try:  # noqa: ASYNC110
                    await asyncio.sleep(0.02)
            await asyncio.sleep(0.2)  # let the worker finish re-queueing before we look
            score = await pool.zscore(job_queue.name, jid)
            assert score is not None
            delays.append(score / 1000 - time.time())
            assert await job_status(jid) is JobStatus.QUEUED
            # Make it due now instead of waiting. Dedupe during the delay holds because
            # arq:job:<id> survives a Retry (the in-progress key is already gone).
            await pool.zadd(job_queue.name, {jid: 1})  # not 0: arq treats a 0 score as finished
        async with asyncio.timeout(10):
            while await job_status(jid) is not JobStatus.FAILED:  # noqa: ASYNC110
                await asyncio.sleep(0.05)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*worker.tasks.values(), return_exceptions=True)
        await worker.pool.aclose()
    assert attempts["d"] == 4
    for got, want in zip(delays, (10, 60, 300), strict=True):
        assert want - 2 <= got <= want + 1
    assert delays == sorted(delays)


# --- AC3: de-duplication -----------------------------------------------------------------------


async def test_same_story_style_id_enqueued_twice_runs_once(job_queue: JobQueue) -> None:
    suffix = f"{job_queue.token}-2026-11-07"
    deterministic = job_id("tests.count", suffix)
    assert deterministic == f"tests.count:{suffix}"
    first = await enqueue("tests.count", _job_id=deterministic, key="a")
    second = await enqueue("tests.count", _job_id=deterministic, key="a")
    assert first == second == deterministic
    worker = await _drain(job_queue)
    assert attempts["a"] == 1
    assert worker.jobs_complete == 1


async def test_different_id_parts_give_two_runs(job_queue: JobQueue) -> None:
    one = job_id("tests.count", f"{job_queue.token}-2026-11-07")
    two = job_id("tests.count", f"{job_queue.token}-2026-11-08")
    assert one != two
    await enqueue("tests.count", _job_id=one, key="a")
    await enqueue("tests.count", _job_id=two, key="a")
    worker = await _drain(job_queue)
    assert attempts["a"] == 2
    assert worker.jobs_complete == 2


async def test_id_stays_blocked_after_the_run_finished_while_the_result_is_kept(
    job_queue: JobQueue,
) -> None:
    jid = job_queue.job_id("done")
    await enqueue("tests.count", _job_id=jid, key="k")
    await _drain(job_queue)
    assert await enqueue("tests.count", _job_id=jid, key="k") == jid
    pool = await core_jobs.get_jobs_pool()
    assert await pool.zcard(job_queue.name) == 0
    assert attempts["k"] == 1


# --- job_id validation -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["ping", "Sys.ping", "sys.Ping", "a.b.c", "1a.b", "a.", ".b", "a b.c", ""]
)
def test_job_id_rejects_malformed_names(name: str) -> None:
    with pytest.raises(ValueError, match="invalid job name"):
        job_id(name)


@pytest.mark.parametrize("part", ["", "a:b", ":", "2026:11"])
def test_job_id_rejects_empty_and_colon_parts(part: str) -> None:
    with pytest.raises(ValueError, match="invalid job id part"):
        job_id("a.b", "ok", part)


@pytest.mark.parametrize("part", [3, None, 1.5, b"x"])
def test_job_id_rejects_non_string_parts(part: Any) -> None:
    with pytest.raises(TypeError):
        job_id("a.b", part)


def test_job_id_is_deterministic() -> None:
    assert job_id("workflows.scryfall_daily_sync", "2026-11-07") == job_id(
        "workflows.scryfall_daily_sync", "2026-11-07"
    )


# --- AC4: end-of-run log -----------------------------------------------------------------------


async def test_three_retries_then_failure_log_exactly_four_lines_in_order(
    job_queue: JobQueue, end_lines: list[dict[str, Any]]
) -> None:
    jid = await enqueue("tests.flaky", _job_id=job_queue.job_id("l"), key="l", fail_times=99)
    await _drain(job_queue)
    assert len(end_lines) == 4
    assert [line["outcome"] for line in end_lines] == ["retry", "retry", "retry", "failed"]
    assert [line["try"] for line in end_lines] == [1, 2, 3, 4]
    for line in end_lines:
        assert line["job"] == "tests.flaky"
        assert line["job_id"] == jid
        assert isinstance(line["duration_ms"], int)
        assert line["duration_ms"] >= 0


async def test_success_line_has_name_id_integer_duration_and_outcome(
    job_queue: JobQueue, end_lines: list[dict[str, Any]]
) -> None:
    jid = await enqueue("tests.tight", _job_id=job_queue.job_id("ok"), seconds=0.2)
    await _drain(job_queue)
    (line,) = end_lines
    assert line["job"] == "tests.tight"
    assert line["job_id"] == jid
    assert line["outcome"] == "success"
    assert isinstance(line["duration_ms"], int)
    assert 150 <= line["duration_ms"] < 2000


async def test_timeout_line_reports_the_elapsed_time_near_the_timeout(
    job_queue: JobQueue, end_lines: list[dict[str, Any]]
) -> None:
    await enqueue("tests.short", _job_id=job_queue.job_id("tl"), seconds=30)
    await _drain(job_queue)
    (line,) = end_lines
    assert line["outcome"] == "timeout"
    assert 150 <= line["duration_ms"] < 3000
