"""Test helpers for background jobs (T-1.9.1.3).

``run_job_in_process`` calls a job function directly with a fake arq ``ctx``: no Redis, no worker.
"""

import dataclasses
from collections.abc import Mapping
from typing import Any

from arq.worker import Worker, get_kwargs

from app.core.jobs import JobFunction, deserialize_job, serialize_job
from app.workers.registry import JOBS, build_functions, wrap_job
from app.workers.schedule import build_cron_jobs
from app.workers.settings import WorkerSettings


async def run_job_in_process(
    job: str | JobFunction,
    *,
    ctx: Mapping[str, Any] | None = None,
    wrapped: bool = False,
    **kwargs: Any,
) -> Any:
    """Run ``job`` (a registered name like ``"system.ping"``, or a function); return its result.

    It runs the bare function, without the S-1.9.2 rules (timeout, retry, log line); pass
    ``wrapped=True`` with a registered name to run it through ``wrap_job`` instead.

    The fake ``ctx`` carries what arq puts in a real one (``job_id``, ``job_try``); pass ``ctx``
    to add or override entries, for example a ``redis`` stub.

    Arguments and the result go through the worker's JSON serializer, as they do on Redis, so a
    non-JSON argument raises ``TypeError`` and a result comes back as JSON (a tuple becomes a
    list). A non-JSON result raises ``TypeError`` too.
    """
    if isinstance(job, str):
        function = wrap_job(job, JOBS[job]) if wrapped else JOBS[job].fn
    else:
        function = job
    fake_ctx: dict[str, Any] = {"job_id": "test-job", "job_try": 1, **(ctx or {})}
    wire_kwargs: dict[str, Any] = deserialize_job(serialize_job({"kwargs": kwargs}))["kwargs"]
    result = await function(fake_ctx, **wire_kwargs)
    wire_result: dict[str, Any] = deserialize_job(serialize_job({"result": result}))
    return wire_result["result"]


@dataclasses.dataclass(frozen=True)
class JobQueue:
    """A private arq queue on the test Redis; ``token`` prefixes every job ID made through it."""

    name: str
    token: str

    def job_id(self, suffix: str) -> str:
        """Return a job ID that :func:`job_queue` cleanup will find."""
        return f"{self.token}-{suffix}"


async def delete_queue_keys(pool: Any, queue: JobQueue) -> None:
    """Delete only the keys that belong to ``queue`` (never the whole database).

    Covers the queue's sorted set, its health check, the keys of every job still queued in it, and
    every ``arq:*:<token>-*`` key made through :meth:`JobQueue.job_id`.
    """
    queued = [m.decode() for m in await pool.zrange(queue.name, 0, -1)]
    keys = [queue.name, f"{queue.name}:health-check"]
    for job_id in queued:
        keys += [f"arq:{kind}:{job_id}" for kind in ("job", "result", "in-progress", "retry")]
    keys += [k async for k in pool.scan_iter(match=f"arq:*:{queue.token}-*")]
    await pool.delete(*keys)


class DrainWorker(Worker):
    """A burst worker that runs what is queued and never schedules cron occurrences itself.

    arq's ``heart_beat`` calls ``run_cron`` with the real wall clock on every poll, so a drain
    that straddles a schedule boundary (for example :00 or :05 for the heartbeat) enqueues and
    runs one extra genuine occurrence and inflates ``jobs_complete``. Occurrences in these tests
    are enqueued explicitly, with a controlled ``now``, so the drain must not add its own. The
    cron jobs stay registered, so the queued occurrence still runs with its cron options.
    """

    async def run_cron(self, n: Any, delay: float, num_windows: int = 2) -> None:
        """Skip scheduling; only an explicit call by a test would reach here, and it is a no-op."""
        return


def make_drain_worker(queue_name: str, settings: Any) -> DrainWorker:
    """Build a :class:`DrainWorker` on ``queue_name`` with the cron jobs of ``settings``."""
    kwargs: dict[str, Any] = {
        "functions": build_functions(),
        "cron_jobs": build_cron_jobs(settings),
        "queue_name": queue_name,
        "burst": True,
        "poll_delay": 0.05,
        "handle_signals": False,
    }
    return DrainWorker(**{**get_kwargs(WorkerSettings), **kwargs})
