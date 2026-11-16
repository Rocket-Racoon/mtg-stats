"""API shapes of the ``identity`` domain."""

from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Credentials. Only bounds are checked here; registration rules do not apply to sign-in."""

    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    """Tokens issued by a successful sign-in."""

    access_token: str
    refresh_token: str | None = Field(
        default=None,
        description="Only for mobile clients (X-Client: mobile, no Origin). Web clients get the "
        "refresh token in the HttpOnly cookie instead and this key is absent.",
    )
    token_type: Literal["bearer"]
    expires_in: int = Field(description="Access token lifetime in seconds.")


class RefreshRequest(BaseModel):
    """Body of ``POST /auth/refresh`` and ``POST /auth/logout`` for mobile clients."""

    refresh_token: str | None = Field(
        default=None,
        max_length=4096,
        description="Mobile clients only. Web clients send the cookie; a body token is ignored.",
    )
