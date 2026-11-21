"""Create, read, rename and list playgroups (S-2.4.1)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.paging import PageParams, Sort, SortKey, paginate
from app.playgroups import repository
from app.playgroups.models import ROLE_ADMIN, ROLE_OWNER, Membership, Playgroup
from app.playgroups.services.access import MembershipInfo, player_id_for_user

_MY_PLAYGROUPS_SORT = Sort((SortKey(Membership.joined_at, True), SortKey(Membership.id, True)))


@dataclass(frozen=True)
class PlaygroupInfo:
    """A playgroup as seen by one member: its fields and the caller's role in it."""

    id: UUID
    name: str
    description: str
    role: str
    created_at: datetime
    updated_at: datetime
    joined_at: datetime


@dataclass(frozen=True)
class PlaygroupPage:
    """One page of the caller's playgroups."""

    items: list[PlaygroupInfo]
    next_cursor: str | None


def info_of(membership: Membership) -> PlaygroupInfo:
    """Snapshot a membership whose ``playgroup`` is loaded."""
    group = membership.playgroup
    return PlaygroupInfo(
        id=group.id,
        name=group.name,
        description=group.description,
        role=membership.role,
        created_at=group.created_at,
        updated_at=group.updated_at,
        joined_at=membership.joined_at,
    )


async def create_playgroup(
    session: AsyncSession, *, user_id: UUID, name: str, description: str = ""
) -> PlaygroupInfo:
    """Create a playgroup; the creator's player becomes its owner. Flushes; the caller commits.

    There is no cap on how many playgroups a user may own or belong to (decision C3). The name
    and description are validated by the schema (3-50 and up to 500 characters) and again by
    database check constraints.
    """
    player_id = await player_id_for_user(session, user_id)
    group = Playgroup(name=name, description=description)
    repository.add_playgroup(session, group)
    await session.flush()
    membership = Membership(playgroup_id=group.id, player_id=player_id, role=ROLE_OWNER)
    repository.add_membership(session, membership)
    await session.flush()
    membership.playgroup = group
    return info_of(membership)


async def get_playgroup(session: AsyncSession, access: MembershipInfo) -> PlaygroupInfo:
    """The playgroup of a membership obtained from a ``require_*`` dependency.

    The dependency's query already read the playgroup, so this runs no statement of its own.
    """
    group = access.playgroup
    return PlaygroupInfo(
        id=access.playgroup_id,
        name=group.name,
        description=group.description,
        role=access.role,
        created_at=group.created_at,
        updated_at=group.updated_at,
        joined_at=access.joined_at,
    )


async def update_playgroup(
    session: AsyncSession,
    access: MembershipInfo,
    *,
    name: str | None = None,
    description: str | None = None,
) -> PlaygroupInfo:
    """Rename and/or re-describe a playgroup. Flushes; the caller commits.

    ``access`` identifies the caller (from ``require_playgroup_admin``). The role is checked
    again on the membership row read here, not on ``access``, so a demotion between the
    dependency and this call cannot authorise the edit.
    """
    membership = await repository.get_active_membership(
        session, access.playgroup_id, access.player_id
    )
    if membership is None:
        raise NotFoundError
    if membership.role not in (ROLE_OWNER, ROLE_ADMIN):
        raise PermissionDeniedError
    group = membership.playgroup
    if name is not None:
        group.name = name
    if description is not None:
        group.description = description
    await session.flush()
    return info_of(membership)


async def list_playgroups(
    session: AsyncSession, *, user_id: UUID, params: PageParams
) -> PlaygroupPage:
    """Every playgroup the user belongs to, newest membership first, with their role in each."""
    player_id = await player_id_for_user(session, user_id)
    rows, next_cursor = await paginate(
        session, repository.active_memberships_of(player_id), _MY_PLAYGROUPS_SORT, params
    )
    return PlaygroupPage(items=[info_of(m) for m in rows], next_cursor=next_cursor)
