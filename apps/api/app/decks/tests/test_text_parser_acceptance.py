"""Acceptance tests for S-4.1.1 (decklist text parser), written from the story criteria."""

from collections.abc import Callable

import pytest

from app.decks.services import Board, ParsedLine, parse_decklist

C, M, S, MB, CO = (
    Board.COMMANDER,
    Board.MAIN,
    Board.SIDEBOARD,
    Board.MAYBEBOARD,
    Board.COMPANION,
)
ATRAXA = "Atraxa, Praetors' Voice"


def _cards(text: str) -> list[tuple[int, str, str, str, Board, int]]:
    return [
        (p.quantity, p.name, p.set_code, p.collector_number, p.board, p.line_number)
        for p in parse_decklist(text).lines
    ]


# --- Criterion 1: quantity forms ---------------------------------------------------------


@pytest.mark.parametrize("line", ["1 Sol Ring", "1x Sol Ring", "Sol Ring"])
def test_quantity_forms_all_yield_one_sol_ring(line: str) -> None:
    result = parse_decklist(line)
    assert result.unparsed_lines == []
    assert result.lines == [ParsedLine(1, "Sol Ring", "", "", M, 1)]


def test_missing_set_and_collector_number_are_empty_strings() -> None:
    (p,) = parse_decklist("Sol Ring").lines
    assert p.set_code == ""
    assert p.collector_number == ""


def test_quantity_greater_than_one_is_kept() -> None:
    assert _cards("4x Lightning Bolt\n12 Island") == [
        (4, "Lightning Bolt", "", "", M, 1),
        (12, "Island", "", "", M, 2),
    ]


# --- Criterion 2: set code, collector number, foil marker --------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "1 Sol Ring (C21) 263",
        "1 Sol Ring (C21) 263 *F*",
        "1x Sol Ring (C21) 263 *F*",
        "Sol Ring (C21) 263",
    ],
)
def test_set_code_and_collector_number_are_extracted_and_foil_marker_dropped(line: str) -> None:
    result = parse_decklist(line)
    assert result.unparsed_lines == []
    assert result.lines == [ParsedLine(1, "Sol Ring", "C21", "263", M, 1)]


@pytest.mark.parametrize("line", ["1 Sol Ring (C21) 263", "1 Sol Ring (C21) 263 *F*"])
def test_name_contains_no_marker_or_set_text(line: str) -> None:
    (p,) = parse_decklist(line).lines
    for leaked in ("*", "F*", "(", ")", "C21", "263"):
        assert leaked not in p.name


# --- Criterion 3: section headers --------------------------------------------------------

HEADER_BOARDS = [
    ("commander", C),
    ("deck", M),
    ("sideboard", S),
    ("maybeboard", MB),
    ("companion", CO),
]
HEADER_VARIANTS = ["{}", "{}:", "{} (1)", "{}(1)", "{} (12)", "{}: (1)", "{} (1):"]


@pytest.mark.parametrize(("word", "board"), HEADER_BOARDS)
@pytest.mark.parametrize("variant", HEADER_VARIANTS)
@pytest.mark.parametrize("case", [str.lower, str.upper, str.title])
def test_header_variant_assigns_following_cards_to_matching_board(
    word: str, board: Board, variant: str, case: Callable[[str], str]
) -> None:
    header = variant.format(case(word))
    result = parse_decklist(f"{header}\n1 Sol Ring\n2 Island")
    assert result.unparsed_lines == []
    assert [(p.name, p.board, p.line_number) for p in result.lines] == [
        ("Sol Ring", board, 2),
        ("Island", board, 3),
    ]


def test_oddly_cased_header_is_recognised() -> None:
    assert _cards("sIdEbOaRd:\n1 Sol Ring") == [(1, "Sol Ring", "", "", S, 2)]


def test_all_five_headers_in_one_list_assign_each_card_to_its_board() -> None:
    text = (
        f"Commander (1)\n1 {ATRAXA}\n"
        "Deck:\n1 Sol Ring\n"
        "SIDEBOARD\n1 Rhystic Study\n"
        "maybeboard (1):\n1 Mana Crypt\n"
        "Companion\n1 Lurrus of the Dream-Den\n"
    )
    assert [(p.name, p.board, p.line_number) for p in parse_decklist(text).lines] == [
        (ATRAXA, C, 2),
        ("Sol Ring", M, 4),
        ("Rhystic Study", S, 6),
        ("Mana Crypt", MB, 8),
        ("Lurrus of the Dream-Den", CO, 10),
    ]


