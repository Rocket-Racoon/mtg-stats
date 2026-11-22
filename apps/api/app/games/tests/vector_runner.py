"""Load and run shared vector files (CONTRACT.md section 8.5).

File access and the per-file runner live here, outside ``app/games/core`` (which must stay free of
I/O) and apart from the pure rules in ``vector_validator``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.games.core import (
    ApplyError,
    CanonicalJsonError,
    GameState,
    apply,
    canonical_json,
    initial_state,
)
from app.games.tests.vector_validator import (
    ValidationResult,
    strip_huge_integers,
    validate_vector_text,
)


class VectorError(Exception):
    """A vector file failed: its validation, or a comparison. The text starts with the file."""


def list_vector_files(directory: Path) -> list[Path]:
    """Every ``*.json`` directly in ``directory``, sorted by name.

    Raises:
        VectorError: ``vectors directory not found: <dir>`` or
            ``vectors directory has no .json files: <dir>``.
    """
    if not directory.is_dir():
        raise VectorError(f"vectors directory not found: {directory}")
    files = sorted(p for p in directory.glob("*.json") if p.is_file())
    if not files:
        raise VectorError(f"vectors directory has no .json files: {directory}")
    return files


def validate_file(path: Path) -> ValidationResult:
    """Read ``path`` as UTF-8 and validate it (section 8.3)."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        return ValidationResult((f"{path.name}: invalid JSON: {error}",), None)
    return validate_vector_text(path.name, text)


def valid_reducer_vectors(files: list[Path]) -> list[dict[str, Any]]:
    """The parsed reducer vectors among ``files`` that pass validation (they feed coverage)."""
    found: list[dict[str, Any]] = []
    for path in files:
        result = validate_file(path)
        if result.ok and result.value is not None and result.value["kind"] == "reducer":
            found.append(result.value)
    return found


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


def _run_canonical(file: str, vector: dict[str, Any]) -> None:
    for case in vector["cases"]:
        name = case["name"]
        prefix = f'{file}: case "{name}": '
        if "input" in case:
            try:
                actual = canonical_json(case["input"])
            except CanonicalJsonError as error:
                raise VectorError(f"{prefix}expected output but got error {error.code}") from None
            if actual != case["output"]:
                raise VectorError(f"{prefix}expected {case['output']!r}, got {actual!r}")
            continue
        invalid = case["invalid"]
        kind = invalid["kind"]
        if kind in {"deep_array", "deep_object"}:
            value = _chain(kind, int(invalid["value"]))  # not wrapped in [value]
        else:
            value = [_build_invalid(kind, invalid.get("value"))]
        try:
            canonical_json(value)
        except CanonicalJsonError as error:
            if error.code != case["error"]:
                raise VectorError(
                    f"{prefix}expected error {case['error']}, got error {error.code}"
                ) from None
        else:
            raise VectorError(f"{prefix}expected error {case['error']}, got no error")


def _final_state(state: GameState) -> str:
    return canonical_json(state.to_json_value())


def _run_reducer(file: str, vector: dict[str, Any]) -> None:
    state = initial_state(vector["game_id"], vector["settings"])
    expect = vector["expect"]
    expected_error = expect.get("error")
    for index, raw in enumerate(vector["events"]):
        # Events reach apply as parsed, with the vector's game_id filled in when omitted.
        result = apply(state, {"game_id": vector["game_id"], **raw})
        if isinstance(result, ApplyError):
            actual = f"index {index}, code {result.error.code}"
            if expected_error is None:
                raise VectorError(f"{file}: expected the final state, got rejection at {actual}")
            want = f"index {expected_error['index']}, code {expected_error['code']}"
            if actual != want:
                raise VectorError(f"{file}: expected rejection at {want}, got {actual}")
            return
        if expected_error is not None and index == expected_error["index"]:
            raise VectorError(
                f"{file}: expected rejection at index {index}, "
                f"code {expected_error['code']}, but the event was applied"
            )
        state = result.state
    if expected_error is not None:
        raise VectorError(
            f"{file}: expected rejection at index {expected_error['index']}, "
            f"code {expected_error['code']}, got no rejection"
        )
    expected_text = canonical_json(expect["state"])
    actual_text = _final_state(state)
    if actual_text != expected_text:
        raise VectorError(
            f"{file}: final state differs\nexpected: {expected_text}\nactual:   {actual_text}"
        )


def run_vector_file(path: Path) -> None:
    """Validate and run one vector file.

    Raises:
        VectorError: with every validation message (one per line), or the first mismatch; each
            line starts with the file name.
    """
    result = validate_file(path)
    if not result.ok or result.value is None:
        raise VectorError("\n".join(result.messages))
    # An over-long integer token is carried as a HugeInteger (see vector_validator); the reducer
    # sees a plain out-of-range int, so it answers with its own code.
    value = strip_huge_integers(result.value)
    if value["kind"] == "canonical_json":
        _run_canonical(path.name, value)
    else:
        _run_reducer(path.name, value)
