"""``rebuild_state`` takes the game lock of ``append_event`` (S-6.3.8 AC3), with real commits.

A throwaway migrated database, so the append-only trigger on ``game_events`` is in place: a
rebuild that touched an event row would fail. Separate engines stand in for separate processes.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core import db
from app.core.db import AppSession
from app.games import repository
from app.games.services import Appended, append_event, canonical_json, rebuild_state
from tests.factories import GameFactory, PlaygroupFactory, create
from tests.games_support import insert_log, lobby_and_turns, player_actor, submission
from tests.migration_support import alembic_config, temporary_database

WAIT_SECONDS = 30.0
STILL_WAITING_SECONDS = 1.0


@dataclass
class Live:
    game_id: UUID
    holder: async_sessionmaker[AppSession]
    other: async_sessionmaker[AppSession]
    probe: AsyncEngine


@pytest.fixture
def migrated_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
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
    engines = [create_async_engine(migrated_url, pool_size=5) for _ in range(3)]
    makers = [
        async_sessionmaker(engine, class_=AppSession, expire_on_commit=False) for engine in engines
    ]
    try:
        async with makers[0]() as session:
            group = await create(session, PlaygroupFactory)
            game = await create(session, GameFactory, playgroup_id=group.id)
            await session.commit()
        yield Live(game.id, makers[0], makers[1], engines[2])
    finally:
        for engine in engines:
            await engine.dispose()


async def _stored(live: Live) -> tuple[int, dict[str, object]]:
    async with live.probe.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT last_seq, state FROM games WHERE id = :g"), {"g": live.game_id}
            )
        ).one()
    return row.last_seq, row.state


async def _rebuild(maker: async_sessionmaker[AppSession], game_id: UUID) -> int:
    async with maker() as session:
        result = await rebuild_state(session, game_id)
        await session.commit()
        return result.event_count


async def test_a_committed_rebuild_repairs_the_snapshot_and_leaves_the_events_alone(
    live: Live,
) -> None:
    async with live.holder() as session:
        await insert_log(session, live.game_id, lobby_and_turns(live.game_id, 30))
        await session.commit()
    assert await _rebuild(live.other, live.game_id) == 30
    last_seq, state = await _stored(live)
    assert last_seq == 30
    assert state["last_seq"] == 30
    assert state["phase"] == "active"
    async with live.probe.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM game_events"))).scalar_one()
    assert count == 30


async def test_a_rebuild_waits_for_the_game_lock_and_then_sees_the_event_that_beat_it(
    live: Live,
) -> None:
    async with live.holder() as holder:
        assert await repository.lock_game(holder, live.game_id) is not None
        task = asyncio.create_task(_rebuild(live.other, live.game_id))
        done, _ = await asyncio.wait({task}, timeout=STILL_WAITING_SECONDS)
        assert not done, "the rebuild ran although another transaction held the game lock"
        # An append that already holds the lock lands before the rebuild reads the log.
        result = await append_event(
            holder,
            live.game_id,
            submission(live.game_id, "settings_changed", {"starting_life": 25}),
            actor=player_actor(),
        )
        assert isinstance(result, Appended)
        await holder.commit()
    assert await asyncio.wait_for(task, WAIT_SECONDS) == 1
    last_seq, state = await _stored(live)
    assert last_seq == 1
    assert canonical_json(state) == canonical_json(result.state.to_json_value())


async def test_an_append_waits_behind_a_rebuild_and_then_builds_on_the_repaired_snapshot(
    live: Live,
) -> None:
    async with live.holder() as session:
        await session.execute(
            text("UPDATE games SET state = '{\"damaged\": true}'::jsonb WHERE id = :g"),
            {"g": live.game_id},
        )
        await session.commit()
    async with live.holder() as rebuilder:
        result = await rebuild_state(rebuilder, live.game_id)  # holds the lock, not committed
        assert result.written

        async def append() -> object:
            async with live.other() as session:
                outcome = await append_event(
                    session, live.game_id, submission(live.game_id), actor=player_actor()
                )
                await session.commit()
                return outcome

        task = asyncio.create_task(append())
        done, _ = await asyncio.wait({task}, timeout=STILL_WAITING_SECONDS)
        assert not done, "the append ran although a rebuild held the game lock"
        await rebuilder.commit()
    outcome = await asyncio.wait_for(task, WAIT_SECONDS)
    assert isinstance(outcome, Appended)
    assert outcome.event.seq == 1
    last_seq, state = await _stored(live)
    assert last_seq == 1
    assert state["last_seq"] == 1


async def test_a_rebuild_rolled_back_leaves_the_damaged_snapshot_and_frees_the_lock(
    live: Live,
) -> None:
    async with live.holder() as session:
        await session.execute(
            text("UPDATE games SET state = '{\"damaged\": true}'::jsonb WHERE id = :g"),
            {"g": live.game_id},
        )
        await session.commit()
    async with live.holder() as session:
        assert (await rebuild_state(session, live.game_id)).written
        await session.rollback()
    assert (await _stored(live))[1] == {"damaged": True}
    async with live.other() as session:  # the lock is gone
        assert await asyncio.wait_for(repository.lock_game(session, live.game_id), 5) is not None
        await session.rollback()
