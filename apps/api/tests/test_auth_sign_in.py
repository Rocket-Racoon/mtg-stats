"""Sign in with access and refresh tokens (S-2.1.4 AC1, AC2, AC3, AC5 sign-in part)."""

import inspect
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

import httpx
import jwt
import pytest
from argon2 import PasswordHasher, Type
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import AppSession, get_session
from app.identity import services
from app.identity.models import User
from app.identity.services import auth as auth_module
from app.identity.services import create_user_with_player, hash_password, password_needs_rehash
from app.main import create_app
from tests.auth_support import secret
from tests.statement_counter import StatementCounter

LOGIN = "/api/v1/auth/login"
PASSWORD = "correct horse battery"

pytestmark = pytest.mark.usefixtures("app_schema")


async def _member(session: AppSession, username: str = "ada") -> User:
    info = await create_user_with_player(
        session, username=username, password=PASSWORD, display_name="Ada"
    )
    user = (await session.execute(select(User).where(User.id == info.user_id))).scalar_one()
    # Release the savepoint: the app's error path rolls the session back, which would otherwise
    # undo this setup after the first 401.
    await session.commit()
    # Detach it: after that rollback an attached instance is expired and cannot lazy-load.
    session.expunge(user)
    return user


async def _set(session: AppSession, user: User, **values: Any) -> None:
    """Update the user's columns and commit, so a later error-path rollback keeps the change."""
    await session.execute(update(User).where(User.id == user.id).values(**values))
    await session.commit()


async def _stored_hash(session: AppSession, user: User) -> str:
    row = await session.execute(
        select(User.password_hash)
        .where(User.id == user.id)
        .execution_options(populate_existing=True)
    )
    return row.scalar_one()


async def _login(
    client: httpx.AsyncClient, username: str = "ada", password: str = PASSWORD
) -> httpx.Response:
    return await client.post(LOGIN, json={"username": username, "password": password})


def _decode(token: str) -> dict[str, Any]:
    return dict(jwt.decode(token, secret(), algorithms=["HS256"]))


@pytest.fixture
def verify_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the hash given to every ``PasswordHasher.verify`` call (the real call still runs)."""
    seen: list[str] = []
    original = PasswordHasher.verify

    def spy(self: PasswordHasher, hash: str | bytes, password: str | bytes) -> bool:
        seen.append(hash if isinstance(hash, str) else hash.decode())
        return original(self, hash, password)

    monkeypatch.setattr(PasswordHasher, "verify", spy)
    return seen


@pytest.fixture
def strong_argon2(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Raise the current argon2 time cost after a hash was made, so stored hashes are 'older'."""
    monkeypatch.setenv("ARGON2_TIME_COST", "2")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ------------------------------------------------------------------------------ AC1


