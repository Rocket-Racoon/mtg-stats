"""Public interface of the ``brackets`` domain; other code imports only from here."""

from app.brackets.services.spellbook import find_deck_combos
from app.brackets.spellbook_schema import (
    ComboCard,
    ComboResult,
    CombosFound,
    DeckCard,
    SpellbookCombo,
    SpellbookResult,
    Unavailable,
    UnavailableReason,
)

__all__ = [
    "ComboCard",
    "ComboResult",
    "CombosFound",
    "DeckCard",
    "SpellbookCombo",
    "SpellbookResult",
    "Unavailable",
    "UnavailableReason",
    "find_deck_combos",
]
