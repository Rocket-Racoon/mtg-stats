"""A role change takes effect on the next request with the same token (S-2.1.11 AC4).

``require_site_admin`` reads ``is_site_admin`` from the database on every request, so a revoke
does not wait for the caller's access token to expire.
"""

import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.cli import run
from app.core.auth import SiteAdminDep, register_site_admin_checker
from app.core.db import AppSession
from app.identity.models import User
from app.identity.services import is_site_admin, set_site_admin
from tests.invite_support import make_account

pytestmark = pytest.mark.usefixtures("app_schema")
PROBE = "/_probe/site-admin"


@pytest.fixture
async def probed(api_app: FastAPI) -> AsyncIterator[FastAPI]:
    """``api_app`` plus a route guarded by ``SiteAdminDep``; the real checker is restored after."""

    @api_app.get(PROBE)
    async def probe(admin: SiteAdminDep) -> dict[str, str]:
        return {"id": str(admin.id)}

    yield api_app
    register_site_admin_checker(is_site_admin)


async def _username(session: AppSession, user_id: UUID) -> str:
    return (await session.execute(select(User.username).where(User.id == user_id))).scalar_one()


def _assert_forbidden(response: httpx.Response) -> None:
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_a_site_admins_token_passes_the_guarded_route(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    user_id, headers = await make_account(app_schema, admin=True)
    response = await client.get(PROBE, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"id": str(user_id)}


async def test_a_non_admin_gets_403_forbidden_from_the_guarded_route(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    _, headers = await make_account(app_schema, admin=False)
    _assert_forbidden(await client.get(PROBE, headers=headers))


async def test_a_revoke_through_the_service_makes_the_same_token_get_403_on_its_next_request(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    await make_account(app_schema, admin=True)  # a second admin, so the revoke is allowed
    user_id, headers = await make_account(app_schema, admin=True)
    assert (await client.get(PROBE, headers=headers)).status_code == 200

    username = await _username(app_schema, user_id)
    await set_site_admin(app_schema, username=username, value=False)
    await app_schema.commit()

    _assert_forbidden(await client.get(PROBE, headers=headers))


async def test_a_re_grant_makes_the_same_token_pass_again(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    await make_account(app_schema, admin=True)
    user_id, headers = await make_account(app_schema, admin=True)

    username = await _username(app_schema, user_id)
    await set_site_admin(app_schema, username=username, value=False)
    await app_schema.commit()
    _assert_forbidden(await client.get(PROBE, headers=headers))

    await set_site_admin(app_schema, username=username, value=True)
    await app_schema.commit()
    assert (await client.get(PROBE, headers=headers)).status_code == 200


async def test_a_grant_makes_a_non_admins_token_pass_on_its_next_request(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    user_id, headers = await make_account(app_schema, admin=False)
    _assert_forbidden(await client.get(PROBE, headers=headers))
    username = await _username(app_schema, user_id)

    await set_site_admin(app_schema, username=username, value=True)
    await app_schema.commit()

    assert (await client.get(PROBE, headers=headers)).status_code == 200


async def test_a_revoke_through_the_cli_makes_the_same_token_get_403_on_its_next_request(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    await make_account(app_schema, admin=True)
    user_id, headers = await make_account(app_schema, admin=True)
    username = await _username(app_schema, user_id)
    assert (await client.get(PROBE, headers=headers)).status_code == 200

    @asynccontextmanager
    async def factory() -> AsyncIterator[AppSession]:
        yield app_schema

    code = await run(
        ["revoke-site-admin", "--username", username],
        session_factory=factory,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code == 0

    _assert_forbidden(await client.get(PROBE, headers=headers))
