"""Acceptance tests for S-6.3.1 "Event log storage and ordered append", written from the story.

Everything that needs Postgres runs on a database built by running the Alembic migrations, never
from ORM metadata, and with real commits through independent engines:

- ``migrated_url`` is one throwaway database at head shared by the tests of this module (each test
  creates its own playgroup and game, so they cannot see each other's rows);
- ``isolated_url`` is a throwaway database of its own for tests that add triggers.

"Two API processes" are two ``AsyncEngine`` objects with their own connection pools. Real
contention is forced in :func:`contended_appends`: one append is left uncommitted while the others
start on both engines, and the test waits until Postgres reports that all of them are blocked on a
lock (``pg_stat_activity.wait_event_type = 'Lock'``) before the first one commits. The test cannot
pass by running the appends one after another, and it fails loudly if they did not collide.
"""

import ast
import asyncio
import inspect
import re
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core import db
from app.core.db import AppSession
from app.core.errors import NotFoundError
from app.games import repository
from app.games.models import GameEvent
from app.games.services import (
    Actor,
    Appended,
    AppendResult,
    GameState,
    Rejected,
    ReplayOk,
    append_event,
    canonical_json,
    get_event,
    get_snapshot,
    initial_state,
    read_events_since,
    replay,
)
from tests.factories import GameFactory, PlaygroupFactory, create
from tests.migration_support import alembic_config, temporary_database

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
TIMEOUT = 60.0
SAFE_MAX = 2**53 - 1
SYSTEM_ACTOR_JSON = '{"kind": "system", "player_id": null}'

# ---------------------------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------------------------


@contextmanager
def migrated_database() -> Iterator[str]:
    """A throwaway database after ``alembic upgrade head`` (dropped on exit)."""
    patch = pytest.MonkeyPatch()
    try:
        with temporary_database(patch) as url:
            db.get_sessionmaker.cache_clear()
            db.get_engine.cache_clear()
            command.upgrade(alembic_config(), "head")
            try:
                yield url
            finally:
                db.get_sessionmaker.cache_clear()
                db.get_engine.cache_clear()
    finally:
        patch.undo()


@pytest.fixture(scope="module")
def migrated_url() -> Iterator[str]:
    with migrated_database() as url:
        yield url


@pytest.fixture
def isolated_url() -> Iterator[str]:
    with migrated_database() as url:
        yield url


@dataclass
class Rig:
    """Two engines (two "processes") that append, and a third one that only observes."""

    url: str
    a: async_sessionmaker[AppSession]
    b: async_sessionmaker[AppSession]
    probe: AsyncEngine


@asynccontextmanager
async def open_rig(url: str) -> AsyncIterator[Rig]:
    engines = [
        create_async_engine(url, pool_size=24, max_overflow=0),
        create_async_engine(url, pool_size=24, max_overflow=0),
        create_async_engine(url, pool_size=6, max_overflow=0),
    ]
    makers = [
        async_sessionmaker(engine, class_=AppSession, expire_on_commit=False)
        for engine in engines[:2]
    ]
    try:
        yield Rig(url, makers[0], makers[1], engines[2])
    finally:
        for engine in engines:
            await engine.dispose()


@pytest.fixture
async def rig(migrated_url: str) -> AsyncIterator[Rig]:
    async with open_rig(migrated_url) as opened:
        yield opened


async def new_game(rig: Rig) -> UUID:
    """A committed playgroup and empty game."""
    async with rig.a() as session:
        group = await create(session, PlaygroupFactory)
        game = await create(session, GameFactory, playgroup_id=group.id)
        await session.commit()
        return game.id


def envelope(
    game_id: UUID | str,
    type: str = "turn_passed",
    payload: Any = None,
    *,
    event_id: str | None = None,
    on_behalf_of_seat: int | None = None,
) -> dict[str, Any]:
    """What a client sends: an optimistic envelope without ``seq`` or ``ts``."""
    return {
        "event_id": event_id or str(uuid4()),
        "game_id": str(game_id),
        "type": type,
        "payload": {} if payload is None else payload,
        "actor": {"kind": "player", "player_id": "whatever-the-client-says"},
        "on_behalf_of_seat": on_behalf_of_seat,
        "v": 1,
    }


def player(player_id: str | None = None) -> Actor:
    return Actor(kind="player", player_id=player_id or str(uuid4()))


async def commit_append(
    maker: async_sessionmaker[AppSession],
    game_id: UUID,
    sub: Any = ...,
    *,
    actor: Actor | None = None,
    now: datetime | None = None,
) -> AppendResult:
    """One append in its own session and transaction, committed."""
    async with maker() as session:
        result = await append_event(
            session,
            game_id,
            envelope(game_id) if sub is ... else sub,
            actor=actor or player(),
            now=now,
        )
        await session.commit()
        return result


async def scalar(engine: AsyncEngine, sql: str, **params: Any) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar_one()


async def game_row(engine: AsyncEngine, game_id: UUID) -> Any:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT last_seq, state FROM games WHERE id = :g"), {"g": game_id}
        )
        return result.one()


async def stored_log(engine: AsyncEngine, game_id: UUID) -> list[dict[str, Any]]:
    """The log rebuilt from raw SQL as contract envelopes, in seq order."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT event_id::text AS event_id, game_id::text AS game_id, seq, type, "
                    "payload, actor, on_behalf_of_seat, ts, v FROM game_events "
                    "WHERE game_id = :g ORDER BY seq"
                ),
                {"g": game_id},
            )
        ).all()
    return [
        {
            "event_id": r.event_id,
            "game_id": r.game_id,
            "seq": r.seq,
            "type": r.type,
            "payload": r.payload,
            "actor": r.actor,
            "on_behalf_of_seat": r.on_behalf_of_seat,
            "ts": f"{r.ts.astimezone(UTC):%Y-%m-%dT%H:%M:%S}.{r.ts.microsecond // 1000:03d}Z",
            "v": r.v,
        }
        for r in rows
    ]


async def assert_log_is_consistent(engine: AsyncEngine, game_id: UUID, count: int) -> None:
    """Gap-free seqs 1..count, and ``games.state``/``last_seq`` equal to the replayed log."""
    log = await stored_log(engine, game_id)
    assert [e["seq"] for e in log] == list(range(1, count + 1))
    row = await game_row(engine, game_id)
    assert row.last_seq == count
    assert row.state["last_seq"] == count
    outcome = replay(initial_state(str(game_id)), log)
    assert isinstance(outcome, ReplayOk)
    assert canonical_json(row.state) == canonical_json(outcome.state.to_json_value())


async def wait_for_blocked(probe: AsyncEngine, at_least: int) -> int:
    """Wait until at least ``at_least`` backends wait on a lock; fail if they never do."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 30.0
    while True:
        blocked: int = await scalar(
            probe,
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'",
        )
        if blocked >= at_least:
            return blocked
        if loop.time() > deadline:
            pytest.fail(f"only {blocked} of {at_least} appends are blocked: they did not contend")
        await asyncio.sleep(0.05)


