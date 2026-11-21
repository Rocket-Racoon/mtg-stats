"""Pure parser for common decklist text formats (S-4.1.1).

Handles the plain ``1 Sol Ring`` / ``1x Sol Ring`` / ``Sol Ring`` forms, a trailing
``(SET) collector`` with an optional foil marker (Moxfield, Arena, Archidekt style), section
headers (``Commander``, ``Deck``, ``Sideboard``, ``Maybeboard``, ``Companion``) and the MTGO
conventions (``SB:`` prefix, or one blank line between main deck and sideboard).

No I/O and no card-name validation: names are returned exactly as written (later stories
resolve them). Lines that match no supported pattern are reported, never raised.

Hostile input: every line is normalised first (zero-width characters removed, every run of
Unicode whitespace collapsed to one space), so no regex sees adjacent whitespace runs, and
anything longer than ``MAX_LINE_LENGTH`` characters is reported as unparsed without being
matched. Total work is linear in the text size. The parser does **not** limit the size of the
text or the number of lines: the endpoint that calls it (S-4.1.3) must cap the body size and the
line count before calling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Board(StrEnum):
    """The board a parsed card belongs to."""

    COMMANDER = "commander"
    MAIN = "main"
    SIDEBOARD = "sideboard"
    MAYBEBOARD = "maybeboard"
    COMPANION = "companion"


@dataclass(frozen=True, slots=True)
class ParsedLine:
    """One card line. Absent ``set_code`` / ``collector_number`` are empty strings."""

    quantity: int
    name: str
    set_code: str
    collector_number: str
    board: Board
    line_number: int


@dataclass(frozen=True, slots=True)
class UnparsedLine:
    """A line that matched no supported pattern, with its 1-based line number."""

    line_number: int
    text: str


@dataclass(slots=True)
class ParsedDecklist:
    """Parser output: the card lines and the lines that could not be parsed."""

    lines: list[ParsedLine] = field(default_factory=list)
    unparsed_lines: list[UnparsedLine] = field(default_factory=list)


MAX_LINE_LENGTH = 300
"""Longest line (after whitespace normalisation) that is parsed; longer lines are unparsed."""
MAX_QUANTITY = 999

_HEADERS: dict[str, Board] = {
    "commander": Board.COMMANDER,
    "deck": Board.MAIN,
    "sideboard": Board.SIDEBOARD,
    "maybeboard": Board.MAYBEBOARD,
    "companion": Board.COMPANION,
}
# Lines are normalised to single spaces before matching, so " ?" is all the whitespace needed.
_FLAGS = re.IGNORECASE | re.ASCII
_HEADER_RE = re.compile(
    r"(?P<name>commander|deck|sideboard|maybeboard|companion)"
    r"(?: ?:)?(?: ?\( ?[0-9]{1,4} ?\))?(?: ?:)?",
    _FLAGS,
)
_SB_RE = re.compile(r"SB ?: ?(?P<rest>.*)", _FLAGS)
_QTY_RE = re.compile(r"(?P<qty>[0-9]+)(?: ?[xX])? (?P<rest>\S.*)")
_SET_TOKEN_RE = re.compile(r"\([A-Za-z0-9]{2,6}\)")
_FOIL_TOKEN_RE = re.compile(r"\*[A-Za-z]+\*")
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")
_ZERO_WIDTH = {
    0x200B: None,
    0x200C: None,
    0x200D: None,
    0xFEFF: None,
}


@dataclass(slots=True)
class _Entry:
    """One classified input line."""

    kind: str  # "blank" | "header" | "card" | "unparsed"
    text: str
    parts: tuple[int, str, str, str] | None = None
    board: Board | None = None
    sb: bool = False


def _normalise(raw: str) -> str:
    """Drop zero-width characters, collapse Unicode whitespace runs to one space and strip."""
    return " ".join(raw.translate(_ZERO_WIDTH).split())


def _split_card(text: str) -> tuple[int, str, str, str] | None:
    """Split a card line (without ``SB:``) into qty, name, set code and collector number."""
    quantity = 1
    rest = text
    if match := _QTY_RE.fullmatch(text):
        digits = match["qty"]
        if len(digits) > 3 or int(digits) < 1:
            return None
        quantity, rest = int(digits), match["rest"]
    elif text[0] == "-" and text[1:2].isdigit():
        return None  # a signed quantity such as ``-1 Sol Ring``
    elif text[0].isdecimal() and not text[0].isascii():
        return None  # non-ASCII digits are never a quantity, and not a name either
    head, _, tail = rest.rpartition(" ")
    if head and tail.endswith("*") and _FOIL_TOKEN_RE.fullmatch(tail):
        rest = head
    set_code = collector = ""
    head, _, tail = rest.rpartition(" ")
    if head and _SET_TOKEN_RE.fullmatch(tail):
        rest, set_code = head, tail[1:-1]
    else:
        head2, _, mid = head.rpartition(" ")
        if head2 and not tail.startswith("*") and _SET_TOKEN_RE.fullmatch(mid):
            rest, set_code, collector = head2, mid[1:-1], tail
    if rest.startswith("//") or not any(c.isalnum() for c in rest):
        return None
    return quantity, rest, set_code, collector


def parse_decklist(text: str) -> ParsedDecklist:
    """Parse decklist ``text`` into card lines and unparsed lines.

    Rules where the formats overlap:

    * A bare line that is exactly a section header word (optionally ``:`` and/or ``(n)``) is a
      header, never a card.
    * ``SB:`` marks only its own line as sideboard; the current section is unchanged.
    * The MTGO blank-line rule applies only when the list has no section header and no ``SB:``
      line: exactly one blank line (not a run of several) after at least one main-deck card
      switches the rest of the list to the sideboard. Otherwise blank lines are ignored.
    * A quantity is 1-3 ASCII digits with value 1-999, optionally followed by ``x``. A line that
      starts with a number outside that (``0``, four or more digits) or with ``-`` and a digit
      is unparsed, so a card name that starts with such a number needs an explicit quantity
      (``1 1996 World Champion``).
    * Lines longer than ``MAX_LINE_LENGTH`` are unparsed, with ``text`` truncated to that length.
    """
    # Classify every line once (header and SB: are each matched a single time).
    entries: list[_Entry] = []
    for raw in _LINE_BREAK_RE.split(text):
        line = _normalise(raw)
        if not line:
            entries.append(_Entry("blank", line))
        elif len(line) > MAX_LINE_LENGTH:
            entries.append(_Entry("unparsed", line))
        elif header := _HEADER_RE.fullmatch(line):
            entries.append(_Entry("header", line, board=_HEADERS[header["name"].lower()]))
        elif sb := _SB_RE.fullmatch(line):
            parts = _split_card(sb["rest"]) if sb["rest"] else None
            entries.append(_Entry("unparsed" if parts is None else "card", line, parts, sb=True))
        else:
            parts = _split_card(line)
            entries.append(_Entry("unparsed" if parts is None else "card", line, parts))
    explicit_boards = any(e.kind == "header" or e.sb for e in entries)

    result = ParsedDecklist()
    board = Board.MAIN
    seen_main_card = False
    blank_run = 0
    for number, entry in enumerate(entries, start=1):
        if entry.kind == "blank":
            blank_run += 1
            continue
        run, blank_run = blank_run, 0
        if run == 1 and not explicit_boards and seen_main_card and board is Board.MAIN:
            board = Board.SIDEBOARD
        if entry.kind == "header" and entry.board is not None:
            board = entry.board
        elif entry.kind == "card" and entry.parts is not None:
            line_board = Board.SIDEBOARD if entry.sb else board
            quantity, name, set_code, collector = entry.parts
            result.lines.append(ParsedLine(quantity, name, set_code, collector, line_board, number))
            if line_board is Board.MAIN:
                seen_main_card = True
        else:
            result.unparsed_lines.append(UnparsedLine(number, entry.text[:MAX_LINE_LENGTH]))
    return result
