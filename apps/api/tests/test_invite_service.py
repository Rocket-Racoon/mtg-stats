"""``validate_invite`` and ``lock_invite`` for every invite state (S-2.1.2 AC4, decisions 3-7)."""

from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import update

from app.core.db import AppSession
from app.playgroups.models import Invite
from app.playgroups.services import InviteNoLongerValidError, lock_invite, validate_invite
from tests.invite_support import create_via_api, make_account, token_from_url
from tests.statement_counter import StatementCounter

pytestmark = pytest.mark.usefixtures("app_schema")
WEB = "http://localhost:5173"

Check = Callable[[AppSession, str], Awaitable[object]]
CHECKS: list[Check] = [validate_invite, lock_invite]
IDS = ["validate_invite", "lock_invite"]


async def _invite(
    client: httpx.AsyncClient, session: AppSession, **changes: Any
) -> tuple[str, UUID]:
    """Create an invite through the real route, apply ``changes`` to its row, return its token."""
    _, headers = await make_account(session, admin=True)
    body = (await create_via_api(client, headers)).json()
    invite_id = UUID(body["id"])
    if changes:
        await session.execute(update(Invite).where(Invite.id == invite_id).values(**changes))
        await session.commit()
    return token_from_url(body["url"], WEB), invite_id


def test_the_error_is_a_410_with_the_documented_code_and_message() -> None:
    assert InviteNoLongerValidError.status_code == 410
    assert InviteNoLongerValidError.code == "invite_no_longer_valid"
    assert InviteNoLongerValidError.default_message == "Invite no longer valid."


@pytest.mark.parametrize("check", CHECKS, ids=IDS)
async def test_an_active_invite_is_valid(
    client: httpx.AsyncClient, app_schema: AppSession, check: Check
) -> None:
    token, _ = await _invite(client, app_schema)
    assert await check(app_schema, token) is not None


@pytest.mark.parametrize("check", CHECKS, ids=IDS)
async def test_an_invite_with_uses_left_is_valid(
    client: httpx.AsyncClient, app_schema: AppSession, check: Check
) -> None:
    token, _ = await _invite(client, app_schema, max_uses=3, use_count=2)
    assert await check(app_schema, token) is not None


@pytest.mark.parametrize("check", CHECKS, ids=IDS)
async def test_a_revoked_invite_is_no_longer_valid(
    client: httpx.AsyncClient, app_schema: AppSession, check: Check
) -> None:
    token, _ = await _invite(client, app_schema, revoked_at=datetime.now(UTC))
    with pytest.raises(InviteNoLongerValidError) as caught:
        await check(app_schema, token)
    assert caught.value.code == "invite_no_longer_valid"
    assert caught.value.status_code == 410


@pytest.mark.parametrize("check", CHECKS, ids=IDS)
async def test_an_expired_invite_is_no_longer_valid(
    client: httpx.AsyncClient, app_schema: AppSession, check: Check
) -> None:
    token, _ = await _invite(
        client, app_schema, expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    with pytest.raises(InviteNoLongerValidError):
        await check(app_schema, token)


@pytest.mark.parametrize("check", CHECKS, ids=IDS)
async def test_an_invite_expiring_at_this_instant_counts_as_expired(
    client: httpx.AsyncClient, app_schema: AppSession, check: Check
) -> None:
    # The clock cannot be pinned, so the row's instant is already past or equal when checked;
    # both must be treated as expired (expires_at <= now).
    token, _ = await _invite(client, app_schema, expires_at=datetime.now(UTC))
    with pytest.raises(InviteNoLongerValidError):
        await check(app_schema, token)


@pytest.mark.parametrize("check", CHECKS, ids=IDS)
async def test_an_invite_expiring_in_the_future_is_still_valid(
    client: httpx.AsyncClient, app_schema: AppSession, check: Check
) -> None:
    token, _ = await _invite(
        client, app_schema, expires_at=datetime.now(UTC) + timedelta(minutes=5)
    )
    assert await check(app_schema, token) is not None


@pytest.mark.parametrize("check", CHECKS, ids=IDS)
async def test_a_used_up_invite_is_no_longer_valid(
    client: httpx.AsyncClient, app_schema: AppSession, check: Check
) -> None:
    token, _ = await _invite(client, app_schema, use_count=1)  # max_uses defaults to 1
    with pytest.raises(InviteNoLongerValidError):
        await check(app_schema, token)


@pytest.mark.parametrize("check", CHECKS, ids=IDS)
async def test_an_unknown_token_is_no_longer_valid(
    client: httpx.AsyncClient, app_schema: AppSession, check: Check
) -> None:
    await _invite(client, app_schema)
    for token in ("definitely-not-a-real-token", "", "A" * 43):
        with pytest.raises(InviteNoLongerValidError) as caught:
            await check(app_schema, token)
        assert caught.value.code == "invite_no_longer_valid"


@pytest.mark.parametrize("check", CHECKS, ids=IDS)
async def test_the_stored_hash_is_not_accepted_in_place_of_the_token(
    client: httpx.AsyncClient, app_schema: AppSession, check: Check
) -> None:
    _, invite_id = await _invite(client, app_schema)
    invite = await app_schema.get(Invite, invite_id)
    assert invite is not None
    with pytest.raises(InviteNoLongerValidError):
        await check(app_schema, invite.token_hash)


async def test_lock_invite_locks_the_row_and_validate_invite_does_not(
    client: httpx.AsyncClient,
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    token, _ = await _invite(client, app_schema)
    with count_statements() as plain:
        await validate_invite(app_schema, token)
    with count_statements() as locked:
        await lock_invite(app_schema, token)
    assert not any("FOR UPDATE" in s.upper() for s in plain.statements)
    assert any("FOR UPDATE" in s.upper() for s in locked.statements)


async def test_revoking_through_the_route_makes_the_token_invalid(
    client: httpx.AsyncClient, app_schema: AppSession
) -> None:
    _, headers = await make_account(app_schema, admin=True)
    body = (await create_via_api(client, headers)).json()
    token = token_from_url(body["url"], WEB)
    await validate_invite(app_schema, token)

    revoke = await client.post(f"/api/v1/invites/{body['id']}/revoke", headers=headers)
    assert revoke.status_code == 200

    with pytest.raises(InviteNoLongerValidError):
        await validate_invite(app_schema, token)
