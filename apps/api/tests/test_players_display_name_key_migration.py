"""The ``display_name_key`` migration (S-2.2.4): backfill and downgrade, on a throwaway database.

Synchronous because ``env.py`` runs its own event loop with ``asyncio.run``.
"""

import uuid

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.identity.names import display_name_key
from tests.migration_support import alembic_config, temporary_database

PARENT = "0c0eb7757b9f"
NAMES = ["Jöel", "ÁLVARO", "Zoë  Ng", "Straße", "Bob", "a\u200bb"]


def test_upgrade_backfills_every_existing_player_and_downgrade_drops_the_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), PARENT)
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                for name in NAMES:
                    conn.execute(
                        text(
                            "INSERT INTO players (id, is_guest, display_name, avatar_color, "
                            "created_at) VALUES (:id, true, :n, 'red', now())"
                        ),
                        {"id": uuid.uuid4(), "n": name},
                    )
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
