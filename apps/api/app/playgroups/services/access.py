"""Playgroup access dependencies (S-2.4.1): ``require_member`` and its stricter variants.

Every ``/playgroups/{playgroup_id}/...`` route (and routes of higher domains scoped to a
playgroup) declares one of these. They answer 404 ``not_found`` to anyone who is not an active
member, so a playgroup's existence is never revealed, and an unparseable id is a 404 too. A
member without enough role gets 403 ``forbidden``. The site-admin role grants no access
(S-2.1.11 AC6). The dependencies live here, not in ``core.auth``: higher domains (``decks``,
``games``) already depend on ``playgroups.services``, so they use them without importing models.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUserDep
from app.core.db import SessionDep
from app.core.errors import NotFoundError, PermissionDeniedError
from app.identity import services as identity_services
from app.playgroups import repository
from app.playgroups.models import ROLE_ADMIN, ROLE_OWNER


@dataclass(frozen=True)
class MembershipInfo:
    """The caller's active membership of one playgroup."""

    playgroup_id: UUID
    player_id: UUID
    user_id: UUID
    role: str
    joined_at: datetime

    @property
    def is_admin(self) -> bool:
        """True for the owner and for admins."""
        return self.role in (ROLE_OWNER, ROLE_ADMIN)

    @property
    def is_owner(self) -> bool:
        """True for the owner."""
        return self.role == ROLE_OWNER


async def player_id_for_user(session: AsyncSession, user_id: UUID) -> UUID:
    """The player id of a user. A user without a player cannot use playgroups (404)."""
    player = await identity_services.get_player_for_user(session, user_id)
    if player is None:
        raise NotFoundError
    return player.id


async def get_membership(
    session: AsyncSession, *, playgroup_id: UUID, user_id: UUID
) -> MembershipInfo | None:
    """The user's active membership of the playgroup, or None (former members are not members)."""
    player = await identity_services.get_player_for_user(session, user_id)
    if player is None:
        return None
    row = await repository.get_active_membership(session, playgroup_id, player.id)
    if row is None:
        return None
    return MembershipInfo(
        playgroup_id=row.playgroup_id,
        player_id=row.player_id,
        user_id=user_id,
        role=row.role,
        joined_at=row.joined_at,
    )


async def require_member(
    playgroup_id: str, user: CurrentUserDep, session: SessionDep
) -> MembershipInfo:
    """Dependency: the caller's membership of ``{playgroup_id}``, else a 404.

    Raises:
        NotFoundError: the id is not a UUID, the playgroup does not exist, or the caller is not
            an active member of it (all indistinguishable).
    """
    try:
        parsed = UUID(playgroup_id)
    except ValueError:
        raise NotFoundError from None
    membership = await get_membership(session, playgroup_id=parsed, user_id=user.id)
    if membership is None:
        raise NotFoundError
    return membership


async def require_playgroup_admin(
    membership: Annotated[MembershipInfo, Depends(require_member)],
) -> MembershipInfo:
    """Dependency: a member who is the owner or an admin, else a 404 (non-member) or 403."""
    if not membership.is_admin:
        raise PermissionDeniedError
    return membership


async def require_playgroup_owner(
    membership: Annotated[MembershipInfo, Depends(require_member)],
) -> MembershipInfo:
    """Dependency: a member who is the owner, else a 404 (non-member) or 403."""
    if not membership.is_owner:
        raise PermissionDeniedError
    return membership


MemberDep = Annotated[MembershipInfo, Depends(require_member)]
PlaygroupAdminDep = Annotated[MembershipInfo, Depends(require_playgroup_admin)]
PlaygroupOwnerDep = Annotated[MembershipInfo, Depends(require_playgroup_owner)]
