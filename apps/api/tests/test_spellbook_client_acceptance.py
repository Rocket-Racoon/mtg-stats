"""Acceptance tests of S-5.2.1 (Commander Spellbook client), written from the story's criteria.

Nothing here touches the network (``httpx.MockTransport``) and nothing really waits (``sleep`` is
injected). The mock responses follow the published OpenAPI schema of the real service (API 7.4.4):
``results`` is ONE object with the buckets ``included``, ``almostIncluded`` and so on, and a
variant's result is the list of features it ``produces``. The cache tests use the test Redis and
look the stored key up by diffing ``SCAN`` snapshots, so they do not depend on the key format.
"""

import asyncio
import json
import logging
import re
import tomllib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

import app.brackets.services as brackets_services
import app.brackets.spellbook_cache as spellbook_cache_module
from app.brackets.services import (
    CombosFound,
    ComboTemplate,
    DeckCard,
    SpellbookCombo,
    Unavailable,
    cached_deck_combos,
    find_deck_combos,
)
from app.brackets.spellbook_cache import SpellbookCache
from app.brackets.spellbook_client import SpellbookClient
from app.core.config import Settings, get_settings
from app.core.redis import get_redis
from app.core.user_agent import build_user_agent
from tests.architecture.test_routers_never_call_spellbook import spellbook_router_violations

API_DIR = Path(__file__).resolve().parents[1]
HOST = "backend.commanderspellbook.com"
ENDPOINT = f"https://{HOST}/find-my-combos"
UA_PATTERN = re.compile(r"^MTGStats/\d+\.\d+\.\d+\S* \(\+https://github\.com/[\w.-]+/[\w.-]+\)$")
CACHE_KEY_GLOB = "*spellbook*"

Responder = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


# --- builders ---------------------------------------------------------------------------------


