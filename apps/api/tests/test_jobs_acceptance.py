"""Acceptance tests for S-1.9.1 that complement ``test_jobs.py``.

Covers the status lifecycle, result expiry, ``enqueue_after_commit`` edge cases, in-process runs
without Redis, import boundaries and test isolation.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import pytest
from arq.jobs import Job
from arq.worker import create_worker
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs as core_jobs
from app.core.config import get_settings
from app.core.db import AppSession
from app.core.jobs import JobStatus, enqueue, enqueue_after_commit, job_status
from app.workers.registry import JOBS, build_functions
from app.workers.settings import WorkerSettings
from tests.architecture.test_domain_imports import APP_ROOT, check_imports, imported_modules
from tests.jobs import JobQueue, delete_queue_keys, run_job_in_process
from tests.slow_worker import TEST_JOBS

CLOSED_REDIS_URL = "redis://127.0.0.1:1/15"


def _worker(queue: JobQueue, **overrides: Any) -> Any:
    return create_worker(
        WorkerSettings,
        functions=build_functions(TEST_JOBS),
        queue_name=queue.name,
        poll_delay=0.05,
        handle_signals=False,
        **overrides,
    )


async def _stop(worker: Any, task: "asyncio.Task[None]") -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.gather(*worker.tasks.values(), return_exceptions=True)
    await worker.pool.aclose()


# --- AC2: status lifecycle ---------------------------------------------------------------------


async def test_one_job_goes_queued_then_in_progress_then_complete(job_queue: JobQueue) -> None:
    job_id = await enqueue("tests.slow", _job_id=job_queue.job_id("a"), seconds=1)
    seen = [await job_status(job_id)]
    worker = _worker(job_queue)
    task = asyncio.create_task(worker.async_run())
    try:
        async with asyncio.timeout(15):
            while seen[-1] is not JobStatus.COMPLETE:
                status = await job_status(job_id)
                if status is not seen[-1]:
                    seen.append(status)
                await asyncio.sleep(0.02)
    finally:
        await _stop(worker, task)
    assert seen == [JobStatus.QUEUED, JobStatus.IN_PROGRESS, JobStatus.COMPLETE]


async def test_failed_job_records_the_exception_type_and_message(job_queue: JobQueue) -> None:
    job_id = await enqueue("tests.boom", _job_id=job_queue.job_id("a"))
    worker = _worker(job_queue, burst=True)
    await asyncio.wait_for(worker.async_run(), 20)
    await worker.pool.aclose()
    pool = await core_jobs.get_jobs_pool()
    info = await Job(job_id, pool, _deserializer=core_jobs.deserialize_job).result_info()
    assert info is not None
    assert info.success is False
    assert info.result == {"error_type": "RuntimeError", "error": "boom"}
    assert await job_status(job_id) is JobStatus.FAILED


async def test_complete_status_expires_to_not_found_after_keep_result(job_queue: JobQueue) -> None:
    job_id = await enqueue("system.ping", _job_id=job_queue.job_id("a"))
    worker = _worker(job_queue, burst=True, keep_result=1)
    await asyncio.wait_for(worker.async_run(), 20)
    await worker.pool.aclose()
    assert await job_status(job_id) is JobStatus.COMPLETE
    await asyncio.sleep(1.5)
    assert await job_status(job_id) is JobStatus.NOT_FOUND


async def test_failed_status_expires_to_not_found_after_keep_result(job_queue: JobQueue) -> None:
    job_id = await enqueue("tests.boom", _job_id=job_queue.job_id("a"))
    worker = _worker(job_queue, burst=True, keep_result=1)
    await asyncio.wait_for(worker.async_run(), 20)
    await worker.pool.aclose()
    assert await job_status(job_id) is JobStatus.FAILED
    await asyncio.sleep(1.5)
    assert await job_status(job_id) is JobStatus.NOT_FOUND


def test_production_keeps_results_for_24_hours() -> None:
    assert core_jobs.KEEP_RESULT_SECONDS == 24 * 3600
    assert WorkerSettings.keep_result == 24 * 3600


async def test_non_json_argument_raises_type_error_and_nothing_is_queued(
    job_queue: JobQueue,
) -> None:
    with pytest.raises(TypeError):
        await enqueue("system.ping", _job_id=job_queue.job_id("a"), bad=object())
    pool = await core_jobs.get_jobs_pool()
    assert await pool.zcard(job_queue.name) == 0
    assert await job_status(job_queue.job_id("a")) is JobStatus.NOT_FOUND


# --- enqueue_after_commit ----------------------------------------------------------------------


async def test_enqueue_after_commit_is_not_enqueued_when_the_commit_fails(
    db_session: AppSession, job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = job_queue.job_id("a")
    enqueue_after_commit(db_session, "system.ping", _job_id=job_id)

    async def boom(self: AsyncSession) -> None:
        raise RuntimeError("commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", boom)
        with pytest.raises(RuntimeError, match="commit failed"):
            await db_session.commit()
    await db_session.commit()
    assert await job_status(job_id) is JobStatus.NOT_FOUND


async def test_enqueue_after_commit_waits_for_the_outer_commit_not_a_savepoint_release(
    db_session: AppSession, job_queue: JobQueue
) -> None:
    job_id = job_queue.job_id("a")
    async with db_session.begin_nested():
        enqueue_after_commit(db_session, "system.ping", _job_id=job_id)
    assert await job_status(job_id) is JobStatus.NOT_FOUND  # savepoint released, not committed
    await db_session.commit()
    assert await job_status(job_id) is JobStatus.QUEUED


async def test_job_registered_in_a_rolled_back_savepoint_is_dropped(
    db_session: AppSession, job_queue: JobQueue
) -> None:
    job_id = job_queue.job_id("a")
    savepoint = await db_session.begin_nested()
    enqueue_after_commit(db_session, "system.ping", _job_id=job_id)
    await savepoint.rollback()
    await db_session.commit()
    assert await job_status(job_id) is JobStatus.NOT_FOUND


async def test_enqueue_after_commit_keeps_the_arguments_as_they_were_when_called(
    db_session: AppSession, job_queue: JobQueue
) -> None:
    job_id = job_queue.job_id("mutated")
    items = ["a", "b"]
    enqueue_after_commit(db_session, "system.ping", _job_id=job_id, items=items)
    items.append("c")  # the caller reuses its list; the job must still get the original
    await db_session.commit()
    pool = await core_jobs.get_jobs_pool()
    job = Job(
        job_id,
        pool,
        _queue_name=get_settings().worker_queue_name,
        _deserializer=core_jobs.deserialize_job,
    )
    info = await job.info()
    assert info is not None
    assert info.kwargs == {"items": ["a", "b"]}


async def test_redis_failure_after_commit_is_logged_and_does_not_raise(
    db_session: AppSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("REDIS_URL", CLOSED_REDIS_URL)
    get_settings.cache_clear()
    await core_jobs.close_jobs_pool()
    try:
        await db_session.execute(text("select 1"))
        enqueue_after_commit(db_session, "system.ping", _job_id="never-queued")
        with caplog.at_level(logging.ERROR, logger="app.core.db"):
            await db_session.commit()  # must not raise
        assert any("after_commit callback failed" in r.getMessage() for r in caplog.records)
        assert any(r.exc_info for r in caplog.records)
        causes = [r.exc_info[1].__cause__ for r in caplog.records if r.exc_info and r.exc_info[1]]
        assert any(c is not None and not isinstance(c, RuntimeError) for c in causes)  # chain kept
        logged = " ".join(
            f"{r.getMessage()} {r.exc_text or ''} {r.exc_info[1] if r.exc_info else ''}"
            for r in caplog.records
        )
        assert "system.ping" in logged
        assert "never-queued" in logged
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        await core_jobs.close_jobs_pool()


# --- AC4: in-process runs, no Redis ------------------------------------------------------------


async def test_run_job_in_process_needs_no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", CLOSED_REDIS_URL)
    get_settings.cache_clear()
    await core_jobs.close_jobs_pool()

    async def no_pool() -> None:
        raise AssertionError("an in-process run must not open a Redis pool")

    monkeypatch.setattr(core_jobs, "get_jobs_pool", no_pool)

    async def raw(ctx: dict[str, Any], n: int) -> int:
        return n + 1

    try:
        assert await run_job_in_process("system.ping") == "pong"
        assert await run_job_in_process(raw, n=1) == 2
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_registry_imports_only_domain_services_and_core() -> None:
    registry = APP_ROOT / "workers" / "registry.py"
    targets = {target for _, target in imported_modules(APP_ROOT, registry)}
    internal = {t for t in targets if t == "app" or t.startswith("app.")}
    assert "app.system.services" in internal
    for target in internal:
        is_domain_services = re.fullmatch(r"app\.[a-z_]+\.services", target) is not None
        assert is_domain_services or target.startswith("app.core"), target


def test_worker_package_has_no_boundary_violations() -> None:
    violations = [v for v in check_imports(APP_ROOT) if v.path.parts[1] == "workers"]
    assert violations == []


def test_registered_jobs_live_in_their_owners_services_package() -> None:
    for name, spec in JOBS.items():
        owner = name.split(".")[0]
        assert spec.fn.__module__.startswith(f"app.{owner}.services"), name


# --- isolation: tests never flush Redis --------------------------------------------------------


async def test_queue_cleanup_deletes_only_its_own_keys(job_queue: JobQueue) -> None:
    """Cleanup of one token-scoped queue leaves another queue and unrelated keys alone."""
    other = JobQueue(
        name=f"arq:queue:test-other-{job_queue.token}", token=f"other{job_queue.token}"
    )
    unrelated = f"arq:unrelated:{job_queue.token}"
    pool = await core_jobs.get_jobs_pool()
    await pool.set(unrelated, "keep me")
    mine = await enqueue("system.ping", _job_id=job_queue.job_id("a"))
    theirs = f"arq:job:{other.job_id('a')}"
    await pool.set(theirs, "other queue's job")
    try:
        await delete_queue_keys(pool, job_queue)  # what the job_queue fixture does on teardown
        assert await pool.exists(f"arq:job:{mine}") == 0
        assert await pool.get(unrelated) == b"keep me"
        assert await pool.get(theirs) == b"other queue's job"
    finally:
        await pool.delete(unrelated, theirs)


def test_no_test_or_fixture_flushes_redis() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = ("flush" + "db", "flush" + "all")
    offenders = [
        str(path.relative_to(root))
        for path in [root / "conftest.py", *(root / "tests").rglob("*.py")]
        if any(word in path.read_text(encoding="utf-8").lower() for word in forbidden)
    ]
    assert offenders == []
