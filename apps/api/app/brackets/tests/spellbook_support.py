"""Helpers shared by the Spellbook client tests: payload builders, a scripted transport, a cache."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx

from app.brackets.spellbook_cache import SpellbookCache
from app.brackets.spellbook_client import API_HOST, FIND_COMBOS_PATH, SpellbookClient
from app.brackets.spellbook_schema import DeckCard, SpellbookCombo
from app.core.config import Settings, get_settings
from app.core.spacing_limiter import SpacingLimiter


def variant(
    variant_id: str = "618-1537",
    *,
    cards: tuple[tuple[str, uuid.UUID | None, bool], ...] = (
        ("Thassa's Oracle", uuid.UUID(int=1), False),
        ("Demonic Consultation", uuid.UUID(int=2), False),
    ),
    features: tuple[str, ...] = ("Win the game",),
) -> dict[str, Any]:
    """A Spellbook variant as the API sends it, with extra fields we ignore."""
    return {
        "id": variant_id,
        "status": "OK",
        "bracketTag": "P",
        "identity": "U",
        "uses": [
            {
                "card": {"id": i, "name": name, "oracleId": str(oracle) if oracle else None},
                "quantity": 1,
                "mustBeCommander": commander,
                "zoneLocations": ["H"],
            }
            for i, (name, oracle, commander) in enumerate(cards)
        ],
        "requires": [
            {
                "template": {"id": 5, "name": "Any creature", "scryfallQuery": "t:creature"},
                "quantity": 2,
                "mustBeCommander": False,
                "zoneLocations": ["B"],
            }
        ],
        "produces": [
            {
                "feature": {"id": 7 + i, "name": name, "status": "S", "uncountable": False},
                "quantity": 1,
            }
            for i, name in enumerate(features)
        ],
    }


def page(
    included: list[dict[str, Any]], *, next_url: str | None = None, almost: int = 1
) -> dict[str, Any]:
    """One page of ``POST /find-my-combos``."""
    return {
        "count": None,
        "next": next_url,
        "previous": None,
        "results": {
            "identity": "WUBRG",
            "included": included,
            "includedByChangingCommanders": [],
            "almostIncluded": [variant("9-9")] * almost,
            "almostIncludedByAddingColors": [],
            "almostIncludedByChangingCommanders": [],
            "almostIncludedByAddingColorsAndChangingCommanders": [],
        },
    }


def next_link(offset: int) -> str:
    """A ``next`` link as Spellbook builds it."""
    return f"https://{API_HOST}{FIND_COMBOS_PATH}?limit=1000&offset={offset}"


def deck(*, commander: bool = True) -> list[DeckCard]:
    """A small deck with unique oracle ids, so tests never share a cache key."""
    cards = [DeckCard("Thassa's Oracle", uuid.uuid4()), DeckCard("Sol Ring", uuid.uuid4())]
    if commander:
        cards.insert(0, DeckCard("Kinnan, Bonder Prodigy", uuid.uuid4(), is_commander=True))
    return cards


class MemoryCache(SpellbookCache):
    """An in-memory cache that records its writes."""

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[str, tuple[SpellbookCombo, ...]] = {}
        self.sets = 0

    async def get(self, key: str) -> tuple[SpellbookCombo, ...] | None:
        return self.store.get(key)

    async def set(self, key: str, combos: tuple[SpellbookCombo, ...]) -> None:
        self.sets += 1
        self.store[key] = combos


class NoLimiter:
    """A limiter that never waits and counts its calls (the real one needs Redis time)."""

    def __init__(self) -> None:
        self.calls = 0

    async def wait(self) -> None:
        self.calls += 1


Responder = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


class Script:
    """A scripted transport: replays responses (or raises exceptions), records everything."""

    def __init__(self, *items: httpx.Response | Exception) -> None:
        self.items = list(items)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.responder: Responder | None = None
        self.limiter = NoLimiter()

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responder is not None:
            result = self.responder(request)
            if isinstance(result, httpx.Response):
                return result
            return await result
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def client(
        self, cache: SpellbookCache | None = None, settings: Settings | None = None
    ) -> SpellbookClient:
        return SpellbookClient(
            settings=settings,
            transport=httpx.MockTransport(self.handler),
            cache=cache or MemoryCache(),
            limiter=cast(SpacingLimiter, self.limiter),
            sleep=self.sleep,
        )

    def waits(self) -> list[float]:
        """The sleeps of the 429 handling (the fake limiter never sleeps)."""
        return list(self.sleeps)


def json_response(payload: object, status: int = 200) -> httpx.Response:
    """A JSON response."""
    return httpx.Response(status, json=payload)


def settings_with(**changes: object) -> Settings:
    """The test settings with some fields changed."""
    return get_settings().model_copy(update=changes)
