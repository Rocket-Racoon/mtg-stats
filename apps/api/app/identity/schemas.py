"""API shapes of the ``identity`` domain."""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Credentials. Only bounds are checked here; registration rules do not apply to sign-in."""

    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class RefreshRequest(BaseModel):
    """Body of ``POST /auth/refresh`` and ``POST /auth/logout`` for mobile clients."""

    refresh_token: str | None = Field(
        default=None,
        max_length=4096,
        description="Mobile clients only. Web clients send the cookie; a body token is ignored.",
    )
