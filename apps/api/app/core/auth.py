"""JWT issuing and decoding, and the ``current_user`` dependency (S-2.1.4).

Tokens are HS256 JWTs signed with ``JWT_SECRET``. Both kinds carry ``sub``, ``exp``, ``iat``,
``jti`` and ``type`` (``access`` or ``refresh``). Decoding accepts only HS256, so ``alg=none`` and
algorithm-confusion tokens fail. ``current_user`` only decodes the token; it never reads the
database, so a deactivated user's access token works until it expires (at most the access
lifetime). ``require_site_admin`` is the exception: it checks the database on every request
through a checker that ``create_app`` registers. This module imports no domain.
"""

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, ClassVar, Literal
from uuid import UUID

import jwt
from fastapi import Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import SessionDep
from app.core.errors import AuthenticationError, PermissionDeniedError

JWT_ALGORITHM = "HS256"
REQUIRED_CLAIMS = ("sub", "exp", "iat", "jti", "type")

TokenType = Literal["access", "refresh"]
ClientMode = Literal["web", "mobile"]

REFRESH_COOKIE_NAME = "mtgstats_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"
CLIENT_HEADER = "X-Client"


class InvalidTokenError(AuthenticationError):
    """The token is missing a claim, malformed, expired, mis-signed or of the wrong type."""

    code = "invalid_token"
    default_message = "Invalid or expired token."
    default_headers: ClassVar[dict[str, str] | None] = {
        "WWW-Authenticate": 'Bearer error="invalid_token"'
    }


@dataclass(frozen=True)
class IssuedToken:
    """A signed token with the data a caller may need to store or report."""

    token: str
    jti: str
    expires_at: datetime
    expires_in: int  # lifetime in seconds, from the same settings as expires_at


@dataclass(frozen=True)
class TokenClaims:
    """The validated claims of a decoded token."""

    sub: UUID
    jti: str
    iat: datetime
    exp: datetime
    type: TokenType


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated caller, from a valid access token."""

    id: UUID
    token_id: str


def issue_token(
    subject: UUID,
    token_type: TokenType,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> IssuedToken:
    """Sign a token for ``subject``; its lifetime comes from the settings for ``token_type``."""
    settings = settings or get_settings()
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    lifetime = (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if token_type == "access"  # noqa: S105  (a token type, not a password)
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    expires_at = issued_at + lifetime
    jti = secrets.token_urlsafe(16)
    payload = {
        "sub": str(subject),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": jti,
        "type": token_type,
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALGORITHM)
    return IssuedToken(
        token=token, jti=jti, expires_at=expires_at, expires_in=int(lifetime.total_seconds())
    )


def decode_token(
    token: str, *, expected_type: TokenType, settings: Settings | None = None
) -> TokenClaims:
    """Verify ``token`` and return its claims.

    Raises:
        InvalidTokenError: for any defect (signature, algorithm, expiry, claims, type).
    """
    settings = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            options={"require": list(REQUIRED_CLAIMS)},
            leeway=0,
        )
        jti = payload["jti"]
        if payload["type"] != expected_type or not isinstance(jti, str) or not jti:
            raise InvalidTokenError
        subject = UUID(payload["sub"])
        if str(subject) != payload["sub"]:
            raise InvalidTokenError
        return TokenClaims(
            sub=subject,
            jti=jti,
            iat=datetime.fromtimestamp(payload["iat"], UTC),
            exp=datetime.fromtimestamp(payload["exp"], UTC),
            type=expected_type,
        )
    except InvalidTokenError:
        raise
    except (
        jwt.PyJWTError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        OverflowError,
        OSError,
    ):
        raise InvalidTokenError from None


_bearer = HTTPBearer(auto_error=False, description="Access token from POST /auth/login.")


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """FastAPI dependency: the caller from a Bearer access token, or a 401.

    Raises:
        AuthenticationError: no ``Authorization: Bearer`` header (code ``unauthenticated``).
        InvalidTokenError: the token is invalid, expired or not an access token.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError
    claims = decode_token(credentials.credentials, expected_type="access")
    return CurrentUser(id=claims.sub, token_id=claims.jti)


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]

SiteAdminChecker = Callable[[AsyncSession, UUID], Awaitable[bool]]
_site_admin_checker: SiteAdminChecker | None = None


def register_site_admin_checker(checker: SiteAdminChecker | None) -> None:
    """Register the function that says whether a user is an active site admin.

    ``core`` cannot import ``identity``, so ``create_app`` registers ``identity``'s checker here.
    Passing ``None`` clears it (``require_site_admin`` then fails closed).
    """
    global _site_admin_checker  # noqa: PLW0603
    _site_admin_checker = checker


async def require_site_admin(user: CurrentUserDep, session: SessionDep) -> CurrentUser:
    """FastAPI dependency: the caller, if they are a site admin right now, else a 403.

    The role is read from the database on every request (no caching, no token claim), so a
    revoked admin or a deactivated account loses access at once.

    Raises:
        PermissionDeniedError: the user is not an active site admin (code ``forbidden``).
        RuntimeError: no checker is registered (fails closed, a 500).
    """
    if _site_admin_checker is None:
        raise RuntimeError("no site-admin checker registered; call register_site_admin_checker")
    if not await _site_admin_checker(session, user.id):
        raise PermissionDeniedError
    return user


SiteAdminDep = Annotated[CurrentUser, Depends(require_site_admin)]


def client_mode(request: Request) -> ClientMode:
    """Return ``mobile`` only for ``X-Client: mobile`` without an ``Origin`` header, else ``web``.

    Browsers always send ``Origin`` on a POST and page script cannot remove it, so script running
    in a browser (XSS included) can never select the mobile mode that returns the refresh token in
    the response body.
    """
    header = request.headers.get(CLIENT_HEADER, "").strip().lower()
    if header == "mobile" and "origin" not in request.headers:
        return "mobile"
    return "web"


def set_refresh_cookie(response: Response, token: IssuedToken) -> None:
    """Set the refresh token as an HttpOnly, Secure, SameSite=Strict cookie on the auth path."""
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token.token,
        max_age=token.expires_in,
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response) -> None:
    """Expire the refresh cookie (same name, path and attributes, ``Max-Age=0``)."""
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        "",
        max_age=0,
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )
