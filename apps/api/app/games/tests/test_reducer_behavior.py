"""Acceptance criteria 3 and 4 (S-6.1.1), Python side, behavioural.

Criterion 3: the reducer reads no clock, randomness or I/O (``test_purity.py`` scans the source;
here ``apply`` runs with those entry points patched to raise). Criterion 4: an unknown ``type`` or
an unsupported ``v`` is rejected with ``unknown_event_type`` or ``unsupported_version``.
"""

from __future__ import annotations

import builtins
import copy
import datetime
import itertools
import os
import random
import socket
import time
import uuid
from typing import Any

import pytest

from app.games.core import (
    EVENT_TYPES,
    ApplyError,
    ApplyOk,
    GameState,
    ReducerError,
    ReplayError,
    ReplayOk,
    Settings,
    apply,
    canonical_bytes,
    canonical_json,
    initial_state,
    reduce,
    replay,
)
from app.games.tests.repo_files import load_vector

GAME_ID = "018f0000-0000-7000-8000-000000000001"
PLAYER_ID = "018f0000-0000-7000-8000-0000000000a1"
DEFAULTS = Settings(
    starting_life=40, commander_damage_threshold=21, poison_threshold=10, trust_mode="casual"
)
_counter = itertools.count(1)


def event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event_id": f"00000000-0000-4000-8000-{next(_counter):012d}",
        "game_id": GAME_ID,
        "type": "turn_passed",
        "payload": {},
        "actor": {"kind": "player", "player_id": PLAYER_ID},
        "on_behalf_of_seat": None,
        "v": 1,
    }
    base.update(overrides)
    return base


def lobby() -> GameState:
    return initial_state(GAME_ID, DEFAULTS)


def populated(**updates: Any) -> GameState:
    """A four-seat state from the shared vector (seats cannot be reached via the no-op handlers)."""
    case = load_vector("canonical-json-game-state")["cases"][0]["input"]
    return GameState.model_validate(case).model_copy(update={"game_id": GAME_ID, **updates})


def code(state: GameState, raw: Any) -> str:
    result = apply(state, raw)
    assert isinstance(result, ApplyError), f"expected a rejection, got {result}"
    return str(result.error.code)


def ok_state(state: GameState, raw: Any) -> GameState:
    result = apply(state, raw)
    assert isinstance(result, ApplyOk), f"expected ok, got {result}"
    return result.state


# --- criterion 3 ---------------------------------------------------------------------------------


class ForbiddenError(Exception):
    pass


def _boom(name: str) -> Any:
    def raiser(*_args: Any, **_kwargs: Any) -> Any:
        raise ForbiddenError(name)

    return raiser


class _ThrowingDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz: Any = None) -> Any:
        raise ForbiddenError("datetime.now")

    @classmethod
    def utcnow(cls) -> Any:
        raise ForbiddenError("datetime.utcnow")

    @classmethod
    def today(cls) -> Any:
        raise ForbiddenError("datetime.today")


def _install_traps(m: pytest.MonkeyPatch) -> None:
    """Make clock, randomness, environment, file and network entry points raise."""
    for name in (
        "time",
        "time_ns",
        "monotonic",
        "monotonic_ns",
        "perf_counter",
        "process_time",
        "sleep",
    ):
        m.setattr(time, name, _boom(f"time.{name}"))
    for name in ("random", "randint", "randrange", "choice", "shuffle", "getrandbits", "sample"):
        m.setattr(random, name, _boom(f"random.{name}"))
    m.setattr(uuid, "uuid4", _boom("uuid.uuid4"))
    m.setattr(uuid, "uuid1", _boom("uuid.uuid1"))
    m.setattr(os, "urandom", _boom("os.urandom"))
    m.setattr(os, "getenv", _boom("os.getenv"))
    m.setattr(os, "listdir", _boom("os.listdir"))
    m.setattr(builtins, "open", _boom("open"))
    m.setattr(socket, "socket", _boom("socket.socket"))
    m.setattr(socket, "create_connection", _boom("socket.create_connection"))
    m.setattr(datetime, "datetime", _ThrowingDatetime)


