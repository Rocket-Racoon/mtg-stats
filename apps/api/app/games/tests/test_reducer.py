"""Unit tests of the reducer entry point and the contract models (CONTRACT.md sections 2, 3, 6)."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from app.games import core, services
from app.games.core import (
    EVENT_TYPES,
    REDUCER_ERROR_CODES,
    ApplyError,
    ApplyOk,
    EventEnvelope,
    GameState,
    ReplayError,
    ReplayOk,
    Settings,
    apply,
    canonical_json,
    initial_state,
    reduce,
    replay,
)
from app.games.core.contract import (
    DEFAULT_SETTINGS,
    SERVER_ONLY_REJECT_CODES,
    WIN_CONDITION_CODES,
)
from app.games.core.reducer import REGISTRY

GAME_ID = "018f0000-0000-7000-8000-000000000001"
EVENT_ID = "00000000-0000-4000-8000-000000000001"


def _state() -> GameState:
    return initial_state(GAME_ID, Settings.model_validate(DEFAULT_SETTINGS))


def _event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": EVENT_ID,
        "game_id": GAME_ID,
        "type": "turn_passed",
        "payload": {},
        "actor": {"kind": "player", "player_id": "p1"},
        "on_behalf_of_seat": None,
        "v": 1,
    }
    event.update(overrides)
    return event


def _code(result: Any) -> str:
    assert isinstance(result, ApplyError)
    return str(result.error.code)


def test_every_registered_type_has_a_handler_and_none_is_extra() -> None:
    assert set(REGISTRY) == set(EVENT_TYPES)
    assert len(EVENT_TYPES) == len(set(EVENT_TYPES)) == 29


def test_reducer_codes_never_include_the_server_only_codes() -> None:
    assert not set(SERVER_ONLY_REJECT_CODES) & set(REDUCER_ERROR_CODES)
    assert len(REDUCER_ERROR_CODES) == 19
    assert "other" in WIN_CONDITION_CODES


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_noop_handler_only_advances_last_seq(event_type: str) -> None:
    before = _state()
    result = apply(before, _event(type=event_type))
    assert isinstance(result, ApplyOk)
    assert result.ok is True
    assert result.state.last_seq == 1
    assert result.state == before.model_copy(update={"last_seq": 1})


def test_apply_does_not_mutate_state_or_event() -> None:
    state = _state()
    event = _event(seq=1)
    state_before = state.to_json_value()
    event_before = copy.deepcopy(event)
    apply(state, event)
    assert state.to_json_value() == state_before
    assert event == event_before


def test_rejected_event_leaves_the_caller_state_unchanged() -> None:
    state = _state()
    before = canonical_json(state.to_json_value())
    assert _code(apply(state, _event(type="nope"))) == "unknown_event_type"
    assert canonical_json(state.to_json_value()) == before


def test_reduce_is_an_alias_of_apply() -> None:
    assert reduce is apply


@pytest.mark.parametrize("event", [None, [], "x", 1, {}, _event(v=None), _event(v=1.0)])
def test_non_object_or_missing_or_non_integer_version_is_invalid_event(event: Any) -> None:
    assert _code(apply(_state(), event)) == "invalid_event"


def test_boolean_version_is_not_an_integer() -> None:
    assert _code(apply(_state(), _event(v=True))) == "invalid_event"


def test_unsupported_version_comes_before_type_checks() -> None:
    assert _code(apply(_state(), _event(v=2))) == "unsupported_version"
    assert _code(apply(_state(), _event(v=0, type="nope"))) == "unsupported_version"
    assert _code(apply(_state(), _event(v=2, type=5))) == "unsupported_version"
    assert _code(apply(_state(), _event(v=-1, payload=None))) == "unsupported_version"


def test_non_string_type_is_invalid_event_and_unknown_type_is_unknown_event_type() -> None:
    assert _code(apply(_state(), _event(type=5))) == "invalid_event"
    assert _code(apply(_state(), _event(type=None))) == "invalid_event"
    assert _code(apply(_state(), _event(type="Turn_Passed"))) == "unknown_event_type"


def test_unknown_type_comes_before_envelope_validation() -> None:
    assert _code(apply(_state(), _event(type="nope", payload=None))) == "unknown_event_type"


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_id": "not-a-uuid"},
        {"event_id": "00000000-0000-1000-8000-000000000001"},  # not version 4
        {"event_id": "00000000-0000-4000-c000-000000000001"},  # bad variant
        {"event_id": "00000000-0000-4000-8000-00000000000A"},  # uppercase
        {"event_id": 7},
        {"game_id": ""},
        {"game_id": 3},
        {"payload": None},
        {"payload": []},
        {"actor": None},
        {"actor": {}},
        {"actor": {"kind": "bot", "player_id": None}},
        {"actor": {"kind": "player", "player_id": None}},
        {"actor": {"kind": "player", "player_id": ""}},
        {"actor": {"kind": "system", "player_id": "p1"}},
        {"actor": {"kind": "system", "player_id": None, "extra": 1}},
        {"on_behalf_of_seat": 0},
        {"on_behalf_of_seat": 9},
        {"on_behalf_of_seat": True},
        {"on_behalf_of_seat": 1.0},
        {"seq": 0},
        {"seq": -1},
        {"seq": None},
        {"seq": True},
        {"seq": 1.0},
        {"ts": None},
        {"ts": 5},
        {"unexpected": 1},
    ],
)
def test_malformed_envelope_is_invalid_event(overrides: dict[str, Any]) -> None:
    assert _code(apply(_state(), _event(**overrides))) == "invalid_event"


def test_envelope_missing_a_required_key_is_invalid_event() -> None:
    for key in ("event_id", "game_id", "type", "payload", "actor", "on_behalf_of_seat"):
        event = _event()
        del event[key]
        assert _code(apply(_state(), event)) == ("invalid_event"), key


def test_on_behalf_of_seat_accepts_a_seat_id_and_system_actor_is_valid() -> None:
    event = _event(on_behalf_of_seat=8, actor={"kind": "system", "player_id": None})
    assert isinstance(apply(_state(), event), ApplyOk)


def test_game_id_mismatch_is_game_mismatch_before_seq_check() -> None:
    assert _code(apply(_state(), _event(game_id="other", seq=9))) == "game_mismatch"


def test_ts_is_accepted_and_never_read() -> None:
    a = apply(_state(), _event(ts="2026-01-01T00:00:00.000Z"))
    b = apply(_state(), _event(ts="garbage"))
    assert isinstance(a, ApplyOk)
    assert isinstance(b, ApplyOk)
    assert a.state == b.state


def test_unknown_payload_keys_are_ignored() -> None:
    assert isinstance(apply(_state(), _event(payload={"surprise": [1, 2]})), ApplyOk)


def test_seq_must_be_exactly_the_next_one() -> None:
    state = _state()
    assert isinstance(apply(state, _event(seq=1)), ApplyOk)
    for seq in (2, 5):
        assert _code(apply(state, _event(seq=seq))) == "out_of_order"
    after = apply(state, _event(seq=1))
    assert isinstance(after, ApplyOk)
    assert _code(apply(after.state, _event(seq=1))) == "out_of_order"  # a repeat
    assert isinstance(apply(after.state, _event(seq=2)), ApplyOk)


def test_absent_seq_is_applied_as_last_seq_plus_one() -> None:
    first = apply(_state(), _event())
    assert isinstance(first, ApplyOk)
    second = apply(first.state, _event())
    assert isinstance(second, ApplyOk)
    assert second.state.last_seq == 2


def test_replay_folds_events_and_reports_the_first_rejection_index() -> None:
    ok = replay(_state(), [_event(), _event(seq=2), _event(type="game_started")])
    assert isinstance(ok, ReplayOk)
    assert ok.state.last_seq == 3
    bad = replay(_state(), [_event(), _event(type="nope"), _event(v=9)])
    assert isinstance(bad, ReplayError)
    assert (bad.index, bad.error.code) == (1, "unknown_event_type")
    assert isinstance(replay(_state(), []), ReplayOk)


def test_envelope_to_wire_omits_unassigned_seq_and_ts() -> None:
    bare = EventEnvelope.model_validate(_event())
    assert set(bare.to_wire()) == {
        "event_id",
        "game_id",
        "type",
        "payload",
        "actor",
        "on_behalf_of_seat",
        "v",
    }
    full = EventEnvelope.model_validate(_event(seq=4, ts="2026-01-01T00:00:00.000Z"))
    assert full.to_wire()["seq"] == 4
    assert full.to_wire()["ts"] == "2026-01-01T00:00:00.000Z"


def test_initial_state_holds_every_key_and_the_four_settings() -> None:
    state = initial_state(
        GAME_ID,
        Settings(
            starting_life=30,
            commander_damage_threshold=15,
            poison_threshold=12,
            trust_mode="strict",
        ),
    )
    value = state.to_json_value()
    assert len(value) == 21
    assert value["settings"] == {
        "starting_life": 30,
        "commander_damage_threshold": 15,
        "poison_threshold": 12,
        "trust_mode": "strict",
    }
    assert (value["phase"], value["control_mode"], value["last_seq"], value["v"]) == (
        "lobby",
        "own_seat",
        0,
        1,
    )


def test_state_model_is_frozen_and_strict() -> None:
    state = _state()
    with pytest.raises(ValidationError):
        state.last_seq = 3  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Settings.model_validate({**DEFAULT_SETTINGS, "starting_life": True})
    with pytest.raises(ValidationError):
        Settings.model_validate({**DEFAULT_SETTINGS, "starting_life": 40.0})
    with pytest.raises(ValidationError):
        Settings.model_validate({**DEFAULT_SETTINGS, "starting_life": 2**53})


def test_seat_model_round_trips_through_the_state() -> None:
    raw = initial_state(GAME_ID, Settings.model_validate(DEFAULT_SETTINGS)).to_json_value()
    raw["seats"] = [
        {
            "seat": 1,
            "player_id": None,
            "name": "",
            "deck_version_id": None,
            "commanders": [{"card_id": None, "name": ""}],
            "life": -3,
            "poison": 0,
            "energy": 0,
            "experience": 0,
            "commander_cast": [0],
            "elimination": {"reason": "life", "by": None, "at_seq": 4, "cause": None},
        }
    ]
    raw["order"] = [1]
    state = GameState.model_validate(raw)
    assert state.to_json_value() == raw


def test_services_interface_exposes_the_core() -> None:
    assert services.apply is apply
    assert services.canonical_json is canonical_json
    assert set(services.__all__) == set(core.__all__)


def test_version_outside_the_safe_range_is_invalid_event_not_unsupported() -> None:
    assert _code(apply(_state(), _event(v=2**53))) == "invalid_event"
    assert _code(apply(_state(), _event(v=-(2**53)))) == "invalid_event"


@pytest.mark.parametrize(
    "payload",
    [
        {"a": 1.0},
        {"a": 1.5},
        {"a": float("nan")},
        {"a": 2**53},
        {"a": -(2**53)},
        {"a": "\ud800"},
        {"\udc00": 1},
        {"a": (1, 2)},
        {"a": {1: 2}},
        {"a": object()},
    ],
)
def test_payload_that_is_not_a_canonical_value_is_invalid_payload(payload: dict[str, Any]) -> None:
    assert _code(apply(_state(), _event(payload=payload))) == "invalid_payload"


def test_payload_is_checked_before_game_mismatch_and_out_of_order() -> None:
    bad = {"a": 1.5}
    assert _code(apply(_state(), _event(payload=bad, game_id="other"))) == "invalid_payload"
    assert _code(apply(_state(), _event(payload=bad, seq=7))) == "invalid_payload"


def test_malformed_envelope_wins_over_a_bad_payload() -> None:
    assert _code(apply(_state(), _event(payload=[1.5]))) == "invalid_event"


def _nested_payload(containers: int) -> dict[str, Any]:
    node: dict[str, Any] = {}
    for _ in range(containers - 1):
        node = {"a": node}
    return node


def test_payload_nesting_counts_the_payload_as_the_top_level_value() -> None:
    assert isinstance(apply(_state(), _event(payload=_nested_payload(65))), ApplyOk)
    result = apply(_state(), _event(payload=_nested_payload(66)))
    assert _code(result) == "invalid_payload"


def test_closed_game_is_not_checked_by_apply_yet() -> None:
    finished = _state().model_copy(update={"phase": "finished"})
    assert isinstance(apply(finished, _event()), ApplyOk)
