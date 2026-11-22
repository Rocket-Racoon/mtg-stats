"""CONTRACT.md 3.5 and 6.1, Python side: ``initial_state(game_id, settings=None)``.

The TypeScript twin is ``packages/game-core/src/initial-state.test.ts``. The same inputs are in the
shared ``initial-state-*`` vectors, which both runners execute and compare as canonical text. The
class of the thrown error is language-specific (``TypeError`` or ``ValueError`` here), so the
throwing cases are paired tests, not vectors.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.games.core import GameState, Settings, canonical_json, initial_state
from app.games.tests.repo_files import VECTORS_DIR, load_vector, require

HIGH = chr(0xD800)
LOW = chr(0xDC00)
DEFAULTS = {
    "starting_life": 40,
    "commander_damage_threshold": 21,
    "poison_threshold": 10,
    "trust_mode": "casual",
}


def settings_of(state: GameState) -> dict[str, Any]:
    return state.settings.model_dump(warnings=False)


def test_defaults_fill_in_when_settings_are_omitted_none_or_empty() -> None:
    assert settings_of(initial_state("g")) == DEFAULTS
    assert settings_of(initial_state("g", None)) == DEFAULTS
    assert settings_of(initial_state("g", {})) == DEFAULTS


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ({"starting_life": 20}, {**DEFAULTS, "starting_life": 20}),
        ({"trust_mode": "strict"}, {**DEFAULTS, "trust_mode": "strict"}),
        (
            {"poison_threshold": 1, "commander_damage_threshold": 99},
            {**DEFAULTS, "poison_threshold": 1, "commander_damage_threshold": 99},
        ),
    ],
    ids=["life", "trust", "two-numbers"],
)
def test_missing_keys_take_their_defaults(given: dict[str, Any], expected: dict[str, Any]) -> None:
    assert settings_of(initial_state("g", given)) == expected


def test_out_of_range_and_wrongly_typed_values_are_stored_unchanged() -> None:
    out_of_range = {
        "starting_life": 5000,
        "commander_damage_threshold": 0,
        "poison_threshold": 100,
        "trust_mode": "banana",
    }
    assert settings_of(initial_state("g", out_of_range)) == out_of_range
    odd = {
        "starting_life": "lots",
        "commander_damage_threshold": None,
        "poison_threshold": -5,
        "trust_mode": 7,
    }
    assert settings_of(initial_state("g", odd)) == odd
    assert canonical_json(initial_state("g", odd).to_json_value())  # still serializes


def test_a_settings_model_instance_is_accepted_and_copied() -> None:
    model = Settings(
        starting_life=20, commander_damage_threshold=15, poison_threshold=7, trust_mode="strict"
    )
    assert settings_of(initial_state("g", model)) == model.model_dump()


def test_the_given_mapping_is_copied_not_aliased() -> None:
    given = {"starting_life": 20}
    state = initial_state("g", given)
    given["starting_life"] = 99
    assert settings_of(state)["starting_life"] == 20


def test_the_order_of_setting_keys_does_not_matter() -> None:
    a = canonical_json(
        initial_state("g", {"starting_life": 1, "trust_mode": "strict"}).to_json_value()
    )
    b = canonical_json(
        initial_state("g", {"trust_mode": "strict", "starting_life": 1}).to_json_value()
    )
    assert a == b


@pytest.mark.parametrize(
    "game_id",
    ["", "g" + HIGH, LOW + "g", LOW + HIGH, HIGH + LOW, 5, None, b"g", {}],
    ids=[
        "empty",
        "lone-high",
        "lone-low",
        "reversed",
        "pair-as-two-code-points",
        "int",
        "none",
        "bytes",
        "dict",
    ],
)
def test_a_bad_game_id_raises_instead_of_returning_a_state(game_id: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        initial_state(game_id)


def test_a_game_id_with_an_astral_character_or_any_non_empty_text_is_accepted() -> None:
    assert initial_state("g-" + chr(0x1F600)).game_id == "g-" + chr(0x1F600)
    assert initial_state(" ").game_id == " "
    assert initial_state("\x00").game_id == "\x00"


@pytest.mark.parametrize(
    "settings",
    [
        {"starting_lives": 20},
        {"starting_life": 20, "extra": 1},
        {"Starting_Life": 20},
        {"constructor": 1},
        {"__proto__": 1},
        {1: 2},
    ],
    ids=["typo", "extra", "case", "constructor", "proto", "int-key"],
)
def test_an_unknown_setting_key_raises(settings: dict[Any, Any]) -> None:
    with pytest.raises((TypeError, ValueError)):
        initial_state("g", settings)


@pytest.mark.parametrize("settings", ["casual", [], 3, [("starting_life", 1)]], ids=repr)
def test_settings_that_are_not_a_mapping_raise(settings: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        initial_state("g", settings)


# --- parity with the shared vectors -----------------------------------------------------------


def _initial_state_vectors() -> list[str]:
    if not VECTORS_DIR.is_dir():
        return []
    return sorted(p.stem for p in VECTORS_DIR.glob("initial-state*.json"))


def test_the_initial_state_vectors_are_present() -> None:
    names = set(_initial_state_vectors())
    if not names:
        require(VECTORS_DIR)
    assert {
        "initial-state",
        "initial-state-default-settings",
        "initial-state-out-of-range-settings",
        "initial-state-partial-settings",
        "initial-state-partial-settings-trust-mode-only",
        "initial-state-empty-settings",
        "initial-state-unvalidated-setting-types",
        "initial-state-astral-game-id",
    } <= names


@pytest.mark.parametrize("name", _initial_state_vectors())
def test_initial_state_gives_exactly_the_expected_canonical_state(name: str) -> None:
    vector = load_vector(name)
    state = initial_state(vector["game_id"], vector["settings"])
    assert canonical_json(state.to_json_value()) == canonical_json(vector["expect"]["state"])


@pytest.mark.parametrize("name", _initial_state_vectors())
def test_the_expected_state_of_an_initial_state_vector_is_the_contract_empty_lobby(
    name: str,
) -> None:
    state = load_vector(name)["expect"]["state"]
    assert state["phase"] == "lobby"
    assert state["seats"] == []
    assert state["order"] == []
    assert state["voided"] == []
    assert state["reverted"] == []
    assert state["last_seq"] == 0
    assert state["v"] == 1
    assert json.dumps(sorted(state)) == json.dumps(sorted(initial_state("g").to_json_value()))
