"""Table-driven tests for the decklist text parser, built on ``.txt`` export samples."""

import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app.decks.services import Board, ParsedDecklist, ParsedLine, parse_decklist
from app.decks.services.text_parser import MAX_LINE_LENGTH

DATA = Path(__file__).parent / "data"
C, M, S, MB = Board.COMMANDER, Board.MAIN, Board.SIDEBOARD, Board.MAYBEBOARD

ATRAXA = "Atraxa, Praetors' Voice"

type Row = tuple[int, str, str, str, Board, int]

SAMPLES = [
    pytest.param(
        "moxfield.txt",
        [
            (1, ATRAXA, "C16", "28", C, 2),
            (1, "Sol Ring", "C21", "263", M, 5),
            (1, "Command Tower", "C21", "270", M, 6),
            (1, "Fire // Ice", "MH2", "290", M, 7),
            (1, "Lim-Dul's Vault", "ALL", "108a", M, 8),
            (1, "Jace, the Mind Sculptor", "A25", "★12", M, 9),
            (1, "Rhystic Study", "WOT", "29", S, 12),
            (1, "Mana Crypt", "2XM", "270", MB, 15),
        ],
        [],
        id="moxfield",
    ),
    pytest.param(
        "arena.txt",
        [
            (1, ATRAXA, "C16", "28", C, 2),
            (1, "Sol Ring", "C21", "263", M, 5),
            (4, "Island", "UNF", "235", M, 6),
            (1, "Rhystic Study", "WOT", "29", S, 9),
        ],
        [],
        id="arena",
    ),
    pytest.param(
        "mtgo_blank_line.txt",
        [
            (4, "Lightning Bolt", "", "", M, 1),
            (4, "Goblin Guide", "", "", M, 2),
            (3, "Monastery Swiftspear", "", "", M, 3),
            (2, "Smash to Smithereens", "", "", S, 5),
            (1, "Pyroblast", "", "", S, 6),
        ],
        [],
        id="mtgo-blank-line-sideboard",
    ),
    pytest.param(
        "mtgo_sb_prefix.txt",
        [
            (4, "Lightning Bolt", "", "", M, 1),
            (4, "Goblin Guide", "", "", M, 2),
            (2, "Smash to Smithereens", "", "", S, 3),
            (1, "Pyroblast", "", "", S, 4),
        ],
        [],
        id="mtgo-sb-prefix",
    ),
    pytest.param(
        "tappedout.txt",
        [
            (1, "Sol Ring", "c21", "", M, 1),
            (1, "Command Tower", "c21", "", M, 2),
            (2, "Swamp", "tdm", "275", M, 3),
        ],
        [(4, "// a comment"), (5, "???")],
        id="tappedout-with-unparsed",
    ),
    pytest.param(
        "headers_variants.txt",
        [
            (1, ATRAXA, "", "", C, 2),
            (2, "Sol Ring", "", "", M, 5),
            (2, "Rhystic Study", "", "", S, 8),
        ],
        [],
        id="header-variants",
    ),
]


def _rows(text: str) -> list[Row]:
    return [
        (p.quantity, p.name, p.set_code, p.collector_number, p.board, p.line_number)
        for p in parse_decklist(text).lines
    ]


@pytest.mark.parametrize(("filename", "expected", "unparsed"), SAMPLES)
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_export_sample_parses_to_expected_lines(
    filename: str, expected: list[Row], unparsed: list[tuple[int, str]], newline: str
) -> None:
    text = (DATA / filename).read_text(encoding="utf-8").replace("\n", newline)
    result = parse_decklist(text)
    assert _rows(text) == expected
    assert [(u.line_number, u.text) for u in result.unparsed_lines] == unparsed


@pytest.mark.parametrize(
    ("line", "qty", "name"),
    [
        ("1 Sol Ring", 1, "Sol Ring"),
        ("1x Sol Ring", 1, "Sol Ring"),
        ("1X Sol Ring", 1, "Sol Ring"),
        ("Sol Ring", 1, "Sol Ring"),
        ("  3   Sol Ring  ", 3, "Sol Ring"),
        ("12 Island", 12, "Island"),
        ("1 Fire // Ice", 1, "Fire // Ice"),
        ("1 Erase (Not the Urza's Card)", 1, "Erase (Not the Urza's Card)"),
        ("1 B.F.M. (Big Furry Monster)", 1, "B.F.M. (Big Furry Monster)"),
    ],
)
def test_quantity_and_name_forms(line: str, qty: int, name: str) -> None:
    (parsed,) = parse_decklist(line).lines
    assert (parsed.quantity, parsed.name, parsed.set_code, parsed.collector_number) == (
        qty,
        name,
        "",
        "",
    )


