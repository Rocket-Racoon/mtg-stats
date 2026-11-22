"""Acceptance tests for S-6.3.8 "Rebuild a game's state from its log", on the rolled-back session.

Written from the story's criteria:

1. A damaged ``games.state`` is replaced by the replay of the log, and the mismatch is logged.
2. A game with 2,000 events is rebuilt in under 500 ms.
3. The rebuild takes the row lock of ``append_event`` (tested with real commits and two sessions in
   ``test_games_rebuild_acceptance_db.py``).

The oracle is never the service: every expected snapshot is the reducer's own public ``replay`` of
the events stored in the database, folded in the test.
"""

import copy
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.ids import uuid7
from app.games.models import GameEvent
from app.games.services import (
    EventRejectedError,
    LogGapError,
    RebuildError,
    initial_state,
    rebuild_state,
)
from tests.factories import GameFactory, PlaygroupFactory, create
from tests.games_rebuild_support import (
    IMPLEMENTED_TYPES,
    SECRETS,
    Step,
    append_steps,
    elimination_steps,
    event_rows,
    fold,
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
from tests.games_support import insert_log, lobby_and_turns, make_game

pytestmark = pytest.mark.usefixtures("app_schema")

APP_LOGGERS = "app."
MISMATCH = "games.state_rebuild_mismatch"
MATCH = "games.state_rebuild_match"
NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
LIMIT_SECONDS = 0.5


@dataclass
class Ctx:
    """A healthy real game and what the oracle says about it."""

    game_id: UUID
    state: dict[str, Any]
    count: int
    wire: list[dict[str, Any]]

    def prefix(self, k: int) -> dict[str, Any]:
        return fold(self.game_id, self.wire[:k]).to_json_value()


async def real_game(session: AsyncSession, steps: list[Step] | None = None) -> Ctx:
    """A game whose log came from ``games.append_event`` and whose snapshot is healthy."""
    steps = real_steps() if steps is None else steps
    game_id = await make_game(session)
    await append_steps(session, game_id, steps)
    wire = await wire_events(session, game_id)
    state, count = await independent_state(session, game_id)
    row = await read_game(session, game_id)
    assert row.last_seq == count == len(steps)
    assert state_bytes(row.state) == state_bytes(state.to_json_value())  # healthy to begin with
    return Ctx(game_id, copy.deepcopy(row.state), count, wire)


def app_records(caplog: pytest.LogCaptureFixture, needle: str = "") -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name.startswith(APP_LOGGERS) and needle in r.getMessage() + str(r.__dict__.get("msg"))
    ]


def everything_logged(caplog: pytest.LogCaptureFixture) -> str:
    """The text of every record of the app's loggers, with its fields, args and traceback."""
    parts: list[str] = []
    for record in caplog.records:
        if record.name.startswith(APP_LOGGERS):
            parts += [record.getMessage(), repr(record.__dict__), caplog.handler.format(record)]
    return "\n".join(parts)


# ---------------------------------------------------------------------------------------------
# Criterion 1: damage of every kind is repaired from the log, and the mismatch is logged
# ---------------------------------------------------------------------------------------------

Damage = Callable[[Ctx], tuple[Any, int]]
"""Returns the damaged snapshot and the ``games.last_seq`` column to store with it."""


def _edit(change: Callable[[dict[str, Any]], None]) -> Damage:
    def damage(ctx: Ctx) -> tuple[Any, int]:
        state = copy.deepcopy(ctx.state)
        change(state)
        return state, ctx.count

    return damage


def _set(key: str, value: Any) -> Damage:
    return _edit(lambda s: s.__setitem__(key, value))


def _ahead(ctx: Ctx) -> tuple[Any, int]:
    state = copy.deepcopy(ctx.state)
    state["last_seq"] = ctx.count + 5
    state["turn"] += 5
    return state, ctx.count + 5


