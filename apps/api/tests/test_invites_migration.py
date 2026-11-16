"""The invites migration (S-2.1.2) on a throwaway database, never on ``mtg_stats*``."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.migration_support import alembic_config, temporary_database

PARENT_REVISION = "b3a91c7e5d42"
"""The refresh-tokens revision, the head before this story."""
USER_ID = "00000000-0000-7000-8000-000000000001"
INSERT_USER = text(
    "INSERT INTO users (id, username, email, password_hash, is_site_admin, is_active, "
    "created_at, password_changed_at) "
    "VALUES (:id, 'ada', '', 'x', false, true, now(), now())"
)
INSERT_INVITE = text(
    "INSERT INTO invites (id, token_hash, created_by, expires_at, max_uses, use_count, "
    "created_at) VALUES (gen_random_uuid(), :h, :uid, now(), :m, :u, now())"
)


def _invites_revision() -> str:
    """This story's revision: the one child of the refresh-tokens revision."""
    script = ScriptDirectory.from_config(alembic_config())
    children = [r for r in script.walk_revisions() if r.down_revision == PARENT_REVISION]
    if len(children) != 1:
        pytest.fail(f"expected one revision after {PARENT_REVISION}, found {len(children)}")
    return children[0].revision


def test_upgrade_creates_invites_with_the_documented_columns_and_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = _invites_revision()
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), revision)
        engine = create_engine(url)
        try:
            insp = inspect(engine)
            columns = {c["name"]: c for c in insp.get_columns("invites")}
            uniques = [u["column_names"] for u in insp.get_unique_constraints("invites")]
            checks = {c["name"] for c in insp.get_check_constraints("invites")}
            fks = {
                (tuple(fk["constrained_columns"]), fk["referred_table"]): fk["options"]
                for fk in insp.get_foreign_keys("invites")
            }
            with engine.connect() as conn:
                indexes = [
                    row[0]
                    for row in conn.execute(
                        text("SELECT indexdef FROM pg_indexes WHERE tablename = 'invites'")
                    )
                ]
        finally:
            engine.dispose()
    assert set(columns) == {
        "id",
        "token_hash",
        "created_by",
        "expires_at",
        "max_uses",
        "use_count",
        "revoked_at",
        "created_at",
    }
    assert columns["token_hash"]["type"].length == 64
    for name in set(columns) - {"revoked_at"}:
        assert columns[name]["nullable"] is False, name
    assert columns["revoked_at"]["nullable"] is True
    assert ["token_hash"] in uniques or any("UNIQUE" in d and "(token_hash)" in d for d in indexes)
    assert fks[(("created_by",), "users")].get("ondelete") == "CASCADE"
    composite = [d for d in indexes if "ix_invites_created_by_created_at_id" in d]
    assert len(composite) == 1
    assert "(created_by, created_at, id)" in composite[0]
    assert {"ck_invites_max_uses_range", "ck_invites_use_count_valid"} <= checks


def test_the_check_constraints_reject_bad_rows_and_the_unique_hash_rejects_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = _invites_revision()
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), revision)
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(INSERT_USER, {"id": USER_ID})

            def insert(token_hash: str, max_uses: int, use_count: int) -> None:
                with engine.begin() as conn:
                    conn.execute(
                        INSERT_INVITE,
                        {"h": token_hash, "m": max_uses, "u": use_count, "uid": USER_ID},
                    )

            insert("a" * 64, 1, 0)
            insert("b" * 64, 20, 20)
            for args in (
                ("c" * 64, 0, 0),  # max_uses below range
                ("d" * 64, 21, 0),  # max_uses above range
                ("e" * 64, 3, -1),  # negative use_count
                ("f" * 64, 3, 4),  # use_count above max_uses
                ("a" * 64, 1, 0),  # duplicate hash
            ):
                with pytest.raises(IntegrityError):
                    insert(*args)
        finally:
            engine.dispose()


def test_deleting_the_creator_cascades_to_their_invites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = _invites_revision()
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), revision)
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(INSERT_USER, {"id": USER_ID})
                conn.execute(INSERT_INVITE, {"h": "a" * 64, "m": 1, "u": 0, "uid": USER_ID})
                conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": USER_ID})
                remaining = conn.execute(text("SELECT count(*) FROM invites")).scalar_one()
        finally:
            engine.dispose()
    assert remaining == 0


def test_downgrade_removes_invites_and_keeps_the_earlier_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = _invites_revision()
    with temporary_database(monkeypatch) as url:
        config = alembic_config()
        command.upgrade(config, revision)
        command.downgrade(config, PARENT_REVISION)
        engine = create_engine(url)
        try:
            tables = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
    assert "invites" not in tables
    assert {"users", "players", "refresh_tokens"} <= tables


def test_the_migration_can_be_applied_again_after_a_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = _invites_revision()
    with temporary_database(monkeypatch) as url:
        config = alembic_config()
        command.upgrade(config, revision)
        command.downgrade(config, PARENT_REVISION)
        command.upgrade(config, revision)
        engine = create_engine(url)
        try:
            tables = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
    assert "invites" in tables


def test_head_has_no_model_drift_after_the_invites_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch):
        config = alembic_config()
        command.upgrade(config, "head")
        command.check(config)
