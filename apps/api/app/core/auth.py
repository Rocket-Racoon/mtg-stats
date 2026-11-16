"""JWT issuing and decoding, and the ``current_user`` dependency (S-2.1.4).

Tokens are HS256 JWTs signed with ``JWT_SECRET``. Both kinds carry ``sub``, ``exp``, ``iat``,
``jti`` and ``type`` (``access`` or ``refresh``). Decoding accepts only HS256, so ``alg=none`` and
algorithm-confusion tokens fail. ``current_user`` only decodes the token; it never reads the
database, so a deactivated user's access token works until it expires (at most the access
lifetime). This module imports no domain.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError

JWT_ALGORITHM = "HS256"
REQUIRED_CLAIMS = ("sub", "exp", "iat", "jti", "type")

TokenType = Literal["access", "refresh"]


class InvalidTokenError(AuthenticationError):
    """The token is missing a claim, malformed, expired, mis-signed or of the wrong type."""

    code = "invalid_token"
    default_message = "Invalid or expired token."


@dataclass(frozen=True)
class IssuedToken:
    """A signed token with the data a caller may need to store or report."""

    token: str
    jti: str
    expires_at: datetime


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
    return IssuedToken(token=token, jti=jti, expires_at=expires_at)


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
        return TokenClaims(
            sub=UUID(payload["sub"]),
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
        raise AuthenticationError("Authentication required.")
    claims = decode_token(credentials.credentials, expected_type="access")
    return CurrentUser(id=claims.sub, token_id=claims.jti)


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]
