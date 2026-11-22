"""The ``display_name_key`` migration (S-2.2.4): backfill and downgrade, on a throwaway database.

Synchronous because ``env.py`` runs its own event loop with ``asyncio.run``.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.identity.names import display_name_key
from tests.migration_support import API_ROOT, alembic_config, temporary_database

PARENT = "0c0eb7757b9f"
NAMES = ["Jöel", "ÁLVARO", "Zoë  Ng", "Straße", "Bob", "a\u200bb"]
MIGRATION = next((API_ROOT / "migrations" / "versions").glob("*_5a1c3e7d9b42_*.py"))
INSERT = text(
    "INSERT INTO players (id, is_guest, display_name, avatar_color, created_at) "
    "VALUES (:id, true, :n, 'red', now())"
)


def test_upgrade_backfills_every_existing_player_and_downgrade_drops_the_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), PARENT)
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

            command.downgrade(alembic_config(), PARENT)
            columns = {c["name"] for c in inspect(engine).get_columns("players")}
            assert "display_name_key" not in columns
        finally:
            engine.dispose()


def test_the_backfill_runs_in_several_batches_and_keys_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = [f"Pláyer {i:02d}" for i in range(7)]  # 7 rows with a batch of 2: 4 batches
    spec = importlib.util.spec_from_file_location("players_key_migration", Path(MIGRATION))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "BATCH", 2)
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), PARENT)
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                for name in names:
                    conn.execute(INSERT, {"id": uuid.uuid4(), "n": name})
                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT display_name, display_name_key FROM players")
                ).all()
            assert dict(rows) == {n: display_name_key(n) for n in names}
            assert all(key.startswith("player ") for _, key in rows)
        finally:
            engine.dispose()


def test_the_column_has_no_database_default_after_the_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            column = {c["name"]: c for c in inspect(engine).get_columns("players")}[
                "display_name_key"
            ]
            assert column["default"] is None
            assert column["nullable"] is False
        finally:
            engine.dispose()
