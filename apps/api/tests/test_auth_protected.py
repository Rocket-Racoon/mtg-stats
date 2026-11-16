"""Bearer authentication on a protected route (S-2.1.4 AC4, AC5 token part, OpenAPI)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from app.core.auth import CurrentUserDep, decode_token
from app.core.db import AppSession, get_session
from app.core.ids import uuid7
from app.identity.models import User
from app.identity.services import create_user_with_player
from app.main import create_app
from tests.auth_support import (
    ACCESS,
    OTHER_SECRET,
    REFRESH,
    claims_for,
    encode,
    issue,
    tamper_payload,
    tamper_signature,
    unsigned_token,
)

PROTECTED = "/_test/protected"
USER = uuid7()


async def _protected(user: CurrentUserDep) -> dict[str, str]:
    return {"id": str(user.id), "token_id": user.token_id}


def _with_route(app: FastAPI) -> FastAPI:
    app.add_api_route(PROTECTED, _protected, methods=["GET"])
    return app


async def _no_database() -> AsyncIterator[AppSession]:
    raise AssertionError("current_user must not open a database session")
    yield  # pragma: no cover


@pytest.fixture
async def anon_client() -> AsyncIterator[httpx.AsyncClient]:
    """App without a database: any session request fails the test (current_user is token-only)."""
    app = _with_route(create_app())
    app.dependency_overrides[get_session] = _no_database
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http_client:
        yield http_client


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _expired_claims() -> dict[str, Any]:
    claims = claims_for(USER)
    claims["iat"] -= 2000
    claims["exp"] = int(datetime.now(UTC).timestamp()) - 5
    return claims


async def test_valid_access_token_returns_200_and_the_route_sees_the_user(
    anon_client: httpx.AsyncClient,
) -> None:
    token = issue(USER)
    response = await anon_client.get(PROTECTED, headers=_bearer(token))
    assert response.status_code == 200
    body = response.json()
    assert UUID(body["id"]) == USER
    assert body["token_id"] == decode_token(token, expected_type=ACCESS).jti


async def test_bearer_scheme_is_case_insensitive(anon_client: httpx.AsyncClient) -> None:
    response = await anon_client.get(PROTECTED, headers={"Authorization": f"bearer {issue(USER)}"})
    assert response.status_code == 200


def _bad_tokens() -> dict[str, str]:
    good = issue(USER)
    no_sub = claims_for(USER)
    del no_sub["sub"]
    no_jti = claims_for(USER)
    del no_jti["jti"]
    no_type = claims_for(USER)
    del no_type["type"]
    return {
        "expired": issue(USER, now=datetime.now(UTC) - timedelta(minutes=16)),
        "expired_by_claims": encode(_expired_claims()),
        "tampered_signature": tamper_signature(good),
        "tampered_payload": tamper_payload(good, sub=str(uuid7())),
        "other_secret": encode(claims_for(USER), key=OTHER_SECRET),
        "alg_none": unsigned_token(claims_for(USER)),
        "alg_none_uppercase": unsigned_token(claims_for(USER), alg="NONE"),
        "hs512": encode(claims_for(USER), algorithm="HS512"),
        "refresh_type": issue(USER, REFRESH),
        "missing_sub": encode(no_sub),
        "missing_jti": encode(no_jti),
        "missing_type": encode(no_type),
        "non_uuid_sub": encode({**claims_for(USER), "sub": "not-a-uuid"}),
        "garbage": "this-is-not-a-jwt",
        "empty_parts": "..",
    }


@pytest.mark.parametrize("name", list(_bad_tokens()))
async def test_bad_token_is_rejected_with_401_invalid_token(
    anon_client: httpx.AsyncClient, name: str
) -> None:
    response = await anon_client.get(PROTECTED, headers=_bearer(_bad_tokens()[name]))
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "invalid_token"
    assert error["message"] == "Invalid or expired token."
    assert response.headers["www-authenticate"] == "Bearer"


async def test_missing_authorization_header_is_401_unauthenticated(
    anon_client: httpx.AsyncClient,
) -> None:
    response = await anon_client.get(PROTECTED)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Token abc", "Bearer", "Bearer "])
async def test_non_bearer_or_empty_authorization_is_401(
    anon_client: httpx.AsyncClient, header: str
) -> None:
    response = await anon_client.get(PROTECTED, headers={"Authorization": header})
    assert response.status_code == 401
    assert response.json()["error"]["code"] in {"unauthenticated", "invalid_token"}
    assert response.headers["www-authenticate"] == "Bearer"


async def test_basic_scheme_with_a_valid_token_is_unauthenticated(
    anon_client: httpx.AsyncClient,
) -> None:
    response = await anon_client.get(PROTECTED, headers={"Authorization": f"Basic {issue(USER)}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_error_envelope_carries_a_request_id(anon_client: httpx.AsyncClient) -> None:
    response = await anon_client.get(PROTECTED, headers=_bearer("garbage"))
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


# ------------------------------------------------------------------------------ AC5


@pytest.mark.usefixtures("app_schema")
async def test_access_token_keeps_working_after_deactivation_until_it_expires(
    api_app: FastAPI, client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _with_route(api_app)
    info = await create_user_with_player(
        app_schema, username="ada", password="correct horse battery", display_name="Ada"
    )
    user = (await app_schema.execute(select(User).where(User.id == info.user_id))).scalar_one()
    token = issue(user.id)
    await app_schema.execute(update(User).where(User.id == user.id).values(is_active=False))

    still_valid = await client.get(PROTECTED, headers=_bearer(token))
    assert still_valid.status_code == 200
    assert UUID(still_valid.json()["id"]) == user.id

    expired = issue(user.id, now=datetime.now(UTC) - timedelta(minutes=15, seconds=1))
    response = await client.get(PROTECTED, headers=_bearer(expired))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_token_for_a_user_that_does_not_exist_is_accepted_without_a_database_lookup(
    anon_client: httpx.AsyncClient,
) -> None:
    response = await anon_client.get(PROTECTED, headers=_bearer(issue(uuid7())))
    assert response.status_code == 200


# ------------------------------------------------------------------------------ OpenAPI


def _schema() -> dict[str, Any]:
    return create_app().openapi()


def _resolve(spec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    ref = node.get("$ref")
    if ref is None:
        return node
    target: Any = spec
    for part in ref.removeprefix("#/").split("/"):
        target = target[part]
    return dict(target)


def test_openapi_documents_the_login_request_and_response() -> None:
    spec = _schema()
    operation = spec["paths"]["/api/v1/auth/login"]["post"]
    request = _resolve(spec, operation["requestBody"]["content"]["application/json"]["schema"])
    assert {"username", "password"} <= set(request["properties"])
    assert set(request["required"]) == {"username", "password"}
    ok = _resolve(spec, operation["responses"]["200"]["content"]["application/json"]["schema"])
    assert {"access_token", "refresh_token", "token_type", "expires_in"} <= set(ok["properties"])
    assert "401" in operation["responses"]


def test_openapi_declares_a_bearer_security_scheme() -> None:
    # No real route is protected yet, so the scheme appears once a route uses CurrentUserDep.
    schemes = _with_route(create_app()).openapi()["components"]["securitySchemes"]
    assert any(
        s.get("type") == "http" and str(s.get("scheme")).lower() == "bearer"
        for s in schemes.values()
    )
