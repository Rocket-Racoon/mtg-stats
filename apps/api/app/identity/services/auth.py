"""Sign-in (S-2.1.4) and refresh-token rotation, reuse detection and revocation (S-2.1.5)."""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import InvalidTokenError, IssuedToken, decode_token, issue_token
from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.core.ids import uuid7
from app.identity import repository
from app.identity.models import RefreshToken
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


def warm_up_sign_in() -> None:
    """Build the cached dummy hash for the current argon2 settings (blocking; run off-loop).

    Called at startup so the first unknown-username sign-in does not pay the extra hashing time.
    """
    _dummy_hash()


async def start_session(session: AsyncSession, *, user_id: UUID) -> SignInResult:
    """Issue an access token and a new refresh-token family for ``user_id``.

    Flushes; never commits. Used by sign-in and by registration, which signs the new user in.
    """
    result = SignInResult(
        user_id=user_id,
        access_token=issue_token(user_id, "access"),
        refresh_token=issue_token(user_id, "refresh"),
    )
    _store_refresh_token(session, result.refresh_token, user_id, family_id=uuid7())
    await session.flush()
    return result


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
    return await start_session(session, user_id=user.id)


CLEANUP_GRACE = timedelta(days=7)


class RefreshTokenReuseError(InvalidTokenError):
    """A revoked refresh token was presented, or its user is inactive; the family was revoked.

    Same code and message as :class:`InvalidTokenError`. ``persist_changes`` makes the request
    session commit the revocation although the response is a 401.

    Warning: because the session commits instead of rolling back, raise this only from a request
    whose only writes are the intended revocation, never inside a composite workflow.
    """

    persist_changes = True


def hash_refresh_token(token: str) -> str:
    """Return the SHA-256 hex digest under which a refresh token is stored."""
    return hashlib.sha256(token.encode()).hexdigest()


def _store_refresh_token(
    session: AsyncSession, issued: IssuedToken, user_id: UUID, *, family_id: UUID
) -> RefreshToken:
    row = RefreshToken(
        id=uuid7(),
        user_id=user_id,
        family_id=family_id,
        token_hash=hash_refresh_token(issued.token),
        expires_at=issued.expires_at,
    )
    repository.add_refresh_token(session, row)
    return row


async def refresh_session(session: AsyncSession, *, refresh_token: str) -> SignInResult:
    """Rotate a refresh token: issue a new access and refresh token, revoke the old one.

    A token whose row is already revoked (rotated or signed out) revokes its whole family, and so
    does a token of an inactive user. Flushes; never commits (the request session does, also for
    :class:`RefreshTokenReuseError`).

    Raises:
        InvalidTokenError: malformed, expired, unknown or wrong-type token.
        RefreshTokenReuseError: the token was already revoked, or its user is inactive.
    """
    claims = decode_token(refresh_token, expected_type="refresh")
    row = await repository.get_refresh_token_for_update(session, hash_refresh_token(refresh_token))
    if row is None:
        raise InvalidTokenError
    now = datetime.now(UTC)
    if row.revoked_at is not None:
        await repository.revoke_family(session, row.family_id, now)
        raise RefreshTokenReuseError
    if row.expires_at <= now:
        raise InvalidTokenError
    user = await repository.get_user(session, row.user_id)
    if user is None or not user.is_active or user.id != claims.sub:
        await repository.revoke_family(session, row.family_id, now)
        raise RefreshTokenReuseError
    result = SignInResult(
        user_id=user.id,
        access_token=issue_token(user.id, "access"),
        refresh_token=issue_token(user.id, "refresh"),
    )
    new_row = _store_refresh_token(session, result.refresh_token, user.id, family_id=row.family_id)
    await session.flush()  # insert the new row first: replaced_by references it
    row.revoked_at = now
    row.replaced_by = new_row.id
    await session.flush()
    return result


async def revoke_refresh_token(session: AsyncSession, *, refresh_token: str) -> None:
    """Sign out: revoke this refresh token. Invalid, unknown or revoked tokens are a no-op."""
    try:
        decode_token(refresh_token, expected_type="refresh")
    except InvalidTokenError:
        return
    row = await repository.get_refresh_token_for_update(session, hash_refresh_token(refresh_token))
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.flush()


async def revoke_all_refresh_tokens(session: AsyncSession, *, user_id: UUID) -> int:
    """Revoke every unrevoked refresh token of ``user_id``; return how many were revoked."""
    return await repository.revoke_user_tokens(session, user_id, datetime.now(UTC))


async def cleanup_refresh_tokens(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Delete refresh tokens that expired more than 7 days before ``now``; return the count."""
    cutoff = (now or datetime.now(UTC)) - CLEANUP_GRACE
    return await repository.delete_refresh_tokens_expired_before(session, cutoff)
