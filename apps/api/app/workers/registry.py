"""The job registry: canonical ``<owner>.<job>`` name -> function (architecture §7).

Functions come only from a domain's ``services`` (or ``app.workflows``), never from a domain's
models or repository. Add a job by exporting it from the owner's ``services/__init__.py`` and
adding one line to :data:`JOBS`.

Jobs are idempotent: a job interrupted by shutdown is run again (up to ``max_tries``).
"""

import functools
from collections.abc import Mapping
from typing import Any, cast

from arq.worker import Function, func

from app.core.jobs import JobFunction
from app.core.logging import log_context
from app.system import services as system_services

JOBS: Mapping[str, JobFunction] = {
    "system.ping": system_services.ping,
}


def _with_log_context(name: str, job: JobFunction) -> JobFunction:
    """Run ``job`` inside ``log_context(job_id=..., job=name)`` so its log lines carry both."""

    @functools.wraps(job)
    async def run(ctx: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        with log_context(job_id=ctx.get("job_id"), job=name):
            return await job(ctx, *args, **kwargs)

    return run


def build_functions(extra: Mapping[str, JobFunction] | None = None) -> list[Function]:
    """Return the arq functions for :data:`JOBS` plus ``extra`` (tests register theirs here)."""
    merged = {**JOBS, **(extra or {})}
    return [
        func(cast(Any, _with_log_context(name, job)), name=name) for name, job in merged.items()
    ]
