"""Unit tests of the Spellbook cache key and Redis cache (S-5.2.1), against the test Redis."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

import app.brackets.spellbook_cache as cache_module
from app.brackets.spellbook_cache import KEY_PREFIX, SpellbookCache, cache_key, deck_lines
from app.brackets.spellbook_client import SpellbookClient
from app.brackets.spellbook_schema import CombosFound, DeckCard, SpellbookCombo
from app.brackets.tests.spellbook_support import Script, deck, json_response, page, variant
from app.core.config import get_settings
from app.core.redis import get_redis

A, B = uuid.UUID(int=10), uuid.UUID(int=11)


def test_key_ignores_card_order() -> None:
    one = [DeckCard("a", A), DeckCard("b", B)]
    assert cache_key(one) == cache_key(list(reversed(one)))
    assert cache_key(one).startswith(f"{KEY_PREFIX}:")


def test_key_changes_with_the_commander_placement_and_the_cards() -> None:
    plain = [DeckCard("a", A), DeckCard("b", B)]
    swapped = [DeckCard("a", A, is_commander=True), DeckCard("b", B)]
    assert cache_key(plain) != cache_key(swapped)
    assert cache_key(plain) != cache_key([DeckCard("a", A)])


def test_key_uses_oracle_ids_not_names_and_merges_duplicates() -> None:
    twice = [DeckCard("a", A), DeckCard("a", A)]
    assert cache_key([DeckCard("Renamed", A)]) == cache_key([DeckCard("a", A)])
    assert cache_key([DeckCard("a", A, quantity=2)]) == cache_key(twice)
    assert cache_key([DeckCard("a", A, quantity=2)]) != cache_key([DeckCard("a", A)])


def test_card_without_oracle_id_is_keyed_by_its_casefolded_name() -> None:
    assert deck_lines([DeckCard(" Plains ", None)]) == ["m:name:plains:1"]
    assert cache_key([DeckCard("Plains", None)]) == cache_key([DeckCard("plains", None)])
    assert cache_key([DeckCard("Plains", None)]) != cache_key([DeckCard("Island", None)])


@pytest.fixture
async def redis_keys() -> AsyncIterator[list[str]]:
    keys: list[str] = []
    yield keys
    if keys:
        await get_redis().delete(*keys)


async def test_lookup_round_trips_through_redis_with_the_ttl(redis_keys: list[str]) -> None:
    cards = deck()
    key = cache_key(cards)
    redis_keys.append(key)
    script = Script(json_response(page([variant()])))
    async with script.client(cache=SpellbookCache()) as client:
        first = await client.find_combos(cards)
        second = await client.find_combos(cards)
    assert isinstance(first, CombosFound)
    assert isinstance(second, CombosFound)
    assert len(script.requests) == 1
    assert second.from_cache
    assert second.combos == first.combos
    ttl = await get_redis().ttl(key)
    assert 0 < ttl <= get_settings().spellbook_cache_ttl_seconds


async def test_unreadable_entry_is_a_miss(redis_keys: list[str]) -> None:
    key = cache_key(deck())
    redis_keys.append(key)
    await get_redis().set(key, b'{"not": "combos"}')
    assert await SpellbookCache().get(key) is None


async def test_oversized_entry_is_not_stored(
    monkeypatch: pytest.MonkeyPatch, redis_keys: list[str]
) -> None:
    monkeypatch.setattr(cache_module, "MAX_ENTRY_BYTES", 10)
    key = cache_key(deck())
    redis_keys.append(key)
    await SpellbookCache().set(key, (SpellbookCombo("1-1", "OK", (), ()),))
    assert await get_redis().get(key) is None


class _DownRedis:
    async def get(self, *args: Any, **kwargs: Any) -> None:
        raise RedisConnectionError("down")

    async def set(self, *args: Any, **kwargs: Any) -> None:
        raise RedisConnectionError("down")


async def test_redis_failure_is_a_miss_and_the_lookup_still_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cache_module, "get_redis", _DownRedis)
    cache = SpellbookCache()
    assert await cache.get("k") is None
    await cache.set("k", ())
    script = Script(json_response(page([variant()])))
    async with SpellbookClient(
        transport=httpx.MockTransport(script.handler), cache=cache, sleep=script.sleep
    ) as client:
        script.responder = None
        assert isinstance(await client.find_combos(deck()), CombosFound)
