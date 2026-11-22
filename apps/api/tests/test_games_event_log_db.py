"""The event log on a migrated database with real commits (S-6.3.1).

A throwaway database is migrated to head. Two engines with their own connection pools stand in
for two API processes: appends race on the game row's ``FOR UPDATE`` lock across both.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core import db
from app.core.db import AppSession
from app.games.models import AUDITED_MUTATION_SETTING
from app.games.services import (
    Appended,
    AppendResult,
    append_event,
    canonical_json,
    read_events_since,
)
from tests.factories import GameFactory, PlaygroupFactory, create
from tests.games_support import player_actor, submission
from tests.migration_support import alembic_config, temporary_database

WAIT_SECONDS = 60.0


@dataclass
class Live:
    url: str
    game_id: UUID
    first: async_sessionmaker[AppSession]
    second: async_sessionmaker[AppSession]
    probe: AsyncEngine


@pytest.fixture
def migrated_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A throwaway database at head; always dropped (``temporary_database`` guarantees it)."""
    with temporary_database(monkeypatch) as url:
        db.get_sessionmaker.cache_clear()
        db.get_engine.cache_clear()
        command.upgrade(alembic_config(), "head")
        try:
            yield url
        finally:
            db.get_sessionmaker.cache_clear()
            db.get_engine.cache_clear()


@pytest.fixture
async def live(migrated_url: str) -> AsyncIterator[Live]:
    engines = [create_async_engine(migrated_url, pool_size=10) for _ in range(3)]
    makers = [
        async_sessionmaker(engine, class_=AppSession, expire_on_commit=False) for engine in engines
    ]
    try:
        async with makers[0]() as session:
            group = await create(session, PlaygroupFactory)
            game = await create(session, GameFactory, playgroup_id=group.id)
            await session.commit()
        yield Live(migrated_url, game.id, makers[0], makers[1], engines[2])
    finally:
        for engine in engines:
            await engine.dispose()


async def _append(
    maker: async_sessionmaker[AppSession], game_id: UUID, event_id: str | None = None
) -> AppendResult:
    async with maker() as session:
        result = await append_event(
            session,
            game_id,
            submission(game_id, event_id=event_id),
            actor=player_actor(),
        )
        await session.commit()
        return result


async def _rows(live: Live) -> list[tuple[int, str]]:
    async with live.probe.connect() as conn:
        result = await conn.execute(
            text("SELECT seq, event_id::text FROM game_events WHERE game_id = :g ORDER BY seq"),
            {"g": live.game_id},
        )
        return [(row.seq, row.event_id) for row in result]


async def test_twenty_appends_from_two_processes_get_seqs_one_to_twenty(live: Live) -> None:
    makers = [live.first, live.second]
    results = await asyncio.wait_for(
        asyncio.gather(*(_append(makers[i % 2], live.game_id) for i in range(20))),
        WAIT_SECONDS,
    )
    seqs = sorted(r.event.seq for r in results if isinstance(r, Appended))
    assert seqs == list(range(1, 21))
    rows = await _rows(live)
    assert [seq for seq, _ in rows] == list(range(1, 21))
    async with live.probe.connect() as conn:
        game = (
            await conn.execute(
                text("SELECT last_seq, state FROM games WHERE id = :g"), {"g": live.game_id}
            )
        ).one()
    assert game.last_seq == 20
    assert game.state["last_seq"] == 20


async def test_concurrent_resubmissions_of_one_event_id_store_one_row(live: Live) -> None:
    event_id = str(uuid4())
    makers = [live.first, live.second]
    results = await asyncio.wait_for(
        asyncio.gather(*(_append(makers[i % 2], live.game_id, event_id) for i in range(6))),
        WAIT_SECONDS,
    )
    assert all(isinstance(r, Appended) and r.event.seq == 1 for r in results)
    assert sum(1 for r in results if isinstance(r, Appended) and not r.duplicate) == 1
    assert await _rows(live) == [(1, event_id)]
    async with live.probe.connect() as conn:
        last_seq = (
            await conn.execute(
                text("SELECT last_seq FROM games WHERE id = :g"), {"g": live.game_id}
            )
        ).scalar_one()
    assert last_seq == 1


