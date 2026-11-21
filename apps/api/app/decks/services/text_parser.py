"""Pure parser for common decklist text formats (S-4.1.1).

Handles the plain ``1 Sol Ring`` / ``1x Sol Ring`` / ``Sol Ring`` forms, a trailing
``(SET) collector`` with an optional foil marker (Moxfield, Arena, Archidekt style), section
headers (``Commander``, ``Deck``, ``Sideboard``, ``Maybeboard``, ``Companion``) and the MTGO
conventions (``SB:`` prefix, or one blank line between main deck and sideboard).

No I/O and no card-name validation: names are returned exactly as written (later stories
resolve them). Lines that match no supported pattern are reported, never raised.
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


@dataclass(frozen=True, slots=True)
class ParsedDecklist:
    """Parser output: the card lines and the lines that could not be parsed."""

    lines: list[ParsedLine] = field(default_factory=list)
    unparsed_lines: list[UnparsedLine] = field(default_factory=list)


_HEADERS: dict[str, Board] = {
    "commander": Board.COMMANDER,
    "deck": Board.MAIN,
    "sideboard": Board.SIDEBOARD,
    "maybeboard": Board.MAYBEBOARD,
    "companion": Board.COMPANION,
}
_HEADER_RE = re.compile(
    r"^(?P<name>commander|deck|sideboard|maybeboard|companion)"
    r"\s*:?\s*(?:\(\s*\d+\s*\))?\s*:?$",
    re.IGNORECASE,
)
_SB_RE = re.compile(r"^SB\s*:\s*(?P<rest>.*)$", re.IGNORECASE)
_QTY_RE = re.compile(r"^(?P<qty>\d+)\s*[xX]?\s+(?P<rest>\S.*)$")
_FOIL_RE = re.compile(r"\s+\*[A-Za-z]+\*$")
_SET_RE = re.compile(r"^(?P<name>.*\S)\s+\((?P<set>[A-Za-z0-9]{2,6})\)(?:\s+(?P<num>[^\s*]\S*))?$")
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")


def _split_card(text: str) -> tuple[int, str, str, str] | None:
    """Split a card line (without ``SB:``) into qty, name, set code and collector number."""
    quantity = 1
    rest = text
    if match := _QTY_RE.match(text):
        quantity = int(match["qty"])
        rest = match["rest"]
    rest = _FOIL_RE.sub("", rest).strip()
    set_code = collector = ""
    if match := _SET_RE.match(rest):
        rest, set_code, collector = match["name"], match["set"], match["num"] or ""
    if quantity < 1 or rest.startswith("//") or not any(c.isalnum() for c in rest):
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
    """
    raw_lines = _LINE_BREAK_RE.split(text.removeprefix("﻿"))
    stripped = [raw.strip() for raw in raw_lines]
    explicit_boards = any(_HEADER_RE.match(s) or _SB_RE.match(s) for s in stripped)

    result = ParsedDecklist()
    board = Board.MAIN
    seen_main_card = False
    blank_run = 0
    for number, line in enumerate(stripped, start=1):
        if not line:
            blank_run += 1
            continue
        run, blank_run = blank_run, 0
        if run == 1 and not explicit_boards and seen_main_card and board is Board.MAIN:
            board = Board.SIDEBOARD
        if header := _HEADER_RE.match(line):
            board = _HEADERS[header["name"].lower()]
            continue
        line_board = board
        card_text = line
        if sb := _SB_RE.match(line):
            line_board, card_text = Board.SIDEBOARD, sb["rest"].strip()
        parts = _split_card(card_text) if card_text else None
        if parts is None:
            result.unparsed_lines.append(UnparsedLine(number, line))
            continue
        quantity, name, set_code, collector = parts
        result.lines.append(ParsedLine(quantity, name, set_code, collector, line_board, number))
        if line_board is Board.MAIN:
            seen_main_card = True
    return result
