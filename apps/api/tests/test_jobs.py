"""Unit and integration tests for ``core.jobs`` and the worker registry (S-1.9.1).

Integration tests use the ``job_queue`` fixture (a private queue on the test Redis) and an
in-process burst worker, so they need Redis but no subprocess.
"""

import asyncio
import json
import re
from typing import Any
from uuid import uuid4

import pytest
from arq.connections import RedisSettings
from arq.worker import Worker, create_worker

from app.core import jobs as core_jobs
from app.core.config import get_settings
from app.core.db import AppSession
from app.core.jobs import JobStatus, enqueue, enqueue_after_commit, job_status
from app.core.logging import JsonFormatter, log_context
from app.workers.registry import JOBS, build_functions
from app.workers.settings import WorkerSettings
from tests.jobs import JobQueue, run_job_in_process
from tests.repo_paths import repo_root
from tests.slow_worker import TEST_JOBS

ARCHITECTURE = repo_root(__file__) / "docs" / "architecture.md"


async def _drain(queue: JobQueue) -> Worker:
    """Run a burst worker on ``queue`` until it is empty."""
    worker = create_worker(
        WorkerSettings,
        functions=build_functions(TEST_JOBS),
        queue_name=queue.name,
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
    )
    try:
        await asyncio.wait_for(worker.async_run(), 20)
    finally:
        await worker.pool.aclose()
    return worker


# --- in-process helper (AC4) -------------------------------------------------------------------


async def test_run_job_in_process_runs_a_registered_job_without_redis() -> None:
    assert await run_job_in_process("system.ping") == "pong"


async def test_run_job_in_process_rejects_a_non_json_argument() -> None:
    async def job(ctx: dict[str, Any], when: Any) -> None:
        raise AssertionError("must not run")

    with pytest.raises(TypeError, match="not JSON serializable"):
        await run_job_in_process(job, when=uuid4())


async def test_run_job_in_process_rejects_a_non_json_result() -> None:
    async def job(ctx: dict[str, Any]) -> Any:
        return uuid4()

    with pytest.raises(TypeError, match="not JSON serializable"):
        await run_job_in_process(job)


async def test_run_job_in_process_returns_the_json_round_tripped_result() -> None:
    async def job(ctx: dict[str, Any]) -> Any:
        return (1, 2)

    assert await run_job_in_process(job) == [1, 2]


async def test_run_job_in_process_passes_kwargs_and_ctx_to_a_function() -> None:
    async def job(ctx: dict[str, Any], x: int) -> list[Any]:
        return [ctx["job_id"], x]

    assert await run_job_in_process(job, x=3) == ["test-job", 3]
    assert await run_job_in_process(job, ctx={"job_id": "j"}, x=1) == ["j", 1]


def test_registry_names_are_canonical_owner_dot_job_names() -> None:
    assert JOBS
    for name in JOBS:
        assert core_jobs.JOB_NAME_PATTERN.fullmatch(name)


def _documented_job_names() -> set[str]:
    """Names in the first column of the architecture section 7 table; fails if it is unparseable."""
    text = ARCHITECTURE.read_text(encoding="utf-8")
    assert "## 7. Background jobs" in text, "architecture.md lost its section 7 heading"
    section = text.split("## 7. Background jobs", 1)[1].split("\n## 8.", 1)[0]
    lines = section.splitlines()
    header = [i for i, line in enumerate(lines) if re.match(r"\|\s*Job\s*\|", line)]
    assert len(header) == 1, "section 7 must contain exactly one table with a 'Job' header"
    assert re.match(r"\|\s*-+\s*\|", lines[header[0] + 1]), "table header lacks its separator"
    names: set[str] = set()
    for line in lines[header[0] + 2 :]:
        if not line.startswith("|"):
            break
        first_cell = line.split("|")[1].split(":")[0]
        row_names = re.findall(r"`([a-z_]+(?:\.[a-z_]+)?)`", first_cell)
        assert row_names, f"unparseable section 7 row: {line[:80]!r}"
        names.update(row_names)
    assert len(names) >= 20, f"parsed only {len(names)} rows from the section 7 table"
    assert {"system.ping", "stats.refresh_game_stats", "db_backup"} <= names
    return names


