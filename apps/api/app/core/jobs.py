"""Enqueue background jobs by name and read their status (S-1.9.1, architecture §3 and §7).

Services never import a job function. They call :func:`enqueue` (or :func:`enqueue_after_commit`
inside a transaction) with the canonical ``<owner>.<job>`` name, and the arq worker in
``app.workers`` resolves the name to a function.

Design notes:

- **Names are checked for shape, not for existence.** ``core`` may not import ``workers`` or a
  domain, so it cannot know the registry. :func:`enqueue` rejects names that are not
  ``<owner>.<job>`` (a typo such as ``"ping"`` fails fast); an unknown but well-formed name is
  enqueued and the worker records the job as failed ("function not found"). A test in
  ``tests/test_jobs.py`` keeps the registry and architecture §7 honest.
- **JSON on the wire.** Arguments and results are JSON, not pickle, so Redis content is never
  executed. Pass IDs as strings; a UUID or datetime argument raises ``TypeError`` at enqueue time
  (for :func:`enqueue_after_commit`, when it is called, in the caller's transaction).
  Exceptions are stored as ``{"error_type", "error"}`` so a failed job can be told apart.
- **Results are kept for** :data:`KEEP_RESULT_SECONDS` (one day). That is the window in which
  :func:`job_status` can report ``complete`` or ``failed``; afterwards the ID is ``not_found``.
  The durable record is the ``job_runs`` table (S-1.9.4).
- **Deterministic job IDs de-duplicate (S-1.9.2).** Build one with :func:`job_id`, for example
  ``job_id("workflows.scryfall_daily_sync", "2026-11-07")``. While a job with that ID is queued
  or running, a second :func:`enqueue` is a no-op and returns the same ID (arq enqueues nothing
  when the job or result key exists). The ID stays blocked after the run finishes for as long as
  the result is kept (:data:`KEEP_RESULT_SECONDS`, 24 h): that is deliberate, it stops a second
  daily sync on the same day. A manual re-run uses a different ID (for example a ``manual-<n>``
  part).
- **Timeouts, retries and errors (S-1.9.2).** Every job runs inside the wrapper in
  ``app.workers.registry``. A job that outlives its timeout (:data:`DEFAULT_TIMEOUT_SECONDS`,
  10 minutes, unless its registry entry sets one) is cancelled and failed, never retried. A job
  raising :class:`RetryableJobError` is retried up to :data:`MAX_RETRIES` times (so at most 4
  attempts) after the delays in :data:`RETRY_DELAYS_SECONDS`. Any other exception, including
  :class:`NonRetryableJobError`, fails the job at once. Services raise these classes; they never
  import ``workers``.
"""

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import timedelta
from enum import StrEnum
from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool
from arq.jobs import Job, SerializationError
from arq.jobs import JobStatus as ArqJobStatus
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import after_commit

logger = logging.getLogger(__name__)

KEEP_RESULT_SECONDS = 24 * 3600
DEFAULT_TIMEOUT_SECONDS = 600
MAX_RETRIES = 3
RETRY_DELAYS_SECONDS = (10, 60, 300)
"""Delay before retry 1, 2 and 3 (a later retry reuses the last entry): growing, fixed."""
JOB_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

JobFunction = Callable[..., Awaitable[Any]]
"""A job: ``async def job(ctx: dict[str, Any], **kwargs) -> JSON-serializable``."""


class RetryableJobError(Exception):
    """Raise (or wrap a transient error in) this to have the job retried with growing delays."""


class NonRetryableJobError(Exception):
    """Raise this to fail the job at once. Any other exception is non-retryable too."""


