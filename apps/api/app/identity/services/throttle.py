"""Sign-in throttling (S-2.1.10): failed attempts are counted per username and per IP.

Counters live in Redis through ``core.rate_limit`` (fail open). The username goes into the key
only as a SHA-256 of its lowercase form.
"""

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.identity.services.auth import InvalidCredentialsError, SignInResult, sign_in


def _keys(username: str, ip: str) -> tuple[str, str]:
    digest = hashlib.sha256(username.lower().encode()).hexdigest()
    return rate_limit.key("signin", "user", digest), rate_limit.key("signin", "ip", ip)


async def throttled_sign_in(
    session: AsyncSession, *, username: str, password: str, ip: str
) -> SignInResult:
    """Sign in, refusing early when the username or the IP has too many failures.

    The check runs before any password work. A failure (``InvalidCredentialsError``) counts
    against both counters; a success clears the username counter only.

    Raises:
        RateLimitedError: a counter reached its limit in the current window (429).
        InvalidCredentialsError: unknown username, wrong password or inactive user.
    """
    settings = get_settings()
    user_key, ip_key = _keys(username, ip)
    windows = await rate_limit.peek([user_key, ip_key])
    if windows is not None:
        blocked = [
            w
            for w, limit in zip(
                windows,
                (settings.signin_max_failures_per_username, settings.signin_max_failures_per_ip),
                strict=True,
            )
            if w.count >= limit
        ]
        if blocked:
            raise RateLimitedError(retry_after=max(w.retry_after for w in blocked))
    try:
        result = await sign_in(session, username=username, password=password)
    except InvalidCredentialsError:
        await rate_limit.hit([user_key, ip_key])
        raise
    await rate_limit.reset(user_key)
    return result
