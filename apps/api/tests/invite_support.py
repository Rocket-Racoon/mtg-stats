"""Helpers for the S-2.1.2 invite tests: users, tokens, requests and invite rows."""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import factory
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.playgroups.models import Invite
from tests.auth_support import issue
from tests.factories import BaseFactory, UserFactory, create

INVITES = "/api/v1/invites"
INVITE_PATH_PREFIX = "/invite/"


class InviteFactory(BaseFactory):
    """An active invite row; ``created_by`` is required and the hash is random."""

    class Meta:
        model = Invite

    id = factory.LazyFunction(uuid7)
    token_hash = factory.LazyFunction(lambda: hashlib.sha256(secrets.token_bytes(8)).hexdigest())
    expires_at = factory.LazyFunction(lambda: datetime.now(UTC) + timedelta(days=7))
    max_uses = 1
    use_count = 0
    revoked_at = None
    created_at = factory.LazyFunction(lambda: datetime.now(UTC))


async def make_account(
    session: AsyncSession, *, admin: bool = False, active: bool = True
) -> tuple[UUID, dict[str, str]]:
    """Create and commit a user; return ``(user_id, Authorization header)``."""
    user = await create(session, UserFactory, is_site_admin=admin, is_active=active)
    user_id = user.id
    await session.commit()
    return user_id, {"Authorization": f"Bearer {issue(user_id)}"}


async def make_invite(session: AsyncSession, created_by: UUID, **fields: Any) -> UUID:
    """Persist an invite row (``fields`` override the defaults), commit, return its id."""
    invite = await create(session, InviteFactory, created_by=created_by, **fields)
    invite_id = invite.id
    await session.commit()
    return invite_id


def token_from_url(url: str, public_web_url: str) -> str:
    """The token segment of an invite URL (asserts the URL shape)."""
    prefix = f"{public_web_url}{INVITE_PATH_PREFIX}"
    assert url.startswith(prefix)
    return url.removeprefix(prefix)


def random_bytes_of(token: str) -> int:
    """How many bytes a urlsafe-base64 token decodes to."""
    return len(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))


def sha256_hex(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_via_api(
    client: httpx.AsyncClient, headers: dict[str, str], body: dict[str, Any] | None = None
) -> httpx.Response:
    """``POST /invites``; with no JSON body at all when ``body`` is None."""
    if body is None:
        return await client.post(INVITES, headers=headers)
    return await client.post(INVITES, json=body, headers=headers)


async def raw_rows(session: AsyncSession) -> list[dict[str, Any]]:
    """Every column of every invite row, straight from SQL."""
    result = await session.execute(text("SELECT * FROM invites"))
    return [dict(r) for r in result.mappings().all()]
