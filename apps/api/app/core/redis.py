"""Lazy Redis client (S-1.4.4, decision A15): redis-py's asyncio client, created on first use.

Importing this module never connects. :func:`close_redis` runs on app shutdown.
"""

from functools import lru_cache

from redis.asyncio import Redis

from app.core.config import get_settings

SOCKET_TIMEOUT_SECONDS = 2.0


@lru_cache
def get_redis() -> Redis:
    """Return the process-wide Redis client (built from ``REDIS_URL`` on first call)."""
    client: Redis = Redis.from_url(
        get_settings().redis_url,
        socket_connect_timeout=SOCKET_TIMEOUT_SECONDS,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
    )
    return client


async def close_redis() -> None:
    """Close the client if it was ever created, and forget it."""
    if get_redis.cache_info().currsize:
        await get_redis().aclose()
    get_redis.cache_clear()
