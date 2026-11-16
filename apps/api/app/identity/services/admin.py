"""Site-admin check (S-2.1.2), the role change (S-2.1.11) and the caller's own info."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.identity import repository
from app.identity.services.players import UserNotFoundError


class LastSiteAdminError(DomainError):
    """The change would leave no active site admin."""

    code = "last_site_admin"
    status_code = 409
    default_message = "at least one site admin is required"


@dataclass(frozen=True, slots=True)
class SiteAdminChange:
    """Outcome of ``set_site_admin``; ``old == new`` for a no-op."""

    user_id: UUID
    username: str
    old: bool
    new: bool
    at: datetime


@dataclass(frozen=True, slots=True)
class MeInfo:
    """The signed-in user's own view, read from the database."""

    id: UUID
    username: str
    display_name: str
    is_site_admin: bool


async def is_site_admin(session: AsyncSession, user_id: UUID) -> bool:
    """True only for an existing, active user whose ``is_site_admin`` flag is set."""
    return await repository.is_active_site_admin(session, user_id)


async def set_site_admin(session: AsyncSession, *, username: str, value: bool) -> SiteAdminChange:
    """Set a user's ``is_site_admin`` flag, keeping at least one active site admin.

    The target and all active site admins are locked ``FOR UPDATE`` first, so two concurrent
    revokes cannot both pass the check. Flushes; never commits. Setting the current value is a
    no-op that still returns the (equal) old and new values.

    Raises:
        UserNotFoundError: if no user has this username (case-insensitive).
        LastSiteAdminError: if revoking would leave zero active site admins.
    """
    found = await repository.get_user_by_username(session, username)
    if found is None:
        raise UserNotFoundError
    rows = await repository.lock_site_admin_change_rows(session, found.id)
    user = next((row for row in rows if row.id == found.id), None)
    if user is None:
        raise UserNotFoundError
    old = user.is_site_admin
    if old and not value:
        others = [r for r in rows if r.id != user.id and r.is_site_admin and r.is_active]
        if not others:
            raise LastSiteAdminError
    if old != value:
        user.is_site_admin = value
        await session.flush()
    return SiteAdminChange(
        user_id=user.id, username=user.username, old=old, new=value, at=datetime.now(UTC)
    )


async def get_me(session: AsyncSession, user_id: UUID) -> MeInfo | None:
    """Return the active user's own info, or None if the user is unknown or inactive."""
    user = await repository.get_user(session, user_id)
    if user is None or not user.is_active:
        return None
    player = await repository.get_player_for_user(session, user_id)
    return MeInfo(
        id=user.id,
        username=user.username,
        display_name=player.display_name if player is not None else "",
        is_site_admin=user.is_site_admin,
    )
