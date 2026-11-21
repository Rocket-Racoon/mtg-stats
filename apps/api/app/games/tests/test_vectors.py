"""Run every shared vector in ``packages/game-core/vectors/`` (CONTRACT.md section 8)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.games.core import (
    ApplyError,
    CanonicalJsonError,
    GameState,
    Settings,
    apply,
    canonical_json,
    initial_state,
)

# apps/api/app/games/tests/test_vectors.py -> repository root
VECTORS_DIR = Path(__file__).resolve().parents[5] / "packages" / "game-core" / "vectors"


def _vector_files() -> list[Path]:
    if not VECTORS_DIR.is_dir():
        # The API container holds only apps/api; CI checks out the whole repository.
        if os.environ.get("CI", "").strip().lower() not in {"", "0", "false", "no", "off"}:
            pytest.fail(f"vectors directory missing on CI: {VECTORS_DIR}", pytrace=False)
        return []
    return sorted(VECTORS_DIR.glob("*.json"))


FILES = _vector_files()


def _load(path: Path) -> dict[str, Any]:
    # Vector files are test data, not wire data: events reach apply as already-parsed values, so
    # a payload integer above 2^53 - 1 stays an int and apply must answer invalid_payload.
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _build_invalid(kind: str, value: str | None) -> object:
    builders: dict[str, Callable[[], object]] = {
        "float": lambda: float(value or ""),
        "nan": lambda: float("nan"),
        "infinity": lambda: float("inf"),
        "integer": lambda: int(value or ""),
        "lone_surrogate": lambda: chr(int(value or "", 16)),
    }
    return builders[kind]()


def _chain(kind: str, count: int) -> object:
    """``count`` nested containers (arrays, or objects keyed ``"a"``), empty at the bottom."""
    node: Any = [] if kind == "deep_array" else {}
    for _ in range(count - 1):
        node = [node] if kind == "deep_array" else {"a": node}
    return node


def _run_canonical(vector: dict[str, Any]) -> None:
    for case in vector["cases"]:
        if "input" in case:
            assert canonical_json(case["input"]) == case["output"], case["name"]
            assert canonical_json(case["input"]).encode("utf-8")  # encodable
        else:
            invalid = case["invalid"]
            kind = invalid["kind"]
            if kind in {"deep_array", "deep_object"}:
                value = _chain(kind, int(invalid["value"]))  # not wrapped in [value]
            else:
                value = [_build_invalid(kind, invalid.get("value"))]
            with pytest.raises(CanonicalJsonError) as raised:
                canonical_json(value)
            assert raised.value.code == case["error"], case["name"]


def _final_state(state: GameState) -> str:
    return canonical_json(state.to_json_value())


def _run_reducer(vector: dict[str, Any]) -> None:
    state = initial_state(vector["game_id"], Settings.model_validate(vector["settings"]))
    expect = vector["expect"]
    assert len(expect) == 1
    for index, raw in enumerate(vector["events"]):
        event = {"game_id": vector["game_id"], **raw}
        result = apply(state, event)
        if "error" in expect and index == expect["error"]["index"]:
            assert isinstance(result, ApplyError), f"event {index} should be rejected"
            assert result.error.code == expect["error"]["code"]
            return
        assert not isinstance(result, ApplyError), f"event {index}: {result}"
        state = result.state
    assert "state" in expect, "an error vector must reject one of its events"
    assert _final_state(state) == canonical_json(expect["state"])


def test_vectors_directory_holds_the_seed_vectors() -> None:
    if not FILES:
        pytest.skip("packages/game-core/vectors is not available in this layout")
    names = {path.stem for path in FILES}
    assert {
        "canonical-json",
        "initial-state",
        "reject-unknown-event-type",
        "reject-unsupported-version",
        "reject-unsupported-version-and-type",
    } <= names


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_python_implementation_passes_vector(path: Path) -> None:
    vector = _load(path)
    assert vector["format"] == 1
    assert vector["name"] == path.stem
    if vector["kind"] == "canonical_json":
        _run_canonical(vector)
    else:
        assert vector["kind"] == "reducer"
        _run_reducer(vector)
