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
``max_retries + 2``: one spare try, so that a job interrupted by shutdown during its last
attempt is still run again instead of being failed as "max retries exceeded".
"""

import asyncio
import dataclasses
import functools
import logging
import time
from collections.abc import Mapping
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
from app.system import services as system_services

logger = logging.getLogger(__name__)

ARQ_TIMEOUT_MARGIN_SECONDS = 5
"""arq's own timeout is the wrapper's plus this, a backstop that should never fire."""


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
    "system.ping": JobSpec(system_services.ping),
}


def _log_end(outcome: str, job_try: int, started: float, *, warning: bool = False) -> None:
    logger.log(
        logging.WARNING if warning else logging.INFO,
        "job finished: %s",
        outcome,
        extra={
            "try": job_try,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "outcome": outcome,
        },
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
    - :class:`RetryableJobError` becomes ``arq.Retry`` with a growing delay while retries remain.
    """

    @functools.wraps(spec.fn)
    async def run(ctx: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        job_try = int(ctx.get("job_try", 1))
        started = time.monotonic()
        with log_context(job_id=ctx.get("job_id"), job=name):
            cm = asyncio.timeout(spec.timeout)
            try:
                async with cm:
                    result = await spec.fn(ctx, *args, **kwargs)
            except asyncio.CancelledError:
                _log_end("cancelled", job_try, started, warning=True)
                raise
            except Retry:  # the job asked arq for a retry itself
                _log_end("retry", job_try, started, warning=True)
                raise
            except RetryableJobError as exc:
                if job_try <= spec.max_retries:
                    _log_end("retry", job_try, started, warning=True)
                    raise Retry(defer=retry_delay(job_try)) from exc
                _log_end("failed", job_try, started, warning=True)
                raise
            except Exception as exc:
                timed_out = isinstance(exc, TimeoutError) and cm.expired()
                _log_end("timeout" if timed_out else "failed", job_try, started, warning=True)
                raise
            _log_end("success", job_try, started)
            return result

    return run


def _normalize(job: JobSpec | JobFunction) -> JobSpec:
    return job if isinstance(job, JobSpec) else JobSpec(job)


def build_functions(extra: Mapping[str, JobSpec | JobFunction] | None = None) -> list[Function]:
    """Return the arq functions for :data:`JOBS` plus ``extra`` (tests register theirs here)."""
    merged = {**JOBS, **{n: _normalize(j) for n, j in (extra or {}).items()}}
    return [
        func(
            cast(Any, wrap_job(name, spec)),
            name=name,
            timeout=spec.timeout + ARQ_TIMEOUT_MARGIN_SECONDS,
            max_tries=spec.max_retries + 2,
        )
        for name, spec in merged.items()
    ]
