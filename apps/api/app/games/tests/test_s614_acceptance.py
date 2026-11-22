"""Acceptance tests of S-6.1.4 (commander damage per commander source), the Python twin of
``packages/game-core/src/acceptance-s614.test.ts``: same cases, same numbers.

Written from the six acceptance criteria of the story (issue #208) and CONTRACT.md 3.1 to 3.3, 4.1,
4.2, 4.4.7, 4.4.8, 4.5.1, 4.5.2, 4.6, 5, 6 and 8, not from the handler. The shared vectors
(``test_vectors.py``) are the primary proof for everything reachable through events. This file adds
the criteria with their own numbers, built through real events from ``game_created``, the check
list and the example table parsed from the contract (so the document and the code cannot drift),
the phases no log reaches yet, purity, and an oracle written from the contract's words over seeded
generated cases.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest

from app.games.core import (
    REDUCER_ERROR_CODES,
    ApplyError,
    GameState,
    apply,
    initial_state,
    replay,
)
from app.games.core import canonical_json as canonical
from app.games.core.contract import Commander
from app.games.tests.lobby_helpers import GAME_ID, accept, lobby, make_event, reject_code
from app.games.tests.repo_files import VECTORS_DIR, contract_text, require

MAX_SAFE = 9007199254740991
ABSENT: Any = object()  # a payload key that is not written at all
ELIMINATION_REASONS = ("life", "commander_damage", "poison", "concession", "declared")


def ev(event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return make_event(event_type, payload)


def cd(source: Any, commander: Any, target: Any, amount: Any) -> dict[str, Any]:
    """``commander_damage {source_seat, commander, target_seat, amount}``."""
    payload: dict[str, Any] = {}
    for name, value in (
        ("source_seat", source),
        ("commander", commander),
        ("target_seat", target),
        ("amount", amount),
    ):
        if value is not ABSENT:
            payload[name] = value
    return ev("commander_damage", payload)


def lc(seat: int, delta: int) -> dict[str, Any]:
    return ev("life_changed", {"seat": seat, "delta": delta})


def concede(seat: int) -> dict[str, Any]:
    return ev("player_conceded", {"seat": seat})


def joined(seat: int) -> dict[str, Any]:
    return ev("seat_joined", {"seat": seat, "player_id": None, "name": f"Seat {seat}"})


def started(first: int = 1) -> dict[str, Any]:
    return ev("game_started", {"first_seat": first, "started_at": "2026-11-21T12:00:00.000Z"})


PARTNER_A = {"card_id": None, "name": "Partner A"}
PARTNER_B = {"card_id": "018f0000-0000-7000-8000-0000000000e2", "name": "Partner B"}
PARTNERS = [PARTNER_A, PARTNER_B]
SOLO = [{"card_id": None, "name": ""}]


def run(state: GameState, *events: dict[str, Any]) -> GameState:
    for event in events:
        state = accept(state, event)
    return state


def lobby_log(
    *,
    count: int = 4,
    threshold: int = 21,
    life: int = 40,
    partners: tuple[int, ...] = (),
    decks: tuple[int, ...] = (),
) -> list[dict[str, Any]]:
    """The lobby of a game as a real log: ``game_created``, the seats, the commanders, no start."""
    settings = {
        "starting_life": life,
        "commander_damage_threshold": threshold,
        "poison_threshold": 10,
        "trust_mode": "casual",
    }
    return [
        ev("game_created", {"settings": settings}),
        *(joined(i) for i in range(1, count + 1)),
        *(ev("commanders_set", {"seat": seat, "commanders": PARTNERS}) for seat in partners),
        *(
            ev(
                "deck_assigned",
                {"seat": seat, "deck_version_id": f"deck-{seat}", "commanders": PARTNERS},
            )
            for seat in decks
        ),
    ]


def lobby_of(**opts: Any) -> GameState:
    return run(lobby(), *lobby_log(**opts))


def game(**opts: Any) -> GameState:
    """A started game, seat 1 first, built from ``game_created``."""
    return run(lobby_of(**opts), started(1))


def raw_game(settings: dict[str, Any], count: int = 4) -> GameState:
    """The same game from the placeholder settings of ``initial_state`` (not validated)."""
    state = initial_state(GAME_ID, settings)
    return run(state, *(joined(i) for i in range(1, count + 1)), started(1))


def seat_of(state: GameState, seat_id: int) -> Any:
    return next(s for s in state.seats if s.seat == seat_id)


def in_game(state: GameState) -> list[int]:
    return [s.seat for s in state.seats if s.eliminated_at_seq is None]


def tallies(state: GameState, target: int) -> dict[str, int] | None:
    return state.commander_damage.get(str(target))


def commanders_of(state: GameState, seat_id: int) -> list[dict[str, Any]]:
    return [{"card_id": c.card_id, "name": c.name} for c in seat_of(state, seat_id).commanders]


def in_phase(state: GameState, phase: str) -> GameState:
    return state.model_copy(
        update={"phase": phase, "resume_phase": "active" if phase == "abandoned" else None}
    )


def with_seat(state: GameState, seat_id: int, **changes: Any) -> GameState:
    seats = [s.model_copy(update=changes) if s.seat == seat_id else s for s in state.seats]
    return state.model_copy(update={"seats": seats})


def out_at(state: GameState, seat_id: int, at_seq: int, round_: int = 1) -> GameState:
    """A seat eliminated by life loss at ``at_seq``, built by hand."""
    return with_seat(
        state,
        seat_id,
        life=0,
        elimination_reason="life",
        eliminated_by=None,
        eliminated_at_seq=at_seq,
        eliminated_round=round_,
        elimination_cause=None,
    )


def canon(state: GameState) -> str:
    return canonical(state.to_json_value())


def plain(state: GameState) -> dict[str, Any]:
    return copy.deepcopy(state.to_json_value())


def code_of(state: GameState, event: dict[str, Any]) -> str:
    """``ok`` when the event is accepted, otherwise the error code."""
    result = apply(state, event)
    return str(result.error.code) if isinstance(result, ApplyError) else "ok"


# --- parsing the contract -------------------------------------------------------------------------


def section(heading_prefix: str) -> str:
    lines = contract_text().split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(heading_prefix))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^#{1,4} ", lines[i]):
            end = i
            break
    return "\n".join(lines[start:end])


def numbered_codes(text: str) -> list[str]:
    """The first reducer error code in each numbered item of a text, in order."""
    out: list[str] = []
    for item in re.finditer(r"^\d+\. (.*)$", text, flags=re.MULTILINE):
        tokens = re.findall(r"`([a-z_]+)`", item.group(1))
        code = next((t for t in tokens if t in REDUCER_ERROR_CODES), None)
        if code is not None:
            out.append(code)
    return out


def table_rows(text: str) -> list[list[str]]:
    """The rows of the tables of a text whose first cell is a number, cells trimmed."""
    return [
        [cell.strip() for cell in line.split("|")[1:-1]]
        for line in text.split("\n")
        if re.match(r"^\| \d+ +\|", line)
    ]


def section_46() -> str:
    return section("### 4.6 ")


def order_text_46() -> str:
    text = section_46()
    return text[text.index("**Order of the checks.**") : text.index("Why check 5 comes before")]


def elimination_text_46() -> str:
    text = section_46()
    return text[
        text.index("**Elimination and the precedence of reasons.**") : text.index(
            "Tallies of different source keys"
        )
    ]


def checks_46() -> list[str]:
    return numbered_codes(order_text_46())


def reasons_46() -> list[str]:
    return re.findall(r"^\d+\. .*?reason `([a-z_]+)`", elimination_text_46(), flags=re.MULTILINE)


def rows_46() -> list[list[str]]:
    return table_rows(section_46())


# --- the contract text ----------------------------------------------------------------------------


def test_4_6_lists_seven_checks_shape_phase_seat_eliminated_amount_commander_numbers() -> None:
    assert checks_46() == [
        "invalid_payload",
        "invalid_phase",
        "unknown_seat",
        "seat_eliminated",
        "invalid_amount",
        "unknown_commander",
        "invalid_amount",
    ]


def test_4_6_starts_its_check_list_with_the_order_of_4_5_1() -> None:
    shared = numbered_codes(section("#### 4.5.1 "))
    assert checks_46()[: len(shared)] == shared


def test_4_6_names_the_two_reasons_the_event_can_give_commander_damage_first_then_life() -> None:
    assert reasons_46() == ["commander_damage", "life"]
    assert all(r in ELIMINATION_REASONS for r in reasons_46())


def test_4_5_2_gives_the_precedence_commander_damage_poison_life_4_6_skips_poison() -> None:
    bullet = next(
        ln for ln in section("#### 4.5.2 ").split("\n") if ln.startswith("- **Reason precedence")
    )
    tail = bullet[bullet.index("in the order") + len("in the order") :]
    order = re.findall(r"`([a-z_]+)`", tail[: tail.index(".")])
    assert order == ["commander_damage", "poison", "life"]
    assert [r for r in order if r in reasons_46()] == reasons_46()


def test_3_2_gives_commander_damage_threshold_the_range_1_to_99_and_the_default_21() -> None:
    row = next(
        ln
        for ln in section("### 3.2 ").split("\n")
        if ln.startswith("| `commander_damage_threshold`")
    )
    cells = [c.strip() for c in row.split("|")]
    assert (cells[3], cells[4]) == ("1 to 99", "21")
    assert lobby().settings.commander_damage_threshold == 21


def test_4_6_has_the_nineteen_example_rows_these_tests_are_built_from() -> None:
    assert [int(r[0]) for r in rows_46()] == list(range(1, 20))


def test_the_registry_row_of_commander_damage_has_the_four_payload_keys_of_the_story() -> None:
    row = next(
        ln
        for ln in contract_text().split("\n")
        if ln.startswith("| `commander_damage`") and "`source_seat`" in ln
    )
    assert "`source_seat`, `commander` (index), `target_seat`, `amount`" in row


# --- criterion 1 ----------------------------------------------------------------------------------


def hit() -> dict[str, Any]:
    return cd(1, 0, 3, 7)


def test_criterion_1_seat_1_has_one_commander_the_unnamed_default_index_0() -> None:
    assert commanders_of(game(), 1) == SOLO


def test_criterion_1_after_the_first_hit_the_tally_is_7_and_the_life_33() -> None:
    state = run(game(), hit())
    assert tallies(state, 3) == {"1:0": 7}
    assert (seat_of(state, 3).life, seat_of(state, 3).eliminated_at_seq) == (33, None)


def test_criterion_1_after_the_second_hit_the_tally_is_14_and_the_life_26() -> None:
    state = run(game(), hit(), hit())
    assert tallies(state, 3) == {"1:0": 14}
    assert (seat_of(state, 3).life, seat_of(state, 3).eliminated_at_seq) == (26, None)


def test_criterion_1_after_the_third_hit_21_and_19_eliminated_by_commander_damage_by_1() -> None:
    before = run(game(), hit(), hit())
    after = run(before, hit())
    assert after.commander_damage == {"3": {"1:0": 21}}
    seat = seat_of(after, 3)
    assert seat.life == 19
    assert seat.elimination_reason == "commander_damage"
    assert seat.eliminated_by == 1
    assert seat.eliminated_at_seq == after.last_seq == before.last_seq + 1
    assert seat.eliminated_round == 1
    assert seat.elimination_cause is None


def test_criterion_1_the_same_numbers_from_the_default_settings_of_initial_state() -> None:
    after = run(raw_game({}), hit(), hit(), hit())
    assert tallies(after, 3) == {"1:0": 21}
    seat = seat_of(after, 3)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (19, "commander_damage", 1)


def test_criterion_1_eliminated_at_seq_is_the_seq_the_event_carries() -> None:
    before = run(game(), hit(), hit())
    seq = before.last_seq + 1
    event = make_event("commander_damage", hit()["payload"], seq=seq)
    assert seat_of(accept(before, event), 3).eliminated_at_seq == seq


def test_criterion_1_the_other_seats_are_untouched_and_nobody_has_won_yet() -> None:
    before = game()
    after = run(before, hit(), hit(), hit())
    for seat_id in (1, 2, 4):
        assert seat_of(after, seat_id) == seat_of(before, seat_id)
    assert in_game(after) == [1, 2, 4]
    assert (after.no_winner, after.winner_seat, after.phase) == (False, None, "active")


def test_criterion_1_the_elimination_does_not_move_the_active_seat_the_turn_or_the_round() -> None:
    before = game()
    after = run(before, hit(), hit(), hit())
    assert (after.active_seat, after.turn, after.round) == (
        before.active_seat,
        before.turn,
        before.round,
    )
    assert (after.monarch, after.initiative, after.win_condition) == (None, None, None)


def test_criterion_1_a_fourth_hit_on_the_eliminated_seat_is_seat_eliminated() -> None:
    assert code_of(run(game(), hit(), hit(), hit()), hit()) == "seat_eliminated"


# --- criterion 2 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["commanders_set", "deck_assigned"])
def test_criterion_2_partners_deal_12_each_two_tallies_16_life_not_eliminated(how: str) -> None:
    before = game(partners=(2,)) if how == "commanders_set" else game(decks=(2,))
    assert commanders_of(before, 2) == PARTNERS
    assert seat_of(before, 2).commander_cast == [0, 0]
    after = run(before, cd(2, 0, 4, 12), cd(2, 1, 4, 12))
    assert after.commander_damage == {"4": {"2:0": 12, "2:1": 12}}
    seat = seat_of(after, 4)
    assert seat.life == 16
    assert (seat.elimination_reason, seat.eliminated_by, seat.eliminated_at_seq) == (
        None,
        None,
        None,
    )
    assert in_game(after) == [1, 2, 3, 4]
    assert after.no_winner is False


def test_criterion_2_the_order_of_the_two_hits_does_not_matter() -> None:
    base = game(partners=(2,))
    ab = run(base, cd(2, 0, 4, 12), cd(2, 1, 4, 12))
    ba = run(base, cd(2, 1, 4, 12), cd(2, 0, 4, 12))
    assert canon(ab) == canon(ba)


def test_criterion_2_24_from_one_source_in_two_tallies_never_adds_up_to_the_threshold() -> None:
    after = run(game(partners=(2,)), cd(2, 0, 4, 12), cd(2, 1, 4, 12))
    assert sum((tallies(after, 4) or {}).values()) == 24
    assert seat_of(after, 4).eliminated_at_seq is None


def test_criterion_2_9_more_from_a_makes_21_from_one_commander_eliminated_by_2() -> None:
    after = run(game(partners=(2,)), cd(2, 0, 4, 12), cd(2, 1, 4, 12), cd(2, 0, 4, 9))
    assert tallies(after, 4) == {"2:0": 21, "2:1": 12}
    seat = seat_of(after, 4)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (7, "commander_damage", 2)


def test_criterion_2_partner_b_alone_can_reach_the_threshold() -> None:
    after = run(game(partners=(2,)), cd(2, 1, 4, 21))
    assert tallies(after, 4) == {"2:1": 21}
    seat = seat_of(after, 4)
    assert (seat.elimination_reason, seat.eliminated_by) == ("commander_damage", 2)


def test_criterion_2_keys_are_told_apart_by_seat_and_index() -> None:
    after = run(game(partners=(2,)), cd(1, 0, 4, 3), cd(2, 0, 4, 4), cd(2, 1, 4, 5))
    assert tallies(after, 4) == {"1:0": 3, "2:0": 4, "2:1": 5}
    assert seat_of(after, 4).life == 28


# --- criterion 3 ----------------------------------------------------------------------------------


def expected_after(
    before: GameState,
    hit_: tuple[int, int, int, int],
    elimination_reason: str | None = None,
) -> dict[str, Any]:
    """The state a hit must give, from the words of 4.6: only these fields change."""
    source, commander, target, amount = hit_
    p = plain(before)
    row = p["commander_damage"].setdefault(str(target), {})
    key = f"{source}:{commander}"
    row[key] = row.get(key, 0) + amount
    seat = next(s for s in p["seats"] if s["seat"] == target)
    seat["life"] -= amount
    p["last_seq"] += 1
    if elimination_reason is not None:
        seat["elimination_reason"] = elimination_reason
        seat["eliminated_by"] = source
        seat["eliminated_at_seq"] = p["last_seq"]
        seat["eliminated_round"] = p["round"]
    return p


def test_criterion_3_one_hit_changes_the_tally_the_life_and_last_seq_and_nothing_else() -> None:
    before = game(partners=(2,))
    after = run(before, cd(2, 1, 4, 7))
    assert canon(after) == canonical(expected_after(before, (2, 1, 4, 7)))


def test_criterion_3_the_hit_that_eliminates_changes_only_its_documented_fields() -> None:
    before = run(game(), cd(1, 0, 3, 7), cd(1, 0, 3, 7))
    after = run(before, cd(1, 0, 3, 7))
    assert canon(after) == canonical(expected_after(before, (1, 0, 3, 7), "commander_damage"))


def test_criterion_3_a_second_hit_on_an_existing_row_leaves_the_other_keys_alone() -> None:
    before = run(game(partners=(2,)), cd(2, 0, 4, 5), cd(1, 0, 4, 2))
    after = run(before, cd(2, 0, 4, 6))
    assert canon(after) == canonical(expected_after(before, (2, 0, 4, 6)))
    assert tallies(after, 4) == {"2:0": 11, "1:0": 2}


AMOUNTS_3 = [7, 5, 9]


def log_without(skip: int | None) -> list[dict[str, Any]]:
    hits = [cd(1, 0, 3, a) for i, a in enumerate(AMOUNTS_3) if i != skip]
    return [*lobby_log(), started(1), *hits]


def replayed(skip: int | None) -> GameState:
    result = replay(lobby(), log_without(skip))
    assert result.ok, result
    return result.state  # type: ignore[union-attr]


def test_criterion_3_a_log_without_the_last_hit_rebuilds_the_state_before_it() -> None:
    before_the_hit = run(game(), cd(1, 0, 3, 7), cd(1, 0, 3, 5))
    without = replayed(2)
    assert canon(without) == canon(before_the_hit)
    seat = seat_of(without, 3)
    assert (seat.life, seat.eliminated_at_seq) == (28, None)
    assert tallies(without, 3) == {"1:0": 12}
    assert seat_of(replayed(None), 3).eliminated_at_seq is not None


@pytest.mark.parametrize("skip", [0, 1, 2])
def test_criterion_3_a_log_without_a_hit_has_tally_and_life_moved_together(skip: int) -> None:
    rebuilt = replayed(skip)
    total = sum(a for i, a in enumerate(AMOUNTS_3) if i != skip)
    assert tallies(rebuilt, 3) == {"1:0": total}
    seat = seat_of(rebuilt, 3)
    assert (seat.life, seat.eliminated_at_seq) == (40 - total, None)


def test_criterion_3_the_tally_and_the_life_move_together_in_every_state_of_the_log() -> None:
    state = game()
    for amount in (7, 5, 9):
        state = run(state, cd(1, 0, 3, amount))
        assert 40 - seat_of(state, 3).life == sum((tallies(state, 3) or {}).values())


# --- criterion 4 ----------------------------------------------------------------------------------


def test_criterion_4_tally_21_and_life_0_in_the_same_event_is_commander_damage() -> None:
    before = run(game(), lc(4, -19))
    assert seat_of(before, 4).life == 21
    after = run(before, cd(1, 0, 4, 21))
    assert tallies(after, 4) == {"1:0": 21}
    seat = seat_of(after, 4)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (0, "commander_damage", 1)
    assert seat.eliminated_at_seq == after.last_seq


def test_criterion_4_tally_25_and_life_minus_15_in_the_same_event_is_commander_damage() -> None:
    after = run(game(), lc(4, -30), cd(1, 0, 4, 25))
    assert tallies(after, 4) == {"1:0": 25}
    seat = seat_of(after, 4)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (-15, "commander_damage", 1)


def test_criterion_4_life_0_with_a_tally_of_5_is_life_eliminated_by_1() -> None:
    after = run(game(), lc(2, -35), cd(1, 0, 2, 5))
    assert tallies(after, 2) == {"1:0": 5}
    seat = seat_of(after, 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (0, "life", 1)
    assert seat.eliminated_at_seq == after.last_seq
    assert seat.elimination_cause is None


def test_criterion_4_life_minus_15_with_a_tally_of_20_is_life() -> None:
    after = run(game(), lc(2, -35), cd(1, 0, 2, 20))
    assert tallies(after, 2) == {"1:0": 20}
    seat = seat_of(after, 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (-15, "life", 1)


def test_criterion_4_a_tally_at_the_threshold_with_life_above_0_is_commander_damage() -> None:
    seat = seat_of(run(game(), cd(1, 0, 3, 21)), 3)
    assert (seat.life, seat.elimination_reason) == (19, "commander_damage")


def test_criterion_4_life_1_and_a_tally_below_the_threshold_is_no_elimination() -> None:
    seat = seat_of(run(game(), lc(2, -35), cd(1, 0, 2, 4)), 2)
    assert (seat.life, seat.eliminated_at_seq) == (1, None)


def test_criterion_4_two_partners_at_12_and_12_are_not_24() -> None:
    after = run(game(partners=(2,)), cd(2, 0, 4, 12), cd(2, 1, 4, 12))
    assert seat_of(after, 4).eliminated_at_seq is None


def test_criterion_4_two_source_seats_at_12_and_12_are_not_24() -> None:
    after = run(game(), cd(1, 0, 2, 12), cd(3, 0, 2, 12))
    assert tallies(after, 2) == {"1:0": 12, "3:0": 12}
    seat = seat_of(after, 2)
    assert (seat.life, seat.eliminated_at_seq) == (16, None)


def test_criterion_4_two_sources_at_20_take_40_life_and_the_reason_is_life_by_the_second() -> None:
    after = run(game(), cd(1, 0, 2, 20), cd(3, 0, 2, 20))
    assert tallies(after, 2) == {"1:0": 20, "3:0": 20}
    seat = seat_of(after, 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (0, "life", 3)
    assert seat.eliminated_at_seq == after.last_seq


def test_criterion_4_three_sources_at_20_each_with_999_life_stay_in_the_game() -> None:
    after = run(game(life=999), cd(1, 0, 2, 20), cd(3, 0, 2, 20), cd(4, 0, 2, 20))
    assert tallies(after, 2) == {"1:0": 20, "3:0": 20, "4:0": 20}
    seat = seat_of(after, 2)
    assert (seat.life, seat.eliminated_at_seq) == (939, None)


def test_criterion_4_poison_is_never_the_reason_of_this_event() -> None:
    after = run(with_seat(game(), 2, poison=12), cd(1, 0, 2, 1))
    seat = seat_of(after, 2)
    assert (seat.poison, seat.life, seat.eliminated_at_seq) == (12, 39, None)


def test_criterion_4_a_seat_eliminated_by_a_tally_of_25_takes_no_further_hit() -> None:
    assert code_of(run(game(), cd(1, 0, 3, 25)), cd(1, 0, 3, 1)) == "seat_eliminated"


def test_criterion_4_a_life_elimination_by_a_hit_in_ending_records_reason_and_eliminator() -> None:
    state = in_phase(run(game(), lc(2, -35)), "ending")
    seat = seat_of(run(state, cd(3, 0, 2, 5)), 2)
    assert (seat.elimination_reason, seat.eliminated_by) == ("life", 3)


# --- criterion 5 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("amount", [0, -1, -999, -1000, 1000, 1001, 1000000, MAX_SAFE, -MAX_SAFE])
def test_criterion_5_an_amount_outside_1_to_999_is_invalid_amount(amount: int) -> None:
    assert code_of(game(), cd(1, 0, 2, amount)) == "invalid_amount"


@pytest.mark.parametrize("amount", [1, 2, 998, 999])
def test_criterion_5_an_amount_from_1_to_999_is_accepted(amount: int) -> None:
    assert code_of(game(life=999), cd(1, 0, 2, amount)) == "ok"


def test_criterion_5_amount_1_and_amount_999_give_the_exact_tally_and_life() -> None:
    low = run(game(), cd(1, 0, 2, 1))
    assert (tallies(low, 2), seat_of(low, 2).life) == ({"1:0": 1}, 39)
    high = run(game(life=999), cd(1, 0, 2, 999))
    assert (tallies(high, 2), seat_of(high, 2).life) == ({"1:0": 999}, 0)
    assert seat_of(high, 2).elimination_reason == "commander_damage"


@pytest.mark.parametrize("amount", ["1", True, False, None, [], {}, 1.5, -0.5])
def test_criterion_5_an_amount_of_the_wrong_type_is_invalid_payload_not_invalid_amount(
    amount: Any,
) -> None:
    assert code_of(game(), cd(1, 0, 2, amount)) == "invalid_payload"


def test_criterion_5_an_amount_outside_the_safe_range_is_invalid_payload() -> None:
    assert code_of(game(), cd(1, 0, 2, MAX_SAFE + 1)) == "invalid_payload"
    assert code_of(game(), cd(1, 0, 2, -MAX_SAFE - 1)) == "invalid_payload"


def test_criterion_5_a_missing_amount_is_invalid_payload() -> None:
    assert code_of(game(), cd(1, 0, 2, ABSENT)) == "invalid_payload"


@pytest.mark.parametrize("commander", [1, 2, -1, 5, MAX_SAFE])
def test_criterion_5_a_commander_a_single_commander_seat_does_not_have(commander: int) -> None:
    assert code_of(game(), cd(1, commander, 2, 1)) == "unknown_commander"


@pytest.mark.parametrize("commander", [2, 3, -1, MAX_SAFE])
def test_criterion_5_a_commander_a_partner_seat_does_not_have(commander: int) -> None:
    assert code_of(game(partners=(2,)), cd(2, commander, 3, 1)) == "unknown_commander"


@pytest.mark.parametrize("commander", [0, 1])
def test_criterion_5_both_partner_commanders_are_accepted(commander: int) -> None:
    assert code_of(game(partners=(2,)), cd(2, commander, 3, 1)) == "ok"


def test_criterion_5_the_index_is_checked_against_the_source_single_hits_a_partner_seat() -> None:
    assert code_of(game(partners=(3,)), cd(1, 1, 3, 1)) == "unknown_commander"


def test_criterion_5_the_index_is_checked_against_the_source_partner_hits_a_single_seat() -> None:
    assert code_of(game(partners=(2,)), cd(2, 1, 3, 1)) == "ok"


def test_criterion_5_a_seat_never_given_a_commander_has_the_unnamed_default_index_0_only() -> None:
    state = game()
    assert code_of(state, cd(2, 0, 3, 1)) == "ok"
    assert code_of(state, cd(2, 1, 3, 1)) == "unknown_commander"


def test_criterion_5_commanders_replaced_by_one_commander_in_the_lobby_leave_only_index_0() -> None:
    lobby_state = run(
        lobby_of(partners=(2,)), ev("commanders_set", {"seat": 2, "commanders": SOLO})
    )
    state = run(lobby_state, started(1))
    assert code_of(state, cd(2, 1, 3, 1)) == "unknown_commander"
    assert code_of(state, cd(2, 0, 3, 1)) == "ok"


@pytest.mark.parametrize("commander", ["1", True, None, [], {}, 0.5])
def test_criterion_5_a_commander_of_the_wrong_type_is_invalid_payload(commander: Any) -> None:
    assert code_of(game(), cd(1, commander, 2, 1)) == "invalid_payload"


def test_criterion_5_a_missing_commander_is_invalid_payload() -> None:
    assert code_of(game(), cd(1, ABSENT, 2, 1)) == "invalid_payload"


def test_criterion_5_a_rejected_event_changes_nothing() -> None:
    state = run(game(partners=(2,)), cd(2, 0, 3, 4))
    snapshot = canon(state)
    for event in (cd(1, 1, 3, 1), cd(1, 0, 3, 0), cd(1, 0, 3, 1000), cd(1, 0, 7, 1)):
        assert isinstance(apply(state, event), ApplyError)
        assert canon(state) == snapshot


def test_criterion_5_an_amount_error_is_reported_before_a_commander_error() -> None:
    assert code_of(game(), cd(1, 5, 2, 1000)) == "invalid_amount"
    assert code_of(game(), cd(1, 5, 2, 0)) == "invalid_amount"


@pytest.mark.parametrize("source", [0, 9, -1, "1", None])
def test_criterion_5_a_source_seat_that_is_not_a_seat_id_is_invalid_payload(source: Any) -> None:
    assert code_of(game(), cd(source, 0, 2, 1)) == "invalid_payload"


def test_criterion_5_a_seat_that_is_not_at_the_table_is_unknown_seat() -> None:
    assert code_of(game(count=3), cd(4, 0, 2, 1)) == "unknown_seat"
    assert code_of(game(count=3), cd(1, 0, 4, 1)) == "unknown_seat"
    assert code_of(game(count=3), cd(5, 0, 4, 1)) == "unknown_seat"


# --- criterion 6 ----------------------------------------------------------------------------------


def settings_with(value: Any) -> dict[str, Any]:
    return {
        "starting_life": 40,
        "commander_damage_threshold": value,
        "poison_threshold": 10,
        "trust_mode": "casual",
    }


def test_criterion_6_the_default_is_21_a_tally_of_20_does_not_eliminate_and_21_does() -> None:
    assert lobby().settings.commander_damage_threshold == 21
    twenty = run(game(), cd(1, 0, 2, 7), cd(1, 0, 2, 7), cd(1, 0, 2, 6))
    assert tallies(twenty, 2) == {"1:0": 20}
    assert seat_of(twenty, 2).eliminated_at_seq is None
    seat = seat_of(run(twenty, cd(1, 0, 2, 1)), 2)
    assert (seat.elimination_reason, seat.eliminated_by) == ("commander_damage", 1)


def test_criterion_6_game_created_with_the_default_threshold_behaves_as_the_default() -> None:
    after = run(lobby(), *lobby_log(), started(1), cd(1, 0, 2, 21))
    assert after.settings.commander_damage_threshold == 21
    assert seat_of(after, 2).elimination_reason == "commander_damage"


def test_criterion_6_threshold_15_from_game_created_14_stays_15_eliminates_life_25() -> None:
    state = game(threshold=15)
    assert state.settings.commander_damage_threshold == 15
    fourteen = run(state, cd(1, 0, 2, 7), cd(1, 0, 2, 7))
    assert (seat_of(fourteen, 2).life, seat_of(fourteen, 2).eliminated_at_seq) == (26, None)
    after = run(state, cd(1, 0, 2, 7), cd(1, 0, 2, 8))
    assert tallies(after, 2) == {"1:0": 15}
    seat = seat_of(after, 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (25, "commander_damage", 1)
    assert seat.eliminated_at_seq == after.last_seq


def test_criterion_6_threshold_15_from_settings_changed_in_the_lobby_gives_the_same_result() -> (
    None
):
    lobby_state = run(lobby_of(), ev("settings_changed", {"commander_damage_threshold": 15}))
    after = run(lobby_state, started(1), cd(1, 0, 2, 7), cd(1, 0, 2, 8))
    seat = seat_of(after, 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (25, "commander_damage", 1)


def test_criterion_6_threshold_15_from_the_settings_of_initial_state_gives_the_same_result() -> (
    None
):
    after = run(raw_game({"commander_damage_threshold": 15}), cd(1, 0, 2, 7), cd(1, 0, 2, 8))
    assert seat_of(after, 2).elimination_reason == "commander_damage"


def test_criterion_6_with_threshold_15_7_and_8_from_two_commanders_is_no_elimination() -> None:
    after = run(game(threshold=15), cd(1, 0, 2, 7), cd(3, 0, 2, 8))
    seat = seat_of(after, 2)
    assert (seat.life, seat.eliminated_at_seq) == (25, None)


def test_criterion_6_with_threshold_15_partners_at_14_and_14_stay_and_15_from_one_eliminates() -> (
    None
):
    state = run(game(threshold=15, partners=(2,)), cd(2, 0, 4, 14), cd(2, 1, 4, 14))
    assert (seat_of(state, 4).life, seat_of(state, 4).eliminated_at_seq) == (12, None)
    after = run(state, cd(2, 1, 4, 1))
    assert tallies(after, 4) == {"2:0": 14, "2:1": 15}
    seat = seat_of(after, 4)
    assert (seat.elimination_reason, seat.eliminated_by) == ("commander_damage", 2)


def test_criterion_6_with_threshold_15_a_single_hit_of_20_eliminates() -> None:
    seat = seat_of(run(game(threshold=15), cd(1, 0, 2, 20)), 2)
    assert (seat.life, seat.elimination_reason) == (20, "commander_damage")


def test_criterion_6_threshold_1_the_first_point_eliminates_life_39() -> None:
    seat = seat_of(run(game(threshold=1), cd(1, 0, 2, 1)), 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (39, "commander_damage", 1)


def test_criterion_6_threshold_99_98_stays_one_more_eliminates() -> None:
    state = game(threshold=99, life=999)
    held = run(state, cd(1, 0, 2, 98))
    assert (seat_of(held, 2).life, seat_of(held, 2).eliminated_at_seq) == (901, None)
    after = run(held, cd(1, 0, 2, 1))
    assert tallies(after, 2) == {"1:0": 99}
    seat = seat_of(after, 2)
    assert (seat.life, seat.elimination_reason) == (900, "commander_damage")


@pytest.mark.parametrize("value", [1, 15, 21, 99])
def test_criterion_6_game_created_and_settings_changed_accept_the_threshold(value: int) -> None:
    assert code_of(lobby(), ev("game_created", {"settings": settings_with(value)})) == "ok"
    changed = ev("settings_changed", {"commander_damage_threshold": value})
    assert code_of(lobby(), changed) == "ok"


@pytest.mark.parametrize("value", [0, 100, -1, 101, 1000, MAX_SAFE, "21", True, None])
def test_criterion_6_a_threshold_outside_1_to_99_is_invalid_setting(value: Any) -> None:
    created = ev("game_created", {"settings": settings_with(value)})
    assert code_of(lobby(), created) == "invalid_setting"
    changed = ev("settings_changed", {"commander_damage_threshold": value})
    assert code_of(lobby(), changed) == "invalid_setting"


def test_criterion_6_a_rejected_threshold_leaves_the_settings_as_they_were() -> None:
    lobby_state = lobby_of(threshold=15)
    changed = ev("settings_changed", {"commander_damage_threshold": 100})
    assert reject_code(lobby_state, changed) == "invalid_setting"
    assert lobby_state.settings.commander_damage_threshold == 15


def test_criterion_6_the_threshold_cannot_change_once_the_game_started() -> None:
    changed = ev("settings_changed", {"commander_damage_threshold": 15})
    assert code_of(game(), changed) == "invalid_phase"


def test_criterion_6_an_integer_threshold_outside_1_to_99_in_an_unvalidated_state_is_used() -> None:
    zero = run(raw_game({"commander_damage_threshold": 0}), cd(1, 0, 2, 1))
    assert seat_of(zero, 2).elimination_reason == "commander_damage"
    huge = run(raw_game({"commander_damage_threshold": 100, "starting_life": 999}), cd(1, 0, 2, 99))
    assert seat_of(huge, 2).eliminated_at_seq is None


# --- phases ---------------------------------------------------------------------------------------


def test_phases_the_lobby_is_invalid_phase_and_a_lobby_event_changes_nothing() -> None:
    lobby_state = lobby_of()
    assert reject_code(lobby_state, cd(1, 0, 2, 5)) == "invalid_phase"
    assert lobby_state.commander_damage == {}
    assert [s.life for s in lobby_state.seats] == [40, 40, 40, 40]


@pytest.mark.parametrize("phase", ["finished", "abandoned"])
def test_phases_a_closed_game_is_invalid_phase(phase: str) -> None:
    assert code_of(in_phase(game(), phase), cd(1, 0, 2, 5)) == "invalid_phase"


def test_phases_active_accepts() -> None:
    assert code_of(game(), cd(1, 0, 2, 5)) == "ok"


def ending() -> GameState:
    return in_phase(game(), "ending")


def test_phases_ending_accepts_stays_in_ending_with_the_same_tally_and_life() -> None:
    after = run(ending(), cd(1, 0, 2, 5))
    assert after.phase == "ending"
    assert tallies(after, 2) == {"1:0": 5}
    assert seat_of(after, 2).life == 35


def test_phases_an_elimination_in_ending_records_reason_eliminator_and_seq() -> None:
    before = ending()
    after = run(before, cd(1, 0, 2, 21))
    seat = seat_of(after, 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (19, "commander_damage", 1)
    assert seat.eliminated_at_seq == before.last_seq + 1
    assert after.phase == "ending"


def test_phases_the_other_rules_hold_in_ending() -> None:
    assert code_of(ending(), cd(1, 0, 7, 1)) == "unknown_seat"
    assert code_of(ending(), cd(1, 0, 2, 0)) == "invalid_amount"
    assert code_of(ending(), cd(1, 1, 2, 1)) == "unknown_commander"
    assert code_of(ending(), cd(1, 0, 2, "1")) == "invalid_payload"
    assert code_of(run(ending(), concede(2)), cd(1, 0, 2, 1)) == "seat_eliminated"


# --- eliminated seats and self damage -------------------------------------------------------------


def test_an_eliminated_target_is_seat_eliminated_however_it_was_eliminated() -> None:
    assert code_of(run(game(), concede(2)), cd(1, 0, 2, 1)) == "seat_eliminated"
    assert code_of(run(game(), lc(2, -40)), cd(1, 0, 2, 1)) == "seat_eliminated"
    assert code_of(run(game(), cd(3, 0, 2, 21)), cd(1, 0, 2, 1)) == "seat_eliminated"
    assert code_of(out_at(game(), 2, 5), cd(1, 0, 2, 1)) == "seat_eliminated"


def test_an_eliminated_source_is_seat_eliminated_however_it_was_eliminated() -> None:
    assert code_of(run(game(), concede(2)), cd(2, 0, 1, 1)) == "seat_eliminated"
    assert code_of(run(game(), lc(2, -40)), cd(2, 0, 1, 1)) == "seat_eliminated"
    assert code_of(run(game(), cd(3, 0, 2, 21)), cd(2, 0, 1, 1)) == "seat_eliminated"


def test_an_eliminated_source_with_a_partner_index_it_has_is_also_seat_eliminated() -> None:
    state = run(game(partners=(2,)), concede(2))
    assert code_of(state, cd(2, 1, 1, 1)) == "seat_eliminated"


def test_seat_eliminated_comes_before_the_amount_and_the_commander_checks() -> None:
    state = run(game(), concede(2))
    assert code_of(state, cd(1, 0, 2, 0)) == "seat_eliminated"
    assert code_of(state, cd(1, 5, 2, 1)) == "seat_eliminated"
    assert code_of(state, cd(2, 0, 1, 1000)) == "seat_eliminated"
    assert code_of(state, cd(2, 5, 1, 1)) == "seat_eliminated"
    assert code_of(state, cd(2, 5, 2, 1000)) == "seat_eliminated"


def test_unknown_seat_comes_before_seat_eliminated_source_first_then_target() -> None:
    state = run(game(), concede(2))
    assert code_of(state, cd(1, 0, 7, 1)) == "unknown_seat"
    assert code_of(state, cd(7, 0, 2, 1)) == "unknown_seat"


def test_a_rejected_event_on_an_eliminated_seat_changes_nothing() -> None:
    state = run(game(), concede(2))
    snapshot = canon(state)
    apply(state, cd(1, 0, 2, 5))
    assert canon(state) == snapshot


def test_the_tallies_of_an_eliminated_seat_and_those_it_dealt_are_kept() -> None:
    after = run(game(), cd(3, 0, 2, 10), concede(3), cd(1, 0, 2, 4))
    assert tallies(after, 2) == {"3:0": 10, "1:0": 4}
    dealt = run(game(), cd(1, 0, 2, 21))
    assert tallies(dealt, 2) == {"1:0": 21}
    assert seat_of(dealt, 2).eliminated_at_seq is not None


def test_self_damage_is_legal_seat_2_hits_itself_with_5_tally_5_life_35() -> None:
    after = run(game(), cd(2, 0, 2, 5))
    assert after.commander_damage == {"2": {"2:0": 5}}
    seat = seat_of(after, 2)
    assert (seat.life, seat.eliminated_at_seq) == (35, None)


def test_self_damage_that_reaches_21_eliminates_with_eliminated_by_the_seat_itself() -> None:
    seat = seat_of(run(game(), cd(2, 0, 2, 21)), 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (19, "commander_damage", 2)


def test_self_damage_by_a_partner_uses_the_partner_key() -> None:
    after = run(game(partners=(2,)), cd(2, 1, 2, 21))
    assert after.commander_damage == {"2": {"2:1": 21}}
    assert seat_of(after, 2).eliminated_by == 2


def test_two_seats_left_eliminating_one_leaves_the_other_and_no_winner_is_false() -> None:
    after = run(game(count=2), cd(1, 0, 2, 21))
    assert in_game(after) == [1]
    assert (after.no_winner, after.winner_seat, after.phase) == (False, None, "active")


def test_the_last_seat_in_the_game_eliminating_itself_is_a_full_draw() -> None:
    after = run(game(count=2), concede(1), cd(2, 0, 2, 21))
    assert after.no_winner is True
    assert [s.eliminated_at_seq for s in after.seats] == [5, 6]
    assert after.phase == "active"


def test_after_a_full_draw_every_seat_naming_event_is_seat_eliminated() -> None:
    state = run(game(count=2), concede(1), cd(2, 0, 2, 21))
    assert code_of(state, cd(1, 0, 2, 1)) == "seat_eliminated"
    assert code_of(state, ev("turn_passed")) == "invalid_phase"


# --- check order ----------------------------------------------------------------------------------


def violation(index: int, state: GameState, payload: dict[str, Any]) -> tuple[GameState, Any]:
    """Make the check at ``index`` of the parsed list fail; the violations do not interfere."""
    if index == 0:  # invalid_payload
        return state, {**payload, "amount": "5"}
    if index == 1:  # invalid_phase
        return in_phase(state, "finished"), payload
    if index == 2:  # unknown_seat
        return state, {**payload, "source_seat": 7}
    if index == 3:  # seat_eliminated (the target)
        return out_at(state, 3, 5), payload
    if index == 4:  # invalid_amount (the amount)
        return state, {**payload, "amount": 0}
    if index == 5:  # unknown_commander
        return state, {**payload, "commander": 1}
    return with_seat(state, 3, life="40"), payload  # invalid_amount (numbers in the state)


def base_payload() -> dict[str, Any]:
    return {"source_seat": 1, "commander": 0, "target_seat": 3, "amount": 5}


def test_check_order_the_46_list_has_one_violation_maker_per_check() -> None:
    assert len(checks_46()) == 7


def test_check_order_every_set_of_simultaneous_violations_gives_the_earliest_check() -> None:
    order = checks_46()
    failures: list[str] = []
    for mask in range(1, 1 << len(order)):
        picked = [i for i in range(len(order)) if mask & (1 << i)]
        state, payload = game(), base_payload()
        # The latest check's violation first, so an earlier one that overwrites the same field wins.
        for i in reversed(picked):
            state, payload = violation(i, state, payload)
        actual = code_of(state, ev("commander_damage", payload))
        if actual != order[picked[0]]:
            failures.append(f"{'+'.join(str(i + 1) for i in picked)} gave {actual}")
    assert failures == []


@pytest.mark.parametrize("index", range(7))
def test_check_order_each_single_violation_gives_its_own_code(index: int) -> None:
    state, payload = violation(index, game(), base_payload())
    assert code_of(state, ev("commander_damage", payload)) == checks_46()[index]


def test_check_order_a_bad_payload_in_a_closed_game_is_invalid_payload() -> None:
    assert code_of(in_phase(game(), "finished"), cd(1, 0, 2, "x")) == "invalid_payload"
    assert code_of(in_phase(game(), "finished"), cd(1, 0, 7, 1)) == "invalid_phase"


# --- the 4.6 worked examples, row by row ----------------------------------------------------------


def result_cell(number: int) -> str:
    return rows_46()[number - 1][3]


def mentions(number: int, *values: int) -> None:
    for value in values:
        assert re.search(rf"(^|[^0-9])-?{value}([^0-9]|$)", result_cell(number)), (number, value)


def names_code(number: int, code: str) -> None:
    assert f"`{code}`" in result_cell(number)
    assert re.search(r"`(invalid_[a-z]+|seat_eliminated|unknown_[a-z]+)`", result_cell(number))


def names_no_code(number: int) -> None:
    assert (
        re.search(r"`(invalid_[a-z]+|seat_eliminated|unknown_[a-z]+)`", result_cell(number)) is None
    )


def test_4_6_row_1_three_hits_of_7() -> None:
    names_no_code(1)
    mentions(1, 7, 14, 21, 33, 26, 19)
    s1 = run(game(), cd(1, 0, 3, 7))
    s2 = run(s1, cd(1, 0, 3, 7))
    s3 = run(s2, cd(1, 0, 3, 7))
    assert [tallies(s, 3) for s in (s1, s2, s3)] == [{"1:0": 7}, {"1:0": 14}, {"1:0": 21}]
    assert [seat_of(s, 3).life for s in (s1, s2, s3)] == [33, 26, 19]
    assert [seat_of(s, 3).eliminated_at_seq for s in (s1, s2)] == [None, None]
    assert (seat_of(s3, 3).elimination_reason, seat_of(s3, 3).eliminated_by) == (
        "commander_damage",
        1,
    )


def test_4_6_row_2_partners_12_and_12() -> None:
    names_no_code(2)
    mentions(2, 12, 16)
    s = run(game(partners=(2,)), cd(2, 0, 4, 12), cd(2, 1, 4, 12))
    assert s.commander_damage == {"4": {"2:0": 12, "2:1": 12}}
    assert (seat_of(s, 4).life, seat_of(s, 4).eliminated_at_seq) == (16, None)


def test_4_6_row_3_nine_more_from_a() -> None:
    names_no_code(3)
    mentions(3, 21, 12, 7, 2)
    s = run(game(partners=(2,)), cd(2, 0, 4, 12), cd(2, 1, 4, 12), cd(2, 0, 4, 9))
    assert tallies(s, 4) == {"2:0": 21, "2:1": 12}
    seat = seat_of(s, 4)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (7, "commander_damage", 2)


def test_4_6_row_4_tally_21_and_life_0() -> None:
    names_no_code(4)
    mentions(4, 21, 0, 1)
    s = run(game(life=21), cd(1, 0, 4, 21))
    assert tallies(s, 4) == {"1:0": 21}
    seat = seat_of(s, 4)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (0, "commander_damage", 1)


def test_4_6_row_5_tally_25_and_life_minus_15() -> None:
    names_no_code(5)
    mentions(5, 25, 15)
    s = run(game(life=10), cd(1, 0, 4, 25))
    assert tallies(s, 4) == {"1:0": 25}
    assert (seat_of(s, 4).life, seat_of(s, 4).elimination_reason) == (-15, "commander_damage")


def test_4_6_row_6_life_0_by_5() -> None:
    names_no_code(6)
    mentions(6, 5, 0, 1)
    s = run(game(life=5), cd(1, 0, 2, 5))
    assert tallies(s, 2) == {"1:0": 5}
    seat = seat_of(s, 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (0, "life", 1)


def test_4_6_row_7_tally_20_below_21_life_minus_15() -> None:
    names_no_code(7)
    mentions(7, 20, 21, 15)
    s = run(game(life=5), cd(1, 0, 2, 20))
    assert tallies(s, 2) == {"1:0": 20}
    assert (seat_of(s, 2).life, seat_of(s, 2).elimination_reason) == (-15, "life")


def test_4_6_row_8_threshold_15() -> None:
    names_no_code(8)
    mentions(8, 14, 15, 25)
    a = run(game(threshold=15), cd(1, 0, 2, 7))
    b = run(a, cd(1, 0, 2, 8))
    c = run(a, cd(1, 0, 2, 7))
    assert tallies(c, 2) == {"1:0": 14}
    assert seat_of(c, 2).eliminated_at_seq is None
    assert tallies(b, 2) == {"1:0": 15}
    assert (seat_of(b, 2).life, seat_of(b, 2).elimination_reason) == (25, "commander_damage")


def test_4_6_row_9_self_damage_21() -> None:
    names_no_code(9)
    mentions(9, 21, 19, 2)
    s = run(game(), cd(2, 0, 2, 21))
    assert s.commander_damage == {"2": {"2:0": 21}}
    seat = seat_of(s, 2)
    assert (seat.life, seat.elimination_reason, seat.eliminated_by) == (19, "commander_damage", 2)


def test_4_6_row_10_a_life_gain_leaves_the_tally() -> None:
    names_no_code(10)
    mentions(10, 4)
    after = run(run(game(), cd(1, 0, 2, 4)), lc(2, 10))
    assert tallies(after, 2) == {"1:0": 4}
    assert seat_of(after, 2).life == 46


def test_4_6_row_11_unknown_commander() -> None:
    names_code(11, "unknown_commander")
    assert code_of(game(), cd(1, 1, 2, 1)) == "unknown_commander"


@pytest.mark.parametrize("amount", [0, -1, 1000])
def test_4_6_row_12_invalid_amount(amount: int) -> None:
    names_code(12, "invalid_amount")
    assert code_of(game(), cd(1, 0, 2, amount)) == "invalid_amount"


def test_4_6_row_13_invalid_amount_before_unknown_commander() -> None:
    names_code(13, "invalid_amount")
    assert code_of(game(), cd(1, 5, 2, 1000)) == "invalid_amount"


def test_4_6_row_14_seat_eliminated() -> None:
    names_code(14, "seat_eliminated")
    state = run(game(), concede(2))
    assert code_of(state, cd(1, 0, 2, 0)) == "seat_eliminated"
    assert code_of(state, cd(2, 5, 1, 1000)) == "seat_eliminated"


def test_4_6_row_15_starting_life_stored_as_a_string() -> None:
    names_code(15, "invalid_amount")
    assert code_of(raw_game({"starting_life": "40"}), cd(1, 0, 2, 1)) == "invalid_amount"


def test_4_6_row_16_threshold_stored_as_a_string() -> None:
    names_code(16, "invalid_amount")
    assert (
        code_of(raw_game({"commander_damage_threshold": "21"}), cd(1, 0, 2, 1)) == "invalid_amount"
    )


def test_4_6_row_17_threshold_stored_as_the_integer_0() -> None:
    names_no_code(17)
    mentions(17, 1)
    s = run(raw_game({"commander_damage_threshold": 0}), cd(1, 0, 2, 1))
    assert tallies(s, 2) == {"1:0": 1}
    seat = seat_of(s, 2)
    assert (seat.elimination_reason, seat.eliminated_by) == ("commander_damage", 1)


def test_4_6_row_18_lobby() -> None:
    names_code(18, "invalid_phase")
    assert code_of(lobby_of(), cd(1, 0, 2, 1)) == "invalid_phase"


def test_4_6_row_19_new_life_leaves_the_safe_range() -> None:
    names_code(19, "invalid_amount")
    assert code_of(with_seat(game(), 2, life=-MAX_SAFE), cd(1, 0, 2, 1)) == "invalid_amount"


# --- canonical order ------------------------------------------------------------------------------

ORDER_HITS = [(4, 1, 3, 2), (3, 0, 1, 3), (2, 0, 4, 4), (1, 0, 2, 5), (4, 0, 3, 6), (1, 0, 4, 7)]


def ordered_table() -> GameState:
    return game(partners=(4,))


def block(state: GameState) -> str:
    found = re.search(r'"commander_damage":(\{.*?\}),"control_mode"', canon(state))
    assert found is not None
    return found.group(1)


def test_canonical_order_lists_targets_by_seat_and_sources_by_seat_then_index() -> None:
    state = run(ordered_table(), *(cd(*h) for h in ORDER_HITS))
    assert block(state) == (
        '{"1":{"3:0":3},"2":{"1:0":5},"3":{"4:0":6,"4:1":2},"4":{"1:0":7,"2:0":4}}'
    )


def test_canonical_order_does_not_depend_on_the_order_the_entries_were_created_in() -> None:
    forward = run(ordered_table(), *(cd(*h) for h in ORDER_HITS))
    backward = run(ordered_table(), *(cd(*h) for h in reversed(ORDER_HITS)))
    rotated = run(ordered_table(), *(cd(*h) for h in [*ORDER_HITS[3:], *ORDER_HITS[:3]]))
    assert canon(backward) == canon(forward)
    assert canon(rotated) == canon(forward)


def test_canonical_order_seats_8_and_1_sort_by_code_point() -> None:
    state = run(game(count=8), cd(8, 0, 1, 1), cd(1, 0, 8, 2), cd(8, 0, 5, 3), cd(1, 0, 5, 4))
    assert block(state) == '{"1":{"8:0":1},"5":{"1:0":4,"8:0":3},"8":{"1:0":2}}'


def test_canonical_order_an_empty_table_is_braces_and_a_row_is_created_by_its_first_hit() -> None:
    assert block(game()) == "{}"
    assert list(run(game(), cd(1, 0, 2, 1)).commander_damage) == ["2"]


# --- purity ---------------------------------------------------------------------------------------

PURITY_EVENTS = [
    cd(1, 0, 2, 5),
    cd(1, 0, 2, 21),
    cd(2, 1, 3, 7),
    cd(2, 0, 2, 21),
    cd(1, 0, 2, 0),
    cd(1, 1, 2, 1),
    cd(7, 0, 2, 1),
    cd(1, 0, 2, "1"),
]


@pytest.mark.parametrize(
    "event", PURITY_EVENTS, ids=[f"event-{i}" for i in range(len(PURITY_EVENTS))]
)
def test_purity_neither_the_state_nor_the_event_is_mutated_accepted_or_rejected(
    event: dict[str, Any],
) -> None:
    state = game(partners=(2,))
    snapshot = canon(state)
    sent = copy.deepcopy(event)
    result = apply(state, sent)
    assert canon(state) == snapshot
    assert sent == event
    if not isinstance(result, ApplyError):
        assert result.state is not state


def test_purity_the_same_event_on_the_same_state_gives_the_same_result_twice() -> None:
    state = run(game(), cd(1, 0, 2, 4))
    first, second = apply(state, cd(1, 0, 2, 17)), apply(state, cd(1, 0, 2, 17))
    assert not isinstance(first, ApplyError)
    assert not isinstance(second, ApplyError)
    assert canon(first.state) == canon(second.state)


def test_purity_a_successful_hit_does_not_alias_the_rows_or_the_seats_of_the_input() -> None:
    state = run(game(), cd(1, 0, 2, 4))
    after = accept(state, cd(1, 0, 2, 4))
    assert after.commander_damage["2"] is not state.commander_damage["2"]
    assert after.seats is not state.seats
    assert tallies(state, 2) == {"1:0": 4}
    assert seat_of(state, 2).life == 36


def test_purity_a_hit_on_one_row_does_not_change_another_row_object_of_the_input() -> None:
    state = run(game(), cd(1, 0, 2, 4), cd(1, 0, 3, 5))
    row3 = state.commander_damage["3"]
    run(state, cd(1, 0, 2, 4))
    assert row3 == {"1:0": 5}


# --- the oracle -----------------------------------------------------------------------------------

CASES = 3000
PHASES = ["active", "active", "active", "active", "ending", "lobby", "finished", "abandoned"]
COUNTS = [4, 4, 3]
THRESHOLDS: list[Any] = [1, 2, 15, 20, 21, 21, 21, 22, 50, 98, 99, 0, 100, -5, "21", None, True]
LIVES: list[Any] = [
    -MAX_SAFE, -MAX_SAFE + 1, -MAX_SAFE + 5, -1000, -5, 0, 1, 5, 10, 21, 40, 40, 40, 100, 999,
    MAX_SAFE - 1, MAX_SAFE, "40", None, True,
]  # fmt: skip
PRIORS: list[Any] = [ABSENT, ABSENT, 0, 3, 10, 14, 15, 19, 20, 21, 98, MAX_SAFE - 3]
SOURCES: list[Any] = [1, 1, 1, 2, 2, 3, 3, 4, 4, 0, 9, "1", None, ABSENT]
TARGETS: list[Any] = [1, 2, 2, 2, 3, 3, 4, 4, 0, 9, "2", None, ABSENT]
COMMANDERS: list[Any] = [0, 0, 0, 0, 1, 1, 1, 2, -1, "0", None, 1.5, ABSENT]
AMOUNTS: list[Any] = [
    -MAX_SAFE, -1000, -1, 0, 1, 1, 2, 5, 7, 12, 20, 21, 21, 25, 98, 99, 100, 998, 999, 1000,
    MAX_SAFE, "5", None, True, 1.5, ABSENT,
]  # fmt: skip


class Generator:
    """Park-Miller: the same numbers in the TypeScript twin, so both run the same cases."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def next(self) -> int:
        self.seed = (self.seed * 48271) % 2147483647
        return self.seed

    def pick(self, items: list[Any]) -> Any:
        return items[self.next() % len(items)]


