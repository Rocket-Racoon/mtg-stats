"""Pure checks for the registration rules and argon2id hashing (S-2.1.1 AC3, decision A9)."""

from collections.abc import Callable, Iterator

import pytest
from argon2 import PasswordHasher, Type
from pydantic import ValidationError

from app.core.config import Settings, get_settings, load_settings
from app.core.errors import DomainError
from app.identity.services import (
    InvalidPasswordError,
    InvalidUsernameError,
    UsernameTakenError,
    UserNotFoundError,
    hash_password,
    password_needs_rehash,
    validate_password,
    validate_username,
    verify_password,
)

PASSWORD = "correct horse battery"


@pytest.fixture
def set_argon2(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[int, int, int], None]]:
    """Change the argon2 settings for one test; the settings cache is reset afterwards."""

    def apply(time_cost: int, memory_cost: int, parallelism: int) -> None:
        monkeypatch.setenv("ARGON2_TIME_COST", str(time_cost))
        monkeypatch.setenv("ARGON2_MEMORY_COST", str(memory_cost))
        monkeypatch.setenv("ARGON2_PARALLELISM", str(parallelism))
        get_settings.cache_clear()

    yield apply
    monkeypatch.undo()
    get_settings.cache_clear()


# ------------------------------------------------------------------ error contracts
@pytest.mark.parametrize(
    ("error", "code", "status", "message"),
    [
        (
            InvalidUsernameError,
            "invalid_username",
            422,
            "Username must be 3 to 24 characters using only letters, digits, '_', '-' and '.'.",
        ),
        (InvalidPasswordError, "invalid_password", 422, "Password must be 12 to 128 characters."),
        (UsernameTakenError, "username_taken", 409, "Username already taken."),
        (UserNotFoundError, "user_not_found", 404, "User not found."),
    ],
)
def test_error_classes_carry_the_documented_code_status_and_message(
    error: type[DomainError], code: str, status: int, message: str
) -> None:
    instance = error()
    assert issubclass(error, DomainError)
    assert (instance.code, instance.status_code, instance.message) == (code, status, message)


def test_invalid_username_and_password_errors_are_value_errors() -> None:
    assert issubclass(InvalidUsernameError, ValueError)
    assert issubclass(InvalidPasswordError, ValueError)


# ------------------------------------------------------------------ username rules
@pytest.mark.parametrize(
    "username",
    ["abc", "a" * 24, "Hector", "a_b-c.d", "UPPER.lower-123", "___", "...", "---", "0" * 24],
)
def test_valid_usernames_are_returned_unchanged(username: str) -> None:
    assert validate_username(username) == username


@pytest.mark.parametrize(
    "username",
    [
        "",
        "ab",
        "a" * 25,
        " abc",
        "abc ",
        "a b c",
        "abc\n",
        "café",
        chr(0x430) + "bc",  # Cyrillic a
        "hector@home",
        "a/b/c",
        "name\u0000",
        "①②③",
        "abc" + chr(0xA0),  # no-break space
    ],
)
def test_invalid_usernames_are_rejected_and_never_trimmed(username: str) -> None:
    with pytest.raises(InvalidUsernameError):
        validate_username(username)


# ------------------------------------------------------------------ password rules
@pytest.mark.parametrize("length", [12, 13, 64, 127, 128])
def test_passwords_of_12_to_128_characters_are_returned_unchanged(length: int) -> None:
    password = "x" * length
    assert validate_password(password) == password


@pytest.mark.parametrize("length", [0, 1, 11, 129, 500])
def test_passwords_outside_12_to_128_characters_are_rejected(length: int) -> None:
    with pytest.raises(InvalidPasswordError):
        validate_password("x" * length)


def test_password_length_counts_code_points_not_bytes() -> None:
    assert validate_password("é" * 12) == "é" * 12
    assert validate_password("\U0001f600" * 128) == "\U0001f600" * 128
    with pytest.raises(InvalidPasswordError):
        validate_password("\U0001f600" * 129)
    with pytest.raises(InvalidPasswordError):
        validate_password("é" * 11)


def test_password_is_never_trimmed_so_padding_spaces_count() -> None:
    assert validate_password("a" + " " * 11) == "a" + " " * 11
    assert validate_password(" " * 12) == " " * 12
    with pytest.raises(InvalidPasswordError):
        validate_password(" " * 5 + "abcdef")  # 11 characters, so trimming cannot help


def test_password_error_message_does_not_contain_the_password() -> None:
    with pytest.raises(InvalidPasswordError) as caught:
        validate_password("tooshort")
    assert "tooshort" not in caught.value.message
    assert "tooshort" not in repr(caught.value.details)