@pytest.mark.parametrize("marker", ["", " *F*", " *f*", " *E*", " *Foil*"])
def test_foil_marker_is_not_part_of_the_name(marker: str) -> None:
    (parsed,) = parse_decklist(f"1 Sol Ring (C21) 263{marker}").lines
    assert (parsed.name, parsed.set_code, parsed.collector_number) == ("Sol Ring", "C21", "263")


def test_foil_marker_without_set_is_dropped_from_the_name() -> None:
    (parsed,) = parse_decklist("1 Sol Ring *F*").lines
    assert (parsed.name, parsed.set_code) == ("Sol Ring", "")


def test_set_without_collector_number_parses() -> None:
    (parsed,) = parse_decklist("1 Sol Ring (C21) *F*").lines
    assert (parsed.name, parsed.set_code, parsed.collector_number) == ("Sol Ring", "C21", "")


@pytest.mark.parametrize(
    ("header", "board"),
    [
        ("Commander", C),
        ("Deck", M),
        ("Sideboard", S),
        ("Maybeboard", MB),
        ("Companion", Board.COMPANION),
    ],
)
@pytest.mark.parametrize("decoration", ["", ":", " (3)", "(3):", " (3) :"])
@pytest.mark.parametrize("case", [str.lower, str.upper, str.title])
def test_each_header_maps_to_its_board(
    header: str, board: Board, decoration: str, case: Callable[[str], str]
) -> None:
    result = parse_decklist(f"{case(header)}{decoration}\n1 Sol Ring")
    assert [p.board for p in result.lines] == [board]
    assert result.unparsed_lines == []


def test_sb_prefix_affects_only_its_own_line() -> None:
    result = parse_decklist("Commander\nsb: 1 Rhystic Study\n1 Atraxa")
    assert [(p.name, p.board) for p in result.lines] == [
        ("Rhystic Study", S),
        ("Atraxa", C),
    ]


def test_blank_lines_between_explicit_sections_do_not_force_the_sideboard() -> None:
    result = parse_decklist("Deck\n1 A\n\n1 B\n\nCommander\n1 C")
    assert [(p.name, p.board) for p in result.lines] == [("A", M), ("B", M), ("C", C)]


def test_blank_rule_is_off_when_any_sb_prefix_is_present() -> None:
    result = parse_decklist("1 A\n\n1 B\nSB: 1 C")
    assert [(p.name, p.board) for p in result.lines] == [("A", M), ("B", M), ("C", S)]


def test_blank_rule_ignores_leading_blank_lines_and_runs_of_several() -> None:
    result = parse_decklist("\n\n1 A\n\n\n1 B")
    assert [p.board for p in result.lines] == [M, M]


def test_second_blank_line_keeps_the_sideboard() -> None:
    result = parse_decklist("1 A\n\n1 B\n\n1 C")
    assert [p.board for p in result.lines] == [M, S, S]


@pytest.mark.parametrize("line", ["// comment", "???", "0 Sol Ring", "1 // note", "SB:", "1 ..."])
def test_unsupported_lines_are_reported_and_parsing_continues(line: str) -> None:
    result = parse_decklist(f"1 Sol Ring\n{line}\n1 Arcane Signet")
    assert [p.name for p in result.lines] == ["Sol Ring", "Arcane Signet"]
    assert [(u.line_number, u.text) for u in result.unparsed_lines] == [(2, line[:MAX_LINE_LENGTH])]


def test_bare_header_word_is_a_header_but_a_quantified_one_is_a_card() -> None:
    result = parse_decklist("Deck\n1 Deck")
    assert [(p.name, p.board) for p in result.lines] == [("Deck", M)]


def test_empty_text_and_bom_and_mixed_line_endings() -> None:
    assert parse_decklist("") == parse_decklist("\n\n")
    result = parse_decklist("\ufeff1 A\r2 B\r\n3 C\n")
    assert result.lines == [
        ParsedLine(1, "A", "", "", M, 1),
        ParsedLine(2, "B", "", "", M, 2),
        ParsedLine(3, "C", "", "", M, 3),
    ]


# --- Hostile input: linear time, no crashes, bounded values ------------------------------

# Sizes at which the previous (super-linear) regexes needed minutes to hours, with a budget
# that stays generous on a loaded machine (the linear parser needs milliseconds).
BUDGET_SECONDS = 10.0


def _timed(text: str) -> ParsedDecklist:
    started = time.perf_counter()
    result = parse_decklist(text)
    assert time.perf_counter() - started < BUDGET_SECONDS
    return result


