"""Helpers for the S-2.1.10 throttle tests: a client per IP, settings overrides, Redis peeks."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import update

from app.core.config import get_settings
from app.core.db import AppSession
from app.identity.models import User
from app.identity.services import create_user_with_player

LOGIN = "/api/v1/auth/login"
PASSWORD = "correct horse battery"


@asynccontextmanager
async def client_from(app: FastAPI, ip: str) -> AsyncIterator[httpx.AsyncClient]:
    """An httpx client whose requests arrive from ``ip`` (the ASGI ``client`` scope)."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=(ip, 4321))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http_client:
        yield http_client


def use_settings(app: FastAPI, monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """Set env vars, drop the settings cache and refresh the app's settings object."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    app.state.settings = get_settings()


@asynccontextmanager
async def peek_redis() -> AsyncIterator[Redis]:
    """A throwaway client on the test Redis for reading counters (read-only use)."""
    redis: Redis = Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=2.0)
    try:
        yield redis
    finally:
        await redis.aclose()


async def keys_under(prefix: str) -> list[str]:
    """Every key under this test's prefix."""
    async with peek_redis() as redis:
        return sorted(
            [k.decode() async for k in redis.scan_iter(match=f"{prefix}*", count=500)],
        )


async def keys_containing(prefix: str, fragment: str) -> list[str]:
    return [k for k in await keys_under(prefix) if fragment in k]


async def ttl_of(key: str) -> int:
    async with peek_redis() as redis:
        return int(await redis.ttl(key))


async def value_of(key: str) -> int:
    async with peek_redis() as redis:
        raw = await redis.get(key)
    return int(raw) if raw is not None else 0


async def make_member(session: AppSession, username: str = "ada", *, active: bool = True) -> None:
    """Create and commit a member with a known password (optionally inactive)."""
    info = await create_user_with_player(
        session, username=username, password=PASSWORD, display_name="Ada"
    )
    if not active:
        await session.execute(update(User).where(User.id == info.user_id).values(is_active=False))
    await session.commit()


async def login(
    client: httpx.AsyncClient,
    username: str = "ada",
    password: str = PASSWORD,
    *,
    mobile: bool = True,
) -> httpx.Response:
    headers = {"X-Client": "mobile"} if mobile else {}
    return await client.post(
        LOGIN, json={"username": username, "password": password}, headers=headers
    )


def spy_on_hashing(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every argon2 hash/verify call (the real call still runs)."""
    calls: list[str] = []
    real_verify = PasswordHasher.verify
    real_hash = PasswordHasher.hash

    def verify(self: PasswordHasher, *args: Any, **kwargs: Any) -> bool:
        calls.append("verify")
        return real_verify(self, *args, **kwargs)

    def hash_(self: PasswordHasher, *args: Any, **kwargs: Any) -> str:
        calls.append("hash")
        return real_hash(self, *args, **kwargs)

    monkeypatch.setattr(PasswordHasher, "verify", verify)
    monkeypatch.setattr(PasswordHasher, "hash", hash_)
    return calls
