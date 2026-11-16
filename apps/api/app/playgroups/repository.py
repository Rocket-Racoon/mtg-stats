"""Queries on the ``playgroups`` domain's own tables."""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.playgroups.models import Invite


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
