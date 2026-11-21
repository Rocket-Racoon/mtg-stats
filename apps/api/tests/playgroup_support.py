"""Helpers for the playgroups domain tests: accounts with players and membership rows."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.playgroups import repository
from app.playgroups.models import Membership
from tests.auth_support import issue
from tests.factories import PlayerFactory, UserFactory, create


async def make_user(session: AsyncSession) -> tuple[UUID, UUID, dict[str, str]]:
    """A user with a player; returns ``(user_id, player_id, Authorization header)``."""
    user = await create(session, UserFactory)
    player = await create(session, PlayerFactory, user_id=user.id)
    user_id, player_id = user.id, player.id
    await session.commit()  # the route's error path rolls the shared test session back
    return user_id, player_id, {"Authorization": f"Bearer {issue(user.id)}"}


async def join(
    session: AsyncSession,
    playgroup_id: UUID | str,
    player_id: UUID,
    role: str = "member",
    *,
    left: bool = False,
) -> Membership:
    """Add a membership (``left=True`` makes it a former one) and flush."""
    membership = Membership(
        playgroup_id=UUID(str(playgroup_id)),
        player_id=player_id,
        role=role,
        left_at=datetime.now(UTC) if left else None,
    )
    repository.add_membership(session, membership)
    await session.flush()
    await session.commit()
    return membership
