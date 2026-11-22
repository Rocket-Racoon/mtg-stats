"""Unit tests of the vector validator, coverage rule and runner helpers (CONTRACT.md section 8)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.games.core import EVENT_TYPES
from app.games.tests.repo_files import VECTORS_DIR, require
from app.games.tests.vector_runner import (
    VectorError,
    list_vector_files,
    run_vector_file,
    valid_reducer_vectors,
    validate_file,
)
from app.games.tests.vector_validator import (
    uncovered_event_type_messages,
    validate_vector_text,
)

F = "case-file.json"
EVENT = {"event_id": "e1", "type": "seat_joined", "payload": {}, "v": 1}


def reducer(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format": 1,
        "kind": "reducer",
        "name": "case-file",
        "description": "d",
        "game_id": "g",
        "settings": {},
        "events": [EVENT, EVENT],
        "expect": {"state": {}},
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not ...}


def canonical(*cases: Any, **changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format": 1,
        "kind": "canonical_json",
        "name": "case-file",
        "description": "d",
        "cases": list(cases),
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not ...}


def msgs(value: Any) -> list[str]:
    text = value if isinstance(value, str) else json.dumps(value)
    return list(validate_vector_text(F, text).messages)


def test_valid_reducer_and_canonical_files_have_no_message() -> None:
    assert msgs(reducer()) == []
    case = {"name": "a", "input": [1], "output": "[1]"}
    assert msgs(canonical(case)) == []


def test_invalid_json_gives_one_message_naming_the_file() -> None:
    [message] = msgs("{not json")
    assert message.startswith(f"{F}: invalid JSON: ")


def test_nan_constant_is_invalid_json() -> None:
    assert msgs('{"a": NaN}')[0].startswith(f"{F}: invalid JSON: ")


def test_non_object_top_level_is_reported() -> None:
    assert msgs("[1]") == [f"{F}: $ must be an object"]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"kind": ...}, "kind is required"),
        ({"kind": "other"}, 'kind must be "reducer" or "canonical_json"'),
        ({"format": ...}, "format is required"),
        ({"format": 2}, "format must be the integer 1"),
        ({"format": "1"}, "format must be the integer 1"),
        ({"format": True}, "format must be the integer 1"),
        ({"name": ...}, "name is required"),
        ({"name": "other"}, 'name must equal "case-file"'),
        ({"name": 5}, 'name must equal "case-file"'),
        ({"description": ...}, "description is required"),
        ({"description": ""}, "description must be a non-empty string"),
        ({"description": 3}, "description must be a non-empty string"),
    ],
)
def test_header_rules(changes: dict[str, Any], expected: str) -> None:
    assert msgs(reducer(**changes)) == [f"{F}: {expected}"]


def test_header_messages_come_in_contract_order_and_all_are_reported() -> None:
    got = msgs({"x": 1})
    assert got == [
        f"{F}: kind is required",
        f"{F}: format is required",
        f"{F}: name is required",
        f"{F}: description is required",
    ]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"game_id": ...}, ["game_id is required"]),
        ({"game_id": ""}, ["game_id must be a non-empty string"]),
        ({"game_id": 4}, ["game_id must be a non-empty string"]),
        ({"settings": ...}, ["settings is required"]),
        ({"settings": []}, ["settings must be an object"]),
        ({"settings": {"bogus": 1, "starting_life": 3}}, ["settings.bogus is not a known key"]),
        ({"events": ...}, ["events is required"]),
        ({"events": {}}, ["events must be an array"]),
        (
            {"events": [EVENT, 3, []], "expect": {"state": {}}},
            [
                "events[1] must be an object",
                "events[2] must be an object",
            ],
        ),
        ({"expect": ...}, ["expect is required"]),
        ({"expect": []}, ["expect must be an object"]),
        ({"expect": {"state": {}, "zzz": 1}}, ["expect.zzz is not a known key"]),
        ({"expect": {}}, ['expect must have exactly one of "state" or "error"']),
        (
            {"expect": {"state": {}, "error": {"index": 0, "code": "unknown_event_type"}}},
            ['expect must have exactly one of "state" or "error"'],
        ),
        ({"expect": {"state": []}}, ["expect.state must be an object"]),
    ],
)
def test_reducer_field_rules(changes: dict[str, Any], expected: list[str]) -> None:
    assert msgs(reducer(**changes)) == [f"{F}: {e}" for e in expected]


def err(**changes: Any) -> dict[str, Any]:
    error: dict[str, Any] = {"index": 1, "code": "unknown_event_type"}
    error.update(changes)
    return reducer(expect={"error": {k: v for k, v in error.items() if v is not ...}})


@pytest.mark.parametrize(
    ("vector", "expected"),
    [
        (reducer(expect={"error": 3}), ["expect.error must be an object"]),
        (err(extra=1), ["expect.error.extra is not a known key"]),
        (err(index=...), ["expect.error.index is required"]),
        (err(index=-1), ["expect.error.index must be a non-negative integer"]),
        (err(index="1"), ["expect.error.index must be a non-negative integer"]),
        (err(index=9007199254740992), ["expect.error.index must be a non-negative integer"]),
        (err(index=2), ["expect.error.index must be less than the number of events (2)"]),
        (err(code=...), ["expect.error.code is required"]),
        (err(code="forbidden"), ["expect.error.code must be a known reducer error code"]),
        (err(code=7), ["expect.error.code must be a known reducer error code"]),
    ],
)
def test_expect_error_rules(vector: dict[str, Any], expected: list[str]) -> None:
    # The bad index number tokens are not in `events`, so the token rule may add its message.
    got = [m for m in msgs(vector) if "number token" not in m and "safe range" not in m]
    assert got == [f"{F}: {e}" for e in expected]


def test_event_type_must_be_a_string_only_for_applied_events() -> None:
    events = [{"type": 5}, {"x": 1}, {"type": 7}]
    state_vector = reducer(events=events)
    assert msgs(state_vector) == [
        f"{F}: events[0].type must be a string",
        f"{F}: events[1].type must be a string",
        f"{F}: events[2].type must be a string",
    ]
    error_vector = reducer(
        events=events, expect={"error": {"index": 1, "code": "unknown_event_type"}}
    )
    assert msgs(error_vector) == [f"{F}: events[0].type must be a string"]


def test_unknown_top_level_keys_are_reported_in_document_order_after_kind_checks() -> None:
    vector = reducer(zeta=1, alpha=2, settings={"bogus": 1})
    assert msgs(vector) == [
        f"{F}: settings.bogus is not a known key",
        f"{F}: zeta is not a known key",
        f"{F}: alpha is not a known key",
    ]


def test_unknown_top_level_keys_are_skipped_when_kind_is_invalid() -> None:
    assert msgs(reducer(kind="x", zeta=1)) == [f'{F}: kind must be "reducer" or "canonical_json"']


def test_canonical_file_may_not_hold_reducer_keys() -> None:
    case = {"name": "a", "input": 1, "output": "1"}
    assert msgs(canonical(case, game_id="g")) == [f"{F}: game_id is not a known key"]


@pytest.mark.parametrize(
    ("vector", "expected"),
    [
        (canonical(cases=...), ["cases is required"]),
        (canonical(cases=[]), ["cases must be a non-empty array"]),
        (canonical(cases={}), ["cases must be a non-empty array"]),
        (canonical(5), ["cases[0] must be an object"]),
        (canonical({"input": 1, "output": "1"}), ["cases[0].name is required"]),
        (
            canonical({"name": "", "input": 1, "output": "1"}),
            ["cases[0].name must be a non-empty string"],
        ),
        (
            canonical(
                {"name": "a", "input": 1, "output": "1"},
                {"name": "a", "input": 2, "output": "2"},
            ),
            ["cases[1].name duplicates the name of cases[0]"],
        ),
        (canonical({"name": "a"}), ['cases[0] must have exactly one of "input" or "invalid"']),
        (
            canonical({"name": "a", "input": 1, "invalid": {}}),
            ['cases[0] must have exactly one of "input" or "invalid"'],
        ),
        (
            canonical({"name": "a", "input": 1, "output": "1", "error": "x"}),
            ["cases[0].error is not a known key"],
        ),
        (canonical({"name": "a", "input": 1}), ["cases[0].output is required"]),
        (canonical({"name": "a", "input": 1, "output": 1}), ["cases[0].output must be a string"]),
    ],
)
def test_canonical_case_rules(vector: dict[str, Any], expected: list[str]) -> None:
    assert msgs(vector) == [f"{F}: {e}" for e in expected]


def inv(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "a",
        "invalid": {"kind": "float", "value": "1.5"},
        "error": "non_integer",
    }
    override = changes.pop("invalid", {})
    if isinstance(override, dict):
        invalid = dict(body["invalid"])
        invalid.update(override)
        body["invalid"] = {k: v for k, v in invalid.items() if v is not ...}
    else:
        body["invalid"] = override
    body.update(changes)
    return canonical({k: v for k, v in body.items() if v is not ...})


@pytest.mark.parametrize(
    ("vector", "expected"),
    [
        (inv(error=...), ["cases[0].error is required"]),
        (inv(error="nope"), ["cases[0].error must be a canonical JSON error code"]),
        (inv(invalid=3), ["cases[0].invalid must be an object"]),
        (inv(invalid={"extra": 1}), ["cases[0].invalid.extra is not a known key"]),
        (inv(invalid={"kind": ...}), ["cases[0].invalid.kind is required"]),
        (
            inv(invalid={"kind": "x"}),
            [
                "cases[0].invalid.kind must be one of float, nan, infinity, integer, "
                "lone_surrogate, deep_array, deep_object"
            ],
        ),
        (inv(invalid={"kind": "nan", "value": "x"}), ["cases[0].invalid.value must be absent"]),
        (inv(invalid={"kind": "infinity", "value": ...}), []),
        (inv(invalid={"value": ...}), ["cases[0].invalid.value is required"]),
    ],
)
def test_canonical_invalid_case_rules(vector: dict[str, Any], expected: list[str]) -> None:
    assert msgs(vector) == [f"{F}: {e}" for e in expected]


@pytest.mark.parametrize(
    ("kind", "good", "bad", "message"),
    [
        (
            "float",
            ["1.5", "1e-7", "5e-324", "-0.5"],
            ["1.0", "1e2", "abc", "1e999", "", True, "01.5"],
            "must be decimal text of a non-integer number",
        ),
        (
            "integer",
            ["9007199254740992", "-9007199254740992"],
            ["9007199254740991", "1.5", "0", "09007199254740992", True],
            "must be decimal text of an integer outside the safe range",
        ),
        (
            "lone_surrogate",
            ["d800", "DFFF", "dBcD"],
            ["d7ff", "e000", "d80", "d8000", "zzzz", 5],
            "must be the 4-digit hex code of a surrogate code unit (d800 to dfff)",
        ),
        (
            "deep_array",
            ["1", "65", "10000"],
            ["0", "10001", "065", "-1", "1.0", 65],
            "must be decimal text of an integer from 1 to 10000",
        ),
        (
            "deep_object",
            ["66"],
            ["0", "x"],
            "must be decimal text of an integer from 1 to 10000",
        ),
    ],
)
def test_invalid_value_formats(kind: str, good: list[Any], bad: list[Any], message: str) -> None:
    for value in good:
        assert msgs(inv(invalid={"kind": kind, "value": value})) == [], value
    for value in bad:
        assert msgs(inv(invalid={"kind": kind, "value": value})) == [
            f"{F}: cases[0].invalid.value {message}"
        ], value


def raw(body: str) -> str:
    """A reducer file whose ``events`` text is ``body`` and whose settings come last."""
    return (
        '{"format": 1, "kind": "reducer", "name": "case-file", "description": "d", '
        f'"game_id": "g", "settings": {{}}, "events": {body}, "expect": {{"state": {{}}}}}}'
    )


@pytest.mark.parametrize(
    "token", ["1", "-0", "123456789012345678901234567890", "1.5", "1e-7", "-2.5E-3"]
)
def test_event_number_tokens_accepted(token: str) -> None:
    assert msgs(raw(f'[{{"type": "t", "payload": {{"n": {token}}}}}]')) == []


@pytest.mark.parametrize("token", ["1.0", "1e2", "1e999", "-0.0", "1E+2", "1e-400"])
def test_event_integral_float_tokens_are_rejected(token: str) -> None:
    assert msgs(raw(f'[{{"type": "t", "payload": {{"n": {token}}}}}]')) == [
        f"{F}: events[0].payload.n has the number token {token}; "
        "write an integral value as an integer"
    ]


def test_number_token_paths_skip_text_inside_strings() -> None:
    body = '[{"type": "1.0", "payload": {"s": "2.0", "list": [1, [3.0]]}}]'
    assert msgs(raw(body)) == [
        f"{F}: events[0].payload.list[1][0] has the number token 3.0; "
        "write an integral value as an integer"
    ]


def test_number_tokens_outside_events_must_be_safe_integers() -> None:
    text = (
        '{"format": 1, "kind": "reducer", "name": "case-file", "description": "d", '
        '"game_id": "g", "settings": {"starting_life": 40.0, "poison_threshold": 9007199254740992,'
        ' "commander_damage_threshold": -0, "trust_mode": "casual"}, "events": [], '
        '"expect": {"state": {"a": [1e2]}}}'
    )
    assert msgs(text) == [
        f"{F}: settings.starting_life must be an integer (found the number token 40.0)",
        f"{F}: settings.poison_threshold must be an integer in the safe range "
        "(found 9007199254740992)",
        f"{F}: expect.state.a[0] must be an integer (found the number token 1e2)",
    ]


def test_header_format_one_point_zero_reports_only_the_token() -> None:
    text = raw("[]").replace('"format": 1', '"format": 1.0')
    assert msgs(text) == [f"{F}: format must be an integer (found the number token 1.0)"]


def test_canonical_input_numbers_follow_the_everywhere_else_rule() -> None:
    text = json.dumps(canonical({"name": "a", "input": [1], "output": "[1]"})).replace(
        '"input": [1]', '"input": [1.5, 99999999999999999999]'
    )
    assert msgs(text) == [
        f"{F}: cases[0].input[0] must be an integer (found the number token 1.5)",
        f"{F}: cases[0].input[1] must be an integer in the safe range (found 99999999999999999999)",
    ]


def test_duplicate_keys_are_reported_after_number_tokens_with_the_second_path() -> None:
    text = raw('[{"type": "t", "type": "u", "payload": {"a": 1, "a": 2}}]').replace(
        '"description": "d"', '"description": "d", "game_id": "h"'
    )
    assert msgs(text) == [
        f"{F}: game_id is a duplicate key",
        f"{F}: events[0].type is a duplicate key",
        f"{F}: events[0].payload.a is a duplicate key",
    ]


def test_every_problem_is_reported_not_only_the_first() -> None:
    text = '{"kind": "reducer", "format": 2, "name": "x", "description": "", "settings": 1.0}'
    got = msgs(text)
    assert len(got) >= 8
    assert got[0] == f"{F}: format must be the integer 1"
    assert got[-1] == f"{F}: settings must be an integer (found the number token 1.0)"


def test_event_types_covered_only_by_applied_events_count() -> None:
    types = ("seat_joined", "game_started", "life_changed")
    ok_state = reducer(events=[{"type": "seat_joined"}, {"type": "game_started"}])
    ok_error = reducer(
        events=[{"type": "seat_joined"}, {"type": "life_changed"}, {"type": "game_started"}],
        expect={"error": {"index": 1, "code": "unknown_event_type"}},
    )
    other = canonical({"name": "a", "input": 1, "output": "1"})
    assert uncovered_event_type_messages([ok_state, ok_error, other], types) == [
        "uncovered event type: life_changed"
    ]
    assert uncovered_event_type_messages([ok_error], types) == [
        "uncovered event type: game_started",
        "uncovered event type: life_changed",
    ]


def test_uncovered_types_are_reported_in_registry_order() -> None:
    assert uncovered_event_type_messages([], EVENT_TYPES) == [
        f"uncovered event type: {t}" for t in EVENT_TYPES
    ]


def test_every_registry_type_is_exercised_by_the_shared_vectors() -> None:
    files = list_vector_files(require(VECTORS_DIR))
    assert uncovered_event_type_messages(valid_reducer_vectors(files), EVENT_TYPES) == []


def test_a_file_failing_validation_contributes_no_coverage(tmp_path: Path) -> None:
    good = reducer(events=[{"type": "seat_joined"}])
    bad = reducer(name="wrong", events=[{"type": "game_started"}])
    (tmp_path / "case-file.json").write_text(json.dumps(good), encoding="utf-8")
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    vectors = valid_reducer_vectors(sorted(tmp_path.glob("*.json")))
    assert [v["name"] for v in vectors] == ["case-file"]


def test_missing_directory_fails_with_the_contract_message(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(VectorError) as raised:
        list_vector_files(missing)
    assert str(raised.value) == f"vectors directory not found: {missing}"


def test_directory_without_json_files_fails_with_the_contract_message(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(VectorError) as raised:
        list_vector_files(tmp_path)
    assert str(raised.value) == f"vectors directory has no .json files: {tmp_path}"


def test_files_are_listed_sorted_by_name(tmp_path: Path) -> None:
    for name in ("b.json", "a.json", "c.txt"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert [p.name for p in list_vector_files(tmp_path)] == ["a.json", "b.json"]


def test_invalid_utf8_is_reported_as_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(b"\xff\xfe{}")
    [message] = validate_file(path).messages
    assert message.startswith("bad.json: invalid JSON: ")


def write(tmp_path: Path, value: dict[str, Any], name: str = "case-file") -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_runner_fails_a_malformed_file_with_its_messages(tmp_path: Path) -> None:
    path = write(tmp_path, reducer(game_id=..., description=""))
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    assert str(raised.value).splitlines() == [
        f"{F}: description must be a non-empty string",
        f"{F}: game_id is required",
    ]


def test_runner_names_the_case_of_a_canonical_mismatch(tmp_path: Path) -> None:
    path = write(tmp_path, canonical({"name": "wrong", "input": [1], "output": "[2]"}))
    with pytest.raises(VectorError, match=r'^case-file\.json: case "wrong": '):
        run_vector_file(path)


def test_runner_reports_a_canonical_invalid_case_that_does_not_fail(tmp_path: Path) -> None:
    case = {"name": "ok", "invalid": {"kind": "deep_array", "value": "3"}, "error": "non_integer"}
    path = write(tmp_path, canonical(case))
    with pytest.raises(VectorError, match='case "ok": expected error non_integer, got no error'):
        run_vector_file(path)


def test_runner_reports_expected_and_actual_rejection(tmp_path: Path) -> None:
    event = {"event_id": "e", "type": "nope", "payload": {}, "v": 1}
    wrong = reducer(events=[event], expect={"error": {"index": 0, "code": "invalid_event"}})
    path = write(tmp_path, wrong)
    with pytest.raises(VectorError) as raised:
        run_vector_file(path)
    text = str(raised.value)
    assert text.startswith("case-file.json: expected rejection at index 0, code invalid_event")
    assert "got index 0, code " in text


def test_runner_reports_an_unexpected_rejection_when_a_state_is_expected(tmp_path: Path) -> None:
    event = {"event_id": "e", "type": "nope", "payload": {}, "v": 1}
    path = write(tmp_path, reducer(events=[event]))
    with pytest.raises(VectorError, match="expected the final state, got rejection at index 0"):
        run_vector_file(path)


def test_runner_reports_a_state_mismatch_with_both_texts(tmp_path: Path) -> None:
    path = write(tmp_path, reducer(events=[], expect={"state": {"a": 1}}))
    with pytest.raises(VectorError, match=r"final state differs\nexpected: .*\nactual: "):
        run_vector_file(path)


GAME = "018f0000-0000-7000-8000-000000000001"


def test_over_long_integer_tokens_never_hit_the_interpreter_digit_limit(tmp_path: Path) -> None:
    big = "9" * 5000
    event = (
        '{"event_id": "00000000-0000-4000-8000-000000000001", '
        '"type": "turn_passed", "payload": {"seat": ' + big + '}, "actor": {"kind": "player", '
        '"player_id": "018f0000-0000-7000-8000-0000000000a1"}, '
        '"on_behalf_of_seat": null, "v": 1}'
    )
    text = (
        raw(f"[{event}]")
        .replace('"game_id": "g"', '"game_id": "' + GAME + '"')
        .replace(
            '"expect": {"state": {}}',
            '"expect": {"error": {"index": 0, "code": "invalid_payload"}}',
        )
    )
    assert msgs(text) == []
    path = tmp_path / "case-file.json"
    path.write_text(text, encoding="utf-8")
    run_vector_file(path)  # the reducer rejects the out-of-range integer with its own code
    negative = text.replace(big, "-" + big)
    path.write_text(negative, encoding="utf-8")
    run_vector_file(path)
    outside = msgs(text.replace('"settings": {}', '"settings": {"starting_life": ' + big + "}"))
    assert outside == [
        f"{F}: settings.starting_life must be an integer in the safe range (found {big})"
    ]