def build_case(gen: Generator, base: GameState) -> tuple[GameState, list[dict[str, Any]], str]:
    phase = gen.pick(PHASES)
    count = gen.pick(COUNTS)
    partner = [gen.next() % 3 == 0 for _ in range(4)]
    out = [gen.next() % 5 == 0 for _ in range(4)]
    lone = gen.next() % 6 == 0  # only seat 2 is left standing: its first hit is its own
    threshold = gen.pick(THRESHOLDS)
    life_seat = gen.pick([1, 2, 3, 4])
    life = gen.pick(LIVES)
    prior = gen.pick(PRIORS)
    prior_source = gen.pick([1, 2, 3])
    state = base
    if count == 3:
        state = state.model_copy(update={"seats": state.seats[:3], "order": [1, 2, 3]})
    for seat_id in range(1, count + 1):
        if partner[seat_id - 1]:
            pair = [
                Commander(card_id=None, name="Partner A"),
                Commander(card_id=str(PARTNER_B["card_id"]), name="Partner B"),
            ]
            state = with_seat(state, seat_id, commanders=pair, commander_cast=[0, 0])
    for seat_id in range(1, count + 1):
        if out[seat_id - 1] or (lone and seat_id != 2):
            state = out_at(state, seat_id, 3)
    state = state.model_copy(
        update={
            "settings": state.settings.model_copy(update={"commander_damage_threshold": threshold})
        }
    )
    if life_seat <= count:
        state = with_seat(state, life_seat, life=life)
    if prior is not ABSENT:
        state = state.model_copy(
            update={"commander_damage": {str(life_seat): {f"{prior_source}:0": prior}}}
        )
    state = in_phase(state, phase)
    hits: list[dict[str, Any]] = []
    for i in range(1 + gen.next() % 4):
        source = gen.pick(SOURCES)
        commander = gen.pick(COMMANDERS)
        target = gen.pick(TARGETS)
        amount = gen.pick(AMOUNTS)
        if lone and i == 0:
            source, target = 2, 2
        payload: dict[str, Any] = {}
        for name, value in (
            ("source_seat", source),
            ("commander", commander),
            ("target_seat", target),
            ("amount", amount),
        ):
            if value is not ABSENT:
                payload[name] = value
        hits.append(payload)
    shown_prior = "-" if prior is ABSENT else str(prior)
    label = (
        f"{phase} count={count} partner={partner} out={out} threshold={threshold!r} "
        f"life[{life_seat}]={life!r} prior={shown_prior}@{prior_source}:0"
    )
    return state, hits, label


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and abs(value) <= MAX_SAFE


