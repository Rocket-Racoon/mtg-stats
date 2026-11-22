"""S-6.3.8 acceptance tests that need real commits, two sessions or the append-only trigger.

A throwaway database is migrated to head (so the trigger on ``game_events`` is in place). Separate
engines stand in for separate processes; a third one only observes. A game row lock is held by
leaving a transaction open, and "the rebuild waits" is shown by Postgres itself: an ungranted
lock in ``pg_locks`` that belongs to a backend of this throwaway database.

Criterion 3: the rebuild takes the same row lock as ``append_event``, so no event can slip in
between. The lock mode is compared with the one ``append_event`` takes by behaviour (what waits,
and what conflicts with it), never by a string copied from the implementation.
"""

import asyncio
import io
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.cli import run
from app.core import db
from app.core.db import AppSession
from app.core.errors import NotFoundError
from app.games.models import AUDITED_MUTATION_SETTING
from app.games.services import (
    Appended,
    AppendResult,
    EventRejectedError,
    LogGapError,
    Rebuilt,
    append_event,
    rebuild_state,
)
from tests.factories import GameFactory, PlaygroupFactory, create
from tests.games_rebuild_support import (
    SECRETS,
    append_steps,
    event_rows,
    independent_state,
    lobby_steps,
    read_game,
    real_steps,
    set_snapshot,
    shaped_log,
    sql_log,
    state_bytes,
    wire_events,
    writes,
)
from tests.games_support import insert_log, lobby_and_turns, player_actor, submission
from tests.migration_support import alembic_config, temporary_database

API_ROOT = Path(__file__).resolve().parents[1]
WAIT_SECONDS = 30.0
BLOCKED_SECONDS = 10.0
LIMIT_SECONDS = 0.5

BLOCKED_LOCKS = text(
    "SELECT l.locktype, l.mode FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
    "WHERE NOT l.granted AND a.datname = current_database()"
)
ROW_LOCKS = {
    "FOR KEY SHARE": text("SELECT 1 FROM games WHERE id = :g FOR KEY SHARE NOWAIT"),
    "FOR SHARE": text("SELECT 1 FROM games WHERE id = :g FOR SHARE NOWAIT"),
    "FOR NO KEY UPDATE": text("SELECT 1 FROM games WHERE id = :g FOR NO KEY UPDATE NOWAIT"),
    "FOR UPDATE": text("SELECT 1 FROM games WHERE id = :g FOR UPDATE NOWAIT"),
}
Maker = async_sessionmaker[AppSession]


# ---------------------------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_url() -> Iterator[str]:
    """One throwaway database at head for the module; each test makes its own games."""
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


@dataclass
class Rig:
    a: Maker
    b: Maker
    probe: AsyncEngine


@pytest.fixture
async def rig(migrated_url: str) -> AsyncIterator[Rig]:
    engines = [create_async_engine(migrated_url, pool_size=12, max_overflow=0) for _ in range(3)]
    makers = [
        async_sessionmaker(engine, class_=AppSession, expire_on_commit=False) for engine in engines
    ]
    try:
        yield Rig(makers[0], makers[1], engines[2])
    finally:
        for engine in engines:
            await engine.dispose()


async def committed_game(rig: Rig, steps: list[tuple[str, dict[str, Any]]] | None = None) -> UUID:
    """A committed game whose log came from ``append_event`` (default: the lobby steps)."""
    async with rig.a() as session:
        group = await create(session, PlaygroupFactory)
        game = await create(session, GameFactory, playgroup_id=group.id)
        await append_steps(session, game.id, lobby_steps() if steps is None else steps)
        await session.commit()
        return game.id


async def damage(rig: Rig, game_id: UUID, *, column: int | None = None) -> None:
    """Commit a valid-looking but wrong snapshot: seat 1's name is changed."""
    async with rig.a() as session:
        state = (await read_game(session, game_id)).state
        state["seats"][0]["name"] = "Injected Name"
        await set_snapshot(session, game_id, state, column)
        await session.commit()


async def stored(rig: Rig, game_id: UUID) -> tuple[bytes, int]:
    async with rig.probe.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT state, last_seq FROM games WHERE id = :g"), {"g": game_id}
            )
        ).one()
    return state_bytes(row.state), row.last_seq


