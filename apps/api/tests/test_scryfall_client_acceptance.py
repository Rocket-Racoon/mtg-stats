"""Acceptance tests of S-3.1.2 (Scryfall HTTP client that follows the API guidelines).

Nothing here really waits: ``sleep`` is injected and ``httpx.MockTransport`` replaces the network.
Spacing is checked deterministically on the Redis slot arithmetic (see the spacing tests), not on
wall-clock gaps.
"""

import asyncio
import re
import tomllib
from pathlib import Path

import httpx
import pytest

from app.cards.scryfall_client import API_HOST, BULK_HOST, ScryfallClient
from app.core.config import get_settings
from app.core.rate_limit import key as rate_limit_key
from app.core.redis import get_redis
from app.core.spacing_limiter import SpacingLimiter
from app.core.user_agent import build_user_agent
from tests.architecture.test_domain_imports import APP_ROOT
from tests.architecture.test_routers_never_call_scryfall import router_violations

API_DIR = Path(__file__).resolve().parents[1]
UA_PATTERN = re.compile(r"^MTGStats/\d+\.\d+\.\d+\S* \(\+https://github\.com/[\w.-]+/[\w.-]+\)$")


class NoWaitLimiter(SpacingLimiter):
    """A limiter that never waits, so retry sleeps can be asserted alone."""

    async def wait(self) -> None:
        return None


