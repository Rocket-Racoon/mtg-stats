"""Unit tests beside the invite service: the happy path through the routes and service."""

import hashlib

import httpx
import pytest
from sqlalchemy import select, update

from app.core.db import AppSession
from app.identity.models import User
from app.playgroups import services
from app.playgroups.models import Invite
from tests.auth_support import issue
from tests.refresh_support import bearer, make_user

pytestmark = pytest.mark.usefixtures("app_schema")


async def _admin(session: AppSession) -> User:
    user = await make_user(session, "root")
    await session.execute(update(User).where(User.id == user.id).values(is_site_admin=True))
    await session.commit()
    return user


async def test_site_admin_creates_lists_and_revokes_an_invite(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    admin = await _admin(app_schema)
    headers = bearer(issue(admin.id))

    created = await client.post("/api/v1/invites", headers=headers)

    assert created.status_code == 201
    body = created.json()
    token = body["url"].rsplit("/invite/", 1)[1]
    assert body["max_uses"] == 1
    row = (await app_schema.execute(select(Invite))).scalar_one()
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert (await services.validate_invite(app_schema, token)).id == row.id

    listed = await client.get("/api/v1/invites", headers=headers)
    assert [i["status"] for i in listed.json()["items"]] == ["active"]
    assert token not in listed.text

    revoked = await client.post(f"/api/v1/invites/{body['id']}/revoke", headers=headers)
    assert revoked.json()["status"] == "revoked"
    with pytest.raises(services.InviteNoLongerValidError):
        await services.validate_invite(app_schema, token)


async def test_non_admin_cannot_create_an_invite(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user = await make_user(app_schema, "plain")
    response = await client.post("/api/v1/invites", headers=bearer(issue(user.id)))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_out_of_range_values_are_422(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    admin = await _admin(app_schema)
    response = await client.post(
        "/api/v1/invites", json={"max_uses": 21}, headers=bearer(issue(admin.id))
    )
    assert response.status_code == 422
