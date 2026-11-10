"""Queries on the ``system`` domain's own tables (and the connectivity probe)."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def ping_database(session: AsyncSession) -> None:
    """Run ``SELECT 1``; raises if the database cannot answer."""
    await session.execute(text("SELECT 1"))
