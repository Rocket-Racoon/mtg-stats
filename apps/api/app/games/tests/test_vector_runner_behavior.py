"""Behavior of the Python vector runner on temporary directories (S-6.1.2, CONTRACT.md 8.3-8.5).

Acceptance criteria 1, 2 and 4: every ``*.json`` file is executed, a malformed file fails and names
the file and the field, a vector whose expectation is wrong for this implementation fails and names
the vector file, and the per-file test id is the file name. The TypeScript twin is
``packages/game-core/src/vector-runner.behavior.test.ts``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from app.games.core import EVENT_TYPES
from app.games.tests import test_vectors
from app.games.tests.repo_files import VECTORS_DIR
from app.games.tests.vector_runner import (
    VectorError,
    list_vector_files,
    run_vector_file,
    valid_reducer_vectors,
)
from app.games.tests.vector_validator import uncovered_event_type_messages


def _real(name: str) -> dict[str, Any]:
    path = VECTORS_DIR / f"{name}.json"
    assert path.is_file(), f"missing vector {path}"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _write(directory: Path, name: str, doc: dict[str, Any]) -> Path:
    """Write ``doc`` as ``<name>.json`` with its ``name`` field set to match."""
    path = directory / f"{name}.json"
    path.write_text(json.dumps({**doc, "name": name}), encoding="utf-8")
    return path


# ------------------------------------------------------------------------------ loading (8.5)


def test_missing_directory_fails_with_the_contract_message(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(VectorError) as raised:
        list_vector_files(missing)
    assert str(raised.value) == f"vectors directory not found: {missing}"


def test_a_file_in_place_of_the_directory_counts_as_not_found(tmp_path: Path) -> None:
    not_a_directory = tmp_path / "vectors"
    not_a_directory.write_text("{}", encoding="utf-8")
    with pytest.raises(VectorError) as raised:
        list_vector_files(not_a_directory)
    assert str(raised.value) == f"vectors directory not found: {not_a_directory}"


def test_empty_directory_fails_with_the_contract_message(tmp_path: Path) -> None:
    with pytest.raises(VectorError) as raised:
        list_vector_files(tmp_path)
    assert str(raised.value) == f"vectors directory has no .json files: {tmp_path}"


def test_directory_with_only_other_extensions_fails_as_empty(tmp_path: Path) -> None:
    for name in ("a.txt", "b.json.bak", "c.jsonl", "json", "d.json5"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    with pytest.raises(VectorError) as raised:
        list_vector_files(tmp_path)
    assert str(raised.value) == f"vectors directory has no .json files: {tmp_path}"


def test_every_json_file_is_listed_sorted_and_other_entries_are_ignored(tmp_path: Path) -> None:
    for name in ("b.json", "a.json", "a-b.json", "a10.json", "a2.json", "has space.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    for name in ("notes.txt", "x.json.bak", "y.jsonl"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "deep.json").write_text("{}", encoding="utf-8")
    (tmp_path / "trap.json").mkdir()
    assert [p.name for p in list_vector_files(tmp_path)] == [
        "a-b.json",
        "a.json",
        "a10.json",
        "a2.json",
        "b.json",
        "has space.json",
    ]


def test_the_real_vectors_directory_lists_every_json_file_it_holds() -> None:
    on_disk = sorted(p.name for p in VECTORS_DIR.iterdir() if p.is_file() and p.suffix == ".json")
    assert [p.name for p in list_vector_files(VECTORS_DIR)] == on_disk
    assert len(on_disk) >= 130


# ---------------------------------------------------------------- per-file tests (criterion 1)


def test_vector_test_ids_are_the_file_names_of_every_json_file() -> None:
    marks = [m for m in cast(Any, test_vectors.test_vector).pytestmark if m.name == "parametrize"]
    assert len(marks) == 1
    ids = list(marks[0].kwargs["ids"])
    on_disk = sorted(p.name for p in VECTORS_DIR.glob("*.json"))
    assert ids == on_disk
    assert all(i.endswith(".json") for i in ids)


def test_one_coverage_test_exists_per_registered_event_type() -> None:
    marks = [
        m
        for m in cast(Any, test_vectors.test_event_type_is_exercised_by_a_vector).pytestmark
        if m.name == "parametrize"
    ]
    assert len(marks) == 1
    assert list(marks[0].args[1]) == list(EVENT_TYPES)
    assert len(EVENT_TYPES) == 29


def test_the_shipped_vectors_exercise_every_registered_event_type() -> None:
    vectors = valid_reducer_vectors(list_vector_files(VECTORS_DIR))
    assert uncovered_event_type_messages(vectors, EVENT_TYPES) == []


def test_every_shipped_vector_passes_the_python_runner() -> None:
    for path in list_vector_files(VECTORS_DIR):
        run_vector_file(path)


# --------------------------------------------- the seed vectors S-6.1.1 relied on (lost rewrite)


@pytest.mark.parametrize(
    "name",
    [
        "canonical-json",
        "initial-state",
        "reject-unknown-event-type",
        "reject-unsupported-version",
        "reject-unsupported-version-and-type",
    ],
)
def test_seed_vector_is_still_in_the_vectors_directory(name: str) -> None:
    assert (VECTORS_DIR / f"{name}.json").is_file()
    assert name + ".json" in {p.name for p in list_vector_files(VECTORS_DIR)}


def test_every_vector_is_format_one_and_named_after_its_file() -> None:
    for path in list_vector_files(VECTORS_DIR):
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["format"] == 1, path.name
        assert doc["name"] == path.stem, path.name
        assert doc["kind"] in {"reducer", "canonical_json"}, path.name


def test_every_reducer_vector_expects_exactly_one_of_state_or_error() -> None:
    for path in list_vector_files(VECTORS_DIR):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc["kind"] == "reducer":
            assert len(doc["expect"]) == 1, path.name
            assert set(doc["expect"]) <= {"state", "error"}, path.name


def test_every_canonical_output_is_utf8_encodable() -> None:
    for path in list_vector_files(VECTORS_DIR):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc["kind"] == "canonical_json":
            for case in doc["cases"]:
                if "output" in case:
                    case["output"].encode("utf-8")


# ----------------------------------------------------- a malformed file fails by name (criterion 4)


def test_malformed_vector_fails_and_names_the_file_and_the_field(tmp_path: Path) -> None:
    doc = _real("reject-unknown-event-type")
    doc["expect"] = {"error": {"index": 5, "code": "unknown_event_type"}}
    path = _write(tmp_path, "reject-broken", doc)
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    assert str(raised.value) == (
        "reject-broken.json: expect.error.index must be less than the number of events (1)"
    )


def test_malformed_vector_reports_every_problem_one_per_line(tmp_path: Path) -> None:
    doc = _real("reject-unknown-event-type")
    del doc["description"]
    doc["format"] = 2
    doc["extra"] = 1
    path = _write(tmp_path, "reject-broken", doc)
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    assert str(raised.value).split("\n") == [
        "reject-broken.json: format must be the integer 1",
        "reject-broken.json: description is required",
        "reject-broken.json: extra is not a known key",
    ]


def test_unknown_kind_fails_naming_the_file(tmp_path: Path) -> None:
    doc = _real("reject-unknown-event-type")
    doc["kind"] = "reducers"
    path = _write(tmp_path, "reject-broken", doc)
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    assert str(raised.value).startswith("reject-broken.json: ")
    assert 'kind must be "reducer" or "canonical_json"' in str(raised.value)


def test_invalid_json_fails_naming_the_file(tmp_path: Path) -> None:
    path = tmp_path / "reject-broken.json"
    path.write_text('{"kind": "reducer",', encoding="utf-8")
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    assert str(raised.value).startswith("reject-broken.json: invalid JSON: ")


def test_a_byte_order_mark_makes_the_file_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "reject-broken.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(_real("reject-unknown-event-type")).encode())
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    assert str(raised.value).startswith("reject-broken.json: invalid JSON: ")


def test_invalid_utf8_bytes_make_the_file_invalid_json(tmp_path: Path) -> None:
    doc = {**_real("reject-unknown-event-type"), "name": "reject-broken", "description": "XX"}
    path = tmp_path / "reject-broken.json"
    path.write_bytes(json.dumps(doc).encode("utf-8").replace(b"XX", b"\xff\xfe"))
    assert b"\xff\xfe" in path.read_bytes()
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    assert str(raised.value).startswith("reject-broken.json: invalid JSON: ")


def test_a_malformed_file_among_valid_ones_does_not_hide_the_valid_ones(tmp_path: Path) -> None:
    good = _write(tmp_path, "accept-good", _real("initial-state-empty-settings"))
    bad_doc = _real("reject-unknown-event-type")
    bad_doc["events"] = "nope"
    bad = _write(tmp_path, "reject-bad", bad_doc)
    run_vector_file(good)
    with pytest.raises(VectorError, match=r"^reject-bad\.json: events must be an array"):
        run_vector_file(bad)
    assert [p.name for p in list_vector_files(tmp_path)] == ["accept-good.json", "reject-bad.json"]


# ------------------------------------------ a wrong expectation fails by file name (criterion 2)


def test_a_correct_copy_of_a_real_vector_passes(tmp_path: Path) -> None:
    for name in (
        "reject-unknown-event-type",
        "accept-optimistic-then-confirmed-seq",
        "initial-state-empty-settings",
        "canonical-json-integers",
    ):
        run_vector_file(_write(tmp_path, "copy-" + name, _real(name)))


def test_wrong_error_code_fails_and_names_the_vector(tmp_path: Path) -> None:
    doc = _real("reject-unknown-event-type")
    doc["expect"] = {"error": {"index": 0, "code": "invalid_event"}}
    path = _write(tmp_path, "reject-wrong-code", doc)
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    message = str(raised.value)
    assert message.startswith("reject-wrong-code.json: ")
    assert "invalid_event" in message
    assert "unknown_event_type" in message


def test_wrong_error_index_fails_and_names_the_vector(tmp_path: Path) -> None:
    doc = _real("reject-out-of-order-repeat")
    assert doc["expect"]["error"]["index"] == 1
    doc["expect"] = {"error": {"index": 0, "code": "out_of_order"}}
    path = _write(tmp_path, "reject-wrong-index", doc)
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    assert str(raised.value).startswith("reject-wrong-index.json: ")


def test_an_accepted_event_where_a_rejection_is_expected_fails_by_name(tmp_path: Path) -> None:
    doc = _real("accept-optimistic-then-confirmed-seq")
    doc["expect"] = {"error": {"index": 0, "code": "invalid_event"}}
    path = _write(tmp_path, "accept-but-expects-reject", doc)
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    assert str(raised.value).startswith("accept-but-expects-reject.json: ")


def test_a_rejection_where_a_state_is_expected_fails_by_name(tmp_path: Path) -> None:
    doc = _real("reject-unknown-event-type")
    doc["expect"] = {"state": _real("initial-state-empty-settings")["expect"]["state"]}
    path = _write(tmp_path, "reject-but-expects-state", doc)
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    assert str(raised.value).startswith("reject-but-expects-state.json: ")
    assert "unknown_event_type" in str(raised.value)


def test_wrong_final_state_fails_and_names_the_vector(tmp_path: Path) -> None:
    doc = _real("accept-optimistic-then-confirmed-seq")
    doc["expect"]["state"]["last_seq"] = doc["expect"]["state"]["last_seq"] + 1
    path = _write(tmp_path, "accept-wrong-state", doc)
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    message = str(raised.value)
    assert message.startswith("accept-wrong-state.json: ")
    assert "final state differs" in message


def test_final_state_is_compared_as_canonical_json_not_by_key_order(tmp_path: Path) -> None:
    doc = _real("initial-state-empty-settings")
    state = doc["expect"]["state"]
    doc["expect"]["state"] = {k: state[k] for k in sorted(state, reverse=True)}
    run_vector_file(_write(tmp_path, "accept-reordered", doc))


def test_wrong_canonical_output_fails_naming_the_file_and_the_case(tmp_path: Path) -> None:
    doc = _real("canonical-json-integers")
    first = doc["cases"][0]
    first["output"] = first["output"] + "0"
    path = _write(tmp_path, "canonical-wrong-output", doc)
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    assert str(raised.value).startswith(f'canonical-wrong-output.json: case "{first["name"]}": ')


def test_wrong_canonical_error_code_fails_naming_the_file_and_the_case(tmp_path: Path) -> None:
    doc = _real("canonical-json-rejections")
    case = next(c for c in doc["cases"] if "invalid" in c)
    case["error"] = "unsupported_value" if case["error"] != "unsupported_value" else "non_integer"
    path = _write(tmp_path, "canonical-wrong-error", doc)
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    assert str(raised.value).startswith(f'canonical-wrong-error.json: case "{case["name"]}": ')


# ------------------------------------------------------------- coverage on a directory (8.4)


def test_coverage_is_computed_from_the_valid_reducer_vectors_of_a_directory(
    tmp_path: Path,
) -> None:
    covers = _real("accept-all-registered-event-types")
    _write(tmp_path, "covers-everything", covers)
    broken = dict(covers)
    broken["events"] = "nope"
    _write(tmp_path, "broken", broken)
    vectors = valid_reducer_vectors(list_vector_files(tmp_path))
    assert [v["name"] for v in vectors] == ["covers-everything"]
    assert uncovered_event_type_messages(vectors, EVENT_TYPES) == []


def test_coverage_of_a_directory_without_reducer_vectors_lists_every_type_in_order(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "only-canonical", _real("canonical-json-integers"))
    vectors = valid_reducer_vectors(list_vector_files(tmp_path))
    assert vectors == []
    assert uncovered_event_type_messages(vectors, EVENT_TYPES) == [
        f"uncovered event type: {t}" for t in EVENT_TYPES
    ]
