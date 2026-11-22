"""Create, list, revoke, validate and use invite links (S-2.1.2, S-2.4.2).

Two kinds of invite share one table. A **site-level** invite (``playgroup_id`` is None) only lets
someone register; a site admin creates it. A **playgroup** invite is bound to a playgroup, which
its owner or an admin creates; registering with it, or confirming it as a signed-in user, also
makes the person a member.

Lock order, everywhere: the invite row first (``FOR UPDATE``), then membership rows. Joining
takes only the invite lock and inserts a membership, so no path locks memberships and then an
invite. Use counts stay correct because every use (registration and join) goes through
:func:`lock_invite` then :func:`consume_invite`, which re-checks the status on the locked row;
``use_count <= max_uses`` is also a check constraint.
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, DomainError, NotFoundError, PermissionDeniedError
from app.core.links import public_link
from app.core.paging import PageParams, created_at_desc, paginate
from app.identity import services as identity_services
from app.playgroups import repository
from app.playgroups.models import ROLE_ADMIN, ROLE_MEMBER, ROLE_OWNER, Invite, Membership
from app.playgroups.services.access import MembershipInfo, player_id_for_user
from app.playgroups.services.playgroups import PlaygroupInfo, info_of

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
    playgroup_id: UUID | None = None


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
        playgroup_id=invite.playgroup_id,
    )


async def create_invite(
    session: AsyncSession,
    *,
    created_by: UUID,
    expires_in_days: int = DEFAULT_EXPIRES_IN_DAYS,
    max_uses: int = DEFAULT_MAX_USES,
    playgroup_id: UUID | None = None,
) -> CreatedInvite:
    """Create an invite and return it with its ``https`` URL.

    The caller has already been authorised: a site admin for a site-level invite (S-2.1.2), or
    :func:`create_playgroup_invite` for a playgroup's. Only the token's SHA-256 is
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
        playgroup_id=playgroup_id,
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


async def _admin_row(session: AsyncSession, access: MembershipInfo) -> Membership:
    """The caller's membership row, re-read; owner or admin is re-checked on it, not on ``access``.

    Raises:
        NotFoundError: the caller left or was removed since the dependency ran.
        PermissionDeniedError: the caller is no longer the owner or an admin.
    """
    row = await repository.get_active_membership(session, access.playgroup_id, access.player_id)
    if row is None:
        raise NotFoundError
    if row.role not in (ROLE_OWNER, ROLE_ADMIN):
        raise PermissionDeniedError
    return row


async def create_playgroup_invite(
    session: AsyncSession,
    access: MembershipInfo,
    *,
    expires_in_days: int = DEFAULT_EXPIRES_IN_DAYS,
    max_uses: int = DEFAULT_MAX_USES,
) -> CreatedInvite:
    """Create an invite bound to the caller's playgroup. Owner or admin only. Flushes only.

    Raises:
        NotFoundError: the caller is no longer a member.
        PermissionDeniedError: the caller is no longer the owner or an admin (403).
    """
    await _admin_row(session, access)
    return await create_invite(
        session,
        created_by=access.user_id,
        expires_in_days=expires_in_days,
        max_uses=max_uses,
        playgroup_id=access.playgroup_id,
    )


async def list_playgroup_invites(
    session: AsyncSession, access: MembershipInfo, *, params: PageParams
) -> InvitePage:
    """Every invite of the playgroup (whoever created it), newest first, as a keyset page.

    Raises:
        NotFoundError, PermissionDeniedError: as for :func:`create_playgroup_invite`.
    """
    await _admin_row(session, access)
    rows, next_cursor = await paginate(
        session,
        repository.invites_of_playgroup(access.playgroup_id),
        created_at_desc(Invite),
        params,
    )
    now = datetime.now(UTC)
    return InvitePage(items=[_info(row, now) for row in rows], next_cursor=next_cursor)


async def revoke_playgroup_invite(
    session: AsyncSession, access: MembershipInfo, *, invite_id: UUID
) -> InviteInfo:
    """Revoke an invite of the playgroup, whoever created it. Idempotent.

    Raises:
        NotFoundError: the invite is unknown or belongs to another playgroup, or the caller left.
        PermissionDeniedError: the caller is no longer the owner or an admin.
    """
    await _admin_row(session, access)
    invite = await repository.get_playgroup_invite(session, access.playgroup_id, invite_id)
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


