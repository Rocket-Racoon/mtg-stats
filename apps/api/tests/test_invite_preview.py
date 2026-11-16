"""``POST /invites/preview`` (S-2.1.3 AC1, with the product owner's route change)."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.db import AppSession
from app.playgroups.services import hash_invite_token
from tests.invite_support import make_account
from tests.register_support import (
    PREVIEW,
    assert_envelope,
    make_inviter,
    make_token_invite,
    new_token,
    scalar,
    use_count_of,
)

pytestmark = pytest.mark.usefixtures("app_schema")


async def test_a_valid_invite_previews_the_inviter_and_expiry_without_auth(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    inviter = await make_inviter(app_schema, "Grace Hopper")
    token = await make_token_invite(app_schema, inviter)
    expires = await scalar(
        app_schema,
        "SELECT expires_at FROM invites WHERE token_hash = :h",
        h=hash_invite_token(token),
    )

    response = await client.post(PREVIEW, json={"token": token})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"inviter_display_name", "expires_at"}
    assert body["inviter_display_name"] == "Grace Hopper"
    assert datetime.fromisoformat(body["expires_at"]) == expires


async def test_preview_does_not_use_up_the_invite(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    token = await make_token_invite(app_schema, await make_inviter(app_schema))
    for _ in range(3):
        assert (await client.post(PREVIEW, json={"token": token})).status_code == 200
    assert await use_count_of(app_schema, token) == 0


async def test_preview_answers_an_empty_name_when_the_inviter_has_no_player(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    user_id, _ = await make_account(app_schema, admin=True)  # a user row without a player
    token = await make_token_invite(app_schema, user_id)

    response = await client.post(PREVIEW, json={"token": token})

    assert response.status_code == 200
    assert response.json()["inviter_display_name"] == ""


async def test_preview_leaks_nothing_but_the_name_and_expiry(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    inviter = await make_inviter(app_schema)
    token = await make_token_invite(app_schema, inviter, max_uses=5, use_count=2)

    text = (await client.post(PREVIEW, json={"token": token})).text

    for hidden in (token, str(inviter), hash_invite_token(token), "use_count", "max_uses"):
        assert hidden not in text
    assert "created_by" not in text


def _unusable(kind: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "revoked": {"revoked_at": now},
        "expired": {"expires_at": now - timedelta(seconds=1)},
        "used_up": {"max_uses": 1, "use_count": 1},
    }[kind]


@pytest.mark.parametrize("kind", ["revoked", "expired", "used_up"])
async def test_an_unusable_invite_previews_as_410_with_the_full_envelope(
    client: httpx.AsyncClient, app_schema: AppSession, kind: str
) -> None:
    inviter = await make_inviter(app_schema)
    token = await make_token_invite(app_schema, inviter, **_unusable(kind))

    error = assert_envelope(
        await client.post(PREVIEW, json={"token": token}), 410, "invite_no_longer_valid"
    )

    assert error["message"] == "Invite no longer valid."


async def test_an_unknown_token_previews_as_410_with_the_full_envelope(
    client: httpx.AsyncClient,
) -> None:
    error = assert_envelope(
        await client.post(PREVIEW, json={"token": new_token()}), 410, "invite_no_longer_valid"
    )
    assert error["message"] == "Invite no longer valid."


async def test_a_128_character_token_is_accepted_and_unknown_gives_410(
    client: httpx.AsyncClient,
) -> None:
    assert_envelope(
        await client.post(PREVIEW, json={"token": "a" * 128}), 410, "invite_no_longer_valid"
    )


@pytest.mark.parametrize("token", ["", "a" * 129], ids=["empty", "129_chars"])
async def test_a_token_outside_1_to_128_characters_is_422(
    client: httpx.AsyncClient, token: str
) -> None:
    error = assert_envelope(
        await client.post(PREVIEW, json={"token": token}), 422, "validation_error"
    )
    assert "body.token" in [f["path"] for f in error["details"]["fields"]]


async def test_a_missing_token_is_422(client: httpx.AsyncClient) -> None:
    assert_envelope(await client.post(PREVIEW, json={}), 422, "validation_error")


async def test_the_token_cannot_travel_in_a_url(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/v1/invites/{new_token()}/preview")
    assert response.status_code in (404, 405)


async def test_the_preview_route_is_tagged_invites_and_needs_no_auth(
    client: httpx.AsyncClient,
) -> None:
    spec = (await client.get("/api/v1/openapi.json")).json()
    operation = spec["paths"][PREVIEW]["post"]
    assert operation["tags"] == ["invites"]
    assert not operation.get("security")


async def test_no_log_record_contains_the_token_during_a_preview(
    client: httpx.AsyncClient, app_schema: AppSession, caplog: pytest.LogCaptureFixture
) -> None:
    valid = await make_token_invite(app_schema, await make_inviter(app_schema))
    unknown = new_token()
    caplog.set_level(logging.DEBUG)

    await client.post(PREVIEW, json={"token": valid})
    await client.post(PREVIEW, json={"token": unknown})

    assert valid not in caplog.text
    assert unknown not in caplog.text
