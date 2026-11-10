"""Test-only worker entry point: the real worker plus ``tests.slow`` and ``tests.boom`` jobs.

Run as ``python -m tests.slow_worker`` by ``test_worker_e2e.py``. These jobs are never part of
the production registry.
"""

import asyncio
from typing import Any


async def slow(ctx: dict[str, Any], seconds: float) -> str:
    """Sleep for ``seconds`` and return ``"done"``."""
    await asyncio.sleep(seconds)
    return "done"


async def boom(ctx: dict[str, Any]) -> None:
    """Always fail."""
    raise RuntimeError("boom")


TEST_JOBS = {"tests.slow": slow, "tests.boom": boom}

if __name__ == "__main__":
    from app.workers.runner import run_worker_process

    run_worker_process(TEST_JOBS)
