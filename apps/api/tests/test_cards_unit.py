"""Unit tests of the cards models, factories and public read functions."""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.cards import services
from app.cards.models import Card, Printing
from app.cards.repository import fold_name
from app.core.db import AppSession
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
async def test_is_extra_only_for_art_series_and_oversized(
    app_schema: AppSession, factory_class: type[CardFactory], expected: bool
) -> None:
    card = await create(app_schema, factory_class)
    await app_schema.refresh(card)
    assert card.is_extra is expected


async def test_search_name_folds_case_accents_and_punctuation(app_schema: AppSession) -> None:
    card = await create(app_schema, CardFactory, name="Æther Vial's \u2013 Lim-Dûl, the Fox")
    await app_schema.refresh(card)
    assert card.search_name == "aether vials lim dul the fox"
    folded = (await app_schema.execute(select(fold_name(card.name)))).scalar_one()
    assert folded == card.search_name


async def test_commander_legality_is_kept_exactly_as_scryfall_sends_it(
    app_schema: AppSession,
) -> None:
    legalities = {"commander": "banned", "modern": "restricted"}
    card = await create(app_schema, CardFactory, legalities=legalities)
    missing = await create(app_schema, TokenCardFactory)
    await app_schema.refresh(card)
    await app_schema.refresh(missing)
    assert card.commander_legality == "banned"
    assert card.legalities == legalities
    assert missing.commander_legality == ""


async def test_game_changer_defaults_to_false(app_schema: AppSession) -> None:
    await app_schema.execute(
        text("INSERT INTO cards (oracle_id, updated_at) VALUES (:id, now())"), {"id": uuid4()}
    )
    assert (await app_schema.execute(select(Card.game_changer))).scalar_one() is False


async def test_printing_requires_its_card(app_schema: AppSession) -> None:
    with pytest.raises(IntegrityError):
        await create(app_schema, PrintingFactory)


async def test_printing_set_collector_number_and_lang_is_unique(app_schema: AppSession) -> None:
    card = await create(app_schema, CardFactory)
    await create(app_schema, PrintingFactory, oracle_id=card.oracle_id, collector_number="7")
    async with app_schema.begin_nested():
        await create(
            app_schema, PrintingFactory, oracle_id=card.oracle_id, collector_number="7", lang="ja"
        )
    with pytest.raises(IntegrityError):
        await create(app_schema, PrintingFactory, oracle_id=card.oracle_id, collector_number="7")


async def test_services_return_cards_and_printings_without_models(
    app_schema: AppSession,
) -> None:
    card = await create(app_schema, DoubleFacedCardFactory, name="Delver of Secrets")
    old = await create(
        app_schema,
        DoubleFacedPrintingFactory,
        oracle_id=card.oracle_id,
        released_at=date(2011, 1, 1),
    )
    new = await create(
        app_schema, PrintingFactory, oracle_id=card.oracle_id, released_at=date(2020, 1, 1)
    )

    info = await services.get_card(app_schema, card.oracle_id)
    assert info is not None
    assert info.name == "Delver of Secrets"
    assert info.commander_legality == "legal"
    assert len(info.card_faces) == 2
    assert not isinstance(info, Card)
    assert await services.get_card(app_schema, uuid4()) is None

    printing = await services.get_printing(app_schema, old.id)
    assert printing is not None
    assert len(printing.image_uris["faces"]) == 2
    assert not isinstance(printing, Printing)
    assert await services.get_printing(app_schema, uuid4()) is None

    listed = await services.list_printings(app_schema, card.oracle_id)
    assert [p.id for p in listed] == [new.id, old.id]


async def test_find_cards_by_names_ignores_case_accents_and_punctuation(
    app_schema: AppSession,
) -> None:
    await create(app_schema, CardFactory, name="Jötun Grunt")
    await create(app_schema, CardFactory, name="Urza's Tower")
    await create(app_schema, CardFactory, name="Other")

    found = await services.find_cards_by_names(app_schema, ["jotun  grunt", "URZAS TOWER"])
    assert [c.name for c in found] == ["Jötun Grunt", "Urza's Tower"]
    assert await services.find_cards_by_names(app_schema, []) == []


async def test_find_cards_by_names_never_matches_an_empty_folded_name(
    app_schema: AppSession,
) -> None:
    underscores = await create(app_schema, CardFactory, name="_____")
    await app_schema.refresh(underscores)
    assert underscores.search_name == ""

    assert await services.find_cards_by_names(app_schema, ["!!"]) == []
    assert await services.find_cards_by_names(app_schema, ["_____"]) == []
