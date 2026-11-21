"""Unit tests of the Scryfall client internals (S-3.1.2) over ``httpx.MockTransport``."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from app.cards.scryfall_client import (
    API_HOST,
    BULK_HOST,
    ScryfallClient,
    ScryfallHostError,
    ScryfallResponseError,
    parse_retry_after,
)
from app.core.config import get_settings
from app.core.user_agent import api_version, build_user_agent


class Recorder:
    """A transport handler that replays scripted responses and records requests and sleeps."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if self.responses else httpx.Response(200, json={"ok": True})
        if isinstance(item, Exception):
            raise item
        return item

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def client(self) -> ScryfallClient:
        return ScryfallClient(transport=httpx.MockTransport(self.handler), sleep=self.sleep)

    def retry_sleeps(self) -> list[float]:
        """Sleeps other than the limiter's (those under one second)."""
        return [s for s in self.sleeps if s >= 1]


def test_user_agent_has_the_exact_format_and_no_email() -> None:
    agent = build_user_agent()
    assert agent == f"MTGStats/{api_version()} (+{get_settings().repo_url})"
    assert "@" not in agent


def test_parse_retry_after_defaults_to_sixty_seconds() -> None:
    assert parse_retry_after(None) == 60.0
    assert parse_retry_after("soon") == 60.0
    assert parse_retry_after("inf") == 60.0
    assert parse_retry_after("7") == 7.0


async def test_api_request_carries_user_agent_and_json_accept() -> None:
    rec = Recorder()
    async with rec.client() as client:
        assert await client.get_json("/cards/named", {"exact": "Sol Ring"}) == {"ok": True}
    request = rec.requests[0]
    assert request.url.host == API_HOST
    assert request.url.params["exact"] == "Sol Ring"
    assert request.headers["User-Agent"] == build_user_agent()
    assert request.headers["Accept"] == "application/json"


async def test_bulk_download_sends_wildcard_accept_and_user_agent() -> None:
    rec = Recorder(httpx.Response(200, content=b"[]"))
    url = f"https://{BULK_HOST}/file/default-cards.json"
    async with rec.client() as client, client.stream_bulk(url) as response:
        assert await response.aread() == b"[]"
    request = rec.requests[0]
    assert request.headers["Accept"] == "*/*"
    assert request.headers["User-Agent"] == build_user_agent()


async def test_other_hosts_are_refused() -> None:
    rec = Recorder()
    async with rec.client() as client:
        with pytest.raises(ScryfallHostError):
            await client.get_json("https://evil.example/cards")
        with pytest.raises(ScryfallHostError):
            async with client.stream_bulk(f"https://{API_HOST}/bulk-data"):
                pass
    assert rec.requests == []


async def test_429_waits_retry_after_then_succeeds() -> None:
    rec = Recorder(httpx.Response(429, headers={"Retry-After": "7"}))
    async with rec.client() as client:
        assert await client.get_json("/sets") == {"ok": True}
    assert rec.retry_sleeps() == [7.0]
    assert len(rec.requests) == 2


async def test_429_without_retry_after_waits_sixty_seconds_and_gives_up_after_three_retries() -> (
    None
):
    rec = Recorder(*[httpx.Response(429) for _ in range(4)])
    async with rec.client() as client:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await client.get_json("/sets")
    assert caught.value.response.status_code == 429
    assert rec.retry_sleeps() == [60.0, 60.0, 60.0]
    assert len(rec.requests) == 4


async def test_5xx_backs_off_exponentially_then_raises() -> None:
    rec = Recorder(*[httpx.Response(503) for _ in range(4)])
    async with rec.client() as client:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await client.get_json("/sets")
    assert caught.value.response.status_code == 503
    assert rec.retry_sleeps() == [2.0, 4.0, 8.0]
    assert len(rec.requests) == 4


async def test_5xx_then_success_returns_the_body() -> None:
    rec = Recorder(httpx.Response(500), httpx.Response(502))
    async with rec.client() as client:
        assert await client.get_json("/sets") == {"ok": True}
    assert rec.retry_sleeps() == [2.0, 4.0]


async def test_other_4xx_is_not_retried() -> None:
    rec = Recorder(httpx.Response(404, json={"object": "error"}))
    async with rec.client() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_json("/cards/nope")
    assert len(rec.requests) == 1
    assert rec.retry_sleeps() == []


async def test_transport_errors_are_retried_like_5xx() -> None:
    rec = Recorder(httpx.ConnectError("boom"))
    async with rec.client() as client:
        assert await client.get_json("/sets") == {"ok": True}
    assert rec.retry_sleeps() == [2.0]


