"""Sign-in throttling (S-2.1.10): attempts are counted per username and per IP.

Counters live in Redis through ``core.rate_limit`` (fail open). The attempt is counted atomically
BEFORE the password is checked, so concurrent attempts cannot exceed the limit. The username goes
into the key only as a SHA-256 of its lowercase form. ``change_password`` (S-2.1.8) shares these
keys and limits through :func:`counted_attempt`.
"""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.identity.services.auth import InvalidCredentialsError, SignInResult, sign_in


def _keys(username: str, ip: str) -> tuple[str, str]:
    digest = hashlib.sha256(username.lower().encode()).hexdigest()
    return rate_limit.key("signin", "user", digest), rate_limit.key("signin", "ip", ip)


@asynccontextmanager
async def counted_attempt(
    username: str, ip: str, *, keep_count_on: type[Exception]
) -> AsyncIterator[None]:
    """Count one attempt on the sign-in keys around the body; refuse when a limit is reached.

    The attempt is counted first, in one atomic step; a blocked attempt never runs the body. An
    exception of type ``keep_count_on`` (the wrong-password error) keeps its count. Success deletes
    the username counter and gives back the IP count. Any other exception gives both back.

    Raises:
        RateLimitedError: a counter reached its limit in the current window (429).
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
        yield
    except keep_count_on:
        raise
    except BaseException:
        if counted:
            await rate_limit.give_back([user_key, ip_key])
        raise
    if counted:  # Redis was down at acquire: skip the extra failing calls
        await rate_limit.reset(user_key)
        await rate_limit.give_back([ip_key])


async def throttled_sign_in(
    session: AsyncSession, *, username: str, password: str, ip: str
) -> SignInResult:
    """Sign in, refusing early when the username or the IP has used up its attempts.

    Raises:
        RateLimitedError: a counter reached its limit in the current window (429).
        InvalidCredentialsError: unknown username, wrong password or inactive user.
    """
    async with counted_attempt(username, ip, keep_count_on=InvalidCredentialsError):
        return await sign_in(session, username=username, password=password)
