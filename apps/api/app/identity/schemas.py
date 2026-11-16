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
    refresh_token: str
    token_type: Literal["bearer"]
    expires_in: int = Field(description="Access token lifetime in seconds.")
