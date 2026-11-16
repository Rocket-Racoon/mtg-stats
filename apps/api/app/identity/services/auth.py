"""Sign-in (S-2.1.4): credential check with constant-ish timing, rehash on login, token issue."""

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import IssuedToken, issue_token
from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.identity import repository
from app.identity.services.passwords import hash_password, password_needs_rehash, verify_password


class InvalidCredentialsError(AuthenticationError):
    """Unknown user, wrong password or inactive account; one message for all three."""

    code = "invalid_credentials"
    default_message = "Incorrect username or password."


@dataclass(frozen=True)
class SignInResult:
    """The signed-in user and the tokens issued for them."""

    user_id: UUID
    access_token: IssuedToken
    refresh_token: IssuedToken


@lru_cache
def _dummy_hash_for(time_cost: int, memory_cost: int, parallelism: int) -> str:
    # The argument tuple keys the cache so a settings change builds a fresh dummy hash.
    del time_cost, memory_cost, parallelism
    return hash_password("dummy-password-for-timing")


def _dummy_hash() -> str:
    s = get_settings()
    return _dummy_hash_for(s.argon2_time_cost, s.argon2_memory_cost, s.argon2_parallelism)


async def sign_in(session: AsyncSession, *, username: str, password: str) -> SignInResult:
    """Check the credentials and issue an access and a refresh token.

    Every failure raises :class:`InvalidCredentialsError` with the same message, and an unknown
    username still runs one argon2 verification against a dummy hash. A successful sign-in with an
    outdated hash stores a new one (flushed, never committed; the caller's session commits).

    Raises:
        InvalidCredentialsError: unknown username, wrong password or inactive user.
    """
    user = await repository.get_user_by_username(session, username)
    if user is None:
        dummy = await asyncio.to_thread(_dummy_hash)
        await asyncio.to_thread(verify_password, dummy, password)
        raise InvalidCredentialsError
    matches = await asyncio.to_thread(verify_password, user.password_hash, password)
    if not matches or not user.is_active:
        raise InvalidCredentialsError
    if await asyncio.to_thread(password_needs_rehash, user.password_hash):
        user.password_hash = await asyncio.to_thread(hash_password, password)
        await session.flush()
    return SignInResult(
        user_id=user.id,
        access_token=issue_token(user.id, "access"),
        refresh_token=issue_token(user.id, "refresh"),
    )
