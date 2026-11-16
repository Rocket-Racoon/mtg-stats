"""The refresh-tokens migration (S-2.1.5) on a throwaway database, never on ``mtg_stats*``.

Synchronous because ``env.py`` runs its own event loop with ``asyncio.run``.
"""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from tests.migration_support import alembic_config, temporary_database

REFRESH_TOKENS_REVISION = "b3a91c7e5d42"
"""The revision that creates ``refresh_tokens`` (pinned, like the identity revision)."""


def _parent() -> str:
    revision = ScriptDirectory.from_config(alembic_config()).get_revision(REFRESH_TOKENS_REVISION)
    if revision is None:
        pytest.fail(f"revision {REFRESH_TOKENS_REVISION} not found")
    assert isinstance(revision.down_revision, str)
    return revision.down_revision


def test_upgrade_creates_refresh_tokens_with_unique_hash_foreign_keys_and_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            insp = inspect(engine)
            tables = set(insp.get_table_names())
            columns = {c["name"]: c for c in insp.get_columns("refresh_tokens")}
            uniques = [u["column_names"] for u in insp.get_unique_constraints("refresh_tokens")]
            fks = {
                (tuple(fk["constrained_columns"]), fk["referred_table"]): fk["options"]
                for fk in insp.get_foreign_keys("refresh_tokens")
            }
            with engine.connect() as conn:
                indexes = [
                    row[0]
                    for row in conn.execute(
                        text("SELECT indexdef FROM pg_indexes WHERE tablename = 'refresh_tokens'")
                    )
                ]
        finally:
            engine.dispose()
    assert "refresh_tokens" in tables
    assert {
        "id",
        "user_id",
        "family_id",
        "token_hash",
        "expires_at",
        "revoked_at",
        "replaced_by",
        "created_at",
    } == set(columns)
    assert columns["token_hash"]["type"].length == 64
    assert columns["revoked_at"]["nullable"] is True
    assert columns["replaced_by"]["nullable"] is True
    for name in ("user_id", "family_id", "token_hash", "expires_at", "created_at"):
        assert columns[name]["nullable"] is False
    assert ["token_hash"] in uniques
    assert fks[(("user_id",), "users")].get("ondelete") == "CASCADE"
    assert fks[(("replaced_by",), "refresh_tokens")].get("ondelete") == "SET NULL"
    assert any("(user_id)" in d for d in indexes)
    assert any("(family_id)" in d for d in indexes)
    assert any("ix_refresh_tokens_expires_at" in d and "(expires_at)" in d for d in indexes)
    assert any("ix_refresh_tokens_replaced_by" in d and "(replaced_by)" in d for d in indexes)


def test_downgrade_removes_refresh_tokens_and_keeps_the_identity_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        config = alembic_config()
        command.upgrade(config, "head")
        command.downgrade(config, _parent())
        engine = create_engine(url)
        try:
            tables = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
    assert "refresh_tokens" not in tables
    assert {"users", "players"} <= tables


def test_the_cleanup_indexes_exist_after_upgrade_and_are_gone_after_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = {"ix_refresh_tokens_expires_at", "ix_refresh_tokens_replaced_by"}
    with temporary_database(monkeypatch) as url:
        config = alembic_config()
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            after_upgrade = {i["name"] for i in inspect(engine).get_indexes("refresh_tokens")}
            command.downgrade(config, _parent())
            remaining = {
                row[0]
                for row in engine.connect().execute(
                    text("SELECT indexname FROM pg_indexes WHERE indexname LIKE 'ix_refresh_%'")
                )
            }
        finally:
            engine.dispose()
    assert names <= after_upgrade
    assert not names & remaining


def test_the_migration_can_be_applied_again_after_a_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        config = alembic_config()
        command.upgrade(config, "head")
        command.downgrade(config, _parent())
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            tables = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
    assert "refresh_tokens" in tables


def test_head_has_no_model_drift_after_the_refresh_tokens_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch):
        config = alembic_config()
        command.upgrade(config, "head")
        command.check(config)