def test_header_lines_are_not_reported_as_cards_or_unparsed() -> None:
    result = parse_decklist("Commander\n1 Sol Ring\nSideboard (1)\n1 Island")
    assert result.unparsed_lines == []
    assert [p.name for p in result.lines] == ["Sol Ring", "Island"]


def test_blank_lines_between_headed_sections_do_not_switch_to_sideboard() -> None:
    text = "Commander\n1 Atraxa\n\nDeck\n1 Sol Ring\n\n1 Island\n\nSideboard\n1 Rhystic Study\n"
    assert _cards(text) == [
        (1, "Atraxa", "", "", C, 2),
        (1, "Sol Ring", "", "", M, 5),
        (1, "Island", "", "", M, 7),
        (1, "Rhystic Study", "", "", S, 10),
    ]


def test_blank_line_inside_deck_section_keeps_main_board() -> None:
    assert _cards("Deck\n1 Sol Ring\n\n1 Island") == [
        (1, "Sol Ring", "", "", M, 2),
        (1, "Island", "", "", M, 4),
    ]


# --- Criterion 4: MTGO sideboard forms ---------------------------------------------------


@pytest.mark.parametrize("prefix", ["SB: ", "SB:", "sb: ", "Sb:  "])
def test_sb_prefix_sends_card_to_sideboard(prefix: str) -> None:
    text = f"4 Lightning Bolt\n3 Goblin Guide\n{prefix}2 Smash to Smithereens\n{prefix}1 Pyroblast"
    assert _cards(text) == [
        (4, "Lightning Bolt", "", "", M, 1),
        (3, "Goblin Guide", "", "", M, 2),
        (2, "Smash to Smithereens", "", "", S, 3),
        (1, "Pyroblast", "", "", S, 4),
    ]


def test_sb_prefix_name_has_no_prefix_left_in_it() -> None:
    (p,) = parse_decklist("SB: 2 Smash to Smithereens").lines
    assert p.name == "Smash to Smithereens"
    assert p.quantity == 2


def test_single_blank_line_after_main_deck_starts_sideboard() -> None:
    text = (
        "4 Lightning Bolt\n4 Goblin Guide\n3 Monastery Swiftspear\n\n"
        "2 Smash to Smithereens\n1 Pyroblast"
    )
    assert _cards(text) == [
        (4, "Lightning Bolt", "", "", M, 1),
        (4, "Goblin Guide", "", "", M, 2),
        (3, "Monastery Swiftspear", "", "", M, 3),
        (2, "Smash to Smithereens", "", "", S, 5),
        (1, "Pyroblast", "", "", S, 6),
    ]


def test_single_blank_line_sideboard_with_windows_line_endings() -> None:
    text = "4 Lightning Bolt\r\n\r\n2 Smash to Smithereens\r\n"
    assert _cards(text) == [
        (4, "Lightning Bolt", "", "", M, 1),
        (2, "Smash to Smithereens", "", "", S, 3),
    ]


def test_cards_before_the_blank_line_stay_in_main() -> None:
    result = parse_decklist("1 Sol Ring\n1 Island\n\n1 Pyroblast")
    assert [p.board for p in result.lines] == [M, M, S]


# --- Criterion 5: unparsed lines ---------------------------------------------------------


def test_comment_and_junk_lines_are_reported_with_one_based_line_numbers() -> None:
    result = parse_decklist("// comment\n???")
    assert result.lines == []
    assert [(u.line_number, u.text) for u in result.unparsed_lines] == [
        (1, "// comment"),
        (2, "???"),
    ]


def test_parsing_continues_after_unparsed_lines() -> None:
    text = "1 Sol Ring\n// comment\n1 Island\n???\n2 Forest"
    result = parse_decklist(text)
    assert [(u.line_number, u.text) for u in result.unparsed_lines] == [
        (2, "// comment"),
        (4, "???"),
    ]
    assert _cards(text) == [
        (1, "Sol Ring", "", "", M, 1),
        (1, "Island", "", "", M, 3),
        (2, "Forest", "", "", M, 5),
    ]


