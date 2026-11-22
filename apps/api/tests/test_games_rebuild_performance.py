"""A 2,000-event game is rebuilt in under 500 ms (S-6.3.8 AC2).

What is timed is the ``rebuild_state`` call on a warm session: the lock, the read of 2,000 rows,
the replay, the comparison and the write of the snapshot. Building the log is not timed. The best
of three runs is asserted, so one slow scheduler tick on a shared CI runner cannot fail it.
"""

import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.games import repository
from app.games.services import rebuild_state
from tests.games_support import insert_log, lobby_and_turns, make_game

pytestmark = pytest.mark.usefixtures("app_schema")

EVENTS = 2000
LIMIT_SECONDS = 0.5
RUNS = 3


async def test_a_game_with_2000_events_is_rebuilt_in_under_500_ms(
    app_schema: AsyncSession,
) -> None:
    game_id = await make_game(app_schema)
    await insert_log(app_schema, game_id, lobby_and_turns(game_id, EVENTS))
    await rebuild_state(app_schema, game_id)  # warm-up: imports, statement cache, first plan

    timings: list[float] = []
    for _ in range(RUNS):
        game = await repository.get_game(app_schema, game_id)
        assert game is not None
        game.state = {"damaged": True}  # every run writes a full snapshot
        game.last_seq = 0
        await app_schema.flush()
        started = time.perf_counter()
        result = await rebuild_state(app_schema, game_id)
        timings.append(time.perf_counter() - started)
        assert result.written
        assert result.event_count == EVENTS
        assert result.state.last_seq == EVENTS
        assert result.state.turn > 1000
    assert min(timings) < LIMIT_SECONDS, f"timings: {timings}"
