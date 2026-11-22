"""Combo lookup service: the entry point other code uses for Commander Spellbook (S-5.2.1)."""

from collections.abc import Sequence

from app.brackets.spellbook_client import SpellbookClient
from app.brackets.spellbook_schema import DeckCard, SpellbookResult


async def find_deck_combos(cards: Sequence[DeckCard]) -> SpellbookResult:
    """The combos fully included in a deck version's cards, or ``Unavailable`` with a reason.

    One lookup per deck version (decision E7): cached for 7 days, rate limited across workers,
    and it never raises. ``cards`` holds the commander(s) with ``is_commander=True`` and every
    other card (main board, companion) with ``False``.
    """
    async with SpellbookClient() as client:
        return await client.find_combos(list(cards))
