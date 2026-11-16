"""The registration workflow hashes off the event loop before it takes the invite lock (S-2.1.3)."""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from app.core.db import AppSession
from app.identity import services as identity
from app.playgroups import services as playgroups
from app.workflows import registration
from tests.register_support import PASSWORD, make_inviter, make_token_invite

pytestmark = pytest.mark.usefixtures("app_schema")


async def test_the_password_is_hashed_in_a_thread_before_the_invite_is_locked(
    app_schema: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await make_token_invite(app_schema, await make_inviter(app_schema))
    events: list[str] = []
    real_to_thread = asyncio.to_thread
    real_lock = playgroups.lock_invite
    real_hash = identity.hash_password

    async def spy_to_thread(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        events.append(f"to_thread:{getattr(func, '__name__', '?')}")
        return await real_to_thread(func, *args, **kwargs)

    async def spy_lock(session: Any, invite_token: str) -> Any:
        events.append("lock_invite")
        return await real_lock(session, invite_token)

    def spy_hash(password: str) -> str:
        events.append("hash_password")
        return real_hash(password)

    monkeypatch.setattr(asyncio, "to_thread", spy_to_thread)
    monkeypatch.setattr(playgroups, "lock_invite", spy_lock)
    monkeypatch.setattr(identity, "hash_password", spy_hash)

    body = registration.RegisterRequest(
        invite_token=token, username="ordered", password=PASSWORD, display_name="Ordered"
    )
    await registration.register(app_schema, body)

    assert "lock_invite" in events
    assert events.index("to_thread:spy_hash") < events.index("lock_invite")
    assert events.index("hash_password") < events.index("lock_invite")
    assert events.count("hash_password") == 1  # never hashed a second time inside the lock