async def oracle(rig: Rig, game_id: UUID) -> tuple[bytes, int]:
    """The replay of the committed events (the expected snapshot) and the event count."""
    async with rig.b() as session:
        state, count = await independent_state(session, game_id)
    return state_bytes(state.to_json_value()), count


async def assert_snapshot_is_the_replay(rig: Rig, game_id: UUID) -> int:
    expected, count = await oracle(rig, game_id)
    assert await stored(rig, game_id) == (expected, count)
    return count


async def rebuild_committed(maker: Maker, game_id: UUID, **kwargs: Any) -> Rebuilt:
    async with maker() as session:
        result = await rebuild_state(session, game_id, **kwargs)
        await session.commit()
        return result


async def append_committed(
    maker: Maker, game_id: UUID, type_: str = "settings_changed", payload: Any = None
) -> AppendResult:
    async with maker() as session:
        result = await append_event(
            session,
            game_id,
            submission(game_id, type_, {"starting_life": 25} if payload is None else payload),
            actor=player_actor(),
        )
        await session.commit()
        return result


async def wait_until_blocked(rig: Rig, task: "asyncio.Task[Any]") -> set[tuple[str, str]]:
    """Wait until Postgres reports an ungranted lock, and check the task is still waiting."""
    deadline = time.monotonic() + BLOCKED_SECONDS
    while time.monotonic() < deadline:
        assert not task.done(), "the task finished although the lock holder was still open"
        async with rig.probe.connect() as conn:
            rows = {(r.locktype, r.mode) for r in await conn.execute(BLOCKED_LOCKS)}
        if rows:
            return rows
        await asyncio.sleep(0.05)
    raise AssertionError("no backend waited for a lock")


async def conflicts(rig: Rig, game_id: UUID) -> dict[str, bool]:
    """Which row-lock requests a third connection cannot get right now (``NOWAIT``)."""
    result: dict[str, bool] = {}
    for mode, statement in ROW_LOCKS.items():
        async with rig.probe.connect() as conn:
            try:
                await conn.execute(statement, {"g": game_id})
                result[mode] = False
            except DBAPIError as exc:
                result[mode] = "could not obtain lock" in str(exc)
                if not result[mode]:
                    raise
            finally:
                await conn.rollback()
    return result


# ---------------------------------------------------------------------------------------------
# Criterion 3: the same row lock as append_event, so no event slips in between
# ---------------------------------------------------------------------------------------------


async def test_a_rebuild_waits_for_an_append_in_progress_and_then_includes_its_event(
    rig: Rig,
) -> None:
    game_id = await committed_game(rig)
    before = len(lobby_steps())
    async with rig.a() as holder:
        await append_steps(
            holder, game_id, [("seat_joined", {"seat": 6, "player_id": None, "name": "Late"})]
        )
        task = asyncio.create_task(rebuild_committed(rig.b, game_id))
        assert await wait_until_blocked(rig, task)
        await holder.commit()
    result = await asyncio.wait_for(task, WAIT_SECONDS)
    assert result.event_count == result.new_last_seq == before + 1
    assert not result.changed  # the append left a healthy snapshot, and the rebuild saw it
    assert await assert_snapshot_is_the_replay(rig, game_id) == before + 1


async def test_a_rebuild_waits_for_an_append_that_is_rolled_back_and_never_sees_its_event(
    rig: Rig,
) -> None:
    game_id = await committed_game(rig)
    before = len(lobby_steps())
    async with rig.a() as holder:
        await append_steps(holder, game_id, [("settings_changed", {"starting_life": 25})])
        task = asyncio.create_task(rebuild_committed(rig.b, game_id))
        assert await wait_until_blocked(rig, task)
        await holder.rollback()
    result = await asyncio.wait_for(task, WAIT_SECONDS)
    assert result.event_count == before
    assert await assert_snapshot_is_the_replay(rig, game_id) == before


