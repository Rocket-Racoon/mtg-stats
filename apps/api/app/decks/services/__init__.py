"""Public interface of the ``decks`` domain; other code imports only from here."""

from app.decks.services.deck_sites import (
    DeckFetched,
    DeckFetchFailed,
    DeckFetchFailure,
    DeckFetchResult,
    DeckSiteClient,
    DeckSiteHostError,
    SiteConfig,
    default_sites,
)
from app.decks.services.text_parser import (
    Board,
    ParsedDecklist,
    ParsedLine,
    UnparsedLine,
    parse_decklist,
)

__all__ = [
    "Board",
    "DeckFetchFailed",
    "DeckFetchFailure",
    "DeckFetchResult",
    "DeckFetched",
    "DeckSiteClient",
    "DeckSiteHostError",
    "ParsedDecklist",
    "ParsedLine",
    "SiteConfig",
    "UnparsedLine",
    "default_sites",
    "parse_decklist",
]
