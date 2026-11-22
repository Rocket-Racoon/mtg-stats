"""Redis cache of Commander Spellbook lookups (S-5.2.1, decision E7: 7 days).

The key is a hash of what the request sends: the deck's cards by ``oracle_id``, each with its
placement (commander or main) and quantity, sorted. Placement is part of the key because Spellbook
answers differently when the same cards have another commander (``mustBeCommander``, colour
identity). A card without an ``oracle_id`` is keyed by its casefolded name. The key carries a
format version, so a change of the stored shape retires old entries by itself.

Only successful lookups are stored (never :class:`Unavailable`). The cache never raises: a Redis
failure, a timeout or an unreadable entry is a miss, and a failed write is only logged. An entry
larger than ``MAX_ENTRY_BYTES`` is not stored.
"""

import asyncio
import hashlib
import logging
from collections.abc import Iterable

from pydantic import TypeAdapter, ValidationError
from redis.exceptions import RedisError

from app.brackets.spellbook_schema import DeckCard, SpellbookCombo
from app.core.config import Settings, get_settings
from app.core.redis import get_redis

logger = logging.getLogger("app.brackets.spellbook_cache")

KEY_PREFIX = "mtgstats:spellbook:combos:v1"
MAX_ENTRY_BYTES = 1024 * 1024

_COMBOS = TypeAdapter(tuple[SpellbookCombo, ...])


def deck_lines(cards: Iterable[DeckCard]) -> list[str]:
    """Sorted, merged ``<placement>:<identity>:<quantity>`` lines, one per distinct card."""
    totals: dict[tuple[str, str], int] = {}
    for card in cards:
        identity = str(card.oracle_id) if card.oracle_id else f"name:{card.name.strip().casefold()}"
        slot = ("c" if card.is_commander else "m", identity)
        totals[slot] = totals.get(slot, 0) + card.quantity
    return sorted(f"{place}:{identity}:{qty}" for (place, identity), qty in totals.items())


def cache_key(cards: Iterable[DeckCard]) -> str:
    """The Redis key of a deck's lookup (a SHA-256 of its sorted lines)."""
    digest = hashlib.sha256("\n".join(deck_lines(cards)).encode()).hexdigest()
    return f"{KEY_PREFIX}:{digest}"


class SpellbookCache:
    """Reads and writes lookups in Redis; every failure degrades to a miss."""

    def __init__(self, settings: Settings | None = None) -> None:
        used = settings or get_settings()
        self._ttl = used.spellbook_cache_ttl_seconds
        self._redis_timeout = used.redis_call_timeout_seconds

    async def get(self, key: str) -> tuple[SpellbookCombo, ...] | None:
        """The cached combos, or ``None`` on a miss, a Redis failure or an unreadable entry."""
        try:
            async with asyncio.timeout(self._redis_timeout):
                raw = await get_redis().get(key)
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("spellbook cache read failed: %s", type(exc).__name__)
            return None
        if raw is None:
            return None
        try:
            return _COMBOS.validate_json(raw)
        except ValidationError:
            logger.warning("spellbook cache entry unreadable; fetching again")
            return None

    async def set(self, key: str, combos: tuple[SpellbookCombo, ...]) -> None:
        """Store a successful lookup for the TTL; skip an oversized entry; never raise."""
        payload = _COMBOS.dump_json(combos)
        if len(payload) > MAX_ENTRY_BYTES:
            logger.warning(
                "spellbook lookup not cached: %d combos is %d bytes (limit %d)",
                len(combos),
                len(payload),
                MAX_ENTRY_BYTES,
            )
            return
        try:
            async with asyncio.timeout(self._redis_timeout):
                await get_redis().set(key, payload, ex=self._ttl)
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("spellbook cache write failed: %s", type(exc).__name__)