@pytest.mark.skipif(not ARCHITECTURE.is_file(), reason="needs docs/architecture.md (monorepo)")
def test_every_registered_job_is_listed_in_architecture_section_7() -> None:
    assert set(JOBS) <= _documented_job_names()


def test_worker_settings_use_the_redis_url_and_queue_from_settings() -> None:
    settings = get_settings()
    redis = WorkerSettings.redis_settings
    expected = RedisSettings.from_dsn(settings.redis_url)
    assert (redis.host, redis.port, redis.database) == (
        expected.host,
        expected.port,
        expected.database,
    )
    assert WorkerSettings.queue_name == settings.worker_queue_name
    assert WorkerSettings.job_completion_wait == 30
    assert {f.name for f in WorkerSettings.functions} == set(JOBS)


def test_log_context_adds_job_name_to_log_lines() -> None:
    import logging  # noqa: PLC0415

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hi", None, None)
    with log_context(job_id="abc", job="system.ping"):
        payload = json.loads(JsonFormatter().format(record))
    assert (payload["job_id"], payload["job"]) == ("abc", "system.ping")


# --- enqueue validation ------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ping", "System.ping", "system.", ".ping", "a.b.c", "x y.z"])
async def test_enqueue_rejects_malformed_job_names(name: str) -> None:
    with pytest.raises(ValueError, match="invalid job name"):
        await enqueue(name)


async def test_enqueue_after_commit_rejects_malformed_job_names(db_session: AppSession) -> None:
    with pytest.raises(ValueError, match="invalid job name"):
        enqueue_after_commit(db_session, "ping")


async def test_enqueue_after_commit_rejects_non_json_arguments_in_the_callers_transaction(
    db_session: AppSession, job_queue: JobQueue
) -> None:
    with pytest.raises(TypeError, match="not JSON serializable"):
        enqueue_after_commit(db_session, "system.ping", _job_id=job_queue.job_id("a"), id=uuid4())
    await db_session.commit()
    assert await job_status(job_queue.job_id("a")) is JobStatus.NOT_FOUND


async def test_concurrent_first_callers_share_one_pool(job_queue: JobQueue) -> None:
    await core_jobs.close_jobs_pool()
    first, second = await asyncio.gather(core_jobs.get_jobs_pool(), core_jobs.get_jobs_pool())
    assert first is second


