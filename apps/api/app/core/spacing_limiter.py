"""Redis spacing limiter: callers of one key are at least ``interval_ms`` apart (S-3.1.2).

Each call atomically reserves the next free slot (one Lua script, using the Redis clock so
processes need no synchronised clocks) and sleeps until it. Needs Redis 5 or later (the script
calls ``TIME`` before it writes).

Guarantees and limits:

* Slots are reserved exactly ``interval_ms`` apart on the Redis clock, so the reservations of any
  number of coroutines and processes never exceed ``1000 / interval_ms`` per second.
* Each caller sleeps from when its reply arrives, so the actual send times can differ from the
  slots by the reply latency and the timer resolution; the spacing of real sends is approximate.
* The waiting queue is uncapped: every waiter holds a reserved slot, however far ahead it is.
* A Redis restart drops the slot key, so the next caller gets an immediate slot.
* When Redis fails, calls are spaced locally instead, with state shared by every limiter of the
  same key in this process (one process then keeps to the interval; several processes do not
  coordinate), and one warning is logged per failure.
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

# Fallback state: the next free local slot (monotonic seconds) per Redis key, shared by every
# limiter of that key in this process. asyncio is single-threaded and the slot is taken without
# an await, so no lock is needed.
_local_next: dict[str, float] = {}


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
        now = self._monotonic()
        slot = max(_local_next.get(self._key, 0.0), now)
        _local_next[self._key] = slot + self._interval
        if slot > now:
            await self._sleep(slot - now)
