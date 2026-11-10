"""Liveness and readiness logic."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.core.db import get_sessionmaker
from app.core.redis import get_redis
from app.system import repository
from app.system.schemas import CheckStatus, LiveResponse, ReadyChecks, ReadyResponse

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 2.0

Check = Callable[[], Awaitable[object]]


def liveness() -> LiveResponse:
    """Report that the process is up. Touches neither Postgres nor Redis."""
    return LiveResponse(status="ok")


async def _check_database() -> None:
    async with get_sessionmaker()() as session:
        await repository.ping_database(session)


async def _check_redis() -> None:
    await get_redis().ping()


async def _run(name: str, check: Check, timeout_seconds: float) -> CheckStatus:
    """Run one check; any failure or timeout is ``"error"`` (never raises)."""
    try:
        async with asyncio.timeout(timeout_seconds):
            await check()
    except Exception as exc:
        logger.warning("health check %s failed: %s", name, type(exc).__name__)
        return "error"
    return "ok"


async def readiness(
    version: str,
    *,
    timeout_seconds: float = CHECK_TIMEOUT_SECONDS,
    database_check: Check | None = None,
    redis_check: Check | None = None,
) -> ReadyResponse:
    """Run the database and Redis checks concurrently, each bounded by ``timeout_seconds``.

    The call takes at most about ``timeout_seconds`` even if both dependencies hang.
    The check callables are injectable for tests.
    """
    database, redis = await asyncio.gather(
        _run("database", database_check or _check_database, timeout_seconds),
        _run("redis", redis_check or _check_redis, timeout_seconds),
    )
    status: CheckStatus = "ok" if database == redis == "ok" else "error"
    return ReadyResponse(
        status=status, checks=ReadyChecks(database=database, redis=redis), version=version
    )