@pytest.mark.parametrize(
    "text",
    [
        "Deck" + " " * 100_000 + "x",
        "Deck" + " " * 100_000 + ":" + " " * 100_000 + "(1)" + " " * 100_000,
        "Deck (" + " " * 100_000 + "1" + " " * 100_000,
        "1 A" + " " * 400_000 + "*a",
        "A" + " " * 400_000 + "(",
        "1 A" + " (SET)" * 100_000,
        "SB:" + " " * 400_000,
        "9" * 1_000_000 + " Sol Ring",
        "1 Sol Ring\n" * 100_000,
        "x" * 1_000_000,
    ],
    ids=[
        "header-spaces-then-x",
        "header-colon-spaces",
        "header-open-paren-spaces",
        "foil-spaces",
        "set-spaces",
        "repeated-set-tokens",
        "sb-spaces",
        "huge-quantity",
        "many-lines",
        "one-megabyte-line",
    ],
)
def test_hostile_input_parses_in_linear_time(text: str) -> None:
    _timed(text)


@pytest.mark.parametrize("digits", [5000, 23])
def test_over_long_quantity_is_unparsed_not_raised(digits: int) -> None:
    line = "9" * digits + " Sol Ring"
    result = parse_decklist(f"1 Island\n{line}\n1 Forest")
    assert [p.name for p in result.lines] == ["Island", "Forest"]
    assert [(u.line_number, u.text) for u in result.unparsed_lines] == [(2, line[:MAX_LINE_LENGTH])]


@pytest.mark.parametrize(
    ("line", "qty"),
    [("999 Sol Ring", 999), ("1 Sol Ring", 1), ("001 Sol Ring", 1), ("999x Sol Ring", 999)],
)
def test_quantity_within_range_is_accepted(line: str, qty: int) -> None:
    (parsed,) = parse_decklist(line).lines
    assert (parsed.quantity, parsed.name) == (qty, "Sol Ring")


@pytest.mark.parametrize(
    "line",
    ["0 Sol Ring", "000 Sol Ring", "1000 Sol Ring", "1996 World Champion", "-1 Sol Ring"],
)
def test_quantity_out_of_range_or_signed_is_unparsed(line: str) -> None:
    result = parse_decklist(line)
    assert result.lines == []
    assert [(u.line_number, u.text) for u in result.unparsed_lines] == [(1, line)]


def test_explicit_quantity_allows_a_name_that_starts_with_digits() -> None:
    (parsed,) = parse_decklist("1 1996 World Champion").lines
    assert (parsed.quantity, parsed.name) == (1, "1996 World Champion")


def test_arabic_indic_digits_are_not_a_quantity() -> None:
    result = parse_decklist("\u0661 Sol Ring")
    assert result.lines == []
    assert [u.line_number for u in result.unparsed_lines] == [1]


def test_header_count_accepts_ascii_digits_only() -> None:
    result = parse_decklist("Deck (\u0661)\n1 Sol Ring")
    assert [(p.name, p.board) for p in result.lines] == [("Deck (\u0661)", M), ("Sol Ring", M)]


def test_over_long_line_is_unparsed_with_truncated_text() -> None:
    line = "1 " + "A" * (MAX_LINE_LENGTH + 50)
    result = parse_decklist(f"1 Island\n{line}\n1 Forest")
    assert [p.name for p in result.lines] == ["Island", "Forest"]
    (unparsed,) = result.unparsed_lines
    assert unparsed.line_number == 2
    assert unparsed.text == line[:MAX_LINE_LENGTH]


def test_line_at_the_length_limit_still_parses() -> None:
    name = "A" * (MAX_LINE_LENGTH - 2)
    (parsed,) = parse_decklist(f"1 {name}").lines
    assert parsed.name == name


def test_non_breaking_and_unicode_spaces_are_spaces_and_runs_collapse() -> None:
    (parsed,) = parse_decklist("1\u00a0Sol\u00a0\u00a0Ring\u2003(C21)\u202f263").lines
    assert (parsed.quantity, parsed.name, parsed.set_code, parsed.collector_number) == (
        1,
        "Sol Ring",
        "C21",
        "263",
    )
    (spaced,) = parse_decklist("1   Sol \t  Ring").lines
    assert spaced.name == "Sol Ring"


def test_zero_width_characters_are_stripped() -> None:
    result = parse_decklist("1 Sol\u200b Ri\u200dng\u200c\n\u200b\u2060\nDe\ufeffck\n1 Island")
    assert [(p.name, p.board) for p in result.lines] == [("Sol Ring", M), ("Island", M)]
    assert [u.text for u in result.unparsed_lines] == ["\u2060"]
