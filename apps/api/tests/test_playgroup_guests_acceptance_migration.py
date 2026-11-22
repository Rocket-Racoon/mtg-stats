"""The ``players.created_by_id`` migration (S-2.3.1) on a throwaway database."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests.migration_support import alembic_config, temporary_database

REVISION = "9de90f47660b"
GUEST_ID = "00000000-0000-7000-8000-0000000000a1"
CREATOR_ID = "00000000-0000-7000-8000-0000000000a2"
INSERT_PLAYER = text(
    "INSERT INTO players (id, user_id, is_guest, display_name, avatar_color, home_playgroup_id, "
    "created_at) VALUES (:id, NULL, true, :name, 'blue', :home, now())"
)


def _parent() -> str:
    parent = ScriptDirectory.from_config(alembic_config()).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def test_existing_guests_get_a_null_creator_and_the_column_is_a_nullable_player_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), _parent())
        engine = create_engine(url)
        try:
            columns_before = {c["name"] for c in inspect(engine).get_columns("players")}
            assert "created_by_id" not in columns_before
            with engine.begin() as conn:
                conn.execute(INSERT_PLAYER, {"id": GUEST_ID, "name": "Old Guest", "home": GUEST_ID})
            command.upgrade(alembic_config(), REVISION)
            insp = inspect(engine)
            columns = {c["name"]: c for c in insp.get_columns("players")}
            assert columns["created_by_id"]["nullable"] is True
            assert str(columns["created_by_id"]["type"]).upper() == "UUID"
            fks = {
                tuple(fk["constrained_columns"]): fk["referred_table"]
                for fk in insp.get_foreign_keys("players")
            }
            assert fks[("created_by_id",)] == "players"
            with engine.connect() as conn:
                assert conn.execute(text("SELECT created_by_id FROM players")).scalar_one() is None
        finally:
            engine.dispose()


def test_a_creator_must_be_an_existing_player_and_can_be_set_on_a_guest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), REVISION)
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(INSERT_PLAYER, {"id": CREATOR_ID, "name": "Maker", "home": CREATOR_ID})
                conn.execute(
                    text(
                        "INSERT INTO players (id, user_id, is_guest, display_name, avatar_color, "
                        "home_playgroup_id, created_by_id, created_at) "
                        "VALUES (:id, NULL, true, 'Made', 'blue', :home, :by, now())"
                    ),
                    {"id": GUEST_ID, "home": CREATOR_ID, "by": CREATOR_ID},
                )
            with engine.connect() as conn:
                created_by = conn.execute(
                    text("SELECT created_by_id FROM players WHERE id = :i"), {"i": GUEST_ID}
                ).scalar_one()
            assert str(created_by) == CREATOR_ID
            with pytest.raises(Exception, match="created_by_id"), engine.begin() as conn:
                conn.execute(
                    text("UPDATE players SET created_by_id = gen_random_uuid() WHERE id = :i"),
                    {"i": GUEST_ID},
                )
        finally:
            engine.dispose()


def test_a_downgrade_removes_the_column_keeps_the_guests_and_an_upgrade_restores_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), REVISION)
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(INSERT_PLAYER, {"id": GUEST_ID, "name": "Keeper", "home": GUEST_ID})
                conn.execute(text("UPDATE players SET created_by_id = id"))
            command.downgrade(alembic_config(), _parent())
            insp = inspect(engine)
            assert "created_by_id" not in {c["name"] for c in insp.get_columns("players")}
            with engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT display_name FROM players")).scalar_one() == "Keeper"
                )
            command.upgrade(alembic_config(), "head")
            assert "created_by_id" in {c["name"] for c in inspect(engine).get_columns("players")}
            command.check(alembic_config())  # raises on model drift
        finally:
            engine.dispose()
