"""Acceptance criterion 1 (S-6.1.1), Python side: CONTRACT.md, the TypeScript package and the
Python models cannot drift.

The contract defines the envelope (``event_id``, ``game_id``, ``seq``, ``type``, ``payload``,
``actor``, ``on_behalf_of_seat``, ``ts``, ``v``), the state with the four settings, and the
reducer error codes. The markdown tables are parsed here and compared with the Python constants
and models. ``packages/game-core/src/contract.test.ts`` and ``contract-criteria.test.ts`` do the
same for the TypeScript arrays.
"""

from __future__ import annotations

import re
from typing import get_args

import pytest

from app.games.core import (
    EVENT_TYPES,
    REDUCER_ERROR_CODES,
    SCHEMA_VERSION,
    Actor,
    Commander,
    Elimination,
    EventEnvelope,
    GameState,
    Seat,
    Settings,
    initial_state,
)
from app.games.core.canonical import MAX_DEPTH, MAX_SAFE_INTEGER
from app.games.core.contract import (
    DEFAULT_SETTINGS,
    EVENT_ID_PATTERN,
    SERVER_ONLY_REJECT_CODES,
    SETTING_RANGES,
    SUPPORTED_VERSIONS,
    TRUST_MODES,
    WIN_CONDITION_CODES,
    ControlMode,
    EliminationReason,
    Phase,
    ResumePhase,
)
from app.games.tests.repo_files import contract_text

ENVELOPE_FIELDS = [
    "event_id",
    "game_id",
    "seq",
    "type",
    "payload",
    "actor",
    "on_behalf_of_seat",
    "ts",
    "v",
]


@pytest.fixture(scope="module")
def contract() -> str:
    return contract_text()


def section(contract: str, heading: str) -> str:
    start = contract.find(f"\n{heading}\n")
    assert start >= 0, f"CONTRACT.md has no heading {heading}"
    rest = contract[start + len(heading) + 2 :]
    end = re.search(r"\n#{1,3} ", rest)
    return rest if end is None else rest[: end.start()]


def rows(contract: str, heading: str) -> dict[str, str]:
    """Table rows of a section keyed by the first-cell code span; value is the rest of the row."""
    found = re.findall(r"^\| `([a-z_]+)` *\|(.*)$", section(contract, heading), flags=re.MULTILINE)
    return dict(found)


def code_spans(text: str) -> list[str]:
    return re.findall(r"`([a-z_]+)`", text)


# --- the criterion's list is in the contract ----------------------------------------------------


def test_the_contract_defines_exactly_the_envelope_fields_of_the_criterion(contract: str) -> None:
    assert list(rows(contract, "## 2. Event envelope")) == ENVELOPE_FIELDS


def test_the_contract_describes_each_envelope_field_as_the_criterion_does(contract: str) -> None:
    envelope = rows(contract, "## 2. Event envelope")
    assert "UUID v4" in envelope["event_id"]
    assert "client" in envelope["event_id"]
    assert "Server-assigned" in envelope["seq"]
    assert "optional (absent)" in envelope["seq"]
    assert "integer or null" in envelope["on_behalf_of_seat"]
    assert "Always present" in envelope["on_behalf_of_seat"]
    assert "Server UTC" in envelope["ts"]
    assert "optional (absent)" in envelope["ts"]
    assert "Schema version" in envelope["v"]


def test_the_contract_defines_the_four_settings_and_both_trust_modes(contract: str) -> None:
    settings = rows(contract, "### 3.2 `settings`")
    assert list(settings) == [
        "starting_life",
        "commander_damage_threshold",
        "poison_threshold",
        "trust_mode",
    ]
    assert "`casual`, `strict`" in settings["trust_mode"]
    assert "settings" in rows(contract, "### 3.1 Top level")


def test_the_contract_lists_every_reducer_error_code_including_the_criterion_four_codes(
    contract: str,
) -> None:
    codes = rows(contract, "### 6.2 Reducer error codes")
    assert "unsupported_version" in codes
    assert "unknown_event_type" in codes
    assert list(codes) == list(REDUCER_ERROR_CODES)


# --- Python constants and models equal the document -------------------------------------------


def test_event_types_equal_the_registry_table_in_order(contract: str) -> None:
    assert list(rows(contract, "## 4. Event registry")) == list(EVENT_TYPES)


def test_reducer_error_codes_equal_the_table_in_order(contract: str) -> None:
    assert list(rows(contract, "### 6.2 Reducer error codes")) == list(REDUCER_ERROR_CODES)


