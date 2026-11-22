"""Public interface of the ``decks`` domain; other code imports only from here."""

from app.decks.services.deck_sites import (
    ArchidektUrlError,
    DeckFetched,
    DeckFetchFailed,
    DeckFetchFailure,
    DeckFetchResult,
    DeckSiteClient,
    DeckSiteHostError,
    SiteConfig,
    archidekt_deck_url,
    default_sites,
    fetch_archidekt_deck,
    parse_archidekt_url,
)
from app.decks.services.text_parser import (
    Board,
    ParsedDecklist,
    ParsedLine,
    UnparsedLine,
    parse_decklist,
)

__all__ = [
    "ArchidektUrlError",
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
    "archidekt_deck_url",
    "default_sites",
    "fetch_archidekt_deck",
    "parse_archidekt_url",
    "parse_decklist",
]
