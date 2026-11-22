"""The player profile view (S-2.2.3): who a player is and which playgroups they share with you."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.identity import services as identity_services
from app.playgroups import repository
from app.playgroups.services.access import player_id_for_user

_NO_PLAYER = UUID(int=0)


@dataclass(frozen=True)
class CommonPlaygroup:
    """A playgroup the viewer and the profile's player share."""

    id: UUID
    name: str


@dataclass(frozen=True)
class PlayerProfile:
    """A player's profile as one viewer may see it. Never stats, email or username."""

    id: UUID
    display_name: str
    avatar_color: str
    is_guest: bool
    member_since: datetime
    common_playgroups: list[CommonPlaygroup]
    redirected_from: UUID | None = None
    """Set when the requested id was a merged guest; ``id`` is then the surviving player."""


async def get_player_profile(
    session: AsyncSession, *, viewer_user_id: UUID, player_id: UUID
) -> PlayerProfile:
    """The profile of ``player_id`` for a signed-in viewer.

    A merged player is resolved to the final survivor (``redirected_from`` is then set) and
    visibility is judged on the survivor, so a viewer who shares nothing with the survivor
    learns nothing. The viewer sees their own profile always. Anyone else is visible only when
    they share a playgroup: both hold an active membership in it, or the target is a guest whose
    ``home_playgroup_id`` is a playgroup the viewer is an active member of. Former memberships
    do not count.

    Raises:
        NotFoundError: no such player, a merge chain that does not end at a player, or no shared
            playgroup. All indistinguishable (404 ``not_found``).
    """
    viewer_id = await player_id_for_user(session, viewer_user_id)
    target = await identity_services.resolve_merged_player(session, player_id)
    # Always run the shared-playgroup query, so a missing player costs what a hidden one does.
    groups = await repository.common_playgroups(
        session,
        viewer_id,
        _NO_PLAYER if target is None else target.id,
        None if target is None or not target.is_guest else target.home_playgroup_id,
    )
    if target is None or (target.id != viewer_id and not groups):
        raise NotFoundError
    return PlayerProfile(
        id=target.id,
        display_name=target.display_name,
        avatar_color=target.avatar_color,
        is_guest=target.is_guest,
        member_since=target.created_at,
        common_playgroups=[CommonPlaygroup(id=g.id, name=g.name) for g in groups],
        redirected_from=None if target.id == player_id else player_id,
    )
