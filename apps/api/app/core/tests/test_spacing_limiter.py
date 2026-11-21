"""Tests of the Redis spacing limiter (S-3.1.2); they use the isolated test Redis."""

import asyncio
import itertools

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core import spacing_limiter
from app.core.rate_limit import key
from app.core.spacing_limiter import SpacingLimiter


async def test_concurrent_callers_get_slots_at_least_the_interval_apart() -> None:
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    limiters = [SpacingLimiter(key("spacing", "t"), 100, sleep=record) for _ in range(3)]
    await asyncio.gather(*(limiter.wait() for limiter in limiters for _ in range(2)))
    waits = sorted(delays)
    # Six reserved slots: the first is immediate (no sleep), the others 0.1 s apart (+- the
    # time that passed between calls, so each is below its slot index * 0.1).
    assert len(waits) == 5
    assert all(b - a >= 0.09 for a, b in itertools.pairwise(waits))
    assert waits[-1] <= 0.5


async def test_falls_back_to_local_spacing_when_redis_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> None:
        raise RedisConnectionError("down")

    monkeypatch.setattr(spacing_limiter, "get_redis", broken)
    delays: list[float] = []
    clock = [0.0]

    async def record(seconds: float) -> None:
        delays.append(seconds)
        clock[0] += seconds

    limiter = SpacingLimiter("k", 100, sleep=record, monotonic=lambda: clock[0])
    for _ in range(3):
        await limiter.wait()
    assert delays == [pytest.approx(0.1), pytest.approx(0.1)]
