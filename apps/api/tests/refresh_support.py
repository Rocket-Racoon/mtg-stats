"""Helpers for the S-2.1.5 refresh-token tests: requests in both client modes, rows, cookies."""

import secrets
from collections.abc import Awaitable
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from typing import Any, Literal
from uuid import UUID

import factory
import httpx
import jwt
from sqlalchemy import Row, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.identity.models import RefreshToken, User
from app.identity.services import create_user_with_player, hash_refresh_token
from tests.auth_support import secret
from tests.factories import BaseFactory

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
LOGOUT_ALL = "/api/v1/auth/logout-all"
PASSWORD = "correct horse battery"
COOKIE_NAME = "mtgstats_refresh"
COOKIE_PATH = "/api/v1/auth"
MOBILE = {"X-Client": "mobile"}
Mode = Literal["web", "mobile"]


class RefreshTokenFactory(BaseFactory):
    """A live refresh-token row; ``user_id`` is required, the hash is random."""

    class Meta:
        model = RefreshToken

    id = factory.LazyFunction(uuid7)
    family_id = factory.LazyFunction(uuid7)
    token_hash = factory.LazyFunction(lambda: secrets.token_hex(32))
    expires_at = factory.LazyFunction(lambda: datetime.now(UTC))
    revoked_at = None
    replaced_by = None
    created_at = factory.LazyFunction(lambda: datetime.now(UTC))


async def make_user(session: AsyncSession, username: str = "ada") -> User:
    """Create a signed-up user, commit (a later 401 rolls the session back) and detach it."""
    info = await create_user_with_player(
        session, username=username, password=PASSWORD, display_name=username.title()
    )
    user = (await session.execute(select(User).where(User.id == info.user_id))).scalar_one()
    await session.commit()
    session.expunge(user)
    return user


async def deactivate(session: AsyncSession, user: User) -> None:
    await session.execute(update(User).where(User.id == user.id).values(is_active=False))
    await session.commit()


def decode(token: str) -> dict[str, Any]:
    return dict(jwt.decode(token, secret(), algorithms=["HS256"]))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE_NAME}={token}"}


def set_cookies(response: httpx.Response) -> list[str]:
    """Every raw ``Set-Cookie`` header of the response for the refresh cookie."""
    return [
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(f"{COOKIE_NAME}=")
    ]


def cookie_attributes(raw: str) -> dict[str, str]:
    """Lower-cased attribute names of a ``Set-Cookie`` value; flags map to ``""``."""
    parts = [p.strip() for p in raw.split(";")[1:]]
    return {
        p.split("=", 1)[0].lower(): (p.split("=", 1)[1] if "=" in p else "") for p in parts if p
    }


def cookie_value(response: httpx.Response) -> str:
    (raw,) = set_cookies(response)
    morsel = SimpleCookie(raw)[COOKIE_NAME]
    return morsel.value


def assert_cookie_cleared(response: httpx.Response) -> None:
    (raw,) = set_cookies(response)
    attributes = cookie_attributes(raw)
    assert raw.startswith((f'{COOKIE_NAME}=""', f"{COOKIE_NAME}=;"))
    assert attributes["max-age"] == "0"
    assert attributes["path"] == COOKIE_PATH


def login_request(
    client: httpx.AsyncClient, mode: Mode, username: str = "ada"
) -> Awaitable[httpx.Response]:
    headers = MOBILE if mode == "mobile" else {}
    return client.post(LOGIN, json={"username": username, "password": PASSWORD}, headers=headers)


def refresh_token_of(response: httpx.Response, mode: Mode) -> str:
    """The refresh token a login or refresh response carries for ``mode``."""
    if mode == "mobile":
        token = response.json()["refresh_token"]
        assert isinstance(token, str)
        return token
    return cookie_value(response)


async def sign_in(client: httpx.AsyncClient, mode: Mode, username: str = "ada") -> tuple[str, str]:
    """Log in; return ``(access_token, refresh_token)``."""
    response = await login_request(client, mode, username)
    assert response.status_code == 200
    return response.json()["access_token"], refresh_token_of(response, mode)


def post_refresh(client: httpx.AsyncClient, mode: Mode, token: str) -> Awaitable[httpx.Response]:
    """Present ``token`` the way ``mode`` does: the cookie for web, the body for mobile."""
    if mode == "mobile":
        return client.post(REFRESH, json={"refresh_token": token}, headers=MOBILE)
    return client.post(REFRESH, headers=cookie_header(token))


def post_logout(client: httpx.AsyncClient, mode: Mode, token: str) -> Awaitable[httpx.Response]:
    if mode == "mobile":
        return client.post(LOGOUT, json={"refresh_token": token}, headers=MOBILE)
    return client.post(LOGOUT, headers=cookie_header(token))


async def rows(session: AsyncSession, **where: UUID) -> list[Row[Any]]:
    """Fresh column values of the refresh-token rows (never stale ORM instances)."""
    stmt = select(
        RefreshToken.id,
        RefreshToken.user_id,
        RefreshToken.family_id,
        RefreshToken.token_hash,
        RefreshToken.expires_at,
        RefreshToken.revoked_at,
        RefreshToken.replaced_by,
        RefreshToken.created_at,
    ).order_by(RefreshToken.created_at, RefreshToken.id)
    for column, value in where.items():
        stmt = stmt.where(getattr(RefreshToken, column) == value)
    return list((await session.execute(stmt)).all())


async def row_for(session: AsyncSession, token: str) -> Row[Any]:
    """The one row stored for ``token`` (looked up by its SHA-256)."""
    found = [r for r in await rows(session) if r.token_hash == hash_refresh_token(token)]
    assert len(found) == 1
    return found[0]
