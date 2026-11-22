"""Player and user creation and lookup. Everything here runs in the caller's session."""

import asyncio
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.core.ids import uuid7
from app.identity import repository
from app.identity.models import AVATAR_COLORS, DISPLAY_NAME_MAX_LENGTH, Player, User
from app.identity.services.auth import replace_password
from app.identity.services.passwords import hash_password, validate_password, validate_username

EMAIL_MAX_LENGTH = 320
"""Length of the ``users.email`` column."""

_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cs"})
"""Unicode categories a display name may not contain: control characters and lone surrogates."""


class InvalidDisplayNameError(DomainError, ValueError):
    """The display name is empty, longer than 32 characters after trimming, or has a control
    character or lone surrogate."""

    code = "invalid_display_name"
    status_code = 422
    default_message = "Display name must be 1 to 32 characters, with no control characters."


class InvalidEmailError(DomainError, ValueError):
    """The email is longer than 320 characters or has a control character or lone surrogate."""

    code = "invalid_email"
    status_code = 422
    default_message = "Email must be at most 320 characters."


class UsernameTakenError(DomainError):
    """A user with this username (compared case-insensitively) already exists."""

    code = "username_taken"
    status_code = 409
    default_message = "Username already taken."


class UserNotFoundError(DomainError):
    """No user has this username."""

    code = "user_not_found"
    status_code = 404
    default_message = "User not found."


class InvalidAvatarColorError(DomainError, ValueError):
    """The avatar color is not one of the palette tokens."""

    code = "invalid_avatar_color"
    status_code = 422
    default_message = "Avatar color is not in the palette."


@dataclass(frozen=True, slots=True)
class PlayerInfo:
    """Read-only view of a player, returned instead of the ORM object."""

    id: UUID
    user_id: UUID | None
    is_guest: bool
    display_name: str
    avatar_color: str
    home_playgroup_id: UUID | None
    merged_into_id: UUID | None
    created_at: datetime


def _to_info(player: Player) -> PlayerInfo:
    return PlayerInfo(
        id=player.id,
        user_id=player.user_id,
        is_guest=player.is_guest,
        display_name=player.display_name,
        avatar_color=player.avatar_color,
        home_playgroup_id=player.home_playgroup_id,
        merged_into_id=player.merged_into_id,
        created_at=player.created_at,
    )


def default_avatar_color(player_id: UUID) -> str:
    """Return the palette token for a player id (deterministic; UUID v7 low bits are random)."""
    return AVATAR_COLORS[player_id.int % len(AVATAR_COLORS)]


def normalize_display_name(value: str) -> str:
    """Trim a display name and check it is 1 to 32 characters.

    Control characters (Unicode category Cc, which includes NUL) and lone surrogates (Cs) are
    rejected too: Postgres can't store NUL and psycopg can't encode a lone surrogate, so without
    this check they would surface as a 500 instead of ``invalid_display_name``.

    Raises:
        InvalidDisplayNameError: if the trimmed name is empty, too long or contains a control
            character or lone surrogate.
    """
    name = value.strip()
    if not 1 <= len(name) <= DISPLAY_NAME_MAX_LENGTH or any(
        unicodedata.category(char) in _FORBIDDEN_CATEGORIES for char in name
    ):
        raise InvalidDisplayNameError(details={"max_length": DISPLAY_NAME_MAX_LENGTH})
    return name


def normalize_email(value: str) -> str:
    """Trim an email and check it fits the column (320 characters, no control characters).

    An empty email stays empty. Control characters and lone surrogates are rejected for the same
    reason as in display names (Postgres can't store NUL, psycopg can't encode surrogates).

    Raises:
        InvalidEmailError: if the trimmed email is too long or has a forbidden character.
    """
    email = value.strip()
    if len(email) > EMAIL_MAX_LENGTH or any(
        unicodedata.category(char) in _FORBIDDEN_CATEGORIES for char in email
    ):
        raise InvalidEmailError(details={"max_length": EMAIL_MAX_LENGTH})
    return email


def _is_username_race(exc: IntegrityError) -> bool:
    """True if the error is a unique violation on the case-insensitive username index."""
    orig = exc.orig
    diag = getattr(orig, "diag", None)
    return (
        getattr(orig, "sqlstate", None) == "23505"
        and getattr(diag, "constraint_name", None) == "uq_users_username_lower"
    )


def _resolve_color(player_id: UUID, avatar_color: str | None) -> str:
    if avatar_color is None:
        return default_avatar_color(player_id)
    if avatar_color not in AVATAR_COLORS:
        raise InvalidAvatarColorError(details={"allowed": list(AVATAR_COLORS)})
    return avatar_color