def test_the_traps_work(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        _install_traps(m)
        for call in (
            time.time,
            random.random,
            uuid.uuid4,
            lambda: os.getenv("X"),
            lambda: builtins.open("x"),  # noqa: PTH123, SIM115
        ):
            with pytest.raises(ForbiddenError):
                call()
        with pytest.raises(ForbiddenError):
            datetime.datetime.now(datetime.UTC)


def test_apply_runs_every_registered_type_with_clock_randomness_and_io_trapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = populated()
    expected = {
        t: canonical_json(
            ok_state(state, event(type=t, ts="2026-11-21T12:00:00.000Z")).to_json_value()
        )
        for t in EVENT_TYPES
    }
    with monkeypatch.context() as m:
        _install_traps(m)
        trapped = {}
        for t in EVENT_TYPES:
            result = apply(state, event(type=t, ts="2026-11-21T12:00:00.000Z"))
            assert isinstance(result, ApplyOk)
            trapped[t] = canonical_json(result.state.to_json_value())
    assert trapped == expected


def test_rejections_and_replay_work_with_clock_randomness_and_io_trapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as m:
        _install_traps(m)
        assert code(lobby(), event(type="nope")) == "unknown_event_type"
        assert code(lobby(), event(v=2)) == "unsupported_version"
        result = replay(lobby(), [event(), event(type="nope")])
        assert isinstance(result, ReplayError)
        assert (result.index, result.error) == (1, ReducerError("unknown_event_type"))
        assert canonical_json(populated().to_json_value())


def test_same_state_and_event_always_give_byte_identical_canonical_output() -> None:
    state = populated()
    e = event(type="life_changed", payload={"seat": 1, "delta": -3})
    first = canonical_bytes(ok_state(state, e).to_json_value())
    for _ in range(50):
        assert canonical_bytes(ok_state(state, e).to_json_value()) == first


def test_result_is_the_same_for_the_same_event_with_its_keys_in_another_order() -> None:
    a = event(payload={"x": 1, "y": 2})
    b = dict(reversed(list(a.items())))
    b["payload"] = {"y": 2, "x": 1}
    assert ok_state(lobby(), a) == ok_state(lobby(), b)


def test_ts_is_never_read_any_string_or_none_gives_the_same_state() -> None:
    state = populated()
    results = set()
    for ts in (
        None,
        "2026-11-21T12:00:00.000Z",
        "1970-01-01T00:00:00.000Z",
        "9999-12-31T23:59:59.999Z",
        "yesterday",
        "",
    ):
        e = event() if ts is None else event(ts=ts)
        results.add(canonical_json(ok_state(state, e).to_json_value()))
    assert len(results) == 1


def test_apply_mutates_neither_the_state_nor_the_event_on_accept_or_reject() -> None:
    state = populated()
    state_before = copy.deepcopy(state.model_dump(mode="json"))
    good = event(payload={"nested": {"list": [1, 2, {"k": "v"}]}}, ts="2026-11-21T12:00:00.000Z")
    good_before = copy.deepcopy(good)
    bad = event(v=2, payload={"deep": [[1]]})
    bad_before = copy.deepcopy(bad)

    assert isinstance(apply(state, good), ApplyOk)
    assert code(state, bad) == "unsupported_version"
    assert code(state, event(payload={"f": 1.5})) == "invalid_payload"

    assert state.model_dump(mode="json") == state_before
    assert good == good_before
    assert bad == bad_before


def test_apply_returns_a_new_state_and_leaves_the_input_state_unchanged() -> None:
    state = populated()
    result = ok_state(state, event())
    assert result is not state
    assert state.last_seq == 41
    assert result.last_seq == 42


def test_game_started_time_and_first_seat_are_accepted_from_the_payload_only() -> None:
    state = lobby()
    started = event(
        type="game_started", payload={"first_seat": 3, "started_at": "2026-11-21T12:00:00.000Z"}
    )
    result = ok_state(state, started)
    # Until S-6.1.6 the handler is a no-op: only last_seq changes (contract 4 and 6.2).
    assert result == state.model_copy(update={"last_seq": 1})
    other = ok_state(
        state,
        event(type="game_started", payload={"first_seat": 8, "started_at": "whenever"}),
    )
    assert other == result


# --- every registered type is a no-op -----------------------------------------------------------


def test_the_registry_has_29_types() -> None:
    assert len(EVENT_TYPES) == 29
    assert len(set(EVENT_TYPES)) == 29


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_every_registered_type_from_the_initial_state_only_advances_last_seq(
    event_type: str,
) -> None:
    state = lobby()
    assert ok_state(state, event(type=event_type)) == state.model_copy(update={"last_seq": 1})


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_every_registered_type_from_a_populated_state_only_advances_last_seq(
    event_type: str,
) -> None:
    state = populated()
    assert ok_state(state, event(type=event_type, seq=42)) == state.model_copy(
        update={"last_seq": 42}
    )


@pytest.mark.parametrize("phase", ["lobby", "active", "ending", "finished", "abandoned"])
def test_an_event_is_accepted_in_every_phase_because_game_closed_is_handler_level(
    phase: str,
) -> None:
    state = populated(phase=phase, resume_phase="active" if phase == "abandoned" else None)
    assert ok_state(state, event(type="life_changed")) == state.model_copy(update={"last_seq": 42})


def test_reduce_is_the_same_function_as_apply() -> None:
    assert reduce is apply


def test_replay_folds_apply_and_reports_the_first_rejection() -> None:
    done = replay(lobby(), [event(), event(), event()])
    assert isinstance(done, ReplayOk)
    assert done.state == lobby().model_copy(update={"last_seq": 3})
    stopped = replay(lobby(), [event(), event(seq=7), event(v=3)])
    assert isinstance(stopped, ReplayError)
    assert (stopped.index, stopped.error.code) == (1, "out_of_order")


# --- criterion 4 --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type",
    [
        "Turn_Passed",
        "TURN_PASSED",
        "turn_passed ",
        " turn_passed",
        "turn_passed\n",
        "turn-passed",
        "turn_pass",
        "turn_passed\u0000",
        "turn_p" + chr(0x430) + "ssed",
        "",
        "no_such_event",
        "__proto__",
        "constructor",
        "__class__",
        "__init__",
    ],
)
def test_unknown_event_type_is_rejected(event_type: str) -> None:
    assert code(lobby(), event(type=event_type)) == "unknown_event_type"