async def test_a_rolled_back_append_changes_nothing_and_the_next_one_reuses_the_seq(
    live: Live,
) -> None:
    await _append(live.first, live.game_id)
    async with live.first() as session:
        result = await append_event(
            session, live.game_id, submission(live.game_id), actor=player_actor()
        )
        assert isinstance(result, Appended)
        assert result.event.seq == 2
        await session.rollback()  # the caller's transaction fails after the service returned
    assert [seq for seq, _ in await _rows(live)] == [1]
    follow_up = await _append(live.second, live.game_id)
    assert isinstance(follow_up, Appended)
    assert follow_up.event.seq == 2


async def test_state_survives_jsonb_with_the_same_canonical_json(live: Live) -> None:
    payload = {"big": 2**53 - 1, "text": "café \U0001f9d9", "nested": {"b": [1, 2], "a": None}}
    async with live.first() as session:
        result = await append_event(
            session,
            live.game_id,
            submission(live.game_id, payload=payload),
            actor=player_actor(),
        )
        await session.commit()
    assert isinstance(result, Appended)
    async with live.second() as session:
        events = await read_events_since(session, live.game_id, 0)
        state_row = (
            await session.execute(
                text("SELECT state FROM games WHERE id = :g"), {"g": live.game_id}
            )
        ).scalar_one()
    assert canonical_json(events[0].payload) == canonical_json(payload)
    assert canonical_json(state_row) == canonical_json(result.state.to_json_value())


async def test_game_events_has_the_story_columns_and_unique_constraints(live: Live) -> None:
    async with live.probe.connect() as conn:
        columns = {
            row.column_name: (row.data_type, row.is_nullable)
            for row in await conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'game_events'"
                )
            )
        }
        uniques = {
            row.cols
            for row in await conn.execute(
                text(
                    "SELECT string_agg(a.attname, ',' ORDER BY k.ord) AS cols "
                    "FROM pg_constraint c "
                    "CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) "
                    "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
                    "WHERE c.conrelid = 'game_events'::regclass AND c.contype = 'u' "
                    "GROUP BY c.oid"
                )
            )
        }
    for name in ("game_id", "seq", "event_id", "type", "payload", "actor", "ts", "v"):
        assert columns[name][1] == "NO", name
    assert columns["on_behalf_of_seat"][1] == "YES"
    assert columns["payload"][0] == "jsonb"
    assert columns["actor"][0] == "jsonb"
    assert columns["ts"][0] == "timestamp with time zone"
    assert uniques == {"game_id,seq", "game_id,event_id"}


async def test_the_database_rejects_update_and_delete_of_an_event(live: Live) -> None:
    await _append(live.first, live.game_id)
    for statement in (
        "UPDATE game_events SET type = 'x'",
        "DELETE FROM game_events",
    ):
        async with live.probe.connect() as conn:
            with pytest.raises(DBAPIError, match="append-only"):
                await conn.execute(text(statement))
    assert len(await _rows(live)) == 1


async def test_a_transaction_that_opts_in_may_change_events_and_nobody_else_can(
    live: Live,
) -> None:
    await _append(live.first, live.game_id)
    async with live.probe.begin() as conn:
        await conn.execute(
            text("SELECT set_config(:n, 'on', true)"), {"n": AUDITED_MUTATION_SETTING}
        )
        await conn.execute(text("UPDATE game_events SET type = 'edited'"))
    async with live.probe.begin() as conn:  # the opt-in ended with the transaction above
        with pytest.raises(DBAPIError, match="append-only"):
            await conn.execute(text("UPDATE game_events SET type = 'again'"))
    async with live.probe.connect() as conn:
        kinds = (await conn.execute(text("SELECT type FROM game_events"))).scalars().all()
    assert kinds == ["edited"]
