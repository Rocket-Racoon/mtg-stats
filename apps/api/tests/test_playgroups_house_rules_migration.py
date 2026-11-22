"""The playgroups house rules migration (S-2.5.4) on a throwaway database."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DataError, IntegrityError

from tests.migration_support import alembic_config, temporary_database

REVISION = "0c0eb7757b9f"
GROUP_ID = "00000000-0000-7000-8000-0000000000aa"
INSERT_GROUP = text(
    "INSERT INTO playgroups (id, name, description, created_at, updated_at) "
    "VALUES (:g, 'Friday', '', now(), now())"
)


def _parent() -> str:
    parent = ScriptDirectory.from_config(alembic_config()).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def test_existing_playgroups_get_empty_rules_and_the_columns_have_the_expected_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), _parent())
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(INSERT_GROUP, {"g": GROUP_ID})
            command.upgrade(alembic_config(), REVISION)
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT house_rules_text, house_rules_edited_by, house_rules_edited_at "
                        "FROM playgroups"
                    )
                ).one()
            assert tuple(row) == ("", None, None)
            columns = {c["name"]: c for c in inspect(engine).get_columns("playgroups")}
            assert columns["house_rules_text"]["nullable"] is False
            assert "''" in str(columns["house_rules_text"]["default"])
            assert columns["house_rules_edited_by"]["nullable"] is True
            assert columns["house_rules_edited_at"]["nullable"] is True
            assert columns["house_rules_edited_at"]["type"].timezone is True
        finally:
            engine.dispose()


def test_the_database_rejects_5001_characters_and_accepts_5000_astral_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(INSERT_GROUP, {"g": GROUP_ID})
                conn.execute(
                    text("UPDATE playgroups SET house_rules_text = :t"), {"t": "\U0001f600" * 5000}
                )
            # The varchar(5000) column rejects it first (DataError); the check constraint is the
            # second line of defence and must exist under its conventional name.
            with pytest.raises((DataError, IntegrityError)), engine.begin() as conn:
                conn.execute(text("UPDATE playgroups SET house_rules_text = :t"), {"t": "x" * 5001})
            with engine.connect() as conn:
                checks = (
                    conn.execute(
                        text(
                            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                            "WHERE conname = 'ck_playgroups_house_rules_length'"
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(checks) == 1
            assert "char_length" in checks[0]
            assert "5000" in checks[0]
            with engine.connect() as conn:
                length = conn.execute(
                    text("SELECT char_length(house_rules_text) FROM playgroups")
                ).scalar_one()
            assert length == 5000
        finally:
            engine.dispose()


def test_a_null_house_rules_text_is_rejected_by_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(INSERT_GROUP, {"g": GROUP_ID})
            with pytest.raises(IntegrityError), engine.begin() as conn:
                conn.execute(text("UPDATE playgroups SET house_rules_text = NULL"))
        finally:
            engine.dispose()


def test_downgrade_removes_the_columns_keeps_the_playgroups_and_the_models_have_no_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        command.check(alembic_config())  # raises on drift
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                conn.execute(INSERT_GROUP, {"g": GROUP_ID})
            command.downgrade(alembic_config(), _parent())
            names = {c["name"] for c in inspect(engine).get_columns("playgroups")}
            assert (
                not {"house_rules_text", "house_rules_edited_by", "house_rules_edited_at"} & names
            )
            with engine.connect() as conn:
                assert conn.execute(text("SELECT count(*) FROM playgroups")).scalar_one() == 1
            command.upgrade(alembic_config(), "head")
            command.check(alembic_config())
        finally:
            engine.dispose()