def test_server_only_reject_codes_equal_the_table_and_are_not_reducer_codes(contract: str) -> None:
    listed = list(rows(contract, "### 6.3 Server-only reject codes"))
    assert listed == list(SERVER_ONLY_REJECT_CODES)
    assert not set(listed) & set(REDUCER_ERROR_CODES)


def test_envelope_model_fields_equal_the_envelope_table_in_order(contract: str) -> None:
    assert list(EventEnvelope.model_fields) == list(rows(contract, "## 2. Event envelope"))


def test_envelope_required_keys_are_the_ones_the_contract_names(contract: str) -> None:
    text = section(contract, "## 2. Event envelope")
    match = re.search(r"The required keys are (.+?)\. ", text)
    assert match
    named = code_spans(match.group(1))
    required = [name for name, field in EventEnvelope.model_fields.items() if field.is_required()]
    assert sorted(named) == sorted(required)
    for optional in ("seq", "ts"):
        assert not EventEnvelope.model_fields[optional].is_required()
        assert EventEnvelope.model_fields[optional].default is None


def test_actor_model_fields_are_kind_and_player_id(contract: str) -> None:
    assert list(Actor.model_fields) == ["kind", "player_id"]
    assert get_args(Actor.model_fields["kind"].annotation) == ("player", "system")
    row = rows(contract, "## 2. Event envelope")["actor"]
    assert '{"kind": "player", "player_id"' in row
    assert '{"kind": "system", "player_id": null}' in row


def test_state_model_fields_equal_the_top_level_table(contract: str) -> None:
    assert list(GameState.model_fields) == list(rows(contract, "### 3.1 Top level"))
    assert all(field.is_required() for field in GameState.model_fields.values())


def test_settings_model_fields_ranges_and_defaults_equal_the_settings_table(contract: str) -> None:
    settings = rows(contract, "### 3.2 `settings`")
    assert list(Settings.model_fields) == list(settings)
    assert list(DEFAULT_SETTINGS) == list(settings)
    for key, (low, high) in SETTING_RANGES.items():
        assert f"{low} to {high}" in settings[key]
    for key, default in DEFAULT_SETTINGS.items():
        cells = [cell.strip(" `") for cell in settings[key].split("|")]
        assert str(default) == cells[-2], key
    assert list(TRUST_MODES) == ["casual", "strict"]


def test_seat_commander_and_elimination_fields_equal_the_contract(contract: str) -> None:
    assert list(Seat.model_fields) == list(rows(contract, "### 3.3 Seat object"))
    text = section(contract, "### 3.3 Seat object")
    assert list(Elimination.model_fields) == ["reason", "by", "at_seq", "cause"]
    assert '`{"reason", "by", "at_seq", "cause"}`' in text
    assert list(Commander.model_fields) == ["card_id", "name"]
    assert '`{"card_id": string or null, "name": string}`' in text


def test_enumerations_equal_the_contract(contract: str) -> None:
    top = rows(contract, "### 3.1 Top level")
    assert code_spans(top["phase"]) == list(get_args(Phase))
    assert code_spans(top["resume_phase"].split("(")[1].split(")")[0]) == list(
        get_args(ResumePhase)
    )
    assert code_spans(top["control_mode"]) == list(get_args(ControlMode))
    seat_text = section(contract, "### 3.3 Seat object")
    reasons = re.search(r"`elimination.reason` is (.+?)\. `by`", seat_text)
    assert reasons
    assert code_spans(reasons.group(1)) == list(get_args(EliminationReason))
    d5 = section(contract, "### 3.4 D5 win-condition codes").strip().split("\n")[0]
    assert code_spans(d5) == list(WIN_CONDITION_CODES)


def test_constants_the_contract_states(contract: str) -> None:
    assert SCHEMA_VERSION == 1
    assert frozenset({1}) == SUPPORTED_VERSIONS
    assert "`v` = 1" in contract
    assert MAX_SAFE_INTEGER == 9007199254740991
    assert "9007199254740991" in contract
    assert MAX_DEPTH == 64
    assert "**64 or fewer** ancestors" in contract
    assert EVENT_ID_PATTERN in contract
    assert "They must produce byte-identical canonical JSON" in contract


def test_every_state_key_of_the_initial_state_is_in_the_contract(contract: str) -> None:
    state = initial_state("g", Settings(**DEFAULT_SETTINGS))  # type: ignore[arg-type]
    assert sorted(state.to_json_value()) == sorted(rows(contract, "### 3.1 Top level"))
    assert sorted(state.to_json_value()["settings"]) == sorted(rows(contract, "### 3.2 `settings`"))
