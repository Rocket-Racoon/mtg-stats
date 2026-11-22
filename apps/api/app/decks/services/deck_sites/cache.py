"""Redis cache of fetched deck bodies, keyed by site and deck ID (S-4.2.1).

Only successful fetches are stored, as the raw body plus its content type, for the TTL from
settings. Every Redis failure (error, timeout, an unreadable entry) is logged and treated as a
miss or a skipped write, so a Redis outage slows imports down but never breaks them.
"""

import asyncio
import logging
from dataclasses import dataclass

from redis.exceptions import RedisError

from app.core.config import Settings, get_settings
from app.core.rate_limit import key as rate_limit_key
from app.core.redis import get_redis

logger = logging.getLogger("app.decks.services.deck_sites.cache")

_SEPARATOR = b"\n"


@dataclass(frozen=True, slots=True)
class CachedBody:
    """A cached response body and its ``Content-Type``."""

    body: bytes
    content_type: str


def cache_key(site: str, deck_id: str) -> str:
    """The Redis key for one deck: ``<prefix>:decksite:<site>:<deck_id>``."""
    return rate_limit_key("decksite", site, deck_id)


class DeckCache:
    """Reads and writes cached deck bodies; never raises on a Redis problem."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def get(self, site: str, deck_id: str) -> CachedBody | None:
        """The cached body, or None on a miss or any cache failure."""
        try:
            async with asyncio.timeout(self._settings.redis_call_timeout_seconds):
                raw = await get_redis().get(cache_key(site, deck_id))
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("deck cache read failed (%s); fetching", type(exc).__name__)
            return None
        if raw is None:
            return None
        content_type, sep, body = bytes(raw).partition(_SEPARATOR)
        if not sep:
            logger.warning("deck cache entry unreadable; fetching")
            return None
        return CachedBody(body=body, content_type=content_type.decode("utf-8", "replace"))

    async def set(self, site: str, deck_id: str, value: CachedBody) -> None:
        """Store ``value`` for the TTL; a failure is logged and ignored."""
        header = value.content_type.replace("\n", " ").encode("utf-8")
        try:
            async with asyncio.timeout(self._settings.redis_call_timeout_seconds):
                await get_redis().set(
                    cache_key(site, deck_id),
                    header + _SEPARATOR + value.body,
                    ex=self._settings.deck_site_cache_ttl_seconds,
                )
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("deck cache write failed (%s); not cached", type(exc).__name__)
