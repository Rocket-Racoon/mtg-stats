"""Review rules for the state (CONTRACT.md 3.1 and 3.3), Python side.

The ``reverted`` key and the five flat seat elimination fields, with their null-ness invariant:
"a seat is eliminated if and only if ``eliminated_at_seq`` is not null; while in the game all five
fields are null". Every state in the shared vectors and the initial state are checked against the
contract tables and the invariant, and the Python models are checked too. The TypeScript twin is
``packages/game-core/src/state-shape.test.ts``.
"""

from __future__ import annotations

import json
import re
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from app.games.core import GameState, Seat, canonical_json, initial_state
from app.games.core.contract import EliminationReason, WinCondition
from app.games.tests.repo_files import VECTORS_DIR, contract_text, require

ELIMINATION_FIELDS = [
    "elimination_reason",
    "eliminated_by",
    "eliminated_at_seq",
    "eliminated_round",
    "elimination_cause",
]
# Vectors whose settings are unvalidated on purpose (3.5) cannot be loaded into the strict model.
UNVALIDATED = {
    "initial-state-out-of-range-settings",
    "initial-state-unvalidated-setting-types",
    "accept-settings-changed-keeps-unvalidated-initial-settings",
    "accept-seat-joined-with-unvalidated-starting-life",
    "accept-player-conceded-with-an-unvalidated-life",
}


def table_keys(contract: str, heading: str) -> list[str]:
    start = contract.index(f"\n{heading}\n")
    rest = contract[start + len(heading) + 2 :]
    end = re.search(r"\n#{1,3} ", rest)
    section = rest if end is None else rest[: end.start()]
    return re.findall(r"^\| `([a-z_]+)` *\|", section, flags=re.MULTILINE)


