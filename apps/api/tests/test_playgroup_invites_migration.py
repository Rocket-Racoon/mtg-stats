"""The ``invites.playgroup_id`` migration (S-2.4.2) on a throwaway database."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests.migration_support import alembic_config, temporary_database

REVISION = "37f07339b8e2"
USER_ID = "00000000-0000-7000-8000-000000000001"
GROUP_ID = "00000000-0000-7000-8000-0000000000aa"
INSERT_USER = text(
    "INSERT INTO users (id, username, email, password_hash, is_site_admin, is_active, "
    "created_at, password_changed_at) "
    "VALUES (:id, 'ada', '', 'x', false, true, now(), now())"
)
INSERT_INVITE = text(
    "INSERT INTO invites (id, token_hash, created_by, expires_at, max_uses, use_count, "
    "created_at) VALUES (gen_random_uuid(), 'h1', :uid, now(), 1, 0, now())"
)


def _parent() -> str:
    parent = ScriptDirectory.from_config(alembic_config()).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def test_existing_invites_survive_as_site_level_invites_and_downgrade_drops_the_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), _parent())
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(INSERT_USER, {"id": USER_ID})
                conn.execute(INSERT_INVITE, {"uid": USER_ID})
            command.upgrade(alembic_config(), REVISION)
            with engine.connect() as conn:
                assert conn.execute(text("SELECT playgroup_id FROM invites")).scalar_one() is None
            insp = inspect(engine)
            columns = {c["name"]: c for c in insp.get_columns("invites")}
            assert columns["playgroup_id"]["nullable"] is True
            fks = {
                tuple(fk["constrained_columns"]): (fk["referred_table"], fk["options"])
                for fk in insp.get_foreign_keys("invites")
            }
            assert fks[("playgroup_id",)] == ("playgroups", {"ondelete": "CASCADE"})
            assert "ix_invites_playgroup_id_created_at_id" in {
                i["name"] for i in insp.get_indexes("invites")
            }
            command.downgrade(alembic_config(), _parent())
            columns_after = {c["name"] for c in inspect(engine).get_columns("invites")}
            assert "playgroup_id" not in columns_after
            with engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM invites")).scalar_one() == 1
        finally:
            engine.dispose()


def test_deleting_a_playgroup_deletes_its_invites(monkeypatch: pytest.MonkeyPatch) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(INSERT_USER, {"id": USER_ID})
                conn.execute(
                    text(
                        "INSERT INTO playgroups (id, name, description, created_at, updated_at) "
                        "VALUES (:g, 'Friday', '', now(), now())"
                    ),
                    {"g": GROUP_ID},
                )
                conn.execute(INSERT_INVITE, {"uid": USER_ID})
                conn.execute(text("UPDATE invites SET playgroup_id = :g"), {"g": GROUP_ID})
                conn.execute(text("DELETE FROM playgroups"))
            with engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM invites")).scalar_one() == 0
        finally:
            engine.dispose()
