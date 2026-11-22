"""Commander Spellbook client: one polite, cached combo lookup per deck version (S-5.2.1).

``POST https://backend.commanderspellbook.com/find-my-combos`` takes the deck's commanders and
main cards by name and answers with *variants* in buckets; the ``included`` bucket holds the
combos fully included in the deck. The client returns those, never raises (criterion 4) and is
polite, following Spellbook's terms ("a few HTTP calls per user interaction", "80 requests per
minute should be a safe rate", "always handle 429"):

* Every request carries the shared ``MTGStats/<version> (+<repo URL>)`` User-Agent and a timeout
  from settings (the whole request, body included).
* Requests to the host are spaced by a Redis limiter shared by every worker
  (``SPELLBOOK_MIN_INTERVAL_MS``, 1 s by default). Every request counts, each page and each retry.
* A lookup is cached in Redis by the deck's cards (see ``spellbook_cache``); a hit sends nothing.
* Paging: Spellbook paginates over the whole variant set, so each page's buckets are partial
  (a probe on 2026-11-21 with ``limit=3`` returned 3 ``included`` variants and nothing in the other
  buckets). One lookup follows ``next`` until it is null, at most ``SPELLBOOK_MAX_PAGES`` pages.
  The client never requests the ``next`` URL: it takes only its ``offset`` (which must be an
  integer and strictly increase) and posts to its own constant URL, so a ``next`` link that
  carries another host (DRF builds it from the Host header) is harmless; a differing host is only
  logged, once per lookup. Redirects are never followed.
* Deadline: one request is bounded by ``SPELLBOOK_TIMEOUT_SECONDS``, but a lookup is bounded as a
  whole by ``SPELLBOOK_LOOKUP_DEADLINE_SECONDS`` (60 s by default), limiter waits and 429 sleeps
  included; passing it is ``unavailable`` with reason ``timeout``. Without it the worst case would
  be ``pages x (3 attempts x timeout + 2 x max Retry-After)``, about 450 s with the defaults.
* 429: waits for ``Retry-After`` (seconds or HTTP-date) and retries, at most twice. A wait longer
  than ``SPELLBOOK_MAX_RETRY_AFTER_SECONDS`` is not retried: the lookup is ``rate_limited`` at
  once, so we never come back before the time Spellbook asked for. Timeouts, 5xx, transport
  errors and every other non-2xx are not retried.
* A body over ``MAX_RESPONSE_BYTES`` or a response that does not match the expected schema is
  ``unavailable``. Logs carry statuses, counts and field paths, never card names.

Only this module talks to Spellbook. API routes never import it, nor ``find_deck_combos``
(an architecture test checks): a web request never waits on Spellbook. Lookups run in jobs through
``find_deck_combos``; request paths may only read the cache through ``cached_deck_combos``.
"""

import asyncio
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Self
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import ValidationError

from app.brackets.spellbook_cache import SpellbookCache, cache_key, normalise_name
from app.brackets.spellbook_schema import (
    CombosFound,
    DeckCard,
    SpellbookCombo,
    SpellbookResult,
    Unavailable,
    UnavailableReason,
    WirePage,
)
from app.core.config import Settings, get_settings
from app.core.rate_limit import key as rate_limit_key
from app.core.spacing_limiter import Sleep, SpacingLimiter
from app.core.user_agent import build_user_agent

logger = logging.getLogger("app.brackets.spellbook_client")

API_HOST = "backend.commanderspellbook.com"
API_BASE_URL = f"https://{API_HOST}"
FIND_COMBOS_PATH = "/find-my-combos"
PAGE_LIMIT = 1000
MAX_RETRIES = 2
DEFAULT_RETRY_AFTER_SECONDS = 10.0
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_COMMANDERS = 12
MAX_MAIN = 600
MAX_NAME_LENGTH = 256
MAX_LOGGED_ERRORS = 3


@dataclass(frozen=True, slots=True)
class _Reply:
    """A fully read response: status, ``Retry-After`` header and the decoded body."""

    status: int
    retry_after: str | None
    content: bytes


class _LookupFailedError(Exception):
    """Internal: ends the lookup with an ``Unavailable`` (never leaves this module)."""

    def __init__(self, reason: UnavailableReason, detail: str = "") -> None:
        super().__init__(reason.value)
        self.result = Unavailable(reason, detail)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def parse_retry_after(value: str | None, *, now: Callable[[], datetime] | None = None) -> float:
    """``Retry-After`` in seconds (delay-seconds or an HTTP-date, RFC 9110).

    A negative or past value is 0. A missing, unparseable or non-finite value is 10 s.
    """
    if value is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        pass
    else:
        return max(0.0, seconds) if math.isfinite(seconds) else DEFAULT_RETRY_AFTER_SECONDS
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return DEFAULT_RETRY_AFTER_SECONDS
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or _utc_now)()).total_seconds())


