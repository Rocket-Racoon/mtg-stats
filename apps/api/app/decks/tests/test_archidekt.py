"""Unit tests of the Archidekt URL parser and fetcher (S-4.3.1); no network, ``MockTransport``."""

import json
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.core.errors import DomainError
from app.core.redis import get_redis
from app.core.spacing_limiter import SpacingLimiter
from app.core.user_agent import build_user_agent
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
from app.decks.services.deck_sites.archidekt import MAX_JSON_DEPTH, _nested_too_deep
from app.decks.services.deck_sites.cache import cache_key

DATA = Path(__file__).parent / "data"
Handler = Callable[[httpx.Request], httpx.Response]


def _recorded(name: str) -> bytes:
    """A recorded body: the data file without its leading ``# `` comment lines."""
    lines = (DATA / name).read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(line for line in lines if not line.startswith("# ")).encode("utf-8")


async def _nosleep(seconds: float) -> None:
    return None


def _client(handler: Handler) -> DeckSiteClient:
    return DeckSiteClient(
        transport=httpx.MockTransport(handler),
        limiter_factory=lambda site: SpacingLimiter(
            f"test:decksite:{uuid.uuid4().hex}", site.min_interval_ms, sleep=_nosleep
        ),
        sleep=_nosleep,
    )


def _json(status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )


async def _cached(deck_id: int) -> bytes | None:
    raw: bytes | None = await get_redis().get(cache_key("archidekt", str(deck_id)))
    return raw


# ---- parser ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "deck_id"),
    [
        ("https://archidekt.com/decks/2000000000", 2000000000),
        ("https://archidekt.com/decks/2000000000/", 2000000000),
        ("https://archidekt.com/decks/2000000000/sample-deck-name", 2000000000),
        ("https://archidekt.com/decks/2000000000/sample-deck-name/", 2000000000),
        ("https://www.archidekt.com/decks/7", 7),
        ("https://archidekt.com/decks/2000000000?tab=primer", 2000000000),
        ("https://archidekt.com/decks/2000000000/slug?x=1#cards", 2000000000),
        ("https://archidekt.com/decks/2000000000#top", 2000000000),
        ("https://ARCHIDEKT.com/decks/2000000000", 2000000000),
        ("HTTPS://archidekt.com/decks/2000000000", 2000000000),
        ("  https://archidekt.com/decks/2000000000\n", 2000000000),
        ("https://archidekt.com/decks/2147483647", 2147483647),
        ("https://archidekt.com/decks/1/%C3%A9l%C3%A9na", 1),
    ],
)
def test_parser_extracts_the_numeric_deck_id(url: str, deck_id: int) -> None:
    assert parse_archidekt_url(url) == deck_id


@pytest.mark.parametrize(
    "url",
    [
        "",
        "archidekt.com/decks/1",
        "http://archidekt.com/decks/1",
        "ftp://archidekt.com/decks/1",
        "https://archidekt.com.evil.test/decks/1",
        "https://evilarchidekt.com/decks/1",
        "https://sub.archidekt.com/decks/1",
        "https://archidekt.com./decks/1",
        "https://user@archidekt.com/decks/1",
        "https://user:pw@archidekt.com/decks/1",  # secrets-check: allow
        "https://archidekt.com@evil.test/decks/1",
        "https://evil.test/archidekt.com/decks/1",
        "https://archidekt.com:8443/decks/1",
        "https://archidekt.com:443/decks/1",
        "https://www.moxfield.com/decks/abc123",
        "https://archidekt.com/",
        "https://archidekt.com/decks",
        "https://archidekt.com/decks/",
        "https://archidekt.com/decks/abc",
        "https://archidekt.com/decks/0",
        "https://archidekt.com/decks/-5",
        "https://archidekt.com/decks/007",
        "https://archidekt.com/decks/1.5",
        "https://archidekt.com/decks/1e3",
        "https://archidekt.com/decks/12345678901",
        "https://archidekt.com/decks/2147483648",
        "https://archidekt.com/decks/99999999999999999999999999",
        "https://archidekt.com/decks/١٢٣",
        "https://archidekt.com/decks/%31%32",
        "https://archidekt.com/decks/1//",
        "https://archidekt.com/decks/1/a/b",
        "https://archidekt.com/decks/1/slug/extra/",
        "https://archidekt.com/u/someone",
        "https://archidekt.com/api/decks/1/",
        "https://archidekt.com/collection/1",
        "https://archidekt.com/folders/1",
        "https://archidekt.com/decks/1/../../u/x",
        "https://archidekt.com//decks/1",
        "https://archidekt.com/decks/1 /slug",
        "https://archidekt.com/decks/1\\slug",
        "https://archidekt.com/decks/1\x00",
        "https://archidekt.com/de\tcks/1",
        "https://archidekt.com/decks/1 https://evil.test",
        "https://[archidekt.com/decks/1",
        "https://ARCHIDE\u212aT.com/decks/1",  # Kelvin sign: str.lower() would turn it into "k"
        "https://archidekt.cöm/decks/1",
    ],
)
def test_parser_rejects_everything_that_is_not_a_deck_url(url: str) -> None:
    with pytest.raises(ArchidektUrlError) as caught:
        parse_archidekt_url(url)
    assert str(caught.value) == "not an Archidekt deck URL"
    assert caught.value.code == "not_archidekt_deck_url"


