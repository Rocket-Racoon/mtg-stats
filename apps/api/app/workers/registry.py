"""The job registry: canonical ``<owner>.<job>`` name -> :class:`JobSpec` (architecture §7).

Functions come only from a domain's ``services`` (or ``app.workflows``), never from a domain's
models or repository. Add a job by exporting it from the owner's ``services/__init__.py`` and
adding one line to :data:`JOBS`: ``"owner.job": JobSpec(services.job)``. A job that needs more
than the default 10 minutes, or a different retry budget, says so there:
``JobSpec(services.job, timeout=3600, max_retries=1)``.

Every job runs inside :func:`wrap_job` (S-1.9.2), which enforces the timeout, turns
:class:`~app.core.jobs.RetryableJobError` into an arq retry and writes the one log line per
attempt. Jobs are idempotent: a job interrupted by shutdown is run again.

Retries and shutdown: arq counts every start of a job (``job_try``), including a re-run after
shutdown or a crash. The wrapper retries a :class:`RetryableJobError` while ``job_try <=
max_retries``, so a shutdown re-run uses up one of the job's retries. arq's own ``max_tries`` is
``max_retries + 1 + SHUTDOWN_SPARE_TRIES``: shutdown re-runs consume the retries first and
then the spare tries, so that a job interrupted by shutdown during its last attempt is still
run again instead of being failed as "max retries exceeded".
"""

import asyncio
import dataclasses
import functools
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from arq.worker import Function, Retry, func

from app.core.jobs import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_RETRIES,
    RETRY_DELAYS_SECONDS,
    JobFunction,
    RetryableJobError,
)
from app.core.logging import log_context
from app.identity import services as identity_services
from app.system import services as system_services

logger = logging.getLogger(__name__)

ARQ_TIMEOUT_MARGIN_SECONDS = 5
"""arq's own timeout is the wrapper's plus this, a backstop that should never fire."""
SHUTDOWN_SPARE_TRIES = 3
"""Extra arq tries beyond ``1 + max_retries`` for re-runs after shutdown or a crash."""
RECORD_TIMEOUT_SECONDS = 3
"""Longest the wrapper waits for the ``job_runs`` write; past it the run goes unrecorded.

Must stay below ``ARQ_TIMEOUT_MARGIN_SECONDS``: a job that ends at the very end of its budget
then still finishes recording before arq's backstop timeout could cancel the wrapper.
"""


@dataclasses.dataclass(frozen=True)
class JobSpec:
    """A registered job: its function plus its own timeout and retry budget."""

    fn: JobFunction
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = MAX_RETRIES


def retry_delay(job_try: int) -> int:
    """Seconds to wait before the retry that follows attempt number ``job_try`` (1-based)."""
    return RETRY_DELAYS_SECONDS[min(max(job_try, 1), len(RETRY_DELAYS_SECONDS)) - 1]


JOBS: Mapping[str, JobSpec] = {
    "identity.cleanup_refresh_tokens": JobSpec(identity_services.cleanup_refresh_tokens_job),
    "system.heartbeat": JobSpec(system_services.heartbeat),
    "system.ping": JobSpec(system_services.ping),
    "system.purge_job_runs": JobSpec(system_services.purge_job_runs_job),
}


def _log_end(outcome: str, job_try: int, started: float, *, warning: bool = False) -> None:
    logger.log(
        logging.WARNING if warning else logging.INFO,
        "job finished: %s",
        outcome,
        extra={
            "try": job_try,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "outcome": outcome,
        },
    )


def _error_text(exc: BaseException | None, fallback: str = "") -> str:
    if exc is None:
        return fallback
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


