"""Site-admin check, registered with ``core.auth`` for ``require_site_admin`` (S-2.1.2)."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.identity import repository


async def is_site_admin(session: AsyncSession, user_id: UUID) -> bool:
    """True only for an existing, active user whose ``is_site_admin`` flag is set."""
    return await repository.is_active_site_admin(session, user_id)
