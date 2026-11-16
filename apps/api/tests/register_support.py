"""Helpers for the S-2.1.3 tests: inviters, tokens, register/preview requests and raw reads."""

import secrets
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.services import create_user_with_player
from app.playgroups.services import hash_invite_token
from tests.invite_support import make_invite
from tests.refresh_support import MOBILE

PREVIEW = "/api/v1/invites/preview"
REGISTER = "/api/v1/auth/register"
PASSWORD = "correct horse battery"
ENVELOPE_KEYS = {"code", "message", "details", "request_id"}


def new_token() -> str:
    return secrets.token_urlsafe(32)


async def make_inviter(session: AsyncSession, display_name: str = "Grace Hopper") -> UUID:
    """Create and commit a member with a player; return the user id."""
    info = await create_user_with_player(
        session,
        username=f"inviter{secrets.token_hex(4)}",
        password=PASSWORD,
        display_name=display_name,
    )
    assert info.user_id is not None
    await session.commit()
    return info.user_id


async def make_token_invite(session: AsyncSession, created_by: UUID, **fields: Any) -> str:
    """Persist an invite whose token we know (its hash is stored); return the token."""
    token = new_token()
    await make_invite(session, created_by, token_hash=hash_invite_token(token), **fields)
    return token


def register_body(token: str, **overrides: Any) -> dict[str, Any]:
    """A valid registration body; an override of ``None`` drops that key."""
    body: dict[str, Any] = {
        "invite_token": token,
        "username": "newbie",
        "password": PASSWORD,
        "display_name": "New Bie",
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


async def register(
    client: httpx.AsyncClient,
    token: str,
    *,
    mobile: bool = False,
    headers: dict[str, str] | None = None,
    **overrides: Any,
) -> httpx.Response:
    sent = {**(MOBILE if mobile else {}), **(headers or {})}
    return await client.post(REGISTER, json=register_body(token, **overrides), headers=sent)


def assert_envelope(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """Assert the exact error envelope and return its ``error`` object."""
    assert response.status_code == status
    assert set(response.json()) == {"error"}
    error = response.json()["error"]
    assert set(error) == ENVELOPE_KEYS
    assert error["code"] == code
    assert isinstance(error["request_id"], str)
    assert error["request_id"]
    return dict(error)


async def scalar(session: AsyncSession, sql: str, **params: Any) -> Any:
    return (await session.execute(text(sql), params)).scalar_one()


async def use_count_of(session: AsyncSession, token: str) -> int:
    return int(
        await scalar(
            session,
            "SELECT use_count FROM invites WHERE token_hash = :h",
            h=hash_invite_token(token),
        )
    )


async def count_users(session: AsyncSession) -> int:
    return int(await scalar(session, "SELECT count(*) FROM users"))


async def count_players(session: AsyncSession) -> int:
    return int(await scalar(session, "SELECT count(*) FROM players"))
