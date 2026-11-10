"""Acceptance tests for S-1.9.1 that complement ``test_jobs.py``.

Covers the status lifecycle, result expiry, ``enqueue_after_commit`` edge cases, in-process runs
without Redis, import boundaries and test isolation.
"""

import asyncio
import logging
from collections.abc import Iterator
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
from tests.jobs import JobQueue, run_job_in_process
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


def test_registry_imports_only_system_services_and_core() -> None:
    registry = APP_ROOT / "workers" / "registry.py"
    targets = {target for _, target in imported_modules(APP_ROOT, registry)}
    internal = {t for t in targets if t == "app" or t.startswith("app.")}
    assert "app.system.services" in internal
    for target in internal:
        assert target == "app.system.services" or target.startswith("app.core"), target


def test_worker_package_has_no_boundary_violations() -> None:
    violations = [v for v in check_imports(APP_ROOT) if v.path.parts[1] == "workers"]
    assert violations == []


def test_registered_jobs_live_in_their_owners_services_package() -> None:
    for name, function in JOBS.items():
        owner = name.split(".")[0]
        assert function.__module__.startswith(f"app.{owner}.services"), name


# --- isolation: tests never flush Redis --------------------------------------------------------

_SENTINEL: dict[str, str] = {}


@pytest.fixture(scope="module")
def _sentinel_cleanup() -> Iterator[None]:
    yield
    if _SENTINEL:
        import redis  # noqa: PLC0415

        client = redis.Redis.from_url(get_settings().redis_url)
        try:
            client.delete(_SENTINEL["key"])
        finally:
            client.close()


@pytest.mark.usefixtures("_sentinel_cleanup")
async def test_isolation_step_1_plant_a_key_outside_the_job_queue_namespace(
    job_queue: JobQueue,
) -> None:
    key = f"arq:unrelated:{job_queue.token}"
    _SENTINEL["key"] = key
    pool = await core_jobs.get_jobs_pool()
    await pool.set(key, "keep me")
    await enqueue("system.ping", _job_id=job_queue.job_id("a"))


@pytest.mark.usefixtures("_sentinel_cleanup")
async def test_isolation_step_2_job_queue_cleanup_left_the_unrelated_key_alone(
    job_queue: JobQueue,
) -> None:
    """Runs after step 1 (file order): that test's fixture cleanup must not touch other keys."""
    assert "key" in _SENTINEL, "step 1 did not run first"
    pool = await core_jobs.get_jobs_pool()
    assert await pool.get(_SENTINEL["key"]) == b"keep me"


def test_no_test_or_fixture_flushes_redis() -> None:
    root = Path(__file__).resolve().parents[1]
    forbidden = ("flush" + "db", "flush" + "all")
    offenders = [
        str(path.relative_to(root))
        for path in [root / "conftest.py", *(root / "tests").rglob("*.py")]
        if any(word in path.read_text(encoding="utf-8").lower() for word in forbidden)
    ]
    assert offenders == []
