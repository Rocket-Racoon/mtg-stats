"""``require_site_admin`` (S-2.1.2 decision 1): a per-request database check, failing closed."""

from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SiteAdminDep, register_site_admin_checker
from app.core.db import AppSession
from app.core.ids import uuid7
from app.identity.models import User
from app.identity.services import is_site_admin
from tests.auth_support import issue
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


async def _set(session: AppSession, user_id: UUID, **values: bool) -> None:
    await session.execute(update(User).where(User.id == user_id).values(**values))
    await session.commit()


async def test_a_site_admin_passes_and_the_dependency_returns_the_current_user(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    user_id, headers = await make_account(app_schema, admin=True)
    response = await client.get(PROBE, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"id": str(user_id)}


async def test_a_signed_in_user_who_is_not_a_site_admin_gets_403_forbidden(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    _, headers = await make_account(app_schema, admin=False)
    response = await client.get(PROBE, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_an_inactive_site_admin_gets_403_forbidden(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    _, headers = await make_account(app_schema, admin=True, active=False)
    response = await client.get(PROBE, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_a_valid_token_for_an_unknown_user_gets_403_forbidden(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    response = await client.get(PROBE, headers={"Authorization": f"Bearer {issue(uuid7())}"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_no_token_is_401_before_any_admin_check(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    response = await client.get(PROBE)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_the_answer_follows_the_database_between_requests_with_the_same_token(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    user_id, headers = await make_account(app_schema, admin=False)
    assert (await client.get(PROBE, headers=headers)).status_code == 403

    await _set(app_schema, user_id, is_site_admin=True)
    assert (await client.get(PROBE, headers=headers)).status_code == 200

    await _set(app_schema, user_id, is_active=False)
    assert (await client.get(PROBE, headers=headers)).status_code == 403

    await _set(app_schema, user_id, is_active=True)
    assert (await client.get(PROBE, headers=headers)).status_code == 200

    await _set(app_schema, user_id, is_site_admin=False)
    assert (await client.get(PROBE, headers=headers)).status_code == 403


async def test_the_registered_checker_is_called_on_every_request_with_session_and_user_id(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    user_id, headers = await make_account(app_schema, admin=False)
    calls: list[tuple[object, UUID]] = []

    async def checker(session: AsyncSession, uid: UUID) -> bool:
        calls.append((session, uid))
        return len(calls) % 2 == 1  # allow the 1st, deny the 2nd, allow the 3rd

    register_site_admin_checker(checker)

    statuses = [(await client.get(PROBE, headers=headers)).status_code for _ in range(3)]

    assert statuses == [200, 403, 200]
    assert [uid for _, uid in calls] == [user_id] * 3
    assert all(isinstance(s, AsyncSession) for s, _ in calls)


async def test_a_failing_checker_does_not_grant_access(
    client: httpx.AsyncClient, app_schema: AppSession, probed: FastAPI
) -> None:
    _, headers = await make_account(app_schema, admin=True)

    async def broken(session: AsyncSession, uid: UUID) -> bool:
        raise ConnectionError("boom")

    register_site_admin_checker(broken)
    response = await client.get(PROBE, headers=headers)
    assert response.status_code == 500


async def test_with_no_checker_registered_it_fails_closed_with_a_500(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    probed: FastAPI,
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    register_site_admin_checker(None)

    response = await client.get(PROBE, headers=headers)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
