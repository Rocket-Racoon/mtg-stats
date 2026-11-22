"""Acceptance tests of S-4.2.1 (polite deck-site HTTP client with caching).

Written from the story's criteria, not from the implementation. Nothing here uses the network or
really waits: ``httpx.MockTransport`` replaces the site and ``sleep`` is injected. Redis is the test
Redis (every test has its own key prefix, see ``conftest.rate_limit_prefix``).

A test that fails marks code that contradicts the story or its safety brief; none is weakened.
"""

import asyncio
import contextlib
import inspect
import tomllib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from app.core import spacing_limiter
from app.core.config import Settings, get_settings
from app.core.redis import get_redis
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
from tests.architecture.test_domain_imports import APP_ROOT
from tests.architecture.test_routers_never_call_scryfall import router_violations

API_DIR = Path(__file__).resolve().parents[1]
HOST = "decks.example.test"
SITE = SiteConfig(name="testsite", hosts=frozenset({HOST}), min_interval_ms=100, max_concurrency=2)
TEST_SITES: Mapping[str, SiteConfig] = {SITE.name: SITE}
BROWSER_TOKENS = ("Mozilla", "Chrome", "Safari", "AppleWebKit", "Gecko", "Edg")
ARCHIDEKT_URL = "https://archidekt.com/api/decks/{}/"

Responder = Callable[[int, httpx.Request], httpx.Response | Awaitable[httpx.Response]]


def url_for(deck: str) -> str:
    return f"https://{HOST}/api/decks/{deck}/"


def new_deck() -> str:
    return uuid.uuid4().hex


def declared_version() -> str:
    data = tomllib.loads((API_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def settings_with(**update: Any) -> Settings:
    return get_settings().model_copy(update=update)


def ok_body(deck: str) -> bytes:
    return f'{{"deck": "{deck}"}}'.encode()


def ok(_n: int, _request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b'{"ok": 1}', headers={"Content-Type": "application/json"})


class NoWaitLimiter(SpacingLimiter):
    """A limiter that never waits, so retry sleeps can be asserted alone."""

    async def wait(self) -> None:
        return None


class Site:
    """A scripted deck site: records every request and every sleep the client asks for."""

    def __init__(self, responder: Responder = ok) -> None:
        self.responder = responder
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        result = self.responder(len(self.requests), request)
        if inspect.isawaitable(result):
            return await result
        return result

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def client(
        self,
        *,
        settings: Settings | None = None,
        sites: Mapping[str, SiteConfig] | None = TEST_SITES,
        no_wait: bool = True,
    ) -> DeckSiteClient:
        return DeckSiteClient(
            settings=settings,
            sites=sites,
            transport=httpx.MockTransport(self.handler),
            limiter_factory=(lambda site: NoWaitLimiter("unused", 100)) if no_wait else None,
            sleep=self.sleep,
        )


def always(status: int, **headers: str) -> Responder:
    def respond(_n: int, _request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers)

    return respond


async def stored_entries(marker: bytes) -> list[tuple[Any, int]]:
    """(key, remaining TTL in ms) of every Redis key of this test whose value holds ``marker``."""
    redis = get_redis()
    prefix = get_settings().rate_limit_key_prefix
    found: list[tuple[Any, int]] = []
    async for key in redis.scan_iter(f"{prefix}*"):
        value = await redis.get(key)
        if value is not None and marker in bytes(value):
            found.append((key, await redis.pttl(key)))
    return found


# --- Criterion 1: User-Agent ------------------------------------------------------------------


async def test_first_request_and_every_429_retry_carry_the_exact_user_agent() -> None:
    site = Site(
        lambda n, r: httpx.Response(429, headers={"Retry-After": "1"}) if n < 3 else ok(n, r)
    )
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetched)
    assert len(site.requests) == 3
    expected = f"MTGStats/{declared_version()} (+{get_settings().repo_url})"
    assert [r.headers["User-Agent"] for r in site.requests] == [expected] * 3
    assert all(len(r.headers.get_list("User-Agent")) == 1 for r in site.requests)


async def test_user_agent_repo_url_follows_the_settings() -> None:
    site = Site()
    other = settings_with(repo_slug="acme/other-repo")
    async with site.client(settings=other) as client:
        await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert site.requests[0].headers["User-Agent"] == (
        f"MTGStats/{declared_version()} (+https://github.com/acme/other-repo)"
    )


async def test_user_agent_has_no_email_and_no_browser_token() -> None:
    site = Site(always(429, **{"Retry-After": "0"}))
    async with site.client() as client:
        await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert len(site.requests) == 3
    for request in site.requests:
        agent = request.headers["User-Agent"]
        assert "@" not in agent
        assert not [token for token in BROWSER_TOKENS if token in agent]
        assert agent.startswith("MTGStats/")


def test_caller_has_no_way_to_override_the_user_agent() -> None:
    forbidden = {"headers", "user_agent", "user-agent", "extra_headers", "kwargs"}
    for target in (DeckSiteClient.fetch, DeckSiteClient.__init__):
        parameters = inspect.signature(target).parameters
        assert not forbidden & set(parameters)
        assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())