async def test_an_append_waits_for_a_rebuild_and_then_builds_on_the_repaired_snapshot(
    rig: Rig,
) -> None:
    game_id = await committed_game(rig)
    before = len(lobby_steps())
    await damage(rig, game_id)  # valid-looking, so an append would happily build on it
    async with rig.a() as rebuilder:
        assert (await rebuild_state(rebuilder, game_id)).written  # holds the lock, uncommitted
        task = asyncio.create_task(append_committed(rig.b, game_id))
        assert await wait_until_blocked(rig, task)
        await rebuilder.commit()
    appended = await asyncio.wait_for(task, WAIT_SECONDS)
    assert isinstance(appended, Appended)
    assert appended.event.seq == before + 1
    assert await assert_snapshot_is_the_replay(rig, game_id) == before + 1
    async with rig.b() as session:
        seqs = [e["seq"] for e in await wire_events(session, game_id)]
        state = (await read_game(session, game_id)).state
    assert seqs == list(range(1, before + 2))
    assert state["seats"][0]["name"] != "Injected Name"


async def test_a_rebuild_of_one_game_does_not_block_an_append_to_another_and_vice_versa(
    rig: Rig,
) -> None:
    game_a = await committed_game(rig)
    game_b = await committed_game(rig)
    await damage(rig, game_a)
    async with rig.a() as rebuilder:
        assert (await rebuild_state(rebuilder, game_a)).written
        appended = await asyncio.wait_for(append_committed(rig.b, game_b), BLOCKED_SECONDS)
        assert isinstance(appended, Appended)
        await rebuilder.commit()
    async with rig.a() as holder:
        await append_steps(holder, game_b, [("settings_changed", {"starting_life": 20})])
        result = await asyncio.wait_for(rebuild_committed(rig.b, game_a), BLOCKED_SECONDS)
        assert not result.changed
        await holder.rollback()
    await assert_snapshot_is_the_replay(rig, game_a)
    await assert_snapshot_is_the_replay(rig, game_b)


async def test_a_rebuild_waits_in_the_same_lock_queue_as_an_append(rig: Rig) -> None:
    game_id = await committed_game(rig)

    async def waiting_locks(waiter: Callable[[], Any]) -> set[tuple[str, str]]:
        async with rig.a() as holder:
            await append_steps(holder, game_id, [("settings_changed", {"starting_life": 25})])
            task = asyncio.create_task(waiter())
            blocked = await wait_until_blocked(rig, task)
            await holder.commit()
        await asyncio.wait_for(task, WAIT_SECONDS)
        return blocked

    behind_append = await waiting_locks(lambda: append_committed(rig.b, game_id))
    behind_rebuild_waiter = await waiting_locks(lambda: rebuild_committed(rig.b, game_id))
    assert behind_append
    assert behind_rebuild_waiter == behind_append


async def test_a_rebuild_holds_a_row_lock_that_conflicts_exactly_as_append_events_does(
    rig: Rig,
) -> None:
    game_id = await committed_game(rig)

    async with rig.a() as holder:
        await append_steps(holder, game_id, [("settings_changed", {"starting_life": 25})])
        by_append = await conflicts(rig, game_id)
        await holder.rollback()

    async with rig.a() as holder:
        assert not (await rebuild_state(holder, game_id)).changed  # takes the lock, writes nothing
        by_clean_rebuild = await conflicts(rig, game_id)
        await holder.rollback()

    await damage(rig, game_id)
    async with rig.a() as holder:
        assert (await rebuild_state(holder, game_id)).written
        by_repairing_rebuild = await conflicts(rig, game_id)
        await holder.rollback()

    assert by_append["FOR NO KEY UPDATE"]  # an append excludes another append
    assert by_clean_rebuild == by_append
    assert by_repairing_rebuild == by_append
    assert not any((await conflicts(rig, game_id)).values())  # every lock was released


async def test_the_lock_is_released_after_a_dry_run_commit_or_error(rig: Rig) -> None:
    game_id = await committed_game(rig)
    async with rig.a() as session:
        await rebuild_state(session, game_id, dry_run=True)
        assert (await conflicts(rig, game_id))["FOR NO KEY UPDATE"]  # held until the caller ends
        await session.rollback()
    assert not any((await conflicts(rig, game_id)).values())
    async with rig.a() as session:
        with pytest.raises(NotFoundError):
            await rebuild_state(session, uuid4())
        await session.rollback()
    assert not any((await conflicts(rig, game_id)).values())


