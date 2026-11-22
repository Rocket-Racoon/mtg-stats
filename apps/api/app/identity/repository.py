"""Queries on the ``identity`` domain's own tables."""

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
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


async def get_user_by_username(
    session: AsyncSession, username: str, *, for_update: bool = False
) -> User | None:
    """Return the user whose username matches case-insensitively, or None.

    ``for_update`` locks the row ``FOR NO KEY UPDATE`` (see :func:`get_user_for_update`) and
    re-reads it if we had to wait.
    """
    query = select(User).where(func.lower(User.username) == username.lower())
    if for_update:
        query = query.with_for_update(key_share=True).execution_options(populate_existing=True)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def get_user_for_update(session: AsyncSession, user_id: UUID) -> User | None:
    """Return the user locked ``FOR NO KEY UPDATE`` (re-read if we waited), or None.

    Excludes every other writer and every :func:`get_user_shared` holder, but not the
    ``FOR KEY SHARE`` an ``INSERT`` into ``refresh_tokens`` takes for its foreign key.
    """
    result = await session.execute(
        select(User)
        .where(User.id == user_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_user_shared(session: AsyncSession, user_id: UUID) -> User | None:
    """Return the user locked ``FOR SHARE`` (re-read if we waited), or None.

    Waits for a pending password change and then sees its result; many holders can coexist.
    """
    result = await session.execute(
        select(User)
        .where(User.id == user_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
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
    """Revoke every unrevoked, unexpired token of a user; return how many.

    Expired rows are skipped (they cannot refresh anyway) so this UPDATE never overlaps the
    cleanup job's DELETE, which only touches rows expired for over a week.
    """
    result = await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
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


async def is_active_site_admin(session: AsyncSession, user_id: UUID) -> bool:
    """Whether this user exists, is active and is a site admin."""
    result = await session.execute(
        select(User.id).where(User.id == user_id, User.is_active, User.is_site_admin)
    )
    return result.scalar_one_or_none() is not None


async def lock_site_admin_change_rows(session: AsyncSession, user_id: UUID) -> list[User]:
    """Lock the target user and every active site admin ``FOR NO KEY UPDATE``, in id order.

    ``FOR NO KEY UPDATE`` conflicts with itself, so two role changes still exclude each other, but
    it does not block a refresh-token insert (a ``FOR KEY SHARE`` FK check) by a signing-in admin.

    One statement with a fixed ``ORDER BY id`` so concurrent callers lock in the same order. A
    row changed by a transaction we waited on is re-read (``populate_existing``).
    """
    result = await session.execute(
        select(User)
        .where(or_(User.id == user_id, and_(User.is_site_admin, User.is_active)))
        .order_by(User.id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars())


async def get_guests_of_playgroup(session: AsyncSession, playgroup_id: UUID) -> list[Player]:
    """Every guest whose home is this playgroup and who is not merged into an account."""
    result = await session.execute(
        select(Player).where(
            Player.home_playgroup_id == playgroup_id,
            Player.is_guest,
            Player.merged_into_id.is_(None),
        )
    )
    return list(result.scalars())