def test_archidekt_url_error_is_a_value_error() -> None:
    assert issubclass(ArchidektUrlError, ValueError)


def test_deck_link_is_built_from_the_id() -> None:
    assert archidekt_deck_url(2000000000) == "https://archidekt.com/decks/2000000000"


@pytest.mark.parametrize("bad", [0, -1, 2147483648, True])
def test_deck_link_refuses_an_invalid_id(bad: int) -> None:
    with pytest.raises(ValueError, match="not an Archidekt deck id"):
        archidekt_deck_url(bad)


# ---- fetcher ---------------------------------------------------------------------------------


async def test_public_deck_goes_through_the_shared_client_and_is_cached() -> None:
    seen: list[httpx.Request] = []
    body = _recorded("archidekt_public_deck.txt")

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})

    async with _client(handler) as client:
        first = await fetch_archidekt_deck(client, 2000000000)
        second = await fetch_archidekt_deck(client, 2000000000)

    assert isinstance(first, DeckFetched)
    assert (first.site, first.deck_id, first.body, first.from_cache) == (
        "archidekt",
        "2000000000",
        body,
        False,
    )
    assert isinstance(second, DeckFetched)
    assert second.from_cache
    assert len(seen) == 1
    assert str(seen[0].url) == "https://archidekt.com/api/decks/2000000000/"
    assert seen[0].headers["User-Agent"] == build_user_agent()
    assert seen[0].headers["Accept"] == "application/json"
    assert await _cached(2000000000) is not None


def test_recorded_public_deck_is_sanitised_and_has_every_shape_the_mapper_needs() -> None:
    raw = _recorded("archidekt_public_deck.txt").decode("utf-8")
    deck = json.loads(raw)
    assert deck["id"] == 2000000000
    assert deck["owner"] == {"id": 1, "username": "sample-owner", "avatar": None, "frame": None}
    assert deck["name"] == "Sample deck name"
    assert "storage.googleapis.com" not in raw
    categories = [tuple(c["categories"]) for c in deck["cards"]]
    assert ("Commander",) in categories
    assert ("Sideboard",) in categories
    assert ("Maybeboard",) in categories
    assert ("Ramp", "Artifact") in categories


@pytest.mark.parametrize("deck_id", [1000000, 999999999])
async def test_deleted_deck_is_not_found_and_nothing_is_cached(deck_id: int) -> None:
    calls = 0
    body = _recorded("archidekt_not_found.txt")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, content=body, headers={"Content-Type": "application/json"})

    async with _client(handler) as client:
        result = await fetch_archidekt_deck(client, deck_id)
        await fetch_archidekt_deck(client, deck_id)

    assert result == DeckFetchFailed("archidekt", str(deck_id), DeckFetchFailure.NOT_FOUND, 404)
    assert calls == 2
    assert await _cached(deck_id) is None


# UNVERIFIED: no private deck could be identified without logging in, so these shapes are what a
# private deck might answer, not recorded responses. The 404 above is the only verified case.
@pytest.mark.parametrize(
    "payload",
    [
        {"error": "Deck not found."},
        {"detail": "Not found."},
        {"detail": "You do not have permission to perform this action."},
        {"id": 55, "private": True, "cards": [], "name": "x"},
        {"id": 55, "private": True},
    ],
)
async def test_private_deck_answered_with_200_is_not_found_and_never_cached(
    payload: dict[str, object],
) -> None:
    async with _client(lambda request: _json(200, payload)) as client:
        result = await fetch_archidekt_deck(client, 55)

    assert result == DeckFetchFailed(
        "archidekt", "55", DeckFetchFailure.NOT_FOUND, 200, "private or deleted deck"
    )
    assert await _cached(55) is None


@pytest.mark.parametrize("status", [404, 410])
async def test_private_deck_answered_with_404_or_410_is_not_found(status: int) -> None:
    async with _client(lambda request: _json(status, {"detail": "x"})) as client:
        result = await fetch_archidekt_deck(client, 56)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.NOT_FOUND
    assert result.status_code == status


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, DeckFetchFailure.BLOCKED),
        (403, DeckFetchFailure.BLOCKED),
        (429, DeckFetchFailure.RATE_LIMITED),
        (500, DeckFetchFailure.SERVER_ERROR),
    ],
)
async def test_other_failures_keep_the_clients_reason(
    status: int, reason: DeckFetchFailure
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"Retry-After": "999"})

    async with _client(handler) as client:
        result = await fetch_archidekt_deck(client, 57)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is reason


