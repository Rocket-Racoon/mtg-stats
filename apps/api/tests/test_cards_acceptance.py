"""Acceptance tests for S-3.1.1 (card and printing schema).

Criteria 1, 2 and 5 are checked against a database built by running the Alembic migration
(``temporary_database``), never against the ``create_all`` schema the other tests use. The tests
that touch it are synchronous because ``env.py`` runs its own event loop with ``asyncio.run``.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.cards import services
from app.cards.models import Card, Printing
from app.core.db import AppSession, Base
from tests.architecture.test_domain_imports import _tree, check_imports
from tests.factories import (
    AdventureCardFactory,
    ArtSeriesCardFactory,
    CardFactory,
    DoubleFacedCardFactory,
    DoubleFacedPrintingFactory,
    EmblemCardFactory,
    OversizedCardFactory,
    PrintingFactory,
    SplitCardFactory,
    TokenCardFactory,
    create,
)
from tests.migration_support import alembic_config, temporary_database

CARDS_REVISION = "40e9501d90da"
APP_ROOT = Path(__file__).resolve().parents[1] / "app"

TIMESTAMPTZ = "timestamp with time zone"
# column -> information_schema data_type
CARD_COLUMNS = {
    "oracle_id": "uuid",
    "name": "character varying",
    "search_name": "text",
    "mana_cost": "character varying",
    "cmc": "numeric",
    "type_line": "character varying",
    "oracle_text": "text",
    "colors": "ARRAY",
    "color_identity": "ARRAY",
    "keywords": "ARRAY",
    "layout": "character varying",
    "card_faces": "jsonb",
    "legalities": "jsonb",
    "commander_legality": "character varying",
    "game_changer": "boolean",
    "oversized": "boolean",
    "is_extra": "boolean",
    "default_printing_id": "uuid",
    "reserved": "boolean",
    "missing_since": TIMESTAMPTZ,
    "updated_at": TIMESTAMPTZ,
}
PRINTING_COLUMNS = {
    "id": "uuid",
    "oracle_id": "uuid",
    "set_code": "character varying",
    "set_name": "character varying",
    "collector_number": "character varying",
    "rarity": "character varying",
    "lang": "character varying",
    "released_at": "date",
    "finishes": "ARRAY",
    "image_uris": "jsonb",
    "digital": "boolean",
    "oversized": "boolean",
    "updated_at": TIMESTAMPTZ,
}
# Project convention: string columns are never nullable.
NON_NULL_STRINGS = {
    "cards": [
        "name",
        "search_name",
        "mana_cost",
        "type_line",
        "oracle_text",
        "layout",
        "commander_legality",
    ],
    "printings": ["set_code", "set_name", "collector_number", "rarity", "lang"],
}

_INSERT_CARD = text(
    "INSERT INTO cards (oracle_id, name, layout, oversized, legalities, updated_at) "
    "VALUES (:id, :name, :layout, :oversized, CAST(:legalities AS jsonb), now()) "
    "RETURNING search_name, is_extra, commander_legality, game_changer"
)
_INSERT_PRINTING = text(
    "INSERT INTO printings (id, oracle_id, set_code, set_name, collector_number, rarity, lang, "
    "updated_at) VALUES (:id, :card, :set_code, 'Set', :number, 'common', :lang, now())"
)


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[Engine]:
    """An engine on a throwaway database that Alembic upgraded to head."""
    with pytest.MonkeyPatch.context() as mp, temporary_database(mp) as url:
        command.upgrade(alembic_config(), "head")
        engine = create_engine(url)
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture
def conn(migrated_engine: Engine) -> Iterator[Connection]:
    """A connection whose transaction is always rolled back, so tests never see each other."""
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def _columns(connection: Connection, table: str) -> dict[str, tuple[str, str]]:
    rows = connection.execute(
        text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    )
    return {name: (data_type, nullable) for name, data_type, nullable in rows}


def _indexdefs(connection: Connection, table: str) -> list[str]:
    rows = connection.execute(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = :t"),
        {"t": table},
    )
    return [row[0] for row in rows]


def _card(
    connection: Connection,
    name: str = "Some Card",
    layout: str = "normal",
    oversized: bool = False,
    legalities: dict[str, str] | None = None,
) -> tuple[UUID, tuple[str, bool, str, bool]]:
    """Insert a card with plain SQL; return its id and (search_name, is_extra, commander, gc)."""
    card_id = uuid4()
    row = connection.execute(
        _INSERT_CARD,
        {
            "id": card_id,
            "name": name,
            "layout": layout,
            "oversized": oversized,
            "legalities": json.dumps(legalities or {}),
        },
    ).one()
    return card_id, (row[0], row[1], row[2], row[3])


# ---------------------------------------------------------------- criterion 1: cards table


def test_migration_creates_cards_with_every_listed_column_and_type(conn: Connection) -> None:
    columns = _columns(conn, "cards")
    assert {name: kind for name, (kind, _) in columns.items()} == CARD_COLUMNS


def test_migration_cards_primary_key_is_oracle_id(conn: Connection) -> None:
    keys = conn.execute(
        text(
            "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
            "ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey) "
            "WHERE i.indrelid = 'public.cards'::regclass AND i.indisprimary"
        )
    )
    assert [row[0] for row in keys] == ["oracle_id"]


def test_migration_cards_string_columns_are_not_nullable(conn: Connection) -> None:
    columns = _columns(conn, "cards")
    assert [c for c in NON_NULL_STRINGS["cards"] if columns[c][1] != "NO"] == []


def test_migration_cards_array_columns_hold_text_elements(conn: Connection) -> None:
    rows = conn.execute(
        text(
            "SELECT column_name, udt_name FROM information_schema.columns "
            "WHERE table_name = 'cards' AND column_name IN ('colors','color_identity','keywords')"
        )
    )
    assert dict(rows.all()) == {
        "colors": "_varchar",
        "color_identity": "_varchar",
        "keywords": "_varchar",
    }


def test_migration_indexes_commander_legality(conn: Connection) -> None:
    assert any("(commander_legality)" in d for d in _indexdefs(conn, "cards"))


def test_migration_game_changer_is_false_when_the_insert_omits_it(conn: Connection) -> None:
    _, (_, _, _, game_changer) = _card(conn)
    assert game_changer is False


def test_migration_defaults_for_reserved_and_missing_since(conn: Connection) -> None:
    card_id, _ = _card(conn)
    row = conn.execute(
        text("SELECT reserved, oversized, missing_since FROM cards WHERE oracle_id = :i"),
        {"i": card_id},
    ).one()
    assert tuple(row) == (False, False, None)


# ---------------------------------------------------------------- criterion 2: printings table


def test_migration_creates_printings_with_every_listed_column_and_type(conn: Connection) -> None:
    columns = _columns(conn, "printings")
    assert {name: kind for name, (kind, _) in columns.items()} == PRINTING_COLUMNS


def test_migration_printings_primary_key_is_id(conn: Connection) -> None:
    keys = conn.execute(
        text(
            "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
            "ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey) "
            "WHERE i.indrelid = 'public.printings'::regclass AND i.indisprimary"
        )
    )
    assert [row[0] for row in keys] == ["id"]


def test_migration_printings_string_columns_are_not_nullable(conn: Connection) -> None:
    columns = _columns(conn, "printings")
    assert [c for c in NON_NULL_STRINGS["printings"] if columns[c][1] != "NO"] == []


def test_migration_printings_oracle_id_is_a_foreign_key_to_cards(conn: Connection) -> None:
    rows = conn.execute(
        text(
            "SELECT a.attname, c2.relname, a2.attname FROM pg_constraint c "
            "JOIN pg_class c2 ON c2.oid = c.confrelid "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
            "JOIN pg_attribute a2 ON a2.attrelid = c.confrelid AND a2.attnum = c.confkey[1] "
            "WHERE c.conrelid = 'public.printings'::regclass AND c.contype = 'f'"
        )
    ).all()
    assert [tuple(r) for r in rows] == [("oracle_id", "cards", "oracle_id")]


def test_migration_unique_index_on_set_collector_number_and_lang(conn: Connection) -> None:
    defs = _indexdefs(conn, "printings")
    assert any(
        d.startswith("CREATE UNIQUE INDEX") and "(set_code, collector_number, lang)" in d
        for d in defs
    )


def test_migration_rejects_a_duplicate_set_collector_number_and_lang(conn: Connection) -> None:
    card_id, _ = _card(conn)
    base = {"card": card_id, "set_code": "dup", "number": "9", "lang": "en"}
    conn.execute(_INSERT_PRINTING, {**base, "id": uuid4()})
    with pytest.raises(IntegrityError):
        conn.execute(_INSERT_PRINTING, {**base, "id": uuid4()})


def test_migration_allows_the_same_set_and_number_in_another_language(conn: Connection) -> None:
    card_id, _ = _card(conn)
    base = {"card": card_id, "set_code": "dup", "number": "9"}
    conn.execute(_INSERT_PRINTING, {**base, "id": uuid4(), "lang": "en"})
    conn.execute(_INSERT_PRINTING, {**base, "id": uuid4(), "lang": "ja"})


def test_migration_rejects_a_printing_whose_card_does_not_exist(conn: Connection) -> None:
    with pytest.raises(IntegrityError):
        conn.execute(
            _INSERT_PRINTING,
            {"id": uuid4(), "card": uuid4(), "set_code": "x", "number": "1", "lang": "en"},
        )


# ---------------------------------------------------------------- criterion 3: is_extra


@pytest.mark.parametrize(
    ("layout", "oversized", "expected"),
    [
        ("art_series", False, True),
        ("normal", True, True),
        ("token", True, True),
        ("normal", False, False),
        ("transform", False, False),
        ("split", False, False),
        ("adventure", False, False),
        ("token", False, False),
        ("emblem", False, False),
    ],
)
def test_migration_is_extra_for_layout_and_oversized(
    conn: Connection, layout: str, oversized: bool, expected: bool
) -> None:
    _, (_, is_extra, _, _) = _card(conn, layout=layout, oversized=oversized)
    assert is_extra is expected


@pytest.mark.parametrize(
    ("factory_class", "expected"),
    [
        (CardFactory, False),
        (DoubleFacedCardFactory, False),
        (SplitCardFactory, False),
        (AdventureCardFactory, False),
        (TokenCardFactory, False),
        (EmblemCardFactory, False),
        (ArtSeriesCardFactory, True),
        (OversizedCardFactory, True),
    ],
)
async def test_is_extra_per_factory_layout_after_a_refresh(
    app_schema: AppSession, factory_class: type[CardFactory], expected: bool
) -> None:
    card = await create(app_schema, factory_class)
    await app_schema.refresh(card)
    assert card.is_extra is expected


async def test_an_oversized_card_of_another_layout_is_extra(app_schema: AppSession) -> None:
    card = await create(app_schema, TokenCardFactory, oversized=True)
    await app_schema.refresh(card)
    assert (card.layout, card.is_extra) == ("token", True)


async def test_is_extra_follows_an_update_of_layout(app_schema: AppSession) -> None:
    card = await create(app_schema, CardFactory)
    card.layout = "art_series"
    await app_schema.flush()
    await app_schema.refresh(card)
    assert card.is_extra is True


# ---------------------------------------------------------------- criterion 4: legalities


SCRYFALL_LEGALITIES = {
    "commander": "legal",
    "modern": "not_legal",
    "legacy": "banned",
    "vintage": "restricted",
}


async def test_legalities_round_trip_exactly_as_scryfall_sends_them(
    app_schema: AppSession,
) -> None:
    card = await create(app_schema, CardFactory, legalities=dict(SCRYFALL_LEGALITIES))
    await app_schema.refresh(card)
    assert card.legalities == SCRYFALL_LEGALITIES
    assert sorted(card.legalities.values()) == ["banned", "legal", "not_legal", "restricted"]
    stored = (
        await app_schema.execute(select(Card.legalities).where(Card.oracle_id == card.oracle_id))
    ).scalar_one()
    assert stored == SCRYFALL_LEGALITIES


@pytest.mark.parametrize("value", ["legal", "not_legal", "banned", "restricted"])
def test_migration_commander_legality_reflects_the_commander_entry_untranslated(
    conn: Connection, value: str
) -> None:
    _, (_, _, commander_legality, _) = _card(
        conn, legalities={"commander": value, "modern": "legal"}
    )
    assert commander_legality == value


def test_migration_commander_legality_is_empty_without_a_commander_entry(
    conn: Connection,
) -> None:
    _, (_, _, commander_legality, _) = _card(conn, legalities={"modern": "banned"})
    assert commander_legality == ""


def test_migration_stored_legalities_values_are_not_translated(conn: Connection) -> None:
    card_id, _ = _card(conn, legalities=SCRYFALL_LEGALITIES)
    rows = conn.execute(
        text("SELECT key, value FROM cards, jsonb_each_text(legalities) WHERE oracle_id = :i"),
        {"i": card_id},
    )
    assert dict(rows.all()) == SCRYFALL_LEGALITIES


# ------------------------------------------------- task T-3.1.1.2: search support and name folding


def test_migration_enables_pg_trgm_and_unaccent(conn: Connection) -> None:
    rows = conn.execute(text("SELECT extname FROM pg_extension"))
    assert {"pg_trgm", "unaccent"} <= {row[0] for row in rows}


def test_migration_has_an_immutable_unaccent_wrapper(conn: Connection) -> None:
    row = conn.execute(
        text(
            "SELECT p.provolatile FROM pg_proc p WHERE p.proname = 'immutable_unaccent' "
            "AND p.pronamespace = 'public'::regnamespace"
        )
    ).one()
    assert row[0] == "i"
    assert conn.execute(text("SELECT immutable_unaccent('Jötun')")).scalar_one() == "Jotun"


def test_migration_has_a_trigram_index_on_the_normalized_name(conn: Connection) -> None:
    defs = _indexdefs(conn, "cards")
    assert any("USING gin" in d and "search_name" in d and "gin_trgm_ops" in d for d in defs)


def test_normalized_name_is_lowercase_with_accents_removed(conn: Connection) -> None:
    _, (jotun, _, _, _) = _card(conn, name="Jötun Grunt")
    assert jotun == "jotun grunt"


@pytest.mark.parametrize(
    ("name", "letters"),
    [
        ("Lim-Dûl's Vault", "limdulsvault"),
        ("Fire // Ice", "fireice"),
        ("Urza's Saga", "urzassaga"),
        ("Jötun Grunt", "jotungrunt"),
    ],
)
def test_normalized_name_keeps_only_folded_letters_and_single_spaces(
    conn: Connection, name: str, letters: str
) -> None:
    """The wording ("punctuation folded") fixes the letters, not whether a hyphen or an
    apostrophe becomes a space or vanishes, so only what it does fix is asserted."""
    _, (folded, _, _, _) = _card(conn, name=name)
    assert folded == folded.lower()
    assert folded.replace(" ", "") == letters
    assert all(ch.isascii() and (ch.isalnum() or ch == " ") for ch in folded)
    assert folded == " ".join(folded.split())


def test_normalized_name_of_a_split_card_keeps_both_halves_in_order(conn: Connection) -> None:
    _, (folded, _, _, _) = _card(conn, name="Fire // Ice")
    assert folded.split() == ["fire", "ice"]


def test_normalized_name_treats_straight_and_curly_apostrophes_alike(conn: Connection) -> None:
    _, (straight, _, _, _) = _card(conn, name="Urza's Saga")
    _, (curly, _, _, _) = _card(conn, name=f"Urza{chr(0x2019)}s Saga")
    assert straight == curly


# ---------------------------------------------------------------- criterion 5: round trip


def _tables(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        return {row[0] for row in rows}


def _parent_of_cards_revision() -> str:
    revision = ScriptDirectory.from_config(alembic_config()).get_revision(CARDS_REVISION)
    assert revision is not None
    assert isinstance(revision.down_revision, str)
    return revision.down_revision


def test_cards_migration_upgrades_downgrades_and_upgrades_again_without_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _parent_of_cards_revision()
    with temporary_database(monkeypatch) as url:
        config = alembic_config()
        engine = create_engine(url)
        try:
            command.upgrade(config, CARDS_REVISION)
            assert {"cards", "printings"} <= _tables(engine)

            command.downgrade(config, parent)
            remaining = _tables(engine)
            assert "cards" not in remaining
            assert "printings" not in remaining

            command.upgrade(config, "head")
            assert {"cards", "printings"} <= _tables(engine)
            command.check(config)  # raises when the models and the migrations drift
        finally:
            engine.dispose()


def test_downgrading_the_cards_migration_removes_the_wrapper_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _parent_of_cards_revision()
    with temporary_database(monkeypatch) as url:
        config = alembic_config()
        command.upgrade(config, CARDS_REVISION)
        command.downgrade(config, parent)
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                count = connection.execute(
                    text("SELECT count(*) FROM pg_proc WHERE proname = 'immutable_unaccent'")
                ).scalar_one()
        finally:
            engine.dispose()
    assert count == 0


# ---------------------------------------------------------------- criterion 6: public interface


def _violations_for(root: Path, extra: dict[str, str]) -> list[tuple[str, str]]:
    files = {
        "cards/models.py": "",
        "cards/repository.py": "",
        "cards/services/__init__.py": "",
        "cards/services/queries.py": "",
        "decks/services/__init__.py": "",
        "identity/services/__init__.py": "",
        "workflows/__init__.py": "",
        **extra,
    }
    return [(v.path.name, v.imported) for v in check_imports(_tree(root, files))]


@pytest.mark.parametrize(
    ("owner", "body", "imported"),
    [
        ("decks/services/a.py", "from app.cards.models import Card\n", "app.cards.models"),
        ("decks/services/a.py", "import app.cards.repository\n", "app.cards.repository"),
        ("decks/services/a.py", "from app.cards import models\n", "app.cards.models"),
        (
            "decks/services/a.py",
            "from app.cards.services import queries\n",
            "app.cards.services.queries",
        ),
        ("workflows/a.py", "from app.cards.models import Card\n", "app.cards.models"),
        ("identity/services/a.py", "from app.cards.models import Card\n", "app.cards.models"),
    ],
)
def test_other_domains_cannot_import_cards_internals(
    tmp_path: Path, owner: str, body: str, imported: str
) -> None:
    assert (Path(owner).name, imported) in _violations_for(tmp_path, {owner: body})


def test_other_domains_may_import_the_cards_public_services(tmp_path: Path) -> None:
    extra = {
        "decks/services/a.py": "from app.cards.services import get_card\n",
        "workflows/a.py": "from app.cards.services import find_cards_by_names\n",
    }
    assert _violations_for(tmp_path, extra) == []


def test_no_module_outside_cards_imports_cards_models_or_repository() -> None:
    offenders = [
        str(v)
        for v in check_imports(APP_ROOT)
        if v.imported.startswith(("app.cards.models", "app.cards.repository"))
    ]
    assert offenders == []


def test_cards_services_export_the_read_functions_and_no_orm_model() -> None:
    exported = {name: getattr(services, name) for name in services.__all__}
    assert set(exported) >= {"get_card", "get_printing", "list_printings", "find_cards_by_names"}
    assert not [n for n, obj in exported.items() if isinstance(obj, type) and issubclass(obj, Base)]


async def test_service_functions_return_plain_data_not_orm_rows(app_schema: AppSession) -> None:
    card = await create(app_schema, DoubleFacedCardFactory, name="Delver of Secrets")
    printing = await create(app_schema, DoubleFacedPrintingFactory, oracle_id=card.oracle_id)
    await create(app_schema, PrintingFactory, oracle_id=card.oracle_id, set_code="oth")

    results: list[object] = [
        await services.get_card(app_schema, card.oracle_id),
        await services.get_printing(app_schema, printing.id),
        *await services.list_printings(app_schema, card.oracle_id),
        *await services.find_cards_by_names(app_schema, ["delver of secrets"]),
    ]
    assert len(results) == 5
    for result in results:
        assert result is not None
        assert not isinstance(result, (Base, Card, Printing))
        assert not hasattr(result, "_sa_instance_state")
