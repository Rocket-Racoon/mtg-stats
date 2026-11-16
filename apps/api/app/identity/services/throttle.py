"""Sign-in throttling (S-2.1.10): attempts are counted per username and per IP.

Counters live in Redis through ``core.rate_limit`` (fail open). The attempt is counted atomically
BEFORE the password is checked, so concurrent attempts cannot exceed the limit. The username goes
into the key only as a SHA-256 of its lowercase form.
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
    try:
        result = await sign_in(session, username=username, password=password)
    except InvalidCredentialsError:
        raise
    except BaseException:
        await rate_limit.give_back([user_key, ip_key])
        raise
    await rate_limit.reset(user_key)
    await rate_limit.give_back([ip_key])
    return result
