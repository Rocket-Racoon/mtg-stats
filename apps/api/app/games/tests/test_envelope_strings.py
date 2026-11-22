"""Review rule (CONTRACT.md section 2 "Well-formed strings", 6.2 steps 3 to 7), Python side.

Every envelope string is free of lone surrogates (``invalid_event``; a ``type`` with one is
``unknown_event_type``) and a payload with one is ``invalid_payload``. Python holds a surrogate
as an ordinary code point, so any ``str`` with a code point in U+D800 to U+DFFF is lone. The shared
vectors ``reject-*lone-surrogate*`` and ``reject-precedence-lone-surrogate-*`` run the same cases
on both sides; the wire reader (5.6) rejects the same escapes before ``apply``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.games.core import (
    ApplyError,
    ApplyOk,
    GameState,
    WireParseError,
    apply,
    canonical_json,
    initial_state,
    loads_wire,
)

GAME_ID = "018f0000-0000-7000-8000-000000000001"
HIGH = chr(0xD800)
LOW = chr(0xDC00)
PAIR_AS_CODE_POINT = chr(0x10000)


def event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event_id": "00000000-0000-4000-8000-000000000001",
        "game_id": GAME_ID,
        "type": "turn_passed",
        "payload": {},
        "actor": {"kind": "player", "player_id": "p1"},
        "on_behalf_of_seat": None,
        "v": 1,
    }
    base.update(overrides)
    return base


def lobby() -> GameState:
    return initial_state(GAME_ID)


def code(raw: Any) -> str:
    result = apply(lobby(), raw)
    return "ok" if isinstance(result, ApplyOk) else str(result.error.code)


BAD_STRINGS = [
    ("lone-high", "a" + HIGH),
    ("lone-low", LOW + "a"),
    ("low-then-high", LOW + HIGH),
    ("two-highs", HIGH + HIGH),
    ("high-before-bmp", HIGH + "x"),
    ("highest-high", chr(0xDBFF)),
    ("highest-low", chr(0xDFFF)),
    # In Python a valid pair written as two code points is still two surrogates (contract 5.4).
    ("pair-as-two-surrogates", HIGH + LOW),
]
IDS = [name for name, _ in BAD_STRINGS]
TEXTS = [text for _, text in BAD_STRINGS]


@pytest.mark.parametrize("bad", TEXTS, ids=IDS)
def test_lone_surrogate_in_game_id_is_invalid_event(bad: str) -> None:
    assert code(event(game_id=bad)) == "invalid_event"


@pytest.mark.parametrize("bad", TEXTS, ids=IDS)
def test_lone_surrogate_in_ts_is_invalid_event(bad: str) -> None:
    assert code(event(ts=bad)) == "invalid_event"


@pytest.mark.parametrize("bad", TEXTS, ids=IDS)
def test_lone_surrogate_in_a_player_id_is_invalid_event(bad: str) -> None:
    assert code(event(actor={"kind": "player", "player_id": bad})) == "invalid_event"


@pytest.mark.parametrize("bad", TEXTS, ids=IDS)
def test_lone_surrogate_in_event_id_is_invalid_event(bad: str) -> None:
    assert code(event(event_id=bad)) == "invalid_event"


@pytest.mark.parametrize("bad", TEXTS, ids=IDS)
def test_lone_surrogate_in_type_is_unknown_event_type(bad: str) -> None:
    assert code(event(type=bad)) == "unknown_event_type"


@pytest.mark.parametrize("bad", TEXTS, ids=IDS)
def test_lone_surrogate_in_a_payload_value_is_invalid_payload(bad: str) -> None:
    assert code(event(payload={"k": bad})) == "invalid_payload"


@pytest.mark.parametrize("bad", TEXTS, ids=IDS)
def test_lone_surrogate_in_a_payload_key_is_invalid_payload(bad: str) -> None:
    assert code(event(payload={bad: 1})) == "invalid_payload"


def test_astral_characters_are_accepted_in_every_envelope_string_and_the_payload() -> None:
    game_id = "g-" + PAIR_AS_CODE_POINT
    state = initial_state(game_id)
    result = apply(
        state,
        event(
            game_id=game_id,
            ts="t" + PAIR_AS_CODE_POINT,
            actor={"kind": "player", "player_id": "p" + PAIR_AS_CODE_POINT},
            payload={"k" + PAIR_AS_CODE_POINT: [PAIR_AS_CODE_POINT]},
        ),
    )
    assert isinstance(result, ApplyOk)
    assert result.state == state.model_copy(update={"last_seq": 1})


def test_the_text_of_a_surrogate_escape_is_ordinary_text() -> None:
    text = chr(92) + "ud800"
    assert code(event(ts=text, payload={"a": text})) == "ok"


# --- precedence (6.2) ---------------------------------------------------------------------------


def test_bad_or_unsupported_version_beats_a_lone_surrogate() -> None:
    assert code(event(v=2, game_id=HIGH)) == "unsupported_version"
    assert code(event(v=True, game_id=HIGH)) == "invalid_event"


def test_unknown_type_beats_a_lone_surrogate_in_another_string() -> None:
    assert code(event(type="nope", ts=HIGH)) == "unknown_event_type"
    assert code(event(type="nope" + HIGH, extra=1)) == "unknown_event_type"


def test_a_type_that_is_not_a_string_is_invalid_event_before_the_string_rule() -> None:
    assert code(event(type=5, ts=HIGH)) == "invalid_event"


def test_envelope_string_rule_beats_a_bad_payload() -> None:
    assert code(event(ts=HIGH, payload={"a": 1.5})) == "invalid_event"
    assert code(event(ts=HIGH, payload={"a": HIGH})) == "invalid_event"
    bad_actor = {"kind": "player", "player_id": HIGH}
    assert code(event(actor=bad_actor, payload={"a": 2**53})) == "invalid_event"


def test_envelope_string_rule_beats_game_mismatch_and_out_of_order() -> None:
    assert code(event(game_id="other" + HIGH)) == "invalid_event"
    assert code(event(game_id="other-game", ts=HIGH)) == "invalid_event"
    assert code(event(seq=5, ts=HIGH)) == "invalid_event"


def test_payload_rule_beats_game_mismatch_and_out_of_order() -> None:
    assert code(event(game_id="other-game", payload={"a": HIGH})) == "invalid_payload"
    assert code(event(seq=5, payload={"a": LOW})) == "invalid_payload"


def test_a_rejected_event_leaves_the_state_unchanged() -> None:
    state = lobby()
    before = canonical_json(state.to_json_value())
    assert isinstance(apply(state, event(ts=HIGH)), ApplyError)
    assert canonical_json(state.to_json_value()) == before


# --- the wire reader (5.6) ----------------------------------------------------------------------


def escaped(*units: int) -> str:
    """A JSON string literal with the given UTF-16 code units written as ``\\uXXXX`` escapes."""
    return '"' + "".join(f"{chr(92)}u{unit:04x}" for unit in units) + '"'


@pytest.mark.parametrize(
    "text",
    [
        escaped(0xD800),
        escaped(0xDC00),
        escaped(0xDBFF),
        escaped(0xDFFF),
        escaped(0xDE00, 0xD83D),  # low then high
        escaped(0xD83D, 0xD83D),  # two highs
        escaped(0xD83D, 0x61),  # high then a BMP character
        escaped(0x61, 0xDE00),  # low without a high before it
        escaped(0xD83D, 0xDE00, 0xDE00),  # a valid pair then a stray low
    ],
    ids=range(9),
)
def test_wire_reader_rejects_a_lone_surrogate_escape_in_a_value_key_or_nesting(text: str) -> None:
    for document in (text, "[" + text + "]", '{"a":' + text + "}", "{" + text + ":1}"):
        with pytest.raises(WireParseError) as raised:
            loads_wire(document)
        assert raised.value.code == "invalid_event"
    with pytest.raises(WireParseError):
        loads_wire(text.encode("utf-8"))
    with pytest.raises(WireParseError):
        loads_wire('{"x":{"y":[1,{"z":[' + text + "]}]}}")


def test_wire_reader_rejects_a_lone_surrogate_in_every_envelope_string() -> None:
    for key in ("event_id", "game_id", "type", "ts"):
        body = json.dumps({**event(), key: "x" + HIGH})  # json.dumps escapes the surrogate
        assert "\\ud800" in body
        with pytest.raises(WireParseError):
            loads_wire(body)
    body = json.dumps(event(actor={"kind": "player", "player_id": HIGH}))
    with pytest.raises(WireParseError):
        loads_wire(body)
    with pytest.raises(WireParseError):
        loads_wire(json.dumps(event(payload={"a": [HIGH]})))


def test_wire_reader_accepts_valid_pairs_in_escaped_and_raw_form() -> None:
    pair = chr(0x1F600)
    assert loads_wire(escaped(0xD83D, 0xDE00)) == pair
    assert loads_wire(json.dumps(pair, ensure_ascii=False)) == pair
    assert loads_wire(json.dumps({pair: [pair]})) == {pair: [pair]}
    assert loads_wire(escaped(0xD83D, 0xDE00, 0xD83D, 0xDE01)) == pair + chr(0x1F601)
    assert loads_wire(escaped(0xDBFF, 0xDFFF)) == chr(0x10FFFF)


def test_wire_reader_accepts_text_that_only_looks_like_an_escape() -> None:
    assert loads_wire('"' + chr(92) * 2 + 'ud800"') == chr(92) + "ud800"


def test_an_accepted_wire_event_reaches_apply_as_well_formed() -> None:
    event_with_pair = event(payload={"name": chr(0x1F600)})
    body = json.dumps(event_with_pair)
    assert "\\ud83d\\ude00" in body
    result = apply(lobby(), loads_wire(body))
    assert isinstance(result, ApplyOk)
