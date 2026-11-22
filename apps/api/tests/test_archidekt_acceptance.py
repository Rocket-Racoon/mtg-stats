"""Acceptance tests of S-4.3.1 (fetch Archidekt decks).

Written from the story's criteria and the product decisions, not from the implementation. Nothing
uses the network: ``httpx.MockTransport`` replaces Archidekt and ``sleep`` is injected. Redis is the
test Redis (every test has its own key prefix, see ``conftest.rate_limit_prefix``).

Tests with ``UNVERIFIED_SHAPE`` in the name document the current behaviour for private-deck answers
that were never observed on the live site (2026-11-21 probe: only 200 public and 404 were seen).
They are not criterion coverage; see the report's open questions.
"""

import inspect
import json
import re
import tomllib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.core.redis import get_redis
from app.core.spacing_limiter import SpacingLimiter
from app.decks.services import (
    ArchidektUrlError,
    DeckFetched,
    DeckFetchFailed,
    DeckFetchFailure,
    DeckSiteClient,
    archidekt_deck_url,
    fetch_archidekt_deck,
    parse_archidekt_url,
)
from app.decks.services.deck_sites import archidekt as archidekt_module
from app.decks.services.deck_sites.cache import cache_key
from tests.architecture.test_domain_imports import APP_ROOT
from tests.architecture.test_routers_never_call_scryfall import router_violations

API_DIR = Path(__file__).resolve().parents[1]
DATA = API_DIR / "app" / "decks" / "tests" / "data"
MESSAGE = "not an Archidekt deck URL"
CODE = "not_archidekt_deck_url"
JSON_HEADERS = {"Content-Type": "application/json"}

Handler = Callable[[httpx.Request], httpx.Response]


def recorded(name: str) -> bytes:
    """A recorded sample without its leading ``# `` comment lines (own loader)."""
    lines = (DATA / name).read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(line for line in lines if not line.startswith("# ")).encode("utf-8")


def header_of(name: str) -> str:
    lines = (DATA / name).read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if line.startswith("# "))


