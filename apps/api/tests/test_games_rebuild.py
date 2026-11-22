"""``rebuild_state`` on a rolled-back session (S-6.3.8): repair, refusal, logging, no commit."""

import copy
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.games import repository
from app.games.models import Game, GameEvent
from app.games.services import (
    Appended,
    EventRejectedError,
    LogGapError,
    RebuildError,
    append_event,
    canonical_json,
    get_snapshot,
    initial_state,
    rebuild_state,
)
from tests.factories import GameFactory, PlaygroupFactory, create
from tests.games_support import insert_log, lobby_and_turns, make_game, player_actor, submission

pytestmark = pytest.mark.usefixtures("app_schema")

LOGGER = "app.games.services.rebuild"
SETTINGS = {
    "starting_life": 30,
    "commander_damage_threshold": 21,
    "poison_threshold": 10,
    "trust_mode": "casual",
}
LOBBY_EVENTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("game_created", {"settings": SETTINGS}),
    ("seat_joined", {"seat": 1, "player_id": "p-secret-1", "name": "Ada Lovelace"}),
    ("seat_joined", {"seat": 2, "player_id": "p-secret-2", "name": "Bob Builder"}),
)
Damage = Callable[[dict[str, Any]], dict[str, Any]]


async def _game_with_log(session: AsyncSession) -> UUID:
    """A game whose log was written through ``append_event``: the healthy reference."""
    game_id = await make_game(session)
    for type_, payload in LOBBY_EVENTS:
        result = await append_event(
            session, game_id, submission(game_id, type_, payload), actor=player_actor()
        )
        assert isinstance(result, Appended)
    return game_id


async def _game(session: AsyncSession, game_id: UUID) -> Game:
    game = await repository.get_game(session, game_id)
    assert game is not None
    return game


async def _damage(session: AsyncSession, game_id: UUID, state: dict[str, Any]) -> None:
    game = await _game(session, game_id)
    game.state = state
    await session.flush()


def _wrong_value(state: dict[str, Any]) -> dict[str, Any]:
    state["turn"] = 99
    state["seats"][0]["life"] = -5
    return state


def _missing_key(state: dict[str, Any]) -> dict[str, Any]:
    del state["first_seat"]  # what a snapshot written before S-6.1.6 looks like
    return state


def _extra_key(state: dict[str, Any]) -> dict[str, Any]:
    state["surprise"] = {"a": 1}
    return state


def _other_game(state: dict[str, Any]) -> dict[str, Any]:
    state["game_id"] = "018f0000-0000-7000-8000-00000000dead"
    return state


def _float_number(state: dict[str, Any]) -> dict[str, Any]:
    state["turn"] = 1.5  # canonical JSON refuses it
    return state


def _wrong_shape(_: dict[str, Any]) -> dict[str, Any]:
    return {"hello": "world"}


def _empty(_: dict[str, Any]) -> dict[str, Any]:
    return {}


@pytest.mark.parametrize(
    "damage",
    [_wrong_value, _missing_key, _extra_key, _other_game, _float_number, _wrong_shape, _empty],
)
async def test_a_damaged_snapshot_is_replaced_by_the_replay_of_the_log(
    app_schema: AsyncSession, damage: Damage, caplog: pytest.LogCaptureFixture
) -> None:
    game_id = await _game_with_log(app_schema)
    healthy = (await _game(app_schema, game_id)).state
    await _damage(app_schema, game_id, damage(copy.deepcopy(healthy)))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        result = await rebuild_state(app_schema, game_id)
    assert result.changed
    assert result.written
    assert result.event_count == 3
    assert result.differing_keys != ()
    app_schema.expire_all()
    game = await _game(app_schema, game_id)
    assert canonical_json(game.state) == canonical_json(healthy)
    assert game.last_seq == 3
    state, last_seq = await get_snapshot(app_schema, game_id)  # loads with the strict model
    assert last_seq == 3
    assert canonical_json(state.to_json_value()) == canonical_json(healthy)
    mismatch = [r for r in caplog.records if "games.state_rebuild_mismatch" in r.getMessage()]
    assert len(mismatch) == 1
    assert mismatch[0].levelno == logging.WARNING


