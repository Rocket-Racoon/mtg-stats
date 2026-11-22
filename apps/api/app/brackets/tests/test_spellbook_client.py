"""Unit tests of the Commander Spellbook client internals (S-5.2.1) over ``httpx.MockTransport``."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

import app.brackets.spellbook_client as client_module
from app.brackets.spellbook_client import (
    API_HOST,
    FIND_COMBOS_PATH,
    PAGE_LIMIT,
    build_request_body,
    next_offset,
    parse_retry_after,
)
from app.brackets.spellbook_schema import (
    CombosFound,
    DeckCard,
    Unavailable,
    UnavailableReason,
)
from app.brackets.tests.spellbook_support import (
    MemoryCache,
    Script,
    deck,
    json_response,
    next_link,
    page,
    settings_with,
    variant,
)
from app.core.user_agent import build_user_agent


def reason(result: object) -> UnavailableReason:
    assert isinstance(result, Unavailable), result
    return result.reason


def test_request_body_puts_commanders_first_and_everything_else_in_main() -> None:
    cards = [
        DeckCard("Kinnan, Bonder Prodigy", uuid.UUID(int=1), is_commander=True),
        DeckCard("Sol Ring", uuid.UUID(int=2)),
        DeckCard("Companion Card", uuid.UUID(int=3)),
    ]
    assert build_request_body(cards) == {
        "commanders": [{"card": "Kinnan, Bonder Prodigy", "quantity": 1}],
        "main": [
            {"card": "Sol Ring", "quantity": 1},
            {"card": "Companion Card", "quantity": 1},
        ],
    }


def test_request_body_merges_repeated_lines_of_the_same_card() -> None:
    body = build_request_body([DeckCard("Plains", None, quantity=3), DeckCard("plains ", None)])
    assert body["main"] == [{"card": "Plains", "quantity": 4}]


@pytest.mark.parametrize(
    "cards",
    [
        [DeckCard("", None)],
        [DeckCard("   ", None)],
        [DeckCard("x" * 257, None)],
        [DeckCard("Sol Ring", None, quantity=0)],
        [DeckCard(f"Card {i}", None) for i in range(601)],
        [DeckCard(f"Commander {i}", None, is_commander=True) for i in range(13)],
    ],
)
async def test_invalid_deck_is_unavailable_without_a_request(cards: list[DeckCard]) -> None:
    script = Script()
    async with script.client() as client:
        result = await client.find_combos(cards)
    assert reason(result) is UnavailableReason.INVALID_REQUEST
    assert script.requests == []


async def test_empty_deck_finds_nothing_without_a_request() -> None:
    script = Script()
    async with script.client() as client:
        assert await client.find_combos([]) == CombosFound(())
    assert script.requests == []


def test_parse_retry_after_reads_seconds_and_http_dates() -> None:
    now = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after("-3") == 0.0
    assert parse_retry_after(None) == 10.0
    assert parse_retry_after("soon") == 10.0
    assert parse_retry_after("nan") == 10.0
    date = format_datetime(now + timedelta(seconds=20), usegmt=True)
    assert parse_retry_after(date, now=lambda: now) == 20.0
    past = format_datetime(now - timedelta(seconds=20), usegmt=True)
    assert parse_retry_after(past, now=lambda: now) == 0.0


def test_next_offset_accepts_the_endpoint_and_returns_the_offset() -> None:
    assert next_offset(next_link(1000), 0) == 1000
    assert next_offset(f"http://{API_HOST}{FIND_COMBOS_PATH}/?offset=5", 0) == 5


@pytest.mark.parametrize(
    "url",
    [
        f"https://evil.example{FIND_COMBOS_PATH}?offset=1000",
        f"https://{API_HOST}/variants?offset=1000",
        f"https://{API_HOST}{FIND_COMBOS_PATH}",
        f"https://{API_HOST}{FIND_COMBOS_PATH}?offset=abc",
        f"https://{API_HOST}{FIND_COMBOS_PATH}?offset=-1",
        f"https://{API_HOST}{FIND_COMBOS_PATH}?offset=1&offset=2",
        f"https://{API_HOST}{FIND_COMBOS_PATH}?offset=0",
        f"https://user@{API_HOST}.evil.example{FIND_COMBOS_PATH}?offset=1000",
    ],
)
def test_next_offset_rejects_a_link_that_leaves_the_endpoint_or_does_not_advance(
    url: str,
) -> None:
    with pytest.raises(client_module._LookupFailedError):
        next_offset(url, 0)


async def test_request_carries_user_agent_timeout_body_and_page_limit() -> None:
    script = Script(json_response(page([variant()])))
    async with script.client() as client:
        result = await client.find_combos(deck())
    (request,) = script.requests
    assert request.method == "POST"
    assert request.url.host == API_HOST
    assert request.url.path == FIND_COMBOS_PATH
    assert request.url.params["limit"] == str(PAGE_LIMIT)
    assert "offset" not in request.url.params
    assert request.headers["User-Agent"] == build_user_agent()
    assert "@" not in request.headers["User-Agent"]
    assert request.extensions["timeout"]["read"] == 10.0
    assert isinstance(result, CombosFound)


async def test_found_combos_carry_id_cards_and_results_as_spellbook_reports_them() -> None:
    oracle = uuid.uuid4()
    payload = page(
        [
            variant(
                "1-2",
                cards=(("Kinnan, Bonder Prodigy", oracle, True), ("Mystery", None, False)),
                features=("Infinite mana", "Win the game"),
            )
        ]
    )
    script = Script(json_response(payload))
    async with script.client() as client:
        result = await client.find_combos(deck())
    assert isinstance(result, CombosFound)
    assert not result.from_cache
    (combo,) = result.combos
    assert combo.spellbook_id == "1-2"
    assert combo.status == "OK"
    assert [(c.name, c.oracle_id, c.must_be_commander) for c in combo.cards] == [
        ("Kinnan, Bonder Prodigy", oracle, True),
        ("Mystery", None, False),
    ]
    assert [(r.name, r.status, r.feature_id) for r in combo.results] == [
        ("Infinite mana", "S", 7),
        ("Win the game", "S", 8),
    ]


async def test_only_the_included_bucket_is_returned() -> None:
    payload = page([variant("1-1")], almost=3)
    payload["results"]["includedByChangingCommanders"] = [variant("2-2")]
    script = Script(json_response(payload))
    async with script.client() as client:
        result = await client.find_combos(deck())
    assert isinstance(result, CombosFound)
    assert [c.spellbook_id for c in result.combos] == ["1-1"]


async def test_second_lookup_of_the_same_deck_is_served_from_the_cache() -> None:
    script = Script(json_response(page([variant()])))
    cache = MemoryCache()
    cards = deck()
    async with script.client(cache) as client:
        first = await client.find_combos(cards)
        second = await client.find_combos(list(reversed(cards)))
    assert len(script.requests) == 1
    assert isinstance(first, CombosFound)
    assert isinstance(second, CombosFound)
    assert second.from_cache
    assert second.combos == first.combos


async def test_failed_lookups_are_never_cached() -> None:
    script = Script(httpx.Response(500))
    cache = MemoryCache()
    async with script.client(cache) as client:
        assert reason(await client.find_combos(deck())) is UnavailableReason.SERVER_ERROR
    assert cache.sets == 0


async def test_pages_are_followed_until_next_is_null_and_merged() -> None:
    script = Script(
        json_response(page([variant("1-1")], next_url=next_link(1000))),
        json_response(page([variant("2-2"), variant("1-1")], next_url=next_link(2000))),
        json_response(page([variant("3-3")])),
    )
    async with script.client() as client:
        result = await client.find_combos(deck())
    assert isinstance(result, CombosFound)
    assert [c.spellbook_id for c in result.combos] == ["1-1", "2-2", "3-3"]
    assert [r.url.params.get("offset") for r in script.requests] == [None, "1000", "2000"]
    assert all(r.url.host == API_HOST for r in script.requests)


async def test_every_request_waits_for_the_limiter_pages_and_retries_alike() -> None:
    script = Script(
        json_response(page([], next_url=next_link(1000))),
        httpx.Response(429, headers={"Retry-After": "1"}),
        json_response(page([variant()])),
    )
    async with script.client() as client:
        await client.find_combos(deck())
    assert len(script.requests) == 3
    assert script.limiter.calls == 3


async def test_more_pages_than_the_cap_is_too_many_pages() -> None:
    script = Script()
    script.responder = lambda r: json_response(
        page([variant()], next_url=next_link(1000 * len(script.requests)))
    )
    async with script.client(settings=settings_with(spellbook_max_pages=3)) as client:
        result = await client.find_combos(deck())
    assert reason(result) is UnavailableReason.TOO_MANY_PAGES
    assert len(script.requests) == 3


async def test_a_next_link_on_another_host_stops_the_lookup() -> None:
    bad = f"https://evil.example{FIND_COMBOS_PATH}?offset=1000"
    script = Script(json_response(page([variant()], next_url=bad)))
    async with script.client() as client:
        result = await client.find_combos(deck())
    assert reason(result) is UnavailableReason.SCHEMA_MISMATCH
    assert len(script.requests) == 1


async def test_429_waits_for_retry_after_and_retries() -> None:
    script = Script(
        httpx.Response(429, headers={"Retry-After": "7"}),
        json_response(page([variant()])),
    )
    async with script.client() as client:
        result = await client.find_combos(deck())
    assert isinstance(result, CombosFound)
    assert len(script.requests) == 2
    assert script.waits() == [7.0]


async def test_429_after_two_retries_is_rate_limited() -> None:
    script = Script(*[httpx.Response(429, headers={"Retry-After": "2"})] * 3)
    async with script.client() as client:
        result = await client.find_combos(deck())
    assert reason(result) is UnavailableReason.RATE_LIMITED
    assert len(script.requests) == 3
    assert script.waits() == [2.0, 2.0]


async def test_retry_after_longer_than_the_cap_is_not_retried() -> None:
    script = Script(httpx.Response(429, headers={"Retry-After": "3600"}))
    async with script.client() as client:
        result = await client.find_combos(deck())
    assert reason(result) is UnavailableReason.RATE_LIMITED
    assert len(script.requests) == 1
    assert script.waits() == []


async def test_429_with_a_retry_after_date_in_the_past_retries_at_once() -> None:
    past = format_datetime(datetime.now(UTC) - timedelta(minutes=5), usegmt=True)
    script = Script(
        httpx.Response(429, headers={"Retry-After": past}), json_response(page([variant()]))
    )
    async with script.client() as client:
        assert isinstance(await client.find_combos(deck()), CombosFound)
    assert script.waits() == [0.0]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(500), UnavailableReason.SERVER_ERROR),
        (httpx.Response(503, headers={"Retry-After": "1"}), UnavailableReason.SERVER_ERROR),
        (
            httpx.Response(302, headers={"Location": "https://evil.example/"}),
            UnavailableReason.REDIRECT,
        ),
        (httpx.Response(404), UnavailableReason.CLIENT_ERROR),
        (httpx.Response(400, json={"detail": "bad"}), UnavailableReason.CLIENT_ERROR),
        (httpx.Response(200, content=b"<html>"), UnavailableReason.SCHEMA_MISMATCH),
    ],
)
async def test_other_responses_are_unavailable_and_not_retried(
    response: httpx.Response, expected: UnavailableReason
) -> None:
    script = Script(response)
    async with script.client() as client:
        result = await client.find_combos(deck())
    assert reason(result) is expected
    assert len(script.requests) == 1


async def test_timeouts_and_transport_errors_are_unavailable() -> None:
    script = Script(httpx.ReadTimeout("slow"), httpx.ConnectError("down"))
    async with script.client() as client:
        assert reason(await client.find_combos(deck())) is UnavailableReason.TIMEOUT
        assert reason(await client.find_combos(deck())) is UnavailableReason.TRANSPORT_ERROR


async def test_a_response_that_outlasts_the_timeout_setting_is_a_timeout() -> None:
    script = Script()

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return json_response(page([]))

    script.responder = slow
    async with script.client(settings=settings_with(spellbook_timeout_seconds=0.05)) as client:
        assert reason(await client.find_combos(deck())) is UnavailableReason.TIMEOUT


@pytest.mark.parametrize(
    "payload",
    [
        {"count": 1, "next": None, "previous": None, "results": []},
        {"next": None, "results": {"identity": "W"}},
        {"results": {"included": []}},
        page([{"status": "OK", "uses": [], "produces": []}]),
        page([{**variant(), "uses": [{"card": {"name": "x"}, "quantity": 1}]}]),
        page([{**variant(), "id": ""}]),
        page([{**variant(), "produces": [{"feature": {"name": "x"}, "quantity": 1}]}]),
        page([{**variant(), "uses": "none"}]),
        [],
    ],
)
async def test_response_that_does_not_match_the_schema_is_schema_mismatch(
    payload: object,
) -> None:
    script = Script(json_response(payload))
    cache = MemoryCache()
    async with script.client(cache) as client:
        result = await client.find_combos(deck())
    assert reason(result) is UnavailableReason.SCHEMA_MISMATCH
    assert cache.sets == 0


async def test_unknown_extra_fields_and_null_oracle_ids_are_tolerated() -> None:
    included = variant(cards=(("No Oracle", None, False),))
    included["brandNewField"] = {"anything": [1, 2]}
    payload = page([included])
    payload["surprise"] = True
    script = Script(json_response(payload))
    async with script.client() as client:
        result = await client.find_combos(deck())
    assert isinstance(result, CombosFound)
    assert result.combos[0].cards[0].oracle_id is None


async def test_schema_mismatch_log_names_the_field_but_not_the_decklist(
    caplog: pytest.LogCaptureFixture,
) -> None:
    script = Script(json_response(page([{"status": "OK", "uses": [], "produces": []}])))
    cards = deck()
    with caplog.at_level(logging.WARNING, logger="app.brackets.spellbook_client"):
        async with script.client() as client:
            await client.find_combos(cards)
    text = caplog.text
    assert "results.included.0.id" in text
    assert "missing" in text
    assert all(card.name not in text for card in cards)


async def test_oversized_body_is_response_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 100)
    script = Script(json_response(page([variant()])))
    async with script.client() as client:
        assert reason(await client.find_combos(deck())) is UnavailableReason.RESPONSE_TOO_LARGE


async def test_oversized_content_length_is_rejected_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 100)
    script = Script(httpx.Response(200, headers={"Content-Length": "5000"}, content=b"{}"))
    async with script.client() as client:
        assert reason(await client.find_combos(deck())) is UnavailableReason.RESPONSE_TOO_LARGE


async def test_unexpected_error_is_internal_error_and_never_raised() -> None:
    class BrokenCache(MemoryCache):
        async def get(self, key: str) -> None:
            raise RuntimeError("boom")

    script = Script()
    async with script.client(BrokenCache()) as client:
        assert reason(await client.find_combos(deck())) is UnavailableReason.INTERNAL_ERROR


async def test_cancellation_is_not_swallowed() -> None:
    script = Script()

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return json_response(page([]))

    script.responder = hang
    async with script.client() as client:
        task = asyncio.create_task(client.find_combos(deck()))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
