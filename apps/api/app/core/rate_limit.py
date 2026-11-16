"""Redis fixed-window rate limiter (S-2.1.10).

Each counter is a Redis key that gets its TTL when first created (``INCR`` then ``EXPIRE ... NX``
in one transaction), so a window starts at the first hit and ends ``RATE_LIMIT_WINDOW_SECONDS``
later. The limiter fails open: any Redis error or timeout lets the request through and logs one
warning (the exception type only: no key, username or address).

This module knows nothing of identity. Callers build the keys with :func:`key` and decide what a
limit means. The client address is ``request.client.host``; behind a reverse proxy run uvicorn with
``--proxy-headers --forwarded-allow-ips=<proxy ip>`` so that it is the real client.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.core.redis import get_redis

logger = logging.getLogger("app.core.rate_limit")

REDIS_CALL_TIMEOUT_SECONDS = 1.0
UNKNOWN_CLIENT = "unknown"


@dataclass(frozen=True)
class Window:
    """State of one counter: its count and the seconds left in its window (at least 1)."""

    count: int
    retry_after: int


def key(*parts: str) -> str:
    """Build ``{RATE_LIMIT_KEY_PREFIX}:{part}:{part}...``."""
    return ":".join([get_settings().rate_limit_key_prefix, *parts])


def client_ip(request: Request) -> str:
    """The peer address (``request.client.host``), or ``"unknown"`` without a client."""
    return request.client.host if request.client else UNKNOWN_CLIENT


def _remaining(ttl: object, window: int) -> int:
    """Remaining seconds from a Redis TTL reply; the window length when missing or negative."""
    if not isinstance(ttl, int) or ttl < 0:
        return window
    return max(ttl, 1)


async def hit(keys: Sequence[str]) -> list[Window] | None:
    """Increment every counter once; ``None`` when Redis failed (fail open)."""
    window = get_settings().rate_limit_window_seconds
    try:
        async with asyncio.timeout(REDIS_CALL_TIMEOUT_SECONDS):
            pipe = get_redis().pipeline(transaction=True)
            for name in keys:
                pipe.incr(name)
                pipe.expire(name, window, nx=True)
                pipe.ttl(name)
            reply = await pipe.execute()
    except (RedisError, OSError, TimeoutError) as exc:
        _warn("hit", exc)
        return None
    return [
        Window(int(reply[i * 3]), _remaining(reply[i * 3 + 2], window)) for i in range(len(keys))
    ]


async def peek(keys: Sequence[str]) -> list[Window] | None:
    """Read every counter without changing it (a missing key is count 0); ``None`` on failure."""
    window = get_settings().rate_limit_window_seconds
    try:
        async with asyncio.timeout(REDIS_CALL_TIMEOUT_SECONDS):
            pipe = get_redis().pipeline(transaction=False)
            for name in keys:
                pipe.get(name)
                pipe.ttl(name)
            reply = await pipe.execute()
    except (RedisError, OSError, TimeoutError) as exc:
        _warn("peek", exc)
        return None
    return [
        Window(int(reply[i * 2] or 0), _remaining(reply[i * 2 + 1], window))
        for i in range(len(keys))
    ]


async def reset(name: str) -> None:
    """Delete one counter; a Redis failure is logged and ignored."""
    try:
        async with asyncio.timeout(REDIS_CALL_TIMEOUT_SECONDS):
            await get_redis().delete(name)
    except (RedisError, OSError, TimeoutError) as exc:
        _warn("reset", exc)


def _warn(operation: str, exc: BaseException) -> None:
    logger.warning("rate limiter unavailable (%s): %s; failing open", operation, type(exc).__name__)


async def enforce_public_limit(request: Request) -> None:
    """Count this request against the per-IP public bucket; 429 once it exceeds the limit."""
    settings = get_settings()
    windows = await hit([key("public", "ip", client_ip(request))])
    if windows is None:
        return
    if windows[0].count > settings.public_max_requests_per_ip:
        raise RateLimitedError(retry_after=windows[0].retry_after)


PublicRateLimitDep = Annotated[None, Depends(enforce_public_limit)]
"""Shared per-IP limit for the public routes (registration, invite preview, reset completion)."""
