"""Change my password (S-2.1.8), throttled like sign-in."""

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import InvalidTokenError
from app.identity import repository
from app.identity.services.auth import (
    InvalidCurrentPasswordError,
    SignInResult,
    replace_password,
    start_session,
)
from app.identity.services.passwords import hash_password, verify_password
from app.identity.services.throttle import counted_attempt


async def change_password(
    session: AsyncSession, *, user_id: UUID, current_password: str, new_password: str, ip: str
) -> SignInResult:
    """Change the caller's password, revoke all their refresh tokens and start a new session.

    Throttled on the same keys and limits as sign-in (a wrong current password keeps its count; a
    success clears the username counter and gives back the IP count). The user row is locked
    exclusively (``FOR NO KEY UPDATE``) after the argon2 work and before any write, and the hash we
    verified is re-checked under it; a concurrent refresh or sign-in
    either finishes first (and is then revoked) or waits and sees the result. Flushes; the
    caller's session commits. ``new_password`` must already be validated (the schema does it).

    Raises:
        InvalidTokenError: the user does not exist or is inactive (401).
        RateLimitedError: a counter reached its limit in the current window (429).
        InvalidCurrentPasswordError: the current password is wrong (400, field error).
    """
    user = await repository.get_user(session, user_id)  # username for the throttle key
    if user is None or not user.is_active:
        raise InvalidTokenError
    async with counted_attempt(user.username, ip, keep_count_on=InvalidCurrentPasswordError):
        # All argon2 work happens before the lock, so a held lock never waits on hashing.
        verified_hash = user.password_hash
        if not await asyncio.to_thread(verify_password, verified_hash, current_password):
            raise InvalidCurrentPasswordError
        new_hash = await asyncio.to_thread(hash_password, new_password)
        locked = await repository.get_user_for_update(session, user_id)
        if locked is None or not locked.is_active:
            raise InvalidTokenError
        if locked.password_hash != verified_hash:  # changed concurrently since we verified
            raise InvalidCurrentPasswordError
        await replace_password(session, locked, new_hash)
        return await start_session(session, user_id=locked.id)