async def test_fetch_rejects_a_headers_argument() -> None:
    site = Site()
    async with site.client() as client:
        with pytest.raises(TypeError):
            await client.fetch(  # type: ignore[call-arg]
                SITE.name, new_deck(), url_for("1"), headers={"User-Agent": "Mozilla/5.0"}
            )
    assert site.requests == []


async def test_accept_argument_cannot_smuggle_a_user_agent() -> None:
    site = Site()
    async with site.client() as client:
        await client.fetch(
            SITE.name, new_deck(), url_for("1"), accept="text/html\r\nUser-Agent: Mozilla/5.0"
        )
    for request in site.requests:
        assert request.headers.get_list("User-Agent") == [build_user_agent()]


# --- Criterion 2: cache -----------------------------------------------------------------------


async def test_same_deck_requested_again_is_served_from_cache_without_a_request() -> None:
    deck = new_deck()
    site = Site(
        lambda n, r: httpx.Response(
            200, content=ok_body(deck), headers={"Content-Type": "application/json"}
        )
    )
    async with site.client() as client:
        first = await client.fetch(SITE.name, deck, url_for("1"))
        second = await client.fetch(SITE.name, deck, url_for("1"))
        third = await client.fetch(SITE.name, deck, url_for("1"))
    assert len(site.requests) == 1
    assert isinstance(first, DeckFetched)
    assert not first.from_cache
    for hit in (second, third):
        assert isinstance(hit, DeckFetched)
        assert hit.from_cache
        assert hit.body == first.body == ok_body(deck)
        assert hit.content_type == "application/json"
        assert (hit.site, hit.deck_id) == (SITE.name, deck)


async def test_cache_is_shared_through_redis_with_another_client_instance() -> None:
    deck = new_deck()
    writer = Site(lambda n, r: httpx.Response(200, content=ok_body(deck)))
    async with writer.client() as first_client:
        await first_client.fetch(SITE.name, deck, url_for("1"))

    def forbidden(_n: int, _request: httpx.Request) -> httpx.Response:
        raise AssertionError("a cache hit must not send a request")

    reader = Site(forbidden)
    async with reader.client() as second_client:
        hit = await second_client.fetch(SITE.name, deck, url_for("1"))
    assert isinstance(hit, DeckFetched)
    assert hit.from_cache
    assert hit.body == ok_body(deck)
    assert reader.requests == []


def test_cache_ttl_defaults_to_fifteen_minutes_in_settings() -> None:
    assert Settings.model_fields["deck_site_cache_ttl_seconds"].default == 900 == 15 * 60
    assert get_settings().deck_site_cache_ttl_seconds == 900


async def test_stored_entry_expires_after_the_default_fifteen_minutes() -> None:
    deck = new_deck()
    site = Site(lambda n, r: httpx.Response(200, content=ok_body(deck)))
    async with site.client() as client:
        await client.fetch(SITE.name, deck, url_for("1"))
    entries = await stored_entries(deck.encode())
    assert len(entries) == 1
    assert 890_000 < entries[0][1] <= 900_000


async def test_stored_entry_ttl_follows_the_setting() -> None:
    deck = new_deck()
    site = Site(lambda n, r: httpx.Response(200, content=ok_body(deck)))
    async with site.client(settings=settings_with(deck_site_cache_ttl_seconds=120)) as client:
        await client.fetch(SITE.name, deck, url_for("1"))
    entries = await stored_entries(deck.encode())
    assert len(entries) == 1
    assert 110_000 < entries[0][1] <= 120_000


async def test_next_fetch_goes_out_again_after_the_entry_expires() -> None:
    deck = new_deck()
    site = Site(lambda n, r: httpx.Response(200, content=ok_body(deck)))
    async with site.client() as client:
        await client.fetch(SITE.name, deck, url_for("1"))
        [(key, _ttl)] = await stored_entries(deck.encode())
        await get_redis().pexpire(key, 1)
        await asyncio.sleep(0.05)
        again = await client.fetch(SITE.name, deck, url_for("1"))
    assert isinstance(again, DeckFetched)
    assert not again.from_cache
    assert len(site.requests) == 2


