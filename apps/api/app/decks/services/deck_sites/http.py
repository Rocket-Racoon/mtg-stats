"""Polite HTTP client for deck sites (S-4.2.1, decision E4).

``DeckSiteClient.fetch(site, deck_id, url)`` returns a typed result and does not raise for site
problems: a :class:`DeckFetched` (the raw body) or a :class:`DeckFetchFailed` with a
:class:`DeckFetchFailure` reason. Parsing a body is the mappers' job, so ``unexpected_format``
is not a reason here.

* Every request carries ``build_user_agent()``; the timeout, retries, cache TTL and size cap come
  from settings.
* Only the hosts of the registered sites are requested (``https`` only, redirects never followed),
  because the import endpoint will take user-supplied URLs. A URL outside the site's hosts, or an
  unregistered site, is a programming error and raises :class:`DeckSiteHostError`.
* A fetched deck is served from Redis for the cache TTL (see ``cache.py``); failures are not cached.
* Requests to a site are spaced through the Redis limiter (shared by every worker) and at most
  ``max_concurrency`` are in flight per client instance, so hold one client per worker.
* A 429 is retried at most ``deck_site_max_retries`` (2) times, waiting for ``Retry-After`` (5 s
  when missing); a ``Retry-After`` above the cap is not waited for and fails at once. 5xx and
  transport errors are not retried: they are reported, and the user falls back to text paste.
* Routers never import this module (an architecture test enforces it).
"""

import asyncio
import enum
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Self

import httpx

from app.core.config import Settings, get_settings
from app.core.rate_limit import key as rate_limit_key
from app.core.retry_after import parse_retry_after
from app.core.spacing_limiter import Sleep, SpacingLimiter
from app.core.user_agent import build_user_agent
from app.decks.services.deck_sites.cache import CachedBody, DeckCache

logger = logging.getLogger("app.decks.services.deck_sites.http")

DEFAULT_RETRY_AFTER_SECONDS = 5.0
DEFAULT_ACCEPT = "application/json"


class DeckSiteHostError(ValueError):
    """The site is not registered, or the URL is not an ``https`` URL on one of its hosts."""


class DeckFetchFailure(enum.StrEnum):
    """Why a fetch failed; the values are the machine-readable reasons the import job passes on."""

    RATE_LIMITED = "rate_limited"  # 429 after the allowed retries, or a Retry-After above the cap
    TIMEOUT = "timeout"
    BLOCKED = "blocked"  # 401 or 403
    NOT_FOUND = "not_found"  # 404 or 410
    SERVER_ERROR = "server_error"  # 5xx
    TOO_LARGE = "too_large"  # body above DECK_SITE_MAX_RESPONSE_BYTES
    NETWORK_ERROR = "network_error"  # connection failed, TLS error, broken body
    UNEXPECTED_RESPONSE = "unexpected_response"  # any other status, redirects included


@dataclass(frozen=True, slots=True)
class DeckFetched:
    """A successful fetch: the raw body, not parsed."""

    site: str
    deck_id: str
    body: bytes
    content_type: str
    from_cache: bool


@dataclass(frozen=True, slots=True)
class DeckFetchFailed:
    """A failed fetch: ``reason`` is stable, ``status_code`` is the last HTTP status if any."""

    site: str
    deck_id: str
    reason: DeckFetchFailure
    status_code: int | None = None
    detail: str = ""


DeckFetchResult = DeckFetched | DeckFetchFailed


@dataclass(frozen=True, slots=True)
class SiteConfig:
    """A deck site: its allowed hosts and how fast it may be asked."""

    name: str
    hosts: frozenset[str]
    min_interval_ms: int
    max_concurrency: int


def default_sites(settings: Settings | None = None) -> dict[str, SiteConfig]:
    """The registered deck sites. Only Archidekt: Moxfield is deferred (decision of 2026-11-21)."""
    used = settings or get_settings()
    return {
        "archidekt": SiteConfig(
            name="archidekt",
            hosts=frozenset({"archidekt.com"}),
            min_interval_ms=used.deck_site_archidekt_min_interval_ms,
            max_concurrency=used.deck_site_max_concurrency,
        )
    }


