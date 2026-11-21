"""Tests of the Redis spacing limiter (S-3.1.2); they use the isolated test Redis."""

import asyncio
import uuid

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core import spacing_limiter
from app.core.rate_limit import key
from app.core.redis import get_redis
from app.core.spacing_limiter import SpacingLimiter


async def test_concurrent_callers_reserve_slots_exactly_the_interval_apart() -> None:
    """Six reservations from three limiters move the shared slot by exactly 6 x 100 ms.

    The key is parked 10 s ahead so every call gets a distinct slot after it; the delays are the
    slots themselves (Redis clock arithmetic), so the result does not depend on wall-clock timing.
    """
    redis = get_redis()
    name = key("spacing", "t")
    seconds, micros = await redis.time()
    seed = int(seconds) * 1000 + int(micros) // 1000 + 10_000
    await redis.set(name, seed)
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    limiters = [SpacingLimiter(name, 100, sleep=record) for _ in range(3)]
    await asyncio.gather(*(limiter.wait() for limiter in limiters for _ in range(2)))

    assert int(await redis.get(name)) == seed + 6 * 100
    assert len(delays) == 6
    # Each delay is its slot minus "now" (which moves a little between calls), so only a
    # generous bound is checked: every slot lies after the 10 s seed and within 6 slots of it.
    assert all(8.0 <= d <= 11.0 for d in delays)


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


async def test_local_fallback_state_is_shared_by_limiters_of_one_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> None:
        raise RedisConnectionError("down")

    monkeypatch.setattr(spacing_limiter, "get_redis", broken)
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    name = f"k-{uuid.uuid4().hex}"
    first = SpacingLimiter(name, 100, sleep=record, monotonic=lambda: 50.0)
    second = SpacingLimiter(name, 100, sleep=record, monotonic=lambda: 50.0)
    for limiter in (first, second, first, second):
        await limiter.wait()
    assert delays == [pytest.approx(0.1), pytest.approx(0.2), pytest.approx(0.3)]


async def test_local_fallback_keys_do_not_share_state(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise RedisConnectionError("down")

    monkeypatch.setattr(spacing_limiter, "get_redis", broken)
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    for name in (f"a-{uuid.uuid4().hex}", f"b-{uuid.uuid4().hex}"):
        await SpacingLimiter(name, 100, sleep=record, monotonic=lambda: 1.0).wait()
    assert delays == []