async def test_next_fetch_goes_out_again_after_the_entry_is_deleted() -> None:
    deck = new_deck()
    site = Site(lambda n, r: httpx.Response(200, content=ok_body(deck)))
    async with site.client() as client:
        await client.fetch(SITE.name, deck, url_for("1"))
        [(key, _ttl)] = await stored_entries(deck.encode())
        await get_redis().delete(key)
        again = await client.fetch(SITE.name, deck, url_for("1"))
    assert isinstance(again, DeckFetched)
    assert not again.from_cache
    assert len(site.requests) == 2


async def test_a_different_deck_id_is_fetched_and_gets_its_own_body() -> None:
    first_deck, second_deck = new_deck(), new_deck()
    site = Site(lambda n, r: httpx.Response(200, content=r.url.path.encode()))
    async with site.client() as client:
        first = await client.fetch(SITE.name, first_deck, url_for(first_deck))
        second = await client.fetch(SITE.name, second_deck, url_for(second_deck))
    assert len(site.requests) == 2
    assert isinstance(first, DeckFetched)
    assert isinstance(second, DeckFetched)
    assert first.body == f"/api/decks/{first_deck}/".encode()
    assert second.body == f"/api/decks/{second_deck}/".encode()
    assert not second.from_cache


@pytest.mark.parametrize("other_form", ["{}", "{}?foo=1", "{}#frag"])
async def test_same_deck_id_with_another_url_form_still_returns_a_deck(other_form: str) -> None:
    """Observation only: the criterion says "same deck URL", the cache is keyed by site + deck ID.

    Whether the second request is a cache hit or goes out is not asserted either way (reported).
    """
    deck = new_deck()
    site = Site(lambda n, r: httpx.Response(200, content=ok_body(deck)))
    async with site.client() as client:
        await client.fetch(SITE.name, deck, url_for(deck))
        result = await client.fetch(SITE.name, deck, other_form.format(url_for(deck)))
    assert isinstance(result, DeckFetched)
    assert result.body == ok_body(deck)


async def test_same_deck_id_with_a_trailing_slash_difference_still_returns_a_deck() -> None:
    deck = new_deck()
    site = Site(lambda n, r: httpx.Response(200, content=ok_body(deck)))
    async with site.client() as client:
        await client.fetch(SITE.name, deck, f"https://{HOST}/api/decks/{deck}/")
        result = await client.fetch(SITE.name, deck, f"https://{HOST}/api/decks/{deck}")
    assert isinstance(result, DeckFetched)
    assert result.body == ok_body(deck)


def _fail_then_succeed(failure: str, state: dict[str, bool], deck: str) -> Responder:
    def respond(_n: int, request: httpx.Request) -> httpx.Response:
        if not state["failing"]:
            return httpx.Response(200, content=ok_body(deck))
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if failure == "network":
            raise httpx.ConnectError("refused", request=request)
        if failure == "oversize":
            return httpx.Response(200, content=b"x" * 2000)
        if failure == "429":
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(int(failure))

    return respond


@pytest.mark.parametrize(
    "failure", ["404", "403", "429", "500", "503", "timeout", "network", "oversize", "302"]
)
async def test_failed_fetch_is_not_served_from_cache_afterwards(failure: str) -> None:
    deck = new_deck()
    state = {"failing": True}
    site = Site(_fail_then_succeed(failure, state, deck))
    async with site.client(settings=settings_with(deck_site_max_response_bytes=1024)) as client:
        failed = await client.fetch(SITE.name, deck, url_for("1"))
        assert isinstance(failed, DeckFetchFailed)
        assert await stored_entries(deck.encode()) == []
        state["failing"] = False
        before = len(site.requests)
        recovered = await client.fetch(SITE.name, deck, url_for("1"))
    assert isinstance(recovered, DeckFetched)
    assert not recovered.from_cache
    assert len(site.requests) == before + 1
    assert recovered.body == ok_body(deck)


class _BrokenRedis:
    def __getattr__(self, name: str) -> Any:
        raise RedisConnectionError("down")