def job_id(name: str, *parts: str) -> str:
    """Return the deterministic job ID ``"<name>:<part>:..."``, for example
    ``job_id("workflows.scryfall_daily_sync", "2026-11-07")``.

    Pass it as ``_job_id`` to :func:`enqueue`: while a job with this ID is queued, running or
    still has its stored result (24 h), a second enqueue does not start another run.

    Raises:
        ValueError: ``name`` is not ``<owner>.<job>``, or a part is empty or contains ``":"``.
        TypeError: a part is not a string.
    """
    _check_name(name)
    for part in parts:
        if not isinstance(part, str):
            raise TypeError(f"job id parts must be strings, got {type(part).__name__}")
        if not part or ":" in part:
            raise ValueError(f"invalid job id part {part!r}: must be non-empty and contain no ':'")
    return ":".join((name, *parts))


class JobStatus(StrEnum):
    """Stable job states returned by :func:`job_status`."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"
    NOT_FOUND = "not_found"


def _encode_unknown(value: object) -> dict[str, str]:
    if isinstance(value, BaseException):
        return {"error_type": type(value).__name__, "error": str(value)}
    raise TypeError(f"{type(value).__name__} is not JSON serializable; pass plain JSON values")


def serialize_job(data: dict[str, Any]) -> bytes:
    """arq serializer: strict JSON, with exceptions (failed results) rendered as a dict."""
    return json.dumps(data, default=_encode_unknown, separators=(",", ":")).encode()


def deserialize_job(raw: bytes) -> dict[str, Any]:
    """arq deserializer matching :func:`serialize_job`."""
    loaded: dict[str, Any] = json.loads(raw)
    return loaded


_pool: ArqRedis | None = None
_pool_loop: asyncio.AbstractEventLoop | None = None
_pool_lock: asyncio.Lock | None = None
_pool_lock_loop: asyncio.AbstractEventLoop | None = None


async def get_jobs_pool() -> ArqRedis:
    """Return the process-wide arq Redis pool, created on first use for the running loop.

    A pool made on a different (for example closed) loop is dropped and rebuilt.
    """
    global _pool, _pool_loop, _pool_lock, _pool_lock_loop  # noqa: PLW0603
    loop = asyncio.get_running_loop()
    if _pool is not None and _pool_loop is loop:
        return _pool
    if _pool_lock is None or _pool_lock_loop is not loop:  # no await: no race creating the lock
        _pool_lock, _pool_lock_loop = asyncio.Lock(), loop
    async with _pool_lock:  # two concurrent first callers must not each create a pool
        if _pool is None or _pool_loop is not loop:
            if _pool is not None:
                # Made on another loop (usually closed): its connections cannot be reused here.
                # Try to close it anyway so sockets are not leaked; failure is expected and fine.
                stale, _pool = _pool, None
                with contextlib.suppress(Exception):
                    await stale.aclose()
            settings = get_settings()
            redis_settings = RedisSettings.from_dsn(settings.redis_url)
            redis_settings.conn_retries = 1  # fail fast on the request path
            _pool = await create_pool(
                redis_settings,
                job_serializer=serialize_job,
                job_deserializer=deserialize_job,
                default_queue_name=settings.worker_queue_name,
            )
            _pool_loop = loop
        return _pool


async def close_jobs_pool() -> None:
    """Close the pool if it exists, and forget it (app shutdown, test cleanup)."""
    global _pool, _pool_loop
    loop = asyncio.get_running_loop()
    if _pool_lock is not None and _pool_lock_loop is loop:
        # Wait for a concurrent get_jobs_pool() so its pool is not stored after we close.
        async with _pool_lock:
            pool, _pool, _pool_loop = _pool, None, None
    else:
        pool, _pool, _pool_loop = _pool, None, None
    if pool is not None:
        await pool.aclose()


def _check_name(job_name: str) -> None:
    if not JOB_NAME_PATTERN.fullmatch(job_name):
        raise ValueError(f"invalid job name {job_name!r}; expected '<owner>.<job>'")


async def enqueue(
    job_name: str,
    /,
    *,
    _job_id: str | None = None,
    _defer_by: float | timedelta | None = None,
    **kwargs: Any,
) -> str:
    """Enqueue the job registered as ``job_name`` and return its job ID.

    Args:
        job_name: canonical ``<owner>.<job>`` name (architecture §7).
        _job_id: optional fixed ID. arq enqueues nothing while the ID is queued, running, or
            finished within the last :data:`KEEP_RESULT_SECONDS` (a stored result also blocks it);
            the call then returns the same ID without enqueueing. A completed or failed job
            therefore cannot be re-enqueued under its ID for 24 h. Build it with :func:`job_id`.
        _defer_by: delay before the job may start.
        **kwargs: JSON-serializable arguments for the job.

    Raises:
        ValueError: ``job_name`` is not of the form ``<owner>.<job>``.
        TypeError: an argument is not JSON-serializable.
    """
    _check_name(job_name)
    pool = await get_jobs_pool()
    try:
        job = await pool.enqueue_job(job_name, _job_id=_job_id, _defer_by=_defer_by, **kwargs)
    except SerializationError as exc:
        raise TypeError(
            f"arguments of {job_name} are not JSON serializable: {exc.__cause__}"
        ) from exc
    if job is not None:
        return job.job_id
    if _job_id is None:  # pragma: no cover  (arq only returns None for an existing ID)
        raise RuntimeError(f"arq refused to enqueue {job_name}")
    logger.info(
        "job %s not enqueued: a job with id %s is queued, running, or finished within the "
        "last 24 h",
        job_name,
        _job_id,
    )
    return _job_id


def enqueue_after_commit(
    session: AsyncSession, job_name: str, /, *, _job_id: str | None = None, **kwargs: Any
) -> None:
    """Enqueue ``job_name`` once ``session`` has committed; drop it if the transaction rolls back.

    Built on :func:`app.core.db.after_commit`, so it needs an ``AppSession``. The name is
    validated now, and so are the arguments (same JSON serializer as :func:`enqueue`), so a typo or
    a UUID argument fails in the caller's transaction instead of after the commit. The
    callbacks are scoped to the savepoint they were registered in: a job registered inside a
    rolled-back ``begin_nested()`` is dropped. If Redis is down at that moment the
    failure is logged and the job is lost; the transaction is already committed.
    """
    _check_name(job_name)
    try:
        # JSON round trip: a private copy, so the caller mutating its arguments after this call
        # cannot change (or break) the enqueue that runs after the commit.
        frozen_kwargs: dict[str, Any] = deserialize_job(serialize_job({"kwargs": kwargs}))["kwargs"]
    except TypeError as exc:
        raise TypeError(f"arguments of {job_name} are not JSON serializable: {exc}") from exc

    async def _enqueue() -> None:
        try:
            await enqueue(job_name, _job_id=_job_id, **frozen_kwargs)
        except Exception as exc:
            # app.core.db logs the raised error and carries on; no argument values in the message.
            id_part = f" (job id {_job_id})" if _job_id is not None else ""
            raise RuntimeError(
                f"enqueue of {job_name}{id_part} failed after commit: {type(exc).__name__}"
            ) from exc

    after_commit(session, _enqueue)


async def job_status(job_id: str) -> JobStatus:
    """Return the state of a job by ID.

    ``queued`` covers deferred jobs and jobs waiting for a retry; ``failed`` means the job
    finished with an exception (or ran out of tries); ``not_found`` means the ID was never seen or
    its result expired after :data:`KEEP_RESULT_SECONDS`.
    """
    pool = await get_jobs_pool()
    queue = get_settings().worker_queue_name
    job = Job(job_id, pool, _queue_name=queue, _deserializer=deserialize_job)
    status = await job.status()
    if status is ArqJobStatus.complete:
        info = await job.result_info()
        return JobStatus.COMPLETE if info is None or info.success else JobStatus.FAILED
    return {
        ArqJobStatus.queued: JobStatus.QUEUED,
        ArqJobStatus.deferred: JobStatus.QUEUED,
        ArqJobStatus.in_progress: JobStatus.IN_PROGRESS,
        ArqJobStatus.not_found: JobStatus.NOT_FOUND,
    }[status]
