"""Sign-in throttling (S-2.1.10): attempts are counted per username and per IP.

Counters live in Redis through ``core.rate_limit`` (fail open). The attempt is counted atomically
BEFORE the password is checked, so concurrent attempts cannot exceed the limit. The username goes
into the key only as a SHA-256 of its lowercase form.
"""

import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.auth import InvalidTokenError
from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.identity import repository
from app.identity.services.auth import (
    InvalidCredentialsError,
    InvalidCurrentPasswordError,
    SignInResult,
    revoke_all_refresh_tokens,
    sign_in,
    start_session,
)
from app.identity.services.passwords import hash_password, verify_password


def _keys(username: str, ip: str) -> tuple[str, str]:
    digest = hashlib.sha256(username.lower().encode()).hexdigest()
    return rate_limit.key("signin", "user", digest), rate_limit.key("signin", "ip", ip)


async def throttled_sign_in(
    session: AsyncSession, *, username: str, password: str, ip: str
) -> SignInResult:
    """Sign in, refusing early when the username or the IP has used up its attempts.

    The attempt is counted first, in one atomic step; a blocked attempt does no password work. A
    failure (``InvalidCredentialsError``) keeps its count. A success deletes the username counter
    and gives back the IP count. Any other exception gives both counts back.

    Raises:
        RateLimitedError: a counter reached its limit in the current window (429).
        InvalidCredentialsError: unknown username, wrong password or inactive user.
    """
    settings = get_settings()
    user_key, ip_key = _keys(username, ip)
    outcome = await rate_limit.acquire(
        [
            (user_key, settings.signin_max_failures_per_username),
            (ip_key, settings.signin_max_failures_per_ip),
        ]
    )
    if isinstance(outcome, rate_limit.Blocked):
        raise RateLimitedError(retry_after=outcome.retry_after)
    counted = isinstance(outcome, rate_limit.Allowed) and outcome.counted
    try:
        result = await sign_in(session, username=username, password=password)
    except InvalidCredentialsError:
        raise
    except BaseException:
        if counted:
            await rate_limit.give_back([user_key, ip_key])
        raise
    if counted:  # Redis was down at acquire: skip the extra failing calls
        await rate_limit.reset(user_key)
        await rate_limit.give_back([ip_key])
    return result


async def change_password(
    session: AsyncSession, *, user_id: UUID, current_password: str, new_password: str, ip: str
) -> SignInResult:
    """Change the caller's password, revoke all their refresh tokens and start a new session.

    Throttled on the same keys and limits as sign-in (a wrong current password keeps its count;
    a success clears the username counter and gives back the IP count; any other exception gives
    both back). The attempt is counted before any password work. The caller's session commits;
    nothing is committed here. ``new_password`` must already be validated (the request schema
    does it).

    Raises:
        InvalidTokenError: the user does not exist or is inactive (401).
        RateLimitedError: a counter reached its limit in the current window (429).
        InvalidCurrentPasswordError: the current password is wrong (400, field error).
    """
    user = await repository.get_user(session, user_id)
    if user is None or not user.is_active:
        raise InvalidTokenError
    settings = get_settings()
    user_key, ip_key = _keys(user.username, ip)
    outcome = await rate_limit.acquire(
        [
            (user_key, settings.signin_max_failures_per_username),
            (ip_key, settings.signin_max_failures_per_ip),
        ]
    )
    if isinstance(outcome, rate_limit.Blocked):
        raise RateLimitedError(retry_after=outcome.retry_after)
    counted = isinstance(outcome, rate_limit.Allowed) and outcome.counted
    try:
        if not await asyncio.to_thread(verify_password, user.password_hash, current_password):
            raise InvalidCurrentPasswordError
        user.password_hash = await asyncio.to_thread(hash_password, new_password)
        user.password_changed_at = datetime.now(UTC)
        await revoke_all_refresh_tokens(session, user_id=user.id)
        result = await start_session(session, user_id=user.id)
    except InvalidCurrentPasswordError:
        raise
    except BaseException:
        if counted:
            await rate_limit.give_back([user_key, ip_key])
        raise
    if counted:
        await rate_limit.reset(user_key)
        await rate_limit.give_back([ip_key])
    return result
