"""Guest players of a playgroup: add and rename (S-2.3.1).

A guest is an identity ``Player`` with ``is_guest`` and ``home_playgroup_id`` set and no user, so
it has no credentials. ``identity`` owns the table; this module owns the playgroup rules: who may
add or rename a guest, and that a name is unique inside the playgroup.

**Uniqueness.** A name is "taken" when its :func:`~app.identity.services.display_name_key` (case
and accents ignored) equals that of an active member or of another guest of the playgroup. Member
names live in ``players`` and membership in ``memberships``, so no single database constraint can
express the rule. Instead every guest write first locks the playgroup row (``SELECT ... FOR
UPDATE``), then reads the names and writes; two concurrent adds or renames of one playgroup run
one after the other, and the second sees the first's guest. The lock is held to the end of the
request's transaction (the caller commits). Every guest write must go through this module.

**Lock order.** The playgroup row first, before any ``players`` row. A later claim or merge
story that touches guests must take ``repository.lock_playgroup`` before locking or updating
players, or it can deadlock against a rename.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.identity import services as identity_services
from app.identity.services import PlayerInfo
from app.playgroups import repository
from app.playgroups.models import ROLE_ADMIN, ROLE_OWNER
from app.playgroups.services.access import MembershipInfo


class PlayerNameTakenError(ConflictError):
    """A member or guest of the playgroup already has this name (case and accents ignored)."""

    code = "player_name_taken"
    default_message = "A player with this name already exists in this playgroup."


@dataclass(frozen=True)
class GuestInfo:
    """A guest player of a playgroup."""

    player_id: UUID
    playgroup_id: UUID
    display_name: str
    avatar_color: str
    created_by: UUID | None
    created_at: datetime


def _info(player: PlayerInfo, playgroup_id: UUID) -> GuestInfo:
    return GuestInfo(
        player_id=player.id,
        playgroup_id=playgroup_id,
        display_name=player.display_name,
        avatar_color=player.avatar_color,
        created_by=player.created_by_id,
        created_at=player.created_at,
    )


async def _lock_and_check_caller(session: AsyncSession, access: MembershipInfo) -> str:
    """Lock the playgroup and return the caller's role, re-read after the lock.

    Raises:
        NotFoundError: the playgroup is gone or the caller is no longer an active member.
    """
    if await repository.lock_playgroup(session, access.playgroup_id) is None:
        raise NotFoundError
    membership = await repository.get_active_membership(
        session, access.playgroup_id, access.player_id, fresh=True
    )
    if membership is None:
        raise NotFoundError
    return membership.role


async def _ensure_name_free(
    session: AsyncSession, playgroup_id: UUID, name: str, *, except_player: UUID | None = None
) -> None:
    """Raise ``PlayerNameTakenError`` if a member or guest (other than ``except_player``) has it."""
    key = identity_services.display_name_key(name)
    member_ids = await repository.active_member_player_ids(session, playgroup_id)
    members = await identity_services.get_players(session, member_ids)
    guests = await identity_services.list_guest_players(session, playgroup_id)
    for player in (*members.values(), *guests):
        if player.id == except_player:
            continue
        if identity_services.display_name_key(player.display_name) == key:
            raise PlayerNameTakenError(
                f"A player named '{name}' already exists in this playgroup",
                details={"display_name": name},
            )


async def add_guest(
    session: AsyncSession, access: MembershipInfo, *, display_name: str
) -> GuestInfo:
    """Add a guest player to the caller's playgroup. Flushes; the caller commits.

    Any active member may. The name is trimmed and checked (1-32 characters).

    Raises:
        NotFoundError: the caller is not an active member.
        InvalidDisplayNameError: the name breaks the display-name rules (422).
        PlayerNameTakenError: a member or guest already has the name (409).
    """
    name = identity_services.normalize_display_name(display_name)
    await _lock_and_check_caller(session, access)
    await _ensure_name_free(session, access.playgroup_id, name)
    guest = await identity_services.create_guest_player(
        session,
        display_name=name,
        home_playgroup_id=access.playgroup_id,
        created_by_id=access.player_id,
    )
    return _info(guest, access.playgroup_id)


async def rename_guest(
    session: AsyncSession, access: MembershipInfo, *, player_id: UUID, display_name: str
) -> GuestInfo:
    """Rename a guest of the caller's playgroup. Flushes; the caller commits.

    Allowed for the guest's creator, the playgroup's owner and its admins; the role is read on the
    row read after the lock, not from ``access``. Renaming to the guest's own name in another case
    is allowed.

    Raises:
        NotFoundError: the caller is not a member, or the player is not an unmerged guest of this
            playgroup.
        PermissionDeniedError: the caller is a plain member who did not add the guest.
        InvalidDisplayNameError: the name breaks the display-name rules (422).
        PlayerNameTakenError: another member or guest has the name (409).
    """
    name = identity_services.normalize_display_name(display_name)
    role = await _lock_and_check_caller(session, access)
    guest = await identity_services.get_player(session, player_id)
    if (
        guest is None
        or not guest.is_guest
        or guest.merged_into_id is not None
        or guest.home_playgroup_id != access.playgroup_id
    ):
        raise NotFoundError
    if role not in (ROLE_OWNER, ROLE_ADMIN) and guest.created_by_id != access.player_id:
        raise PermissionDeniedError
    await _ensure_name_free(session, access.playgroup_id, name, except_player=player_id)
    renamed = await identity_services.rename_guest_player(session, player_id, display_name=name)
    return _info(renamed, access.playgroup_id)
