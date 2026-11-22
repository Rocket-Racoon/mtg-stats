"""Archidekt: deck URL parser and fetcher on top of the shared deck-site client (S-4.3.1).

Only the numeric deck id leaves the parser, and the API URL is built here from that id alone:
user-supplied URL text never reaches :class:`DeckSiteClient`, whose cache trusts the deck id.

Archidekt's deck endpoint is undocumented (informal permission, decision of 2026-11-21), so the
response is treated defensively:

* ``404`` (observed for a deleted or never-existing deck, body ``{"error":"Deck not found."}``)
  and ``410`` are ``not_found`` through the client.
* A private deck's answer was not observed (no private deck id could be found without logging
  in). A ``200`` whose JSON says ``"private": true``, or that has no ``cards`` but an ``error`` or
  ``detail`` field, is turned into ``not_found`` here and is never cached. ``401``/``403`` stay
  ``blocked``: they cannot be told apart from a real block (S-4.2.3 maps ``blocked`` to the same
  text-paste fallback).
* A ``200`` that is not a recognisable deck (an HTML maintenance page, JSON of another shape) is
  returned as :class:`DeckFetched` but not cached, and the mapper (S-4.3.2) reports
  ``unexpected_format``. A deck body carrying another deck's id is ``unexpected_response``.
"""

import enum
import json
import re
from typing import Final
from urllib.parse import urlsplit

from app.core.errors import DomainError
from app.decks.services.deck_sites.http import (
    DeckFetchFailed,
    DeckFetchFailure,
    DeckFetchResult,
    DeckSiteClient,
)

SITE: Final = "archidekt"
MAX_DECK_ID: Final = 2_147_483_647  # int32: ids are far below this; anything above is not an id
_HOSTS: Final = frozenset({"archidekt.com", "www.archidekt.com"})
_PATH = re.compile(r"/decks/(?P<id>[1-9][0-9]{0,9})(?:/[^/]+)?/?", re.ASCII)

NOT_AN_ARCHIDEKT_URL_CODE: Final = "not_archidekt_deck_url"
NOT_AN_ARCHIDEKT_URL_MESSAGE: Final = "not an Archidekt deck URL"


class ArchidektUrlError(DomainError, ValueError):
    """The text is not an Archidekt deck URL; ``code`` is stable for the import job."""

    code = NOT_AN_ARCHIDEKT_URL_CODE
    status_code = 422
    default_message = NOT_AN_ARCHIDEKT_URL_MESSAGE


def parse_archidekt_url(url: str) -> int:
    """The deck id of ``https://[www.]archidekt.com/decks/<id>[/<slug>]``.

    A trailing slash, a query string and a fragment are tolerated, and so is whitespace around
    the whole text. Everything else raises :class:`ArchidektUrlError`: ``http://``, any other
    host (look-alikes included), credentials, a port, other Archidekt paths, and an id that is
    not plain ASCII digits without leading zeros, zero, or above 2**31 - 1.
    """
    text = url.strip()
    if any(ch.isspace() or ch == "\\" or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ArchidektUrlError
    try:
        parts = urlsplit(text)
        # Non-ASCII before lower-casing: str.lower() maps U+212A (Kelvin sign) to "k".
        if not parts.netloc.isascii():
            raise ArchidektUrlError
        host = parts.netloc.lower()
    except ValueError as exc:  # ArchidektUrlError is one too and stays as raised
        raise ArchidektUrlError from exc
    if parts.scheme.lower() != "https" or host not in _HOSTS:
        raise ArchidektUrlError
    match = _PATH.fullmatch(parts.path)
    if match is None:
        raise ArchidektUrlError
    deck_id = int(match["id"])
    if deck_id > MAX_DECK_ID:
        raise ArchidektUrlError
    return deck_id


def archidekt_deck_url(deck_id: int) -> str:
    """The public web page of a deck, for linking back (an import condition)."""
    return f"https://archidekt.com/decks/{_checked_id(deck_id)}"


class _Body(enum.Enum):
    DECK = "deck"
    NOT_FOUND = "not_found"
    WRONG_DECK = "wrong_deck"
    UNKNOWN = "unknown"


def _checked_id(deck_id: int) -> int:
    if isinstance(deck_id, bool) or not isinstance(deck_id, int) or not 0 < deck_id <= MAX_DECK_ID:
        raise ValueError(f"not an Archidekt deck id: {deck_id!r}")
    return deck_id


def _classify(body: bytes, deck_id: int) -> _Body:
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):  # ValueError includes UnicodeDecodeError
        return _Body.UNKNOWN
    if not isinstance(data, dict):
        return _Body.UNKNOWN
    if data.get("private") is True:
        return _Body.NOT_FOUND
    if isinstance(data.get("cards"), list):
        found = data.get("id")
        # Strict: the integer asked for (``True == 1`` and ``"5" != 5`` must not pass).
        return _Body.DECK if type(found) is int and found == deck_id else _Body.WRONG_DECK
    if "cards" not in data and ("error" in data or "detail" in data):
        return _Body.NOT_FOUND
    return _Body.UNKNOWN


async def fetch_archidekt_deck(client: DeckSiteClient, deck_id: int) -> DeckFetchResult:
    """Fetch one deck through the shared client; the raw JSON body is returned, not mapped.

    The URL is built from ``deck_id`` only (``ValueError`` if it is not a valid id). A private or
    deleted deck is ``DeckFetchFailed(not_found)`` and nothing is cached for it; other failures
    carry the client's reason. A body is cached only when it is a deck with this id.
    """
    checked = _checked_id(deck_id)
    result = await client.fetch(
        SITE,
        str(checked),
        f"https://archidekt.com/api/decks/{checked}/",
        cache_body=lambda body: _classify(body, checked) is _Body.DECK,
    )
    if isinstance(result, DeckFetchFailed):
        return result
    kind = _classify(result.body, checked)
    if kind is _Body.NOT_FOUND:
        return DeckFetchFailed(
            SITE, str(checked), DeckFetchFailure.NOT_FOUND, 200, "private or deleted deck"
        )
    if kind is _Body.WRONG_DECK:
        return DeckFetchFailed(
            SITE, str(checked), DeckFetchFailure.UNEXPECTED_RESPONSE, 200, "deck id mismatch"
        )
    return result
