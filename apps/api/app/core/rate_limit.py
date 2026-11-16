"""Redis fixed-window rate limiter (S-2.1.10).

Counting is one atomic Lua script (:func:`acquire`): check every limit, then increment, so
concurrent requests cannot slip past a limit. A counter gets its TTL when first created, so a
window starts at the first attempt and ends ``RATE_LIMIT_WINDOW_SECONDS`` later. The limiter fails
open: any Redis error or timeout lets the request through and logs one warning (the exception type
only: no key, username or address). It needs Lua scripting but no particular Redis version beyond
that (no ``EXPIRE ... NX``).

This module knows nothing of identity. Callers build the keys with :func:`key` and decide what a
limit means. The client address is ``request.client.host``; behind a reverse proxy, uvicorn must be
told which proxies to trust (``--forwarded-allow-ips`` / ``FORWARDED_ALLOW_IPS``), and the app never
reads ``X-Forwarded-For`` itself. When ``request.client`` is ``None`` (a Unix socket) every client
shares the ``"unknown"`` bucket.
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

UNKNOWN_CLIENT = "unknown"

# KEYS = counters, ARGV[1] = window seconds, ARGV[i + 1] = max for KEYS[i].
# Returns {1, retry_after} when blocked (nothing incremented), else {0, 0}.
_ACQUIRE_LUA = """
local window = tonumber(ARGV[1])
local retry = 0
local blocked = 0
for i, name in ipairs(KEYS) do
  local count = tonumber(redis.call('GET', name) or '0')
  if count == nil then
    redis.call('DEL', name)
    count = 0
  end
  if count >= tonumber(ARGV[i + 1]) then
    blocked = 1
    local ttl = redis.call('TTL', name)
    if ttl == -1 then redis.call('EXPIRE', name, window) end
    if ttl < 0 then ttl = window elseif ttl < 1 then ttl = 1 end
    if ttl > retry then retry = ttl end
  end
end
if blocked == 1 then return {1, retry} end
for _, name in ipairs(KEYS) do
  redis.call('INCR', name)
  if redis.call('TTL', name) < 0 then redis.call('EXPIRE', name, window) end
end
return {0, 0}
"""

# Decrement each key that exists and is above 0; never creates a key.
_GIVE_BACK_LUA = """
for _, name in ipairs(KEYS) do
  local count = tonumber(redis.call('GET', name) or '0')
  if count > 0 then redis.call('DECR', name) end
end
return 0
"""


@dataclass(frozen=True)
class Allowed:
    """The attempt may proceed. ``counted`` is False when Redis failed and it passed uncounted."""

    counted: bool


@dataclass(frozen=True)
class Blocked:
    """A limit is reached; nothing was incremented. ``retry_after`` is at least 1 second."""

    retry_after: int


def key(*parts: str) -> str:
    """Build ``{RATE_LIMIT_KEY_PREFIX}:{part}:{part}...``."""
    return ":".join([get_settings().rate_limit_key_prefix, *parts])


def client_ip(request: Request) -> str:
    """The peer address (``request.client.host``), or ``"unknown"`` without a client."""
    return request.client.host if request.client else UNKNOWN_CLIENT


async def acquire(limits: Sequence[tuple[str, int]]) -> Allowed | Blocked:
    """Atomically count one attempt against every ``(key, max)``, or block.

    One Lua script reads every key; if any count is at or over its max it increments nothing and
    returns :class:`Blocked` with the largest remaining TTL (the window when a TTL is missing).
    Otherwise every key is incremented, and any key without a TTL gets the window. A Redis error or
    timeout fails open: ``Allowed(counted=False)`` and one warning.
    """
    settings = get_settings()
    try:
        async with asyncio.timeout(settings.redis_call_timeout_seconds):
            script = get_redis().register_script(_ACQUIRE_LUA)
            reply = await script(
                keys=[name for name, _ in limits],
                args=[settings.rate_limit_window_seconds, *(limit for _, limit in limits)],
            )
    except (RedisError, OSError, TimeoutError) as exc:
        _warn("acquire", exc)
        return Allowed(counted=False)
    if int(reply[0]) == 1:
        return Blocked(retry_after=max(int(reply[1]), 1))
    return Allowed(counted=True)


async def give_back(keys: Sequence[str]) -> None:
    """Decrement each existing counter, never below 0 and never creating one (best effort)."""
    try:
        async with asyncio.timeout(get_settings().redis_call_timeout_seconds):
            await get_redis().register_script(_GIVE_BACK_LUA)(keys=list(keys))
    except (RedisError, OSError, TimeoutError) as exc:
        _warn("give_back", exc)


async def reset(name: str) -> None:
    """Delete one counter; a Redis failure is logged and ignored."""
    try:
        async with asyncio.timeout(get_settings().redis_call_timeout_seconds):
            await get_redis().delete(name)
    except (RedisError, OSError, TimeoutError) as exc:
        _warn("reset", exc)


def _warn(operation: str, exc: BaseException) -> None:
    logger.warning("rate limiter unavailable (%s): %s; failing open", operation, type(exc).__name__)


async def enforce_public_limit(request: Request) -> None:
    """Count this request against the per-IP public bucket; 429 when the limit is reached.

    Uses :func:`acquire`: calls 1 to N pass and call N + 1 gets 429 (a 429 does not increment).
    """
    limit = get_settings().public_max_requests_per_ip
    outcome = await acquire([(key("public", "ip", client_ip(request)), limit)])
    if isinstance(outcome, Blocked):
        raise RateLimitedError(retry_after=outcome.retry_after)


PublicRateLimitDep = Annotated[None, Depends(enforce_public_limit)]
"""Shared per-IP limit for the public routes (registration, invite preview, reset completion)."""