DAMAGES: dict[str, Damage] = {
    "wrong_nested_seat_value": _edit(lambda s: s["seats"][0].__setitem__("life", 999)),
    "wrong_nested_commander_name": _edit(
        lambda s: s["seats"][1]["commanders"][0].__setitem__("name", "Injected Name")
    ),
    "wrong_seat_order": _edit(lambda s: s["order"].reverse()),
    "missing_top_level_key": _edit(lambda s: s.pop("monarch")),
    "missing_nested_key": _edit(lambda s: s["seats"][0].pop("poison")),
    "extra_top_level_key": _set("bogus", {"x": 1}),
    "extra_nested_key": _edit(lambda s: s["seats"][0].__setitem__("bogus", True)),
    "game_id_of_another_game": _set("game_id", str(uuid4())),
    "whole_state_of_another_game": lambda ctx: (initial_state(str(uuid4())).to_json_value(), 0),
    "stale_prefix_with_matching_column": lambda ctx: (ctx.prefix(ctx.count - 4), ctx.count - 4),
    "stale_prefix_with_current_column": lambda ctx: (ctx.prefix(ctx.count - 4), ctx.count),
    "stale_initial_state": lambda ctx: (initial_state(str(ctx.game_id)).to_json_value(), 0),
    "column_zero": lambda ctx: (ctx.state, 0),
    "column_one_behind": lambda ctx: (ctx.state, ctx.count - 1),
    "schema_version_changed": _set("v", 99),
    "wrong_type_seats_is_a_string": _set("seats", "not-a-list"),
    "wrong_type_turn_is_a_string": _set("turn", "three"),
    "wrong_type_settings_is_null": _set("settings", None),
    "wrong_type_phase_unknown_value": _set("phase", "bogus"),
    "wrong_type_seat_life_is_a_string": _edit(lambda s: s["seats"][0].__setitem__("life", "40")),
    "empty_object": lambda ctx: ({}, ctx.count),
}


AHEAD_DAMAGES: dict[str, Damage] = {
    "snapshot_ahead_of_the_log": _ahead,
    "column_ahead_of_snapshot": lambda ctx: (ctx.state, ctx.count + 3),
    "column_one_above_the_event_count": lambda ctx: (ctx.state, ctx.count + 1),
    "snapshot_last_seq_one_above_the_event_count": lambda ctx: (
        {**ctx.state, "last_seq": ctx.count + 1},
        ctx.count,
    ),
}
CASES: dict[str, tuple[Damage, bool]] = {
    **{name: (damage, False) for name, damage in DAMAGES.items()},
    **{name: (damage, True) for name, damage in AHEAD_DAMAGES.items()},
}
"""Every kind of damage, with whether the log is *behind* the snapshot (an ERROR, not a WARNING).

The log is behind when the old ``games.last_seq`` column, or the old snapshot's integer
``last_seq``, is greater than the event count: events may have been lost after the last stored
seq. Equal to the count (a wrong seat value, say) and below it are ordinary mismatches.
"""


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
async def test_a_damaged_snapshot_is_replaced_by_the_replay_of_the_log_and_the_mismatch_is_logged(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture, case: tuple[Damage, bool]
) -> None:
    damage, ahead = case
    ctx = await real_game(app_schema)
    damaged, column = damage(ctx)
    await set_snapshot(app_schema, ctx.game_id, damaged, column)
    assert state_bytes(ctx.prefix(ctx.count)) == state_bytes(ctx.state)

    with caplog.at_level(logging.DEBUG):
        result = await rebuild_state(app_schema, ctx.game_id, now=NOW)

    expected = state_bytes(ctx.prefix(ctx.count))
    row = await read_game(app_schema, ctx.game_id)
    assert state_bytes(row.state) == expected
    assert row.last_seq == ctx.count
    assert row.updated_at == NOW
    assert result.changed
    assert result.written
    assert result.event_count == ctx.count
    assert result.old_last_seq == column
    assert result.new_last_seq == ctx.count
    assert result.log_behind_snapshot is ahead
    assert result.settings_from_log is True
    assert state_bytes(result.state.to_json_value()) == expected

    mismatches = app_records(caplog, MISMATCH)
    assert len(mismatches) == 1
    assert mismatches[0].levelno == (logging.ERROR if ahead else logging.WARNING)
    assert f"log_behind_snapshot={ahead}" in mismatches[0].getMessage()
    assert "settings_from_log=True" in mismatches[0].getMessage()
    assert str(ctx.game_id) in mismatches[0].getMessage()
    assert app_records(caplog, MATCH) == []
    logged = everything_logged(caplog)
    for secret in SECRETS:
        assert secret not in logged, secret
    assert "Injected Name" not in logged