async def test_finished_job_id_cannot_be_re_enqueued_within_keep_result(
    job_queue: JobQueue, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = await enqueue("system.ping", _job_id=job_queue.job_id("a"))
    worker = create_worker(
        WorkerSettings,
        functions=build_functions(TEST_JOBS),
        queue_name=job_queue.name,
        burst=True,
        keep_result=30,
        poll_delay=0.05,
        handle_signals=False,
    )
    try:
        await asyncio.wait_for(worker.async_run(), 20)
    finally:
        await worker.pool.aclose()
    assert await job_status(job_id) is JobStatus.COMPLETE
    with caplog.at_level("INFO", logger="app.core.jobs"):
        again = await enqueue("system.ping", _job_id=job_id)
    assert again == job_id
    pool = await core_jobs.get_jobs_pool()
    assert await pool.zcard(job_queue.name) == 0  # nothing was queued
    assert any("finished within the last 24 h" in r.getMessage() for r in caplog.records)


async def test_enqueue_rejects_arguments_that_are_not_json(job_queue: JobQueue) -> None:
    with pytest.raises(TypeError, match="not JSON serializable"):
        await enqueue("system.ping", _job_id=job_queue.job_id("a"), when=uuid4())


# --- enqueue, status and the worker ------------------------------------------------------------


async def test_enqueued_job_is_queued_until_a_worker_runs_it(job_queue: JobQueue) -> None:
    job_id = await enqueue("system.ping", _job_id=job_queue.job_id("a"))
    assert job_id == job_queue.job_id("a")
    assert await job_status(job_id) is JobStatus.QUEUED
    await _drain(job_queue)
    assert await job_status(job_id) is JobStatus.COMPLETE


async def test_enqueue_with_an_existing_job_id_is_a_no_op(job_queue: JobQueue) -> None:
    first = await enqueue("system.ping", _job_id=job_queue.job_id("a"))
    second = await enqueue("system.ping", _job_id=job_queue.job_id("a"))
    assert first == second
    pool = await core_jobs.get_jobs_pool()
    assert await pool.zcard(job_queue.name) == 1


async def test_deferred_job_reports_queued(job_queue: JobQueue) -> None:
    job_id = await enqueue("system.ping", _job_id=job_queue.job_id("a"), _defer_by=3600)
    assert await job_status(job_id) is JobStatus.QUEUED


async def test_unknown_job_id_is_not_found(job_queue: JobQueue) -> None:
    assert await job_status(job_queue.job_id("never")) is JobStatus.NOT_FOUND


async def test_failing_job_reports_failed(job_queue: JobQueue) -> None:
    job_id = await enqueue("tests.boom", _job_id=job_queue.job_id("a"))
    worker = await _drain(job_queue)
    assert worker.jobs_failed == 1
    assert await job_status(job_id) is JobStatus.FAILED


async def test_unregistered_job_name_fails_in_the_worker(job_queue: JobQueue) -> None:
    job_id = await enqueue("nobody.home", _job_id=job_queue.job_id("a"))
    await _drain(job_queue)
    assert await job_status(job_id) is JobStatus.FAILED


async def test_job_with_arguments_completes(job_queue: JobQueue) -> None:
    job_id = await enqueue("tests.slow", _job_id=job_queue.job_id("a"), seconds=0.01)
    await _drain(job_queue)
    assert await job_status(job_id) is JobStatus.COMPLETE
    pool = await core_jobs.get_jobs_pool()
    from arq.jobs import Job  # noqa: PLC0415

    info = await Job(job_id, pool, _deserializer=core_jobs.deserialize_job).result_info()
    assert info is not None
    assert info.result == "done"


async def test_job_status_reports_in_progress_while_running(job_queue: JobQueue) -> None:
    job_id = await enqueue("tests.slow", _job_id=job_queue.job_id("a"), seconds=2)
    worker = create_worker(
        WorkerSettings,
        functions=build_functions(TEST_JOBS),
        queue_name=job_queue.name,
        poll_delay=0.05,
        handle_signals=False,
    )
    task = asyncio.create_task(worker.async_run())
    try:
        async with asyncio.timeout(10):
            while await job_status(job_id) is not JobStatus.IN_PROGRESS:  # noqa: ASYNC110
                await asyncio.sleep(0.05)
        async with asyncio.timeout(10):
            while await job_status(job_id) is not JobStatus.COMPLETE:  # noqa: ASYNC110
                await asyncio.sleep(0.05)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*worker.tasks.values(), return_exceptions=True)
        await worker.pool.aclose()


# --- enqueue_after_commit ----------------------------------------------------------------------


async def test_enqueue_after_commit_enqueues_only_after_the_commit(
    db_session: AppSession, job_queue: JobQueue
) -> None:
    job_id = job_queue.job_id("a")
    enqueue_after_commit(db_session, "system.ping", _job_id=job_id)
    assert await job_status(job_id) is JobStatus.NOT_FOUND
    await db_session.commit()
    assert await job_status(job_id) is JobStatus.QUEUED


async def test_enqueue_after_commit_is_dropped_on_rollback(
    db_session: AppSession, job_queue: JobQueue
) -> None:
    job_id = job_queue.job_id("a")
    enqueue_after_commit(db_session, "system.ping", _job_id=job_id)
    await db_session.rollback()
    await db_session.commit()
    assert await job_status(job_id) is JobStatus.NOT_FOUND