async def test_transport_error_surfaces_after_three_retries() -> None:
    rec = Recorder(*[httpx.ConnectError("boom") for _ in range(4)])
    async with rec.client() as client:
        with pytest.raises(httpx.ConnectError):
            await client.get_json("/sets")
    assert len(rec.requests) == 4


async def test_bulk_retries_5xx_and_raises_after_the_last() -> None:
    rec = Recorder(*[httpx.Response(500) for _ in range(4)])
    async with rec.client() as client:
        with pytest.raises(httpx.HTTPStatusError):
            async with client.stream_bulk(f"https://{BULK_HOST}/f.json"):
                pass
    assert rec.retry_sleeps() == [2.0, 4.0, 8.0]


async def test_bulk_404_raises_without_retry() -> None:
    rec = Recorder(httpx.Response(404))
    async with rec.client() as client:
        with pytest.raises(httpx.HTTPStatusError):
            async with client.stream_bulk(f"https://{BULK_HOST}/f.json"):
                pass
    assert len(rec.requests) == 1


NOW = datetime(2099, 10, 21, 7, 0, 0, tzinfo=UTC)


def _now() -> datetime:
    return NOW


def test_parse_retry_after_reads_an_http_date_relative_to_now() -> None:
    date = "Wed, 21 Oct 2099 07:28:00 GMT"
    assert parse_retry_after(date, now=_now) == 28 * 60.0


def test_parse_retry_after_waits_zero_for_a_past_date_or_negative_seconds() -> None:
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=_now) == 0.0
    assert parse_retry_after("-5") == 0.0


def test_parse_retry_after_garbage_falls_back_to_sixty() -> None:
    assert parse_retry_after("not a date", now=_now) == 60.0
    assert parse_retry_after("", now=_now) == 60.0
    assert parse_retry_after("nan") == 60.0


async def test_429_with_an_http_date_waits_until_that_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.cards.scryfall_client._utc_now", _now)
    header = {"Retry-After": "Wed, 21 Oct 2099 07:00:30 GMT"}
    rec = Recorder(httpx.Response(429, headers=header))
    async with rec.client() as client:
        await client.get_json("/sets")
    assert rec.retry_sleeps() == [30.0]


@pytest.mark.parametrize("status", [301, 302, 307])
async def test_redirect_from_the_api_host_is_an_error_not_followed(status: int) -> None:
    rec = Recorder(httpx.Response(status, headers={"Location": "https://evil.example/x"}))
    async with rec.client() as client:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await client.get_json("/sets")
    assert caught.value.response.status_code == status
    assert len(rec.requests) == 1


async def test_redirect_from_the_bulk_host_is_an_error_not_a_body() -> None:
    rec = Recorder(httpx.Response(302, headers={"Location": "https://evil.example/x"}))
    async with rec.client() as client:
        with pytest.raises(httpx.HTTPStatusError):
            async with client.stream_bulk(f"https://{BULK_HOST}/f.json"):
                pytest.fail("a redirect must not be yielded as the bulk file")
    assert len(rec.requests) == 1


async def test_200_with_a_non_json_body_raises_a_typed_error() -> None:
    rec = Recorder(httpx.Response(200, content=b"<html>oops</html>"))
    async with rec.client() as client:
        with pytest.raises(ScryfallResponseError):
            await client.get_json("/sets")


class TrackedStream(httpx.AsyncByteStream):
    """A response body that can fail mid-read and records whether it was closed."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"{"
        if self.fail:
            raise httpx.ReadError("broken body")
        yield b'"ok": true}'

    async def aclose(self) -> None:
        self.closed = True


async def test_a_retried_5xx_response_is_closed_before_the_retry() -> None:
    stream = TrackedStream()
    rec = Recorder(httpx.Response(503, stream=stream))
    async with rec.client() as client:
        assert await client.get_json("/sets") == {"ok": True}
    assert stream.closed


async def test_a_body_read_error_is_retried_like_any_transport_error() -> None:
    rec = Recorder(httpx.Response(200, stream=TrackedStream(fail=True)))
    async with rec.client() as client:
        assert await client.get_json("/sets") == {"ok": True}
    assert rec.retry_sleeps() == [2.0]


async def test_a_5xx_whose_body_always_breaks_ends_as_the_read_error_with_streams_closed() -> None:
    streams = [TrackedStream(fail=True) for _ in range(4)]
    rec = Recorder(*[httpx.Response(500, stream=stream) for stream in streams])
    async with rec.client() as client:
        with pytest.raises(httpx.ReadError):
            await client.get_json("/sets")
    assert all(stream.closed for stream in streams)
    assert rec.retry_sleeps() == [2.0, 4.0, 8.0]
