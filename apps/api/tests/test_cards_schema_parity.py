"""Parity between the migrated cards schema and the ``create_all`` schema (S-3.1.1 review).

``alembic check`` ignores generation expressions, server defaults and function bodies, and the
pytest schema is built by ``create_search_support`` + ``create_all``. This test builds both in
throwaway databases and compares what Alembic does not, so changing a model expression without a
migration (or the reverse) fails here. Synchronous because ``env.py`` runs its own event loop.
"""

from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Connection, create_engine, text

from app.cards.models import create_search_support
from app.core.db import Base
from tests.migration_support import alembic_config, temporary_database

TABLES = ("cards", "printings")
COLUMNS_SQL = """
SELECT table_name, column_name, data_type, udt_name, character_maximum_length,
       numeric_precision, numeric_scale, is_nullable, column_default, is_generated,
       generation_expression
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = ANY(:tables)
"""
INDEXES_SQL = "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = ANY(:t)"
CONSTRAINTS_SQL = """
SELECT conrelid::regclass::text, conname, contype, pg_get_constraintdef(oid)
FROM pg_constraint WHERE conrelid = ANY(ARRAY['cards'::regclass, 'printings'::regclass])
"""
FUNCTION_SQL = """
SELECT pg_get_functiondef(p.oid), p.provolatile, p.proisstrict, p.proparallel
FROM pg_proc p WHERE p.proname = 'immutable_unaccent' AND p.pronamespace = 'public'::regnamespace
"""


def snapshot(connection: Connection) -> dict[str, Any]:
    """Everything Alembic does not compare, for the cards and printings tables."""
    params = {"tables": list(TABLES), "t": list(TABLES)}
    return {
        "columns": sorted(tuple(r) for r in connection.execute(text(COLUMNS_SQL), params)),
        "indexes": sorted(r[0] for r in connection.execute(text(INDEXES_SQL), params)),
        "constraints": sorted(tuple(r) for r in connection.execute(text(CONSTRAINTS_SQL))),
        "function": [tuple(r) for r in connection.execute(text(FUNCTION_SQL))],
    }


def _migrated_snapshot(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                return snapshot(connection)
        finally:
            engine.dispose()


def _model_snapshot(monkeypatch: pytest.MonkeyPatch, *, tamper: bool = False) -> dict[str, Any]:
    import app.model_registry  # noqa: F401, PLC0415

    with temporary_database(monkeypatch) as url:
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                create_search_support(connection)
                Base.metadata.create_all(connection)
                if tamper:
                    connection.execute(text("ALTER TABLE cards DROP COLUMN search_name CASCADE"))
                    connection.execute(
                        text(
                            "ALTER TABLE cards ADD COLUMN search_name text "
                            "GENERATED ALWAYS AS (lower(name)) STORED NOT NULL"
                        )
                    )
            with engine.connect() as connection:
                return snapshot(connection)
        finally:
            engine.dispose()


def test_migrated_and_create_all_cards_schemas_are_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrated = _migrated_snapshot(monkeypatch)
    from_models = _model_snapshot(monkeypatch)
    assert migrated["function"], "the migration must create public.immutable_unaccent"
    expressions = [str(row[-1]) for row in migrated["columns"] if row[1] == "search_name"]
    assert expressions
    assert all("immutable_unaccent" in e for e in expressions)
    for key in ("columns", "indexes", "constraints", "function"):
        assert migrated[key] == from_models[key], key


def test_parity_check_detects_a_changed_generation_expression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrated = _migrated_snapshot(monkeypatch)
    tampered = _model_snapshot(monkeypatch, tamper=True)
    assert migrated["columns"] != tampered["columns"]


def test_cmc_stores_the_largest_scryfall_value_and_fractions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gleemax has cmc 1000000.0 on Scryfall; half mana costs exist too."""
    with temporary_database(monkeypatch) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO cards (oracle_id, name, cmc, updated_at) VALUES "
                        "(gen_random_uuid(), 'Gleemax', 1000000, now()), "
                        "(gen_random_uuid(), 'Little Girl', 0.5, now())"
                    )
                )
                rows = connection.execute(text("SELECT name, cmc FROM cards ORDER BY cmc")).all()
        finally:
            engine.dispose()
    assert rows == [("Little Girl", Decimal("0.50")), ("Gleemax", Decimal("1000000.00"))]
