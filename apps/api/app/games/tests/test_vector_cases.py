"""Run the shared validator cases of ``packages/game-core/vector-cases/`` (S-6.1.2, CONTRACT.md 8).

The TypeScript suite (``src/vector-cases.test.ts``) executes the same files, so the two validators
cannot drift apart unnoticed. The format is in ``vector-cases/README.md``. Expected messages come
from the contract's tables (8.3, 8.3.1, 8.4), never from a validator. Unlike the other repository
file helpers, a missing directory fails everywhere (CONTRACT.md 8.5: fail loudly).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.games.tests.repo_files import GAME_CORE
from app.games.tests.vector_validator import (
    uncovered_event_type_messages,
    validate_vector_text,
)

CASES_DIR = GAME_CORE / "vector-cases"

Message = str | dict[str, str]


def _load_cases() -> list[dict[str, Any]]:
    if not CASES_DIR.is_dir():
        pytest.fail(f"vector-cases directory not found: {CASES_DIR}", pytrace=False)
    paths = sorted(CASES_DIR.glob("*.json"))
    if not paths:
        pytest.fail(f"vector-cases directory has no .json files: {CASES_DIR}", pytrace=False)
    loaded: list[dict[str, Any]] = []
    for path in paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["format"] == 1, f"{path.name}: format must be 1"
        assert doc["name"] == path.stem, f"{path.name}: name must equal the file stem"
        loaded.append(doc)
    return loaded


CASES = _load_cases()
VALIDATOR_CASES = [c for c in CASES if c["kind"] == "validator"]
COVERAGE_CASES = [c for c in CASES if c["kind"] == "coverage"]


def _agrees(actual: list[str], expected: list[Message]) -> bool:
    if len(actual) != len(expected):
        return False
    for got, want in zip(actual, expected, strict=True):
        if isinstance(want, str):
            if got != want:
                return False
        elif not got.startswith(want["startsWith"]):
            return False
    return True


def _display(expected: list[Message]) -> list[str]:
    return [m if isinstance(m, str) else f"{m['startsWith']}<detail>" for m in expected]


# Each message template of CONTRACT.md 8.3 / 8.3.1 appears in at least one case's expectation, so no
# rule of the contract goes unpinned. Same list as the TypeScript suite.
TEMPLATES = [
    "invalid JSON: ",
    "$ must be an object",
    "kind is required",
    'kind must be "reducer" or "canonical_json"',
    "format is required",
    "format must be the integer 1",
    "name is required",
    'name must equal "',
    "description is required",
    "description must be a non-empty string",
    "game_id is required",
    "game_id must be a non-empty string",
    "settings is required",
    "settings must be an object",
    "settings.zeta is not a known key",
    "events is required",
    "events must be an array",
    "events[0] must be an object",
    "expect is required",
    "expect must be an object",
    "expect.extra is not a known key",
    'expect must have exactly one of "state" or "error"',
    "expect.state must be an object",
    "expect.error must be an object",
    "expect.error.z is not a known key",
    "expect.error.index is required",
    "expect.error.index must be a non-negative integer",
    "expect.error.index must be less than the number of events (",
    "expect.error.code is required",
    "expect.error.code must be a known reducer error code",
    "events[1].type must be a string",
    " is not a known key",
    "has the number token ",
    "write an integral value as an integer",
    "must be an integer (found the number token ",
    "must be an integer in the safe range (found ",
    " is a duplicate key",
    "cases is required",
    "cases must be a non-empty array",
    "cases[0] must be an object",
    "cases[0].name is required",
    "cases[0].name must be a non-empty string",
    "cases[2].name duplicates the name of cases[0]",
    'must have exactly one of "input" or "invalid"',
    "cases[0].output is required",
    "cases[0].output must be a string",
    "cases[0].error is required",
    "cases[0].error must be a canonical JSON error code",
    "cases[0].invalid must be an object",
    "cases[0].invalid.z is not a known key",
    "cases[0].invalid.kind is required",
    "cases[0].invalid.kind must be one of float, nan, infinity, integer, lone_surrogate, "
    "deep_array, deep_object",
    "cases[0].invalid.value must be absent",
    "cases[0].invalid.value is required",
    "must be decimal text of a non-integer number",
    "must be decimal text of an integer outside the safe range",
    "must be the 4-digit hex code of a surrogate code unit (d800 to dfff)",
    "must be decimal text of an integer from 1 to 10000",
]


def test_corpus_holds_both_kinds_of_case_named_after_their_files() -> None:
    assert len(VALIDATOR_CASES) >= 300
    assert len(COVERAGE_CASES) >= 20
    assert len({c["name"] for c in CASES}) == len(CASES)
    for c in VALIDATOR_CASES:
        assert c["file"].endswith(".json"), c["name"]
        assert ("messages" in c) != ("alternatives" in c), f"{c['name']}: messages xor gap"
        if "alternatives" in c:
            assert c.get("gap"), c["name"]
            assert len(c["alternatives"]) >= 2, c["name"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_some_case_pins_the_message(template: str) -> None:
    every: list[str] = []
    for c in VALIDATOR_CASES:
        entries = list(c.get("messages", [])) + [
            m for alt in c.get("alternatives", []) for m in alt
        ]
        every.extend(m if isinstance(m, str) else m["startsWith"] for m in entries)
    assert any(template in m for m in every)


@pytest.mark.parametrize("case", VALIDATOR_CASES, ids=[c["name"] for c in VALIDATOR_CASES])
def test_validator_agrees_with_the_shared_case(case: dict[str, Any]) -> None:
    actual = list(validate_vector_text(case["file"], case["text"]).messages)
    if "messages" in case:
        assert _agrees(actual, case["messages"]), (
            f"{case['name']}: expected {_display(case['messages'])}, got {actual}"
        )
        return
    readings = case["alternatives"]
    assert any(_agrees(actual, alt) for alt in readings), (
        f"{case['name']}: contract gap ({case['gap']}); got {actual}, "
        f"defensible readings {[_display(r) for r in readings]}"
    )


@pytest.mark.parametrize("case", COVERAGE_CASES, ids=[c["name"] for c in COVERAGE_CASES])
def test_coverage_rule_agrees_with_the_shared_case(case: dict[str, Any]) -> None:
    vectors: list[dict[str, Any]] = []
    for f in case["files"]:
        result = validate_vector_text(f["file"], f["text"])
        if result.ok and result.value is not None and result.value.get("kind") == "reducer":
            vectors.append(result.value)
    assert uncovered_event_type_messages(vectors, case["registry"]) == case["messages"]
