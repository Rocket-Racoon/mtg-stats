"""API shapes of the ``playgroups`` domain."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.paging import Page


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
