"""Helpers for the S-2.1.4 sign-in and token tests: hand-built JWTs."""

import base64
import json
import os
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import jwt

from app.core.auth import TokenType, issue_token

ACCESS = cast("TokenType", "access")
REFRESH = cast("TokenType", "refresh")
OTHER_SECRET = "a-completely-different-secret-0123456789"  # secrets-check: allow


def secret() -> str:
    """The forced test ``JWT_SECRET`` (see ``conftest.py``)."""
    return os.environ["JWT_SECRET"]


def issue(subject: UUID, token_type: TokenType = ACCESS, *, now: datetime | None = None) -> str:
    """A real token from ``issue_token``."""
    return issue_token(subject, token_type, now=now).token


def claims_for(subject: UUID, *, token_type: str = "access", ttl: int = 900) -> dict[str, Any]:  # noqa: S107
    """A full valid claim set, with epoch-second ints."""
    iat = int(datetime.now(UTC).timestamp())
    return {
        "sub": str(subject),
        "iat": iat,
        "exp": iat + ttl,
        "jti": "0123456789abcdef",
        "type": token_type,
    }


def encode(claims: dict[str, Any], *, key: str | None = None, algorithm: str = "HS256") -> str:
    """Sign arbitrary claims with PyJWT."""
    return jwt.encode(claims, key or secret(), algorithm=algorithm)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unsigned_token(claims: dict[str, Any], *, alg: str = "none") -> str:
    """An ``alg=none`` token built by hand: header.payload with an empty signature."""
    header = _b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims).encode())
    return f"{header}.{payload}."


def tamper_signature(token: str) -> str:
    """Change the first signature character so the signature no longer matches."""
    head, _, signature = token.rpartition(".")
    flipped = "A" if signature[0] != "A" else "B"
    return f"{head}.{flipped}{signature[1:]}"


def tamper_payload(token: str, **changes: Any) -> str:
    """Rewrite payload claims but keep the old signature."""
    header, payload, signature = token.split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data.update(changes)
    return f"{header}.{_b64(json.dumps(data).encode())}.{signature}"
