"""The player profile view (S-2.2.3): who a player is and which playgroups they share with you."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.identity import services as identity_services
from app.playgroups import repository
from app.playgroups.models import Playgroup
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

    The viewer sees their own profile always. Anyone else is visible only when they share a
    playgroup: both hold an active membership in it, or the target is a guest whose
    ``home_playgroup_id`` is a playgroup the viewer is an active member of. Former memberships
    do not count.

    A merged player is resolved to the final survivor (``redirected_from`` is then set). The
    redirect needs both: the viewer could open the *requested* guest by the rule above (or is the
    survivor), and the viewer can open the survivor. Otherwise it is a 404, so an old id never
    shows that a guest was merged to someone who could not have seen that guest.

    Raises:
        NotFoundError: no such player, a merge chain that does not end at a player, or not
            visible. All indistinguishable (404 ``not_found``).
    """
    viewer_id = await player_id_for_user(session, viewer_user_id)
    requested, target = await identity_services.resolve_merge(session, player_id)
    merged = requested is not None and requested.merged_into_id is not None

    # Always run the shared-playgroup query, so an unknown player costs what a hidden unmerged
    # one does. A merged id costs one lookup per hop plus one more visibility query.
    groups = await _shared(session, viewer_id, target)
    if target is None or (target.id != viewer_id and not groups):
        raise NotFoundError
    if merged and target.id != viewer_id and not await _shared(session, viewer_id, requested):
        raise NotFoundError
    return PlayerProfile(
        id=target.id,
        display_name=target.display_name,
        avatar_color=target.avatar_color,
        is_guest=target.is_guest,
        member_since=target.created_at,
        common_playgroups=[CommonPlaygroup(id=g.id, name=g.name) for g in groups],
        redirected_from=player_id if merged else None,
    )


async def _shared(
    session: AsyncSession, viewer_id: UUID, target: identity_services.PlayerInfo | None
) -> list[Playgroup]:
    """The playgroups the viewer shares with ``target`` (a guest also through its home group)."""
    return await repository.common_playgroups(
        session,
        viewer_id,
        _NO_PLAYER if target is None else target.id,
        None if target is None or not target.is_guest else target.home_playgroup_id,
    )
