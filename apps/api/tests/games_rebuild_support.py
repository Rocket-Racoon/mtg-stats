"""Helpers for the S-6.3.8 acceptance tests: a real game, an independent replay, damage.

Nothing here calls the rebuild service. ``independent_state`` folds the stored events with the
reducer's public ``replay``, which is the oracle every rebuilt snapshot is compared with.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.games.services import (
    Appended,
    GameState,
    ReplayOk,
    append_event,
    canonical_bytes,
    format_ts,
    initial_state,
    replay,
)
from tests.games_support import player_actor, submission

STARTED_AT = "2026-11-21T12:00:00.000Z"

# Recognisable strings seeded into the game. None of them may ever reach a log record.
SECRETS = (
    "Zebulon Quixote",
    "Ysolde Marrowgate",
    "Xanthe Pelham",
    "Wystan Oddlaw",
    "Zoë Ünicöde \U0001f9d9",
    "pid-seed-1",
    "pid-seed-2",
    "pid-seed-3",
    "pid-seed-4",
    "deck-seed-alpha",
    "card-seed-1",
    "card-seed-2",
    "card-seed-3",
    "Commander Seedname One",
    "Commander Seedname Two",
    "Commander Seedname Three",
)

NINE_HANDLER_TYPES = frozenset(
    {
        "game_created",
        "settings_changed",
        "seat_joined",
        "seat_left",
        "seat_moved",
        "deck_assigned",
        "commanders_set",
        "game_started",
        "turn_passed",
    }
)

Step = tuple[str, dict[str, Any]]


def lobby_steps() -> list[Step]:
    """Lobby events: every lobby handler of the reducer, with recognisable names."""
    return [
        (
            "game_created",
            {
                "settings": {
                    "starting_life": 40,
                    "commander_damage_threshold": 21,
                    "poison_threshold": 10,
                    "trust_mode": "casual",
                }
            },
        ),
        ("settings_changed", {"starting_life": 30}),
        ("seat_joined", {"seat": 1, "player_id": "pid-seed-1", "name": SECRETS[0]}),
        ("seat_joined", {"seat": 2, "player_id": "pid-seed-2", "name": SECRETS[1]}),
        ("seat_joined", {"seat": 3, "player_id": "pid-seed-3", "name": SECRETS[2]}),
        ("seat_joined", {"seat": 4, "player_id": "pid-seed-4", "name": SECRETS[3]}),
        ("seat_joined", {"seat": 5, "player_id": None, "name": SECRETS[4]}),
        ("seat_left", {"seat": 5}),
        ("seat_moved", {"from": 1, "to": 2}),
        (
            "deck_assigned",
            {
                "seat": 1,
                "deck_version_id": "deck-seed-alpha",
                "commanders": [{"card_id": "card-seed-1", "name": SECRETS[13]}],
            },
        ),
        (
            "commanders_set",
            {
                "seat": 3,
                "commanders": [
                    {"card_id": "card-seed-2", "name": SECRETS[14]},
                    {"card_id": "card-seed-3", "name": SECRETS[15]},
                ],
            },
        ),
    ]


def play_steps() -> list[Step]:
    """``game_started`` and play: turns, life changes, a concession and a correction."""
    return [
        ("game_started", {"first_seat": 3, "started_at": STARTED_AT}),
        *([("turn_passed", {})] * 6),
        ("life_changed", {"seat": 1, "delta": -7}),
        ("life_changed", {"seat": 3, "delta": -30}),
        ("player_conceded", {"seat": 2}),
        ("turn_passed", {}),
        ("turn_passed", {}),
        ("seat_moved", {"from": 1, "to": 4, "correction": True}),
    ]


def real_steps() -> list[Step]:
    return [*lobby_steps(), *play_steps()]


async def append_steps(session: AsyncSession, game_id: UUID, steps: list[Step]) -> None:
    """Append through the real ``games.append_event`` (not committed)."""
    for type_, payload in steps:
        result = await append_event(
            session, game_id, submission(game_id, type_, payload), actor=player_actor()
        )
        assert isinstance(result, Appended), (type_, result)


async def wire_events(session: AsyncSession, game_id: UUID) -> list[dict[str, Any]]:
    """The stored events as full wire envelopes, read with plain SQL, in seq order."""
    rows = await session.execute(
        text(
            "SELECT seq, event_id, type, payload, actor, on_behalf_of_seat, ts, v "
            "FROM game_events WHERE game_id = :g ORDER BY seq"
        ),
        {"g": game_id},
    )
    return [
        {
            "event_id": str(row.event_id),
            "game_id": str(game_id),
            "seq": row.seq,
            "type": row.type,
            "payload": row.payload,
            "actor": row.actor,
            "on_behalf_of_seat": row.on_behalf_of_seat,
            "ts": format_ts(row.ts),
            "v": row.v,
        }
        for row in rows
    ]


def fold(game_id: UUID | str, wire: list[dict[str, Any]]) -> GameState:
    """The reducer's replay of ``wire`` from the empty lobby; fails the test if it rejects."""
    outcome = replay(initial_state(str(game_id)), wire)
    assert isinstance(outcome, ReplayOk), outcome
    return outcome.state