def seat_violations(seat: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if any(key not in seat for key in ELIMINATION_FIELDS):
        out.append("missing elimination field")
    eliminated = seat.get("eliminated_at_seq") is not None
    values = [seat.get(key) for key in ELIMINATION_FIELDS]
    if not eliminated and any(v is not None for v in values):
        out.append("in the game but an elimination field is set")
    if eliminated:
        if seat.get("elimination_reason") not in get_args(EliminationReason):
            out.append("bad reason")
        if type(seat.get("eliminated_round")) is not int:
            out.append("eliminated_round is not an integer")
        if type(seat.get("eliminated_at_seq")) is not int:
            out.append("eliminated_at_seq is not an integer")
    by = seat.get("eliminated_by")
    if by is not None and not (type(by) is int and 1 <= by <= 8):
        out.append("bad eliminated_by")
    cause = seat.get("elimination_cause")
    if cause is not None and cause not in get_args(WinCondition):
        out.append("bad cause")
    return out


def state_violations(state: dict[str, Any], top: list[str], seat_keys: list[str]) -> list[str]:
    out: list[str] = []
    if sorted(state) != sorted(top):
        out.append(f"keys differ from 3.1: {sorted(state)}")
    for key in ("voided", "reverted"):
        values = state.get(key)
        if not isinstance(values, list):
            out.append(f"{key} is not a list")
        elif any(
            type(n) is not int or (i > 0 and n <= values[i - 1]) for i, n in enumerate(values)
        ):
            out.append(f"{key} is not ascending without duplicates")
    for seat in state["seats"]:
        if sorted(seat) != sorted(seat_keys):
            out.append(f"seat {seat['seat']} keys differ from 3.3")
        out.extend(f"seat {seat['seat']}: {v}" for v in seat_violations(seat))
    return out


def collect_states() -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = [
        ("initial_state()", initial_state("g").to_json_value())
    ]
    if not VECTORS_DIR.is_dir():
        return found
    for path in sorted(VECTORS_DIR.glob("*.json")):
        vector = json.loads(path.read_text(encoding="utf-8"))
        if vector["kind"] == "reducer" and "state" in vector["expect"]:
            found.append((path.stem, vector["expect"]["state"]))
        elif path.stem == "canonical-json-game-state":
            found.extend((f"{path.stem}: {c['name']}", c["input"]) for c in vector["cases"])
    return found


STATES = collect_states()


def test_the_contract_tables_have_reverted_and_the_five_flat_fields() -> None:
    contract = contract_text()
    top = table_keys(contract, "### 3.1 Top level")
    seat = table_keys(contract, "### 3.3 Seat object")
    assert "reverted" in top
    assert top.index("voided") < top.index("reverted")
    assert top[-1] == "last_seq"
    assert seat[-5:] == ELIMINATION_FIELDS
    assert "elimination" not in seat


def test_the_models_have_the_same_keys_as_the_tables() -> None:
    contract = contract_text()
    assert list(GameState.model_fields) == table_keys(contract, "### 3.1 Top level")
    assert list(Seat.model_fields) == table_keys(contract, "### 3.3 Seat object")


def test_state_collection_found_the_initial_state_and_the_vector_states() -> None:
    require(VECTORS_DIR)
    assert len(STATES) > 18
    assert len([n for n, _ in STATES if n.startswith("canonical-json-game-state")]) == 3


@pytest.mark.parametrize(("name", "state"), STATES, ids=[n for n, _ in STATES])
def test_every_vector_state_follows_the_tables_and_the_invariant(
    name: str, state: dict[str, Any]
) -> None:
    contract = contract_text()
    top = table_keys(contract, "### 3.1 Top level")
    seat_keys = table_keys(contract, "### 3.3 Seat object")
    assert state_violations(state, top, seat_keys) == []
    assert "reverted" in state
    assert all("elimination" not in seat for seat in state["seats"])


@pytest.mark.parametrize(("name", "state"), STATES, ids=[n for n, _ in STATES])
def test_every_vector_state_loads_into_the_python_model_and_round_trips(
    name: str, state: dict[str, Any]
) -> None:
    if name in UNVALIDATED:
        pytest.skip("settings are unvalidated on purpose (3.5)")
    loaded = GameState.model_validate(state)
    assert canonical_json(loaded.to_json_value()) == canonical_json(state)


def test_the_vectors_contain_eliminated_and_in_game_seats() -> None:
    seats = [seat for _, state in STATES for seat in state["seats"]]
    assert any(seat["eliminated_at_seq"] is not None for seat in seats)
    assert any(seat["eliminated_at_seq"] is None for seat in seats)
    assert any(
        seat["eliminated_by"] is None and seat["eliminated_at_seq"] is not None for seat in seats
    )
    assert any(seat["elimination_cause"] is not None for seat in seats)


def test_the_invariant_checker_catches_a_broken_seat() -> None:
    in_game = dict.fromkeys(ELIMINATION_FIELDS)
    out = {
        "elimination_reason": "life",
        "eliminated_by": 1,
        "eliminated_at_seq": 5,
        "eliminated_round": 2,
        "elimination_cause": None,
    }
    assert seat_violations(in_game) == []
    assert seat_violations(out) == []
    assert seat_violations({**out, "eliminated_by": None, "elimination_cause": "combo"}) == []
    broken = [
        {**in_game, "elimination_reason": "life"},
        {**in_game, "eliminated_round": 1},
        {**in_game, "eliminated_by": 2},
        {**in_game, "elimination_cause": "combo"},
        {**out, "elimination_reason": None},
        {**out, "eliminated_round": None},
        {**out, "elimination_reason": "boredom"},
        {**out, "eliminated_by": 9},
        {**out, "elimination_cause": "draw"},
    ]
    for seat in broken:
        assert seat_violations(seat), seat


def test_the_game_state_vector_keeps_its_raw_characters() -> None:
    text = (VECTORS_DIR / "canonical-json-game-state.json").read_text(encoding="utf-8")
    assert "é (NFD)" in text
    assert chr(0x202E) in text
    assert chr(0x1F600) in text


# --- the seat model ----------------------------------------------------------------------------

SEAT: dict[str, Any] = {
    "seat": 1,
    "player_id": None,
    "name": "",
    "deck_version_id": None,
    "commanders": [{"card_id": None, "name": ""}],
    "life": 40,
    "poison": 0,
    "energy": 0,
    "experience": 0,
    "commander_cast": [0],
    **dict.fromkeys(ELIMINATION_FIELDS),
}


def test_the_seat_model_accepts_an_in_game_seat_and_an_eliminated_seat() -> None:
    assert Seat.model_validate(SEAT).eliminated_at_seq is None
    out = {
        **SEAT,
        "elimination_reason": "poison",
        "eliminated_by": None,
        "eliminated_at_seq": 9,
        "eliminated_round": 4,
        "elimination_cause": "combo",
    }
    assert Seat.model_validate(out).elimination_reason == "poison"


@pytest.mark.parametrize(
    "update",
    [
        {"elimination_reason": "boredom", "eliminated_at_seq": 1, "eliminated_round": 1},
        {"eliminated_by": 9},
        {"elimination_cause": "draw"},
        {"eliminated_round": -1, "eliminated_at_seq": 1, "elimination_reason": "life"},
        {"eliminated_at_seq": 1.5},
    ],
    ids=["reason", "by", "cause", "round", "float-seq"],
)
def test_the_seat_model_rejects_values_outside_the_contract_enumerations(
    update: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        Seat.model_validate({**SEAT, **update})


@pytest.mark.parametrize(
    "update",
    [
        {"elimination_reason": "life"},
        {"eliminated_round": 1},
        {"eliminated_by": 2},
        {"elimination_cause": "combo"},
        {"eliminated_at_seq": 3},
        {"eliminated_at_seq": 3, "eliminated_round": 1},
        {"eliminated_at_seq": 3, "elimination_reason": "life"},
    ],
    ids=["reason", "round", "by", "cause", "seq-only", "seq-round", "seq-reason"],
)
def test_the_seat_model_rejects_a_seat_that_breaks_the_elimination_invariant(
    update: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        Seat.model_validate({**SEAT, **update})