def is_seat_id(value: Any) -> bool:
    return is_int(value) and 1 <= value <= 8


def oracle(before: dict[str, Any], payload: dict[str, Any]) -> str | dict[str, Any]:
    """The expected outcome, from the contract's words: an error code, or the expected state."""
    p = copy.deepcopy(before)
    s: Any = payload.get("source_seat")
    c: Any = payload.get("commander")
    t: Any = payload.get("target_seat")
    a: Any = payload.get("amount")
    # 1. payload shape
    if not (is_seat_id(s) and is_seat_id(t) and is_int(c) and is_int(a)):
        return "invalid_payload"
    # 2. phase
    if p["phase"] not in ("active", "ending"):
        return "invalid_phase"
    # 3. seats at the table, the source first
    seats = {x["seat"]: x for x in p["seats"]}
    if s not in seats or t not in seats:
        return "unknown_seat"
    source, target = seats[s], seats[t]
    # 4. eliminated target or source
    if target["eliminated_at_seq"] is not None or source["eliminated_at_seq"] is not None:
        return "seat_eliminated"
    # 5. the amount
    if a < 1 or a > 999:
        return "invalid_amount"
    # 6. the commander index of the source seat
    if c < 0 or c >= len(source["commanders"]):
        return "unknown_commander"
    # 7. numbers in the state
    threshold = p["settings"]["commander_damage_threshold"]
    row_before = p["commander_damage"].get(str(t), {})
    key = f"{s}:{c}"
    tally = row_before.get(key, 0) + a
    if not is_int(target["life"]) or not is_int(threshold):
        return "invalid_amount"
    if abs(target["life"] - a) > MAX_SAFE or abs(tally) > MAX_SAFE:
        return "invalid_amount"
    # the change
    standing = [x for x in p["seats"] if x["eliminated_at_seq"] is None]
    p["commander_damage"][str(t)] = {**row_before, key: tally}
    target["life"] = target["life"] - a
    p["last_seq"] += 1
    reason = None
    if tally >= threshold:
        reason = "commander_damage"
    elif target["life"] <= 0:
        reason = "life"
    if reason is not None:
        target["elimination_reason"] = reason
        target["eliminated_by"] = s
        target["eliminated_at_seq"] = p["last_seq"]
        target["eliminated_round"] = p["round"]
        target["elimination_cause"] = None
        if len(standing) == 1:
            p["no_winner"] = True
    return p


