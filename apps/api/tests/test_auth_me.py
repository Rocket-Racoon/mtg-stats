"""``GET /api/v1/auth/me`` (S-2.1.11 AC5, decision 6).

The response is read from the database on every request; the API never trusts a client-supplied
``is_site_admin``.
"""

from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, update

from app.core.db import AppSession
from app.core.ids import uuid7
from app.identity.models import Player, User
from app.identity.services import create_user_with_player
from tests.auth_support import issue

pytestmark = pytest.mark.usefixtures("app_schema")
ME = "/api/v1/auth/me"
KEYS = {"id", "username", "display_name", "is_site_admin"}


async def make_member(
    session: AppSession,
    username: str = "ada",
    display_name: str = "Ada Lovelace",
    *,
    admin: bool = False,
) -> tuple[UUID, dict[str, str]]:
    """Commit a user with a player; return ``(user_id, Authorization header)``."""
    info = await create_user_with_player(
        session,
        username=username,
        password="correct horse battery",
        display_name=display_name,
        is_site_admin=admin,
    )
    assert info.user_id is not None
    await session.commit()
    return info.user_id, {"Authorization": f"Bearer {issue(info.user_id)}"}


async def set_user(session: AppSession, user_id: UUID, **values: Any) -> None:
    await session.execute(update(User).where(User.id == user_id).values(**values))
    await session.commit()


async def test_me_returns_exactly_the_four_documented_keys(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_member(app_schema)
    response = await client.get(ME, headers=headers)
    assert response.status_code == 200
    assert set(response.json()) == KEYS


async def test_me_returns_the_user_id_username_and_the_players_display_name(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_member(app_schema, "ada", "Ada Lovelace")
    body = (await client.get(ME, headers=headers)).json()
    assert body["id"] == str(user_id)
    assert body["username"] == "ada"
    assert body["display_name"] == "Ada Lovelace"


async def test_me_reports_is_site_admin_false_for_a_member(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_member(app_schema, admin=False)
    body = (await client.get(ME, headers=headers)).json()
    assert body["is_site_admin"] is False


async def test_me_reports_is_site_admin_true_for_a_site_admin(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_member(app_schema, admin=True)
    body = (await client.get(ME, headers=headers)).json()
    assert body["is_site_admin"] is True


async def test_me_follows_a_role_change_on_the_next_request_with_the_same_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_member(app_schema)
    assert (await client.get(ME, headers=headers)).json()["is_site_admin"] is False

    await set_user(app_schema, user_id, is_site_admin=True)
    assert (await client.get(ME, headers=headers)).json()["is_site_admin"] is True

    await set_user(app_schema, user_id, is_site_admin=False)
    assert (await client.get(ME, headers=headers)).json()["is_site_admin"] is False


async def test_me_reads_the_display_name_from_the_database_on_every_request(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_member(app_schema, display_name="Before")
    assert (await client.get(ME, headers=headers)).json()["display_name"] == "Before"
    await app_schema.execute(
        update(Player).where(Player.user_id == user_id).values(display_name="After")
    )
    await app_schema.commit()
    assert (await client.get(ME, headers=headers)).json()["display_name"] == "After"


async def test_me_without_a_token_is_401_unauthenticated(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    response = await client.get(ME)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_me_with_a_garbage_token_is_401_invalid_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    response = await client.get(ME, headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_me_for_a_valid_token_of_an_unknown_user_is_401_invalid_token(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    response = await client.get(ME, headers={"Authorization": f"Bearer {issue(uuid7())}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_me_for_a_user_deleted_after_the_token_was_issued_is_401(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_member(app_schema)
    assert (await client.get(ME, headers=headers)).status_code == 200
    await app_schema.execute(delete(Player).where(Player.user_id == user_id))
    await app_schema.execute(delete(User).where(User.id == user_id))
    await app_schema.commit()
    response = await client.get(ME, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_me_for_an_inactive_user_is_401_invalid_token_and_recovers_on_reactivation(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_member(app_schema)
    await set_user(app_schema, user_id, is_active=False)
    response = await client.get(ME, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"

    await set_user(app_schema, user_id, is_active=True)
    assert (await client.get(ME, headers=headers)).status_code == 200


async def test_an_inactive_site_admin_does_not_get_a_200_from_me(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, headers = await make_member(app_schema, admin=True)
    await set_user(app_schema, user_id, is_active=False)
    assert (await client.get(ME, headers=headers)).status_code == 401


@pytest.mark.parametrize("claimed", ["true", "1", "True"])
async def test_a_client_supplied_is_site_admin_query_parameter_is_ignored(
    client: httpx.AsyncClient, app_schema: AppSession, claimed: str
) -> None:
    _, headers = await make_member(app_schema, admin=False)
    response = await client.get(ME, params={"is_site_admin": claimed}, headers=headers)
    assert response.status_code == 200
    assert response.json()["is_site_admin"] is False


@pytest.mark.parametrize("header", ["X-Is-Site-Admin", "X-Site-Admin", "is_site_admin"])
async def test_a_client_supplied_is_site_admin_header_is_ignored(
    client: httpx.AsyncClient, app_schema: AppSession, header: str
) -> None:
    _, headers = await make_member(app_schema, admin=False)
    response = await client.get(ME, headers={**headers, header: "true"})
    assert response.status_code == 200
    assert response.json()["is_site_admin"] is False


async def test_a_client_supplied_is_site_admin_body_is_ignored(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_member(app_schema, admin=False)
    response = await client.request("GET", ME, json={"is_site_admin": True}, headers=headers)
    assert response.status_code == 200
    assert response.json()["is_site_admin"] is False


async def test_a_client_supplied_false_does_not_hide_a_real_site_admin(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_member(app_schema, admin=True)
    response = await client.get(ME, params={"is_site_admin": "false"}, headers=headers)
    assert response.json()["is_site_admin"] is True


async def test_me_returns_only_the_callers_own_data(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, ada_headers = await make_member(app_schema, "ada", "Ada", admin=True)
    _, bob_headers = await make_member(app_schema, "bob", "Bob")
    assert (await client.get(ME, headers=bob_headers)).json()["username"] == "bob"
    assert (await client.get(ME, headers=ada_headers)).json()["username"] == "ada"


async def test_me_is_a_get_only_route_with_an_identity_operation_id_and_the_auth_tag(
    api_app: FastAPI,
) -> None:
    operations = api_app.openapi()["paths"][ME]
    assert set(operations) == {"get"}
    operation = operations["get"]
    assert operation["operationId"].startswith("identity_")
    assert operation["tags"] == ["auth"]
    assert operation["security"], "the route must advertise the bearer scheme"


async def test_the_me_response_schema_lists_exactly_the_four_fields(api_app: FastAPI) -> None:
    spec = api_app.openapi()
    ref = spec["paths"][ME]["get"]["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    schema = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    assert set(schema["properties"]) == KEYS
    assert schema["properties"]["is_site_admin"]["type"] == "boolean"
    assert set(schema["required"]) == KEYS
