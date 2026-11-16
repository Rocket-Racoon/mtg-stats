"""Queries on the ``identity`` domain's own tables."""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import Player, User


async def get_player(session: AsyncSession, player_id: UUID) -> Player | None:
    """Return the player with this id, or None."""
    return await session.get(Player, player_id)


async def get_players(session: AsyncSession, player_ids: Collection[UUID]) -> list[Player]:
    """Return the players with these ids in one query; missing ids are absent."""
    result = await session.execute(select(Player).where(Player.id.in_(set(player_ids))))
    return list(result.scalars())


async def get_player_for_user(session: AsyncSession, user_id: UUID) -> Player | None:
    """Return the player owned by this user, or None."""
    result = await session.execute(select(Player).where(Player.user_id == user_id))
    return result.scalar_one_or_none()


def add_user(session: AsyncSession, user: User) -> None:
    """Add a user to the session."""
    session.add(user)


def add_player(session: AsyncSession, player: Player) -> None:
    """Add a player to the session."""
    session.add(player)


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    """Return the user whose username matches case-insensitively, or None."""
    result = await session.execute(
        select(User).where(func.lower(User.username) == username.lower())
    )
    return result.scalar_one_or_none()