class _HangingRedis:
    async def get(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(30)

    async def set(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(30)


class _WriteFailsRedis:
    async def get(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def set(self, *args: Any, **kwargs: Any) -> None:
        raise RedisError("read-only replica")


async def test_redis_outage_does_not_turn_a_fetchable_deck_into_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> _BrokenRedis:
        raise RedisConnectionError("down")

    monkeypatch.setattr(cache_module, "get_redis", broken)
    monkeypatch.setattr(spacing_limiter, "get_redis", broken)
    deck = new_deck()
    site = Site(lambda n, r: httpx.Response(200, content=ok_body(deck)))
    async with site.client(no_wait=False) as client:
        first = await client.fetch(SITE.name, deck, url_for("1"))
        second = await client.fetch(SITE.name, deck, url_for("1"))
    for result in (first, second):
        assert isinstance(result, DeckFetched)
        assert result.body == ok_body(deck)
        assert not result.from_cache


async def test_redis_that_hangs_does_not_turn_a_fetchable_deck_into_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cache_module, "get_redis", _HangingRedis)
    deck = new_deck()
    site = Site(lambda n, r: httpx.Response(200, content=ok_body(deck)))
    quick = settings_with(redis_call_timeout_seconds=0.05)
    async with site.client(settings=quick) as client:
        result = await asyncio.wait_for(client.fetch(SITE.name, deck, url_for("1")), timeout=5)
    assert isinstance(result, DeckFetched)
    assert result.body == ok_body(deck)


async def test_redis_that_cannot_store_the_entry_still_returns_the_deck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cache_module, "get_redis", _WriteFailsRedis)
    deck = new_deck()
    site = Site(lambda n, r: httpx.Response(200, content=ok_body(deck)))
    async with site.client() as client:
        result = await client.fetch(SITE.name, deck, url_for("1"))
    assert isinstance(result, DeckFetched)
    assert result.body == ok_body(deck)


# --- Criterion 3: 429 and Retry-After ---------------------------------------------------------


async def test_429_every_time_makes_three_requests_and_reports_rate_limited() -> None:
    site = Site(always(429, **{"Retry-After": "3"}))
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.RATE_LIMITED
    assert result.reason == "rate_limited"
    assert result.status_code == 429
    assert len(site.requests) == 3
    assert site.sleeps == [3.0, 3.0]


async def test_wait_before_each_retry_equals_that_responses_retry_after() -> None:
    def respond(n: int, _request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": str(n * 2)})

    site = Site(respond)
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert len(site.requests) == 3
    assert site.sleeps == [2.0, 4.0]


async def test_retry_after_http_date_is_honoured_for_each_retry() -> None:
    def respond(_n: int, _request: httpx.Request) -> httpx.Response:
        when = format_datetime(datetime.now(UTC) + timedelta(seconds=20), usegmt=True)
        return httpx.Response(429, headers={"Retry-After": when})

    site = Site(respond)
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.RATE_LIMITED
    assert len(site.requests) == 3
    assert len(site.sleeps) == 2
    assert all(18.0 <= s <= 20.0 for s in site.sleeps)


async def test_429_then_200_returns_the_deck_after_one_retry() -> None:
    site = Site(
        lambda n, r: httpx.Response(429, headers={"Retry-After": "1"}) if n == 1 else ok(n, r)
    )
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetched)
    assert result.body == b'{"ok": 1}'
    assert len(site.requests) == 2
    assert site.sleeps == [1.0]


async def test_two_429s_then_200_returns_the_deck_on_the_last_allowed_retry() -> None:
    site = Site(
        lambda n, r: httpx.Response(429, headers={"Retry-After": "1"}) if n < 3 else ok(n, r)
    )
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetched)
    assert len(site.requests) == 3
    assert site.sleeps == [1.0, 1.0]


async def test_retry_after_equal_to_the_cap_is_waited_for() -> None:
    cap = get_settings().deck_site_retry_after_cap_seconds
    site = Site(always(429, **{"Retry-After": str(int(cap))}))
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert site.sleeps == [cap, cap]
    assert len(site.requests) == 3


async def test_retry_after_above_the_cap_never_sleeps_longer_than_the_cap() -> None:
    """Both readings agree on: never above the cap, at most 2 retries, ends in rate_limited.

    Whether the client gives up at once or waits the capped time is not asserted (reported).
    """
    cap = get_settings().deck_site_retry_after_cap_seconds
    site = Site(always(429, **{"Retry-After": str(int(cap) + 3600)}))
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.RATE_LIMITED
    assert 1 <= len(site.requests) <= 3
    assert all(s <= cap for s in site.sleeps)


async def test_retry_after_http_date_far_in_the_future_never_sleeps_longer_than_the_cap() -> None:
    cap = get_settings().deck_site_retry_after_cap_seconds

    def respond(_n: int, _request: httpx.Request) -> httpx.Response:
        when = format_datetime(datetime.now(UTC) + timedelta(days=2), usegmt=True)
        return httpx.Response(429, headers={"Retry-After": when})

    site = Site(respond)
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.RATE_LIMITED
    assert all(s <= cap for s in site.sleeps)
    assert len(site.requests) <= 3


async def test_cap_comes_from_settings() -> None:
    small = settings_with(deck_site_retry_after_cap_seconds=5.0)
    at_cap = Site(always(429, **{"Retry-After": "5"}))
    async with at_cap.client(settings=small) as client:
        await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert at_cap.sleeps == [5.0, 5.0]

    over = Site(always(429, **{"Retry-After": "6"}))
    async with over.client(settings=small) as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.RATE_LIMITED
    assert all(s <= 5.0 for s in over.sleeps)
    assert len(over.requests) <= 3