async def test_the_replayed_games_went_through_every_handler_the_reducer_implements(
    app_schema: AsyncSession,
) -> None:
    played = await real_game(app_schema)
    eliminated = await real_game(app_schema, elimination_steps())
    types = {e["type"] for e in [*played.wire, *eliminated.wire]}
    assert types >= IMPLEMENTED_TYPES
    assert len(IMPLEMENTED_TYPES) == 12  # nine of S-6.1.6 and the three of S-6.1.3
    state = played.state
    assert state["phase"] == "active"
    assert state["settings"]["starting_life"] == 30
    assert state["first_seat"] == 3
    assert [s["seat"] for s in state["seats"]] == [1, 2, 3, 4]  # seat 5 joined and left
    assert state["last_seq"] == played.count
    by_seat = {s["seat"]: s for s in state["seats"]}
    assert by_seat[3]["elimination_reason"] == "life"  # life_changed took seat 3 to 0
    assert by_seat[2]["elimination_reason"] == "concession"
    assert by_seat[1]["eliminated_at_seq"] is None
    assert by_seat[1]["life"] == 23


async def test_the_elimination_game_reaches_one_seat_left_and_then_a_full_draw(
    app_schema: AsyncSession,
) -> None:
    ctx = await real_game(app_schema, elimination_steps())
    last_but_one = ctx.prefix(ctx.count - 2)  # one seat left, before its life change
    alive = [s["seat"] for s in last_but_one["seats"] if s["eliminated_at_seq"] is None]
    assert alive == [1]
    assert last_but_one["no_winner"] is False
    seats = {s["seat"]: s for s in ctx.state["seats"]}
    assert seats[2]["elimination_reason"] == "life"
    assert seats[2]["eliminated_by"] == 1
    assert seats[4]["elimination_reason"] == "concession"
    assert seats[3]["elimination_reason"] == "declared"
    assert seats[3]["elimination_cause"] == "mill"
    assert seats[1]["elimination_reason"] == "concession"
    assert ctx.state["no_winner"] is True  # the last seat conceded


def _wipe_elimination(state: dict[str, Any]) -> None:
    for seat in state["seats"]:
        for key in (
            "elimination_reason",
            "eliminated_by",
            "eliminated_at_seq",
            "eliminated_round",
            "elimination_cause",
        ):
            seat[key] = None


def _revive_seat_2(state: dict[str, Any]) -> None:
    seat = next(s for s in state["seats"] if s["seat"] == 2)
    seat.update(
        elimination_reason=None, eliminated_by=None, eliminated_at_seq=None, eliminated_round=None
    )
    seat["life"] = 30


ELIMINATION_DAMAGES: dict[str, Damage] = {
    "eliminated_seat_shown_alive": _edit(_revive_seat_2),
    "elimination_fields_wiped": _edit(_wipe_elimination),
    "no_winner_flipped": _set("no_winner", False),
    "cause_and_eliminator_swapped": _edit(
        lambda s: s["seats"][2].update(elimination_cause="combo", eliminated_by=4)
    ),
    "stale_prefix_before_the_eliminations": lambda ctx: (ctx.prefix(len(lobby_steps()) + 2), 13),
    "stale_prefix_one_seat_left": lambda ctx: (ctx.prefix(ctx.count - 1), ctx.count - 1),
}


