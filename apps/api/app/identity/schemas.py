"""API shapes of the ``identity`` domain."""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.identity.services import validate_password


class LoginRequest(BaseModel):
    """Credentials. Only bounds are checked here; registration rules do not apply to sign-in."""

    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    """Body of ``POST /auth/change-password``. The new password follows the registration rules."""

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _new_password(cls, value: str) -> str:
        validate_password(value)
        return value


class RefreshRequest(BaseModel):
    """Body of ``POST /auth/refresh`` and ``POST /auth/logout`` for mobile clients."""

    refresh_token: str | None = Field(
        default=None,
        max_length=4096,
        description="Mobile clients only. Web clients send the cookie; a body token is ignored.",
    )


class MeResponse(BaseModel):
    """The signed-in user. ``is_site_admin`` is for showing or hiding UI only; the server
    re-checks the role on every protected request."""

    id: UUID
    username: str
    display_name: str
    is_site_admin: bool
