"""Queries on the ``playgroups`` domain's own tables."""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.playgroups.models import Invite, Membership, Playgroup


def add_invite(session: AsyncSession, invite: Invite) -> None:
    """Add an invite to the session."""
    session.add(invite)


def invites_created_by(user_id: UUID) -> Select[Invite]:
    """A select of the invites this user created (ordering and paging are applied later)."""
    return select(Invite).where(Invite.created_by == user_id)


async def get_invite_by_hash(
    session: AsyncSession, token_hash: str, *, for_update: bool = False
) -> Invite | None:
    """Return the invite with this token hash, or None; optionally ``SELECT ... FOR UPDATE``."""
    stmt = select(Invite).where(Invite.token_hash == token_hash)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_invite_for_update(session: AsyncSession, invite_id: UUID) -> Invite | None:
    """Return the invite with this id, locked with ``SELECT ... FOR UPDATE``, or None."""
    stmt = (
        select(Invite)
        .where(Invite.id == invite_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_invite_of(session: AsyncSession, invite_id: UUID, user_id: UUID) -> Invite | None:
    """Return this invite if ``user_id`` created it, else None."""
    result = await session.execute(
        select(Invite).where(Invite.id == invite_id, Invite.created_by == user_id)
    )
    return result.scalar_one_or_none()


def add_playgroup(session: AsyncSession, playgroup: Playgroup) -> None:
    """Add a playgroup to the session."""
    session.add(playgroup)


def add_membership(session: AsyncSession, membership: Membership) -> None:
    """Add a membership to the session."""
    session.add(membership)


async def get_active_membership(
    session: AsyncSession, playgroup_id: UUID, player_id: UUID
) -> Membership | None:
    """The player's active membership of the playgroup (with the playgroup loaded), or None."""
    result = await session.execute(
        select(Membership)
        .options(joinedload(Membership.playgroup))
        .where(
            Membership.playgroup_id == playgroup_id,
            Membership.player_id == player_id,
            Membership.left_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


def active_memberships_of(player_id: UUID) -> Select[Membership]:
    """A select of the player's active memberships, with each playgroup eager-loaded.

    Ordering and paging are applied later.
    """
    return (
        select(Membership)
        .options(joinedload(Membership.playgroup))
        .where(Membership.player_id == player_id, Membership.left_at.is_(None))
    )


async def lock_active_memberships(session: AsyncSession, playgroup_id: UUID) -> list[Membership]:
    """Every active membership of the playgroup, locked with ``SELECT ... FOR UPDATE``.

    Rows are locked in id order so two callers never deadlock. ``populate_existing`` refreshes
    instances already in the session, so the roles returned are the committed ones.
    """
    result = await session.execute(
        select(Membership)
        .where(Membership.playgroup_id == playgroup_id, Membership.left_at.is_(None))
        .order_by(Membership.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(result.scalars())