@pytest.mark.parametrize("damage", ELIMINATION_DAMAGES.values(), ids=ELIMINATION_DAMAGES.keys())
async def test_a_damaged_snapshot_of_a_game_with_eliminations_is_rebuilt_from_the_log(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture, damage: Damage
) -> None:
    ctx = await real_game(app_schema, elimination_steps())
    damaged, column = damage(ctx)
    assert state_bytes(damaged) != state_bytes(ctx.state)
    await set_snapshot(app_schema, ctx.game_id, damaged, column)
    with caplog.at_level(logging.DEBUG):
        result = await rebuild_state(app_schema, ctx.game_id, now=NOW)
    row = await read_game(app_schema, ctx.game_id)
    assert result.written
    assert state_bytes(row.state) == state_bytes(ctx.prefix(ctx.count))
    assert state_bytes(row.state) == state_bytes(ctx.state)
    assert row.last_seq == ctx.count
    assert len(app_records(caplog, MISMATCH)) == 1
    for secret in SECRETS:
        assert secret not in everything_logged(caplog), secret


async def test_a_healthy_game_with_eliminations_is_a_match(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = await real_game(app_schema, elimination_steps())
    with caplog.at_level(logging.DEBUG):
        result = await rebuild_state(app_schema, ctx.game_id)
    assert not result.changed
    assert app_records(caplog, MISMATCH) == []


async def test_a_healthy_snapshot_is_left_alone_and_logged_as_a_match(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = await real_game(app_schema)
    before = await read_game(app_schema, ctx.game_id)
    with caplog.at_level(logging.DEBUG), sql_log(app_schema) as statements:
        result = await rebuild_state(app_schema, ctx.game_id, now=NOW)
    after = await read_game(app_schema, ctx.game_id)
    assert not result.changed
    assert not result.written
    assert result.differing_keys == ()
    assert after.updated_at == before.updated_at != NOW
    assert state_bytes(after.state) == state_bytes(before.state)
    assert after.last_seq == before.last_seq == ctx.count
    assert writes(statements) == []
    assert app_records(caplog, MISMATCH) == []
    matches = app_records(caplog, MATCH)
    assert len(matches) == 1
    assert matches[0].levelno == logging.INFO
    assert str(ctx.game_id) in matches[0].getMessage()
    for secret in SECRETS:
        assert secret not in everything_logged(caplog), secret


async def test_rebuilding_twice_reports_a_match_the_second_time(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = await real_game(app_schema)
    await set_snapshot(app_schema, ctx.game_id, ctx.prefix(5), 5)
    with caplog.at_level(logging.DEBUG):
        first = await rebuild_state(app_schema, ctx.game_id, now=NOW)
        second = await rebuild_state(app_schema, ctx.game_id, now=NOW)
    assert first.written
    assert not second.changed
    assert not second.written
    assert second.old_last_seq == ctx.count
    assert len(app_records(caplog, MISMATCH)) == 1
    assert len(app_records(caplog, MATCH)) == 1


@pytest.mark.parametrize(
    "damage",
    [
        DAMAGES["wrong_nested_seat_value"],
        DAMAGES["column_zero"],
        DAMAGES["wrong_type_seats_is_a_string"],
    ],
    ids=["nested_value", "only_the_column", "wrong_type"],
)
async def test_a_dry_run_reports_the_mismatch_and_writes_nothing(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture, damage: Damage
) -> None:
    ctx = await real_game(app_schema)
    damaged, column = damage(ctx)
    await set_snapshot(app_schema, ctx.game_id, damaged, column)
    before = await read_game(app_schema, ctx.game_id)
    with caplog.at_level(logging.DEBUG), sql_log(app_schema) as statements:
        result = await rebuild_state(app_schema, ctx.game_id, dry_run=True, now=NOW)
    after = await read_game(app_schema, ctx.game_id)
    assert result.changed
    assert not result.written
    assert result.event_count == ctx.count
    assert state_bytes(result.state.to_json_value()) == state_bytes(
        ctx.state
    )  # what it would write
    assert state_bytes(after.state) == state_bytes(before.state)
    assert (after.last_seq, after.updated_at) == (before.last_seq, before.updated_at)
    assert writes(statements) == []
    mismatches = app_records(caplog, MISMATCH)
    assert len(mismatches) == 1
    assert mismatches[0].levelno == logging.WARNING
    assert str(ctx.game_id) in mismatches[0].getMessage()
    # ...and the real run afterwards still repairs it.
    assert (await rebuild_state(app_schema, ctx.game_id, now=NOW)).written
    assert state_bytes((await read_game(app_schema, ctx.game_id)).state) == state_bytes(ctx.state)


async def test_a_game_with_no_events_gets_the_empty_lobby_back(app_schema: AsyncSession) -> None:
    game_id = await make_game(app_schema)
    await set_snapshot(app_schema, game_id, {"damaged": True}, 4)
    result = await rebuild_state(app_schema, game_id)
    row = await read_game(app_schema, game_id)
    assert result.event_count == 0
    assert state_bytes(row.state) == state_bytes(initial_state(str(game_id)).to_json_value())
    assert row.last_seq == 0


# ---------------------------------------------------------------------------------------------
# Corrupt logs and unknown games: the documented errors, and nothing is written
# ---------------------------------------------------------------------------------------------

DAMAGED = {"damaged": True}
DAMAGED_COLUMN = 5


async def _game_with_envelopes(session: AsyncSession, envelopes: list[dict[str, Any]]) -> UUID:
    game_id = await make_game(session)
    fixed = [{**e, "game_id": str(game_id)} for e in envelopes]
    await insert_log(session, game_id, fixed)
    await set_snapshot(session, game_id, DAMAGED, DAMAGED_COLUMN)
    return game_id


def _lobby(count: int = 12) -> list[dict[str, Any]]:
    return lobby_and_turns(uuid4(), count)


async def _assert_nothing_written(session: AsyncSession, game_id: UUID, rows: list[Any]) -> None:
    row = await read_game(session, game_id)
    assert row.state == DAMAGED
    assert row.last_seq == DAMAGED_COLUMN
    assert await event_rows(session, game_id) == rows


async def test_a_gap_in_the_seqs_raises_log_gap_error_and_writes_nothing(
    app_schema: AsyncSession,
) -> None:
    envelopes = _lobby()
    del envelopes[5]  # seq 6 is gone
    game_id = await _game_with_envelopes(app_schema, envelopes)
    rows = await event_rows(app_schema, game_id)
    with sql_log(app_schema) as statements, pytest.raises(LogGapError) as caught:
        await rebuild_state(app_schema, game_id)
    assert (caught.value.expected, caught.value.found) == (6, 7)
    assert isinstance(caught.value, RebuildError)
    assert writes(statements) == []
    await _assert_nothing_written(app_schema, game_id, rows)


async def test_a_log_that_does_not_start_at_seq_1_raises_log_gap_error(
    app_schema: AsyncSession,
) -> None:
    game_id = await _game_with_envelopes(app_schema, _lobby()[1:])
    rows = await event_rows(app_schema, game_id)
    with pytest.raises(LogGapError) as caught:
        await rebuild_state(app_schema, game_id)
    assert (caught.value.expected, caught.value.found) == (1, 2)
    await _assert_nothing_written(app_schema, game_id, rows)


def _reject_turn_in_lobby(base: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [*base[:3], {**base[8], "seq": 4}]


def _reject_unknown_type(base: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [*base[:3], {**base[8], "seq": 4, "type": "bogus_event"}]


def _reject_future_version(base: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [*base[:3], {**base[8], "seq": 4, "v": 2}]


def _reject_bad_payload(base: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [*base[:3], {**base[2], "seq": 4, "event_id": str(uuid4()), "payload": {"seat": "x"}}]


def _reject_taken_seat(base: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [*base[:3], {**base[2], "seq": 4, "event_id": str(uuid4())}]


@pytest.mark.parametrize(
    ("corrupt", "code"),
    [
        (_reject_turn_in_lobby, "invalid_phase"),
        (_reject_unknown_type, "unknown_event_type"),
        (_reject_future_version, "unsupported_version"),
        (_reject_bad_payload, "invalid_payload"),
        (_reject_taken_seat, "seat_taken"),
    ],
)
async def test_an_event_the_reducer_rejects_raises_with_its_seq_and_code_and_writes_nothing(
    app_schema: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    corrupt: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    code: str,
) -> None:
    game_id = await _game_with_envelopes(app_schema, corrupt(_lobby(9)))
    rows = await event_rows(app_schema, game_id)
    with caplog.at_level(logging.DEBUG), pytest.raises(EventRejectedError) as caught:
        await rebuild_state(app_schema, game_id)
    assert caught.value.seq == 4
    assert caught.value.code == code
    assert isinstance(caught.value, RebuildError)
    assert "player_id" not in str(caught.value)
    await _assert_nothing_written(app_schema, game_id, rows)
    assert app_records(caplog, MATCH) == []


async def test_an_unknown_game_raises_not_found(app_schema: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await rebuild_state(app_schema, uuid4())


async def test_a_naive_now_is_refused_before_anything_is_written(app_schema: AsyncSession) -> None:
    ctx = await real_game(app_schema)
    await set_snapshot(app_schema, ctx.game_id, DAMAGED, DAMAGED_COLUMN)
    with pytest.raises(ValueError, match="aware"):
        await rebuild_state(app_schema, ctx.game_id, now=datetime(2030, 1, 1))  # noqa: DTZ001
    row = await read_game(app_schema, ctx.game_id)
    assert (row.state, row.last_seq) == (DAMAGED, DAMAGED_COLUMN)


# ---------------------------------------------------------------------------------------------
# The service never commits and never touches game_events
# ---------------------------------------------------------------------------------------------


async def test_the_service_never_commits_and_never_writes_to_game_events(
    app_schema: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.orm import Session  # noqa: PLC0415

    ctx = await real_game(app_schema)
    await set_snapshot(app_schema, ctx.game_id, ctx.prefix(4), 4)
    events_before = await event_rows(app_schema, ctx.game_id)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("rebuild_state committed")

    monkeypatch.setattr(AsyncSession, "commit", forbidden)
    monkeypatch.setattr(Session, "commit", forbidden)
    with sql_log(app_schema) as statements:
        assert (await rebuild_state(app_schema, ctx.game_id)).written
    monkeypatch.undo()

    assert await event_rows(app_schema, ctx.game_id) == events_before
    assert len(events_before) == ctx.count
    touched = [s for s in writes(statements) if "game_events" in s.lower()]
    assert touched == []
    assert [s.split()[0].upper() for s in writes(statements)] == ["UPDATE"]  # the snapshot only
    assert app_schema.in_transaction()


async def test_a_failed_rebuild_leaves_the_events_as_they_were(app_schema: AsyncSession) -> None:
    envelopes = _lobby()
    del envelopes[5]
    game_id = await _game_with_envelopes(app_schema, envelopes)
    rows = await event_rows(app_schema, game_id)
    with pytest.raises(LogGapError):
        await rebuild_state(app_schema, game_id)
    assert await event_rows(app_schema, game_id) == rows


# ---------------------------------------------------------------------------------------------
# Criterion 2: a 2,000-event game in under 500 ms
# ---------------------------------------------------------------------------------------------

EVENTS = 2000
RUNS = 5


async def test_a_game_with_2000_events_is_rebuilt_in_under_500_ms(
    app_schema: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    game_id = await make_game(app_schema)
    envelopes = shaped_log(game_id, EVENTS)
    await app_schema.execute(
        insert(GameEvent),
        [
            {
                "id": uuid7(),
                "game_id": game_id,
                "seq": e["seq"],
                "event_id": UUID(e["event_id"]),
                "type": e["type"],
                "payload": e["payload"],
                "actor": e["actor"],
                "on_behalf_of_seat": None,
                "ts": datetime(2026, 11, 21, 12, 0, tzinfo=UTC),
                "v": 1,
            }
            for e in envelopes
        ],
    )
    await app_schema.flush()
    expected = state_bytes(fold(game_id, envelopes).to_json_value())
    assert {e["type"] for e in envelopes} >= {"turn_passed", "life_changed", "seat_moved"}
    full = fold(game_id, envelopes)  # fails the test if the reducer rejects any event
    assert full.last_seq == EVENTS
    assert all(seat.eliminated_at_seq is None for seat in full.seats)

    await set_snapshot(app_schema, game_id, DAMAGED, 0)
    await rebuild_state(app_schema, game_id)  # warm-up: imports, statement cache, first plan

    timings: list[float] = []
    for _ in range(RUNS):
        await set_snapshot(app_schema, game_id, DAMAGED, 0)  # every run replaces the snapshot
        started = time.perf_counter()
        result = await rebuild_state(app_schema, game_id)
        timings.append(time.perf_counter() - started)
        assert result.written
        assert result.event_count == EVENTS
    row = await read_game(app_schema, game_id)
    assert state_bytes(row.state) == expected
    assert row.last_seq == EVENTS
    with capsys.disabled():
        print(f"\nrebuild of {EVENTS} events (s): {[round(t, 4) for t in timings]}")  # noqa: T201
    assert min(timings) < LIMIT_SECONDS, f"timings: {timings}"


# ---------------------------------------------------------------------------------------------
# The boundary of "ahead of the log", the flag in the match record, and settings_from_log
# ---------------------------------------------------------------------------------------------


async def test_a_column_equal_to_the_event_count_is_not_ahead_and_one_above_is(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = await real_game(app_schema)
    levels: dict[int, int] = {}
    for column in (ctx.count, ctx.count + 1):
        await set_snapshot(app_schema, ctx.game_id, ctx.state, column)
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            result = await rebuild_state(app_schema, ctx.game_id, dry_run=True)
        assert result.changed is (column != ctx.count)
        assert result.log_behind_snapshot is (column > ctx.count)
        records = app_records(caplog, MISMATCH)
        levels[column] = records[0].levelno if records else 0
    assert levels == {ctx.count: 0, ctx.count + 1: logging.ERROR}


async def test_a_dry_run_of_an_ahead_snapshot_flags_it_and_writes_nothing(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = await real_game(app_schema)
    damaged, column = _ahead(ctx)
    await set_snapshot(app_schema, ctx.game_id, damaged, column)
    before = await read_game(app_schema, ctx.game_id)
    with caplog.at_level(logging.DEBUG):
        result = await rebuild_state(app_schema, ctx.game_id, dry_run=True)
    after = await read_game(app_schema, ctx.game_id)
    assert result.log_behind_snapshot
    assert not result.written
    assert (state_bytes(after.state), after.last_seq) == (state_bytes(before.state), column)
    assert [r.levelno for r in app_records(caplog, MISMATCH)] == [logging.ERROR]


async def test_a_normal_game_reads_its_settings_from_the_log_and_says_so_in_both_records(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = await real_game(app_schema)
    with caplog.at_level(logging.DEBUG):
        matched = await rebuild_state(app_schema, ctx.game_id)
    assert matched.settings_from_log is True
    assert not matched.log_behind_snapshot
    assert "settings_from_log=True" in app_records(caplog, MATCH)[0].getMessage()


async def test_an_empty_game_does_not_get_its_settings_from_the_log(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    game_id = await make_game(app_schema)
    with caplog.at_level(logging.DEBUG):
        matched = await rebuild_state(app_schema, game_id)
    assert matched.settings_from_log is False
    assert not matched.changed
    assert "settings_from_log=False" in app_records(caplog, MATCH)[0].getMessage()
    await set_snapshot(app_schema, game_id, DAMAGED, 0)
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        repaired = await rebuild_state(app_schema, game_id)
    assert repaired.written
    assert repaired.settings_from_log is False
    assert "settings_from_log=False" in app_records(caplog, MISMATCH)[0].getMessage()


async def test_a_log_that_does_not_start_with_game_created_is_rebuilt_with_the_default_settings(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Pins today's behaviour (flagged for the project owner): placeholder settings are lost.

    The row was created with a non-default placeholder (``starting_life`` 25) and the log has no
    ``game_created``. The log cannot say what the placeholder was, so the rebuild replays from the
    contract defaults and the rebuilt seats and settings differ from the healthy-looking snapshot.
    """
    group = await create(app_schema, PlaygroupFactory)
    placeholder = initial_state(str(uuid4()), {"starting_life": 25}).to_json_value()
    game = await create(app_schema, GameFactory, playgroup_id=group.id)
    placeholder["game_id"] = str(game.id)
    await set_snapshot(app_schema, game.id, placeholder, 0)
    await append_steps(
        app_schema,
        game.id,
        [
            ("seat_joined", {"seat": 1, "player_id": "pid-seed-1", "name": SECRETS[0]}),
            ("seat_joined", {"seat": 2, "player_id": "pid-seed-2", "name": SECRETS[1]}),
        ],
    )
    healthy = await read_game(app_schema, game.id)
    assert healthy.state["settings"]["starting_life"] == 25
    assert [s["life"] for s in healthy.state["seats"]] == [25, 25]

    with caplog.at_level(logging.DEBUG):
        result = await rebuild_state(app_schema, game.id)
    default_life = initial_state(str(game.id)).settings.starting_life
    assert default_life != 25
    assert result.settings_from_log is False
    assert result.changed
    assert result.written
    assert {"seats", "settings"} <= set(result.differing_keys)
    row = await read_game(app_schema, game.id)
    assert row.state["settings"]["starting_life"] == default_life
    assert [s["life"] for s in row.state["seats"]] == [default_life, default_life]
    wire = await wire_events(app_schema, game.id)
    assert state_bytes(row.state) == state_bytes(fold(game.id, wire).to_json_value())
    message = app_records(caplog, MISMATCH)[0].getMessage()
    assert "settings_from_log=False" in message
    for secret in SECRETS:
        assert secret not in everything_logged(caplog), secret


# ---------------------------------------------------------------------------------------------
# The mismatch record never carries a raw value from the damaged snapshot
# ---------------------------------------------------------------------------------------------

LEAK = "LEAK-Recognisable-Snapshot-Value"


@pytest.mark.parametrize(
    ("value", "logged"),
    [
        (LEAK, "snapshot_last_seq=str "),
        ({"owner": SECRETS[0], "players": [SECRETS[1], "pid-seed-2"]}, "snapshot_last_seq=dict "),
        ([SECRETS[2], LEAK], "snapshot_last_seq=list "),
        (True, "snapshot_last_seq=bool "),
        (False, "snapshot_last_seq=bool "),
        (3.5, "snapshot_last_seq=float "),
        (None, "snapshot_last_seq=NoneType "),
    ],
    ids=["string", "nested_object", "list", "true", "false", "float", "null"],
)
async def test_a_non_integer_snapshot_last_seq_is_logged_as_a_type_name_never_as_its_content(
    app_schema: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    value: Any,
    logged: str,
) -> None:
    ctx = await real_game(app_schema)
    damaged = {**ctx.state, "last_seq": value}
    await set_snapshot(app_schema, ctx.game_id, damaged, ctx.count)
    with caplog.at_level(logging.DEBUG):
        result = await rebuild_state(app_schema, ctx.game_id)
    assert result.written
    assert not result.log_behind_snapshot  # only an integer can be "ahead"
    text = everything_logged(caplog)
    assert logged in app_records(caplog, MISMATCH)[0].getMessage()
    for secret in (*SECRETS, LEAK):
        assert secret not in text, secret
    assert state_bytes((await read_game(app_schema, ctx.game_id)).state) == state_bytes(ctx.state)


async def test_an_integer_snapshot_last_seq_is_logged_as_the_number(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = await real_game(app_schema)
    await set_snapshot(app_schema, ctx.game_id, {**ctx.state, "last_seq": 7}, ctx.count)
    with caplog.at_level(logging.DEBUG):
        await rebuild_state(app_schema, ctx.game_id)
    assert "snapshot_last_seq=7 " in app_records(caplog, MISMATCH)[0].getMessage()