@pytest.mark.parametrize(("retries", "requests"), [(0, 1), (1, 2), (2, 3)])
async def test_retry_count_comes_from_settings(retries: int, requests: int) -> None:
    site = Site(always(429, **{"Retry-After": "1"}))
    async with site.client(settings=settings_with(deck_site_max_retries=retries)) as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.RATE_LIMITED
    assert len(site.requests) == requests


def test_defaults_are_two_retries_and_a_thirty_second_cap() -> None:
    assert Settings.model_fields["deck_site_max_retries"].default == 2
    assert Settings.model_fields["deck_site_retry_after_cap_seconds"].default == 30.0


def test_settings_cannot_raise_the_retries_above_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECK_SITE_MAX_RETRIES", "3")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


async def test_429_without_retry_after_still_stops_after_two_retries_and_within_the_cap() -> None:
    site = Site(always(429))
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.RATE_LIMITED
    assert len(site.requests) == 3
    assert all(s <= get_settings().deck_site_retry_after_cap_seconds for s in site.sleeps)


async def test_429_then_a_server_error_reports_the_server_error() -> None:
    site = Site(
        lambda n, r: (
            httpx.Response(429, headers={"Retry-After": "0"}) if n == 1 else httpx.Response(503)
        )
    )
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.SERVER_ERROR
    assert result.status_code == 503
    assert len(site.requests) == 2


# --- Criterion 4: timeout ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "exception", [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout]
)
async def test_httpx_timeout_is_reported_as_timeout_not_raised(
    exception: type[httpx.TimeoutException],
) -> None:
    def respond(_n: int, request: httpx.Request) -> httpx.Response:
        raise exception("took too long", request=request)

    site = Site(respond)
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.TIMEOUT
    assert result.reason == "timeout"


async def test_response_slower_than_the_configured_timeout_is_a_timeout() -> None:
    async def respond(_n: int, _request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, content=b"late")

    site = Site(respond)
    async with site.client(settings=settings_with(deck_site_timeout_seconds=0.05)) as client:
        result = await asyncio.wait_for(client.fetch(SITE.name, new_deck(), url_for("1")), 1.5)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.TIMEOUT


async def test_body_that_trickles_past_the_total_timeout_is_a_timeout() -> None:
    async def trickle() -> AsyncIterator[bytes]:
        for _ in range(100):
            await asyncio.sleep(0.02)
            yield b"x"

    site = Site(lambda n, r: httpx.Response(200, content=trickle()))
    async with site.client(settings=settings_with(deck_site_timeout_seconds=0.1)) as client:
        result = await asyncio.wait_for(client.fetch(SITE.name, new_deck(), url_for("1")), 1.5)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.TIMEOUT


def test_timeout_defaults_to_ten_seconds_in_settings() -> None:
    assert Settings.model_fields["deck_site_timeout_seconds"].default == 10.0
    assert get_settings().deck_site_timeout_seconds == 10.0


@pytest.mark.parametrize("seconds", [10.0, 3.5])
async def test_configured_timeout_is_applied_to_the_request(seconds: float) -> None:
    site = Site()
    async with site.client(settings=settings_with(deck_site_timeout_seconds=seconds)) as client:
        await client.fetch(SITE.name, new_deck(), url_for("1"))
    timeout = site.requests[0].extensions["timeout"]
    assert timeout == {"connect": seconds, "read": seconds, "write": seconds, "pool": seconds}


async def test_timed_out_fetch_is_not_cached() -> None:
    deck = new_deck()

    def respond(n: int, request: httpx.Request) -> httpx.Response:
        if n == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, content=ok_body(deck))

    site = Site(respond)
    async with site.client() as client:
        first = await client.fetch(SITE.name, deck, url_for("1"))
        assert await stored_entries(deck.encode()) == []
        second = await client.fetch(SITE.name, deck, url_for("1"))
    assert isinstance(first, DeckFetchFailed)
    assert isinstance(second, DeckFetched)
    assert not second.from_cache


# --- Per-site limits and the Archidekt conditions ---------------------------------------------