class Scripted:
    """Replays scripted responses (then 200) and records requests and every sleep."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0) if self.responses else httpx.Response(200, json={"ok": 1})

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def client(self, *, limiter: SpacingLimiter | None = None) -> ScryfallClient:
        return ScryfallClient(
            transport=httpx.MockTransport(self.handler),
            limiter=limiter or NoWaitLimiter("unused", 100),
            sleep=self.sleep,
        )


# --- Criterion 1: User-Agent and Accept -------------------------------------------------------


def test_user_agent_matches_the_exact_format_with_package_version_and_settings_repo_url() -> None:
    agent = build_user_agent()
    assert UA_PATTERN.fullmatch(agent)
    assert agent.endswith(f" (+{get_settings().repo_url})")
    assert "@" not in agent


def test_user_agent_version_is_the_version_declared_by_the_api_package() -> None:
    declared = tomllib.loads((API_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    assert build_user_agent().startswith(f"MTGStats/{declared} (+")


def test_user_agent_repo_url_follows_the_settings() -> None:
    other = get_settings().model_copy(update={"repo_slug": "acme/other-repo"})
    assert build_user_agent(other).endswith(" (+https://github.com/acme/other-repo)")
    assert "@" not in build_user_agent(other)


async def test_api_host_request_sends_user_agent_and_json_accept() -> None:
    script = Scripted()
    async with script.client() as client:
        await client.get_json("/cards/named", {"exact": "Sol Ring"})
    request = script.requests[0]
    assert request.url.host == "api.scryfall.com" == API_HOST
    assert request.headers["User-Agent"] == build_user_agent()
    assert "@" not in request.headers["User-Agent"]
    assert request.headers["Accept"] == "application/json"


async def test_bulk_host_download_sends_user_agent_and_wildcard_accept() -> None:
    script = Scripted(httpx.Response(200, content=b"[]"))
    url = "https://data.scryfall.io/default-cards/default-cards.json"
    async with script.client() as client, client.stream_bulk(url) as response:
        await response.aread()
    request = script.requests[0]
    assert request.url.host == "data.scryfall.io" == BULK_HOST
    assert request.headers["User-Agent"] == build_user_agent()
    assert request.headers["Accept"] == "*/*"


async def test_every_retry_keeps_the_user_agent_and_accept_headers() -> None:
    script = Scripted(
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(503),
        httpx.Response(500),
    )
    async with script.client() as client:
        await client.get_json("/sets")
    assert len(script.requests) == 4
    for request in script.requests:
        assert request.headers["User-Agent"] == build_user_agent()
        assert request.headers["Accept"] == "application/json"


async def test_bulk_retries_keep_the_user_agent_and_wildcard_accept() -> None:
    script = Scripted(httpx.Response(503), httpx.Response(200, content=b"[]"))
    url = "https://data.scryfall.io/default-cards/x.json"
    async with script.client() as client, client.stream_bulk(url) as response:
        await response.aread()
    assert len(script.requests) == 2
    for request in script.requests:
        assert request.headers["User-Agent"] == build_user_agent()
        assert request.headers["Accept"] == "*/*"


# --- Criterion 2: spacing shared through Redis -------------------------------------------------

SEED_AHEAD_MS = 10_000


async def _seed_slot(key: str) -> int:
    """Park the shared slot key ``SEED_AHEAD_MS`` in the future and return that slot (Redis ms)."""
    redis = get_redis()
    seconds, micros = await redis.time()
    slot = int(seconds) * 1000 + int(micros) // 1000 + SEED_AHEAD_MS
    await redis.set(key, slot)
    return slot


async def test_two_processes_sharing_redis_reserve_slots_exactly_100_ms_apart() -> None:
    """Each reservation moves the shared slot by exactly 100 ms, whichever limiter made it.

    The shared key is seeded 10 s ahead so every call reserves a distinct slot after it. With
    N reservations from two independent limiter instances (two "processes") the stored slot
    must end at ``seed + N * 100``: no reservation was skipped, shared or shorter than 100 ms.
    """
    key = rate_limit_key("scryfall", API_HOST)
    seed = await _seed_slot(key)
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)

    process_a = SpacingLimiter(key, 100, sleep=record)
    process_b = SpacingLimiter(key, 100, sleep=record)
    calls = 10
    await asyncio.gather(
        *(limiter.wait() for _ in range(calls // 2) for limiter in (process_a, process_b))
    )
    assert int(await get_redis().get(key)) == seed + calls * 100
    assert len(delays) == calls
    assert len(set(delays)) == calls  # distinct slots
    # The earliest slot is the seed, the latest is (calls - 1) * 100 ms after it.
    assert min(delays) <= SEED_AHEAD_MS / 1000
    assert max(delays) - min(delays) == pytest.approx((calls - 1) * 0.1, abs=0.5)
    assert max(delays) > SEED_AHEAD_MS / 1000 + (calls - 2) * 0.1 - 1.0


async def test_two_independent_clients_use_one_redis_slot_key_for_api_calls() -> None:
    key = rate_limit_key("scryfall", API_HOST)
    seed = await _seed_slot(key)
    script = Scripted()
    # No explicit limiter: each client builds its own default limiter on the shared Redis key.
    clients = [
        ScryfallClient(transport=httpx.MockTransport(script.handler), sleep=script.sleep)
        for _ in range(2)
    ]
    try:
        await asyncio.gather(*(c.get_json(f"/cards/{i}") for i in range(4) for c in clients))
    finally:
        await asyncio.gather(*(c.aclose() for c in clients))
    assert len(script.requests) == 8
    assert int(await get_redis().get(key)) == seed + 8 * 100
    assert len(script.sleeps) == 8
    assert len(set(script.sleeps)) == 8


async def test_the_default_limiter_interval_is_100_ms() -> None:
    assert get_settings().scryfall_min_interval_ms == 100


async def test_bulk_downloads_do_not_consume_api_rate_limit_slots() -> None:
    key = rate_limit_key("scryfall", API_HOST)
    seed = await _seed_slot(key)
    script = Scripted(httpx.Response(200, content=b"[]"))
    client = ScryfallClient(transport=httpx.MockTransport(script.handler), sleep=script.sleep)
    async with client, client.stream_bulk("https://data.scryfall.io/f.json") as response:
        await response.aread()
    assert int(await get_redis().get(key)) == seed
    assert script.sleeps == []


# --- Criterion 3: 429 and 5xx handling ---------------------------------------------------------


async def test_429_waits_the_retry_after_seconds_then_retries() -> None:
    script = Scripted(httpx.Response(429, headers={"Retry-After": "7"}))
    async with script.client() as client:
        assert await client.get_json("/sets") == {"ok": 1}
    assert script.sleeps == [7.0]
    assert len(script.requests) == 2


async def test_429_without_retry_after_waits_60_seconds() -> None:
    script = Scripted(httpx.Response(429))
    async with script.client() as client:
        await client.get_json("/sets")
    assert script.sleeps == [60.0]
    assert len(script.requests) == 2


async def test_429_is_retried_at_most_3_times_then_the_error_reaches_the_caller() -> None:
    script = Scripted(*(httpx.Response(429, headers={"Retry-After": "5"}) for _ in range(10)))
    async with script.client() as client:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await client.get_json("/sets")
    assert caught.value.response.status_code == 429
    assert len(script.requests) == 4  # 1 request + 3 retries
    assert script.sleeps == [5.0, 5.0, 5.0]  # no sleep after the last failure


async def test_429_each_retry_honours_its_own_retry_after() -> None:
    script = Scripted(
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(429),
        httpx.Response(429, headers={"Retry-After": "3"}),
    )
    async with script.client() as client:
        await client.get_json("/sets")
    assert script.sleeps == [1.0, 60.0, 3.0]
    assert len(script.requests) == 4


async def test_5xx_backs_off_2_4_8_seconds_over_3_retries() -> None:
    script = Scripted(httpx.Response(503), httpx.Response(502), httpx.Response(500))
    async with script.client() as client:
        assert await client.get_json("/sets") == {"ok": 1}
    assert script.sleeps == [2.0, 4.0, 8.0]
    assert len(script.requests) == 4


async def test_5xx_after_3_retries_raises_the_error_to_the_caller() -> None:
    script = Scripted(*(httpx.Response(503) for _ in range(10)))
    async with script.client() as client:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await client.get_json("/sets")
    assert caught.value.response.status_code == 503
    assert len(script.requests) == 4
    assert script.sleeps == [2.0, 4.0, 8.0]


async def test_5xx_then_success_stops_retrying_after_the_first_delay() -> None:
    script = Scripted(httpx.Response(500))
    async with script.client() as client:
        await client.get_json("/sets")
    assert script.sleeps == [2.0]
    assert len(script.requests) == 2


async def test_client_error_other_than_429_is_not_retried() -> None:
    script = Scripted(httpx.Response(404, json={"object": "error"}))
    async with script.client() as client:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await client.get_json("/cards/nope")
    assert caught.value.response.status_code == 404
    assert len(script.requests) == 1
    assert script.sleeps == []


async def test_bulk_5xx_backs_off_2_4_8_seconds_then_raises() -> None:
    script = Scripted(*(httpx.Response(500) for _ in range(10)))
    async with script.client() as client:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            async with client.stream_bulk("https://data.scryfall.io/f.json"):
                pass
    assert caught.value.response.status_code == 500
    assert len(script.requests) == 4
    assert script.sleeps == [2.0, 4.0, 8.0]


# --- Criterion 4: routers never call Scryfall ---------------------------------------------------


def test_the_real_app_has_no_router_importing_the_scryfall_client() -> None:
    assert router_violations(APP_ROOT) == []


def test_architecture_check_fails_for_a_router_that_imports_the_client_under_an_alias(
    tmp_path: Path,
) -> None:
    router = tmp_path / "decks" / "router.py"
    router.parent.mkdir(parents=True)
    router.write_text(
        "from fastapi import APIRouter\n"
        "from app.cards.scryfall_client import ScryfallClient as Client\n"
        "router = APIRouter()\n",
        encoding="utf-8",
    )
    violations = router_violations(tmp_path)
    assert violations
    assert all(v.startswith("decks/router.py:2:") for v in violations)