def test_the_reducer_agrees_with_the_oracle_on_3000_generated_cases_hit_after_hit() -> None:
    gen = Generator(614)
    base = game()
    seen: dict[str, int] = {}
    mismatches: list[str] = []

    def bump(kind: str) -> None:
        seen[kind] = seen.get(kind, 0) + 1

    for _ in range(CASES):
        state, hits, label = build_case(gen, base)
        for payload in hits:
            expected = oracle(plain(state), payload)
            result = apply(state, ev("commander_damage", payload))
            actual = (
                str(result.error.code) if isinstance(result, ApplyError) else canon(result.state)
            )
            want = expected if isinstance(expected, str) else canonical(expected)
            if actual != want:
                if len(mismatches) < 5:
                    mismatches.append(f"{label} {payload!r}\n  want {want}\n  got  {actual}")
                break
            if isinstance(expected, str):
                bump(expected)
                continue
            hit_seat = next(x for x in expected["seats"] if x["seat"] == payload["target_seat"])
            kind = "ok"
            if hit_seat["eliminated_at_seq"] == expected["last_seq"]:
                kind = f"ok_{hit_seat['elimination_reason']}"
                if expected["no_winner"] and not plain(state)["no_winner"]:
                    kind = "ok_full_draw"
            bump(kind)
            if payload["commander"] == 1:
                bump("ok_partner")
            if payload["source_seat"] == payload["target_seat"]:
                bump("ok_self")
            if len(expected["commander_damage"][str(payload["target_seat"])]) > 1:
                bump("ok_two_keys")
            assert not isinstance(result, ApplyError)
            state = result.state
    assert mismatches == []
    for kind in (
        "invalid_payload",
        "invalid_phase",
        "unknown_seat",
        "seat_eliminated",
        "invalid_amount",
        "unknown_commander",
        "ok",
        "ok_commander_damage",
        "ok_life",
        "ok_full_draw",
        "ok_partner",
        "ok_self",
        "ok_two_keys",
    ):
        assert seen.get(kind, 0) > 0, f"the generator never produced {kind}"