async def contended_appends(rig: Rig, game_id: UUID, subs: list[Any]) -> list[AppendResult]:
    """Run ``subs`` as appends that provably collide, alternating over the two engines.

    ``subs[0]`` is appended first and its transaction is left open. The others start (on both
    engines, one transaction each) and the function waits until Postgres shows every one of them
    blocked on a lock. Only then is the first transaction committed, releasing all of them at
    once.
    """
    holder = rig.a()
    tasks: list[asyncio.Task[AppendResult]] = []
    try:
        first = await append_event(holder, game_id, subs[0], actor=player())
        for index, sub in enumerate(subs[1:]):
            maker = rig.b if index % 2 else rig.a
            tasks.append(asyncio.create_task(commit_append(maker, game_id, sub)))
        await wait_for_blocked(rig.probe, len(tasks))
        await holder.commit()
        rest = await asyncio.wait_for(asyncio.gather(*tasks), TIMEOUT)
        return [first, *rest]
    finally:
        await holder.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def simultaneous_appends(rig: Rig, plan: list[tuple[UUID, Any]]) -> list[AppendResult]:
    """Appends released together: every session has its connection checked out before the start.

    The connection is already in a transaction when the shared event is set, so all statements
    reach Postgres in the same instant, from both engines.
    """
    ready = asyncio.Event()
    go = asyncio.Event()
    arrived = 0

    async def one(index: int, game_id: UUID, sub: Any) -> AppendResult:
        nonlocal arrived
        maker = rig.b if index % 2 else rig.a
        async with maker() as session:
            await session.connection()
            arrived += 1
            if arrived == len(plan):
                ready.set()
            await go.wait()
            result = await append_event(session, game_id, sub, actor=player())
            await session.commit()
            return result

    tasks = [asyncio.create_task(one(i, g, s)) for i, (g, s) in enumerate(plan)]
    try:
        await asyncio.wait_for(ready.wait(), TIMEOUT)
        go.set()
        return list(await asyncio.wait_for(asyncio.gather(*tasks), TIMEOUT))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def seqs_of(results: list[AppendResult]) -> list[int]:
    assert all(isinstance(r, Appended) for r in results)
    return sorted(r.event.seq for r in results if isinstance(r, Appended))


# ---------------------------------------------------------------------------------------------
# Criterion 1: the migration, the columns, the unique constraints, the drift check
# ---------------------------------------------------------------------------------------------

EXPECTED_COLUMN_TYPES: dict[str, set[str]] = {
    "game_id": {"uuid"},
    "seq": {"bigint", "integer"},
    "event_id": {"uuid"},
    "type": {"character varying", "text"},
    "payload": {"jsonb"},
    "actor": {"jsonb"},
    "on_behalf_of_seat": {"smallint", "integer"},
    "ts": {"timestamp with time zone"},
    "v": {"smallint", "integer"},
}


async def test_migration_creates_game_events_with_the_columns_of_the_story(rig: Rig) -> None:
    async with rig.probe.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'game_events'"
                )
            )
        ).all()
    columns = {r.column_name: (r.data_type, r.is_nullable) for r in rows}
    for name, types in EXPECTED_COLUMN_TYPES.items():
        assert name in columns, f"game_events has no column {name}"
        data_type, nullable = columns[name]
        assert data_type in types, f"{name} is {data_type}"
        assert nullable == ("YES" if name == "on_behalf_of_seat" else "NO"), name


async def _unique_constraints(engine: AsyncEngine) -> dict[tuple[str, ...], str]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT c.conname, array_agg(a.attname ORDER BY k.ord) AS cols "
                    "FROM pg_constraint c "
                    "CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) "
                    "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
                    "WHERE c.conrelid = 'public.game_events'::regclass AND c.contype = 'u' "
                    "GROUP BY c.conname"
                )
            )
        ).all()
    return {tuple(r.cols): r.conname for r in rows}


async def test_game_events_has_real_unique_constraints_on_game_seq_and_game_event_id(
    rig: Rig,
) -> None:
    # pg_constraint contype 'u' = a UNIQUE constraint, not just a unique index.
    constraints = await _unique_constraints(rig.probe)
    assert ("game_id", "seq") in constraints
    assert ("game_id", "event_id") in constraints


async def _insert_raw(engine: AsyncEngine, game_id: UUID, seq: int, event_id: UUID) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO game_events (id, game_id, seq, event_id, type, payload, actor, "
                "on_behalf_of_seat, ts, v) VALUES (gen_random_uuid(), :g, :s, :e, 'turn_passed', "
                "'{}'::jsonb, CAST(:actor AS jsonb), NULL, now(), 1)"
            ),
            {"g": game_id, "s": seq, "e": event_id, "actor": SYSTEM_ACTOR_JSON},
        )


async def test_a_second_row_with_the_same_game_and_seq_violates_a_unique_constraint(
    rig: Rig,
) -> None:
    game_id = await new_game(rig)
    await _insert_raw(rig.probe, game_id, 1, uuid4())
    with pytest.raises(IntegrityError) as caught:
        await _insert_raw(rig.probe, game_id, 1, uuid4())
    assert caught.value.orig is not None
    assert caught.value.orig.sqlstate == "23505"
    assert (
        caught.value.orig.diag.constraint_name
        == (await _unique_constraints(rig.probe))[("game_id", "seq")]
    )


async def test_a_second_row_with_the_same_game_and_event_id_violates_a_unique_constraint(
    rig: Rig,
) -> None:
    game_id = await new_game(rig)
    event_id = uuid4()
    await _insert_raw(rig.probe, game_id, 1, event_id)
    with pytest.raises(IntegrityError) as caught:
        await _insert_raw(rig.probe, game_id, 2, event_id)
    assert caught.value.orig is not None
    assert caught.value.orig.sqlstate == "23505"
    assert (
        caught.value.orig.diag.constraint_name
        == (await _unique_constraints(rig.probe))[("game_id", "event_id")]
    )


async def test_the_same_event_id_and_the_same_seq_are_allowed_in_two_different_games(
    rig: Rig,
) -> None:
    first, second = await new_game(rig), await new_game(rig)
    event_id = uuid4()
    await _insert_raw(rig.probe, first, 1, event_id)
    await _insert_raw(rig.probe, second, 1, event_id)
    count = await scalar(
        rig.probe, "SELECT count(*) FROM game_events WHERE event_id = :e", e=event_id
    )
    assert count == 2


