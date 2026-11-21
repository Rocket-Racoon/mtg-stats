"""Redis spacing limiter: callers of one key are at least ``interval_ms`` apart (S-3.1.2).

Each call atomically reserves the next free slot (one Lua script, using the Redis clock so
processes need no synchronised clocks) and sleeps until it. Slots are ``interval_ms`` apart, so
several coroutines or processes never exceed ``1000 / interval_ms`` calls per second. When Redis
fails, it falls back to spacing within this process and logs one warning (so a Redis outage never
makes an upstream API see a burst from one process).
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.redis import get_redis

logger = logging.getLogger("app.core.spacing_limiter")

# KEYS[1] = slot key, ARGV[1] = interval ms. Returns the milliseconds to wait for the reserved slot.
_RESERVE_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local interval = tonumber(ARGV[1])
local nxt = tonumber(redis.call('GET', KEYS[1]) or '0')
if nxt == nil or nxt < now then nxt = now end
redis.call('SET', KEYS[1], nxt + interval, 'PX', (nxt - now) + interval + 60000)
return nxt - now
"""

Sleep = Callable[[float], Awaitable[None]]


class SpacingLimiter:
    """Spaces calls for one Redis ``key`` by ``interval_ms`` across coroutines and processes."""

    def __init__(
        self,
        key: str,
        interval_ms: int,
        *,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._key = key
        self._interval = interval_ms / 1000
        self._interval_ms = interval_ms
        self._sleep = sleep
        self._monotonic = monotonic
        self._local_next = 0.0
        self._local_lock = asyncio.Lock()

    async def wait(self) -> None:
        """Block until this caller's slot arrives."""
        try:
            async with asyncio.timeout(get_settings().redis_call_timeout_seconds):
                reply = await get_redis().register_script(_RESERVE_LUA)(
                    keys=[self._key], args=[self._interval_ms]
                )
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("spacing limiter unavailable: %s; spacing locally", type(exc).__name__)
            await self._wait_locally()
            return
        delay_ms = int(reply)
        if delay_ms > 0:
            await self._sleep(delay_ms / 1000)

    async def _wait_locally(self) -> None:
        async with self._local_lock:
            delay = self._local_next - self._monotonic()
            if delay > 0:
                await self._sleep(delay)
            self._local_next = max(self._local_next, self._monotonic()) + self._interval
