"""The identity migration (S-2.2.1) on a throwaway database, never on ``mtg_stats*``.

Synchronous because ``env.py`` runs its own event loop with ``asyncio.run``.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests.migration_support import alembic_config, temporary_database


def _identity_revision_parent() -> str:
    """Parent of the revision whose file creates the ``players`` table."""
    script = ScriptDirectory.from_config(alembic_config())
    for revision in script.walk_revisions():
        path = revision.path
        if path and '"players"' in Path(path).read_text(encoding="utf-8"):
            assert isinstance(revision.down_revision, str)
            return revision.down_revision
    pytest.fail("no migration creates the players table")


def _inspect(url: str) -> tuple[set[str], dict[str, list[str]], list[str], list[str], list[str]]:
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        if "players" not in tables:
            return tables, {}, [], [], []
        uniques = {c["name"]: c["column_names"] for c in insp.get_unique_constraints("players")}
        checks = [str(c["sqltext"]) for c in insp.get_check_constraints("players")]
        with engine.connect() as conn:
            indexes = [
                row[0]
                for row in conn.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE tablename = 'users'")
                )
            ]
        fks = [
            f"{fk['constrained_columns']}->{fk['referred_table']}"
            for fk in insp.get_foreign_keys("players")
        ]
        return tables, uniques, checks, indexes, fks
    finally:
        engine.dispose()


def test_upgrade_creates_users_and_players_with_their_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        tables, uniques, checks, indexes, fks = _inspect(url)
    assert {"users", "players"} <= tables
    assert ["user_id"] in uniques.values()
    joined = " ".join(checks)
    assert "is_guest" in joined
    assert "user_id IS NOT NULL" in joined
    assert "char_length" in joined
    assert "avatar_color" in joined
    for token in ("red", "orange", "amber", "yellow", "lime", "green"):
        assert f"'{token}'" in joined
    for token in ("teal", "cyan", "blue", "indigo", "violet", "pink"):
        assert f"'{token}'" in joined
    assert any("UNIQUE" in d and "lower(" in d and "username" in d for d in indexes)
    assert "['user_id']->users" in fks
    assert "['merged_into_id']->players" in fks
    assert not any("home_playgroup_id" in fk for fk in fks)


def test_head_has_no_model_drift_after_the_identity_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch):
        config = alembic_config()
        command.upgrade(config, "head")
        command.check(config)


def test_downgrading_the_identity_revision_removes_users_and_players(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        config = alembic_config()
        command.upgrade(config, "head")
        command.downgrade(config, _identity_revision_parent())
        tables, *_ = _inspect(url)
        assert "users" not in tables
        assert "players" not in tables
        assert "alembic_version" in tables
        command.upgrade(config, "head")
        tables, *_ = _inspect(url)
    assert {"users", "players"} <= tables
