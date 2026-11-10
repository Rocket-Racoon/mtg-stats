"""Test helpers for background jobs (T-1.9.1.3).

``run_job_in_process`` calls a job function directly with a fake arq ``ctx``: no Redis, no worker.
"""

import dataclasses
from collections.abc import Mapping
from typing import Any

from app.core.jobs import JobFunction
from app.workers.registry import JOBS


async def run_job_in_process(
    job: str | JobFunction,
    *,
    ctx: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Run ``job`` (a registered name like ``"system.ping"``, or a function); return its result.

    The fake ``ctx`` carries what arq puts in a real one (``job_id``, ``job_try``); pass ``ctx``
    to add or override entries, for example a ``redis`` stub.
    """
    function = JOBS[job] if isinstance(job, str) else job
    fake_ctx: dict[str, Any] = {"job_id": "test-job", "job_try": 1, **(ctx or {})}
    return await function(fake_ctx, **kwargs)


@dataclasses.dataclass(frozen=True)
class JobQueue:
    """A private arq queue on the test Redis; ``token`` prefixes every job ID made through it."""

    name: str
    token: str

    def job_id(self, suffix: str) -> str:
        """Return a job ID that :func:`job_queue` cleanup will find."""
        return f"{self.token}-{suffix}"