@pytest.mark.parametrize("v", [0, 2, -1, 100, 2**53 - 1, -(2**53 - 1)])
def test_unsupported_version_is_rejected(v: int) -> None:
    assert code(lobby(), event(v=v)) == "unsupported_version"


@pytest.mark.parametrize(
    "v",
    [
        2**53,
        -(2**53),
        10**30,
        1.5,
        1.0,
        float("nan"),
        float("inf"),
        True,
        False,
        None,
        "1",
        [1],
        {"v": 1},
    ],
    ids=repr,
)
def test_v_that_is_not_an_integer_in_the_safe_range_is_invalid_event(v: Any) -> None:
    assert code(lobby(), event(v=v)) == "invalid_event"


def test_missing_v_and_a_non_object_event_are_invalid_event() -> None:
    e = event()
    del e["v"]
    assert code(lobby(), e) == "invalid_event"
    bad_events: list[Any] = [None, 1, "x", [], True, ("a",)]
    for bad in bad_events:
        assert code(lobby(), bad) == "invalid_event"


@pytest.mark.parametrize("bad_type", [5, None, True, {"a": 1}, ["turn_passed"], 1.5])
def test_a_type_that_is_not_a_string_is_invalid_event(bad_type: Any) -> None:
    assert code(lobby(), event(type=bad_type)) == "invalid_event"


def test_version_is_checked_before_type() -> None:
    assert code(lobby(), event(v=0, type="no_such_event")) == "unsupported_version"
    assert code(lobby(), event(v=2, type=7)) == "unsupported_version"


def test_version_and_type_are_checked_before_the_rest_of_the_envelope_and_the_payload() -> None:
    malformed = {"v": 2, "type": "nope", "payload": {"a": 1.5}, "actor": "x"}
    assert code(lobby(), malformed) == "unsupported_version"
    assert code(lobby(), {**malformed, "v": 1}) == "unknown_event_type"


def test_a_rejected_event_leaves_the_callers_state_untouched() -> None:
    state = populated()
    before = state.model_dump(mode="json")
    assert code(state, event(v=2)) == "unsupported_version"
    assert state.model_dump(mode="json") == before


# --- payload canonicity for in-memory values (contract 6.2 step 5) ------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"a": 1.0},
        {"a": 1.5},
        {"a": float("nan")},
        {"a": float("inf")},
        {"a": 2**53},
        {"a": -(2**53)},
        {"a": [[{"b": 10**30}]]},
        {"a": chr(0xD800)},
        {chr(0xDC00): 1},
        {"a": b"bytes"},
        {"a": (1, 2)},
        {"a": {1, 2}},
    ],
    ids=repr,
)
def test_payload_that_is_not_a_canonical_value_is_invalid_payload(payload: dict[str, Any]) -> None:
    assert code(lobby(), event(payload=payload)) == "invalid_payload"


def test_payload_at_the_safe_integer_extremes_with_unicode_is_accepted() -> None:
    payload = {"hi": 2**53 - 1, "lo": -(2**53 - 1), "z": 0, "name": "Zoë שלום"}
    assert ok_state(lobby(), event(payload=payload)).last_seq == 1


def test_initial_state_keeps_out_of_range_settings_unchanged() -> None:
    settings = Settings(
        starting_life=5000, commander_damage_threshold=0, poison_threshold=100, trust_mode="banana"
    )
    assert initial_state(GAME_ID, settings).settings == settings


def test_initial_state_returns_independent_lists_and_dicts_on_every_call() -> None:
    a = initial_state(GAME_ID, DEFAULTS)
    a.seats.append(None)  # type: ignore[arg-type]
    a.voided.append(1)
    a.order.append(1)
    a.commander_damage["x"] = {}
    assert canonical_json(lobby().to_json_value()) == canonical_json(
        initial_state(GAME_ID, DEFAULTS).to_json_value()
    )
    assert initial_state(GAME_ID, DEFAULTS).seats == []