async def test_rebuilds_racing_appends_from_two_processes_leave_one_consistent_log(
    rig: Rig,
) -> None:
    game_id = await committed_game(rig, real_steps())
    base = len(real_steps())
    await damage(rig, game_id)
    makers = [rig.a, rig.b]
    jobs: list[Any] = []
    for i in range(6):
        jobs.append(append_committed(makers[i % 2], game_id, "turn_passed", {}))
        if i % 2 == 0:
            jobs.append(rebuild_committed(makers[(i + 1) % 2], game_id))
    results = await asyncio.wait_for(asyncio.gather(*jobs), 60)
    assert sum(1 for r in results if isinstance(r, Appended)) == 6
    count = await assert_snapshot_is_the_replay(rig, game_id)
    assert count == base + 6
    async with rig.b() as session:
        assert [e["seq"] for e in await wire_events(session, game_id)] == list(range(1, count + 1))
        state = (await read_game(session, game_id)).state
    assert state["seats"][0]["name"] != "Injected Name"
    assert state["last_seq"] == count


async def test_the_cli_waits_for_an_append_in_progress_and_includes_its_event(rig: Rig) -> None:
    game_id = await committed_game(rig)
    before = len(lobby_steps())
    out, err = io.StringIO(), io.StringIO()
    async with rig.a() as holder:
        await append_steps(
            holder, game_id, [("seat_joined", {"seat": 6, "player_id": None, "name": "Late"})]
        )
        task = asyncio.create_task(
            run(
                ["rebuild-state", "--game-id", str(game_id)],
                session_factory=rig.b,
                stdout=out,
                stderr=err,
            )
        )
        assert await wait_until_blocked(rig, task)
        await holder.commit()
    assert await asyncio.wait_for(task, WAIT_SECONDS) == 0, err.getvalue()
    assert f"{before + 1} events" in out.getvalue()
    assert await assert_snapshot_is_the_replay(rig, game_id) == before + 1


# ---------------------------------------------------------------------------------------------
# Criterion 1 with real commits: commit and rollback belong to the caller; events are not touched
# ---------------------------------------------------------------------------------------------


async def test_the_repair_is_invisible_to_others_until_the_caller_commits_and_a_rollback_undoes_it(
    rig: Rig,
) -> None:
    game_id = await committed_game(rig)
    await damage(rig, game_id)
    damaged = await stored(rig, game_id)
    async with rig.a() as session:
        assert (await rebuild_state(session, game_id)).written
        assert await stored(rig, game_id) == damaged  # nobody committed
        await session.rollback()
    assert await stored(rig, game_id) == damaged
    await rebuild_committed(rig.a, game_id)
    assert await stored(rig, game_id) != damaged
    await assert_snapshot_is_the_replay(rig, game_id)


async def test_a_committed_rebuild_leaves_every_event_row_byte_for_byte_as_it_was(
    rig: Rig,
) -> None:
    game_id = await committed_game(rig, real_steps())
    await damage(rig, game_id, column=3)
    async with rig.b() as session:
        events_before = await event_rows(session, game_id)
    async with rig.a() as session:
        with sql_log(session) as statements:
            assert (await rebuild_state(session, game_id)).written
        await session.commit()
    async with rig.b() as session:
        events_after = await event_rows(session, game_id)  # includes xmin: a rewrite would show
    assert events_after == events_before
    assert len(events_after) == len(real_steps())
    assert [s for s in writes(statements) if "game_events" in s.lower()] == []
    await assert_snapshot_is_the_replay(rig, game_id)


async def test_a_healthy_game_with_unicode_survives_the_jsonb_round_trip_without_a_write(
    rig: Rig,
) -> None:
    game_id = await committed_game(rig, real_steps())
    async with rig.b() as session:
        before = await read_game(session, game_id)
        with sql_log(session) as statements:
            result = await rebuild_state(session, game_id)
        await session.rollback()
    assert not result.changed
    assert writes(statements) == []
    async with rig.b() as session:
        assert (await read_game(session, game_id)).updated_at == before.updated_at