def build_request_body(cards: list[DeckCard]) -> dict[str, list[dict[str, Any]]]:
    """The JSON body: ``commanders`` and ``main`` by name (the companion is in ``main``).

    Lines for the same card and placement are merged. Raises an internal error, which
    ``find_combos`` turns into ``invalid_request``, for a blank or over-long name, a quantity
    under 1 or a deck over Spellbook's list limits.
    """
    lines: dict[tuple[bool, str], dict[str, Any]] = {}
    for card in cards:
        name = normalise_name(card.name)
        if not name or len(name) > MAX_NAME_LENGTH or card.quantity < 1:
            raise _LookupFailedError(
                UnavailableReason.INVALID_REQUEST, "a card has a bad name or quantity"
            )
        slot = (card.is_commander, str(card.oracle_id) if card.oracle_id else name.casefold())
        if slot in lines:
            lines[slot]["quantity"] += card.quantity
        else:
            lines[slot] = {"card": name, "quantity": card.quantity}
    commanders = [line for (is_commander, _), line in lines.items() if is_commander]
    main = [line for (is_commander, _), line in lines.items() if not is_commander]
    if len(commanders) > MAX_COMMANDERS or len(main) > MAX_MAIN:
        raise _LookupFailedError(
            UnavailableReason.INVALID_REQUEST,
            f"deck exceeds Spellbook's limits ({MAX_COMMANDERS} commanders, {MAX_MAIN} main)",
        )
    return {"commanders": commanders, "main": main}


def next_offset(next_url: str, current_offset: int) -> int:
    """The ``offset`` of a ``next`` link: an integer strictly greater than ``current_offset``.

    Only the offset is read; the link itself is never requested, so its scheme, host and path do
    not matter (see :func:`next_link_host_differs`). Raises an internal error, which
    ``find_combos`` turns into ``schema_mismatch``, when the offset is missing, not an integer or
    does not advance (so a misbehaving server cannot loop us).
    """
    values = parse_qs(urlsplit(next_url).query).get("offset", [])
    if len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
        raise _LookupFailedError(
            UnavailableReason.SCHEMA_MISMATCH, "next link has no usable offset"
        )
    offset = int(values[0])
    if offset <= current_offset:
        raise _LookupFailedError(UnavailableReason.SCHEMA_MISMATCH, "next link does not advance")
    return offset


def next_link_host_differs(next_url: str) -> bool:
    """True when a ``next`` link names a host other than Spellbook's (it is not followed)."""
    return urlsplit(next_url).hostname != API_HOST


def echoed_limit(next_url: str) -> str:
    """The ``limit`` a ``next`` link echoes (the page size the server applied), or ``"?"``."""
    values = parse_qs(urlsplit(next_url).query).get("limit", [])
    return values[0] if len(values) == 1 and values[0].isdigit() else "?"