def validate_url(config: SiteConfig, url: str) -> httpx.URL:
    """Parse ``url`` once with httpx and return the object that is then requested.

    Refused with :class:`DeckSiteHostError`: whitespace, control characters or backslashes
    anywhere; an unparseable URL; a scheme other than ``https``; credentials (user or password);
    a port other than the default 443; an empty host; and a host that is not one of the site's
    hosts. The host is compared after httpx's lower-casing and IDNA normalisation, so
    ``ARCHIDEKT.com`` is accepted. A trailing-dot host (``archidekt.com.``) is refused, not
    normalised.
    """
    if any(ch.isspace() or ch == "\\" or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise DeckSiteHostError("URL contains whitespace, control characters or a backslash")
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise DeckSiteHostError("URL is not valid") from exc
    if parsed.scheme != "https":
        raise DeckSiteHostError("URL is not https")
    if parsed.userinfo:
        raise DeckSiteHostError("URL carries credentials")
    if parsed.port not in (None, 443):
        raise DeckSiteHostError("URL has a non-default port")
    if not parsed.host or parsed.host not in config.hosts:
        raise DeckSiteHostError(f"not a {config.name} URL: {parsed.host}")
    return parsed


LimiterFactory = Callable[[SiteConfig], SpacingLimiter]


@dataclass(slots=True)
class _Attempt:
    """Outcome of one request: exactly one of ``fetched`` and ``failure``."""

    fetched: DeckFetched | None = None
    failure: DeckFetchFailed | None = None
    retry_after: float = 0.0


class DeckSiteClient:
    """Async deck-site client; use as ``async with DeckSiteClient() as client``."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        sites: Mapping[str, SiteConfig] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: DeckCache | None = None,
        limiter_factory: LimiterFactory | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._settings = settings or get_settings()
        self._sites = dict(sites) if sites is not None else default_sites(self._settings)
        self._sleep = sleep
        self._cache = cache or DeckCache(self._settings)

        def spacing(site: SiteConfig) -> SpacingLimiter:
            return SpacingLimiter(
                rate_limit_key("decksite-spacing", site.name), site.min_interval_ms, sleep=sleep
            )

        factory = limiter_factory or spacing
        self._limiters = {name: factory(site) for name, site in self._sites.items()}
        self._semaphores = {
            name: asyncio.Semaphore(site.max_concurrency) for name, site in self._sites.items()
        }
        self._http = httpx.AsyncClient(
            headers={"User-Agent": build_user_agent(self._settings)},
            timeout=httpx.Timeout(self._settings.deck_site_timeout_seconds),
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

    async def fetch(
        self, site: str, deck_id: str, url: str, *, accept: str = DEFAULT_ACCEPT
    ) -> DeckFetchResult:
        """Fetch a deck (``url`` on ``site``'s hosts), from the cache when fresh.

        The cache is keyed by ``site`` and ``deck_id`` only and ignores ``url``: a different path
        or query string with the same ``deck_id`` is a cache hit. The per-site code must pass a
        ``deck_id`` that uniquely identifies what is fetched.

        Raises:
            DeckSiteHostError: unregistered site, or a URL that is not acceptable (see
                :func:`validate_url`). This is checked before the cache, the limiter and the
                network, so a refused URL is never served from the cache either.
        """
        config = self._sites.get(site)
        if config is None:
            raise DeckSiteHostError(f"unknown deck site: {site!r}")
        target = validate_url(config, url)

        cached = await self._cache.get(site, deck_id)
        if cached is not None:
            return DeckFetched(site, deck_id, cached.body, cached.content_type, from_cache=True)

        async with self._semaphores[site]:
            result = await self._fetch_with_retries(config, deck_id, target, accept)
        if isinstance(result, DeckFetched):
            await self._cache.set(site, deck_id, CachedBody(result.body, result.content_type))
        return result

    async def _fetch_with_retries(
        self, config: SiteConfig, deck_id: str, url: httpx.URL, accept: str
    ) -> DeckFetchResult:
        limiter = self._limiters[config.name]
        max_retries = self._settings.deck_site_max_retries
        cap = self._settings.deck_site_retry_after_cap_seconds
        attempt = 0
        while True:
            await limiter.wait()
            outcome = await self._attempt(config.name, deck_id, url, accept)
            if outcome.fetched is not None:
                return outcome.fetched
            failed = outcome.failure
            assert failed is not None  # noqa: S101  (an _Attempt always has one of the two)
            if failed.reason is not DeckFetchFailure.RATE_LIMITED:
                return failed
            if attempt >= max_retries or outcome.retry_after > cap:
                logger.warning(
                    "deck site %s rate limited us (Retry-After %.0fs, attempt %d); giving up",
                    config.name,
                    outcome.retry_after,
                    attempt + 1,
                )
                return failed
            logger.warning(
                "deck site %s returned 429; retrying in %.0fs", config.name, outcome.retry_after
            )
            attempt += 1
            await self._sleep(outcome.retry_after)

    async def _attempt(self, site: str, deck_id: str, url: httpx.URL, accept: str) -> _Attempt:
        """One request. Never raises for a site problem; the response is always closed."""
        try:
            async with asyncio.timeout(self._settings.deck_site_timeout_seconds):
                request = self._http.build_request("GET", url, headers={"Accept": accept})
                response = await self._http.send(request, stream=True)
                try:
                    return await self._handle(response, site, deck_id)
                finally:
                    await response.aclose()
        except (TimeoutError, httpx.TimeoutException):
            return _failed(site, deck_id, DeckFetchFailure.TIMEOUT)
        except httpx.HTTPError as exc:
            logger.warning("deck site %s request failed: %s", site, type(exc).__name__)
            return _failed(site, deck_id, DeckFetchFailure.NETWORK_ERROR, detail=type(exc).__name__)

    async def _handle(self, response: httpx.Response, site: str, deck_id: str) -> _Attempt:
        status = response.status_code
        if status == 429:
            outcome = _failed(site, deck_id, DeckFetchFailure.RATE_LIMITED, status)
            outcome.retry_after = parse_retry_after(
                response.headers.get("Retry-After"), default=DEFAULT_RETRY_AFTER_SECONDS
            )
            return outcome
        if status in (401, 403):
            return _failed(site, deck_id, DeckFetchFailure.BLOCKED, status)
        if status in (404, 410):
            return _failed(site, deck_id, DeckFetchFailure.NOT_FOUND, status)
        if status >= 500:
            return _failed(site, deck_id, DeckFetchFailure.SERVER_ERROR, status)
        if not response.is_success:
            return _failed(site, deck_id, DeckFetchFailure.UNEXPECTED_RESPONSE, status)
        limit = self._settings.deck_site_max_response_bytes
        declared = response.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > limit:
            return _failed(site, deck_id, DeckFetchFailure.TOO_LARGE, status)
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limit:
                return _failed(site, deck_id, DeckFetchFailure.TOO_LARGE, status)
            chunks.append(chunk)
        content_type = response.headers.get("Content-Type", "")
        return _Attempt(fetched=DeckFetched(site, deck_id, b"".join(chunks), content_type, False))


def _failed(
    site: str,
    deck_id: str,
    reason: DeckFetchFailure,
    status: int | None = None,
    *,
    detail: str = "",
) -> _Attempt:
    return _Attempt(failure=DeckFetchFailed(site, deck_id, reason, status, detail))