def test_migration_upgrades_downgrades_and_upgrades_again_with_no_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = alembic_config()
    script = ScriptDirectory.from_config(config)
    revision = next(
        r for r in script.walk_revisions() if (r.doc or "").strip() == "games_event_log"
    )
    assert isinstance(revision.down_revision, str)
    with temporary_database(monkeypatch):
        db.get_sessionmaker.cache_clear()
        db.get_engine.cache_clear()
        try:
            command.upgrade(config, "head")
            command.downgrade(config, revision.down_revision)
            command.upgrade(config, "head")
            command.check(config)  # raises when the models and the migrations drift
        finally:
            db.get_sessionmaker.cache_clear()
            db.get_engine.cache_clear()


# ---------------------------------------------------------------------------------------------
# Criterion 2: concurrent appends from two processes
# ---------------------------------------------------------------------------------------------


async def test_twenty_colliding_appends_from_two_engines_get_seqs_one_to_twenty(rig: Rig) -> None:
    game_id = await new_game(rig)
    subs = [envelope(game_id) for _ in range(20)]
    results = await contended_appends(rig, game_id, subs)  # fails unless 19 are blocked at once
    assert seqs_of(results) == list(range(1, 21))
    assert all(isinstance(r, Appended) and not r.duplicate for r in results)
    await assert_log_is_consistent(rig.probe, game_id, 20)
    async with rig.b() as session:
        events = await read_events_since(session, game_id, 0)
    assert [e.seq for e in events] == list(range(1, 21))
    assert {e.event_id for e in events} == {s["event_id"] for s in subs}


@pytest.mark.parametrize("round_number", range(5))
async def test_twenty_simultaneously_released_appends_get_seqs_one_to_twenty(
    rig: Rig, round_number: int
) -> None:
    game_id = await new_game(rig)
    results = await simultaneous_appends(rig, [(game_id, envelope(game_id)) for _ in range(20)])
    assert seqs_of(results) == list(range(1, 21))
    await assert_log_is_consistent(rig.probe, game_id, 20)


async def test_appends_to_two_games_at_once_each_get_their_own_sequence(rig: Rig) -> None:
    first, second = await new_game(rig), await new_game(rig)
    plan = [(first if i % 2 == 0 else second, None) for i in range(20)]
    results = await simultaneous_appends(rig, [(game_id, envelope(game_id)) for game_id, _ in plan])
    by_game: dict[str, list[int]] = {str(first): [], str(second): []}
    for result in results:
        assert isinstance(result, Appended)
        by_game[result.event.game_id].append(result.event.seq)
    assert sorted(by_game[str(first)]) == list(range(1, 11))
    assert sorted(by_game[str(second)]) == list(range(1, 11))
    await assert_log_is_consistent(rig.probe, first, 10)
    await assert_log_is_consistent(rig.probe, second, 10)


# ---------------------------------------------------------------------------------------------
# Criterion 3: an event_id that is already stored
# ---------------------------------------------------------------------------------------------


async def test_appending_a_stored_event_id_returns_the_stored_event_and_writes_nothing(
    rig: Rig,
) -> None:
    game_id = await new_game(rig)
    sub = envelope(game_id, "life_changed", {"seat": 1, "delta": -3})
    first = await commit_append(rig.a, game_id, sub)
    assert isinstance(first, Appended)
    log_before = await stored_log(rig.probe, game_id)
    row_before = await game_row(rig.probe, game_id)

    again = await commit_append(rig.b, game_id, sub)  # a new transaction, another engine

    assert isinstance(again, Appended)
    assert again.duplicate is True
    assert again.event == first.event
    assert again.event.seq == 1
    assert await stored_log(rig.probe, game_id) == log_before
    row_after = await game_row(rig.probe, game_id)
    assert row_after.last_seq == row_before.last_seq == 1
    assert canonical_json(row_after.state) == canonical_json(row_before.state)


async def test_a_retry_after_later_events_still_returns_the_original_seq(rig: Rig) -> None:
    game_id = await new_game(rig)
    sub = envelope(game_id)
    first = await commit_append(rig.a, game_id, sub)
    assert isinstance(first, Appended)
    for _ in range(3):
        await commit_append(rig.a, game_id)
    log_before = await stored_log(rig.probe, game_id)

    again = await commit_append(rig.b, game_id, sub)

    assert isinstance(again, Appended)
    assert (again.duplicate, again.event.seq) == (True, 1)
    assert again.event == first.event
    assert await stored_log(rig.probe, game_id) == log_before
    assert (await game_row(rig.probe, game_id)).last_seq == 4


async def test_two_racing_appends_of_one_event_id_store_exactly_one_row(rig: Rig) -> None:
    game_id = await new_game(rig)
    sub = envelope(game_id)
    # The first append stays uncommitted; the second starts on the other engine and must wait.
    holder = rig.a()
    try:
        first = await append_event(holder, game_id, sub, actor=player())
        racer = asyncio.create_task(commit_append(rig.b, game_id, dict(sub)))
        await wait_for_blocked(rig.probe, 1)
        await holder.commit()
        second = await asyncio.wait_for(racer, TIMEOUT)
    finally:
        await holder.close()
    assert isinstance(first, Appended)
    assert isinstance(second, Appended)
    assert (first.duplicate, second.duplicate) == (False, True)
    assert second.event.seq == first.event.seq == 1
    await assert_log_is_consistent(rig.probe, game_id, 1)


async def test_six_simultaneous_submissions_of_one_event_id_store_exactly_one_row(
    rig: Rig,
) -> None:
    game_id = await new_game(rig)
    sub = envelope(game_id)
    results = await simultaneous_appends(rig, [(game_id, dict(sub)) for _ in range(6)])
    assert all(isinstance(r, Appended) and r.event.seq == 1 for r in results)
    assert sum(1 for r in results if isinstance(r, Appended) and not r.duplicate) == 1
    await assert_log_is_consistent(rig.probe, game_id, 1)


async def test_a_resubmitted_event_id_with_another_body_keeps_the_stored_event(rig: Rig) -> None:
    game_id = await new_game(rig)
    event_id = str(uuid4())
    first = await commit_append(
        rig.a,
        game_id,
        envelope(game_id, "life_changed", {"seat": 1, "delta": -1}, event_id=event_id),
    )
    assert isinstance(first, Appended)
    log_before = await stored_log(rig.probe, game_id)

    again = await commit_append(
        rig.a, game_id, envelope(game_id, "turn_passed", {}, event_id=event_id)
    )

    assert isinstance(again, Appended)
    assert again.duplicate is True
    assert again.event == first.event  # the stored event wins; ``body_differs`` is reported below
    assert await stored_log(rig.probe, game_id) == log_before


async def test_the_same_event_id_in_another_game_is_a_new_event_with_its_own_seq(
    rig: Rig,
) -> None:
    first_game, second_game = await new_game(rig), await new_game(rig)
    event_id = str(uuid4())
    await commit_append(rig.a, first_game, envelope(first_game, event_id=event_id))
    await commit_append(rig.a, first_game)

    other = await commit_append(rig.a, second_game, envelope(second_game, event_id=event_id))

    assert isinstance(other, Appended)
    assert (other.duplicate, other.event.seq) == (False, 1)
    await assert_log_is_consistent(rig.probe, first_game, 2)
    await assert_log_is_consistent(rig.probe, second_game, 1)