# --- the vectors are named after their content (8.6) and hold the criteria ------------------------

VECTOR_NAME = re.compile(r"^(accept|reject)-commander-damage-.*\.json$")

REJECT_NAME_CODES: list[tuple[str, str]] = [
    (r"^(unknown-seat|a-seat-that-left-the-lobby)$", "unknown_seat"),
    (r"^(source|target)-seat-not-at-the-table$", "unknown_seat"),
    (r"^(source|target)-seat-", "invalid_payload"),
    (r"^(eliminated-|from-an?-|on-an?-|self-damage-of-an-eliminated)", "seat_eliminated"),
    (r"^amount-(zero|negative-one|1000|minus-1000|a-million)$", "invalid_amount"),
    (r"^amount-", "invalid_payload"),
    (
        r"^commander-(1-of-a-single|2-of-a-partner|index-of-the-target|far-too-large|negative)",
        "unknown_commander",
    ),
    (r"^commander-1-after-the-partners-were-replaced$", "unknown_commander"),
    (r"^commander-", "invalid_payload"),
    (r"^unknown-commander$", "unknown_commander"),
    (r"^invalid-amount$", "invalid_amount"),
    (r"^invalid-phase$", "invalid_phase"),
    (r"^in-the-lobby$", "invalid_phase"),
    (r"^bad-payload$", "invalid_payload"),
    (r"^(life-is-|threshold-is-|new-life-below-)", "invalid_amount"),
    (r"^after-an-accepted-hit$", "invalid_amount"),
]


