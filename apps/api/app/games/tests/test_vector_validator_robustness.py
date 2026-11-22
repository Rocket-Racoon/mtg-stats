"""Depth limit, never-throws, discovery and canonical reporting of the Python vector runner.

CONTRACT.md 8.3 (nesting depth, never throws, key names, integer cap) and 8.5 (loading, one
failure per canonical file listing every failing case).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from app.games.tests import vector_runner, vector_validator
from app.games.tests.repo_files import GAME_CORE
from app.games.tests.vector_runner import (
    VectorError,
    list_vector_files,
    run_vector_file,
    valid_reducer_vectors,
)
from app.games.tests.vector_validator import (
    nesting_depth,
    validate_vector_text,
)

F = "deep.json"


def _header(extra: str) -> str:
    return (
        '{"format": 1, "kind": "canonical_json", "name": "deep", "description": "d", '
        f'"cases": [{{"name": "a", "input": {extra}, "output": "x"}}]}}'
    )


def _nested(levels_inside_input: int) -> str:
    # object (1) + cases array (2) + case object (3) + the chain
    return _header("[" * levels_inside_input + "]" * levels_inside_input)


def test_depth_counts_levels_and_skips_strings_with_backslashes() -> None:
    assert nesting_depth("[[{}]]") == 3
    assert nesting_depth('["[[[", "\\"[[", 1]') == 1
    assert nesting_depth("]]]][") == 1
    assert nesting_depth('["unterminated [[[') == 1
    assert nesting_depth('["ends with backslash \\') == 1


def test_exactly_512_levels_is_accepted_and_513_gives_the_single_depth_message() -> None:
    assert validate_vector_text(F, _nested(512 - 3)).messages[:1] != (
        f"{F}: $ is nested more than 512 levels deep",
    )
    over = validate_vector_text(F, _nested(512 - 2))
    assert over.messages == (f"{F}: $ is nested more than 512 levels deep",)
    assert over.value is None


def test_depth_message_replaces_even_invalid_json() -> None:
    text = "[" * 600 + "{not json"
    assert validate_vector_text(F, text).messages == (
        f"{F}: $ is nested more than 512 levels deep",
    )


def test_a_very_deep_text_does_not_raise() -> None:
    for text in ("[" * 100000, "{" * 100000 + "}" * 100000, '{"a":' * 5000):
        assert len(validate_vector_text(F, text).messages) == 1


def test_a_validator_failure_is_one_internal_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(file: str, text: str) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(vector_validator, "_validate", boom)
    [message] = validate_vector_text(F, "{}").messages
    assert message.startswith(f"{F}: $ internal error: ")
    assert "boom" in message


def test_the_runner_turns_an_unexpected_error_into_a_message_naming_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(file: str, vector: dict[str, Any]) -> None:
        raise RuntimeError("kaput")

    monkeypatch.setattr(vector_runner, "_run_reducer", boom)
    path = tmp_path / "a.json"
    path.write_text(
        json.dumps(
            {
                "format": 1,
                "kind": "reducer",
                "name": "a",
                "description": "d",
                "game_id": "g",
                "settings": {},
                "events": [],
                "expect": {"state": {}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(VectorError, match=r"^a\.json: unexpected RuntimeError: kaput"):
        run_vector_file(path)


def test_a_deep_file_among_others_does_not_stop_coverage_computation(tmp_path: Path) -> None:
    (tmp_path / "deep.json").write_text("[" * 5000, encoding="utf-8")
    (tmp_path / "other.json").write_text("{}", encoding="utf-8")
    assert valid_reducer_vectors(list_vector_files(tmp_path)) == []
    with pytest.raises(VectorError, match=r"^deep\.json: \$ is nested more than 512 levels deep$"):
        run_vector_file(tmp_path / "deep.json")


def test_proto_like_names_are_plain_unknown_keys() -> None:
    text = (
        '{"format": 1, "kind": "reducer", "name": "p", "description": "d", "game_id": "g", '
        '"settings": {"constructor": 1, "toString": 2}, "events": [], '
        '"expect": {"state": {}, "__proto__": 1}, "hasOwnProperty": 3}'
    )
    assert list(validate_vector_text("p.json", text).messages) == [
        "p.json: settings.constructor is not a known key",
        "p.json: settings.toString is not a known key",
        "p.json: expect.__proto__ is not a known key",
        "p.json: hasOwnProperty is not a known key",
    ]


def test_integer_like_keys_stay_in_document_order() -> None:
    text = (
        '{"format": 1, "kind": "reducer", "name": "p", "description": "d", "game_id": "g", '
        '"settings": {}, "events": [], "expect": {"state": {}}, "10": 1, "2": 2, "1": 3}'
    )
    assert list(validate_vector_text("p.json", text).messages) == [
        "p.json: 10 is not a known key",
        "p.json: 2 is not a known key",
        "p.json: 1 is not a known key",
    ]


def _integer_case(value: str) -> list[str]:
    case = {"name": "a", "invalid": {"kind": "integer", "value": value}, "error": "x"}
    text = json.dumps(
        {"format": 1, "kind": "canonical_json", "name": "i", "description": "d", "cases": [case]}
    ).replace('"error": "x"', '"error": "integer_out_of_range"')
    return list(validate_vector_text("i.json", text).messages)


def test_integer_text_may_have_at_most_300_digits_and_the_minus_is_not_counted() -> None:
    assert _integer_case("9" * 300) == []
    assert _integer_case("-" + "9" * 300) == []
    assert _integer_case("9" * 301) == [
        "i.json: cases[0].invalid.value must have at most 300 digits"
    ]
    assert _integer_case("-" + "9" * 301) == [
        "i.json: cases[0].invalid.value must have at most 300 digits"
    ]
    assert _integer_case("9007199254740991") == [
        "i.json: cases[0].invalid.value must be decimal text of an integer outside the safe range"
    ]


def test_a_lowered_interpreter_digit_limit_does_not_turn_tokens_into_invalid_json() -> None:
    old = sys.get_int_max_str_digits()
    token = "9" * 700
    text = (
        '{"format": 1, "kind": "reducer", "name": "d", "description": "x", "game_id": "g", '
        '"settings": {}, "events": [{"type": "t", "payload": {"n": ' + token + "}}], "
        '"expect": {"state": {}}}'
    )
    try:
        sys.set_int_max_str_digits(640)
        assert validate_vector_text("d.json", text).messages == ()
    finally:
        sys.set_int_max_str_digits(old)


def test_a_canonical_file_reports_every_failing_case_one_line_each_in_case_order(
    tmp_path: Path,
) -> None:
    cases = [
        {"name": "ok", "input": [1], "output": "[1]"},
        {"name": "bad1", "input": [1], "output": "[2]"},
        {"name": "bad2", "input": [3], "output": "[4]"},
    ]
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {"format": 1, "kind": "canonical_json", "name": "c", "description": "d", "cases": cases}
        ),
        encoding="utf-8",
    )
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    lines = str(raised.value).split("\n")
    assert [line.split(": ")[1] for line in lines] == ['case "bad1"', 'case "bad2"']
    assert all(line.startswith("c.json: case ") for line in lines)


def test_only_regular_files_with_the_exact_lowercase_extension_are_vectors(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "b.JSON").write_text("{}", encoding="utf-8")
    (tmp_path / "c.Json").write_text("{}", encoding="utf-8")
    (tmp_path / "dir.json").mkdir()
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "n.json").write_text("{}", encoding="utf-8")
    try:
        (tmp_path / "link.json").symlink_to(tmp_path / "a.json")
        (tmp_path / "dangling.json").symlink_to(tmp_path / "missing.json")
    except (OSError, NotImplementedError):
        pass  # symbolic links need a privilege on some platforms
    assert [p.name for p in list_vector_files(tmp_path)] == ["a.json"]


def test_a_directory_holding_only_non_regular_entries_has_no_json_files(tmp_path: Path) -> None:
    (tmp_path / "x.JSON").write_text("{}", encoding="utf-8")
    (tmp_path / "dir.json").mkdir()
    with pytest.raises(VectorError, match=r"has no \.json files"):
        list_vector_files(tmp_path)


def test_a_bom_is_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf{}")
    with pytest.raises(VectorError, match=r"^bom\.json: invalid JSON: "):
        run_vector_file(path)


def test_no_shared_corpus_case_carries_alternatives_or_a_gap() -> None:
    cases_dir = GAME_CORE / "vector-cases"
    assert cases_dir.is_dir(), cases_dir
    offenders: list[str] = []
    for path in sorted(cases_dir.glob("*.json")):
        doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        for case in doc.get("cases", [doc]):
            if "alternatives" in case or "gap" in case:
                offenders.append(path.name)
        if "alternatives" in doc or "gap" in doc:
            offenders.append(path.name)
    assert offenders == []