def variant(
    variant_id: str = "618-1537",
    *,
    cards: tuple[tuple[str, uuid.UUID | None], ...] = (
        ("Thassa's Oracle", uuid.UUID(int=101)),
        ("Demonic Consultation", uuid.UUID(int=102)),
    ),
    features: tuple[str, ...] = ("Win the game",),
    templates: tuple[tuple[int, str, int, bool], ...] = ((5, "Any creature", 1, False),),
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A variant as the real API sends it (many fields we do not read)."""
    value: dict[str, Any] = {
        "id": variant_id,
        "status": "OK",
        "bracketTag": "P",
        "identity": "U",
        "popularity": 1234,
        "uses": [
            {
                "card": {"id": 1000 + i, "name": name, "oracleId": str(oracle) if oracle else None},
                "quantity": 1,
                "zoneLocations": ["H"],
                "mustBeCommander": False,
            }
            for i, (name, oracle) in enumerate(cards)
        ],
        "requires": [
            {
                "template": {"id": tid, "name": tname, "scryfallQuery": "t:creature"},
                "quantity": quantity,
                "zoneLocations": ["B"],
                "mustBeCommander": must_be_commander,
            }
            for tid, tname, quantity, must_be_commander in templates
        ],
        "produces": [
            {
                "feature": {"id": 70 + i, "name": name, "status": "S", "uncountable": False},
                "quantity": 1,
            }
            for i, name in enumerate(features)
        ],
    }
    value.update(extra or {})
    return value


def body_of(
    included: list[dict[str, Any]] | None = None,
    *,
    next_url: str | None = None,
    **buckets: list[dict[str, Any]],
) -> dict[str, Any]:
    """One page of ``POST /find-my-combos``; extra keyword arguments fill the other buckets."""
    results: dict[str, Any] = {
        "identity": "WUBRG",
        "included": included or [],
        "includedByChangingCommanders": [],
        "almostIncluded": [],
        "almostIncludedByAddingColors": [],
        "almostIncludedByChangingCommanders": [],
        "almostIncludedByAddingColorsAndChangingCommanders": [],
    }
    results.update(buckets)
    return {"count": 3, "next": next_url, "previous": None, "results": results}


def next_link(offset: int) -> str:
    return f"{ENDPOINT}?limit=1000&offset={offset}"


def reply(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def deck(*, commander: bool = True) -> list[DeckCard]:
    """A small deck with fresh oracle ids, so tests never share a cache entry."""
    cards = [DeckCard("Thassa's Oracle", uuid.uuid4()), DeckCard("Sol Ring", uuid.uuid4())]
    if commander:
        cards.insert(0, DeckCard("Kinnan, Bonder Prodigy", uuid.uuid4(), is_commander=True))
    return cards


class RecordingLimiter:
    """Counts waits and records them in the order of the shared ``events`` list."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.waits = 0

    async def wait(self) -> None:
        self.waits += 1
        self.events.append("wait")


class MemoryCache(SpellbookCache):
    """An in-memory cache, for tests that are not about Redis."""

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[str, tuple[SpellbookCombo, ...]] = {}

    async def get(self, key: str) -> tuple[SpellbookCombo, ...] | None:
        return self.store.get(key)

    async def set(self, key: str, combos: tuple[SpellbookCombo, ...]) -> None:
        self.store[key] = combos


class Harness:
    """A scripted Spellbook: replays ``items`` in order (or calls ``responder``)."""

    def __init__(self, *items: httpx.Response | Exception) -> None:
        self.items = list(items)
        self.requests: list[httpx.Request] = []
        self.events: list[str] = []
        self.sleeps: list[float] = []
        self.limiter = RecordingLimiter(self.events)
        self.responder: Responder | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.events.append("send")
        if self.responder is not None:
            result = self.responder(request)
            return result if isinstance(result, httpx.Response) else await result
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def client(
        self,
        *,
        cache: SpellbookCache | None = None,
        settings: Settings | None = None,
        real_limiter: bool = False,
    ) -> SpellbookClient:
        limiter: Any = None if real_limiter else self.limiter
        return SpellbookClient(
            settings=settings,
            transport=httpx.MockTransport(self.handler),
            limiter=limiter,
            cache=cache if cache is not None else MemoryCache(),
            sleep=self.sleep,
        )

    async def lookup(
        self, cards: list[DeckCard], *, cache: SpellbookCache | None = None, **options: Any
    ) -> Any:
        async with self.client(cache=cache, **options) as client:
            return await client.find_combos(cards)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def settings_with(**changes: object) -> Settings:
    return get_settings().model_copy(update=changes)


def found(result: Any) -> CombosFound:
    assert isinstance(result, CombosFound), result
    return result


def ids(result: Any) -> list[str]:
    return [combo.spellbook_id for combo in found(result).combos]


# --- Criterion 1: combos fully included in the deck -------------------------------------------


async def test_lookup_is_one_post_with_commanders_and_main_and_companion_by_name_and_quantity() -> (
    None
):
    script = Harness(reply(body_of()))
    cards = [
        DeckCard("Kinnan, Bonder Prodigy", uuid.uuid4(), is_commander=True),
        DeckCard("Sol Ring", uuid.uuid4()),
        DeckCard("Island", uuid.uuid4(), quantity=10),
        DeckCard("Lurrus of the Dream-Den", uuid.uuid4()),  # the companion: not a commander
    ]
    result = await script.lookup(cards)
    assert found(result).combos == ()
    assert len(script.requests) == 1
    request = script.requests[0]
    assert request.method == "POST"
    assert (request.url.scheme, request.url.host, request.url.path) == (
        "https",
        HOST,
        "/find-my-combos",
    )
    sent = script.bodies()[0]
    assert sent["commanders"] == [{"card": "Kinnan, Bonder Prodigy", "quantity": 1}]
    assert sorted(sent["main"], key=lambda line: line["card"]) == [
        {"card": "Island", "quantity": 10},
        {"card": "Lurrus of the Dream-Den", "quantity": 1},
        {"card": "Sol Ring", "quantity": 1},
    ]


async def test_lookup_without_a_commander_sends_every_card_in_main() -> None:
    script = Harness(reply(body_of()))
    await script.lookup(deck(commander=False))
    sent = script.bodies()[0]
    assert sent["commanders"] == []
    assert {line["card"] for line in sent["main"]} == {"Thassa's Oracle", "Sol Ring"}


async def test_only_the_included_bucket_is_returned_and_never_the_almost_buckets() -> None:
    included = [variant("1-1"), variant("2-2")]
    script = Harness(
        reply(
            body_of(
                included,
                includedByChangingCommanders=[variant("3-3")],
                almostIncluded=[variant("4-4")],
                almostIncludedByAddingColors=[variant("5-5")],
                almostIncludedByChangingCommanders=[variant("6-6")],
                almostIncludedByAddingColorsAndChangingCommanders=[variant("7-7")],
            )
        )
    )
    result = await script.lookup(deck())
    assert ids(result) == ["1-1", "2-2"]
    assert found(result).from_cache is False


async def test_each_combo_carries_its_id_cards_and_result_features_verbatim() -> None:
    oracle_a, oracle_b = uuid.uuid4(), uuid.uuid4()
    script = Harness(
        reply(
            body_of(
                [
                    variant(
                        "618-1537",
                        cards=(("Thassa's Oracle", oracle_a), ("Demonic Consultation", oracle_b)),
                        features=(
                            "Infinite creature tokens with haste",
                            "Win the game",
                        ),
                    )
                ]
            )
        )
    )
    combos = found(await script.lookup(deck())).combos
    assert len(combos) == 1
    combo = combos[0]
    assert combo.spellbook_id == "618-1537"
    assert [(card.name, card.oracle_id) for card in combo.cards] == [
        ("Thassa's Oracle", oracle_a),
        ("Demonic Consultation", oracle_b),
    ]
    assert [result.name for result in combo.results] == [
        "Infinite creature tokens with haste",
        "Win the game",
    ]


async def test_a_card_without_an_oracle_id_in_a_combo_is_kept_with_none() -> None:
    script = Harness(reply(body_of([variant("9-9", cards=(("Odd Card", None),))])))
    combo = found(await script.lookup(deck())).combos[0]
    assert (combo.cards[0].name, combo.cards[0].oracle_id) == ("Odd Card", None)


async def test_an_empty_included_bucket_is_a_successful_empty_answer_not_unavailable() -> None:
    script = Harness(reply(body_of([], almostIncluded=[variant("4-4")])))
    result = await script.lookup(deck())
    assert isinstance(result, CombosFound)
    assert result.combos == ()


async def test_one_page_costs_one_request() -> None:
    script = Harness(reply(body_of([variant("1-1")])))
    result = await script.lookup(deck())
    assert ids(result) == ["1-1"]
    assert len(script.requests) == 1


async def test_two_pages_cost_two_requests_and_merge_without_duplicates() -> None:
    script = Harness(
        reply(body_of([variant("1-1"), variant("2-2")], next_url=next_link(1000))),
        reply(body_of([variant("2-2"), variant("3-3")])),
    )
    result = await script.lookup(deck())
    assert ids(result) == ["1-1", "2-2", "3-3"]
    assert len(script.requests) == 2


async def test_three_pages_cost_three_requests_with_the_same_body_and_an_advancing_offset() -> None:
    script = Harness(
        reply(body_of([variant("1-1")], next_url=next_link(1000))),
        reply(body_of([variant("2-2")], next_url=next_link(2000))),
        reply(body_of([variant("3-3")])),
    )
    result = await script.lookup(deck())
    assert ids(result) == ["1-1", "2-2", "3-3"]
    assert len(script.requests) == 3
    first, second, third = script.bodies()
    assert first == second == third
    offsets = [parse_qs(request.url.query.decode()).get("offset") for request in script.requests]
    assert offsets[0] in (None, ["0"])
    assert offsets[1:] == [["1000"], ["2000"]]
    assert {request.url.host for request in script.requests} == {HOST}


async def test_a_lookup_ending_exactly_at_the_page_cap_succeeds_with_that_many_requests() -> None:
    cap = get_settings().spellbook_max_pages
    assert cap == 5
    pages = [
        reply(body_of([variant(f"{n}-{n}")], next_url=next_link(1000 * (n + 1))))
        for n in range(cap - 1)
    ]
    pages.append(reply(body_of([variant("last-1")])))
    script = Harness(*pages)
    result = await script.lookup(deck())
    assert len(ids(result)) == cap
    assert len(script.requests) == cap


async def test_a_lookup_that_still_has_a_next_link_after_the_cap_is_unavailable_not_partial() -> (
    None
):
    cap = get_settings().spellbook_max_pages
    script = Harness()
    script.responder = lambda request: reply(
        body_of(
            [variant(f"p{len(script.requests)}-1")],
            next_url=next_link(1000 * len(script.requests)),
        )
    )
    cache = MemoryCache()
    result = await script.lookup(deck(), cache=cache)
    assert isinstance(result, Unavailable)
    assert result.reason.value == "too_many_pages"
    assert len(script.requests) == cap  # never asks for more than the cap
    assert cache.store == {}


async def test_the_page_cap_comes_from_the_settings() -> None:
    script = Harness()
    script.responder = lambda request: reply(
        body_of([variant(f"p{len(script.requests)}-1")], next_url=next_link(len(script.requests)))
    )
    result = await script.lookup(deck(), settings=settings_with(spellbook_max_pages=2))
    assert isinstance(result, Unavailable)
    assert len(script.requests) == 2


@pytest.mark.parametrize(
    "foreign",
    [
        "https://evil.example/find-my-combos?limit=1000&offset=1000",
        "http://backend:8000/find-my-combos?limit=1000&offset=1000",
        "https://evil.example/somewhere/else?offset=1000",
    ],
    ids=["other_host", "internal_name_behind_proxy", "other_path"],
)
async def test_a_next_link_naming_another_host_is_never_contacted_and_the_pages_merge(
    foreign: str,
) -> None:
    script = Harness(
        reply(body_of([variant("1-1")], next_url=foreign)),
        reply(body_of([variant("2-2")])),
    )
    result = await script.lookup(deck())
    assert ids(result) == ["1-1", "2-2"]
    assert len(script.requests) == 2
    for request in script.requests:
        assert (request.url.scheme, request.url.host, request.url.path) == (
            "https",
            HOST,
            "/find-my-combos",
        )
    assert parse_qs(script.requests[1].url.query.decode())["offset"] == ["1000"]


@pytest.mark.parametrize(
    "next_url",
    [
        f"{ENDPOINT}?limit=1000&offset=0",
        f"{ENDPOINT}?limit=1000",
        f"{ENDPOINT}?limit=1000&offset=abc",
        f"{ENDPOINT}?limit=1000&offset=1.5",
        f"{ENDPOINT}?limit=1000&offset=-1000",
        f"{ENDPOINT}?limit=1000&offset=",
        f"{ENDPOINT}?limit=1000&offset=1000&offset=2000",
        "not a url",
    ],
    ids=[
        "repeats_the_start",
        "missing",
        "not_an_integer",
        "decimal",
        "negative",
        "empty",
        "twice",
        "garbage",
    ],
)
async def test_a_first_page_next_link_with_a_bad_offset_is_schema_mismatch_with_no_more_requests(
    next_url: str,
) -> None:
    script = Harness(reply(body_of([variant("1-1")], next_url=next_url)), reply(body_of()))
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "schema_mismatch"
    assert len(script.requests) == 1


@pytest.mark.parametrize("offset", [1000, 500, 0], ids=["repeats", "goes_backwards", "to_start"])
async def test_a_later_next_link_that_does_not_advance_is_schema_mismatch(offset: int) -> None:
    script = Harness(
        reply(body_of([variant("1-1")], next_url=next_link(1000))),
        reply(body_of([variant("2-2")], next_url=next_link(offset))),
        reply(body_of([variant("3-3")])),
    )
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "schema_mismatch"
    assert len(script.requests) == 2


# --- Criterion 2: User-Agent and timeout -----------------------------------------


async def test_every_request_carries_the_exact_user_agent_pages_and_retries_included() -> None:
    script = Harness(
        httpx.Response(429, headers={"Retry-After": "1"}),
        reply(body_of([variant("1-1")], next_url=next_link(1000))),
        reply(body_of([variant("2-2")])),
    )
    result = await script.lookup(deck())
    assert ids(result) == ["1-1", "2-2"]
    assert len(script.requests) == 3
    declared = tomllib.loads((API_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    expected = f"MTGStats/{declared} (+{get_settings().repo_url})"
    for request in script.requests:
        agent = request.headers["User-Agent"]
        assert agent == expected
        assert UA_PATTERN.fullmatch(agent)
        assert "@" not in agent
    assert expected == build_user_agent()


async def test_user_agent_repo_url_follows_the_settings_and_never_has_an_email() -> None:
    script = Harness(reply(body_of()))
    await script.lookup(deck(), settings=settings_with(repo_slug="acme/other-repo"))
    agent = script.requests[0].headers["User-Agent"]
    assert agent.endswith(" (+https://github.com/acme/other-repo)")
    assert "@" not in agent


def test_default_timeout_setting_is_10_seconds() -> None:
    assert Settings.model_fields["spellbook_timeout_seconds"].default == 10.0


async def test_requests_use_the_settings_timeout_by_default_and_when_changed() -> None:
    script = Harness(reply(body_of()))
    await script.lookup(deck())
    assert script.requests[0].extensions["timeout"] == {
        "connect": get_settings().spellbook_timeout_seconds,
        "read": get_settings().spellbook_timeout_seconds,
        "write": get_settings().spellbook_timeout_seconds,
        "pool": get_settings().spellbook_timeout_seconds,
    }
    changed = Harness(reply(body_of()))
    await changed.lookup(deck(), settings=settings_with(spellbook_timeout_seconds=3.5))
    assert set(changed.requests[0].extensions["timeout"].values()) == {3.5}


async def test_a_response_slower_than_the_settings_timeout_is_unavailable_timeout() -> None:
    script = Harness()

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return reply(body_of())

    script.responder = slow
    result = await script.lookup(deck(), settings=settings_with(spellbook_timeout_seconds=0.05))
    assert isinstance(result, Unavailable)
    assert result.reason.value == "timeout"


# --- Criterion 3: cache (real Redis) ----------------------------------------------------------


@pytest.fixture
async def stored_keys() -> AsyncIterator[Callable[[], Awaitable[set[str]]]]:
    """Return a coroutine giving the cache keys created since the test began; delete them after."""
    redis = get_redis()

    async def snapshot() -> set[str]:
        return {
            key.decode() if isinstance(key, bytes) else key
            async for key in redis.scan_iter(match=CACHE_KEY_GLOB)
        }

    before = await snapshot()

    async def created() -> set[str]:
        return await snapshot() - before

    yield created
    leftovers = await created()
    if leftovers:
        await redis.delete(*leftovers)


async def test_the_same_cards_in_another_order_hit_the_cache_with_no_outbound_request(
    stored_keys: Callable[[], Awaitable[set[str]]],
) -> None:
    script = Harness(reply(body_of([variant("1-1")])))
    cards = deck()
    cache = SpellbookCache()
    first = await script.lookup(cards, cache=cache)
    assert len(script.requests) == 1
    waits_after_first = script.limiter.waits
    second = await script.lookup(list(reversed(cards)), cache=cache)
    assert len(script.requests) == 1  # nothing went out for the second call
    assert script.limiter.waits == waits_after_first  # a hit never touches the limiter
    assert found(first).from_cache is False
    assert found(second).from_cache is True
    assert found(second).combos == found(first).combos
    assert len(await stored_keys()) == 1


async def test_a_cache_hit_from_a_fresh_client_instance_also_sends_nothing(
    stored_keys: Callable[[], Awaitable[set[str]]],
) -> None:
    cards = deck()
    redis_script = Harness(reply(body_of([variant("1-1")])))
    await redis_script.lookup(cards, cache=SpellbookCache())
    other = Harness()
    result = await other.lookup(cards, cache=SpellbookCache())
    assert other.requests == []
    assert ids(result) == ["1-1"]
    await stored_keys()


async def test_a_different_card_set_misses_the_cache(
    stored_keys: Callable[[], Awaitable[set[str]]],
) -> None:
    cards = deck()
    other = [*cards[:-1], DeckCard("Arcane Signet", uuid.uuid4())]
    script = Harness(reply(body_of([variant("1-1")])), reply(body_of([variant("2-2")])))
    cache = SpellbookCache()
    await script.lookup(cards, cache=cache)
    second = await script.lookup(other, cache=cache)
    assert len(script.requests) == 2
    assert ids(second) == ["2-2"]
    assert found(second).from_cache is False
    assert len(await stored_keys()) == 2


def test_default_cache_ttl_setting_is_7_days() -> None:
    assert Settings.model_fields["spellbook_cache_ttl_seconds"].default == 7 * 24 * 3600


async def test_the_stored_entry_expires_after_the_default_7_days(
    stored_keys: Callable[[], Awaitable[set[str]]],
) -> None:
    script = Harness(reply(body_of([variant("1-1")])))
    await script.lookup(deck(), cache=SpellbookCache())
    (key,) = await stored_keys()
    ttl = await get_redis().ttl(key)
    assert 604_799 <= ttl <= 604_800


async def test_the_stored_entry_expires_after_the_ttl_from_the_settings(
    stored_keys: Callable[[], Awaitable[set[str]]],
) -> None:
    script = Harness(reply(body_of([variant("1-1")])))
    cache = SpellbookCache(settings_with(spellbook_cache_ttl_seconds=1234))
    await script.lookup(deck(), cache=cache)
    (key,) = await stored_keys()
    ttl = await get_redis().ttl(key)
    assert 1233 <= ttl <= 1234


async def test_a_failed_lookup_is_not_cached_and_the_next_call_goes_out_again(
    stored_keys: Callable[[], Awaitable[set[str]]],
) -> None:
    script = Harness(httpx.Response(503), reply(body_of([variant("1-1")])))
    cards = deck()
    cache = SpellbookCache()
    first = await script.lookup(cards, cache=cache)
    assert isinstance(first, Unavailable)
    assert await stored_keys() == set()
    second = await script.lookup(cards, cache=cache)
    assert ids(second) == ["1-1"]
    assert len(script.requests) == 2


async def test_an_empty_answer_is_cached_like_any_other_success(
    stored_keys: Callable[[], Awaitable[set[str]]],
) -> None:
    script = Harness(reply(body_of([])))
    cards = deck()
    cache = SpellbookCache()
    await script.lookup(cards, cache=cache)
    again = await script.lookup(cards, cache=cache)
    assert len(script.requests) == 1
    assert found(again).combos == ()
    assert found(again).from_cache is True
    assert len(await stored_keys()) == 1


class DeadRedis:
    """A Redis client whose every call fails like a dropped connection."""

    async def get(self, *args: object, **kwargs: object) -> None:
        raise RedisConnectionError("redis is down")

    async def set(self, *args: object, **kwargs: object) -> None:
        raise RedisConnectionError("redis is down")


async def test_a_redis_outage_does_not_break_the_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spellbook_cache_module, "get_redis", DeadRedis)
    script = Harness(reply(body_of([variant("1-1")])))
    result = await script.lookup(deck(), cache=SpellbookCache())
    assert ids(result) == ["1-1"]
    assert len(script.requests) == 1


# --- Criterion 4: unavailable, never raises ---------------------------------------------------


@pytest.mark.parametrize(
    "error", [httpx.ConnectTimeout("connect"), httpx.ReadTimeout("read")], ids=["connect", "read"]
)
async def test_a_timeout_returns_unavailable_timeout(error: Exception) -> None:
    script = Harness(error)
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "timeout"


async def test_429_every_time_makes_three_requests_and_waits_retry_after_between_them() -> None:
    script = Harness(*[httpx.Response(429, headers={"Retry-After": "3"}) for _ in range(5)])
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "rate_limited"
    assert len(script.requests) == 3  # the first plus at most 2 retries
    assert script.sleeps == [3.0, 3.0]


async def test_each_429_wait_follows_its_own_retry_after_header() -> None:
    script = Harness(
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(429, headers={"Retry-After": "9"}),
    )
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert script.sleeps == [2.0, 7.0]


async def test_a_429_then_a_success_returns_the_combos() -> None:
    script = Harness(
        httpx.Response(429, headers={"Retry-After": "1"}), reply(body_of([variant("1-1")]))
    )
    result = await script.lookup(deck())
    assert ids(result) == ["1-1"]
    assert len(script.requests) == 2
    assert script.sleeps == [1.0]


async def test_two_429s_then_a_success_still_returns_the_combos() -> None:
    script = Harness(
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(429, headers={"Retry-After": "1"}),
        reply(body_of([variant("1-1")])),
    )
    result = await script.lookup(deck())
    assert ids(result) == ["1-1"]
    assert len(script.requests) == 3


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_a_5xx_returns_unavailable_server_error(status: int) -> None:
    script = Harness(httpx.Response(status), httpx.Response(status), httpx.Response(status))
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "server_error"


@pytest.mark.parametrize(
    "content",
    [b"<html>Bad gateway</html>", b"", b"not json {", b"null", b"[]", b'"text"'],
    ids=["html", "empty", "truncated", "null", "array", "string"],
)
async def test_a_200_with_a_body_that_is_not_the_expected_json_is_schema_mismatch(
    content: bytes,
) -> None:
    script = Harness(httpx.Response(200, content=content))
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "schema_mismatch"


def _without_results() -> dict[str, Any]:
    payload = body_of([variant()])
    del payload["results"]
    return payload


def _results_as_array() -> dict[str, Any]:
    payload = body_of([variant()])
    payload["results"] = [payload["results"]]
    return payload


def _without_included() -> dict[str, Any]:
    payload = body_of([variant()])
    del payload["results"]["included"]
    return payload


def _included_not_a_list() -> dict[str, Any]:
    payload = body_of([variant()])
    payload["results"]["included"] = {"id": "1-1"}
    return payload


def _variant_missing(field: str) -> Callable[[], dict[str, Any]]:
    def build() -> dict[str, Any]:
        broken = variant("1-1")
        del broken[field]
        return body_of([broken])

    return build


def _variant_with(**changes: Any) -> Callable[[], dict[str, Any]]:
    def build() -> dict[str, Any]:
        return body_of([variant("1-1", extra=changes)])

    return build


def _feature_without_name() -> dict[str, Any]:
    broken = variant("1-1")
    del broken["produces"][0]["feature"]["name"]
    return body_of([broken])


def _card_without_name() -> dict[str, Any]:
    broken = variant("1-1")
    del broken["uses"][0]["card"]["name"]
    return body_of([broken])


def _oracle_id_not_a_uuid() -> dict[str, Any]:
    broken = variant("1-1")
    broken["uses"][0]["card"]["oracleId"] = "not-a-uuid"
    return body_of([broken])


def _one_bad_variant_among_good_ones() -> dict[str, Any]:
    broken = variant("2-2")
    del broken["uses"]
    return body_of([variant("1-1"), broken, variant("3-3")])


def _requires_template_without_must_be_commander() -> dict[str, Any]:
    broken = variant("1-1")
    del broken["requires"][0]["mustBeCommander"]
    return body_of([broken])


def _requires_template_without_id() -> dict[str, Any]:
    broken = variant("1-1")
    del broken["requires"][0]["template"]["id"]
    return body_of([broken])


def _requires_template_without_name() -> dict[str, Any]:
    broken = variant("1-1")
    del broken["requires"][0]["template"]["name"]
    return body_of([broken])


def _requires_without_template() -> dict[str, Any]:
    broken = variant("1-1")
    del broken["requires"][0]["template"]
    return body_of([broken])


def _requires_quantity_not_a_number() -> dict[str, Any]:
    broken = variant("1-1")
    broken["requires"][0]["quantity"] = "one"
    return body_of([broken])


DRIFTED = {
    "variant_without_requires": _variant_missing("requires"),
    "requires_is_object": _variant_with(requires={"template": "x"}),
    "requires_is_null": _variant_with(requires=None),
    "requires_template_without_id": _requires_template_without_id,
    "requires_template_without_name": _requires_template_without_name,
    "requires_entry_without_template": _requires_without_template,
    "requires_quantity_not_a_number": _requires_quantity_not_a_number,
    "no_results": _without_results,
    "results_is_array": _results_as_array,
    "no_included": _without_included,
    "included_not_list": _included_not_a_list,
    "variant_without_id": _variant_missing("id"),
    "variant_without_uses": _variant_missing("uses"),
    "variant_without_produces": _variant_missing("produces"),
    "id_is_integer": _variant_with(id=618),
    "id_is_null": _variant_with(id=None),
    "uses_is_object": _variant_with(uses={"card": "x"}),
    "uses_is_string": _variant_with(uses="Thassa's Oracle"),
    "produces_is_object": _variant_with(produces={"feature": "x"}),
    "produces_is_null": _variant_with(produces=None),
    "feature_without_name": _feature_without_name,
    "card_without_name": _card_without_name,
    "oracle_id_not_a_uuid": _oracle_id_not_a_uuid,
    "one_bad_variant_among_good": _one_bad_variant_among_good_ones,
}


@pytest.mark.parametrize("build", list(DRIFTED.values()), ids=list(DRIFTED))
async def test_a_response_that_drifted_from_the_expected_schema_is_schema_mismatch(
    build: Callable[[], dict[str, Any]],
) -> None:
    script = Harness(reply(build()))
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "schema_mismatch"


async def test_a_schema_mismatch_on_a_later_page_is_unavailable_not_a_partial_answer() -> None:
    script = Harness(
        reply(body_of([variant("1-1")], next_url=next_link(1000))),
        httpx.Response(200, content=b"<html>oops</html>"),
    )
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "schema_mismatch"


async def test_unknown_extra_fields_anywhere_are_tolerated() -> None:
    payload = body_of(
        [
            variant(
                "1-1",
                extra={
                    "somethingNew": {"a": [1, 2]},
                    "notes": "added by Spellbook in 8.0",
                },
            )
        ]
    )
    payload["results"]["newBucket"] = [variant("8-8")]
    payload["results"]["included"][0]["uses"][0]["card"]["futureField"] = True
    payload["results"]["included"][0]["produces"][0]["feature"]["futureField"] = None
    payload["extraTopLevel"] = 1
    script = Harness(reply(payload))
    assert ids(await script.lookup(deck())) == ["1-1"]


async def test_a_3xx_is_not_followed_and_is_unavailable() -> None:
    script = Harness(
        httpx.Response(301, headers={"Location": "https://elsewhere.example/find-my-combos"}),
        reply(body_of([variant("1-1")])),
    )
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "redirect"
    assert len(script.requests) == 1
    assert {request.url.host for request in script.requests} == {HOST}


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx.ConnectError("refused"), "transport_error"),
        (httpx.ReadError("reset"), "transport_error"),
        (httpx.RemoteProtocolError("bad framing"), "transport_error"),
    ],
    ids=["connect_error", "read_error", "protocol_error"],
)
async def test_a_transport_error_returns_unavailable(error: Exception, reason: str) -> None:
    script = Harness(error)
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == reason


