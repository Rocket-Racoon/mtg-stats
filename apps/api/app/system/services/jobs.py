"""Background jobs owned by the ``system`` domain (architecture §7)."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def ping(ctx: dict[str, Any]) -> str:
    """Smoke-test job: returns ``"pong"``. Idempotent and side-effect free.

    It gives the worker a registered job from day one and the end-to-end tests a real target.
    """
    return "pong"


async def heartbeat(ctx: dict[str, Any]) -> None:
    """Scheduled liveness signal: logs one INFO line. Idempotent and side-effect free.

    Runs every 5 minutes on the UTC clock (``app.workers.schedule``). S-1.9.4 records each run in
    ``job_runs``, which ``/health/ops`` reads to tell whether the scheduler is alive.
    """
    logger.info("heartbeat")
