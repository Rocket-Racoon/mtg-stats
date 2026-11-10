"""Liveness and readiness logic."""

import asyncio
import logging
import re
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


_USERINFO = re.compile(r"(?<=//)[^/@\s]*@")
_MAX_DETAIL = 200


def _describe(exc: Exception) -> str:
    """A short, credential-free description of a failed check for the log."""
    if isinstance(exc, TimeoutError):
        return f"timed out after {CHECK_TIMEOUT_SECONDS:g} s"
    detail = _USERINFO.sub("***@", str(exc)).replace(chr(10), " ")[:_MAX_DETAIL]
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


async def _run(name: str, check: Check) -> CheckStatus:
    """Run one check; any failure or timeout is ``"error"`` (never raises)."""
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await check()
    except Exception as exc:
        logger.warning("health check %s failed: %s", name, _describe(exc))
        return "error"
    return "ok"


async def readiness(version: str) -> ReadyResponse:
    """Run the database and Redis checks concurrently, each bounded by ``CHECK_TIMEOUT_SECONDS``.

    The call takes at most about that long even if both dependencies hang. Tests replace the
    private ``_check_database`` and ``_check_redis`` functions.
    """
    database, redis = await asyncio.gather(
        _run("database", _check_database), _run("redis", _check_redis)
    )
    status: CheckStatus = "ok" if database == redis == "ok" else "error"
    return ReadyResponse(
        status=status, checks=ReadyChecks(database=database, redis=redis), version=version
    )
