"""Shapes of the Commander Spellbook client (S-5.2.1): its input, its result and the wire models.

Three groups live here:

* **Input**: :class:`DeckCard`, one line of a deck version.
* **Result**: :class:`CombosFound` or :class:`Unavailable` (:data:`SpellbookResult`). The client
  returns one of them and never raises. :class:`SpellbookCombo` carries what the bracket engine
  needs; Spellbook's words are passed through untranslated (feature names, statuses).
* **Wire models** (underscore-free ``Wire*`` classes): the part of Spellbook's response we read,
  checked by Pydantic. Unknown fields are ignored, so Spellbook may add fields freely. A field we
  rely on that is missing or has another type fails validation, which the client reports as
  ``schema_mismatch`` (the schema-drift guard, T-5.2.1.3).

Result tags: Spellbook has no ``infinite`` or ``win`` flag. A combo's result is the list of
features it ``produces`` (``produces[].feature.name``, for example "Infinite creature tokens with
haste"), plus its ``status``. Whether a feature name means "infinite" or "wins the game" is
therefore for the classifier (S-5.2.2) to decide; this module classifies nothing.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class DeckCard:
    """One card line of a deck version, as the caller knows it.

    ``is_commander`` is true for the commander(s); every other board, the companion included,
    is ``False`` (Spellbook has no companion field, so those cards go in its ``main`` list).
    ``oracle_id`` is ``None`` when the card has no Scryfall oracle id.
    """

    name: str
    oracle_id: UUID | None
    is_commander: bool = False
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class ComboCard:
    """A card a combo uses, as reported by Spellbook.

    ``must_be_commander`` is Spellbook's ``mustBeCommander``: the card has to be the commander
    for the combo to work (decision E7's "commander plus one other card").
    """

    name: str
    oracle_id: UUID | None
    quantity: int
    must_be_commander: bool


@dataclass(frozen=True, slots=True)
class ComboResult:
    """One result of a combo: a Spellbook feature it produces, untranslated.

    ``name`` is the feature's name ("Infinite ...", "Win the game", ...) and ``status`` its
    Spellbook status code (``S`` standalone, ``C`` contextual, ``H`` helper, ``PU``/``HU``
    utilities).
    """

    feature_id: int
    name: str
    status: str
    quantity: int


@dataclass(frozen=True, slots=True)
class SpellbookCombo:
    """A combo fully included in the deck (a Spellbook *variant*)."""

    spellbook_id: str
    status: str
    cards: tuple[ComboCard, ...]
    results: tuple[ComboResult, ...]


@dataclass(frozen=True, slots=True)
class CombosFound:
    """Spellbook answered (or the cache did): the combos fully included in the deck.

    An empty ``combos`` is a real answer: the deck has none.
    """

    combos: tuple[SpellbookCombo, ...]
    from_cache: bool = False


class UnavailableReason(StrEnum):
    """Why :class:`Unavailable` was returned. Stable values: callers may store or switch on them."""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    SCHEMA_MISMATCH = "schema_mismatch"
    TRANSPORT_ERROR = "transport_error"
    REDIRECT = "redirect"
    CLIENT_ERROR = "client_error"
    RESPONSE_TOO_LARGE = "response_too_large"
    TOO_MANY_PAGES = "too_many_pages"
    INVALID_REQUEST = "invalid_request"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class Unavailable:
    """The lookup could not be completed. ``detail`` is a short diagnostic with no card names."""

    reason: UnavailableReason
    detail: str = ""


SpellbookResult = CombosFound | Unavailable


class _Wire(BaseModel):
    """Base of the wire models: unknown fields are ignored, instances are immutable."""

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class WireCard(_Wire):
    """``results.included[].uses[].card``."""

    name: str = Field(min_length=1)
    oracle_id: UUID | None = Field(alias="oracleId")


class WireCardInVariant(_Wire):
    """``results.included[].uses[]``."""

    card: WireCard
    quantity: int = Field(ge=1)
    must_be_commander: bool = Field(alias="mustBeCommander")


class WireFeature(_Wire):
    """``results.included[].produces[].feature``."""

    id: int
    name: str = Field(min_length=1)
    status: str


class WireFeatureProduced(_Wire):
    """``results.included[].produces[]``."""

    feature: WireFeature
    quantity: int = Field(ge=1)


class WireVariant(_Wire):
    """One entry of a bucket: a combo as it applies to this deck."""

    id: str = Field(min_length=1, max_length=128)
    status: str
    uses: list[WireCardInVariant]
    produces: list[WireFeatureProduced]

    def to_combo(self) -> SpellbookCombo:
        """Convert to the client's result type."""
        return SpellbookCombo(
            spellbook_id=self.id,
            status=self.status,
            cards=tuple(
                ComboCard(
                    name=use.card.name,
                    oracle_id=use.card.oracle_id,
                    quantity=use.quantity,
                    must_be_commander=use.must_be_commander,
                )
                for use in self.uses
            ),
            results=tuple(
                ComboResult(
                    feature_id=made.feature.id,
                    name=made.feature.name,
                    status=made.feature.status,
                    quantity=made.quantity,
                )
                for made in self.produces
            ),
        )


class WireBuckets(_Wire):
    """``results``: only ``included`` is read (combos fully included in the deck)."""

    included: list[WireVariant]


class WirePage(_Wire):
    """One page of ``POST /find-my-combos``: ``next`` is a URL while more pages exist."""

    next: str | None
    results: WireBuckets