def test_unparsed_line_does_not_change_current_board() -> None:
    text = "Sideboard\n// note\n1 Rhystic Study"
    assert _cards(text) == [(1, "Rhystic Study", "", "", S, 3)]
    assert [u.line_number for u in parse_decklist(text).unparsed_lines] == [2]


# --- Line numbers ------------------------------------------------------------------------


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_line_numbers_are_correct_for_unix_and_windows_line_endings(eol: str) -> None:
    text = eol.join(["Commander", "1 Atraxa", "", "Deck", "// c", "1 Sol Ring", "???", "1 Island"])
    result = parse_decklist(text)
    assert [(p.name, p.line_number) for p in result.lines] == [
        ("Atraxa", 2),
        ("Sol Ring", 6),
        ("Island", 8),
    ]
    assert [u.line_number for u in result.unparsed_lines] == [5, 7]


@pytest.mark.parametrize("text", ["", "\n\n  \n"])
def test_empty_or_blank_text_yields_nothing(text: str) -> None:
    result = parse_decklist(text)
    assert result.lines == []
    assert result.unparsed_lines == []


# --- Whole exports in the spirit of the user story ---------------------------------------

MOXFIELD = f"""\
Commander
1 {ATRAXA} (C16) 28

Deck
1 Sol Ring (C21) 263
1 Command Tower (C21) 270 *F*
1 Fire // Ice (MH2) 290
1 Island

Sideboard
1 Rhystic Study (WOT) 29

Maybeboard
1 Mana Crypt (2XM) 270
"""

ARENA_BODY = f"""\
Commander
1 {ATRAXA} (C16) 28

Deck
1 Sol Ring (C21) 263
4 Island (UNF) 235

Sideboard
1 Rhystic Study (WOT) 29
"""

MTGO_BLANK = """\
4 Lightning Bolt
4 Goblin Guide
3 Monastery Swiftspear

2 Smash to Smithereens
1 Pyroblast
"""

MTGO_SB = """\
4 Lightning Bolt
4 Goblin Guide
SB: 2 Smash to Smithereens
SB: 1 Pyroblast
"""


def test_moxfield_export_lands_every_card_on_the_right_board() -> None:
    assert parse_decklist(MOXFIELD).unparsed_lines == []
    assert _cards(MOXFIELD) == [
        (1, ATRAXA, "C16", "28", C, 2),
        (1, "Sol Ring", "C21", "263", M, 5),
        (1, "Command Tower", "C21", "270", M, 6),
        (1, "Fire // Ice", "MH2", "290", M, 7),
        (1, "Island", "", "", M, 8),
        (1, "Rhystic Study", "WOT", "29", S, 11),
        (1, "Mana Crypt", "2XM", "270", MB, 14),
    ]


def test_arena_export_body_lands_every_card_on_the_right_board() -> None:
    assert parse_decklist(ARENA_BODY).unparsed_lines == []
    assert _cards(ARENA_BODY) == [
        (1, ATRAXA, "C16", "28", C, 2),
        (1, "Sol Ring", "C21", "263", M, 5),
        (4, "Island", "UNF", "235", M, 6),
        (1, "Rhystic Study", "WOT", "29", S, 9),
    ]


def test_arena_export_with_windows_line_endings_matches_unix_result() -> None:
    assert _cards(ARENA_BODY.replace("\n", "\r\n")) == _cards(ARENA_BODY)


def test_mtgo_blank_line_export_lands_every_card_on_the_right_board() -> None:
    result = parse_decklist(MTGO_BLANK)
    assert result.unparsed_lines == []
    assert [(p.quantity, p.name, p.board) for p in result.lines] == [
        (4, "Lightning Bolt", M),
        (4, "Goblin Guide", M),
        (3, "Monastery Swiftspear", M),
        (2, "Smash to Smithereens", S),
        (1, "Pyroblast", S),
    ]


def test_mtgo_sb_prefix_export_lands_every_card_on_the_right_board() -> None:
    result = parse_decklist(MTGO_SB)
    assert result.unparsed_lines == []
    assert [(p.quantity, p.name, p.board) for p in result.lines] == [
        (4, "Lightning Bolt", M),
        (4, "Goblin Guide", M),
        (2, "Smash to Smithereens", S),
        (1, "Pyroblast", S),
    ]
