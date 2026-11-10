"""Worker entry point (S-1.9.1): ``python -m app.workers``.

arq's own runner builds the event loop itself. psycopg's async mode cannot run on Windows'
default Proactor loop, so this runner starts the worker inside ``asyncio.run`` with a
``SelectorEventLoop`` on win32 (the same choice as ``migrations/env.py``). Windows loops cannot
register signal handlers, so there Ctrl+C / Ctrl+Break are routed to arq's graceful-shutdown
handler by hand.

The worker never imports ``app.main``.
"""

import asyncio
import logging
import signal
import sys
from collections.abc import Mapping
from typing import Any, cast

from arq.worker import Worker, create_worker

from app.core.config import get_settings
from app.core.jobs import JobFunction
from app.core.logging import configure_logging
from app.workers.registry import JobSpec, build_functions
from app.workers.settings import WorkerSettings

logger = logging.getLogger(__name__)


def _install_windows_signal_handlers(worker: Worker, loop: asyncio.AbstractEventLoop) -> None:
    if sys.platform != "win32":  # also narrows signal.SIGBREAK for mypy on other platforms
        return

    def handler(signum: int, frame: object) -> None:
        loop.call_soon_threadsafe(worker.handle_sig_wait_for_completion, signal.Signals(signum))

    for sig in (signal.SIGINT, signal.SIGBREAK, signal.SIGTERM):
        signal.signal(sig, handler)


async def serve(extra_jobs: Mapping[str, JobSpec | JobFunction] | None = None) -> None:
    """Run one worker until it is stopped by a signal (or its Redis pool fails)."""
    overrides: dict[str, Any] = {}
    if extra_jobs:
        overrides["functions"] = build_functions(extra_jobs)
    worker = create_worker(cast(Any, WorkerSettings), **overrides)
    redis = WorkerSettings.redis_settings  # host, port and database only: never user or password
    logger.info(
        "worker connecting to redis host=%s port=%s db=%s queue=%s",
        redis.host,
        redis.port,
        redis.database,
        worker.queue_name,
    )
    logger.info(
        "registered jobs (%d): %s", len(worker.functions), ", ".join(sorted(worker.functions))
    )
    if sys.platform == "win32":
        _install_windows_signal_handlers(worker, asyncio.get_running_loop())
    try:
        await worker.async_run()
    except asyncio.CancelledError:
        pass  # arq cancels its main task on shutdown; that is the normal way out
    finally:
        await worker.close()


def run_worker_process(extra_jobs: Mapping[str, JobSpec | JobFunction] | None = None) -> None:
    """Configure logging and run the worker; ``extra_jobs`` is for tests only."""
    settings = get_settings()
    configure_logging(settings.log_level)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(serve(extra_jobs), loop_factory=loop_factory)
