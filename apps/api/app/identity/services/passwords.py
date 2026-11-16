"""Password hashing (argon2id) and the registration rules for usernames and passwords."""

import re
from functools import lru_cache

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.errors import DomainError

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 24
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128

_USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{3,24}")


class InvalidUsernameError(DomainError, ValueError):
    """The username breaks the registration rules."""

    code = "invalid_username"
    status_code = 422
    default_message = (
        "Username must be 3 to 24 characters using only letters, digits, '_', '-' and '.'."
    )


class InvalidPasswordError(DomainError, ValueError):
    """The password breaks the registration rules."""

    code = "invalid_password"
    status_code = 422
    default_message = "Password must be 12 to 128 characters."


def validate_username(value: str) -> str:
    """Return ``value`` unchanged if it is 3 to 24 ASCII letters, digits, ``_``, ``-`` or ``.``.

    Nothing is trimmed: any other character, spaces included, is invalid.

    Raises:
        InvalidUsernameError: if the rules are broken.
    """
    if not _USERNAME_RE.fullmatch(value):
        raise InvalidUsernameError(
            details={"min_length": USERNAME_MIN_LENGTH, "max_length": USERNAME_MAX_LENGTH}
        )
    return value


def validate_password(value: str) -> str:
    """Return ``value`` unchanged if it is 12 to 128 characters (code points; never trimmed).

    A string that cannot be encoded as UTF-8 (a lone surrogate) is rejected too, since it could
    not be hashed. The error never includes the password.

    Raises:
        InvalidPasswordError: if the rules are broken.
    """
    encodable = True
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        encodable = False
    if not PASSWORD_MIN_LENGTH <= len(value) <= PASSWORD_MAX_LENGTH or not encodable:
        raise InvalidPasswordError(
            details={"min_length": PASSWORD_MIN_LENGTH, "max_length": PASSWORD_MAX_LENGTH}
        )
    return value


@lru_cache
def _hasher_for(time_cost: int, memory_cost: int, parallelism: int) -> PasswordHasher:
    return PasswordHasher(
        time_cost=time_cost,
        memory_cost=memory_cost,
        parallelism=parallelism,
        type=Type.ID,
    )


def _hasher() -> PasswordHasher:
    settings = get_settings()
    return _hasher_for(
        settings.argon2_time_cost, settings.argon2_memory_cost, settings.argon2_parallelism
    )


def hash_password(password: str) -> str:
    """Hash a password with argon2id; the parameters are encoded in the returned hash."""
    return _hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return True if ``password`` matches; False on a mismatch or an invalid hash."""
    try:
        return _hasher().verify(password_hash, password)
    except (VerificationError, InvalidHashError, UnicodeEncodeError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """Return True if the hash was made with parameters other than the current settings."""
    try:
        return _hasher().check_needs_rehash(password_hash)
    except (VerificationError, InvalidHashError):
        return True