async def test_requests_in_flight_never_exceed_the_site_concurrency() -> None:
    limit = 3
    site_config = SiteConfig(SITE.name, SITE.hosts, min_interval_ms=100, max_concurrency=limit)
    active = 0
    peak = 0
    saturated = asyncio.Event()
    gate = asyncio.Event()

    async def respond(_n: int, _request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active >= limit:
            saturated.set()
        await gate.wait()
        active -= 1
        return httpx.Response(200, content=b"x")

    site = Site(respond)
    async with site.client(sites={site_config.name: site_config}) as client:
        tasks = [
            asyncio.create_task(client.fetch(SITE.name, new_deck(), url_for(str(i))))
            for i in range(10)
        ]
        try:
            await asyncio.wait_for(saturated.wait(), timeout=5)
            await asyncio.sleep(0.2)  # give an over-limit burst the chance to show itself
            assert active == limit
        finally:
            gate.set()
        results = await asyncio.gather(*tasks)
    assert all(isinstance(r, DeckFetched) for r in results)
    assert peak == limit
    assert len(site.requests) == 10


def test_site_concurrency_default_is_two_and_comes_from_settings() -> None:
    assert Settings.model_fields["deck_site_max_concurrency"].default == 2
    assert default_sites()["archidekt"].max_concurrency == 2
    assert (
        default_sites(settings_with(deck_site_max_concurrency=1))["archidekt"].max_concurrency == 1
    )


async def test_every_request_including_retries_passes_the_site_limiter_first() -> None:
    events: list[str] = []
    built_for: list[str] = []

    class Recording(SpacingLimiter):
        async def wait(self) -> None:
            events.append("wait")

    def factory(site_config: SiteConfig) -> SpacingLimiter:
        built_for.append(site_config.name)
        return Recording("unused", site_config.min_interval_ms)

    def respond(n: int, _request: httpx.Request) -> httpx.Response:
        events.append("request")
        return httpx.Response(429, headers={"Retry-After": "0"}) if n < 3 else ok(n, _request)

    site = Site(respond)
    async with DeckSiteClient(
        sites=TEST_SITES,
        transport=httpx.MockTransport(site.handler),
        limiter_factory=factory,
        sleep=site.sleep,
    ) as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetched)
    assert events == ["wait", "request"] * 3
    assert built_for == [SITE.name]


async def test_cache_hit_does_not_consume_a_limiter_slot() -> None:
    waits = 0

    class Counting(SpacingLimiter):
        async def wait(self) -> None:
            nonlocal waits
            waits += 1

    deck = new_deck()
    site = Site(lambda n, r: httpx.Response(200, content=ok_body(deck)))
    async with DeckSiteClient(
        sites=TEST_SITES,
        transport=httpx.MockTransport(site.handler),
        limiter_factory=lambda s: Counting("unused", 100),
        sleep=site.sleep,
    ) as client:
        await client.fetch(SITE.name, deck, url_for("1"))
        assert waits == 1
        for _ in range(3):
            hit = await client.fetch(SITE.name, deck, url_for("1"))
            assert isinstance(hit, DeckFetched)
            assert hit.from_cache
    assert waits == 1


def test_archidekt_is_registered_and_its_default_rate_is_well_under_forty_a_minute() -> None:
    sites = default_sites()
    assert Settings.model_fields["deck_site_archidekt_min_interval_ms"].default == 3000
    interval = sites["archidekt"].min_interval_ms
    assert interval == get_settings().deck_site_archidekt_min_interval_ms
    assert 60_000 / interval <= 30


def test_archidekt_interval_cannot_be_configured_to_forty_a_minute_or_faster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECK_SITE_ARCHIDEKT_MIN_INTERVAL_MS", "1500")  # 40 a minute
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


async def test_default_archidekt_limiter_reserves_slots_one_configured_interval_apart() -> None:
    """The default limiter reserves Redis slots one configured interval apart for archidekt."""
    site = Site()
    interval = default_sites()["archidekt"].min_interval_ms / 1000
    async with site.client(sites=None, no_wait=False) as client:
        for i in range(3):
            result = await client.fetch("archidekt", new_deck(), ARCHIDEKT_URL.format(i))
            assert isinstance(result, DeckFetched)
    assert len(site.requests) == 3
    assert {r.url.host for r in site.requests} == {"archidekt.com"}
    assert len(site.sleeps) == 2
    assert interval - 0.3 <= site.sleeps[0] <= interval
    assert 2 * interval - 0.3 <= site.sleeps[1] <= 2 * interval


async def test_a_429_retry_also_waits_for_the_spacing_slot() -> None:
    site = Site(
        lambda n, r: httpx.Response(429, headers={"Retry-After": "1"}) if n == 1 else ok(n, r)
    )
    interval = default_sites()["archidekt"].min_interval_ms / 1000
    async with site.client(sites=None, no_wait=False) as client:
        result = await client.fetch("archidekt", new_deck(), ARCHIDEKT_URL.format(1))
    assert isinstance(result, DeckFetched)
    assert len(site.requests) == 2
    assert 1.0 in site.sleeps
    assert max(site.sleeps) >= interval - 0.3  # the retry reserved the next slot


# --- Safety: hosts, redirects, size -----------------------------------------------------------


def test_only_archidekt_is_registered_and_moxfield_is_not() -> None:
    assert set(default_sites()) == {"archidekt"}
    assert default_sites()["archidekt"].hosts == {"archidekt.com"}


