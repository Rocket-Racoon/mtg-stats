"""Scryfall HTTP client that follows Scryfall's API guidelines (S-3.1.2).

Every request carries the shared ``MTGStats/<version> (+<repo URL>)`` User-Agent and an ``Accept``
header (``application/json``, or ``*/*`` for bulk file downloads). Calls to ``api.scryfall.com``
are spaced at least 100 ms apart through a Redis limiter shared by every worker. A 429 waits for
``Retry-After`` (60 s when missing) and a 5xx or transport error backs off 2 s, 4 s, 8 s; each is
retried at most 3 times, after which the error reaches the caller as ``httpx.HTTPStatusError``
(or the ``httpx.TransportError``). Only this module talks to Scryfall; API routes read the local
database (an architecture test keeps routers from importing it).
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings, get_settings
from app.core.rate_limit import key as rate_limit_key
from app.core.spacing_limiter import Sleep, SpacingLimiter
from app.core.user_agent import build_user_agent

logger = logging.getLogger("app.cards.scryfall_client")

API_HOST = "api.scryfall.com"
BULK_HOST = "data.scryfall.io"
API_BASE_URL = f"https://{API_HOST}"
JSON_ACCEPT = "application/json"
BULK_ACCEPT = "*/*"
MAX_RETRIES = 3
DEFAULT_RETRY_AFTER_SECONDS = 60.0
BACKOFF_BASE_SECONDS = 2.0
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
BULK_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


class ScryfallHostError(ValueError):
    """The URL is not on ``api.scryfall.com`` or ``data.scryfall.io``."""


def parse_retry_after(value: str | None) -> float:
    """``Retry-After`` in seconds; 60 when missing, not a non-negative number or not finite."""
    if value is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        seconds = float(value.strip())
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS
    if not 0 <= seconds < float("inf"):
        return DEFAULT_RETRY_AFTER_SECONDS
    return seconds


class ScryfallClient:
    """Async Scryfall client; use as ``async with ScryfallClient() as client``."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: SpacingLimiter | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        used = settings or get_settings()
        self._sleep = sleep
        self._limiter = limiter or SpacingLimiter(
            rate_limit_key("scryfall", API_HOST), used.scryfall_min_interval_ms, sleep=sleep
        )
        self._http = httpx.AsyncClient(
            base_url=API_BASE_URL,
            headers={"User-Agent": build_user_agent(used), "Accept": JSON_ACCEPT},
            timeout=TIMEOUT,
            transport=transport,
            follow_redirects=False,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """GET a JSON document from ``api.scryfall.com`` (``path`` or an absolute Scryfall URL)."""
        response = await self._send("GET", path, params=params, accept=JSON_ACCEPT)
        return response.json()

    @asynccontextmanager
    async def stream_bulk(self, url: str) -> AsyncIterator[httpx.Response]:
        """Stream a bulk file from ``data.scryfall.io`` (``Accept: */*``, not rate limited).

        Retry and backoff apply to getting the response headers; once the body is streaming, a
        failure reaches the caller.
        """
        _check_host(url, {BULK_HOST})
        request = self._http.build_request(
            "GET", url, headers={"Accept": BULK_ACCEPT}, timeout=BULK_TIMEOUT
        )
        response = await self._with_retries(request, stream=True, limit=False)
        try:
            yield response
        finally:
            await response.aclose()

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        accept: str,
    ) -> httpx.Response:
        _check_host(self._http.base_url.join(url).__str__(), {API_HOST})
        request = self._http.build_request(method, url, params=params, headers={"Accept": accept})
        return await self._with_retries(request, stream=False, limit=True)

    async def _with_retries(
        self, request: httpx.Request, *, stream: bool, limit: bool
    ) -> httpx.Response:
        """Send ``request``, retrying 429 and 5xx; raise the final error for the caller."""
        attempt = 0
        while True:
            if limit:
                await self._limiter.wait()
            try:
                response = await self._http.send(request, stream=stream)
            except httpx.TransportError as exc:
                if attempt >= MAX_RETRIES:
                    raise
                delay = BACKOFF_BASE_SECONDS * 2**attempt
                logger.warning(
                    "scryfall transport error %s; retrying in %.0fs", type(exc).__name__, delay
                )
            else:
                delay = await self._retry_delay(response, attempt, stream=stream)
                if delay is None:
                    return response
            attempt += 1
            await self._sleep(delay)

    async def _retry_delay(
        self, response: httpx.Response, attempt: int, *, stream: bool
    ) -> float | None:
        """Seconds to wait before retrying, or None to hand the response back (or raise)."""
        status = response.status_code
        retryable = status == 429 or status >= 500
        if not retryable:
            if status >= 400:
                await _close(response, stream)
                response.raise_for_status()
            return None
        if attempt >= MAX_RETRIES:
            if not stream:
                response.raise_for_status()
            await response.aread()
            await response.aclose()
            response.raise_for_status()
        delay = (
            parse_retry_after(response.headers.get("Retry-After"))
            if status == 429
            else BACKOFF_BASE_SECONDS * 2**attempt
        )
        await _close(response, stream)
        logger.warning("scryfall returned %d; retrying in %.0fs", status, delay)
        return delay


async def _close(response: httpx.Response, stream: bool) -> None:
    if stream:
        await response.aread()
        await response.aclose()


def _check_host(url: str, allowed: set[str]) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in allowed:
        raise ScryfallHostError(f"not a Scryfall URL for this call: {parts.hostname}")
