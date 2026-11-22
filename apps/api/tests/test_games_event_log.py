"""``append_event`` and the event-log reads on a rolled-back session (S-6.3.1)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.games import repository
from app.games.core import Actor
from app.games.models import Game, GameEvent
from app.games.services import (
    Appended,
    GameLogCorruptError,
    InvalidActorError,
    Rejected,
    append_event,
    canonical_json,
    format_ts,
    get_event,
    get_snapshot,
    initial_state,
    read_events_since,
)
from tests.factories import GameFactory, PlaygroupFactory, create
from tests.games_support import SYSTEM_ACTOR, make_game, player_actor, submission

pytestmark = pytest.mark.usefixtures("app_schema")


async def _count(session: AsyncSession, game_id: UUID) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(GameEvent).where(GameEvent.game_id == game_id)
        )
        or 0
    )


async def _appended(session: AsyncSession, game_id: UUID, **kwargs: Any) -> Appended:
    result = await append_event(
        session, game_id, submission(game_id, **kwargs), actor=player_actor()
    )
    assert isinstance(result, Appended)
    return result


async def test_appends_get_consecutive_seqs_starting_at_one(app_schema: AsyncSession) -> None:
    game_id = await make_game(app_schema)
    seqs = [(await _appended(app_schema, game_id)).event.seq for _ in range(5)]
    assert seqs == [1, 2, 3, 4, 5]


async def test_an_append_writes_the_event_the_state_and_last_seq_together(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    result = await _appended(app_schema, game_id)
    await _appended(app_schema, game_id)
    app_schema.expire_all()
    game = await app_schema.get_one(Game, game_id)
    assert game.last_seq == 2
    assert game.state["last_seq"] == 2
    assert result.state.last_seq == 1
    assert await _count(app_schema, game_id) == 2


async def test_the_server_sets_seq_ts_and_actor_whatever_the_client_sent(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    sent = submission(game_id)
    sent.update(seq=99, ts="1999-01-01T00:00:00.000Z")
    actor = player_actor()
    now = datetime(2026, 11, 22, 1, 2, 3, 456789, tzinfo=UTC)
    result = await append_event(app_schema, game_id, sent, actor=actor, now=now)
    assert isinstance(result, Appended)
    assert result.event.seq == 1
    assert result.event.ts == "2026-11-22T01:02:03.456Z"
    assert result.event.actor == {"kind": "player", "player_id": actor.player_id}
    row = (await app_schema.execute(select(GameEvent))).scalar_one()
    assert row.ts == datetime(2026, 11, 22, 1, 2, 3, 456000, tzinfo=UTC)
    assert format_ts(row.ts) == result.event.ts


async def test_a_system_actor_is_stored_with_a_null_player(app_schema: AsyncSession) -> None:
    game_id = await make_game(app_schema)
    result = await append_event(
        app_schema, game_id, submission(game_id, on_behalf_of_seat=3), actor=SYSTEM_ACTOR
    )
    assert isinstance(result, Appended)
    assert result.event.actor == {"kind": "system", "player_id": None}
    assert result.event.on_behalf_of_seat == 3
    assert result.event.v == 1
    assert result.event.to_wire()["seq"] == 1


async def test_a_resubmitted_event_id_returns_the_stored_event_and_writes_nothing(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    first = await _appended(app_schema, game_id, payload={"n": 1})
    await _appended(app_schema, game_id)
    again = await append_event(
        app_schema,
        game_id,
        submission(game_id, payload={"n": 1}, event_id=first.event.event_id),
        actor=player_actor(),
    )
    assert isinstance(again, Appended)
    assert (again.duplicate, again.body_differs) == (True, False)
    assert again.event == first.event
    assert again.event.seq == 1
    assert await _count(app_schema, game_id) == 2
    app_schema.expire_all()
    assert (await app_schema.get_one(Game, game_id)).last_seq == 2


async def test_a_duplicate_with_another_body_returns_the_stored_event_and_is_flagged(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    first = await _appended(app_schema, game_id, payload={"n": 1})
    again = await append_event(
        app_schema,
        game_id,
        submission(game_id, payload={"n": 2}, event_id=first.event.event_id),
        actor=player_actor(),
    )
    assert isinstance(again, Appended)
    assert again.duplicate
    assert again.body_differs
    assert again.event.payload == {"n": 1}
    assert await _count(app_schema, game_id) == 1


async def test_an_event_id_is_unique_per_game_not_globally(app_schema: AsyncSession) -> None:
    first_game = await make_game(app_schema)
    second_game = await make_game(app_schema)
    shared = str(uuid4())
    one = await _appended(app_schema, first_game, event_id=shared)
    two = await _appended(app_schema, second_game, event_id=shared)
    assert (one.event.seq, two.event.seq) == (1, 1)
    assert not two.duplicate


async def test_a_unique_violation_the_precheck_missed_returns_the_stored_event(
    app_schema: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = await make_game(app_schema)
    first = await _appended(app_schema, game_id)
    real = repository.get_by_event_id
    calls = 0

    async def blind_once(session: AsyncSession, gid: UUID, event_id: UUID) -> GameEvent | None:
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real(session, gid, event_id)

    monkeypatch.setattr(repository, "get_by_event_id", blind_once)
    # Another game state is simulated by pointing the game back at last_seq 0: the insert at
    # seq 1 then violates both constraints, and the event_id one is what the service answers.
    game = await app_schema.get_one(Game, game_id)
    game.last_seq = 0
    game.state = initial_state(str(game_id)).to_json_value()
    await app_schema.flush()
    again = await append_event(
        app_schema,
        game_id,
        submission(game_id, event_id=first.event.event_id),
        actor=player_actor(),
    )
    assert isinstance(again, Appended)
    assert again.duplicate
    assert again.event.seq == 1
    assert await _count(app_schema, game_id) == 1
    assert (await app_schema.execute(select(1))).scalar_one() == 1  # the session is still usable


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"type": "no_such_event"}, "unknown_event_type"),
        ({"v": 2}, "unsupported_version"),
        ({"event_id": "not-a-uuid"}, "invalid_event"),
        ({"extra": 1}, "invalid_event"),
        ({"payload": {"n": 1.5}}, "invalid_payload"),
        ({"game_id": str(uuid4())}, "game_mismatch"),
    ],
)
async def test_a_reducer_rejection_is_returned_and_nothing_is_written(
    app_schema: AsyncSession, change: dict[str, Any], code: str
) -> None:
    game_id = await make_game(app_schema)
    sent = {**submission(game_id), **change}
    result = await append_event(app_schema, game_id, sent, actor=player_actor())
    assert result == Rejected(code)  # type: ignore[arg-type]
    assert await _count(app_schema, game_id) == 0
    app_schema.expire_all()
    game = await app_schema.get_one(Game, game_id)
    assert game.last_seq == 0
    assert game.state == initial_state(str(game_id)).to_json_value()


async def test_a_body_that_is_not_an_object_is_rejected_as_invalid_event(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    result = await append_event(app_schema, game_id, ["nope"], actor=player_actor())
    assert result == Rejected("invalid_event")


async def test_a_nul_character_in_a_payload_is_rejected_because_jsonb_cannot_store_it(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    for payload in ({"name": "a\x00b"}, {"a\x00": 1}, {"deep": [{"x": ["\x00"]}]}):
        result = await append_event(
            app_schema, game_id, submission(game_id, payload=payload), actor=player_actor()
        )
        assert result == Rejected("invalid_payload")
    assert await _count(app_schema, game_id) == 0


async def test_large_integers_and_unicode_survive_storage_unchanged(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    payload = {
        "big": 2**53 - 1,
        "neg": -(2**53 - 1),
        "text": "café 🧙 " + chr(0x2028) + r' "quoted" \ ',
        "list": [0, -0, 7, [], {}],
        "é": {"z": None, "a": True},
    }
    result = await _appended(app_schema, game_id, payload=payload)
    app_schema.expire_all()
    stored = await get_event(app_schema, game_id, UUID(result.event.event_id))
    assert stored is not None
    assert canonical_json(stored.payload) == canonical_json(payload)
    state, last_seq = await get_snapshot(app_schema, game_id)
    assert last_seq == 1
    assert canonical_json(state.to_json_value()) == canonical_json(result.state.to_json_value())


async def test_a_failure_after_the_event_row_is_written_leaves_nothing_behind(
    app_schema: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = await make_game(app_schema)
    await _appended(app_schema, game_id)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("snapshot write failed")

    monkeypatch.setattr(repository, "save_snapshot", boom)
    with pytest.raises(RuntimeError, match="snapshot write failed"):
        async with app_schema.begin_nested():  # stands in for the caller's transaction
            await append_event(app_schema, game_id, submission(game_id), actor=player_actor())
    app_schema.expire_all()
    assert await _count(app_schema, game_id) == 1
    game = await app_schema.get_one(Game, game_id)
    assert game.last_seq == 1
    assert game.state["last_seq"] == 1


async def test_an_unknown_game_raises_not_found(app_schema: AsyncSession) -> None:
    missing = uuid4()
    with pytest.raises(NotFoundError):
        await append_event(app_schema, missing, submission(missing), actor=player_actor())
    with pytest.raises(NotFoundError):
        await get_snapshot(app_schema, missing)


async def test_a_snapshot_that_disagrees_with_last_seq_is_reported_not_extended(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    game = await app_schema.get_one(Game, game_id)
    game.last_seq = 5  # the snapshot still says 0
    await app_schema.flush()
    with pytest.raises(GameLogCorruptError):
        await append_event(app_schema, game_id, submission(game_id), actor=player_actor())
    game.last_seq = 0
    game.state = {"not": "a state"}
    await app_schema.flush()
    with pytest.raises(GameLogCorruptError):
        await get_snapshot(app_schema, game_id)


async def test_read_events_since_returns_later_events_in_order(app_schema: AsyncSession) -> None:
    game_id = await make_game(app_schema)
    other = await make_game(app_schema)
    for _ in range(4):
        await _appended(app_schema, game_id)
    await _appended(app_schema, other)
    assert [e.seq for e in await read_events_since(app_schema, game_id, 0)] == [1, 2, 3, 4]
    assert [e.seq for e in await read_events_since(app_schema, game_id, 2)] == [3, 4]
    assert [e.seq for e in await read_events_since(app_schema, game_id, 1, limit=2)] == [2, 3]
    assert await read_events_since(app_schema, game_id, 4) == []


async def test_get_event_finds_an_event_by_its_client_id_within_its_game(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    other = await make_game(app_schema)
    result = await _appended(app_schema, game_id)
    event_id = UUID(result.event.event_id)
    assert await get_event(app_schema, game_id, event_id) == result.event
    assert await get_event(app_schema, other, event_id) is None


@pytest.mark.parametrize(
    "actor",
    [
        Actor.model_construct(kind="player", player_id="bad\x00id"),
        Actor.model_construct(kind="player", player_id="bad\ud800id"),
        Actor.model_construct(kind="player", player_id=""),
        Actor.model_construct(kind="player", player_id=None),
        Actor.model_construct(kind="system", player_id="someone"),
        Actor.model_construct(kind="robot", player_id=None),
    ],
)
async def test_an_actor_that_cannot_be_stored_raises_before_anything_is_written(
    app_schema: AsyncSession, actor: Actor
) -> None:
    game_id = await make_game(app_schema)
    with pytest.raises(InvalidActorError):
        await append_event(app_schema, game_id, submission(game_id), actor=actor)
    assert await _count(app_schema, game_id) == 0
    assert (await app_schema.execute(select(1))).scalar_one() == 1  # no DataError poisoned it


async def test_a_nul_in_type_or_event_id_is_rejected_not_a_database_error(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    bad_type = {**submission(game_id), "type": "turn_passed\x00"}
    bad_id = {**submission(game_id), "event_id": "\x00" + str(uuid4())[1:]}
    assert await append_event(app_schema, game_id, bad_type, actor=player_actor()) == Rejected(
        "unknown_event_type"
    )
    assert await append_event(app_schema, game_id, bad_id, actor=player_actor()) == Rejected(
        "invalid_event"
    )
    assert await _count(app_schema, game_id) == 0


async def test_a_lone_surrogate_in_a_payload_is_rejected_not_a_database_error(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    for payload in ({"a": "\ud800"}, {"\udc00": 1}):
        result = await append_event(
            app_schema, game_id, submission(game_id, payload=payload), actor=player_actor()
        )
        assert result == Rejected("invalid_payload")
    assert await _count(app_schema, game_id) == 0


async def test_a_duplicate_whose_new_payload_is_not_canonical_is_flagged_as_different(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    first = await _appended(app_schema, game_id, payload={"n": 1})
    again = await append_event(
        app_schema,
        game_id,
        submission(game_id, payload={"n": 1.5}, event_id=first.event.event_id),
        actor=player_actor(),
    )
    assert isinstance(again, Appended)
    assert (again.duplicate, again.body_differs) == (True, True)


async def test_a_submission_without_seq_ts_or_actor_keys_is_accepted_the_server_owns_them(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    sent = submission(game_id)
    del sent["actor"]
    first = await append_event(app_schema, game_id, sent, actor=player_actor())
    sent2 = {**submission(game_id), "seq": None, "ts": 5}
    second = await append_event(app_schema, game_id, sent2, actor=player_actor())
    assert isinstance(first, Appended)
    assert isinstance(second, Appended)
    assert second.event.seq == 2


async def test_add_game_and_get_game_round_trip_a_game_row(app_schema: AsyncSession) -> None:
    group = await create(app_schema, PlaygroupFactory)
    game = GameFactory.build(playgroup_id=group.id)
    repository.add_game(app_schema, game)
    await app_schema.flush()
    assert await repository.get_game(app_schema, game.id) is game
    assert await repository.get_game(app_schema, uuid4()) is None


async def test_a_seq_collision_that_is_not_a_duplicate_event_id_is_raised(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    await _appended(app_schema, game_id)
    game = await app_schema.get_one(Game, game_id)
    game.last_seq = 0  # a damaged counter: the next seq (1) is already taken
    game.state = initial_state(str(game_id)).to_json_value()
    await app_schema.flush()
    with pytest.raises(IntegrityError):
        await append_event(app_schema, game_id, submission(game_id), actor=player_actor())
    assert (await app_schema.execute(select(1))).scalar_one() == 1