async def test_fetching_from_moxfield_raises_a_host_error_without_a_request() -> None:
    site = Site()
    async with site.client(sites=None) as client:
        with pytest.raises(DeckSiteHostError):
            await client.fetch("moxfield", "abc", "https://moxfield.com/decks/abc")
        with pytest.raises(DeckSiteHostError):
            await client.fetch("moxfield", "abc", "https://api2.moxfield.com/v3/decks/all/abc")
    assert site.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/api/decks/1/",
        "http://archidekt.com/api/decks/1/",
        "https://evilarchidekt.com/api/decks/1/",
        "https://archidekt.com.evil.test/api/decks/1/",
        "https://archidekt.com@evil.test/api/decks/1/",
        "https://user:pass@archidekt.com/api/decks/1/",  # secrets-check: allow
        "https://user@archidekt.com/api/decks/1/",
        "https://archidekt.com:8443/api/decks/1/",
        "https://archidekt.com:22/api/decks/1/",
        "ftp://archidekt.com/api/decks/1/",
        "file:///etc/passwd",
        "//archidekt.com/api/decks/1/",
        "archidekt.com/api/decks/1/",
        "",
    ],
)
async def test_url_outside_the_registered_site_is_refused_before_any_request(url: str) -> None:
    site = Site()
    async with site.client(sites=None) as client:
        with pytest.raises(DeckSiteHostError):
            await client.fetch("archidekt", new_deck(), url)
    assert site.requests == []


async def test_unregistered_site_name_is_refused_before_any_request() -> None:
    site = Site()
    async with site.client(sites=None) as client:
        with pytest.raises(DeckSiteHostError):
            await client.fetch("evilsite", new_deck(), ARCHIDEKT_URL.format(1))
    assert site.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test\\@archidekt.com/api/decks/1/",
        "https://evil.test#@archidekt.com/api/decks/1/",
        "https://evil.test?@archidekt.com/api/decks/1/",
        "https://archidekt.com\\@evil.test/api/decks/1/",
        "https://archidekt.com%2f@evil.test/api/decks/1/",
        "https://archidekt.com\t.evil.test/",
    ],
)
async def test_parser_confusion_urls_never_send_a_request_to_another_host(url: str) -> None:
    """Either refused, or every request that goes out is for archidekt.com with no userinfo."""
    site = Site()
    async with site.client(sites=None) as client:
        with contextlib.suppress(DeckSiteHostError, ValueError, httpx.InvalidURL):
            await client.fetch("archidekt", new_deck(), url)
    for request in site.requests:
        assert request.url.host == "archidekt.com"
        assert not request.url.userinfo
        assert request.url.port is None


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("location", ["https://evil.test/steal", "https://archidekt.com/other"])
async def test_redirects_are_not_followed_and_are_a_failure_result(
    status: int, location: str
) -> None:
    site = Site(lambda n, r: httpx.Response(status, headers={"Location": location}))
    async with site.client(sites=None) as client:
        result = await client.fetch("archidekt", new_deck(), ARCHIDEKT_URL.format(1))
    assert isinstance(result, DeckFetchFailed)
    assert result.status_code == status
    assert [r.url.host for r in site.requests] == ["archidekt.com"]


async def test_body_declared_larger_than_the_cap_is_too_large_unread_and_not_cached() -> None:
    cap = 1024
    deck = new_deck()
    yielded = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal yielded
        for _ in range(500):
            yielded += 1000
            yield b"x" * 1000

    site = Site(
        lambda n, r: httpx.Response(200, headers={"Content-Length": str(500_000)}, content=body())
    )
    async with site.client(settings=settings_with(deck_site_max_response_bytes=cap)) as client:
        result = await client.fetch(SITE.name, deck, url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.TOO_LARGE
    assert yielded <= cap + 1000
    assert await stored_entries(b"x" * 100) == []


async def test_undeclared_streamed_body_over_the_cap_stops_reading_early_and_is_not_cached() -> (
    None
):
    cap = 1024
    yielded = 0

    async def body() -> AsyncIterator[bytes]:
        nonlocal yielded
        for _ in range(1000):
            yielded += 1000
            yield b"y" * 1000

    site = Site(lambda n, r: httpx.Response(200, content=body()))
    async with site.client(settings=settings_with(deck_site_max_response_bytes=cap)) as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.TOO_LARGE
    assert yielded <= cap + 2000  # far from the 1 MB the site would have sent
    assert await stored_entries(b"y" * 100) == []


async def test_body_of_exactly_the_cap_is_accepted_and_one_byte_more_is_not() -> None:
    cap = 2048
    exact = Site(lambda n, r: httpx.Response(200, content=b"z" * cap))
    over = Site(lambda n, r: httpx.Response(200, content=b"z" * (cap + 1)))
    settings = settings_with(deck_site_max_response_bytes=cap)
    async with exact.client(settings=settings) as client:
        accepted = await client.fetch(SITE.name, new_deck(), url_for("1"))
    async with over.client(settings=settings) as client:
        refused = await client.fetch(SITE.name, new_deck(), url_for("2"))
    assert isinstance(accepted, DeckFetched)
    assert len(accepted.body) == cap
    assert isinstance(refused, DeckFetchFailed)
    assert refused.reason is DeckFetchFailure.TOO_LARGE


def test_size_cap_defaults_to_five_megabytes() -> None:
    assert Settings.model_fields["deck_site_max_response_bytes"].default == 5_000_000


# --- Never raise: every failure the import job cares about is a result with a stable reason ----


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (429, "rate_limited"),
        (403, "blocked"),
        (404, "not_found"),
        (500, "server_error"),
        (502, "server_error"),
        (503, "server_error"),
        (504, "server_error"),
    ],
)
async def test_http_failure_is_a_result_with_the_stable_reason(status: int, reason: str) -> None:
    site = Site(always(status, **{"Retry-After": "0"}))
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason == reason
    assert result.status_code == status


