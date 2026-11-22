"""The player picker and roster (S-2.2.4): a playgroup's members and guests, searchable and paged.

Membership and roles are in ``playgroups``; names are in ``identity``'s ``players``. A repository
may not join across domains, so ``playgroups`` reads the active members' ids and roles (one
query) and hands the ids to ``identity.services.search_playgroup_players``, which filters,
orders and pages members and the home guests in one SQL statement. ``playgroups`` then attaches
the roles. Memory is bounded by the member count (one UUID and role per active member, which is
uncapped but small in practice); the page itself is cut in SQL.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.paging import PageParams
from app.identity import services as identity_services
from app.playgroups import repository
from app.playgroups.services.access import MembershipInfo


@dataclass(frozen=True)
class PlaygroupPlayer:
    """A member or guest of a playgroup, as the picker and the roster show them."""

    player_id: UUID
    display_name: str
    avatar_color: str
    is_guest: bool
    role: str | None
    """``owner``, ``admin`` or ``member`` for a member; None for a guest."""
    created_by: UUID | None
    """For a guest, the player who added it (the rename rule needs it); None for a member."""


@dataclass(frozen=True)
class PlaygroupPlayerPage:
    """One page of a playgroup's players."""

    items: list[PlaygroupPlayer]
    next_cursor: str | None


async def list_playgroup_players(
    session: AsyncSession, access: MembershipInfo, *, query: str | None, params: PageParams
) -> PlaygroupPlayerPage:
    """The playgroup's active members, then its unmerged guests, each group alphabetical.

    Any active member may call it (``access`` proves it). ``query`` keeps the names with a word
    starting with it, ignoring case and accents; see ``identity.services.search_playgroup_players``
    for the order, the matching and the paging guarantees. A deactivated account that is still a
    member is listed, as on its profile.

    Raises:
        InvalidCursorError: the cursor is malformed (422 ``invalid_cursor``).
    """
    roles = await repository.active_member_roles(session, access.playgroup_id)
    page = await identity_services.search_playgroup_players(
        session,
        member_ids=roles.keys(),
        guest_playgroup_id=access.playgroup_id,
        query=query,
        cursor=params.cursor,
        limit=params.limit,
    )
    return PlaygroupPlayerPage(
        items=[
            PlaygroupPlayer(
                player_id=p.id,
                display_name=p.display_name,
                avatar_color=p.avatar_color,
                is_guest=p.is_guest,
                role=roles.get(p.id),
                created_by=p.created_by_id if p.is_guest else None,
            )
            for p in page.items
        ],
        next_cursor=page.next_cursor,
    )
