"""Combo lookup services for Commander Spellbook (S-5.2.1).

Two entry points, for two kinds of caller:

* :func:`find_deck_combos` is for **jobs** (for example ``brackets.assess_deck_version``): it may
  call Spellbook, and so may wait up to the lookup deadline.
* :func:`cached_deck_combos` is for **request paths** (routers, for example the draft bracket
  preview): it only reads the cache. A web request never waits on Spellbook.

Neither raises (criterion 4); ``asyncio.CancelledError`` still propagates.
"""

import logging
from collections.abc import Sequence

from app.brackets.spellbook_cache import SpellbookCache, cache_key
from app.brackets.spellbook_client import SpellbookClient
from app.brackets.spellbook_schema import (
    CombosFound,
    DeckCard,
    SpellbookResult,
    Unavailable,
    UnavailableReason,
)

logger = logging.getLogger("app.brackets.services.spellbook")


async def find_deck_combos(cards: Sequence[DeckCard]) -> SpellbookResult:
    """The combos fully included in a deck version's cards, or ``Unavailable`` with a reason.

    For jobs, not request paths. One lookup per deck version (decision E7): cached for 7 days,
    rate limited across workers, bounded by a deadline, and it never raises: a failure anywhere,
    building or closing the client included, is ``Unavailable(internal_error)``. ``cards`` holds
    the commander(s) with ``is_commander=True`` and every other card (main board, companion) with
    ``False``; see :class:`DeckCard` for the name form.
    """
    try:
        client = SpellbookClient()
    except Exception as exc:
        logger.exception("spellbook client could not be built")
        return Unavailable(UnavailableReason.INTERNAL_ERROR, type(exc).__name__)
    try:
        return await client.find_combos(list(cards))
    except Exception as exc:  # find_combos already guards; this keeps the promise regardless
        logger.exception("spellbook lookup failed unexpectedly")
        return Unavailable(UnavailableReason.INTERNAL_ERROR, type(exc).__name__)
    finally:
        try:
            await client.aclose()
        except Exception:
            logger.warning("closing the spellbook client failed", exc_info=True)


async def cached_deck_combos(cards: Sequence[DeckCard]) -> CombosFound | None:
    """The cached answer for a deck's cards, or ``None`` on a miss. For request paths.

    Never touches the network or the rate limiter, and never raises: any failure (Redis down,
    unreadable entry, settings error) is a miss. A deck with no cards has no combos.
    """
    try:
        if not cards:
            return CombosFound(())
        combos = await SpellbookCache().get(cache_key(cards))
    except Exception:
        logger.warning("spellbook cache lookup failed", exc_info=True)
        return None
    return None if combos is None else CombosFound(combos, from_cache=True)
