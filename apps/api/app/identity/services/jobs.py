"""Background jobs owned by the ``identity`` domain (architecture §7)."""

import logging
from typing import Any

from app.core.db import get_sessionmaker
from app.identity.services.auth import cleanup_refresh_tokens

logger = logging.getLogger(__name__)


async def cleanup_refresh_tokens_job(ctx: dict[str, Any]) -> int:
    """Daily: delete refresh tokens that expired more than 7 days ago; return how many.

    Opens its own session and commits. Idempotent: a re-run finds nothing more to delete.
    """
    async with get_sessionmaker()() as session:
        deleted = await cleanup_refresh_tokens(session)
        await session.commit()
    logger.info("deleted %d expired refresh token(s)", deleted)
    return deleted