async def test_a_lost_event_is_reported_as_a_gap_and_nothing_is_written(rig: Rig) -> None:
    game_id = await committed_game(rig, real_steps())
    async with rig.probe.begin() as conn:  # the audited path is the only way to remove an event
        await conn.execute(
            text("SELECT set_config(:n, 'on', true)"), {"n": AUDITED_MUTATION_SETTING}
        )
        await conn.execute(
            text("DELETE FROM game_events WHERE game_id = :g AND seq = 7"), {"g": game_id}
        )
    await damage(rig, game_id)
    damaged = await stored(rig, game_id)
    async with rig.a() as session:
        with pytest.raises(LogGapError) as caught:
            await rebuild_state(session, game_id)
        await session.rollback()
    assert (caught.value.expected, caught.value.found) == (7, 8)
    assert await stored(rig, game_id) == damaged
    async with rig.b() as session:
        assert len(await wire_events(session, game_id)) == len(real_steps()) - 1


# ---------------------------------------------------------------------------------------------
# The command line, against the migrated database with real commits
# ---------------------------------------------------------------------------------------------


@dataclass
class Cli:
    code: int
    out: str
    err: str


async def cli(rig: Rig, *argv: str) -> Cli:
    out, err = io.StringIO(), io.StringIO()
    code = await run(list(argv), session_factory=rig.a, stdout=out, stderr=err)
    return Cli(code, out.getvalue(), err.getvalue())


def assert_prints_no_game_content(result: Cli) -> None:
    for secret in (*SECRETS, "Injected Name"):
        assert secret not in result.out + result.err, secret
    assert "{" not in result.out + result.err


async def test_the_cli_repairs_a_damaged_game_exits_0_and_commits(rig: Rig) -> None:
    game_id = await committed_game(rig, real_steps())
    await damage(rig, game_id, column=2)
    result = await cli(rig, "rebuild-state", "--game-id", str(game_id))
    assert result.code == 0, result.err
    assert str(game_id) in result.out
    assert_prints_no_game_content(result)
    assert await assert_snapshot_is_the_replay(rig, game_id) == len(real_steps())
    assert not any((await conflicts(rig, game_id)).values())


async def test_the_cli_on_a_healthy_game_exits_0_and_writes_nothing(rig: Rig) -> None:
    game_id = await committed_game(rig, real_steps())
    async with rig.b() as session:
        before = await read_game(session, game_id)
    result = await cli(rig, "rebuild-state", "--game-id", str(game_id))
    assert result.code == 0, result.err
    assert_prints_no_game_content(result)
    async with rig.b() as session:
        after = await read_game(session, game_id)
    assert (after.updated_at, after.last_seq) == (before.updated_at, before.last_seq)


async def test_the_cli_dry_run_on_a_damaged_game_exits_3_and_writes_nothing(rig: Rig) -> None:
    game_id = await committed_game(rig, real_steps())
    await damage(rig, game_id)
    damaged = await stored(rig, game_id)
    result = await cli(rig, "rebuild-state", "--game-id", str(game_id), "--dry-run")
    assert result.code == 3
    assert_prints_no_game_content(result)
    assert await stored(rig, game_id) == damaged
    assert not any((await conflicts(rig, game_id)).values())  # the dry run let go of the lock


async def test_the_cli_dry_run_on_a_healthy_game_exits_0(rig: Rig) -> None:
    game_id = await committed_game(rig, real_steps())
    result = await cli(rig, "rebuild-state", "--game-id", str(game_id), "--dry-run")
    assert result.code == 0, result.err


async def test_the_cli_exits_1_for_an_unknown_game(rig: Rig) -> None:
    result = await cli(rig, "rebuild-state", "--game-id", str(uuid4()))
    assert result.code == 1
    assert result.err.startswith("error:")


@pytest.mark.parametrize(
    "argv",
    [
        ["rebuild-state"],
        ["rebuild-state", "--game-id"],
        ["rebuild-state", "--game-id", "not-a-uuid"],
        ["rebuild-state", "--game-id", "12345"],
        ["rebuild-state", "--game-id", ""],
    ],
)
async def test_the_cli_exits_2_when_the_game_id_is_missing_or_malformed(
    rig: Rig, argv: list[str]
) -> None:
    result = await cli(rig, *argv)
    assert result.code == 2