async def test_login_with_correct_credentials_returns_200_and_both_tokens(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await _member(app_schema)
    response = await _login(client)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "refresh_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert isinstance(body["access_token"], str)
    assert isinstance(body["refresh_token"], str)


async def test_access_token_is_hs256_with_the_required_claims_and_15_minute_life(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user = await _member(app_schema)
    token = (await _login(client)).json()["access_token"]
    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    claims = _decode(token)
    assert {"sub", "exp", "iat", "jti", "type"} <= set(claims)
    assert claims["sub"] == str(user.id)
    assert claims["type"] == "access"
    assert claims["exp"] - claims["iat"] == 15 * 60


async def test_refresh_token_has_type_refresh_and_lives_30_days(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user = await _member(app_schema)
    claims = _decode((await _login(client)).json()["refresh_token"])
    assert claims["type"] == "refresh"
    assert claims["sub"] == str(user.id)
    assert claims["exp"] - claims["iat"] == 30 * 24 * 3600


async def test_token_lifetimes_follow_the_settings(
    client: httpx.AsyncClient, app_schema: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _member(app_schema)
    monkeypatch.setenv("ACCESS_TOKEN_TTL_MINUTES", "5")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_DAYS", "2")
    get_settings.cache_clear()
    try:
        body = (await _login(client)).json()
    finally:
        get_settings.cache_clear()
    access, refresh = _decode(body["access_token"]), _decode(body["refresh_token"])
    assert body["expires_in"] == 300
    assert access["exp"] - access["iat"] == 5 * 60
    assert refresh["exp"] - refresh["iat"] == 2 * 24 * 3600


async def test_login_expires_in_matches_the_real_access_token_with_explicit_app_settings(
    app_schema: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An app built with settings that differ from the environment: the body must report the
    # lifetime the token really has, not a value read from app.state.settings.
    await _member(app_schema)
    monkeypatch.setenv("ACCESS_TOKEN_TTL_MINUTES", "7")
    get_settings.cache_clear()
    try:
        explicit = get_settings().model_copy(update={"access_token_ttl_minutes": 3})
        app = create_app(explicit)

        async def _session() -> AsyncIterator[AppSession]:
            yield app_schema

        app.dependency_overrides[get_session] = _session
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http_client:
            body = (await _login(http_client)).json()
    finally:
        get_settings.cache_clear()
    claims = _decode(body["access_token"])
    assert body["expires_in"] == claims["exp"] - claims["iat"]
    assert body["expires_in"] == 7 * 60


async def test_each_login_issues_new_unique_jtis(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await _member(app_schema)
    first, second = (await _login(client)).json(), (await _login(client)).json()
    jtis = {
        _decode(first["access_token"])["jti"],
        _decode(first["refresh_token"])["jti"],
        _decode(second["access_token"])["jti"],
        _decode(second["refresh_token"])["jti"],
    }
    assert len(jtis) == 4


@pytest.mark.parametrize("typed", ["ADA", "Ada", "aDa"])
async def test_username_is_case_insensitive(
    client: httpx.AsyncClient, app_schema: AppSession, typed: str
) -> None:
    user = await _member(app_schema, "ada")
    response = await _login(client, typed)
    assert response.status_code == 200
    assert _decode(response.json()["access_token"])["sub"] == str(user.id)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"username": "ada"},
        {"password": PASSWORD},
        {"username": "", "password": PASSWORD},
        {"username": "ada", "password": ""},
        {"username": "a" * 257, "password": PASSWORD},
        {"username": "ada", "password": "p" * 1025},
        {"username": 5, "password": PASSWORD},
    ],
)
async def test_bad_login_bodies_are_422_validation_errors(
    client: httpx.AsyncClient, body: dict[str, Any]
) -> None:
    response = await client.post(LOGIN, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_login_accepts_the_maximum_field_lengths(client: httpx.AsyncClient) -> None:
    response = await client.post(LOGIN, json={"username": "a" * 256, "password": "p" * 1024})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


# ------------------------------------------------------------------------------ AC2


async def test_unknown_username_and_wrong_password_return_identical_401s(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    await _member(app_schema)
    unknown = await _login(client, "nobody", PASSWORD)
    wrong = await _login(client, "ada", "not the password at all")
    assert unknown.status_code == wrong.status_code == 401
    unknown_error, wrong_error = unknown.json()["error"], wrong.json()["error"]
    assert unknown_error["code"] == wrong_error["code"] == "invalid_credentials"
    assert unknown_error["message"] == wrong_error["message"] == "Incorrect username or password."
    assert unknown_error["details"] == wrong_error["details"]
    unknown_error.pop("request_id")
    wrong_error.pop("request_id")
    assert unknown_error == wrong_error


async def test_unknown_username_still_runs_one_argon2_verification_with_current_parameters(
    client: httpx.AsyncClient, app_schema: AppSession, verify_calls: list[str]
) -> None:
    await _member(app_schema)
    response = await _login(client, "nobody", PASSWORD)
    assert response.status_code == 401
    assert len(verify_calls) == 1
    assert verify_calls[0].startswith("$argon2id$")
    assert password_needs_rehash(verify_calls[0]) is False


async def test_wrong_password_runs_one_argon2_verification(
    client: httpx.AsyncClient, app_schema: AppSession, verify_calls: list[str]
) -> None:
    await _member(app_schema)
    await _login(client, "ada", "not the password at all")
    assert len(verify_calls) == 1


async def test_failed_sign_ins_write_nothing(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    user = await _member(app_schema)
    before = await _stored_hash(app_schema, user)
    with count_statements() as counter:
        await _login(client, "nobody", PASSWORD)
        await _login(client, "ada", "not the password at all")
    writes = [
        s
        for s in counter.statements
        if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert writes == []
    assert await _stored_hash(app_schema, user) == before


async def test_sign_in_service_raises_invalid_credentials_for_both_cases(
    app_schema: AppSession,
) -> None:
    await _member(app_schema)
    for username, password in (("nobody", PASSWORD), ("ada", "wrong password here")):
        with pytest.raises(services.InvalidCredentialsError) as caught:
            await services.sign_in(app_schema, username=username, password=password)
        assert caught.value.code == "invalid_credentials"
        assert caught.value.status_code == 401
        assert caught.value.message == "Incorrect username or password."


async def test_sign_in_service_returns_ids_and_tokens(app_schema: AppSession) -> None:
    user = await _member(app_schema)
    result = await services.sign_in(app_schema, username="ada", password=PASSWORD)
    assert result.user_id == user.id
    assert _decode(result.access_token.token)["jti"] == result.access_token.jti
    assert _decode(result.refresh_token.token)["jti"] == result.refresh_token.jti


# ------------------------------------------------------------------------------ AC3


async def test_outdated_hash_is_replaced_with_current_parameters_on_sign_in(
    client: httpx.AsyncClient, app_schema: AppSession, strong_argon2: None
) -> None:
    del strong_argon2
    user = await _member(app_schema)
    old = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, type=Type.ID).hash(PASSWORD)
    await _set(app_schema, user, password_hash=old)
    assert password_needs_rehash(old) is True

    response = await _login(client)

    assert response.status_code == 200
    new = await _stored_hash(app_schema, user)
    assert new != old
    assert password_needs_rehash(new) is False
    assert "t=2" in new
    assert services.verify_password(new, PASSWORD) is True


async def test_hash_with_current_parameters_is_left_unchanged(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user = await _member(app_schema)
    before = await _stored_hash(app_schema, user)
    assert password_needs_rehash(before) is False
    assert (await _login(client)).status_code == 200
    assert await _stored_hash(app_schema, user) == before


async def test_failed_sign_in_never_rehashes_an_outdated_hash(
    client: httpx.AsyncClient, app_schema: AppSession, strong_argon2: None
) -> None:
    del strong_argon2
    user = await _member(app_schema)
    old = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, type=Type.ID).hash(PASSWORD)
    await _set(app_schema, user, password_hash=old)

    response = await _login(client, "ada", "not the password at all")

    assert response.status_code == 401
    assert await _stored_hash(app_schema, user) == old


async def test_rehash_does_not_change_other_user_columns(
    app_schema: AppSession, strong_argon2: None
) -> None:
    del strong_argon2
    user = await _member(app_schema)
    old = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, type=Type.ID).hash(PASSWORD)
    await _set(app_schema, user, password_hash=old)
    columns = select(User.username, User.email, User.is_active, User.is_site_admin).where(
        User.id == user.id
    )
    before = (await app_schema.execute(columns)).one()
    await services.sign_in(app_schema, username="ada", password=PASSWORD)
    assert (await app_schema.execute(columns)).one() == before
    assert await _stored_hash(app_schema, user) != old


# ------------------------------------------------------------------------------ AC5


async def test_inactive_user_with_the_correct_password_gets_the_generic_401(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user = await _member(app_schema)
    await _set(app_schema, user, is_active=False)

    inactive = await _login(client)
    wrong = await _login(client, "ada", "not the password at all")

    assert inactive.status_code == 401
    inactive_error, wrong_error = inactive.json()["error"], wrong.json()["error"]
    assert inactive_error["code"] == "invalid_credentials"
    inactive_error.pop("request_id")
    wrong_error.pop("request_id")
    assert inactive_error == wrong_error


async def test_inactive_user_gets_no_tokens_and_no_rehash(
    app_schema: AppSession, strong_argon2: None
) -> None:
    del strong_argon2
    user = await _member(app_schema)
    old = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, type=Type.ID).hash(PASSWORD)
    await _set(app_schema, user, is_active=False, password_hash=old)
    with pytest.raises(services.InvalidCredentialsError):
        await services.sign_in(app_schema, username="ada", password=PASSWORD)
    assert await _stored_hash(app_schema, user) == old


async def test_inactive_user_path_still_runs_an_argon2_verification(
    client: httpx.AsyncClient, app_schema: AppSession, verify_calls: list[str]
) -> None:
    user = await _member(app_schema)
    await _set(app_schema, user, is_active=False)
    await _login(client)
    assert len(verify_calls) == 1


async def test_reactivated_user_can_sign_in_again(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user = await _member(app_schema)
    await _set(app_schema, user, is_active=False)
    assert (await _login(client)).status_code == 401
    await _set(app_schema, user, is_active=True)
    assert (await _login(client)).status_code == 200


# ------------------------------------------------------------------------------ warm-up


@pytest.fixture
def hash_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every ``PasswordHasher.hash`` call (the real call still runs)."""
    seen: list[str] = []
    original = PasswordHasher.hash

    def spy(self: PasswordHasher, password: str | bytes, *, salt: bytes | None = None) -> str:
        seen.append("hash")
        return original(self, password, salt=salt)

    monkeypatch.setattr(PasswordHasher, "hash", spy)
    return seen


async def _warm_up() -> None:
    result: Any = services.warm_up_sign_in()
    if inspect.isawaitable(result):
        await result


async def test_first_unknown_username_sign_in_after_warm_up_hashes_nothing(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    verify_calls: list[str],
    hash_calls: list[str],
) -> None:
    await _member(app_schema)
    auth_module._dummy_hash_for.cache_clear()
    await _warm_up()
    hash_calls.clear()
    verify_calls.clear()

    response = await _login(client, "nobody", PASSWORD)

    assert response.status_code == 401
    assert len(verify_calls) == 1
    assert hash_calls == []


async def test_app_lifespan_calls_the_sign_in_warm_up(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    original = services.warm_up_sign_in

    def spy() -> Any:
        called.append("warm")
        return original()

    monkeypatch.setattr(services, "warm_up_sign_in", spy)
    monkeypatch.setattr("app.main.warm_up_sign_in", spy, raising=False)
    app = create_app()
    async with app.router.lifespan_context(app):
        assert called == ["warm"]


def test_hash_password_uses_current_parameters() -> None:
    assert password_needs_rehash(hash_password(PASSWORD)) is False
