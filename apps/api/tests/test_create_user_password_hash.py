"""``create_user_with_player(password_hash=...)`` (S-2.1.3, decision 3: hash before the lock)."""

import pytest
from sqlalchemy import text

from app.core.db import AppSession
from app.identity.services import (
    create_user_with_player,
    hash_password,
    verify_password,
)
from app.identity.services import players as players_module

PASSWORD = "correct horse battery"

pytestmark = pytest.mark.usefixtures("app_schema")


async def _stored_hash(session: AppSession, username: str) -> str:
    result = await session.execute(
        text("SELECT password_hash FROM users WHERE username = :u"), {"u": username}
    )
    return str(result.scalar_one())


async def test_a_given_hash_is_stored_as_is_and_verifies_with_the_original_password(
    app_schema: AppSession,
) -> None:
    given = hash_password(PASSWORD)

    await create_user_with_player(
        app_schema, username="ada", password=PASSWORD, display_name="Ada", password_hash=given
    )

    stored = await _stored_hash(app_schema, "ada")
    assert stored == given
    assert verify_password(stored, PASSWORD)


async def test_a_given_hash_is_not_hashed_again(
    app_schema: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    given = hash_password(PASSWORD)

    def forbidden(password: str) -> str:
        raise AssertionError("hash_password must not run when password_hash is given")

    monkeypatch.setattr(players_module, "hash_password", forbidden)

    await create_user_with_player(
        app_schema, username="ada", password=PASSWORD, display_name="Ada", password_hash=given
    )

    assert await _stored_hash(app_schema, "ada") == given


async def test_without_a_hash_the_password_is_hashed_exactly_once_as_before(
    app_schema: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def spy(password: str) -> str:
        calls.append(password)
        return hash_password(password)

    monkeypatch.setattr(players_module, "hash_password", spy)

    await create_user_with_player(app_schema, username="ada", password=PASSWORD, display_name="Ada")

    assert calls == [PASSWORD]
    assert verify_password(await _stored_hash(app_schema, "ada"), PASSWORD)


async def test_a_none_hash_behaves_like_no_hash(app_schema: AppSession) -> None:
    await create_user_with_player(
        app_schema, username="ada", password=PASSWORD, display_name="Ada", password_hash=None
    )
    assert verify_password(await _stored_hash(app_schema, "ada"), PASSWORD)
