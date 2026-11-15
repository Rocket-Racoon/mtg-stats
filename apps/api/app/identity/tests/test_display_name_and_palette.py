"""Pure checks for the display name and avatar palette rules (S-2.2.1 AC3)."""

from uuid import UUID

import pytest

from app.identity.services import (
    AVATAR_COLORS,
    InvalidAvatarColorError,
    InvalidDisplayNameError,
    default_avatar_color,
    normalize_display_name,
)

EXPECTED_PALETTE = (
    "red",
    "orange",
    "amber",
    "yellow",
    "lime",
    "green",
    "teal",
    "cyan",
    "blue",
    "indigo",
    "violet",
    "pink",
)


def test_palette_is_exactly_the_twelve_tokens_in_order() -> None:
    assert tuple(AVATAR_COLORS) == EXPECTED_PALETTE
    assert len(set(AVATAR_COLORS)) == 12


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Ada  ", "Ada"),
        ("\tAda\t", "Ada"),
        ("\n Ada Lovelace \n", "Ada Lovelace"),
        ("a", "a"),
        ("x" * 32, "x" * 32),
        (" " + "x" * 32 + " ", "x" * 32),
    ],
)
def test_display_name_is_trimmed_and_boundaries_are_accepted(raw: str, expected: str) -> None:
    assert normalize_display_name(raw) == expected


@pytest.mark.parametrize("raw", ["", " ", "   ", "\t\n", "x" * 33, " " + "x" * 33 + " "])
def test_display_name_empty_whitespace_only_or_too_long_is_rejected(raw: str) -> None:
    with pytest.raises(InvalidDisplayNameError):
        normalize_display_name(raw)


def test_identity_errors_are_value_errors() -> None:
    assert issubclass(InvalidDisplayNameError, ValueError)
    assert issubclass(InvalidAvatarColorError, ValueError)


@pytest.mark.parametrize("char", ["é", "日", "😀"])
def test_thirty_two_multibyte_characters_are_accepted_but_thirty_three_are_not(char: str) -> None:
    assert normalize_display_name(char * 32) == char * 32
    with pytest.raises(InvalidDisplayNameError):
        normalize_display_name(char * 33)


def test_default_avatar_color_is_the_palette_entry_for_the_id_modulo_twelve() -> None:
    for number in (0, 1, 11, 12, 13, 2**100 + 5):
        assert default_avatar_color(UUID(int=number)) == AVATAR_COLORS[number % 12]


def test_default_avatar_color_is_deterministic() -> None:
    player_id = UUID(int=987654321)
    assert default_avatar_color(player_id) == default_avatar_color(player_id)


def test_default_avatar_color_spreads_across_all_twelve_tokens() -> None:
    assert {default_avatar_color(UUID(int=n)) for n in range(12)} == set(EXPECTED_PALETTE)