async def _end(
    name: str,
    ctx: dict[str, Any],
    outcome: str,
    job_try: int,
    started: float,
    *,
    started_at: datetime,
    error: str = "",
    warning: bool = False,
) -> None:
    """Log the attempt's one line and record it in ``job_runs`` (S-1.9.4).

    Recording is best effort: any failure (including a hung database) is logged and swallowed,
    so it never fails the job nor hides the job's own exception.
    """
    _log_end(outcome, job_try, started, warning=warning)
    try:
        await asyncio.wait_for(
            system_services.record_job_run(
                job_name=name,
                job_id=ctx.get("job_id"),
                started_at=started_at,
                finished_at=datetime.now(UTC),
                status=outcome,
                error=error,
            ),
            RECORD_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # No traceback: it is huge for a SQL error, and the job's own outcome is already logged.
        logger.error(
            "could not record the job run in job_runs: %s: %s",
            type(exc).__name__,
            str(exc)[:200],
            extra={"outcome": outcome},
        )


def wrap_job(name: str, spec: JobSpec) -> JobFunction:
    """Return ``spec.fn`` wrapped with the S-1.9.2 rules; reusable for cron jobs.

    - Log context ``job`` and ``job_id``, and exactly one line per attempt with ``try``,
      ``duration_ms`` and ``outcome`` (``success``, ``retry``, ``failed``, ``timeout`` or
      ``cancelled``).
    - The timeout is the wrapper's own ``asyncio.timeout`` (arq's is a few seconds longer, as a
      backstop). arq enforces timeouts by cancelling the task, which looks the same as a
      shutdown cancellation; with our own timeout the two differ: ``cm.expired()`` is true
      only for the timeout. A timeout raises ``TimeoutError`` (failed, never retried). A
      shutdown ``CancelledError`` is logged as ``cancelled`` and re-raised, so arq leaves the
      job queued for the next worker.
    - :class:`RetryableJobError` becomes ``arq.Retry`` with a growing delay while retries remain
      (``job_try <= max_retries``). Jobs raise :class:`RetryableJobError`, never ``arq.Retry``; if
      one does anyway it obeys the same budget.
    - A job that has timed out is always failed and logged ``timeout``, even if it swallowed the
      cancellation, returned, or raised a retryable error from its cancel handler.
    - Every attempt, whatever its outcome, is recorded in ``job_runs`` (S-1.9.4) with the same
      outcome word, start and end time and an error summary (at most 2 KB). Recording happens
      after the outcome is decided, is best effort (a failure is logged, never raised) and so
      can neither fail the job nor replace its exception.
    """

    @functools.wraps(spec.fn)
    async def run(ctx: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        job_try = int(ctx.get("job_try", 1))
        started = time.perf_counter()
        started_at = datetime.now(UTC)
        with log_context(job_id=ctx.get("job_id"), job=name):

            async def end(outcome: str, error: str = "", *, warning: bool = True) -> None:
                await _end(
                    name,
                    ctx,
                    outcome,
                    job_try,
                    started,
                    started_at=started_at,
                    error=error,
                    warning=warning,
                )

            cm = asyncio.timeout(spec.timeout)
            try:
                async with cm:
                    result = await spec.fn(ctx, *args, **kwargs)
            except asyncio.CancelledError as exc:
                if cm.expired():  # our timeout fired and a later cancel (arq's backstop) arrived
                    await end("timeout", f"timed out after {spec.timeout:g} s")
                    raise TimeoutError(f"job {name} timed out") from exc
                await end("cancelled", "cancelled by worker shutdown")
                raise
            except (Retry, RetryableJobError) as exc:
                if cm.expired():  # a job that turns its cancellation into a retry still timed out
                    await end("timeout", f"timed out after {spec.timeout:g} s")
                    raise TimeoutError(f"job {name} timed out") from exc
                if job_try <= spec.max_retries:
                    await end("retry", _error_text(exc, "retry requested"))
                    if isinstance(exc, Retry):
                        raise
                    raise Retry(defer=retry_delay(job_try)) from exc
                await end("failed", _error_text(exc, "retry requested, out of retries"))
                if isinstance(exc, Retry):  # never hand arq a Retry we have no budget for
                    raise RetryableJobError(f"job {name} out of retries") from exc
                raise
            except Exception as exc:
                timed_out = isinstance(exc, TimeoutError) and cm.expired()
                if timed_out:
                    await end("timeout", f"timed out after {spec.timeout:g} s")
                else:
                    await end("failed", _error_text(exc))
                raise
            if cm.expired():  # the job swallowed the cancellation and returned
                await end("timeout", f"timed out after {spec.timeout:g} s")
                raise TimeoutError(f"job {name} timed out")
            await end("success", warning=False)
            return result

    return run


def arq_options(spec: JobSpec) -> dict[str, Any]:
    """Keyword arguments for ``arq.worker.func`` / ``arq.cron.cron`` that match ``spec``.

    arq's cron defaults are ``max_tries=1`` and ``timeout=None``, so cron jobs must pass these.
    arq keeps a cron job's in-progress key for 60 s, so a cron retry delay is effectively >= 60 s.
    """
    return {
        "timeout": spec.timeout + ARQ_TIMEOUT_MARGIN_SECONDS,
        "max_tries": spec.max_retries + 1 + SHUTDOWN_SPARE_TRIES,
    }


def _normalize(job: JobSpec | JobFunction) -> JobSpec:
    return job if isinstance(job, JobSpec) else JobSpec(job)


def build_functions(extra: Mapping[str, JobSpec | JobFunction] | None = None) -> list[Function]:
    """Return the arq functions for :data:`JOBS` plus ``extra`` (tests register theirs here)."""
    merged = {**JOBS, **{n: _normalize(j) for n, j in (extra or {}).items()}}
    return [
        func(
            cast(Any, wrap_job(name, spec)),
            name=name,
            **arq_options(spec),
        )
        for name, spec in merged.items()
    ]
