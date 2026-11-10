"""End-to-end worker tests (S-1.9.1 AC1-AC3): the real worker as a subprocess against Redis.

Signals: on POSIX the test sends SIGTERM; on Windows it sends CTRL_BREAK_EVENT to a new process
group, which ``app.workers.runner`` maps to the same graceful-shutdown handler. Windows has no
real SIGTERM delivery, so that is the closest equivalent.
"""

import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.core.config import get_settings
from app.core.jobs import JobStatus, enqueue, job_status
from tests.jobs import JobQueue

API_ROOT = Path(__file__).resolve().parents[1]
WIN = sys.platform == "win32"
SECRET = "s3cr3t-pw-Zq9"


def _start_worker(
    queue: JobQueue, grace: int, extra_env: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    env = {
        **os.environ,
        **(extra_env or {}),
        "WORKER_QUEUE_NAME": queue.name,
        "WORKER_SHUTDOWN_GRACE_SECONDS": str(grace),
        "PYTHONUNBUFFERED": "1",
    }
    return subprocess.Popen(
        [sys.executable, "-m", "tests.slow_worker"],
        cwd=API_ROOT,
        env=env,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if WIN else 0,
    )


def _terminate(proc: subprocess.Popen[str]) -> None:
    if WIN:
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGTERM)


async def _wait_for(job_id: str, wanted: JobStatus, limit: float = 90) -> None:
    async with asyncio.timeout(limit):
        while await job_status(job_id) is not wanted:  # noqa: ASYNC110
            await asyncio.sleep(0.1)


@pytest.fixture
async def worker_proc(job_queue: JobQueue) -> AsyncIterator[subprocess.Popen[str]]:
    """Start a worker with a 30 s grace; make sure it is gone afterwards."""
    proc = _start_worker(job_queue, grace=30)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate()


async def test_worker_runs_enqueued_job_and_stops_taking_jobs_on_sigterm(
    job_queue: JobQueue, worker_proc: subprocess.Popen[str]
) -> None:
    ping_id = await enqueue("system.ping", _job_id=job_queue.job_id("ping"))
    await _wait_for(ping_id, JobStatus.COMPLETE)

    slow_id = await enqueue("tests.slow", _job_id=job_queue.job_id("slow"), seconds=5)
    await _wait_for(slow_id, JobStatus.IN_PROGRESS)
    _terminate(worker_proc)
    await asyncio.sleep(1.5)  # past the Windows handler's polling delay
    late_id = await enqueue("system.ping", _job_id=job_queue.job_id("late"))

    _, stderr = await asyncio.to_thread(worker_proc.communicate, None, 40)
    assert worker_proc.returncode == 0
    assert await job_status(slow_id) is JobStatus.COMPLETE  # finished within the grace period
    assert await job_status(late_id) is JobStatus.QUEUED  # not taken after the signal
    # AC1: the startup log names every registered job.
    assert "system.ping" in stderr
    assert "tests.slow" in stderr
    assert "tests.boom" in stderr


async def test_job_still_running_after_the_grace_period_is_left_queued_for_retry(
    job_queue: JobQueue,
) -> None:
    proc = _start_worker(job_queue, grace=1)
    try:
        slow_id = await enqueue("tests.slow", _job_id=job_queue.job_id("slow"), seconds=120)
        await _wait_for(slow_id, JobStatus.IN_PROGRESS)
        _terminate(proc)
        await asyncio.to_thread(proc.communicate, None, 40)
        assert proc.returncode == 0
        assert await job_status(slow_id) is JobStatus.QUEUED
    finally:
        if proc.poll() is None:
            proc.kill()
            with suppress(Exception):
                proc.communicate()


async def _startup_stderr(job_queue: JobQueue) -> str:
    """Run a worker that connects with a password in REDIS_URL; return its whole stderr."""
    # Redis accepts any password for a user without one, so the worker connects with credentials.
    base = urlsplit(get_settings().redis_url)
    url = base._replace(netloc=f"default:{SECRET}@{base.hostname}:{base.port or 6379}").geturl()
    proc = _start_worker(job_queue, grace=5, extra_env={"REDIS_URL": url})
    try:
        ping_id = await enqueue("system.ping", _job_id=job_queue.job_id("ping"))
        await _wait_for(ping_id, JobStatus.COMPLETE)
        _terminate(proc)
        _, stderr = await asyncio.to_thread(proc.communicate, None, 40)
    finally:
        if proc.poll() is None:
            proc.kill()
            with suppress(Exception):
                proc.communicate()
    return stderr


async def test_worker_logs_never_contain_the_redis_password(job_queue: JobQueue) -> None:
    stderr = await _startup_stderr(job_queue)
    assert "system.ping" in stderr  # the worker did start and log
    assert SECRET not in stderr


async def test_worker_startup_log_names_the_redis_host_and_db(job_queue: JobQueue) -> None:
    stderr = await _startup_stderr(job_queue)
    redis = urlsplit(get_settings().redis_url)
    db = redis.path.lstrip("/") or "0"
    assert any(
        str(redis.hostname) in line and f"db={db}" in line for line in stderr.splitlines()
    ), stderr
    assert SECRET not in stderr


async def test_job_left_queued_by_a_short_grace_runs_again_on_the_next_worker(
    job_queue: JobQueue,
) -> None:
    first = _start_worker(job_queue, grace=1)
    second: subprocess.Popen[str] | None = None
    try:
        slow_id = await enqueue("tests.slow", _job_id=job_queue.job_id("slow"), seconds=4)
        await _wait_for(slow_id, JobStatus.IN_PROGRESS)
        _terminate(first)
        await asyncio.to_thread(first.communicate, None, 40)
        assert await job_status(slow_id) is JobStatus.QUEUED
        second = _start_worker(job_queue, grace=30)
        await _wait_for(slow_id, JobStatus.COMPLETE, limit=60)
    finally:
        for proc in (first, second):
            if proc is not None and proc.poll() is None:
                proc.kill()
                with suppress(Exception):
                    proc.communicate()