class SpellbookClient:
    """Async Commander Spellbook client; use as ``async with SpellbookClient() as client``."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: SpacingLimiter | None = None,
        cache: SpellbookCache | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        used = settings or get_settings()
        self._timeout = used.spellbook_timeout_seconds
        self._max_retry_after = used.spellbook_max_retry_after_seconds
        self._max_pages = used.spellbook_max_pages
        self._deadline = used.spellbook_lookup_deadline_seconds
        self._sleep = sleep
        self._limiter = limiter or SpacingLimiter(
            rate_limit_key("spellbook", API_HOST), used.spellbook_min_interval_ms, sleep=sleep
        )
        self._cache = cache or SpellbookCache(used)
        self._http = httpx.AsyncClient(
            base_url=API_BASE_URL,
            headers={"User-Agent": build_user_agent(used), "Accept": "application/json"},
            timeout=httpx.Timeout(self._timeout),
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

    async def find_combos(self, cards: list[DeckCard]) -> SpellbookResult:
        """The combos fully included in ``cards``, or ``Unavailable`` with a reason. Never raises.

        Cancellation still propagates (it is not an error of the lookup).
        """
        try:
            body = build_request_body(cards)
            if not body["commanders"] and not body["main"]:
                return CombosFound(())
            key = cache_key(cards)
            cached = await self._cache.get(key)
            if cached is not None:
                return CombosFound(cached, from_cache=True)
            try:
                async with asyncio.timeout(self._deadline):
                    combos = await self._fetch_all(body)
            except TimeoutError as exc:
                raise _LookupFailedError(
                    UnavailableReason.TIMEOUT, f"lookup deadline of {self._deadline:g}s passed"
                ) from exc
            await self._cache.set(key, combos)
            return CombosFound(combos)
        except _LookupFailedError as failure:
            logger.warning(
                "spellbook unavailable: %s (%s)", failure.result.reason.value, failure.result.detail
            )
            return failure.result
        except Exception as exc:
            logger.exception("spellbook lookup failed unexpectedly")
            return Unavailable(UnavailableReason.INTERNAL_ERROR, type(exc).__name__)

    async def _fetch_all(self, body: dict[str, list[dict[str, Any]]]) -> tuple[SpellbookCombo, ...]:
        """Follow the pages (at most ``max_pages``) and merge the ``included`` buckets by id."""
        combos: dict[str, SpellbookCombo] = {}
        sizes: list[int] = []
        echoed = "?"
        warned = False
        offset = 0
        for _ in range(self._max_pages):
            page = await self._request_page(body, offset)
            sizes.append(page.results.total_items())
            for variant in page.results.included:
                combos.setdefault(variant.id, variant.to_combo())
            if page.next is None:
                logger.info(
                    "spellbook lookup done: %d pages, items per page %s, limit echoed %s",
                    len(sizes),
                    sizes,
                    echoed,
                )
                return tuple(combos.values())
            if not warned and next_link_host_differs(page.next):
                warned = True
                logger.warning("spellbook next link names another host; posting to our own URL")
            echoed = echoed_limit(page.next)
            offset = next_offset(page.next, offset)
        raise _LookupFailedError(
            UnavailableReason.TOO_MANY_PAGES, f"more than {self._max_pages} pages of results"
        )

    async def _request_page(self, body: dict[str, list[dict[str, Any]]], offset: int) -> WirePage:
        """One page, with the 429 retries. Each attempt waits for the limiter first."""
        params: dict[str, int] = {"limit": PAGE_LIMIT}
        if offset:
            params["offset"] = offset
        for attempt in range(MAX_RETRIES + 1):
            await self._limiter.wait()
            reply = await self._send(body, params)
            if reply.status != 429:
                return _parse(reply)
            wait = parse_retry_after(reply.retry_after)
            if attempt == MAX_RETRIES:
                raise _LookupFailedError(
                    UnavailableReason.RATE_LIMITED, "429 after the allowed retries"
                )
            if wait > self._max_retry_after:
                raise _LookupFailedError(
                    UnavailableReason.RATE_LIMITED,
                    f"429 with Retry-After of {wait:.0f}s (too long)",
                )
            logger.warning("spellbook returned 429; retrying in %.0fs", wait)
            await self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _send(self, body: dict[str, list[dict[str, Any]]], params: dict[str, int]) -> _Reply:
        """POST once and read the body (at most ``MAX_RESPONSE_BYTES`` once decoded).

        The response is always closed. A timeout (of the whole request, body included) or a
        transport error ends the lookup.
        """
        request = self._http.build_request("POST", FIND_COMBOS_PATH, params=params, json=body)
        response: httpx.Response | None = None
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._http.send(request, stream=True)
                content = await _read_capped(response) if response.is_success else b""
                return _Reply(response.status_code, response.headers.get("Retry-After"), content)
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise _LookupFailedError(UnavailableReason.TIMEOUT, type(exc).__name__) from exc
        except httpx.TransportError as exc:
            raise _LookupFailedError(UnavailableReason.TRANSPORT_ERROR, type(exc).__name__) from exc
        finally:
            if response is not None:
                await response.aclose()


async def _read_capped(response: httpx.Response) -> bytes:
    """Read the body, failing as soon as it passes ``MAX_RESPONSE_BYTES``."""
    declared = response.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise _LookupFailedError(
            UnavailableReason.RESPONSE_TOO_LARGE, "Content-Length over the limit"
        )
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise _LookupFailedError(UnavailableReason.RESPONSE_TOO_LARGE, "body over the limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse(reply: _Reply) -> WirePage:
    """Check the status and validate the body against the wire models."""
    status = reply.status
    if 300 <= status < 400:
        raise _LookupFailedError(UnavailableReason.REDIRECT, f"status {status}")
    if status >= 500:
        raise _LookupFailedError(UnavailableReason.SERVER_ERROR, f"status {status}")
    if not 200 <= status < 300:
        raise _LookupFailedError(UnavailableReason.CLIENT_ERROR, f"status {status}")
    try:
        return WirePage.model_validate_json(reply.content)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(part) for part in err['loc'])}: {err['type']}"
            for err in exc.errors(include_input=False, include_url=False)[:MAX_LOGGED_ERRORS]
        ]
        raise _LookupFailedError(
            UnavailableReason.SCHEMA_MISMATCH,
            f"{exc.error_count()} errors, first: {'; '.join(problems)}",
        ) from exc
