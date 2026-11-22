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
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


@dataclass(frozen=True, slots=True)
class DeckCard:
    """One card line of a deck version, as the caller knows it.

    ``is_commander`` is true for the commander(s); every other board, the companion included,
    is ``False`` (Spellbook has no companion field, so those cards go in its ``main`` list).
    ``oracle_id`` is ``None`` when the card has no Scryfall oracle id.

    ``name`` is the Scryfall oracle name; for a multi-face card pass the full ``Front // Back``
    name (a probe on 2026-11-21 showed Spellbook matches that form and not the front name alone).
    Spellbook silently ignores names it does not recognise, so a wrong form is not an error: the
    card is simply left out and the answer, possibly "no combos", would be cached for 7 days.
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
class ComboTemplate:
    """A template a combo needs on top of its named cards ("Any creature", "Any sac outlet").

    Any card that fits the template will do, so the combo needs ``quantity`` more cards than
    ``SpellbookCombo.cards`` lists. ``must_be_commander`` is Spellbook's ``mustBeCommander``.
    """

    template_id: int
    name: str
    quantity: int
    must_be_commander: bool


@dataclass(frozen=True, slots=True)
class SpellbookCombo:
    """A combo fully included in the deck (a Spellbook *variant*).

    ``cards`` are the named cards it uses and ``templates`` the generic cards it also requires;
    the number of cards a combo needs counts both.
    """

    spellbook_id: str
    status: str
    cards: tuple[ComboCard, ...]
    templates: tuple[ComboTemplate, ...]
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


class WireTemplate(_Wire):
    """``results.included[].requires[].template``: ``scryfallQuery`` is not read."""

    id: int
    name: str = Field(min_length=1)


class WireTemplateInVariant(_Wire):
    """``results.included[].requires[]``: ``mustBeCommander`` is required, as in the schema."""

    template: WireTemplate
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
    requires: list[WireTemplateInVariant]
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
            templates=tuple(
                ComboTemplate(
                    template_id=need.template.id,
                    name=need.template.name,
                    quantity=need.quantity,
                    must_be_commander=need.must_be_commander,
                )
                for need in self.requires
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
    """``results``: only ``included`` is read (combos fully included in the deck).

    The other buckets are only counted (for the page-size log), never validated: ``other`` holds
    them as they came.
    """

    included: list[WireVariant]
    other: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _keep_other_buckets(cls, data: Any) -> Any:
        if isinstance(data, dict):
            others = {k: v for k, v in data.items() if k != "included"}
            return {**data, "other": others}
        return data

    def total_items(self) -> int:
        """Variants on this page across every bucket (the page size the server applied)."""
        return len(self.included) + sum(len(v) for v in self.other.values() if isinstance(v, list))


class WirePage(_Wire):
    """One page of ``POST /find-my-combos``: ``next`` is a URL while more pages exist."""

    next: str | None
    results: WireBuckets
