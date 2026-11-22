"""Racing guest adds and renames over HTTP, with real commits (S-2.3.1 AC2, AC3).

Like ``test_playgroup_invites_acceptance_concurrency.py``: real requests through the app at once,
against a throwaway migrated database. Whatever the interleaving, a name is held by at most one
member or guest of the playgroup.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from uuid import UUID

import httpx
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import db
from app.identity.services import create_user_with_player
from app.main import create_app
from app.playgroups import services
from tests.auth_support import issue
from tests.migration_support import alembic_config, temporary_database
from tests.playgroup_support import join
from tests.register_support import PASSWORD

ATTEMPTS = 6


@dataclass
class World:
    engine: AsyncEngine
    client: httpx.AsyncClient
    group_id: UUID
    members: list[dict[str, str]]
    url: str


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
async def world(migrated_url: str) -> AsyncIterator[World]:
    async with db.get_sessionmaker()() as session:
        accounts: list[UUID] = []
        players: list[UUID] = []
        for name in ["owner", *[f"member{i}" for i in range(ATTEMPTS)]]:
            info = await create_user_with_player(
                session, username=name, password=PASSWORD, display_name=name.title()
            )
            assert info.user_id is not None
            accounts.append(info.user_id)
            players.append(info.id)
        group = await services.create_playgroup(session, user_id=accounts[0], name="Friday")
        await session.flush()
        for player_id in players[1:]:
            await join(session, group.id, player_id)
        await session.commit()
    probe = create_async_engine(migrated_url, poolclass=NullPool)
    transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        try:
            headers = [{"Authorization": f"Bearer {issue(u)}"} for u in accounts]
            yield World(probe, client, group.id, headers, f"/api/v1/playgroups/{group.id}/guests")
        finally:
            await probe.dispose()
            await db.dispose_engine()


async def _access(session: db.AppSession, group_id: UUID, user_id: UUID) -> services.MembershipInfo:
    access = await services.get_membership(session, playgroup_id=group_id, user_id=user_id)
    assert access is not None
    return access


async def _names(world: World) -> list[str]:
    async with world.engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT display_name FROM players WHERE is_guest AND home_playgroup_id = :g"),
            {"g": world.group_id},
        )
        return [row.display_name for row in rows]


async def test_simultaneous_adds_of_one_name_in_different_cases_create_exactly_one_guest(
    world: World,
) -> None:
    variants = ["Zoë", "ZOË", "zoe", "ZOE", "Zoë", " zoë "]
    responses = await asyncio.gather(
        *[
            world.client.post(world.url, json={"display_name": name}, headers=headers)
            for name, headers in zip(variants, world.members[1:], strict=True)
        ]
    )

    assert sorted(r.status_code for r in responses) == [201] + [409] * (ATTEMPTS - 1)
    for loser in (r for r in responses if r.status_code == 409):
        assert loser.json()["error"]["code"] == "player_name_taken"
    assert len(await _names(world)) == 1


async def test_simultaneous_adds_of_different_names_all_succeed(world: World) -> None:
    responses = await asyncio.gather(
        *[
            world.client.post(world.url, json={"display_name": f"Guest {i}"}, headers=headers)
            for i, headers in enumerate(world.members[1:])
        ]
    )

    assert [r.status_code for r in responses] == [201] * ATTEMPTS
    assert len(set(await _names(world))) == ATTEMPTS


async def test_a_guest_add_racing_a_rename_to_the_same_name_leaves_one_holder(
    world: World,
) -> None:
    owner = world.members[0]
    first = await world.client.post(world.url, json={"display_name": "Ava"}, headers=owner)
    assert first.status_code == 201
    first_id = first.json()["id"]

    renamed, added = await asyncio.gather(
        world.client.patch(f"{world.url}/{first_id}", json={"display_name": "Cy"}, headers=owner),
        world.client.post(world.url, json={"display_name": "CY"}, headers=world.members[1]),
    )

    assert sorted([renamed.status_code, added.status_code]) in ([201, 409], [200, 409])
    keys = [name.casefold() for name in await _names(world)]
    assert keys.count("cy") == 1


async def test_two_guests_renamed_to_one_name_at_once_leave_one_holder(world: World) -> None:
    owner = world.members[0]
    ids = [
        (await world.client.post(world.url, json={"display_name": n}, headers=owner)).json()["id"]
        for n in ("A", "B", "C")
    ]

    responses = await asyncio.gather(
        *[
            world.client.patch(
                f"{world.url}/{guest_id}", json={"display_name": name}, headers=owner
            )
            for guest_id, name in zip(ids, ("Dot", "DOT", "dot"), strict=True)
        ]
    )

    assert sorted(r.status_code for r in responses) == [200, 409, 409]
    assert [n.casefold() for n in await _names(world)].count("dot") == 1
