"""``invites.playgroup_id`` on a migrated database: round trip and no drift (S-2.4.2).

``test_playgroup_invites_migration.py`` already proves the column, its nullability, the cascading
foreign key, the index, and that a downgrade drops only the column. This adds the full
upgrade, downgrade, upgrade round trip with a playgroup invite, and ``alembic check``.
"""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from tests.migration_support import alembic_config, temporary_database
from tests.test_playgroup_invites_migration import GROUP_ID, INSERT_INVITE, INSERT_USER, REVISION


def test_upgrade_downgrade_upgrade_works_and_the_models_have_no_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = ScriptDirectory.from_config(alembic_config()).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        command.check(alembic_config())  # raises on drift
        command.downgrade(alembic_config(), parent)
        command.upgrade(alembic_config(), "head")
        command.check(alembic_config())
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(INSERT_USER, {"id": "00000000-0000-7000-8000-000000000001"})
                conn.execute(
                    text(
                        "INSERT INTO playgroups (id, name, description, created_at, updated_at) "
                        "VALUES (:g, 'Friday', '', now(), now())"
                    ),
                    {"g": GROUP_ID},
                )
                conn.execute(INSERT_INVITE, {"uid": "00000000-0000-7000-8000-000000000001"})
                conn.execute(text("UPDATE invites SET playgroup_id = :g"), {"g": GROUP_ID})
            with engine.connect() as conn:
                assert (
                    str(conn.execute(text("SELECT playgroup_id FROM invites")).scalar_one())
                    == GROUP_ID
                )
        finally:
            engine.dispose()
