"""Deck-site clients: the polite HTTP client and its Redis cache (S-4.2.1)."""

from app.decks.services.deck_sites.archidekt import (
    ArchidektUrlError,
    archidekt_deck_url,
    fetch_archidekt_deck,
    parse_archidekt_url,
)
from app.decks.services.deck_sites.cache import CachedBody, DeckCache, cache_key
from app.decks.services.deck_sites.http import (
    DeckFetched,
    DeckFetchFailed,
    DeckFetchFailure,
    DeckFetchResult,
    DeckSiteClient,
    DeckSiteHostError,
    SiteConfig,
    default_sites,
)

__all__ = [
    "ArchidektUrlError",
    "CachedBody",
    "DeckCache",
    "DeckFetchFailed",
    "DeckFetchFailure",
    "DeckFetchResult",
    "DeckFetched",
    "DeckSiteClient",
    "DeckSiteHostError",
    "SiteConfig",
    "archidekt_deck_url",
    "cache_key",
    "default_sites",
    "fetch_archidekt_deck",
    "parse_archidekt_url",
]