async def test_html_maintenance_page_is_returned_uncached_for_the_mapper() -> None:
    page = b"<html>maintenance</html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=page, headers={"Content-Type": "text/html"})

    async with _client(handler) as client:
        result = await fetch_archidekt_deck(client, 58)
    assert isinstance(result, DeckFetched)
    assert result.body == page
    assert await _cached(58) is None


@pytest.mark.parametrize("payload", [[1, 2], {"unrelated": True}, {"id": 59, "cards": "oops"}])
async def test_unrecognised_json_is_returned_uncached_for_the_mapper(payload: object) -> None:
    async with _client(lambda request: _json(200, payload)) as client:
        result = await fetch_archidekt_deck(client, 59)
    assert isinstance(result, DeckFetched)
    assert await _cached(59) is None


async def test_json_that_is_not_utf8_is_returned_uncached() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"\xff\xfe", headers={"Content-Type": "application/json"}
        )

    async with _client(handler) as client:
        result = await fetch_archidekt_deck(client, 60)
    assert isinstance(result, DeckFetched)
    assert await _cached(60) is None


async def test_a_deck_with_another_id_is_unexpected_and_never_cached() -> None:
    async with _client(lambda request: _json(200, {"id": 999, "cards": []})) as client:
        result = await fetch_archidekt_deck(client, 61)
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.UNEXPECTED_RESPONSE
    assert await _cached(61) is None


async def test_fetch_refuses_an_invalid_id_before_any_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with _client(handler) as client:
        with pytest.raises(ValueError, match="not an Archidekt deck id"):
            await fetch_archidekt_deck(client, 0)


async def test_client_cache_body_hook_can_veto_caching() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"x", headers={"Content-Type": "application/json"})

    async with _client(handler) as client:
        url = "https://archidekt.com/api/decks/62/"
        await client.fetch("archidekt", "62", url, cache_body=lambda body: False)
        await client.fetch("archidekt", "62", url, cache_body=lambda body: True)
        again = await client.fetch("archidekt", "62", url)
    assert calls == 2
    assert isinstance(again, DeckFetched)
    assert again.from_cache


def _nested_deck(levels: int) -> bytes:
    """A deck body for id 5 whose total bracket nesting is ``levels``."""
    inner = levels - 1  # the outer object is level 1, ``cards`` itself is the first list
    return b'{"id": 5, "cards": ' + b"[" * inner + b"]" * inner + b"}"


async def _fetch_body(body: bytes) -> DeckFetched | DeckFetchFailed:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})

    async with _client(handler) as client:
        return await fetch_archidekt_deck(client, 5)


async def test_body_nested_exactly_at_the_depth_limit_is_a_deck_and_cached() -> None:
    result = await _fetch_body(_nested_deck(MAX_JSON_DEPTH))
    assert isinstance(result, DeckFetched)
    assert await _cached(5) is not None


@pytest.mark.parametrize("levels", [MAX_JSON_DEPTH + 1, 100_000])
async def test_body_nested_beyond_the_depth_limit_is_returned_uncached(levels: int) -> None:
    body = _nested_deck(levels)
    result = await _fetch_body(body)
    assert isinstance(result, DeckFetched)
    assert result.body == body
    assert await _cached(5) is None


def test_brackets_inside_strings_and_escapes_do_not_count_towards_depth() -> None:
    text = '{"id": 5, "cards": [], "name": "' + "[" * 500 + '\\" ' + "{" * 500 + '"}'
    assert not _nested_too_deep(text.encode())
    assert _nested_too_deep(b"[" * (MAX_JSON_DEPTH + 1))
    assert not _nested_too_deep(b"[]" * 1000)


@pytest.mark.parametrize("found", ["5", True, 5.0, None])
async def test_a_deck_id_that_is_not_the_integer_asked_for_is_unexpected(found: object) -> None:
    async with _client(lambda request: _json(200, {"id": found, "cards": []})) as client:
        result = await fetch_archidekt_deck(client, 5 if found != True else 1)  # noqa: E712
    assert isinstance(result, DeckFetchFailed)
    assert result.reason is DeckFetchFailure.UNEXPECTED_RESPONSE


async def test_a_cache_body_callback_that_raises_means_do_not_cache() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x", headers={"Content-Type": "application/json"})

    def boom(body: bytes) -> bool:
        raise RuntimeError("bug in site code")

    async with _client(handler) as client:
        result = await client.fetch(
            "archidekt", "63", "https://archidekt.com/api/decks/63/", cache_body=boom
        )
    assert isinstance(result, DeckFetched)
    assert result.body == b"x"
    assert await _cached(63) is None


def test_archidekt_url_error_follows_the_domain_error_convention() -> None:
    error = ArchidektUrlError()
    assert isinstance(error, DomainError)
    assert (error.code, error.status_code) == ("not_archidekt_deck_url", 422)
    assert error.message == "not an Archidekt deck URL"
