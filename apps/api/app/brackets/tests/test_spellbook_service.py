"""Unit tests of the public Spellbook services (S-5.2.1): never raise, and the cache-only reader."""

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

import app.brackets.services.spellbook as service_module
import app.brackets.spellbook_cache as cache_module
from app.brackets.services import (
    CombosFound,
    DeckCard,
    Unavailable,
    UnavailableReason,
    cached_deck_combos,
    find_deck_combos,
)
from app.brackets.spellbook_cache import KEY_PREFIX, SpellbookCache, cache_key
from app.brackets.spellbook_client import SpellbookClient
from app.brackets.spellbook_schema import (
    ComboCard,
    ComboResult,
    ComboTemplate,
    SpellbookCombo,
)
from app.brackets.tests.spellbook_support import (
    MemoryCache,
    NoLimiter,
    deck,
    json_response,
    page,
    variant,
)
from app.core.redis import get_redis

# The stored shape and its cache-key version change together: edit both, or this test fails.
STORED_SHAPE = {
    "ComboCard": ["name", "oracle_id", "quantity", "must_be_commander"],
    "ComboTemplate": ["template_id", "name", "quantity", "must_be_commander"],
    "ComboResult": ["feature_id", "name", "status", "quantity"],
    "SpellbookCombo": ["spellbook_id", "status", "cards", "templates", "results"],
}
STORED_KEY_PREFIX = "mtgstats:spellbook:combos:v2"


def test_stored_shape_and_cache_key_version_change_together() -> None:
    classes = (ComboCard, ComboTemplate, ComboResult, SpellbookCombo)
    shape = {cls.__name__: [f.name for f in dataclasses.fields(cls)] for cls in classes}
    assert shape == STORED_SHAPE
    assert KEY_PREFIX == STORED_KEY_PREFIX


def _install(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real_init = SpellbookClient.__init__

    def init(self: SpellbookClient, **kwargs: Any) -> None:
        real_init(
            self,
            transport=httpx.MockTransport(handler),
            cache=MemoryCache(),
            limiter=NoLimiter(),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(SpellbookClient, "__init__", init)


async def test_find_deck_combos_returns_the_found_combos(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda request: json_response(page([variant()])))
    result = await find_deck_combos(deck())
    assert isinstance(result, CombosFound)
    assert len(result.combos) == 1


async def test_a_failing_client_constructor_is_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(self: SpellbookClient, **kwargs: Any) -> None:
        raise RuntimeError("settings are broken")

    monkeypatch.setattr(SpellbookClient, "__init__", broken)
    result = await find_deck_combos(deck())
    assert result == Unavailable(UnavailableReason.INTERNAL_ERROR, "RuntimeError")


async def test_a_failing_close_does_not_lose_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda request: json_response(page([variant()])))

    async def broken_close(self: SpellbookClient) -> None:
        raise OSError("close failed")

    monkeypatch.setattr(SpellbookClient, "aclose", broken_close)
    assert isinstance(await find_deck_combos(deck()), CombosFound)


async def test_find_combos_raising_is_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda request: json_response(page([])))

    async def broken(self: SpellbookClient, cards: list[DeckCard]) -> None:
        raise ValueError("boom")

    monkeypatch.setattr(SpellbookClient, "find_combos", broken)
    result = await find_deck_combos(deck())
    assert result == Unavailable(UnavailableReason.INTERNAL_ERROR, "ValueError")


async def test_cancellation_propagates_and_the_client_is_still_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, lambda request: json_response(page([])))
    closed: list[bool] = []
    real_close = SpellbookClient.aclose

    async def hang(self: SpellbookClient, cards: list[DeckCard]) -> None:
        await asyncio.sleep(30)

    async def close(self: SpellbookClient) -> None:
        closed.append(True)
        await real_close(self)

    monkeypatch.setattr(SpellbookClient, "find_combos", hang)
    monkeypatch.setattr(SpellbookClient, "aclose", close)
    task = asyncio.create_task(find_deck_combos(deck()))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]


@pytest.fixture
async def redis_keys() -> AsyncIterator[list[str]]:
    keys: list[str] = []
    yield keys
    if keys:
        await get_redis().delete(*keys)


async def test_cached_deck_combos_returns_none_on_a_miss_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("cached_deck_combos must not touch the network")

    _install(monkeypatch, forbidden)
    assert await cached_deck_combos(deck()) is None


async def test_cached_deck_combos_returns_a_stored_answer_as_from_cache(
    redis_keys: list[str],
) -> None:
    cards = deck()
    key = cache_key(cards)
    redis_keys.append(key)
    combo = SpellbookCombo("1-1", "OK", (), (), ())
    await SpellbookCache().set(key, (combo,))
    result = await cached_deck_combos(list(reversed(cards)))
    assert result == CombosFound((combo,), from_cache=True)


async def test_cached_deck_combos_for_an_empty_deck_is_an_empty_answer() -> None:
    assert await cached_deck_combos([]) == CombosFound(())


async def test_cached_deck_combos_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(SpellbookCache):
        async def get(self, key: str) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(service_module, "SpellbookCache", Broken)
    assert await cached_deck_combos(deck()) is None


async def test_cached_deck_combos_survives_a_redis_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    class Down:
        async def get(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("down")

    monkeypatch.setattr(cache_module, "get_redis", Down)
    assert await cached_deck_combos(deck()) is None