# ---------------------------------------------------------------------------------------------
# Criterion 4: event row, games.state and games.last_seq in one transaction
# ---------------------------------------------------------------------------------------------


async def test_after_a_commit_event_state_and_last_seq_are_visible_and_agree(rig: Rig) -> None:
    game_id = await new_game(rig)
    await commit_append(rig.a, game_id, envelope(game_id, "turn_passed"))
    await commit_append(rig.b, game_id, envelope(game_id, "life_changed", {"seat": 2, "delta": 5}))
    await commit_append(rig.a, game_id, envelope(game_id, "monarch_set", {"seat": 1}))
    # The probe is a third connection; the stored state equals the fold of the stored log.
    await assert_log_is_consistent(rig.probe, game_id, 3)


async def test_before_the_commit_a_second_connection_sees_none_of_the_three(rig: Rig) -> None:
    game_id = await new_game(rig)
    initial = await game_row(rig.probe, game_id)
    session = rig.a()
    try:
        result = await append_event(session, game_id, envelope(game_id), actor=player())
        assert isinstance(result, Appended)
        assert (
            await session.scalar(
                text("SELECT count(*) FROM game_events WHERE game_id = :g"), {"g": game_id}
            )
            == 1
        )  # visible inside the writer's own transaction ...

        assert await stored_log(rig.probe, game_id) == []  # ... and nowhere else
        outside = await game_row(rig.probe, game_id)
        assert outside.last_seq == 0
        assert canonical_json(outside.state) == canonical_json(initial.state)

        await session.commit()
    finally:
        await session.close()
    await assert_log_is_consistent(rig.probe, game_id, 1)


async def test_a_reader_never_sees_the_event_the_state_and_last_seq_disagree(rig: Rig) -> None:
    game_id = await new_game(rig)
    stop = asyncio.Event()
    observations: list[tuple[int, int, int, int]] = []

    async def reader() -> None:
        # One statement, so one snapshot: a writer that committed the three in separate
        # transactions would show up as a row where they disagree.
        while not stop.is_set():
            async with rig.probe.connect() as conn:
                row = (
                    await conn.execute(
                        text(
                            "SELECT g.last_seq, (g.state->>'last_seq')::int, "
                            "(SELECT coalesce(max(seq), 0) FROM game_events WHERE game_id = g.id), "
                            "(SELECT count(*) FROM game_events WHERE game_id = g.id) "
                            "FROM games g WHERE g.id = :g"
                        ),
                        {"g": game_id},
                    )
                ).one()
            observations.append((row[0], row[1], row[2], row[3]))

    watcher = asyncio.create_task(reader())
    try:
        for index in range(30):
            await commit_append(rig.a if index % 2 else rig.b, game_id)
    finally:
        stop.set()
        await watcher
    assert len(observations) > 5
    assert all(len(set(seen)) == 1 for seen in observations), observations


async def test_a_rolled_back_append_changes_nothing_and_the_next_append_reuses_the_seq(
    rig: Rig,
) -> None:
    game_id = await new_game(rig)
    await commit_append(rig.a, game_id)
    before_log = await stored_log(rig.probe, game_id)
    before_row = await game_row(rig.probe, game_id)

    async with rig.a() as session:
        result = await append_event(session, game_id, envelope(game_id), actor=player())
        assert isinstance(result, Appended)
        assert result.event.seq == 2
        await session.rollback()  # the caller's transaction fails after the service returned

    assert await stored_log(rig.probe, game_id) == before_log
    after_row = await game_row(rig.probe, game_id)
    assert after_row.last_seq == before_row.last_seq == 1
    assert canonical_json(after_row.state) == canonical_json(before_row.state)
    follow_up = await commit_append(rig.b, game_id)
    assert isinstance(follow_up, Appended)
    assert follow_up.event.seq == 2
    await assert_log_is_consistent(rig.probe, game_id, 2)


async def _install_failing_trigger(engine: AsyncEngine, ddl: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE FUNCTION s631_fail() RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN RAISE EXCEPTION 'injected failure' USING ERRCODE = 'P0001'; END $$"
            )
        )
        await conn.execute(text(ddl))


