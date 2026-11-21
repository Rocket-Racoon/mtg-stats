"""S-2.4.1 AC1, "the database allows exactly one owner per playgroup", checked in the database.

Two schemas could differ, so the rule is checked on both: the ORM-built test schema
(``app_schema``) and a throwaway database upgraded to ``head`` by Alembic (the real migration).
"""

import uuid
from datetime import datetime

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.core.db import AppSession
from app.playgroups.models import Membership
from tests.factories import PlaygroupFactory, create
from tests.migration_support import alembic_config, temporary_database
from tests.playgroup_support import join, make_user

# ------------------------------------------------------------------ ORM-built test schema


async def _insert_in_savepoint(session: AppSession, row: Membership) -> None:
    async with session.begin_nested():
        session.add(row)
        await session.flush()


@pytest.mark.usefixtures("app_schema")
async def test_orm_schema_rejects_a_second_active_owner(app_schema: AppSession) -> None:
    _, first, _ = await make_user(app_schema)
    _, second, _ = await make_user(app_schema)
    group = await create(app_schema, PlaygroupFactory)
    group_id = group.id
    await join(app_schema, group_id, first, "owner")

    with pytest.raises(IntegrityError):
        await _insert_in_savepoint(
            app_schema, Membership(playgroup_id=group_id, player_id=second, role="owner")
        )


@pytest.mark.usefixtures("app_schema")
async def test_orm_schema_allows_one_owner_per_playgroup_across_playgroups(
    app_schema: AppSession,
) -> None:
    _, player, _ = await make_user(app_schema)
    first = (await create(app_schema, PlaygroupFactory)).id
    second = (await create(app_schema, PlaygroupFactory)).id
    await join(app_schema, first, player, "owner")
    await join(app_schema, second, player, "owner")  # no error


# ------------------------------------------------------------------ migrated database (head)

INSERT_USER = text(
    "INSERT INTO users (id, username, email, password_hash, is_site_admin, is_active, "
    "created_at, password_changed_at) "
    "VALUES (:id, :name, '', 'x', false, true, now(), now())"
)
INSERT_PLAYER = text(
    "INSERT INTO players (id, user_id, is_guest, display_name, avatar_color, created_at) "
    "VALUES (:id, :user_id, false, :name, 'red', now())"
)
INSERT_GROUP = text(
    "INSERT INTO playgroups (id, name, description, created_at, updated_at) "
    "VALUES (:id, 'Friday Night', '', now(), now())"
)
INSERT_MEMBERSHIP = text(
    "INSERT INTO memberships (id, playgroup_id, player_id, role, joined_at, left_at) "
    "VALUES (:id, :gid, :pid, :role, now(), :left_at)"
)


def _player(conn: object, name: str) -> str:
    user_id, player_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(INSERT_USER, {"id": user_id, "name": name})  # type: ignore[attr-defined]
    conn.execute(  # type: ignore[attr-defined]
        INSERT_PLAYER, {"id": player_id, "user_id": user_id, "name": name}
    )
    return player_id


def _membership(
    conn: object, gid: str, pid: str, role: str, left_at: datetime | None = None
) -> None:
    conn.execute(  # type: ignore[attr-defined]
        INSERT_MEMBERSHIP,
        {"id": str(uuid.uuid4()), "gid": gid, "pid": pid, "role": role, "left_at": left_at},
    )


def test_migrated_database_rejects_a_second_active_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            gid = str(uuid.uuid4())
            with engine.begin() as conn:
                first, second = _player(conn, "ada"), _player(conn, "bob")
                conn.execute(INSERT_GROUP, {"id": gid})
                _membership(conn, gid, first, "owner")
            with pytest.raises(IntegrityError), engine.begin() as conn:
                _membership(conn, gid, second, "owner")
            with engine.connect() as conn:
                owners = conn.execute(
                    text("SELECT count(*) FROM memberships WHERE role = 'owner'")
                ).scalar_one()
            assert owners == 1
        finally:
            engine.dispose()


def test_migrated_database_still_lets_other_roles_and_other_playgroups_have_their_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            g1, g2 = str(uuid.uuid4()), str(uuid.uuid4())
            with engine.begin() as conn:
                owner, admin, member = (_player(conn, n) for n in ("ada", "bob", "cy"))
                conn.execute(INSERT_GROUP, {"id": g1})
                conn.execute(INSERT_GROUP, {"id": g2})
                _membership(conn, g1, owner, "owner")
                _membership(conn, g1, admin, "admin")
                _membership(conn, g1, member, "member")
                _membership(conn, g2, owner, "owner")  # the same player owns a second group
                count = conn.execute(text("SELECT count(*) FROM memberships")).scalar_one()
            assert count == 4
        finally:
            engine.dispose()


def test_migrated_database_rejects_an_unknown_role(monkeypatch: pytest.MonkeyPatch) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            gid = str(uuid.uuid4())
            with engine.begin() as conn:
                player = _player(conn, "ada")
                conn.execute(INSERT_GROUP, {"id": gid})
            with pytest.raises(IntegrityError), engine.begin() as conn:
                _membership(conn, gid, player, "superuser")
        finally:
            engine.dispose()
