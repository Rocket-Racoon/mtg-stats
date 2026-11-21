"""API shapes of the ``playgroups`` domain."""

import unicodedata
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.core.paging import Page
from app.playgroups.models import (
    PLAYGROUP_DESCRIPTION_MAX_LENGTH,
    PLAYGROUP_NAME_MAX_LENGTH,
    PLAYGROUP_NAME_MIN_LENGTH,
)

_DESCRIPTION_ALLOWED_CONTROLS = frozenset({"\n", "\r", "\t"})


def _reject_control_characters(value: str) -> str:
    """Reject control characters (category Cc, NUL included) and lone surrogates (Cs).

    Postgres cannot store NUL and psycopg cannot encode a lone surrogate, so without this they
    would be a 500. Runs after trimming, as for identity display names.
    """
    if any(unicodedata.category(ch) in ("Cc", "Cs") for ch in value):
        raise ValueError("must not contain control characters")
    return value


def _reject_description_controls(value: str) -> str:
    """Like the name rule, but a description may hold newlines, carriage returns and tabs.

    A carriage return is accepted and stored as sent (no normalisation), so CRLF from a client
    comes back unchanged and the 500-character limit counts exactly what was sent.
    """
    if any(
        unicodedata.category(ch) in ("Cc", "Cs") and ch not in _DESCRIPTION_ALLOWED_CONTROLS
        for ch in value
    ):
        raise ValueError("must not contain control characters other than newline and tab")
    return value


PlaygroupName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=PLAYGROUP_NAME_MIN_LENGTH,
        max_length=PLAYGROUP_NAME_MAX_LENGTH,
    ),
    AfterValidator(_reject_control_characters),
]
PlaygroupDescription = Annotated[
    str,
    StringConstraints(max_length=PLAYGROUP_DESCRIPTION_MAX_LENGTH),
    AfterValidator(_reject_description_controls),
]


class InviteCreate(BaseModel):
    """Optional body of ``POST /invites``."""

    model_config = ConfigDict(strict=True)

    expires_in_days: int = Field(default=7, ge=1, le=30, description="Days until it expires.")
    max_uses: int = Field(default=1, ge=1, le=20, description="How many sign-ups it allows.")


class InviteOut(BaseModel):
    """An invite as listed. It never carries the token or the URL."""

    id: UUID
    status: Literal["active", "used", "expired", "revoked"]
    expires_at: datetime
    max_uses: int
    use_count: int
    revoked_at: datetime | None
    created_at: datetime


class InviteCreated(InviteOut):
    """A new invite. ``url`` is the only place the token ever appears."""

    url: str = Field(description="The shareable https link; shown once, never stored.")


class InvitePageOut(Page[InviteOut]):
    """A page of the caller's invites."""


class InvitePreviewRequest(BaseModel):
    """Body of ``POST /invites/preview``. The token travels in the body, never in a URL."""

    token: str = Field(min_length=1, max_length=128, description="The invite token.")


class InvitePreviewOut(BaseModel):
    """What the public preview shows: nothing but the inviter's name and the expiry."""

    inviter_display_name: str
    expires_at: datetime


class PlaygroupCreate(BaseModel):
    """Body of ``POST /playgroups``."""

    model_config = ConfigDict(strict=True)

    name: PlaygroupName = Field(description="3 to 50 characters (surrounding spaces are trimmed).")
    description: PlaygroupDescription = Field(default="", description="Up to 500 characters.")


class PlaygroupUpdate(BaseModel):
    """Body of ``PATCH /playgroups/{id}``. Send only the fields to change; null is rejected."""

    model_config = ConfigDict(strict=True)

    name: PlaygroupName | None = None
    description: PlaygroupDescription | None = None

    @model_validator(mode="after")
    def _no_explicit_null(self) -> Self:
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null; omit it to leave it unchanged")
        return self


class PlaygroupOut(BaseModel):
    """A playgroup with the caller's role in it."""

    id: UUID
    name: str
    description: str
    role: Literal["owner", "admin", "member"] = Field(description="The caller's role.")
    created_at: datetime
    updated_at: datetime
    joined_at: datetime = Field(description="When the caller joined.")


class PlaygroupPageOut(Page[PlaygroupOut]):
    """One page of the caller's playgroups."""
