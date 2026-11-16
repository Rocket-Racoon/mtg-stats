"""Queries on the ``identity`` domain's own tables."""

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import Player, RefreshToken, User


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


async def get_user(session: AsyncSession, user_id: UUID) -> User | None:
    """Return the user with this id, or None."""
    return await session.get(User, user_id)


def add_refresh_token(session: AsyncSession, row: RefreshToken) -> None:
    """Add a refresh-token row to the session."""
    session.add(row)


async def get_refresh_token_for_update(
    session: AsyncSession, token_hash: str
) -> RefreshToken | None:
    """Return the refresh-token row with this hash, locked ``FOR UPDATE``, or None."""
    result = await session.execute(
        select(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def revoke_family(session: AsyncSession, family_id: UUID, now: datetime) -> None:
    """Revoke every unrevoked token of a family."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session="fetch")
    )


async def revoke_user_tokens(session: AsyncSession, user_id: UUID, now: datetime) -> int:
    """Revoke every unrevoked token of a user; return how many."""
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session="fetch")
        .returning(RefreshToken.id)
    )
    return len(result.all())


async def delete_refresh_tokens_expired_before(session: AsyncSession, cutoff: datetime) -> int:
    """Delete refresh tokens that expired before ``cutoff``; return how many."""
    result = await session.execute(
        delete(RefreshToken)
        .where(RefreshToken.expires_at < cutoff)
        .execution_options(synchronize_session=False)
        .returning(RefreshToken.id)
    )
    return len(result.all())