async def test_the_mismatch_log_has_ids_and_key_names_but_no_state_or_player_data(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    game_id = await _game_with_log(app_schema)
    state = copy.deepcopy((await _game(app_schema, game_id)).state)
    state["turn"] = 7
    state["extra_key"] = 1
    await _damage(app_schema, game_id, state)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await rebuild_state(app_schema, game_id)
    message = next(r.getMessage() for r in caplog.records if "mismatch" in r.getMessage())
    assert str(game_id) in message
    assert "differing_keys=extra_key,turn" in message
    assert "old_last_seq=3" in message
    assert "new_last_seq=3" in message
    for secret in ("Ada Lovelace", "p-secret-1", "Bob Builder"):
        assert secret not in message


async def test_a_last_seq_column_that_disagrees_with_the_log_is_repaired(
    app_schema: AsyncSession,
) -> None:
    game_id = await _game_with_log(app_schema)
    game = await _game(app_schema, game_id)
    game.last_seq = 9
    await app_schema.flush()
    result = await rebuild_state(app_schema, game_id)
    assert result.changed
    assert result.differing_keys == ()
    assert (result.old_last_seq, result.new_last_seq) == (9, 3)
    app_schema.expire_all()
    assert (await _game(app_schema, game_id)).last_seq == 3


async def test_a_matching_snapshot_writes_nothing_and_logs_no_mismatch(
    app_schema: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    game_id = await _game_with_log(app_schema)
    stamp = (await _game(app_schema, game_id)).updated_at
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        result = await rebuild_state(app_schema, game_id)
    assert not result.changed
    assert not result.written
    assert result.differing_keys == ()
    assert result.event_count == 3
    assert result.state.last_seq == 3
    assert not app_schema.dirty
    assert (await _game(app_schema, game_id)).updated_at == stamp
    messages = [r.getMessage() for r in caplog.records]
    assert not any("mismatch" in m for m in messages)
    matched = [r for r in caplog.records if "games.state_rebuild_match" in r.getMessage()]
    assert len(matched) == 1
    assert matched[0].levelno == logging.INFO


async def test_a_game_with_no_events_is_rebuilt_to_the_empty_lobby(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    game = await _game(app_schema, game_id)
    game.state = {"nonsense": True}
    game.last_seq = 4
    await app_schema.flush()
    result = await rebuild_state(app_schema, game_id)
    assert result.changed
    assert result.event_count == 0
    app_schema.expire_all()
    game = await _game(app_schema, game_id)
    assert game.last_seq == 0
    assert canonical_json(game.state) == canonical_json(initial_state(str(game_id)).to_json_value())


async def test_a_dry_run_reports_the_mismatch_and_writes_nothing(
    app_schema: AsyncSession,
) -> None:
    game_id = await _game_with_log(app_schema)
    damaged = {"nonsense": True}
    await _damage(app_schema, game_id, damaged)
    result = await rebuild_state(app_schema, game_id, dry_run=True)
    assert result.changed
    assert not result.written
    app_schema.expire_all()
    assert (await _game(app_schema, game_id)).state == damaged


async def test_the_rebuilt_state_is_the_state_append_event_produced(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    await insert_log(app_schema, game_id, lobby_and_turns(game_id, 40))
    result = await rebuild_state(app_schema, game_id)
    assert result.event_count == 40
    assert result.state.phase == "active"
    assert result.state.settings.starting_life == 40
    # The same log through the real append path, in a second game, ends in the same state.
    other = await make_game(app_schema)
    for envelope in lobby_and_turns(other, 40):
        submitted = {k: envelope[k] for k in ("event_id", "game_id", "type", "payload", "v")}
        submitted["on_behalf_of_seat"] = None
        appended = await append_event(app_schema, other, submitted, actor=player_actor())
        assert isinstance(appended, Appended)
    stored, _ = await get_snapshot(app_schema, other)
    expected = stored.to_json_value() | {"game_id": str(game_id)}
    assert canonical_json(result.state.to_json_value()) == canonical_json(expected)


async def test_a_stored_event_the_reducer_rejects_raises_and_writes_nothing(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    envelopes = lobby_and_turns(game_id, 9)
    # seq 4 joins seat 1 a second time
    envelopes[3] = {**envelopes[1], "event_id": envelopes[3]["event_id"], "seq": 4}
    await insert_log(app_schema, game_id, envelopes)
    before = canonical_json((await _game(app_schema, game_id)).state)
    with pytest.raises(EventRejectedError) as raised:
        await rebuild_state(app_schema, game_id)
    assert raised.value.seq == 4
    assert raised.value.code == "seat_taken"
    assert isinstance(raised.value, RebuildError)
    assert not app_schema.dirty
    app_schema.expire_all()
    game = await _game(app_schema, game_id)
    assert canonical_json(game.state) == before
    assert game.last_seq == 0


async def test_a_hole_in_the_seqs_raises_and_writes_nothing(app_schema: AsyncSession) -> None:
    game_id = await make_game(app_schema)
    envelopes = lobby_and_turns(game_id, 9)
    del envelopes[2]  # seq 3 is missing
    await insert_log(app_schema, game_id, envelopes)
    with pytest.raises(LogGapError) as raised:
        await rebuild_state(app_schema, game_id)
    assert (raised.value.expected, raised.value.found) == (3, 4)
    app_schema.expire_all()
    assert (await _game(app_schema, game_id)).last_seq == 0


async def test_a_log_that_does_not_start_at_seq_one_raises(app_schema: AsyncSession) -> None:
    game_id = await make_game(app_schema)
    await insert_log(app_schema, game_id, lobby_and_turns(game_id, 9)[1:])
    with pytest.raises(LogGapError) as raised:
        await rebuild_state(app_schema, game_id)
    assert (raised.value.expected, raised.value.found) == (1, 2)


async def test_an_unknown_game_raises_not_found(app_schema: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await rebuild_state(app_schema, UUID(int=1))


async def test_a_naive_now_is_refused(app_schema: AsyncSession) -> None:
    game_id = await make_game(app_schema)
    with pytest.raises(ValueError, match="timezone-aware"):
        await rebuild_state(app_schema, game_id, now=datetime(2026, 1, 1))  # noqa: DTZ001


async def test_the_write_is_stamped_with_now_and_the_events_are_untouched(
    app_schema: AsyncSession,
) -> None:
    game_id = await _game_with_log(app_schema)
    await _damage(app_schema, game_id, {"x": 1})

    async def events() -> list[tuple[int, UUID, str, str]]:
        rows = (await app_schema.execute(select(GameEvent).order_by(GameEvent.seq))).scalars()
        return [(e.seq, e.event_id, e.type, canonical_json(e.payload)) for e in rows]

    before = await events()
    moment = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
    await rebuild_state(app_schema, game_id, now=moment)
    app_schema.expire_all()
    assert (await _game(app_schema, game_id)).updated_at == moment
    assert await events() == before


async def test_rebuild_state_never_commits(
    app_schema: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = await _game_with_log(app_schema)
    await _damage(app_schema, game_id, {"x": 1})

    async def boom() -> None:
        raise AssertionError("rebuild_state must not commit")

    monkeypatch.setattr(app_schema, "commit", boom)
    await rebuild_state(app_schema, game_id)
    assert app_schema.in_transaction()


async def test_a_factory_game_with_a_stale_snapshot_rebuilds(app_schema: AsyncSession) -> None:
    group = await create(app_schema, PlaygroupFactory)
    game = await create(app_schema, GameFactory, playgroup_id=group.id, state={"a": 1}, last_seq=2)
    result = await rebuild_state(app_schema, game.id)
    assert result.changed
    assert result.new_last_seq == 0
