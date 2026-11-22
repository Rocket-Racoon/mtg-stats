"""Change member roles and transfer ownership (S-2.4.3).

Both operations lock the playgroup's active memberships (``SELECT ... FOR UPDATE``, id order)
before reading any role. Concurrent role changes and transfers on one playgroup therefore run
one after the other, and each re-reads the roles committed by the previous one. The caller's own
role is re-checked on the locked row, never taken from the dependency's snapshot.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.playgroups import repository
from app.playgroups.models import ROLE_ADMIN, ROLE_MEMBER, ROLE_OWNER, Membership
from app.playgroups.services.access import MembershipInfo
from app.playgroups.services.playgroups import PlaygroupInfo


class TransferOwnershipFirstError(ConflictError):
    """The owner tried to change their own role; ownership must be handed over instead."""

    code = "transfer_ownership_first"
    default_message = "Transfer ownership first."


class OwnershipTransferRequiredError(ConflictError):
    """The role endpoint was asked to set ``owner``; only the transfer endpoint moves it."""

    code = "ownership_transfer_required"
    default_message = "Use the transfer-ownership endpoint to make someone the owner."


class AlreadyOwnerError(ConflictError):
    """The owner tried to transfer ownership to themselves."""

    code = "already_owner"
    default_message = "You already own this playgroup."


@dataclass(frozen=True)
class MemberInfo:
    """A member's role in a playgroup."""

    player_id: UUID
    role: str
    joined_at: datetime


async def _locked_owner_and_target(
    session: AsyncSession, access: MembershipInfo, target_player_id: UUID
) -> tuple[Membership, Membership]:
    """Lock the memberships; return the caller's row (owner re-checked) and the target's row.

    Raises:
        NotFoundError: the caller is no longer an active member, or the target is not one.
        PermissionDeniedError: the caller is no longer the owner.
    """
    rows = await repository.lock_active_memberships(session, access.playgroup_id)
    by_player = {row.player_id: row for row in rows}
    caller = by_player.get(access.player_id)
    if caller is None:
        raise NotFoundError
    if caller.role != ROLE_OWNER:
        raise PermissionDeniedError
    target = by_player.get(target_player_id)
    if target is None:
        raise NotFoundError
    return caller, target


async def change_member_role(
    session: AsyncSession, access: MembershipInfo, *, player_id: UUID, role: str
) -> MemberInfo:
    """Promote a member to admin or demote an admin to member. Flushes; the caller commits.

    Only the owner may do this (403 otherwise, decided before the target is looked up, so a
    non-owner learns nothing about other members). Edge cases: a target who is not an active
    member is 404; setting the current role again succeeds without change; changing the owner's
    role (only the owner themselves can be the target) is 409 ``transfer_ownership_first``;
    asking for ``owner`` is 409 ``ownership_transfer_required``.
    """
    _, target = await _locked_owner_and_target(session, access, player_id)
    if role == ROLE_OWNER:
        raise OwnershipTransferRequiredError
    if role not in (ROLE_ADMIN, ROLE_MEMBER):
        raise ValueError(role)
    if target.role == ROLE_OWNER:
        raise TransferOwnershipFirstError
    if target.role != role:
        target.role = role
        await session.flush()
    return MemberInfo(player_id=target.player_id, role=target.role, joined_at=target.joined_at)


async def transfer_ownership(
    session: AsyncSession, access: MembershipInfo, *, player_id: UUID
) -> PlaygroupInfo:
    """Make another active member the owner; the previous owner becomes an admin.

    Flushes; the caller commits, so both changes land in one transaction. The old owner is
    demoted and flushed before the new owner is promoted, because the partial unique index
    allowing one active owner cannot be deferred. Returns the playgroup with the caller's new
    role. A target who is not an active member is 404; the owner themselves is 409
    ``already_owner``.
    """
    caller, target = await _locked_owner_and_target(session, access, player_id)
    if target.id == caller.id:
        raise AlreadyOwnerError
    caller.role = ROLE_ADMIN
    await session.flush()
    target.role = ROLE_OWNER
    await session.flush()
    group = access.playgroup
    return PlaygroupInfo(
        id=access.playgroup_id,
        name=group.name,
        description=group.description,
        role=caller.role,
        created_at=group.created_at,
        updated_at=group.updated_at,
        joined_at=caller.joined_at,
    )
