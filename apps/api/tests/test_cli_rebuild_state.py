"""``python -m app.cli rebuild-state`` (S-6.3.8): repair, dry run, refusals and exit codes.

Like the other CLI tests it runs against the rolled-back ``db_session`` through an injected
session factory.
"""

import io
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import run
from app.core.db import AppSession
from app.games import repository
from app.games.services import canonical_json, initial_state
from tests.games_support import insert_log, lobby_and_turns, make_game


@dataclass
class Outcome:
    code: int
    out: str
    err: str


@pytest.fixture
async def session(app_schema: AppSession) -> AppSession:
    """The rolled-back session with the schema committed to its outer savepoint."""
    await app_schema.commit()
    return app_schema


async def invoke(session: AppSession, argv: Sequence[str]) -> Outcome:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AppSession]:
        yield session

    out, err = io.StringIO(), io.StringIO()
    code = await run(argv, session_factory=factory, stdout=out, stderr=err)
    return Outcome(code, out.getvalue(), err.getvalue())


async def _stale_game(session: AsyncSession, events: int = 12) -> UUID:
    game_id = await make_game(session)
    await insert_log(session, game_id, lobby_and_turns(game_id, events))
    await session.commit()
    return game_id


async def _stored(session: AsyncSession, game_id: UUID) -> tuple[int, str]:
    session.expire_all()
    game = await repository.get_game(session, game_id)
    assert game is not None
    return game.last_seq, canonical_json(game.state)


async def test_rebuild_state_repairs_a_stale_snapshot_and_commits(session: AppSession) -> None:
    game_id = await _stale_game(session)
    outcome = await invoke(session, ["rebuild-state", "--game-id", str(game_id)])
    assert outcome.code == 0, outcome.err
    assert str(game_id) in outcome.out
    assert "rebuilt from 12 events" in outcome.out
    last_seq, state = await _stored(session, game_id)
    assert last_seq == 12
    assert '"phase":"active"' in state


async def test_rebuild_state_on_a_matching_snapshot_says_so_and_writes_nothing(
    session: AppSession,
) -> None:
    game_id = await _stale_game(session)
    await invoke(session, ["rebuild-state", "--game-id", str(game_id)])
    before = await _stored(session, game_id)
    outcome = await invoke(session, ["rebuild-state", "--game-id", str(game_id)])
    assert outcome.code == 0
    assert "matches its 12 events" in outcome.out
    assert await _stored(session, game_id) == before


async def test_a_dry_run_exits_3_on_a_mismatch_and_changes_nothing(session: AppSession) -> None:
    game_id = await _stale_game(session)
    before = await _stored(session, game_id)
    outcome = await invoke(session, ["rebuild-state", "--game-id", str(game_id), "--dry-run"])
    assert outcome.code == 3
    assert "dry run, nothing written" in outcome.out
    assert await _stored(session, game_id) == before
    assert before[0] == 0  # still the stale factory snapshot


async def test_a_dry_run_exits_0_when_the_snapshot_matches(session: AppSession) -> None:
    game_id = await make_game(session)
    await session.commit()
    outcome = await invoke(session, ["rebuild-state", "--game-id", str(game_id), "--dry-run"])
    assert outcome.code == 0
    assert "matches its 0 events" in outcome.out
    assert (
        canonical_json(initial_state(str(game_id)).to_json_value())
        == (await _stored(session, game_id))[1]
    )


async def test_an_unknown_game_exits_1_with_a_message(session: AppSession) -> None:
    outcome = await invoke(session, ["rebuild-state", "--game-id", str(uuid4())])
    assert outcome.code == 1
    assert outcome.err.strip() == "error: Game not found."
    assert outcome.out == ""


async def test_a_log_the_reducer_rejects_exits_1_and_leaves_the_snapshot(
    session: AppSession,
) -> None:
    game_id = await make_game(session)
    envelopes = lobby_and_turns(game_id, 9)
    envelopes[3] = {**envelopes[1], "event_id": envelopes[3]["event_id"], "seq": 4}
    await insert_log(session, game_id, envelopes)
    await session.commit()
    before = await _stored(session, game_id)
    outcome = await invoke(session, ["rebuild-state", "--game-id", str(game_id)])
    assert outcome.code == 1
    assert "seq 4" in outcome.err
    assert "seat_taken" in outcome.err
    assert await _stored(session, game_id) == before


@pytest.mark.parametrize("argv", [["rebuild-state"], ["rebuild-state", "--game-id", "not-a-uuid"]])
async def test_a_missing_or_malformed_game_id_is_a_usage_error(
    session: AppSession, argv: list[str]
) -> None:
    outcome = await invoke(session, argv)
    assert outcome.code == 2


async def test_a_log_without_game_created_prints_the_defaults_note(session: AppSession) -> None:
    game_id = await _stale_game(session)
    envelopes = lobby_and_turns(game_id, 9)[1:]  # no game_created: drop it, renumber below
    other = await make_game(session)
    for seq, envelope in enumerate(envelopes, start=1):
        envelope.update(seq=seq, game_id=str(other))
    await insert_log(session, other, envelopes[:4])  # four seat_joined events
    await session.commit()
    outcome = await invoke(session, ["rebuild-state", "--game-id", str(other)])
    assert outcome.code == 0, outcome.err
    assert "does not start with game_created" in outcome.out
    assert "settings were rebuilt from the defaults" in outcome.out


async def test_a_snapshot_ahead_of_the_log_prints_a_warning(session: AppSession) -> None:
    game_id = await _stale_game(session)
    await invoke(session, ["rebuild-state", "--game-id", str(game_id)])
    game = await repository.get_game(session, game_id)
    assert game is not None
    game.last_seq = 40
    await session.commit()
    outcome = await invoke(session, ["rebuild-state", "--game-id", str(game_id)])
    assert outcome.code == 0
    assert "WARNING: the snapshot or last_seq was ahead of the log" in outcome.out
    assert "does not start with game_created" not in outcome.out