async def test_a_response_body_that_never_ends_is_cut_off_as_response_too_large() -> None:
    script = Harness()
    chunk = b" " * (1024 * 1024)

    async def endless() -> AsyncIterator[bytes]:
        while True:
            yield chunk

    script.responder = lambda request: httpx.Response(200, content=endless())
    result = await script.lookup(deck(), settings=settings_with(spellbook_timeout_seconds=20))
    assert isinstance(result, Unavailable)
    assert result.reason.value == "response_too_large"


async def test_a_declared_oversized_body_is_response_too_large() -> None:
    script = Harness(httpx.Response(200, headers={"Content-Length": str(2**40)}, content=b"{}"))
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "response_too_large"


async def test_an_unexpected_error_in_the_transport_still_never_raises() -> None:
    script = Harness(RuntimeError("boom"))
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value  # a reason string is always present


async def test_find_deck_combos_never_raises_when_the_service_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_init = SpellbookClient.__init__

    def init(self: SpellbookClient, **kwargs: Any) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        real_init(
            self,
            transport=httpx.MockTransport(refuse),
            cache=MemoryCache(),
            limiter=RecordingLimiter([]),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(SpellbookClient, "__init__", init)
    result = await find_deck_combos(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "transport_error"


async def test_cancellation_is_not_swallowed() -> None:
    script = Harness()
    started = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    script.responder = hang
    task = asyncio.create_task(script.lookup(deck()))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- Politeness -------------------------------------------------------------------------------


async def test_every_request_waits_for_the_limiter_first_pages_and_retries_included() -> None:
    script = Harness(
        httpx.Response(429, headers={"Retry-After": "1"}),
        reply(body_of([variant("1-1")], next_url=next_link(1000))),
        reply(body_of([variant("2-2")])),
    )
    await script.lookup(deck())
    assert script.events == ["wait", "send", "wait", "send", "wait", "send"]
    assert script.limiter.waits == len(script.requests) == 3


async def test_a_cache_hit_never_touches_the_limiter() -> None:
    cache = MemoryCache()
    cards = deck()
    first = Harness(reply(body_of([variant("1-1")])))
    await first.lookup(cards, cache=cache)
    second = Harness()
    result = await second.lookup(cards, cache=cache)
    assert found(result).from_cache is True
    assert second.limiter.waits == 0
    assert second.requests == []


async def test_default_limiter_spaces_requests_1_second_apart_on_one_key_for_the_host() -> None:
    assert Settings.model_fields["spellbook_min_interval_ms"].default == 1000
    redis = get_redis()
    stale = [key async for key in redis.scan_iter(match="*commanderspellbook*")]
    if stale:
        await redis.delete(*stale)
    seconds, micros = await redis.time()
    start_ms = int(seconds) * 1000 + int(micros) // 1000
    script = Harness(
        reply(body_of([variant("1-1")], next_url=next_link(1000))),
        reply(body_of([variant("2-2")])),
    )
    try:
        result = await script.lookup(deck(), real_limiter=True)
        assert ids(result) == ["1-1", "2-2"]
        keys = [key async for key in redis.scan_iter(match="*commanderspellbook*")]
        assert len(keys) == 1
        assert HOST in (keys[0].decode() if isinstance(keys[0], bytes) else keys[0])
        reserved_until = int(await redis.get(keys[0]))
        # two reservations of 1000 ms each, from "now" (the first slot is free)
        assert 2000 <= reserved_until - start_ms <= 2000 + 1500
        # the second request was made to wait for the first slot (about one interval)
        assert len(script.sleeps) == 1
        assert 0.5 < script.sleeps[0] <= 1.0
    finally:
        left = [key async for key in redis.scan_iter(match="*commanderspellbook*")]
        if left:
            await redis.delete(*left)


# --- Architecture -----------------------------------------------------------------------------


def test_the_public_interface_does_not_export_the_client_class() -> None:
    assert "SpellbookClient" not in brackets_services.__all__
    assert not hasattr(brackets_services, "SpellbookClient")
    assert "find_deck_combos" in brackets_services.__all__


def test_a_router_importing_the_client_is_flagged_in_a_synthetic_tree(tmp_path: Path) -> None:
    files = {
        "brackets/spellbook_client.py": "class SpellbookClient: ...\n",
        "decks/router.py": (
            "from app.brackets.spellbook_client import SpellbookClient\n"
            "client = SpellbookClient()\n"
        ),
    }
    for name, source in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    problems = spellbook_router_violations(tmp_path)
    assert problems
    assert all(problem.startswith("decks/router.py:") for problem in problems)


# --- Logging ----------------------------------------------------------------------------------


@pytest.fixture
def all_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


async def test_a_schema_mismatch_is_logged_without_card_names_or_the_decklist(
    all_logs: pytest.LogCaptureFixture,
) -> None:
    names = ["Zzyzx Quuxwhistle", "Plugh Frobnitzer", "Xyzzy Bazquux"]
    cards = [
        DeckCard(names[0], uuid.uuid4(), is_commander=True),
        DeckCard(names[1], uuid.uuid4()),
        DeckCard(names[2], uuid.uuid4()),
    ]
    broken = variant("1-1", cards=(("Wibble Snorkelfast", uuid.uuid4()),))
    del broken["produces"]
    script = Harness(reply(body_of([broken])))
    result = await script.lookup(cards)
    assert isinstance(result, Unavailable)
    assert result.reason.value == "schema_mismatch"
    assert all_logs.records, "the mismatch should be logged"
    rendered = all_logs.text + "\n".join(
        f"{record.getMessage()} {record.exc_text or ''}" for record in all_logs.records
    )
    for secret in [*names, "Wibble Snorkelfast"]:
        assert secret not in rendered
    assert result.detail
    for secret in [*names, "Wibble Snorkelfast"]:
        assert secret not in result.detail


# --- Templates are part of a combo (criterion 1) ----------------------------------------------


async def test_a_combo_with_named_cards_only_has_no_templates() -> None:
    script = Harness(reply(body_of([variant("1-1", templates=())])))
    combo = found(await script.lookup(deck())).combos[0]
    assert combo.templates == ()
    assert len(combo.cards) == 2


async def test_a_combo_with_one_template_returns_its_id_name_quantity_and_commander_flag() -> None:
    script = Harness(
        reply(body_of([variant("1-1", templates=((55, "Any sacrifice outlet", 1, False),))]))
    )
    combo = found(await script.lookup(deck())).combos[0]
    assert combo.templates == (
        ComboTemplate(
            template_id=55, name="Any sacrifice outlet", quantity=1, must_be_commander=False
        ),
    )


async def test_a_combo_with_several_templates_returns_them_in_order_verbatim() -> None:
    script = Harness(
        reply(
            body_of(
                [
                    variant(
                        "1-1",
                        templates=(
                            (55, "Any sacrifice outlet", 1, False),
                            (7, "Creature with power 2 or greater", 3, False),
                            (9, "Commander that taps for mana", 1, True),
                        ),
                    )
                ]
            )
        )
    )
    combo = found(await script.lookup(deck())).combos[0]
    assert [(t.template_id, t.name, t.quantity, t.must_be_commander) for t in combo.templates] == [
        (55, "Any sacrifice outlet", 1, False),
        (7, "Creature with power 2 or greater", 3, False),
        (9, "Commander that taps for mana", 1, True),
    ]


async def test_a_combo_lists_named_cards_and_templates_so_the_cards_it_needs_can_be_counted() -> (
    None
):
    script = Harness(
        reply(
            body_of(
                [
                    variant(
                        "1-1",
                        cards=(
                            ("Kinnan, Bonder Prodigy", uuid.uuid4()),
                            ("Basalt Monolith", uuid.uuid4()),
                        ),
                        templates=((55, "Any sacrifice outlet", 1, False),),
                    )
                ]
            )
        )
    )
    combo = found(await script.lookup(deck())).combos[0]
    assert [card.name for card in combo.cards] == ["Kinnan, Bonder Prodigy", "Basalt Monolith"]
    assert [template.name for template in combo.templates] == ["Any sacrifice outlet"]
    needed = sum(card.quantity for card in combo.cards) + sum(
        template.quantity for template in combo.templates
    )
    assert needed == 3  # "commander + one card + any sac outlet" is not a two-card combo


async def test_a_template_flagged_must_be_commander_keeps_the_flag_true() -> None:
    script = Harness(reply(body_of([variant("1-1", templates=((9, "Any commander", 1, True),))])))
    combo = found(await script.lookup(deck())).combos[0]
    assert combo.templates[0].must_be_commander is True


async def test_templates_survive_the_cache_round_trip(
    stored_keys: Callable[[], Awaitable[set[str]]],
) -> None:
    cards = deck()
    script = Harness(reply(body_of([variant("1-1", templates=((55, "Any outlet", 2, True),))])))
    cache = SpellbookCache()
    first = await script.lookup(cards, cache=cache)
    second = await script.lookup(cards, cache=cache)
    assert found(second).from_cache is True
    assert found(second).combos == found(first).combos
    assert found(second).combos[0].templates == (ComboTemplate(55, "Any outlet", 2, True),)
    await stored_keys()


async def test_a_template_entry_without_must_be_commander_is_schema_mismatch() -> None:
    """The published schema marks ``requires[].mustBeCommander`` required.

    EXPECTED TO FAIL until the client reads the flag strictly (it is lenient, default False).
    """
    script = Harness(reply(_requires_template_without_must_be_commander()))
    result = await script.lookup(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "schema_mismatch"


# --- Lookup deadline --------------------------------------------------------------------------


def test_default_lookup_deadline_setting_is_60_seconds() -> None:
    assert Settings.model_fields["spellbook_lookup_deadline_seconds"].default == 60.0


class HangingLimiter:
    """A limiter whose wait never ends (nothing really waits: the deadline must cut it)."""

    async def wait(self) -> None:
        await asyncio.Event().wait()


async def test_a_limiter_that_never_lets_the_request_through_ends_at_the_deadline() -> None:
    script = Harness(reply(body_of([variant("1-1")])))
    script.limiter = HangingLimiter()  # type: ignore[assignment]
    result = await asyncio.wait_for(
        script.lookup(deck(), settings=settings_with(spellbook_lookup_deadline_seconds=0.1)), 5
    )
    assert isinstance(result, Unavailable)
    assert result.reason.value == "timeout"
    assert script.requests == []


async def test_429_waits_that_pass_the_deadline_end_the_lookup_as_timeout_not_a_hang() -> None:
    script = Harness(*[httpx.Response(429, headers={"Retry-After": "5"}) for _ in range(3)])

    async def hang_sleep(seconds: float) -> None:
        await asyncio.Event().wait()

    script.sleep = hang_sleep  # type: ignore[method-assign]
    result = await asyncio.wait_for(
        script.lookup(deck(), settings=settings_with(spellbook_lookup_deadline_seconds=0.1)), 5
    )
    assert isinstance(result, Unavailable)
    assert result.reason.value == "timeout"
    assert len(script.requests) == 1


async def test_paging_that_never_finishes_in_time_is_unavailable_timeout_and_not_cached() -> None:
    script = Harness()

    async def slow_page(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.06)
        n = len(script.requests)
        return reply(body_of([variant(f"p{n}-1")], next_url=next_link(1000 * n)))

    script.responder = slow_page
    cache = MemoryCache()
    result = await script.lookup(
        deck(),
        cache=cache,
        settings=settings_with(spellbook_lookup_deadline_seconds=0.1, spellbook_max_pages=50),
    )
    assert isinstance(result, Unavailable)
    assert result.reason.value == "timeout"
    assert cache.store == {}


# --- Service entry points ---------------------------------------------------------------------


def patch_client(
    monkeypatch: pytest.MonkeyPatch, script: Harness, *, cache: SpellbookCache | None = None
) -> None:
    """Make ``SpellbookClient()`` (as ``find_deck_combos`` builds it) use the scripted transport."""
    real_init = SpellbookClient.__init__

    def init(self: SpellbookClient, **kwargs: Any) -> None:
        real_init(
            self,
            transport=httpx.MockTransport(script.handler),
            cache=cache if cache is not None else MemoryCache(),
            limiter=script.limiter,  # type: ignore[arg-type]
            sleep=script.sleep,
        )

    monkeypatch.setattr(SpellbookClient, "__init__", init)


async def test_find_deck_combos_never_raises_when_building_the_client_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(self: SpellbookClient, **kwargs: Any) -> None:
        raise RuntimeError("settings are broken")

    monkeypatch.setattr(SpellbookClient, "__init__", broken)
    result = await find_deck_combos(deck())
    assert isinstance(result, Unavailable)
    assert result.reason.value == "internal_error"


async def test_find_deck_combos_never_raises_when_closing_the_client_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = Harness(reply(body_of([variant("1-1")])))
    patch_client(monkeypatch, script)

    async def broken_close(self: SpellbookClient) -> None:
        raise RuntimeError("close failed")

    monkeypatch.setattr(SpellbookClient, "aclose", broken_close)
    result = await find_deck_combos(deck())
    assert isinstance(result, (CombosFound, Unavailable))


async def test_find_deck_combos_does_not_swallow_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = Harness()
    started = asyncio.Event()

    async def hang(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    script.responder = hang
    patch_client(monkeypatch, script)
    task = asyncio.create_task(find_deck_combos(deck()))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_cached_deck_combos_hits_after_a_find_for_the_same_cards_in_another_order(
    monkeypatch: pytest.MonkeyPatch,
    stored_keys: Callable[[], Awaitable[set[str]]],
) -> None:
    script = Harness(reply(body_of([variant("1-1", templates=((55, "Any outlet", 1, False),))])))
    patch_client(monkeypatch, script, cache=SpellbookCache())
    cards = deck()
    first = await find_deck_combos(cards)
    assert ids(first) == ["1-1"]
    requests_before, waits_before = len(script.requests), script.limiter.waits
    hit = await cached_deck_combos(list(reversed(cards)))
    assert hit is not None
    assert hit.from_cache is True
    assert hit.combos == found(first).combos
    assert len(script.requests) == requests_before
    assert script.limiter.waits == waits_before
    await stored_keys()


async def test_cached_deck_combos_miss_returns_none_with_no_request_and_no_limiter_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = Harness(reply(body_of([variant("1-1")])))
    patch_client(monkeypatch, script)
    assert await cached_deck_combos(deck()) is None
    assert script.requests == []
    assert script.limiter.waits == 0


async def test_cached_deck_combos_does_not_serve_the_entry_of_another_card_set(
    monkeypatch: pytest.MonkeyPatch,
    stored_keys: Callable[[], Awaitable[set[str]]],
) -> None:
    script = Harness(reply(body_of([variant("1-1")])))
    patch_client(monkeypatch, script, cache=SpellbookCache())
    cards = deck()
    await find_deck_combos(cards)
    other = [DeckCard("Another Card", uuid.uuid4()), *cards[1:]]
    assert await cached_deck_combos(other) is None
    await stored_keys()


async def test_cached_deck_combos_returns_none_on_a_redis_outage_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = Harness()
    patch_client(monkeypatch, script)
    monkeypatch.setattr(spellbook_cache_module, "get_redis", DeadRedis)
    assert await cached_deck_combos(deck()) is None
    assert script.requests == []


async def test_cached_deck_combos_of_an_empty_deck_has_no_combos_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = Harness()
    patch_client(monkeypatch, script)
    result = await cached_deck_combos([])
    assert result is not None
    assert result.combos == ()
    assert script.requests == []
    assert script.limiter.waits == 0


def test_the_public_interface_exports_the_cache_only_entry_point() -> None:
    assert "cached_deck_combos" in brackets_services.__all__
    assert "ComboTemplate" in brackets_services.__all__


# --- Architecture: request paths never wait on Spellbook --------------------------------------


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for name, source in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return root


def test_a_router_importing_find_deck_combos_is_flagged(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "decks/router.py": "from app.brackets.services import find_deck_combos\n",
            "stats/router.py": (
                "from app import brackets\nx = brackets.services.find_deck_combos\n"
            ),
        },
    )
    flagged = {problem.split(":")[0] for problem in spellbook_router_violations(root)}
    assert flagged == {"decks/router.py", "stats/router.py"}


def test_a_router_using_cached_deck_combos_and_the_value_types_is_not_flagged(
    tmp_path: Path,
) -> None:
    root = _write_tree(
        tmp_path,
        {
            "decks/router.py": (
                "from app.brackets.services import CombosFound, DeckCard, cached_deck_combos\n"
            ),
            "jobs/runner.py": "from app.brackets.services import find_deck_combos\n",
        },
    )
    assert spellbook_router_violations(root) == []