@pytest.mark.parametrize("status", [400, 401, 405, 410, 418, 301, 302, 304])
async def test_other_statuses_are_failure_results_not_exceptions(status: int) -> None:
    site = Site(always(status))
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert isinstance(result.reason, DeckFetchFailure)
    assert result.status_code == status


@pytest.mark.parametrize(
    "exception", [httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError]
)
async def test_network_error_is_a_failure_result_not_an_exception(
    exception: type[httpx.TransportError],
) -> None:
    def respond(_n: int, request: httpx.Request) -> httpx.Response:
        raise exception("boom", request=request)

    site = Site(respond)
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason == "network_error"


async def test_connection_dropping_mid_body_is_a_failure_result_not_an_exception() -> None:
    async def body() -> AsyncIterator[bytes]:
        yield b"partial"
        raise httpx.ReadError("connection reset")

    site = Site(lambda n, r: httpx.Response(200, content=body()))
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetchFailed)
    assert result.reason == "network_error"


def test_failure_reasons_are_the_stable_snake_case_names() -> None:
    assert {r.value for r in DeckFetchFailure} == {
        "rate_limited",
        "timeout",
        "blocked",
        "not_found",
        "server_error",
        "too_large",
        "network_error",
        "unexpected_response",
    }


async def test_a_200_with_any_body_is_returned_raw_for_the_mappers_to_judge() -> None:
    """The client does not parse; a non-JSON 200 comes back as a fetched body (reported)."""
    site = Site(lambda n, r: httpx.Response(200, content=b"<html>not json</html>"))
    async with site.client() as client:
        result = await client.fetch(SITE.name, new_deck(), url_for("1"))
    assert isinstance(result, DeckFetched)
    assert result.body == b"<html>not json</html>"


# --- Architecture: no router reaches the client -----------------------------------------------


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def _flagged(root: Path) -> set[str]:
    return {line.split(":")[0] for line in router_violations(root)}


def test_no_router_in_the_app_reaches_the_deck_site_client() -> None:
    assert router_violations(APP_ROOT) == []


def test_synthetic_router_importing_the_deck_site_client_is_flagged(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "decks/services/deck_sites/http.py": "",
            "a/router.py": "from app.decks.services import DeckSiteClient\n",
            "b/router.py": "from app.decks.services.deck_sites import DeckSiteClient\n",
            "c/router.py": "from app.decks.services.deck_sites.http import DeckSiteClient\n",
            "d/router.py": "import app.decks.services.deck_sites\n",
            "e/router.py": "from app.decks.services import deck_sites\n",
            "f/router.py": "from app.decks import services\nclient = services.DeckSiteClient()\n",
            "g/router.py": "from app.decks.services.deck_sites.cache import DeckCache\n",
        },
    )
    assert _flagged(root) == {f"{x}/router.py" for x in "abcdefg"}


def test_synthetic_router_importing_parse_decklist_is_not_flagged(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "decks/router.py": "from app.decks.services import parse_decklist\n",
            "other/router.py": "from app.decks.services import Board, parse_decklist\n",
        },
    )
    assert router_violations(root) == []


def test_synthetic_router_module_not_named_router_is_flagged_and_a_job_is_not(
    tmp_path: Path,
) -> None:
    root = _write(
        tmp_path,
        {
            "decks/endpoints.py": (
                "from fastapi import APIRouter\nfrom app.decks.services import DeckSiteClient\n"
            ),
            "workers/import_deck.py": "from app.decks.services import DeckSiteClient\n",
        },
    )
    assert _flagged(root) == {"decks/endpoints.py"}
