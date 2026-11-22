"""Racing for one single-use playgroup invite over HTTP, with real commits (S-2.4.2 AC2, AC3).

Unlike ``test_playgroup_invites_concurrency.py`` (which drives the service behind a held row
lock), this sends real requests through the app at once, against a throwaway migrated database.
Whatever the interleaving, a single-use invite admits exactly one person and counts exactly one use.
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
from tests.register_support import PASSWORD, register_body

JOIN = "/api/v1/invites/join"
REGISTER = "/api/v1/auth/register"
ATTEMPTS = 3


@dataclass
class World:
    engine: AsyncEngine
    client: httpx.AsyncClient
    group_id: UUID
    token: str
    joiners: list[dict[str, str]]


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
        for name in ["owner", *[f"joiner{i}" for i in range(ATTEMPTS)]]:
            info = await create_user_with_player(
                session, username=name, password=PASSWORD, display_name=name.title()
            )
            assert info.user_id is not None
            accounts.append(info.user_id)
        group = await services.create_playgroup(session, user_id=accounts[0], name="Friday")
        access = await services.get_membership(session, playgroup_id=group.id, user_id=accounts[0])
        assert access is not None
        created = await services.create_playgroup_invite(session, access)
        await session.commit()
    probe = create_async_engine(migrated_url, poolclass=NullPool)
    transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        try:
            yield World(
                engine=probe,
                client=client,
                group_id=group.id,
                token=created.url.rsplit("/", 1)[1],
                joiners=[{"Authorization": f"Bearer {issue(u)}"} for u in accounts[1:]],
            )
        finally:
            await probe.dispose()
            await db.dispose_engine()


async def _scalar(world: World, sql: str) -> int:
    async with world.engine.connect() as conn:
        return int((await conn.execute(text(sql))).scalar_one())


async def test_signed_in_users_racing_for_a_single_use_invite_admit_exactly_one(
    world: World,
) -> None:
    responses = await asyncio.gather(
        *[
            world.client.post(JOIN, json={"token": world.token}, headers=headers)
            for headers in world.joiners
        ]
    )

    assert sorted(r.status_code for r in responses) == [200] + [410] * (ATTEMPTS - 1)
    for loser in (r for r in responses if r.status_code == 410):
        assert loser.json()["error"]["code"] == "invite_no_longer_valid"
    winner = next(r for r in responses if r.status_code == 200)
    assert winner.json()["already_member"] is False
    assert await _scalar(world, "SELECT use_count FROM invites") == 1
    assert await _scalar(world, "SELECT count(*) FROM memberships WHERE role = 'member'") == 1


async def test_a_new_registration_and_a_signed_in_join_race_for_one_single_use_invite(
    world: World,
) -> None:
    register = world.client.post(
        REGISTER,
        json=register_body(world.token, username="latecomer"),
        headers={"X-Client": "mobile"},
    )
    join = world.client.post(JOIN, json={"token": world.token}, headers=world.joiners[0])

    responses = await asyncio.gather(register, join)

    assert sorted(r.status_code for r in responses) in ([200, 410], [201, 410])
    assert await _scalar(world, "SELECT use_count FROM invites") == 1
    assert await _scalar(world, "SELECT count(*) FROM memberships WHERE role = 'member'") == 1
    users = await _scalar(world, "SELECT count(*) FROM users")
    assert users == (ATTEMPTS + 2 if responses[0].status_code == 201 else ATTEMPTS + 1)