def declared_version() -> str:
    data = tomllib.loads((API_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def fresh_id() -> int:
    """A deck id nobody else's test uses (cache keys are per test prefix anyway)."""
    return 2_000_000 + uuid.uuid4().int % 100_000_000


def deck_body(deck_id: int, **extra: Any) -> bytes:
    payload: dict[str, Any] = {"id": deck_id, "name": "x", "private": False, "cards": [], **extra}
    return json.dumps(payload).encode()


class Archidekt:
    """A scripted Archidekt: records every request and sleep."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.limiter_waits = 0

    def _serve(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def client(self) -> DeckSiteClient:
        site = self

        class CountingLimiter(SpacingLimiter):
            async def wait(self) -> None:
                site.limiter_waits += 1

        return DeckSiteClient(
            transport=httpx.MockTransport(self._serve),
            limiter_factory=lambda cfg: CountingLimiter("unused", cfg.min_interval_ms),
            sleep=self._sleep,
        )


def respond(status: int, body: bytes, **headers: str) -> Handler:
    merged = {**JSON_HEADERS, **headers}
    return lambda _r: httpx.Response(status, content=body, headers=merged)


async def cached(deck_id: int) -> bytes | None:
    raw: bytes | None = await get_redis().get(cache_key("archidekt", str(deck_id)))
    return raw


# --- Criterion 1: parsing ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://archidekt.com/decks/1", 1),
        ("https://archidekt.com/decks/2000000000", 2000000000),
        ("https://archidekt.com/decks/123456/my-deck", 123456),
        ("https://archidekt.com/decks/9876543/a-b-c-d-e", 9876543),
        ("https://archidekt.com/decks/42/Atraxa%2C%20Praetors%27%20Voice", 42),
        ("https://archidekt.com/decks/42/%E2%9C%A8-sparkle", 42),
        ("https://archidekt.com/decks/42/Ünïcödé-dęck", 42),
        ("https://archidekt.com/decks/2147483647/edge", 2147483647),
    ],
)
def test_deck_url_yields_the_numeric_id_as_an_int(url: str, expected: int) -> None:
    result = parse_archidekt_url(url)
    assert result == expected
    assert type(result) is int


def test_slug_is_optional() -> None:
    assert parse_archidekt_url("https://archidekt.com/decks/77") == 77
    assert parse_archidekt_url("https://archidekt.com/decks/77/some-slug") == 77


@pytest.mark.parametrize(
    "url",
    [
        "https://www.archidekt.com/decks/55/slug",
        "https://archidekt.com/decks/55/slug/",
        "https://archidekt.com/decks/55/slug?utm_source=x&ref=1",
        "https://archidekt.com/decks/55/slug#primer",
        "https://archidekt.com/decks/55?x=1",
        "HTTPS://ARCHIDEKT.COM/decks/55/slug",
        "  https://archidekt.com/decks/55/slug  ",
    ],
)
def test_tolerated_variants_do_not_misparse_the_id(url: str) -> None:
    """Observation: the implementation tolerates these; the criterion only requires the id."""
    assert parse_archidekt_url(url) == 55


REJECTED = [
    # other hosts and look-alikes
    "https://example.com/decks/1/x",
    "https://archidekt.com.evil.test/decks/1/x",
    "https://evilarchidekt.com/decks/1/x",
    "https://archidekt.co/decks/1/x",
    "https://archidekt.com@evil.test/decks/1/x",
    "https://evil.test/archidekt.com/decks/1/x",
    "https://sub.archidekt.com/decks/1/x",
    "https://archidekt.com./decks/1/x",
    # Moxfield (deferred) and its look-alike shape
    "https://www.moxfield.com/decks/abc123XYZ",
    "https://moxfield.com/decks/abc123XYZ",
    # scheme
    "http://archidekt.com/decks/1/x",
    "ftp://archidekt.com/decks/1/x",
    "//archidekt.com/decks/1/x",
    "archidekt.com/decks/1/x",
    # credentials and ports
    "https://user@archidekt.com/decks/1/x",
    "https://user:" + "word@archidekt.com/decks/1/x",
    "https://archidekt.com:8443/decks/1/x",
    "https://archidekt.com:443/decks/1/x",
    # other paths
    "https://archidekt.com/u/someone",
    "https://archidekt.com/api/decks/1/",
    "https://archidekt.com/collection/1",
    "https://archidekt.com/folders/1",
    "https://archidekt.com/decks/",
    "https://archidekt.com/decks",
    "https://archidekt.com/decks//slug",
    "https://archidekt.com/decks/1/slug/extra",
    "https://archidekt.com/",
    "https://archidekt.com",
    "https://archidekt.com/Decks/1",
    "https://archidekt.com/decks/1/../2",
    # ids
    "https://archidekt.com/decks/0",
    "https://archidekt.com/decks/0/x",
    "https://archidekt.com/decks/-5/x",
    "https://archidekt.com/decks/abc/x",
    "https://archidekt.com/decks/12abc/x",
    "https://archidekt.com/decks/1.5/x",
    "https://archidekt.com/decks/007/x",
    "https://archidekt.com/decks/00/x",
    "https://archidekt.com/decks/١٢٣/x",  # Arabic-Indic digits
    "https://archidekt.com/decks/" + chr(0xFF11) + chr(0xFF12) + "/x",  # fullwidth digits
    "https://archidekt.com/decks/" + "9" * 40 + "/x",
    "https://archidekt.com/decks/2147483648/x",
    "https://archidekt.com/decks/%31%32/x",  # percent-encoded digits are not an id
    # empty and bare
    "",
    "   ",
    "12345",
    "decks/12345",
    # whitespace, control characters, backslashes inside
    "https://archidekt.com/decks/1/a b",
    "https://archidekt.com/decks/1/a\tb",
    "https://archidekt.com/decks/1/a\nb",
    "https://archidekt.com/decks/1/a\rb",
    "https://archidekt.com/decks/1/a\x00b",
    "https://archidekt.com/decks/1/a\x7fb",
    "https://archidekt.com\\decks\\1",
    "https://archidekt.com/decks/1\\x",
    "https://arch idekt.com/decks/1",
    "https://archidekt.com/decks/1\n\nhttps://evil.test",
]


@pytest.mark.parametrize("url", REJECTED)
def test_other_shapes_are_rejected_with_the_typed_error(url: str) -> None:
    with pytest.raises(ArchidektUrlError) as caught:
        parse_archidekt_url(url)
    assert str(caught.value) == MESSAGE
    assert caught.value.code == CODE
    assert isinstance(caught.value, ValueError)


def test_error_message_and_code_are_exact() -> None:
    error = ArchidektUrlError()
    assert str(error) == "not an Archidekt deck URL"
    assert error.code == "not_archidekt_deck_url"


def test_a_malformed_ipv6_host_is_rejected_not_crashed() -> None:
    with pytest.raises(ArchidektUrlError):
        parse_archidekt_url("https://[archidekt.com/decks/1")


# --- link back ---------------------------------------------------------------------------------


@pytest.mark.parametrize("deck_id", [1, 2000000000, 2147483647])
def test_link_back_is_the_public_deck_page_and_round_trips(deck_id: int) -> None:
    link = archidekt_deck_url(deck_id)
    assert link == f"https://archidekt.com/decks/{deck_id}"
    assert parse_archidekt_url(link) == deck_id


@pytest.mark.parametrize("bad", [0, -1, 2147483648, True])
def test_link_back_refuses_a_non_id(bad: int) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        archidekt_deck_url(bad)


# --- Criterion 2: shared client ----------------------------------------------------------------


async def test_public_deck_is_requested_on_the_archidekt_api_path_with_the_exact_user_agent() -> (
    None
):
    deck_id = fresh_id()
    site = Archidekt(respond(200, deck_body(deck_id)))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
    assert isinstance(result, DeckFetched)
    assert json.loads(result.body)["id"] == deck_id
    assert len(site.requests) == 1
    request = site.requests[0]
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "archidekt.com"
    assert request.url.path == f"/api/decks/{deck_id}/"
    assert request.url.query == b""
    expected = f"MTGStats/{declared_version()} (+{get_settings().repo_url})"
    assert request.headers["User-Agent"] == expected


async def test_outbound_url_is_built_from_the_id_alone_never_from_the_user_text() -> None:
    deck_id = fresh_id()
    messy = (
        f"https://archidekt.com/decks/{deck_id}/Evil%20Slug%3Cscript%3E"
        "?utm_source=tracker&ref=SECRETREF#frag-ment"
    )
    parsed = parse_archidekt_url(messy)
    assert parsed == deck_id
    site = Archidekt(respond(200, deck_body(deck_id)))
    async with site.client() as client:
        await fetch_archidekt_deck(client, parsed)
    seen = str(site.requests[0].url) + repr(dict(site.requests[0].headers))
    for fragment in ("Evil", "script", "utm_source", "tracker", "SECRETREF", "frag", "%3C"):
        assert fragment not in seen
    assert str(site.requests[0].url) == f"https://archidekt.com/api/decks/{deck_id}/"


async def test_second_fetch_within_the_ttl_is_served_from_the_cache_without_a_request() -> None:
    deck_id = fresh_id()
    body = deck_body(deck_id, name="cached")
    site = Archidekt(respond(200, body))
    async with site.client() as client:
        first = await fetch_archidekt_deck(client, deck_id)
        second = await fetch_archidekt_deck(client, deck_id)
    assert isinstance(first, DeckFetched)
    assert isinstance(second, DeckFetched)
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.body == first.body == body
    assert len(site.requests) == 1


async def test_cache_is_per_deck_id() -> None:
    first_id, second_id = fresh_id(), fresh_id() + 1
    site = Archidekt(lambda r: respond(200, deck_body(int(r.url.path.split("/")[3])))(r))
    async with site.client() as client:
        await fetch_archidekt_deck(client, first_id)
        other = await fetch_archidekt_deck(client, second_id)
    assert isinstance(other, DeckFetched)
    assert json.loads(other.body)["id"] == second_id
    assert len(site.requests) == 2


async def test_429_with_retry_after_is_retried_by_the_client_with_the_injected_sleep() -> None:
    deck_id = fresh_id()

    def handler(request: httpx.Request) -> httpx.Response:
        if len(site.requests) < 3:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, content=deck_body(deck_id), headers=JSON_HEADERS)

    site = Archidekt(handler)
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
    assert isinstance(result, DeckFetched)
    assert len(site.requests) == 3  # first try plus two retries
    assert site.sleeps == [3.0, 3.0]


async def test_429_is_retried_at_most_twice_then_rate_limited() -> None:
    deck_id = fresh_id()
    site = Archidekt(lambda _r: httpx.Response(429, headers={"Retry-After": "1"}))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.RATE_LIMITED
    assert result.status_code == 429
    assert len(site.requests) == 3
    assert site.sleeps == [1.0, 1.0]
    assert await cached(deck_id) is None


async def test_every_request_passes_the_archidekt_spacing_limiter() -> None:
    deck_id = fresh_id()

    def handler(_r: httpx.Request) -> httpx.Response:
        if len(site.requests) < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, content=deck_body(deck_id), headers=JSON_HEADERS)

    site = Archidekt(handler)
    async with site.client() as client:
        await fetch_archidekt_deck(client, deck_id)
    assert site.limiter_waits == 3  # one wait before each of the three requests


async def test_archidekt_spacing_stays_well_under_40_requests_per_minute() -> None:
    """The product condition: at most 40 per minute; the configured spacing must be above 1.5 s."""
    assert get_settings().deck_site_archidekt_min_interval_ms >= 1500


def test_fetcher_builds_no_http_client_of_its_own() -> None:
    source = inspect.getsource(archidekt_module)
    assert not re.search(r"httpx\.(Async)?Client|httpx\.(get|post|request)\b", source)
    assert "import requests" not in source
    assert "urllib.request" not in source
    assert "aiohttp" not in source
    signature = inspect.signature(fetch_archidekt_deck)
    assert list(signature.parameters) == ["client", "deck_id"]


async def test_fetcher_makes_no_request_for_an_invalid_id() -> None:
    site = Archidekt(respond(200, b"{}"))
    async with site.client() as client:
        for bad in (0, -1, 2**31):
            with pytest.raises(ValueError):  # noqa: PT011
                await fetch_archidekt_deck(client, bad)
    assert site.requests == []


# --- Criterion 3: not found, nothing stored ----------------------------------------------------


async def test_deleted_deck_404_with_the_recorded_body_is_not_found() -> None:
    deck_id = fresh_id()
    site = Archidekt(respond(404, recorded("archidekt_not_found.txt")))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.NOT_FOUND
    assert result.reason == "not_found"
    assert result.status_code == 404
    assert result.site == "archidekt"
    assert result.deck_id == str(deck_id)
    assert not isinstance(result, DeckFetched)
    assert not hasattr(result, "body")


async def test_not_found_is_not_cached_and_the_next_fetch_goes_out_again() -> None:
    deck_id = fresh_id()
    site = Archidekt(respond(404, recorded("archidekt_not_found.txt")))
    async with site.client() as client:
        await fetch_archidekt_deck(client, deck_id)
        assert await cached(deck_id) is None
        again = await fetch_archidekt_deck(client, deck_id)
    assert isinstance(again, DeckFetchFailed)
    assert len(site.requests) == 2


async def test_a_private_deck_that_answers_404_is_not_found() -> None:
    """Private is indistinguishable from deleted on the observed 404, so it is the same case."""
    deck_id = fresh_id()
    site = Archidekt(respond(404, b'{"error":"Deck not found."}'))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.NOT_FOUND
    assert await cached(deck_id) is None


async def test_410_is_not_found_and_not_cached() -> None:
    deck_id = fresh_id()
    site = Archidekt(respond(410, b""))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
        await fetch_archidekt_deck(client, deck_id)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.NOT_FOUND
    assert len(site.requests) == 2


# UNVERIFIED-SHAPE: documents current behaviour; the live site's private answer was not observed.


async def test_unverified_shape_200_with_private_true_is_not_found_and_not_cached() -> None:
    deck_id = fresh_id()
    body = json.dumps({"id": deck_id, "private": True, "name": "secret", "cards": []}).encode()
    site = Archidekt(respond(200, body))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
        assert await cached(deck_id) is None
        await fetch_archidekt_deck(client, deck_id)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.NOT_FOUND
    assert len(site.requests) == 2


@pytest.mark.parametrize("payload", [{"error": "Deck is private."}, {"detail": "Not found."}])
async def test_unverified_shape_200_with_error_or_detail_and_no_cards_is_not_found(
    payload: dict[str, str],
) -> None:
    deck_id = fresh_id()
    site = Archidekt(respond(200, json.dumps(payload).encode()))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
        assert await cached(deck_id) is None
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.NOT_FOUND


@pytest.mark.parametrize("status", [401, 403])
async def test_unverified_shape_401_403_come_back_as_blocked_not_not_found(status: int) -> None:
    deck_id = fresh_id()
    site = Archidekt(respond(status, b'{"detail":"nope"}'))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.BLOCKED
    assert result.status_code == status
    assert await cached(deck_id) is None


# --- "No partial data is stored" ---------------------------------------------------------------


async def test_non_success_outcomes_return_a_failure_and_store_nothing() -> None:
    cases: dict[str, tuple[Handler, DeckFetchFailure]] = {
        "404": (respond(404, b'{"error":"Deck not found."}'), DeckFetchFailure.NOT_FOUND),
        "410": (respond(410, b""), DeckFetchFailure.NOT_FOUND),
        "401": (respond(401, b""), DeckFetchFailure.BLOCKED),
        "403": (respond(403, b""), DeckFetchFailure.BLOCKED),
        "500": (respond(500, b"boom"), DeckFetchFailure.SERVER_ERROR),
        "503": (respond(503, b"boom"), DeckFetchFailure.SERVER_ERROR),
        "302": (
            lambda _r: httpx.Response(302, headers={"Location": "https://evil.test/"}),
            DeckFetchFailure.UNEXPECTED_RESPONSE,
        ),
    }
    for name, (handler, reason) in cases.items():
        deck_id = fresh_id()
        site = Archidekt(handler)
        async with site.client() as client:
            first = await fetch_archidekt_deck(client, deck_id)
            assert await cached(deck_id) is None, name
            await fetch_archidekt_deck(client, deck_id)
        assert isinstance(first, DeckFetchFailed), name
        assert first.reason is reason, name
        assert len(site.requests) == 2, name  # a redirect was not followed either


async def test_timeout_returns_timeout_and_stores_nothing() -> None:
    deck_id = fresh_id()

    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    site = Archidekt(handler)
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
        assert await cached(deck_id) is None
        await fetch_archidekt_deck(client, deck_id)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.TIMEOUT
    assert len(site.requests) == 2


async def test_connection_error_returns_network_error_and_stores_nothing() -> None:
    deck_id = fresh_id()

    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    site = Archidekt(handler)
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.NETWORK_ERROR
    assert await cached(deck_id) is None


async def test_oversize_body_is_too_large_and_stores_nothing() -> None:
    deck_id = fresh_id()
    limit = get_settings().deck_site_max_response_bytes
    filler = "x" * (limit + 10)
    body = json.dumps({"id": deck_id, "cards": [], "description": filler}).encode()
    site = Archidekt(respond(200, body))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
        assert await cached(deck_id) is None
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.TOO_LARGE


async def test_declared_content_length_above_the_cap_is_too_large() -> None:
    deck_id = fresh_id()
    limit = get_settings().deck_site_max_response_bytes
    site = Archidekt(respond(200, b"{}", **{"Content-Length": str(limit + 1)}))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.TOO_LARGE


@pytest.mark.parametrize(
    "body",
    [b"[]", b'"deck"', b"42", b"null", b'{"hello": "world"}', b"{}", b'{"id": 1, "cards": null}'],
    ids=["list", "string", "number", "null", "other-object", "empty-object", "cards-null"],
)
async def test_200_json_that_is_not_a_deck_is_never_cached(body: bytes) -> None:
    """Observation: such a body is returned as DeckFetched (the mapper judges it), not stored."""
    deck_id = fresh_id()
    site = Archidekt(respond(200, body))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
        assert await cached(deck_id) is None
        again = await fetch_archidekt_deck(client, deck_id)
    assert len(site.requests) == 2
    assert isinstance(result, DeckFetched)
    assert isinstance(again, DeckFetched)
    assert result.from_cache is False
    assert result.body == body


async def test_200_deck_with_another_ids_is_unexpected_response_and_never_cached() -> None:
    deck_id = fresh_id()
    site = Archidekt(respond(200, deck_body(deck_id + 1, name="someone else's deck")))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
        assert await cached(deck_id) is None
        assert await cached(deck_id + 1) is None
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.UNEXPECTED_RESPONSE


async def test_200_deck_with_a_string_id_is_not_cached_as_a_deck() -> None:
    deck_id = fresh_id()
    body = json.dumps({"id": str(deck_id), "cards": []}).encode()
    site = Archidekt(respond(200, body))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
        assert await cached(deck_id) is None
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.UNEXPECTED_RESPONSE


async def test_truncated_json_is_returned_for_the_mapper_but_never_cached() -> None:
    deck_id = fresh_id()
    full = deck_body(deck_id)
    truncated = full[: len(full) // 2]
    site = Archidekt(respond(200, truncated))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
        assert await cached(deck_id) is None
        await fetch_archidekt_deck(client, deck_id)
    assert len(site.requests) == 2
    assert isinstance(result, DeckFetched)
    assert result.body == truncated
    with pytest.raises(ValueError):  # noqa: PT011
        json.loads(result.body)


async def test_html_page_with_200_is_returned_for_the_mapper_but_never_cached() -> None:
    deck_id = fresh_id()
    page = b"<html><body>Maintenance</body></html>"
    site = Archidekt(respond(200, page, **{"Content-Type": "text/html; charset=utf-8"}))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, deck_id)
        assert await cached(deck_id) is None
        await fetch_archidekt_deck(client, deck_id)
    assert len(site.requests) == 2
    assert isinstance(result, DeckFetched)
    assert result.content_type.startswith("text/html")
    assert result.body == page


async def test_html_page_that_contains_valid_deck_json_is_still_not_cached() -> None:
    deck_id = fresh_id()
    site = Archidekt(respond(200, deck_body(deck_id), **{"Content-Type": "text/html"}))
    async with site.client() as client:
        await fetch_archidekt_deck(client, deck_id)
    assert await cached(deck_id) is None


async def test_a_good_deck_is_cached_under_its_own_id_only() -> None:
    deck_id = fresh_id()
    site = Archidekt(respond(200, deck_body(deck_id)))
    async with site.client() as client:
        await fetch_archidekt_deck(client, deck_id)
    assert await cached(deck_id) is not None
    assert await cached(deck_id + 1) is None


# --- the recorded public sample ---------------------------------------------------------------


def test_recorded_public_sample_is_a_deck_json_for_the_id_in_its_header() -> None:
    data = json.loads(recorded("archidekt_public_deck.txt"))
    assert isinstance(data["cards"], list)
    assert data["cards"]
    assert data["private"] is False
    assert data["id"] == 2000000000
    assert "archidekt.com/api/decks/2000000000/" in header_of("archidekt_public_deck.txt")


def test_recorded_public_sample_has_placeholders_and_no_personal_data() -> None:
    raw = recorded("archidekt_public_deck.txt").decode("utf-8")
    data = json.loads(raw)
    assert data["name"] == "Sample deck name"
    assert data["description"] == "Sample deck description"
    assert data["owner"]["username"] == "sample-owner"
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", raw), "looks like an email"
    assert not re.search(r"avatars?/|\.(png|jpe?g|webp|gif)\b.*avatar|gravatar", raw, re.I)
    assert "avatar" not in {k.lower() for k in data["owner"]} or not data["owner"].get("avatar")
    usernames: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in {"username", "user_name", "owner_name", "ownername"}:
                    usernames.add(str(value))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    assert usernames <= {"sample-owner"}


def test_recorded_public_sample_header_states_recorded_versus_edited() -> None:
    header = header_of("archidekt_public_deck.txt")
    assert "recorded 2026-11-21" in header
    assert "Status 200" in header
    assert "application/json" in header
    assert "sanitised" in header
    assert "CONSTRUCTED, not recorded" in header
    assert "Trimmed" in header


def test_recorded_not_found_sample_header_and_body() -> None:
    header = header_of("archidekt_not_found.txt")
    assert "status 404" in header
    assert "application/json" in header
    assert json.loads(recorded("archidekt_not_found.txt")) == {"error": "Deck not found."}


async def test_recorded_public_sample_fetches_as_a_deck_and_is_cached() -> None:
    body = recorded("archidekt_public_deck.txt")
    site = Archidekt(respond(200, body))
    async with site.client() as client:
        result = await fetch_archidekt_deck(client, 2000000000)
    assert isinstance(result, DeckFetched)
    assert result.body == body
    assert await cached(2000000000) is not None


# --- architecture and spec ---------------------------------------------------------------------


def test_the_archidekt_fetcher_module_is_not_flagged_in_the_real_tree() -> None:
    assert router_violations(APP_ROOT) == []


def test_router_importing_the_archidekt_module_directly_is_reported(tmp_path: Path) -> None:
    target = tmp_path / "decks" / "services" / "deck_sites"
    target.mkdir(parents=True)
    (target / "archidekt.py").write_text("", encoding="utf-8")
    router = tmp_path / "decks" / "router.py"
    router.write_text(
        "from app.decks.services.deck_sites.archidekt import fetch_archidekt_deck\n",
        encoding="utf-8",
    )
    assert router_violations(tmp_path) != []


def test_router_importing_the_fetcher_through_the_services_facade_is_reported(
    tmp_path: Path,
) -> None:
    """The facade re-export is the public interface; a router taking the fetcher from it is a
    router reaching an outbound client. The rule is name-based, so this documents coverage."""
    router = tmp_path / "decks" / "router.py"
    router.parent.mkdir(parents=True)
    router.write_text("from app.decks.services import fetch_archidekt_deck\n", encoding="utf-8")
    assert router_violations(tmp_path) != []
