"""The ``players.display_name_key`` migration (S-2.2.4), acceptance side, on a throwaway database.

Synchronous because ``env.py`` runs its own event loop with ``asyncio.run``.
"""

import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.identity.names import display_name_key
from tests.migration_support import alembic_config, temporary_database

REVISION = "5a1c3e7d9b42"
NAMES = [
    "Jöel",
    "Double  space",
    "ÁLVARO",
    "Jo\u0308rg",
    "a\u200bb",
    "Straße",
    "\u0301",  # only a combining mark: the key is empty
    "Ann-Jo",
    "\U0001f600 emoji",
    "Жанна",
]
INSERT = text(
    "INSERT INTO players (id, is_guest, display_name, avatar_color, created_at) "
    "VALUES (:id, true, :n, 'red', now())"
)


def _parent() -> str:
    parent = ScriptDirectory.from_config(alembic_config()).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def test_the_revision_is_the_head_and_adds_a_required_text_column() -> None:
    assert ScriptDirectory.from_config(alembic_config()).get_heads() == [REVISION]


def test_pre_existing_rows_are_backfilled_with_the_python_function_and_the_column_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), _parent())
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                for name in NAMES:
                    conn.execute(INSERT, {"id": uuid.uuid4(), "n": name})
            command.upgrade(alembic_config(), "head")

            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT display_name, display_name_key FROM players")
                ).all()
            assert dict(rows) == {n: display_name_key(n) for n in NAMES}
            assert dict(rows)["\u0301"] == ""
            column = {c["name"]: c for c in inspect(engine).get_columns("players")}[
                "display_name_key"
            ]
            assert column["nullable"] is False
            assert str(column["type"]).upper() == "TEXT"
        finally:
            engine.dispose()


def test_a_downgrade_drops_the_column_keeps_the_players_and_a_second_upgrade_backfills_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO players (id, is_guest, display_name, display_name_key, "
                        "avatar_color, created_at) VALUES (:id, true, 'Jöel', 'joel', 'red', now())"
                    ),
                    {"id": uuid.uuid4()},
                )
            command.downgrade(alembic_config(), _parent())
            assert "display_name_key" not in {
                c["name"] for c in inspect(engine).get_columns("players")
            }
            with engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM players")).scalar_one() == 1

            command.upgrade(alembic_config(), "head")
            with engine.connect() as conn:
                assert conn.execute(text("SELECT display_name_key FROM players")).scalar_one() == (
                    "joel"
                )
        finally:
            engine.dispose()


def test_alembic_check_shows_no_drift_at_head(monkeypatch: pytest.MonkeyPatch) -> None:
    with temporary_database(monkeypatch):
        config = alembic_config()
        command.upgrade(config, "head")
        command.check(config)  # raises when the models and the migrations drift