# ------------------------------------------------------------------ hashing
def test_hash_is_argon2id_and_encodes_its_parameters() -> None:
    hashed = hash_password(PASSWORD)
    assert hashed.startswith("$argon2id$")
    assert "m=8,t=1,p=1" in hashed  # the forced test settings, read from the hash itself
    assert PASSWORD not in hashed


def test_hashing_the_same_password_twice_gives_different_salted_hashes() -> None:
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_verify_password_accepts_the_right_password_only() -> None:
    hashed = hash_password(PASSWORD)
    assert verify_password(hashed, PASSWORD) is True
    assert verify_password(hashed, PASSWORD + "x") is False
    assert verify_password(hashed, PASSWORD.upper()) is False


@pytest.mark.parametrize("garbage", ["", "not-a-hash", "$argon2id$broken", "$2b$12$abcdefg", "h"])
def test_verify_password_is_false_for_a_garbage_hash_without_raising(garbage: str) -> None:
    assert verify_password(garbage, PASSWORD) is False


def test_non_ascii_passwords_hash_and_verify() -> None:
    password = "pässwörd ☃ жжж"
    assert verify_password(hash_password(password), password) is True


def test_current_hash_does_not_need_a_rehash() -> None:
    assert password_needs_rehash(hash_password(PASSWORD)) is False


def test_hash_made_with_lower_parameters_needs_a_rehash(
    set_argon2: Callable[[int, int, int], None],
) -> None:
    old = hash_password(PASSWORD)
    set_argon2(2, 16, 1)
    assert password_needs_rehash(old) is True
    new = hash_password(PASSWORD)
    assert "m=16,t=2,p=1" in new
    assert password_needs_rehash(new) is False
    assert verify_password(old, PASSWORD) is True  # an old hash still verifies


def test_hash_made_with_stronger_parameters_is_flagged_as_different() -> None:
    same = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, type=Type.ID)
    assert password_needs_rehash(same.hash(PASSWORD)) is False
    stronger = PasswordHasher(time_cost=4, memory_cost=64, parallelism=1, type=Type.ID)
    assert password_needs_rehash(stronger.hash(PASSWORD)) is True


# ------------------------------------------------------------------ settings
def test_argon2_defaults_follow_the_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ARGON2_TIME_COST", "ARGON2_MEMORY_COST", "ARGON2_PARALLELISM"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings()
    assert (settings.argon2_time_cost, settings.argon2_memory_cost) == (3, 65536)
    assert settings.argon2_parallelism == 4


def test_argon2_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGON2_TIME_COST", "5")
    monkeypatch.setenv("ARGON2_MEMORY_COST", "1024")
    monkeypatch.setenv("ARGON2_PARALLELISM", "2")
    settings = load_settings()
    assert (settings.argon2_time_cost, settings.argon2_memory_cost) == (5, 1024)
    assert settings.argon2_parallelism == 2


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ARGON2_TIME_COST", "0"),
        ("ARGON2_TIME_COST", "-1"),
        ("ARGON2_MEMORY_COST", "0"),
        ("ARGON2_MEMORY_COST", "-8"),
        ("ARGON2_PARALLELISM", "0"),
        ("ARGON2_PARALLELISM", "-1"),
        ("ARGON2_TIME_COST", "abc"),
    ],
)
def test_argon2_settings_that_are_not_positive_integers_are_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        load_settings()


def test_argon2_memory_below_eight_times_parallelism_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGON2_PARALLELISM", "4")
    monkeypatch.setenv("ARGON2_MEMORY_COST", "31")
    with pytest.raises(ValidationError):
        load_settings()


def test_argon2_memory_of_exactly_eight_times_parallelism_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGON2_PARALLELISM", "4")
    monkeypatch.setenv("ARGON2_MEMORY_COST", "32")
    settings: Settings = load_settings()
    assert settings.argon2_memory_cost == 32


def test_the_test_environment_forces_cheap_argon2_values() -> None:
    settings = get_settings()
    assert (settings.argon2_time_cost, settings.argon2_memory_cost) == (1, 8)
    assert settings.argon2_parallelism == 1


def test_password_with_a_lone_surrogate_is_rejected_even_at_a_valid_length() -> None:
    with pytest.raises(InvalidPasswordError):
        validate_password("a" * 11 + chr(0xD800))


def test_an_unparseable_hash_needs_a_rehash() -> None:
    assert password_needs_rehash("garbage") is True