async def independent_state(session: AsyncSession, game_id: UUID) -> tuple[GameState, int]:
    """The oracle: the replay of the stored events, and how many there are."""
    wire = await wire_events(session, game_id)
    return fold(game_id, wire), len(wire)


def state_bytes(value: Any) -> bytes:
    return canonical_bytes(value)


@dataclass(frozen=True)
class Row:
    state: Any
    last_seq: int
    updated_at: datetime


async def read_game(session: AsyncSession, game_id: UUID) -> Row:
    """The committed-or-flushed ``games`` columns, read with SQL (never through the ORM cache)."""
    row = (
        await session.execute(
            text("SELECT state, last_seq, updated_at FROM games WHERE id = :g"), {"g": game_id}
        )
    ).one()
    return Row(row.state, row.last_seq, row.updated_at)


async def set_snapshot(
    session: AsyncSession, game_id: UUID, state: Any, last_seq: int | None = None
) -> None:
    """Damage ``games.state`` (and optionally ``last_seq``) with plain SQL; events are untouched."""
    await session.execute(
        text("UPDATE games SET state = CAST(:s AS jsonb) WHERE id = :g"),
        {"s": json.dumps(state), "g": game_id},
    )
    if last_seq is not None:
        await session.execute(
            text("UPDATE games SET last_seq = :n WHERE id = :g"), {"n": last_seq, "g": game_id}
        )


async def event_rows(session: AsyncSession, game_id: UUID) -> list[tuple[Any, ...]]:
    """Every column of every event row plus the row's ``xmin``: any rewrite shows up."""
    rows = await session.execute(
        text(
            "SELECT xmin::text, id, seq, event_id, type, payload, actor, on_behalf_of_seat, ts, v "
            "FROM game_events WHERE game_id = :g ORDER BY seq"
        ),
        {"g": game_id},
    )
    return [tuple(row) for row in rows]


@contextmanager
def sql_log(session: AsyncSession) -> Iterator[list[str]]:
    """The SQL statements the session's connection executes inside the block."""
    bind = session.sync_session.get_bind()
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(bind, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(bind, "before_cursor_execute", record)


def writes(statements: list[str]) -> list[str]:
    """The statements that are not plain reads (locking ``SELECT ... FOR`` is a read here)."""
    return [
        s
        for s in statements
        if not s.lstrip().upper().startswith(("SELECT", "SAVEPOINT", "RELEASE"))
    ]


def other_game_id() -> UUID:
    return uuid4()


def shaped_log(game_id: UUID, total: int) -> list[dict[str, Any]]:
    """Six seats, decks and commanders, a start, then turns, life changes and corrections."""
    specs: list[tuple[str, dict[str, Any]]] = [
        (
            "game_created",
            {
                "settings": {
                    "starting_life": 40,
                    "commander_damage_threshold": 21,
                    "poison_threshold": 10,
                    "trust_mode": "strict",
                }
            },
        ),
        *(
            ("seat_joined", {"seat": i, "player_id": f"p{i}", "name": f"Player {i}"})
            for i in range(1, 7)
        ),
        *(
            (
                "deck_assigned",
                {
                    "seat": i,
                    "deck_version_id": f"deck-{i}",
                    "commanders": [{"card_id": f"card-{i}", "name": f"Commander {i}"}],
                },
            )
            for i in (1, 2, 3)
        ),
        *(
            (
                "commanders_set",
                {
                    "seat": i,
                    "commanders": [
                        {"card_id": f"card-{i}a", "name": f"Partner {i}a"},
                        {"card_id": f"card-{i}b", "name": f"Partner {i}b"},
                    ],
                },
            )
            for i in (4, 5)
        ),
        ("game_started", {"first_seat": 2, "started_at": "2026-11-21T12:00:00.000Z"}),
    ]
    step = 0
    while len(specs) < total:
        step += 1
        if step % 25 == 0:
            specs.append(
                ("seat_moved", {"from": 1 + step % 6, "to": 1 + (step + 2) % 6, "correction": True})
            )
        elif step % 3 == 0:
            specs.append(("life_changed", {"seat": 1 + step % 6, "delta": -(step % 5) - 1}))
        else:
            specs.append(("turn_passed", {}))
    return [
        {
            "event_id": str(uuid4()),
            "game_id": str(game_id),
            "seq": seq,
            "type": type_,
            "payload": payload,
            "actor": {"kind": "player", "player_id": "p1"},
            "on_behalf_of_seat": None,
            "ts": "2026-11-21T12:00:00.000Z",
            "v": 1,
        }
        for seq, (type_, payload) in enumerate(specs, start=1)
    ]
