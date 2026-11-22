"""Unit tests of the deck-site client internals (S-4.2.1); no network, ``httpx.MockTransport``."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import get_settings
from app.core.redis import get_redis
from app.core.retry_after import parse_retry_after
from app.core.spacing_limiter import SpacingLimiter
from app.core.user_agent import build_user_agent
from app.decks.services import (
    DeckFetched,
    DeckFetchFailed,
    DeckFetchFailure,
    DeckSiteClient,
    DeckSiteHostError,
    SiteConfig,
    default_sites,
)
from app.decks.services.deck_sites import cache as cache_module
from app.decks.services.deck_sites.cache import CachedBody, DeckCache, cache_key

HOST = "decks.example.test"
SITE = SiteConfig(name="testsite", hosts=frozenset({HOST}), min_interval_ms=100, max_concurrency=2)
Handler = Callable[[httpx.Request], httpx.Response]


def _url(deck_id: str) -> str:
    return f"https://{HOST}/api/decks/{deck_id}/"


def _deck_id() -> str:
    return uuid.uuid4().hex


class _Recorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)


def _fast_limiter(site: SiteConfig) -> SpacingLimiter:
    return SpacingLimiter(f"test:decksite:{uuid.uuid4().hex}", 1, sleep=_Recorder().sleep)


def _client(handler: Handler, rec: _Recorder | None = None) -> DeckSiteClient:
    recorder = rec or _Recorder()
    return DeckSiteClient(
        sites={SITE.name: SITE},
        transport=httpx.MockTransport(handler),
        limiter_factory=lambda site: SpacingLimiter(
            f"test:decksite:{uuid.uuid4().hex}", site.min_interval_ms, sleep=recorder.sleep
        ),
        sleep=recorder.sleep,
    )


async def test_success_returns_raw_body_content_type_and_user_agent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, content=b'{"a": 1}', headers={"Content-Type": "application/json"}
        )

    async with _client(handler) as client:
        result = await client.fetch(SITE.name, _deck_id(), _url("1"))

    assert isinstance(result, DeckFetched)
    assert (result.body, result.content_type, result.from_cache) == (
        b'{"a": 1}',
        "application/json",
        False,
    )
    assert seen[0].headers["User-Agent"] == build_user_agent()
    assert "@" not in seen[0].headers["User-Agent"]
    assert seen[0].headers["Accept"] == "application/json"


async def test_second_fetch_of_the_same_deck_is_served_from_cache() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"body", headers={"Content-Type": "text/plain"})

    deck = _deck_id()
    async with _client(handler) as client:
        first = await client.fetch(SITE.name, deck, _url("1"))
        second = await client.fetch(SITE.name, deck, _url("1"))
    assert calls == 1
    assert isinstance(first, DeckFetched)
    assert isinstance(second, DeckFetched)
    assert second.from_cache
    assert second.body == b"body"
    assert second.content_type == "text/plain"
    ttl = await get_redis().ttl(cache_key(SITE.name, deck))
    assert 0 < ttl <= get_settings().deck_site_cache_ttl_seconds


async def test_failures_are_not_cached() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    deck = _deck_id()
    async with _client(handler) as client:
        await client.fetch(SITE.name, deck, _url("1"))
        await client.fetch(SITE.name, deck, _url("1"))
    assert calls == 2
    assert await get_redis().get(cache_key(SITE.name, deck)) is None


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, DeckFetchFailure.BLOCKED),
        (403, DeckFetchFailure.BLOCKED),
        (404, DeckFetchFailure.NOT_FOUND),
        (410, DeckFetchFailure.NOT_FOUND),
        (500, DeckFetchFailure.SERVER_ERROR),
        (503, DeckFetchFailure.SERVER_ERROR),
        (400, DeckFetchFailure.UNEXPECTED_RESPONSE),
        (302, DeckFetchFailure.UNEXPECTED_RESPONSE),
    ],
)
async def test_status_is_mapped_to_a_reason_without_retrying(
    status: int, reason: DeckFetchFailure
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, headers={"Location": "https://evil.example.test/"})

    async with _client(handler) as client:
        result = await client.fetch(SITE.name, _deck_id(), _url("1"))
    assert result == DeckFetchFailed(SITE.name, result.deck_id, reason, status)
    assert calls == 1


async def test_429_waits_for_retry_after_and_retries_at_most_twice() -> None:
    calls = 0
    rec = _Recorder()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "3"})

    async with _client(handler, rec) as client:
        result = await client.fetch(SITE.name, _deck_id(), _url("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.RATE_LIMITED
    assert result.status_code == 429
    assert calls == 3
    assert [d for d in rec.delays if d >= 3] == [3.0, 3.0]


async def test_429_then_success_returns_the_deck() -> None:
    answers = iter(
        [httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, content=b"x")]
    )

    async with _client(lambda request: next(answers)) as client:
        result = await client.fetch(SITE.name, _deck_id(), _url("1"))
    assert isinstance(result, DeckFetched)
    assert result.body == b"x"


async def test_retry_after_above_the_cap_is_not_waited_for() -> None:
    calls = 0
    rec = _Recorder()
    cap = get_settings().deck_site_retry_after_cap_seconds

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": str(int(cap) + 1)})

    async with _client(handler, rec) as client:
        result = await client.fetch(SITE.name, _deck_id(), _url("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.RATE_LIMITED
    assert calls == 1
    assert all(d < cap for d in rec.delays)


async def test_missing_retry_after_waits_the_default() -> None:
    answers = iter([httpx.Response(429), httpx.Response(200, content=b"x")])
    rec = _Recorder()
    async with _client(lambda request: next(answers), rec) as client:
        await client.fetch(SITE.name, _deck_id(), _url("1"))
    assert 5.0 in rec.delays


async def test_timeout_is_reported_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with _client(handler) as client:
        result = await client.fetch(SITE.name, _deck_id(), _url("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.TIMEOUT


async def test_a_response_slower_than_the_overall_timeout_is_a_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200)

    fast = get_settings().model_copy(update={"deck_site_timeout_seconds": 0.05})
    async with DeckSiteClient(
        settings=fast,
        sites={SITE.name: SITE},
        transport=httpx.MockTransport(handler),
        limiter_factory=lambda s: SpacingLimiter(
            f"test:{uuid.uuid4().hex}", 1, sleep=_Recorder().sleep
        ),
    ) as client:
        result = await client.fetch(SITE.name, _deck_id(), _url("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.TIMEOUT


async def test_connection_error_is_a_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with _client(handler) as client:
        result = await client.fetch(SITE.name, _deck_id(), _url("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.NETWORK_ERROR
    assert result.detail == "ConnectError"


async def test_body_over_the_size_cap_is_too_large_and_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = get_settings().deck_site_max_response_bytes
    big = b"x" * (limit + 1)
    deck = _deck_id()

    async with _client(lambda request: httpx.Response(200, content=big)) as client:
        declared = await client.fetch(SITE.name, deck, _url("1"))

    async def chunks() -> AsyncIterator[bytes]:
        yield b"x" * limit
        yield b"y"

    def streamed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=chunks())

    async with _client(streamed) as client:
        chunked = await client.fetch(SITE.name, deck, _url("1"))
    for result in (declared, chunked):
        assert isinstance(result, DeckFetchFailed)
        assert result.reason is DeckFetchFailure.TOO_LARGE
    assert await get_redis().get(cache_key(SITE.name, deck)) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.test/api/decks/1/",
        f"http://{HOST}/api/decks/1/",
        f"https://{HOST}.evil.example.test/",
        f"https://user@evil.example.test/@{HOST}/",
        "file:///etc/passwd",
    ],
)
async def test_url_outside_the_site_hosts_is_refused_without_a_request(url: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    async with _client(handler) as client:
        with pytest.raises(DeckSiteHostError):
            await client.fetch(SITE.name, _deck_id(), url)
    assert calls == 0


async def test_unregistered_site_is_refused() -> None:
    async with _client(lambda request: httpx.Response(200)) as client:
        with pytest.raises(DeckSiteHostError):
            await client.fetch("moxfield", "1", "https://moxfield.com/decks/1")


async def test_redirects_are_not_followed() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(301, headers={"Location": "https://169.254.169.254/"})

    async with _client(handler) as client:
        result = await client.fetch(SITE.name, _deck_id(), _url("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.UNEXPECTED_RESPONSE
    assert seen == [HOST]


async def test_concurrency_per_site_is_capped() -> None:
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(200, content=b"x")

    site = SiteConfig(SITE.name, SITE.hosts, min_interval_ms=100, max_concurrency=2)
    async with DeckSiteClient(
        sites={site.name: site},
        transport=httpx.MockTransport(handler),
        limiter_factory=lambda s: SpacingLimiter(
            f"test:{uuid.uuid4().hex}", 1, sleep=_Recorder().sleep
        ),
    ) as client:
        results = await asyncio.gather(
            *(client.fetch(SITE.name, _deck_id(), _url(str(i))) for i in range(6))
        )
    assert all(isinstance(r, DeckFetched) for r in results)
    assert peak == 2


async def test_each_request_waits_for_the_site_spacing_limiter() -> None:
    waits = 0

    class Counting(SpacingLimiter):
        async def wait(self) -> None:
            nonlocal waits
            waits += 1

    answers = iter([httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200)])
    async with DeckSiteClient(
        sites={SITE.name: SITE},
        transport=httpx.MockTransport(lambda request: next(answers)),
        limiter_factory=lambda s: Counting("k", 100),
        sleep=_Recorder().sleep,
    ) as client:
        await client.fetch(SITE.name, _deck_id(), _url("1"))
    assert waits == 2


async def test_cache_hit_makes_no_limiter_wait_and_no_request() -> None:
    deck = _deck_id()
    await DeckCache().set(SITE.name, deck, CachedBody(b"cached", "application/json"))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with _client(handler) as client:
        result = await client.fetch(SITE.name, deck, _url("1"))
    assert isinstance(result, DeckFetched)
    assert result.from_cache
    assert result.body == b"cached"


async def test_redis_failure_falls_back_to_fetching(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise RedisConnectionError("down")

    monkeypatch.setattr(cache_module, "get_redis", broken)
    async with _client(lambda request: httpx.Response(200, content=b"live")) as client:
        result = await client.fetch(SITE.name, _deck_id(), _url("1"))
    assert isinstance(result, DeckFetched)
    assert result.body == b"live"
    assert not result.from_cache


async def test_unreadable_cache_entry_is_a_miss() -> None:
    deck = _deck_id()
    await get_redis().set(cache_key(SITE.name, deck), b"no separator here", ex=30)
    assert await DeckCache().get(SITE.name, deck) is None


def test_archidekt_default_spacing_is_well_under_forty_a_minute() -> None:
    site = default_sites()["archidekt"]
    assert site.hosts == {"archidekt.com"}
    assert 60_000 / site.min_interval_ms <= 30


def test_core_retry_after_uses_the_given_default() -> None:
    assert parse_retry_after(None, default=5.0) == 5.0
    assert parse_retry_after("soon", default=5.0) == 5.0
    assert parse_retry_after("7", default=5.0) == 7.0