@dataclass(frozen=True)
class InvitePreview:
    """What a visitor may see about an invite before registering."""

    inviter_display_name: str
    expires_at: datetime
    playgroup_name: str | None = None
    """The playgroup's name for a playgroup invite; None for a site-level one."""


async def preview_invite(session: AsyncSession, token: str) -> InvitePreview:
    """Describe an active invite for the public preview.

    Shows the inviter's display name, the expiry and, for a playgroup invite, the playgroup's
    name. Nothing else about the playgroup (no id, description or members).

    Raises:
        InviteNoLongerValidError: revoked, expired, used up or unknown (410).
    """
    info = await validate_invite(session, token)
    player = await identity_services.get_player_for_user(session, info.created_by)
    playgroup = (
        await repository.get_playgroup(session, info.playgroup_id)
        if info.playgroup_id is not None
        else None
    )
    return InvitePreview(
        inviter_display_name=player.display_name if player is not None else "",
        expires_at=info.expires_at,
        playgroup_name=playgroup.name if playgroup is not None else None,
    )


async def consume_invite(session: AsyncSession, invite_id: UUID, *, player_id: UUID) -> None:
    """Count one use of an invite and, for a playgroup invite, add ``player_id`` as a member.

    Call it after :func:`lock_invite` in the same session. It locks the row again (a no-op for
    the same transaction) and re-checks the status, so it never takes a use past ``max_uses``.
    The new membership has the role ``member`` and a fresh ``joined_at``; a former membership
    row is left as history. The caller must not already be an active member (see
    :func:`join_with_invite`); the partial unique index would raise ``IntegrityError``. Flushes;
    never commits.

    Raises:
        InviteNoLongerValidError: the invite is gone, or is no longer active.
    """
    invite = await repository.get_invite_for_update(session, invite_id)
    if invite is None or invite_status(invite) != "active":
        raise InviteNoLongerValidError
    invite.use_count += 1
    if invite.playgroup_id is not None:
        repository.add_membership(
            session,
            Membership(playgroup_id=invite.playgroup_id, player_id=player_id, role=ROLE_MEMBER),
        )
    await session.flush()


@dataclass(frozen=True)
class JoinResult:
    """The outcome of confirming a playgroup invite."""

    already_member: bool
    """True when the user was already an active member: nothing changed, no use was counted."""
    playgroup: PlaygroupInfo
    """The playgroup with the caller's role in it."""


async def join_with_invite(session: AsyncSession, *, user_id: UUID, token: str) -> JoinResult:
    """Join the invite's playgroup as the signed-in user (the "Join <P>" confirmation).

    In one transaction: lock the invite (:func:`lock_invite`), then either report that the user
    is already an active member (no use counted; the owner and admins included) or count one use
    and insert a ``member`` membership. A former member gets a new row. If a concurrent join of
    the same user through another invite wins the unique index, the use is rolled back with a
    savepoint and the result is ``already_member``. Never commits.

    Raises:
        AuthenticationError: the account is unknown or deactivated (401).
        NotFoundError: the user has no player.
        InviteNoLongerValidError: revoked, expired, used up or unknown, or a site-level invite
            that has no playgroup to join (410).
    """
    if await identity_services.get_me(session, user_id) is None:
        raise AuthenticationError
    player_id = await player_id_for_user(session, user_id)
    invite = await lock_invite(session, token)
    if invite.playgroup_id is None:
        raise InviteNoLongerValidError
    playgroup_id = invite.playgroup_id
    existing = await repository.get_active_membership(session, playgroup_id, player_id)
    if existing is not None:
        return JoinResult(already_member=True, playgroup=info_of(existing))
    try:
        async with session.begin_nested():
            await consume_invite(session, invite.id, player_id=player_id)
    except IntegrityError:
        raced = await repository.get_active_membership(session, playgroup_id, player_id)
        if raced is None:
            raise
        return JoinResult(already_member=True, playgroup=info_of(raced))
    joined = await repository.get_active_membership(session, playgroup_id, player_id)
    if joined is None:  # inserted a moment ago in this transaction
        raise RuntimeError("membership missing after join")
    return JoinResult(already_member=False, playgroup=info_of(joined))