async def _remove_failing_trigger(engine: AsyncEngine, table: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TRIGGER s631_trigger ON {table}"))


@pytest.mark.parametrize(
    ("table", "ddl"),
    [
        (
            "games",
            "CREATE TRIGGER s631_trigger BEFORE UPDATE ON games "
            "FOR EACH ROW EXECUTE FUNCTION s631_fail()",
        ),
        (
            "game_events",
            "CREATE TRIGGER s631_trigger BEFORE INSERT ON game_events "
            "FOR EACH ROW EXECUTE FUNCTION s631_fail()",
        ),
    ],
    ids=["the-games-update-fails", "the-event-insert-fails"],
)
async def test_a_failing_write_leaves_the_log_the_state_and_last_seq_unchanged(
    isolated_url: str, table: str, ddl: str
) -> None:
    async with open_rig(isolated_url) as rig:
        game_id = await new_game(rig)
        await commit_append(rig.a, game_id)
        before_log = await stored_log(rig.probe, game_id)
        before_row = await game_row(rig.probe, game_id)
        await _install_failing_trigger(rig.probe, ddl)

        async with rig.a() as session:
            with pytest.raises(DBAPIError):
                await append_event(session, game_id, envelope(game_id), actor=player())
            await session.rollback()

        assert await stored_log(rig.probe, game_id) == before_log
        after_row = await game_row(rig.probe, game_id)
        assert after_row.last_seq == before_row.last_seq == 1
        assert canonical_json(after_row.state) == canonical_json(before_row.state)

        await _remove_failing_trigger(rig.probe, table)
        retry = await commit_append(rig.b, game_id)
        assert isinstance(retry, Appended)
        assert retry.event.seq == 2  # no gap
        await assert_log_is_consistent(rig.probe, game_id, 2)


async def test_a_failure_at_commit_time_undoes_the_event_row_the_state_and_last_seq(
    isolated_url: str,
) -> None:
    # A deferred constraint trigger on ``games`` fires at COMMIT, after the event row and the new
    # snapshot were both written: the strongest "something fails after the event row is written".
    async with open_rig(isolated_url) as rig:
        game_id = await new_game(rig)
        await commit_append(rig.a, game_id)
        before_log = await stored_log(rig.probe, game_id)
        before_row = await game_row(rig.probe, game_id)
        await _install_failing_trigger(
            rig.probe,
            "CREATE CONSTRAINT TRIGGER s631_trigger AFTER UPDATE ON games "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION s631_fail()",
        )

        async with rig.a() as session:
            result = await append_event(session, game_id, envelope(game_id), actor=player())
            assert isinstance(result, Appended)  # the service itself succeeded ...
            with pytest.raises(DBAPIError):
                await session.commit()  # ... and the transaction is refused at commit

        assert await stored_log(rig.probe, game_id) == before_log
        after_row = await game_row(rig.probe, game_id)
        assert after_row.last_seq == before_row.last_seq == 1
        assert canonical_json(after_row.state) == canonical_json(before_row.state)

        await _remove_failing_trigger(rig.probe, "games")
        retry = await commit_append(rig.b, game_id)
        assert isinstance(retry, Appended)
        assert retry.event.seq == 2
        await assert_log_is_consistent(rig.probe, game_id, 2)


async def test_appending_to_an_unknown_game_raises_not_found(rig: Rig) -> None:
    missing = uuid4()
    async with rig.a() as session:
        with pytest.raises(NotFoundError):
            await append_event(session, missing, envelope(missing), actor=player())


# ---------------------------------------------------------------------------------------------
# Server authority: seq, ts and actor are the server's; the reducer's rejections store nothing
# ---------------------------------------------------------------------------------------------


async def test_client_supplied_seq_ts_and_actor_are_replaced_by_the_server(rig: Rig) -> None:
    game_id = await new_game(rig)
    sub = envelope(game_id)
    sub.update(seq=999, ts="1999-01-01T00:00:00.000Z", actor={"kind": "player", "player_id": "eve"})
    authenticated = player("the-authenticated-player")
    moment = datetime(2026, 11, 21, 12, 0, 0, 123456, tzinfo=UTC)

    result = await commit_append(rig.a, game_id, sub, actor=authenticated, now=moment)

    assert isinstance(result, Appended)
    assert result.event.seq == 1
    assert result.event.ts == "2026-11-21T12:00:00.123Z"
    assert result.event.actor == {"kind": "player", "player_id": "the-authenticated-player"}
    (stored,) = await stored_log(rig.probe, game_id)
    assert stored["seq"] == 1
    assert stored["ts"] == "2026-11-21T12:00:00.123Z"
    assert stored["actor"] == {"kind": "player", "player_id": "the-authenticated-player"}


async def test_without_a_given_time_the_server_stamps_the_current_utc_time(rig: Rig) -> None:
    game_id = await new_game(rig)
    sub = envelope(game_id)
    sub["ts"] = "1999-01-01T00:00:00.000Z"
    before = datetime.now(UTC)
    result = await commit_append(rig.a, game_id, sub)
    after = datetime.now(UTC)
    assert isinstance(result, Appended)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", result.event.ts)
    stamped = datetime.strptime(result.event.ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    assert before.replace(microsecond=0) <= stamped <= after


async def test_a_system_actor_is_stored_as_given_by_the_server(rig: Rig) -> None:
    game_id = await new_game(rig)
    result = await commit_append(
        rig.a, game_id, envelope(game_id), actor=Actor(kind="system", player_id=None)
    )
    assert isinstance(result, Appended)
    (stored,) = await stored_log(rig.probe, game_id)
    assert stored["actor"] == {"kind": "system", "player_id": None}


async def test_a_client_seq_that_is_not_the_next_one_is_not_an_error_the_server_decides(
    rig: Rig,
) -> None:
    game_id = await new_game(rig)
    await commit_append(rig.a, game_id)
    sub = envelope(game_id)
    sub["seq"] = 7  # a stale or hostile client
    result = await commit_append(rig.a, game_id, sub)
    assert isinstance(result, Appended)
    assert result.event.seq == 2


def _nest(depth: int) -> Any:
    value: Any = 1
    for _ in range(depth):
        value = [value]
    return value


def _with(**changes: Any) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def change(sub: dict[str, Any]) -> dict[str, Any]:
        merged = {**sub, **changes}
        return {k: v for k, v in merged.items() if v is not _MISSING}

    return change


_MISSING = object()

REJECTED_SUBMISSIONS: list[tuple[str, Callable[[dict[str, Any]], dict[str, Any]], str]] = [
    ("v-missing", _with(v=_MISSING), "invalid_event"),
    ("v-is-a-boolean", _with(v=True), "invalid_event"),
    ("v-is-a-string", _with(v="1"), "invalid_event"),
    ("v-is-2", _with(v=2), "unsupported_version"),
    ("v-is-0", _with(v=0), "unsupported_version"),
    ("bad-v-beats-bad-type", _with(v=2, type="nope"), "unsupported_version"),
    ("type-not-registered", _with(type="not_a_registered_type"), "unknown_event_type"),
    ("type-is-not-a-string", _with(type=5), "invalid_event"),
    ("unknown-envelope-key", _with(extra="x"), "invalid_event"),
    ("event_id-missing", _with(event_id=_MISSING), "invalid_event"),
    ("event_id-uppercase", _with(event_id=str(uuid4()).upper()), "invalid_event"),
    (
        "event_id-is-a-v1-uuid",
        _with(event_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8"),
        "invalid_event",
    ),
    ("payload-is-a-list", _with(payload=[]), "invalid_event"),
    ("payload-is-null", _with(payload=None), "invalid_event"),
    ("on_behalf_of_seat-out-of-range", _with(on_behalf_of_seat=9), "invalid_event"),
    ("on_behalf_of_seat-missing", _with(on_behalf_of_seat=_MISSING), "invalid_event"),
    ("payload-integer-beyond-safe-range", _with(payload={"n": 2**53}), "invalid_payload"),
    ("payload-has-a-float", _with(payload={"n": 1.5}), "invalid_payload"),
    ("payload-has-a-lone-surrogate", _with(payload={"s": "\ud800"}), "invalid_payload"),
    ("payload-nested-beyond-the-limit", _with(payload={"n": _nest(70)}), "invalid_payload"),
]


@pytest.mark.parametrize(
    ("change", "code"),
    [pytest.param(c, k, id=n) for n, c, k in REJECTED_SUBMISSIONS],
)
async def test_an_event_the_reducer_rejects_is_not_stored_and_returns_its_code(
    rig: Rig, change: Callable[[dict[str, Any]], dict[str, Any]], code: str
) -> None:
    game_id = await new_game(rig)
    before = await game_row(rig.probe, game_id)

    result = await commit_append(rig.a, game_id, change(envelope(game_id)))

    assert result == Rejected(code)  # type: ignore[arg-type]
    assert await stored_log(rig.probe, game_id) == []
    after = await game_row(rig.probe, game_id)
    assert after.last_seq == 0
    assert canonical_json(after.state) == canonical_json(before.state)
    follow_up = await commit_append(rig.a, game_id)  # no seq was consumed
    assert isinstance(follow_up, Appended)
    assert follow_up.event.seq == 1


@pytest.mark.parametrize("not_an_object", [None, [], "event", 7])
async def test_a_submission_that_is_not_an_object_is_rejected_as_invalid_event(
    rig: Rig, not_an_object: Any
) -> None:
    game_id = await new_game(rig)
    result = await commit_append(rig.a, game_id, not_an_object)
    assert result == Rejected("invalid_event")  # type: ignore[arg-type]
    assert await stored_log(rig.probe, game_id) == []
    assert (await game_row(rig.probe, game_id)).last_seq == 0


async def test_an_event_addressed_to_another_game_is_stored_in_neither(rig: Rig) -> None:
    # Which code it gets is reported, not pinned: the server could also have rewritten game_id.
    target, other = await new_game(rig), await new_game(rig)
    result = await commit_append(rig.a, target, envelope(other))
    assert isinstance(result, Rejected)
    assert await stored_log(rig.probe, target) == []
    assert await stored_log(rig.probe, other) == []
    assert (await game_row(rig.probe, target)).last_seq == 0
    assert (await game_row(rig.probe, other)).last_seq == 0


# ---------------------------------------------------------------------------------------------
# Storage fidelity
# ---------------------------------------------------------------------------------------------

AWKWARD_PAYLOAD: dict[str, Any] = {
    "max": SAFE_MAX,
    "min": -SAFE_MAX,
    "zero": 0,
    "astral": "\U0001f9d9\u200d\u2640\ufe0f \U00010348",
    "rtl": "\u05e9\u05dc\u05d5\u05dd \u0627\u0644\u0639\u0631\u0628\u064a\u0629 \u202e\u2067",
    "line_separator": "a\u2028b\u2029c",
    "nfc": "caf\u00e9",
    "nfd": "cafe\u0301",
    "": "empty key",
    "\u00e9\U0001f9d9": [1, [2, [3, {"deep": [None, True, False, "x"]}]]],
    "nested": {"b": [SAFE_MAX, -SAFE_MAX], "a": {"z": None, "y": {"x": []}}},
    "control": 'tab\t newline\n quote" backslash\\ \x1f \x7f',
    "deep": _nest(60),
}


async def test_awkward_payload_round_trips_with_the_same_canonical_json(rig: Rig) -> None:
    game_id = await new_game(rig)
    sub = envelope(game_id, "turn_passed", AWKWARD_PAYLOAD)
    result = await commit_append(rig.a, game_id, sub)
    assert isinstance(result, Appended)
    expected = canonical_json(AWKWARD_PAYLOAD)

    assert canonical_json(result.event.payload) == expected
    async with rig.b() as session:  # read back by another engine
        (read,) = await read_events_since(session, game_id, 0)
        by_id = await get_event(session, game_id, UUID(sub["event_id"]))
    assert canonical_json(read.payload) == expected
    assert by_id is not None
    assert canonical_json(by_id.payload) == expected
    (raw,) = await stored_log(rig.probe, game_id)
    assert canonical_json(raw["payload"]) == expected
    assert raw["payload"] == AWKWARD_PAYLOAD


async def test_a_payload_with_a_nul_character_does_not_crash_the_append(rig: Rig) -> None:
    # Contract question, reported not enshrined: the canonical JSON allows U+0000 but jsonb cannot
    # store it. Whatever the outcome, the call must not leak a database error and the log must
    # agree with the returned result.
    game_id = await new_game(rig)
    payloads = [{"s": "a\x00b"}, {"a\x00b": 1}, {"deep": [{"s": "\x00"}]}]
    for payload in payloads:
        result = await commit_append(rig.a, game_id, envelope(game_id, payload=payload))
        assert isinstance(result, Appended | Rejected)
    log = await stored_log(rig.probe, game_id)
    stored_ok = sum(1 for p in payloads if any(e["payload"] == p for e in log))
    assert len(log) == stored_ok
    await assert_log_is_consistent(rig.probe, game_id, len(log))


async def test_an_event_read_back_later_is_byte_for_byte_what_was_stored(rig: Rig) -> None:
    game_id = await new_game(rig)
    sub = envelope(
        game_id, "life_changed", {"seat": 3, "delta": -SAFE_MAX, "note": "caf\u00e9 \U0001f9d9"}
    )
    moment = datetime(2026, 11, 21, 8, 30, 15, 987654, tzinfo=UTC)
    actor = player("p-1")
    appended = await commit_append(rig.a, game_id, sub, actor=actor, now=moment)
    assert isinstance(appended, Appended)

    expected_envelope = {
        **sub,
        "seq": 1,
        "ts": "2026-11-21T08:30:15.987Z",
        "actor": {"kind": "player", "player_id": "p-1"},
    }
    async with rig.b() as session:
        first_read = await read_events_since(session, game_id, 0)
    async with rig.probe.connect() as conn:
        text_before = (
            await conn.execute(
                text(
                    "SELECT payload::text, actor::text, ts::text "
                    "FROM game_events WHERE game_id = :g"
                ),
                {"g": game_id},
            )
        ).one()
    for _ in range(3):
        await commit_append(rig.a, game_id)
    async with rig.a() as session:
        second_read = await read_events_since(session, game_id, 0)
        tail = await read_events_since(session, game_id, 1)
    async with rig.probe.connect() as conn:
        text_after = (
            await conn.execute(
                text(
                    "SELECT payload::text, actor::text, ts::text FROM game_events "
                    "WHERE game_id = :g AND seq = 1"
                ),
                {"g": game_id},
            )
        ).one()

    assert canonical_json(first_read[0].to_wire()) == canonical_json(expected_envelope)
    assert canonical_json(second_read[0].to_wire()) == canonical_json(expected_envelope)
    assert first_read[0] == second_read[0] == appended.event
    assert tuple(text_before) == tuple(text_after)
    assert [e.seq for e in second_read] == [1, 2, 3, 4]
    assert [e.seq for e in tail] == [2, 3, 4]


async def test_the_snapshot_read_returns_the_stored_state_and_last_seq(rig: Rig) -> None:
    game_id = await new_game(rig)
    await commit_append(rig.a, game_id)
    await commit_append(rig.b, game_id)
    async with rig.a() as session:
        state, last_seq = await get_snapshot(session, game_id)
    row = await game_row(rig.probe, game_id)
    assert last_seq == row.last_seq == 2
    assert isinstance(state, GameState)
    assert canonical_json(state.to_json_value()) == canonical_json(row.state)


# ---------------------------------------------------------------------------------------------
# Criterion 5: the log is append-only in code and in the database
# ---------------------------------------------------------------------------------------------

EVENT_FUNCTIONS = {"append", "read_since", "get_by_event_id"}
AUDITED_FUNCTIONS = {"reassign_player", "delete_playgroup_data"}
GAMES_ROW_FUNCTIONS = {"add_game", "get_game", "lock_game", "save_snapshot"}
GAME_EVENT_COLUMNS = {"seq", "event_id", "type", "payload", "actor", "on_behalf_of_seat", "ts", "v"}
MUTATING_CALLS = {
    "update",
    "delete",
    "merge",
    "truncate",
    "expunge",
    "bulk_update_mappings",
    "bulk_save_objects",
    "bulk_insert_mappings",
}
MUTATING_SQL = re.compile(r"\b(update|delete\s+from|truncate)\b[^;]{0,40}?game_events", re.I | re.S)


def _public_repository_functions() -> dict[str, Any]:
    return {
        name: obj
        for name, obj in vars(repository).items()
        if inspect.isfunction(obj) and obj.__module__ == repository.__name__ and name[0] != "_"
    }


def test_the_repository_exposes_append_read_the_two_audited_operations_and_games_helpers() -> None:
    functions = _public_repository_functions()
    assert set(functions) == EVENT_FUNCTIONS | AUDITED_FUNCTIONS | GAMES_ROW_FUNCTIONS


def test_the_games_row_helpers_never_touch_the_event_table() -> None:
    for name in GAMES_ROW_FUNCTIONS:
        function = _public_repository_functions()[name]
        assert "GameEvent" not in function.__code__.co_names, name
        assert "game_events" not in inspect.getsource(function), name


def test_the_read_functions_only_read() -> None:
    tree = ast.parse(Path(inspect.getsourcefile(repository) or "").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name in {
            "read_since",
            "get_by_event_id",
        }:
            called = {
                (c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", ""))
                for c in ast.walk(node)
                if isinstance(c, ast.Call)
            }
            assert not called & (
                MUTATING_CALLS
                | {"add", "add_all", "flush", "execute_insert", "insert", "GameEvent"}
            ), node.name


def test_the_two_audited_operations_exist_in_the_repository() -> None:
    # Their stories (S-2.3.4, S-2.6.3) are later: today they are stubs, so only the names and the
    # fact that they are not silently half-implemented are checked here.
    functions = _public_repository_functions()
    assert inspect.iscoroutinefunction(functions["reassign_player"])
    assert inspect.iscoroutinefunction(functions["delete_playgroup_data"])


def _references_guarded_name(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id == "GameEvent":
            return True
        if isinstance(child, ast.Attribute) and child.attr == "GameEvent":
            return True
        if isinstance(child, ast.alias) and child.name == "GameEvent":
            return True
        if (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and re.search(r"game_events|GameEvent", child.value, re.I)
        ):
            return True
    return False


def _docstring_nodes(tree: ast.AST) -> set[int]:
    nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                nodes.add(id(first.value))
    return nodes


def _is_event_mutation_call(call: ast.Call) -> bool:
    name = _call_name(call)
    if name not in MUTATING_CALLS:
        return False
    if name == "update" and isinstance(call.func, ast.Attribute):
        # ``candidate.update(...)`` is a dict; ``GameEvent.__table__.update()`` is not.
        receiver = ast.dump(call.func.value)
        return "GameEvent" in receiver or "__table__" in receiver or "game_events" in receiver
    return True


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def scan_for_event_mutations(root: Path) -> list[str]:
    """Every place under ``root`` (the ``app`` tree) that could change or delete event rows.

    Stricter than a keyword search: a module outside the three games modules may not even name
    ``GameEvent`` or ``game_events`` (so aliases, ``__table__``, ``table("game_events")`` and a
    ``session.delete(loaded_event)`` all need such a name first). The three games modules may not
    call update/delete/merge-like functions, write event columns on a loaded row or contain
    mutating SQL, except inside the two audited repository functions.
    """
    allowed = {
        Path("games/models.py"),
        Path("games/repository.py"),
        Path("games/services/events.py"),
    }
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if "tests" in relative.parts or "__pycache__" in relative.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if relative not in allowed:
            if _references_guarded_name(tree):
                found.append(f"{relative.as_posix()}: names GameEvent or game_events")
            continue
        docstrings = _docstring_nodes(tree)
        for top in tree.body:
            audited = (
                relative == Path("games/repository.py")
                and isinstance(top, ast.FunctionDef | ast.AsyncFunctionDef)
                and top.name in AUDITED_FUNCTIONS
            )
            if audited:
                continue
            for child in ast.walk(top):
                if isinstance(child, ast.Call) and _is_event_mutation_call(child):
                    found.append(f"{relative.as_posix()}:{child.lineno}: {_call_name(child)}()")
                elif (
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and id(child) not in docstrings
                    and MUTATING_SQL.search(child.value)
                ):
                    found.append(f"{relative.as_posix()}:{child.lineno}: mutating SQL")
                elif (
                    isinstance(child, ast.Attribute)
                    and isinstance(child.ctx, ast.Store)
                    and child.attr in GAME_EVENT_COLUMNS
                ):
                    found.append(f"{relative.as_posix()}:{child.lineno}: writes .{child.attr}")
    return found


def test_no_code_in_app_updates_or_deletes_game_events() -> None:
    assert scan_for_event_mutations(APP_ROOT) == []


IMPORT = "from app.games.models import GameEvent"
LOADED = "e = (await s.execute(select(GameEvent))).scalars().first()"
SYNTHETIC_OFFENDERS: dict[str, str] = {
    "raw_update": 'async def f(s):\n    await s.execute(text("UPDATE game_events SET v = 1"))\n',
    "raw_schema_qualified": (
        'async def f(s):\n    await s.execute(text("DELETE FROM public.game_events"))\n'
    ),
    "raw_lowercase_multiline": (
        'async def f(s):\n    await s.execute(text("""\n update\n  game_events set v = 1"""))\n'
    ),
    "orm_bulk_update": (
        f"{IMPORT}\nasync def f(s):\n    await s.execute(update(GameEvent).values(v=2))\n"
    ),
    "orm_bulk_delete_alias": (
        "from app.games.models import GameEvent as Ev\n"
        "async def f(s):\n    await s.execute(delete(Ev))\n"
    ),
    "orm_table_delete": (
        f"{IMPORT}\nasync def f(s):\n    await s.execute(GameEvent.__table__.delete())\n"
    ),
    "core_table_delete": 'async def f(s):\n    await s.execute(delete(table("game_events")))\n',
    "session_delete_loaded_row": (
        f"{IMPORT}\nasync def f(s):\n    {LOADED}\n    await s.delete(e)\n"
    ),
    "attribute_write_on_loaded_row": (
        f"{IMPORT}\nasync def f(s):\n    {LOADED}\n    e.payload = {{}}\n"
    ),
    "merge": f"{IMPORT}\nasync def f(s):\n    await s.merge(GameEvent(seq=1))\n",
}


@pytest.fixture
def synthetic_app(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "games").mkdir()
    (root / "games" / "services").mkdir()
    for relative in ("games/models.py", "games/repository.py", "games/services/events.py"):
        shutil.copy(APP_ROOT / relative, root / relative)
    (root / "stats").mkdir()
    return root


def test_the_scan_is_clean_on_a_copy_of_the_real_games_modules(synthetic_app: Path) -> None:
    assert scan_for_event_mutations(synthetic_app) == []


@pytest.mark.parametrize("name", sorted(SYNTHETIC_OFFENDERS))
def test_the_scan_flags_a_new_module_that_mutates_game_events(
    synthetic_app: Path, name: str
) -> None:
    (synthetic_app / "stats" / "rogue.py").write_text(SYNTHETIC_OFFENDERS[name], encoding="utf-8")
    assert scan_for_event_mutations(synthetic_app), name


def test_the_scan_flags_a_delete_added_to_a_non_audited_repository_function(
    synthetic_app: Path,
) -> None:
    repository_copy = synthetic_app / "games" / "repository.py"
    source = repository_copy.read_text(encoding="utf-8")
    repository_copy.write_text(
        source + "\n\nasync def prune(session, event):\n    await session.delete(event)\n",
        encoding="utf-8",
    )
    assert scan_for_event_mutations(synthetic_app)


@pytest.fixture(scope="module")
def app_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of ``app/`` plus the implementer's architecture test, to run that test on a tree."""
    root = tmp_path_factory.mktemp("arch_copy")
    shutil.copytree(APP_ROOT, root / "app", ignore=shutil.ignore_patterns("__pycache__"))
    (root / "tests" / "architecture").mkdir(parents=True)
    shutil.copy(
        Path(__file__).parent / "architecture" / "test_game_events_append_only.py",
        root / "tests" / "architecture",
    )
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return root


def _implementers_architecture_test_passes(root: Path) -> bool:
    outcome = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/architecture",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return outcome.returncode == 0


GAPS_IN_THE_IMPLEMENTERS_SCAN = {
    "raw_schema_qualified",
    "orm_bulk_delete_alias",
    "orm_table_delete",
    "core_table_delete",
    "session_delete_loaded_row",
    "merge",
    "attribute_write_on_loaded_row",  # its docstring says the trigger test covers this one
}


def test_the_implementers_architecture_test_passes_on_the_unmodified_tree(app_copy: Path) -> None:
    assert _implementers_architecture_test_passes(app_copy)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(
            n,
            marks=pytest.mark.xfail(
                strict=True,
                reason="known gap in tests/architecture/test_game_events_append_only.py",
            ),
        )
        if n in GAPS_IN_THE_IMPLEMENTERS_SCAN
        else n
        for n in sorted(SYNTHETIC_OFFENDERS)
    ],
)
def test_the_implementers_architecture_test_fails_when_a_module_mutates_game_events(
    app_copy: Path, name: str
) -> None:
    rogue = app_copy / "app" / "rogue_case.py"
    rogue.write_text(SYNTHETIC_OFFENDERS[name], encoding="utf-8")
    try:
        assert not _implementers_architecture_test_passes(app_copy), name
    finally:
        rogue.unlink()


async def _snapshot_of_events(engine: AsyncEngine, game_id: UUID) -> list[tuple[Any, ...]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT id, seq, event_id, type, payload::text, actor::text, ts, v "
                "FROM game_events WHERE game_id = :g ORDER BY seq"
            ),
            {"g": game_id},
        )
        return [tuple(r) for r in rows]


async def _game_with_three_events(rig: Rig) -> UUID:
    game_id = await new_game(rig)
    for _ in range(3):
        await commit_append(rig.a, game_id)
    return game_id


async def test_the_database_rejects_a_raw_update_of_an_event(rig: Rig) -> None:
    game_id = await _game_with_three_events(rig)
    before = await _snapshot_of_events(rig.probe, game_id)
    with pytest.raises(DBAPIError) as caught:
        async with rig.probe.begin() as conn:
            await conn.execute(
                text("UPDATE game_events SET payload = '{\"x\": 1}'::jsonb WHERE game_id = :g"),
                {"g": game_id},
            )
    assert caught.value.orig is not None
    assert caught.value.orig.sqlstate == "23001"
    assert await _snapshot_of_events(rig.probe, game_id) == before


async def test_the_database_rejects_a_raw_delete_of_an_event(rig: Rig) -> None:
    game_id = await _game_with_three_events(rig)
    before = await _snapshot_of_events(rig.probe, game_id)
    with pytest.raises(DBAPIError) as caught:
        async with rig.probe.begin() as conn:
            await conn.execute(text("DELETE FROM game_events WHERE game_id = :g"), {"g": game_id})
    assert caught.value.orig is not None
    assert caught.value.orig.sqlstate == "23001"
    assert await _snapshot_of_events(rig.probe, game_id) == before


async def test_the_database_rejects_changing_a_loaded_event_through_the_orm(rig: Rig) -> None:
    game_id = await _game_with_three_events(rig)
    before = await _snapshot_of_events(rig.probe, game_id)
    async with rig.a() as session:
        row = (
            await session.execute(
                select(GameEvent).where(GameEvent.game_id == game_id, GameEvent.seq == 2)
            )
        ).scalar_one()
        row.type = "life_changed"
        with pytest.raises(DBAPIError) as caught:
            await session.flush()
        assert caught.value.orig is not None
        assert caught.value.orig.sqlstate == "23001"
        await session.rollback()
    assert await _snapshot_of_events(rig.probe, game_id) == before


async def test_the_database_rejects_deleting_a_loaded_event_through_the_orm(rig: Rig) -> None:
    game_id = await _game_with_three_events(rig)
    before = await _snapshot_of_events(rig.probe, game_id)
    async with rig.a() as session:
        row = (
            await session.execute(
                select(GameEvent).where(GameEvent.game_id == game_id, GameEvent.seq == 3)
            )
        ).scalar_one()
        await session.delete(row)
        with pytest.raises(DBAPIError) as caught:
            await session.flush()
        assert caught.value.orig is not None
        assert caught.value.orig.sqlstate == "23001"
        await session.rollback()
    assert await _snapshot_of_events(rig.probe, game_id) == before


async def test_the_database_rejects_an_orm_bulk_update_of_events(rig: Rig) -> None:
    game_id = await _game_with_three_events(rig)
    before = await _snapshot_of_events(rig.probe, game_id)
    async with rig.a() as session:
        with pytest.raises(DBAPIError) as caught:
            await session.execute(update(GameEvent).where(GameEvent.game_id == game_id).values(v=2))
        assert caught.value.orig is not None
        assert caught.value.orig.sqlstate == "23001"
        await session.rollback()
    assert await _snapshot_of_events(rig.probe, game_id) == before


async def test_appending_still_works_after_a_rejected_update_attempt(rig: Rig) -> None:
    game_id = await _game_with_three_events(rig)
    with pytest.raises(DBAPIError):
        async with rig.probe.begin() as conn:
            await conn.execute(text("DELETE FROM game_events WHERE game_id = :g"), {"g": game_id})
    result = await commit_append(rig.b, game_id)
    assert isinstance(result, Appended)
    assert result.event.seq == 4
    await assert_log_is_consistent(rig.probe, game_id, 4)