def code_from_name(stem: str) -> str | None:
    """The code a reject vector's name promises (the part before ``-before-``)."""
    head = re.sub(r"^reject-commander-damage-", "", stem).split("-before-")[0]
    return next((code for pattern, code in REJECT_NAME_CODES if re.search(pattern, head)), None)


def vector_files() -> list[str]:
    if not VECTORS_DIR.is_dir():
        return []
    return sorted(p.name for p in VECTORS_DIR.glob("*.json") if VECTOR_NAME.match(p.name))


def load_vector(file: str) -> dict[str, Any]:
    loaded = json.loads((VECTORS_DIR / file).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_vectors_there_are_at_least_90_for_the_type() -> None:
    require(VECTORS_DIR)
    assert len(vector_files()) >= 90


def test_vectors_every_vector_the_contract_names_exists() -> None:
    named = [
        f"{m}.json"
        for m in re.findall(r"`((?:accept|reject)-commander-damage-[a-z0-9-]+)`", contract_text())
    ]
    assert len(named) >= 2
    for file in named:
        assert (VECTORS_DIR / file).is_file(), file


@pytest.mark.parametrize("file", vector_files())
def test_vectors_a_vector_is_named_after_its_content(file: str) -> None:
    vector = load_vector(file)
    stem = file.removesuffix(".json")
    assert vector["name"] == stem
    events = vector["events"]
    if stem.startswith("accept-"):
        final = vector["expect"]["state"]
        assert any(e["type"] == "commander_damage" for e in events)
        eliminated = [s for s in final["seats"] if s["eliminated_at_seq"] is not None]
        created = next((e for e in events if e["type"] == "game_created"), None)
        changed = next((e for e in events if e["type"] == "settings_changed"), None)
        if re.search(r"-stays?-in-the-game|-stay-", stem):
            assert eliminated == []
        if re.search(
            r"(eliminates|eliminate|reason-is-commander-damage|reaching-the-threshold"
            r"|reached-by-two-hits)$",
            stem,
        ):
            assert any(s["elimination_reason"] == "commander_damage" for s in eliminated)
        if re.search(r"life-elimination$", stem):
            assert [s["elimination_reason"] for s in eliminated] == ["life"]
        if "partner" in stem:
            assert any(len(s["commanders"]) == 2 for s in final["seats"])
            assert any(
                e["type"] == "commander_damage" and e["payload"]["commander"] == 1 for e in events
            )
        if "self-damage" in stem:
            assert any(
                e["type"] == "commander_damage"
                and e["payload"]["source_seat"] == e["payload"]["target_seat"]
                for e in events
            )
        given = re.search(r"threshold-(\d+)", stem)
        if given is not None:
            number = int(given.group(1))
            assert final["settings"]["commander_damage_threshold"] == number
            via = [
                vector["settings"].get("commander_damage_threshold"),
                created["payload"]["settings"]["commander_damage_threshold"] if created else None,
                changed["payload"]["commander_damage_threshold"] if changed else None,
            ]
            assert number in via
        by_amount = re.search(r"amount-(\d+)$", stem)
        if by_amount is not None:
            assert any(
                e["type"] == "commander_damage"
                and e["payload"]["amount"] == int(by_amount.group(1))
                for e in events
            )
    else:
        error = vector["expect"]["error"]
        assert events[error["index"]]["type"] == "commander_damage"
        promised = code_from_name(stem)
        assert promised is not None, f"no code is promised by the name {stem}"
        assert error["code"] == promised


def final_state(file: str) -> dict[str, Any]:
    state = load_vector(file)["expect"]["state"]
    assert isinstance(state, dict)
    return state


def test_vectors_criterion_1_three_events_of_7_tally_21_life_19_eliminated_by_1() -> None:
    file = "accept-commander-damage-three-hits-of-7-from-one-commander-eliminate.json"
    hits = [e["payload"] for e in load_vector(file)["events"] if e["type"] == "commander_damage"]
    assert hits == [{"source_seat": 1, "commander": 0, "target_seat": 3, "amount": 7}] * 3
    final = final_state(file)
    assert final["commander_damage"] == {"3": {"1:0": 21}}
    seat = next(s for s in final["seats"] if s["seat"] == 3)
    assert seat["life"] == 19
    assert seat["elimination_reason"] == "commander_damage"
    assert seat["eliminated_by"] == 1
    assert seat["eliminated_at_seq"] == final["last_seq"]


def test_vectors_criterion_2_partners_deal_12_each_two_tallies_16_life_not_eliminated() -> None:
    file = "accept-commander-damage-partners-are-tracked-separately.json"
    hits = [e["payload"] for e in load_vector(file)["events"] if e["type"] == "commander_damage"]
    assert hits == [
        {"source_seat": 2, "commander": 0, "target_seat": 4, "amount": 12},
        {"source_seat": 2, "commander": 1, "target_seat": 4, "amount": 12},
    ]
    final = final_state(file)
    assert final["commander_damage"] == {"4": {"2:0": 12, "2:1": 12}}
    seat = next(s for s in final["seats"] if s["seat"] == 4)
    assert (seat["life"], seat["elimination_reason"], seat["eliminated_at_seq"]) == (16, None, None)
    assert len(next(s for s in final["seats"] if s["seat"] == 2)["commanders"]) == 2


def test_vectors_criterion_3_the_eliminating_event_changes_tally_life_and_fields_together() -> None:
    final = final_state(
        "accept-commander-damage-tally-and-life-and-elimination-change-in-one-event.json"
    )
    seat = next(s for s in final["seats"] if s["seat"] == 3)
    assert (seat["life"], seat["elimination_reason"]) == (19, "commander_damage")
    assert seat["eliminated_at_seq"] == final["last_seq"]
    assert final["commander_damage"] == {"3": {"1:0": 21}}


def test_vectors_criterion_4_tally_21_and_life_0_at_once_gives_commander_damage() -> None:
    final = final_state(
        "accept-commander-damage-threshold-and-life-at-once-the-reason-is-commander-damage.json"
    )
    assert final["commander_damage"] == {"4": {"1:0": 21}}
    seat = next(s for s in final["seats"] if s["seat"] == 4)
    assert (seat["life"], seat["elimination_reason"], seat["eliminated_by"]) == (
        0,
        "commander_damage",
        1,
    )


def test_vectors_criterion_5_amounts_0_minus_1_1000_and_commanders_1_and_2_are_rejected() -> None:
    wanted = {
        "reject-commander-damage-amount-zero": "invalid_amount",
        "reject-commander-damage-amount-negative-one": "invalid_amount",
        "reject-commander-damage-amount-1000": "invalid_amount",
        "reject-commander-damage-commander-1-of-a-single-commander-seat": "unknown_commander",
        "reject-commander-damage-commander-2-of-a-partner-seat": "unknown_commander",
    }
    for stem, code in wanted.items():
        assert load_vector(f"{stem}.json")["expect"]["error"]["code"] == code, stem


def test_vectors_criterion_6_threshold_15_reached_by_7_and_8_with_14_one_short() -> None:
    final = final_state("accept-commander-damage-threshold-15-reached-by-two-hits.json")
    assert final["settings"]["commander_damage_threshold"] == 15
    assert final["commander_damage"] == {"2": {"1:0": 15}}
    seat = next(s for s in final["seats"] if s["seat"] == 2)
    assert (seat["life"], seat["elimination_reason"]) == (25, "commander_damage")
    short = final_state("accept-commander-damage-threshold-15-one-short-stays-in-the-game.json")
    assert short["commander_damage"] == {"2": {"1:0": 14}}


def test_vectors_the_five_kinds_of_task_t_6_1_4_3_each_have_a_vector() -> None:
    files = vector_files()

    def has(pattern: str) -> bool:
        return any(re.search(pattern, f) for f in files)

    assert has(r"^accept-commander-damage-three-hits")  # single commander to 21
    assert has(r"^accept-commander-damage-partners-are-tracked-separately")
    assert has(r"^accept-commander-damage-threshold-and-life-at-once")  # precedence
    assert has(r"^reject-commander-damage-(source-seat-not-at-the-table|commander-1-of-a-single)")
    assert has(r"^accept-commander-damage-threshold-15")  # a non-default threshold
