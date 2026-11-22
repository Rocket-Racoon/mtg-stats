"""Public interface of the ``brackets`` domain; other code imports only from here."""

from app.brackets.services.spellbook import cached_deck_combos, find_deck_combos
from app.brackets.spellbook_schema import (
    ComboCard,
    ComboResult,
    CombosFound,
    ComboTemplate,
    DeckCard,
    SpellbookCombo,
    SpellbookResult,
    Unavailable,
    UnavailableReason,
)

__all__ = [
    "ComboCard",
    "ComboResult",
    "ComboTemplate",
    "CombosFound",
    "DeckCard",
    "SpellbookCombo",
    "SpellbookResult",
    "Unavailable",
    "UnavailableReason",
    "cached_deck_combos",
    "find_deck_combos",
]