async def get_player(session: AsyncSession, player_id: UUID) -> PlayerInfo | None:
    """Return the player with this id, or None."""
    player = await repository.get_player(session, player_id)
    return None if player is None else _to_info(player)


MAX_MERGE_HOPS = 10
"""Longest chain of merges ``resolve_merged_player`` follows before giving up."""


async def resolve_merged_player(session: AsyncSession, player_id: UUID) -> PlayerInfo | None:
    """Follow ``merged_into_id`` from this player to the surviving player.

    Returns the player itself when it was never merged, the end of the chain otherwise, and None
    when the id is unknown, the chain ends at a missing row, loops or is longer than
    ``MAX_MERGE_HOPS``. It runs one query per hop (a chain is almost always 0 or 1 long).
    """
    seen: set[UUID] = set()
    current: UUID = player_id
    for _ in range(MAX_MERGE_HOPS + 1):
        if current in seen:
            return None
        seen.add(current)
        player = await repository.get_player(session, current)
        if player is None:
            return None
        if player.merged_into_id is None:
            return _to_info(player)
        current = player.merged_into_id
    return None


async def get_players(
    session: AsyncSession, player_ids: Collection[UUID]
) -> dict[UUID, PlayerInfo]:
    """Return the players with these ids in one query; missing ids are absent."""
    if not player_ids:
        return {}
    return {p.id: _to_info(p) for p in await repository.get_players(session, player_ids)}


async def get_player_for_user(session: AsyncSession, user_id: UUID) -> PlayerInfo | None:
    """Return the player that belongs to this user, or None."""
    player = await repository.get_player_for_user(session, user_id)
    return None if player is None else _to_info(player)


async def create_guest_player(
    session: AsyncSession, *, display_name: str, avatar_color: str | None = None
) -> PlayerInfo:
    """Create a guest player (no user). Flushes; the caller commits."""
    name = normalize_display_name(display_name)
    player_id = uuid7()
    player = Player(
        id=player_id,
        user_id=None,
        is_guest=True,
        display_name=name,
        avatar_color=_resolve_color(player_id, avatar_color),
    )
    repository.add_player(session, player)
    await session.flush()
    return _to_info(player)


async def create_user_with_player(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    display_name: str,
    email: str = "",
    is_site_admin: bool = False,
    avatar_color: str | None = None,
    password_hash: str | None = None,
) -> PlayerInfo:
    """Create a user and its one player in the caller's session. Flushes; the caller commits.

    Validates the username, password and display name (in that order) before writing, then hashes
    the password, unless ``password_hash`` is given. It must be ``hash_password(password)``, made
    by the caller off the event loop; it is stored as is, nothing is hashed here, and a value not
    starting with ``$argon2id$`` raises ``ValueError``. The password is never logged or put in an
    error.

    Raises:
        InvalidUsernameError: if the username breaks the rules.
        InvalidPasswordError: if the password breaks the rules.
        InvalidDisplayNameError: if the display name breaks the rules.
        InvalidEmailError: if the email is too long or has a control character.
        UsernameTakenError: if the username exists, by pre-check or by losing a race.
    """
    validate_username(username)
    validate_password(password)
    if password_hash is not None and not password_hash.startswith("$argon2id$"):
        raise ValueError("password_hash must be an argon2id hash from hash_password")
    name = normalize_display_name(display_name)
    normalized_email = normalize_email(email)
    player_id = uuid7()
    color = _resolve_color(player_id, avatar_color)
    if await repository.get_user_by_username(session, username) is not None:
        raise UsernameTakenError
    user = User(
        username=username,
        email=normalized_email,
        password_hash=password_hash if password_hash is not None else hash_password(password),
        is_site_admin=is_site_admin,
    )
    try:
        async with session.begin_nested():
            repository.add_user(session, user)
            await session.flush()
    except IntegrityError as exc:
        if _is_username_race(exc):
            raise UsernameTakenError from None
        raise
    player = Player(
        id=player_id,
        user_id=user.id,
        is_guest=False,
        display_name=name,
        avatar_color=color,
    )
    repository.add_player(session, player)
    await session.flush()
    return _to_info(player)


async def set_password(session: AsyncSession, *, username: str, password: str) -> None:
    """Replace a user's password hash and stamp ``password_changed_at``.

    Also revokes all of the user's refresh tokens. Flushes; never commits.

    Raises:
        InvalidPasswordError: if the password breaks the rules.
        UserNotFoundError: if no user has this username (case-insensitive).
    """
    validate_password(password)
    user = await repository.get_user_by_username(session, username, for_update=True)
    if user is None:
        raise UserNotFoundError
    await replace_password(session, user, await asyncio.to_thread(hash_password, password))
