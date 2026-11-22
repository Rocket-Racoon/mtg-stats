"""The contract rules added in review (S-6.1.1), checked in the document so the document, the TS
package and the Python package cannot drift. Twin of ``contract-review.test.ts``: versioning (1.1),
the state keys (3.1, 3.3), ``initial_state`` (3.5), "specified but not yet enforced" (4.3), reverts
(6.5) and the wire reader (5.6).
"""

from __future__ import annotations

import re

import pytest

from app.games.core import (
    ApplyError,
    ApplyOk,
    apply,
    initial_state,
)
from app.games.core.contract import DEFAULT_SETTINGS, SUPPORTED_VERSIONS
from app.games.tests.repo_files import contract_text

GAME_ID = "018f0000-0000-7000-8000-000000000001"


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
    return dict(re.findall(r"^\| `([a-z_]+)` *\|(.*)$", section(contract, heading), flags=re.M))


def test_reverted_is_defined_as_ascending_seqs_empty_initially(contract: str) -> None:
    row = rows(contract, "### 3.1 Top level")["reverted"]
    assert "array of integers" in row
    assert "Ascending, no duplicates" in row
    assert "`[]` initially" in row
    assert "6.5" in row
    assert initial_state(GAME_ID).reverted == []


def test_the_five_flat_elimination_fields_have_the_documented_types(contract: str) -> None:
    seat = rows(contract, "### 3.3 Seat object")
    assert list(seat)[-5:] == [
        "elimination_reason",
        "eliminated_by",
        "eliminated_at_seq",
        "eliminated_round",
        "elimination_cause",
    ]
    assert "string or null" in seat["elimination_reason"]
    for key in ("eliminated_by", "eliminated_at_seq", "eliminated_round"):
        assert "integer or null" in seat[key]
    assert "string or null" in seat["elimination_cause"]
    assert "D6" in seat["eliminated_at_seq"]


def test_the_invariant_is_stated(contract: str) -> None:
    text = section(contract, "### 3.3 Seat object")
    assert "A seat is eliminated if and only if `eliminated_at_seq` is not `null`" in text
    assert "While the seat is in the game all five elimination fields are `null`" in text
    assert "`elimination_reason`, `eliminated_at_seq` and `eliminated_round` are non-null" in text
    assert "`eliminated_by` and `eliminated_at_seq` are the names" in text


def test_initial_state_rules_are_stated(contract: str) -> None:
    text = section(contract, "### 3.5 Initial state")
    for phrase in (
        "non-empty string without a lone surrogate",
        "**throws**",
        "never returns a reducer error code",
        "optional and may be partial",
        "A missing key takes its default",
        "not one of the four setting names",
        "neither types nor ranges are validated",
        "**placeholder**",
        "`reverted` `[]`",
    ):
        assert phrase in text, phrase


def test_versioning_rules_are_stated(contract: str) -> None:
    text = section(contract, "### 1.1 Versioning")
    for phrase in (
        "**envelope**",
        "**registered payload shapes**",
        "**state shape**",
        "keeps accepting **every** version still present in a stored log",
        "`SUPPORTED_VERSIONS`",
    ):
        assert phrase in text, phrase
    assert 1 in SUPPORTED_VERSIONS
    bumps = {
        m[0].strip(): m[1] for m in re.findall(r"^\| (.+?) +\| (Yes|No) +\|", text, flags=re.M)
    }
    assert bumps["Adding an event type"] == "No"
    assert bumps["Adding a state key"] == "No"
    assert bumps["Adding an error code"] == "No"
    assert bumps["Adding an optional payload key"] == "No"
    assert bumps["Changing the envelope keys or their rules, or the canonical JSON rules"] == "Yes"
    assert (
        bumps["Removing, renaming or retyping a state key, or changing what an existing key means"]
        == "Yes"
    )


def test_not_yet_enforced_rules_are_stated_and_the_rest_really_not_enforced(contract: str) -> None:
    text = section(contract, "### 4.3 Specified but not yet enforced")
    for phrase in (
        "valid **only as the event at `seq` 1**",
        "`invalid_phase`",
        "**overwrites**",
        "Message size",
    ):
        assert phrase in text, phrase

    def make(event_type: str, payload: dict[str, object], **extra: object) -> dict[str, object]:
        return {
            "event_id": "00000000-0000-4000-8000-000000000001",
            "game_id": GAME_ID,
            "type": event_type,
            "payload": payload,
            "actor": {"kind": "system", "player_id": None},
            "on_behalf_of_seat": None,
            "v": 1,
            **extra,
        }

    state = initial_state(GAME_ID)
    settings = {**DEFAULT_SETTINGS, "starting_life": 30}
    first = apply(state, make("game_created", {"settings": settings}))
    assert isinstance(first, ApplyOk)
    assert first.state.settings.model_dump() == settings
    again = apply(first.state, make("game_created", {"settings": settings}, seq=2))
    assert isinstance(again, ApplyError)  # S-6.1.6 enforces seq 1 only (4.4.2)
    assert again.error.code == "invalid_phase"
    bad = apply(state, make("game_created", {"settings": {**settings, "starting_life": 0}}))
    assert isinstance(bad, ApplyError)
    assert bad.error.code == "invalid_setting"
    assert initial_state(GAME_ID, {"starting_life": 0}).settings.starting_life == 0
    assert isinstance(apply(state, make("settings_changed", {})), ApplyOk)
    reverted = apply(state, make("event_reverted", {"target_seq": 1}))
    assert isinstance(reverted, ApplyOk)
    assert reverted.state.reverted == []
    assert reverted.state.voided == []


def test_reverts_and_the_wire_reader_are_described(contract: str) -> None:
    reverts = section(contract, "### 6.5 Reverts are resolved by the fold over the log")
    for phrase in (
        "replay",
        "`not_revertible`",
        "`already_reverted`",
        "is **effective** unless a later effective `event_reverted` targets it",
        "`reverted` array",
        "`voided`",
    ):
        assert phrase in reverts, phrase
    wire = section(contract, "### 5.6 Reading JSON into the model (wire parsing)")
    assert "lone surrogate escape" in wire
    assert "`invalid_event`" in wire
    assert "`JSON.parse` accepts such an escape" in wire
    envelope = section(contract, "## 2. Event envelope")
    assert "**Well-formed strings.**" in envelope
    for field in ("`event_id`", "`game_id`", "`type`", "`ts`", "`actor.player_id`"):
        assert field in envelope
    assert "`unknown_event_type`, not `invalid_event`" in envelope