async def test_the_cli_exits_1_on_a_log_the_reducer_rejects_and_prints_no_game_content(
    rig: Rig,
) -> None:
    game_id = await committed_game(rig, [])
    async with rig.a() as session:
        envelopes = lobby_and_turns(game_id, 9)
        envelopes[3] = {**envelopes[1], "event_id": str(uuid4()), "seq": 4}  # seat 1 joins twice
        envelopes[1]["payload"] = {**envelopes[1]["payload"], "name": SECRETS[0]}
        await insert_log(session, game_id, envelopes[:4])
        await set_snapshot(session, game_id, {"damaged": True}, 9)
        await session.commit()
    result = await cli(rig, "rebuild-state", "--game-id", str(game_id))
    assert result.code == 1
    assert "seq 4" in result.err
    assert_prints_no_game_content(result)
    assert await stored(rig, game_id) == (state_bytes({"damaged": True}), 9)


def _fresh_interpreter(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and module, controlled arguments
        [sys.executable, "-m", "app.cli", *argv],
        cwd=API_ROOT,
        env={**os.environ, "PYTHONPATH": str(API_ROOT)},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


async def test_python_m_app_cli_rebuild_state_works_end_to_end_in_a_fresh_process(
    rig: Rig,
) -> None:
    game_id = await committed_game(rig, real_steps())
    await damage(rig, game_id, column=1)
    dry = await asyncio.to_thread(
        _fresh_interpreter, "rebuild-state", "--game-id", str(game_id), "--dry-run"
    )
    assert dry.returncode == 3, dry.stderr
    assert (await stored(rig, game_id))[1] == 1
    done = await asyncio.to_thread(_fresh_interpreter, "rebuild-state", "--game-id", str(game_id))
    assert done.returncode == 0, done.stderr
    for secret in SECRETS:
        assert secret not in done.stdout + done.stderr
    assert await assert_snapshot_is_the_replay(rig, game_id) == len(real_steps())
    again = await asyncio.to_thread(_fresh_interpreter, "rebuild-state", "--game-id", str(game_id))
    assert again.returncode == 0
    missing = await asyncio.to_thread(
        _fresh_interpreter, "rebuild-state", "--game-id", str(uuid4())
    )
    assert missing.returncode == 1
    malformed = await asyncio.to_thread(_fresh_interpreter, "rebuild-state", "--game-id", "nope")
    assert malformed.returncode == 2


# ---------------------------------------------------------------------------------------------
# Criterion 2 with committed data
# ---------------------------------------------------------------------------------------------


async def test_a_committed_game_with_2000_events_is_rebuilt_in_under_500_ms(
    rig: Rig, capsys: pytest.CaptureFixture[str]
) -> None:
    game_id = await committed_game(rig, [])
    envelopes = shaped_log(game_id, 2000)
    async with rig.a() as session:
        await insert_log(session, game_id, envelopes)
        await session.commit()
    timings: list[float] = []
    async with rig.b() as session:  # one warm connection, as a long-lived worker would have
        for run_number in range(5):
            await set_snapshot(session, game_id, {"damaged": True}, 0)
            await session.commit()
            started = time.perf_counter()
            result = await rebuild_state(session, game_id)
            elapsed = time.perf_counter() - started
            await session.commit()
            if run_number > 0:  # the first run warms the statement cache and the plan
                timings.append(elapsed)
            assert result.written
            assert result.event_count == 2000
    await assert_snapshot_is_the_replay(rig, game_id)
    with capsys.disabled():
        print(f"\ncommitted rebuild of 2000 events (s): {[round(t, 4) for t in timings]}")  # noqa: T201
    assert min(timings) < LIMIT_SECONDS, f"timings: {timings}"


async def test_a_rejected_event_in_a_committed_log_raises_and_leaves_the_snapshot(rig: Rig) -> None:
    game_id = await committed_game(rig, lobby_steps())
    async with rig.a() as session:  # a turn passes in a lobby: nothing the system could produce
        envelopes = (await wire_events(session, game_id))[:3]
        await insert_log(
            session,
            game_id,
            [
                {
                    **envelopes[0],
                    "event_id": str(uuid4()),
                    "seq": len(lobby_steps()) + 1,
                    "type": "turn_passed",
                    "payload": {},
                }
            ],
        )
        await session.commit()
    before = await stored(rig, game_id)
    async with rig.a() as session:
        with pytest.raises(EventRejectedError) as caught:
            await rebuild_state(session, game_id)
        await session.rollback()
    assert (caught.value.seq, caught.value.code) == (len(lobby_steps()) + 1, "invalid_phase")
    assert await stored(rig, game_id) == before
