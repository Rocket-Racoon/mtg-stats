"""Create, list, revoke and validate invite links (S-2.1.2)."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError, NotFoundError
from app.core.links import public_link
from app.core.paging import PageParams, created_at_desc, paginate
from app.playgroups import repository
from app.playgroups.models import Invite

InviteStatus = Literal["active", "used", "expired", "revoked"]

DEFAULT_EXPIRES_IN_DAYS = 7
DEFAULT_MAX_USES = 1


class InviteNoLongerValidError(DomainError):
    """The invite is revoked, expired, used up or unknown; one answer for all four."""

    code = "invite_no_longer_valid"
    status_code = 410
    default_message = "Invite no longer valid."


@dataclass(frozen=True)
class InviteInfo:
    """A snapshot of an invite. Never carries the token."""

    id: UUID
    created_by: UUID
    status: InviteStatus
    expires_at: datetime
    max_uses: int
    use_count: int
    revoked_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class CreatedInvite:
    """A new invite and its shareable URL (the only time the token is available)."""

    invite: InviteInfo
    url: str


@dataclass(frozen=True)
class InvitePage:
    """One page of a user's invites."""

    items: list[InviteInfo]
    next_cursor: str | None


def hash_invite_token(token: str) -> str:
    """The SHA-256 hex digest stored in place of the token."""
    return hashlib.sha256(token.encode()).hexdigest()


def invite_status(invite: Invite, *, now: datetime | None = None) -> InviteStatus:
    """Derive the status: revoked, then used, then expired, else active."""
    if invite.revoked_at is not None:
        return "revoked"
    if invite.use_count >= invite.max_uses:
        return "used"
    if invite.expires_at <= (now or datetime.now(UTC)):
        return "expired"
    return "active"


def _info(invite: Invite, now: datetime | None = None) -> InviteInfo:
    return InviteInfo(
        id=invite.id,
        created_by=invite.created_by,
        status=invite_status(invite, now=now),
        expires_at=invite.expires_at,
        max_uses=invite.max_uses,
        use_count=invite.use_count,
        revoked_at=invite.revoked_at,
        created_at=invite.created_at,
    )


async def create_invite(
    session: AsyncSession,
    *,
    created_by: UUID,
    expires_in_days: int = DEFAULT_EXPIRES_IN_DAYS,
    max_uses: int = DEFAULT_MAX_USES,
) -> CreatedInvite:
    """Create an invite and return it with its ``https`` URL.

    The caller has already been authorised (a site admin, S-2.1.2). Only the token's SHA-256 is
    stored. The ranges (1-30 days, 1-20 uses) are enforced at the API edge and, for
    ``max_uses``, by a check constraint.
    """
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    invite = Invite(
        token_hash=hash_invite_token(token),
        created_by=created_by,
        expires_at=now + timedelta(days=expires_in_days),
        max_uses=max_uses,
        use_count=0,
        created_at=now,
    )
    repository.add_invite(session, invite)
    await session.flush()
    return CreatedInvite(invite=_info(invite, now), url=public_link("invite", token))


async def list_invites(session: AsyncSession, *, user_id: UUID, params: PageParams) -> InvitePage:
    """The caller's own invites, newest first, as a keyset page."""
    rows, next_cursor = await paginate(
        session, repository.invites_created_by(user_id), created_at_desc(Invite), params
    )
    now = datetime.now(UTC)
    return InvitePage(items=[_info(row, now) for row in rows], next_cursor=next_cursor)


async def revoke_invite(session: AsyncSession, *, invite_id: UUID, user_id: UUID) -> InviteInfo:
    """Revoke the caller's own invite. Idempotent: a revoked invite is returned unchanged.

    Raises:
        NotFoundError: the invite does not exist or belongs to someone else.
    """
    invite = await repository.get_invite_of(session, invite_id, user_id)
    if invite is None:
        raise NotFoundError
    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(UTC)
        await session.flush()
    return _info(invite)


async def _find_valid(session: AsyncSession, token: str, *, for_update: bool) -> InviteInfo:
    token_hash = hash_invite_token(token)
    invite = await repository.get_invite_by_hash(session, token_hash, for_update=for_update)
    if invite is None or not hmac.compare_digest(invite.token_hash, token_hash):
        raise InviteNoLongerValidError
    info = _info(invite)
    if info.status != "active":
        raise InviteNoLongerValidError
    return info


async def validate_invite(session: AsyncSession, token: str) -> InviteInfo:
    """Check that ``token`` names an active invite, without locking it.

    Raises:
        InviteNoLongerValidError: revoked, expired, used up or unknown (410).
    """
    return await _find_valid(session, token, for_update=False)


async def lock_invite(session: AsyncSession, token: str) -> InviteInfo:
    """:func:`validate_invite` with ``SELECT ... FOR UPDATE``, for the registration workflow."""
    return await _find_valid(session, token, for_update=True)
