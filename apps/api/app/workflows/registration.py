"""Register with an invite link (S-2.1.3): one transaction across ``playgroups`` and ``identity``.

Order: validate the body, hash the password off the event loop, lock the invite (410 if it is
not active), create the User and Player (409 if the username is taken; the request session then
rolls back, so the invite is not used), count the use, and issue the first session. The token
delivery is the same as ``POST /auth/login``: web gets the refresh token as a cookie only, mobile
gets it in the body.
"""

import asyncio

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.core.auth import TokenResponse, client_mode, token_response
from app.core.db import AppSession, SessionDep
from app.core.rate_limit import PublicRateLimitDep
from app.identity import services as identity
from app.playgroups import services as playgroups

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    """Body of ``POST /auth/register``. The rules live in ``identity.services``."""

    invite_token: str = Field(min_length=1, max_length=128)
    username: str
    password: str
    display_name: str
    email: str = Field(
        default="",
        description="Optional. Stored trimmed; never verified, unique, shown or used to sign in.",
    )

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        identity.validate_username(value)
        return value

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        identity.validate_password(value)
        return value

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        return identity.normalize_display_name(value)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return identity.normalize_email(value)


async def run_registration(session: AppSession, body: RegisterRequest) -> identity.SignInResult:
    """Run the registration steps in the caller's session; never commits.

    Raises:
        InviteNoLongerValidError: the invite is revoked, expired, used up or unknown (410).
        UsernameTakenError: the username exists, compared case-insensitively (409).
    """
    password_hash = await asyncio.to_thread(identity.hash_password, body.password)
    invite = await playgroups.lock_invite(session, body.invite_token)
    player = await identity.create_user_with_player(
        session,
        username=body.username,
        password=body.password,
        display_name=body.display_name,
        email=body.email,
        password_hash=password_hash,
    )
    await playgroups.consume_invite(session, invite.id)
    if player.user_id is None:  # a registered player always has a user
        raise RuntimeError("create_user_with_player returned a player without a user")
    return await identity.start_session(session, user_id=player.user_id)


@router.post(
    "/register",
    summary="Register with an invite link",
    status_code=201,
    response_model_exclude_none=True,
)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    _rate_limit: PublicRateLimitDep,
) -> TokenResponse:
    """Create an account from an invite and sign the new user in.

    Limited to 30 requests per IP address per 15 minutes by default, shared with the invite
    preview (429 `rate_limited`, with `Retry-After`).

    The invite is checked first (410 `invite_no_longer_valid`), then the username (409
    `username_taken`, and the invite is not used up). Field errors are a 422.

    Client mode, as for login: send `X-Client: mobile` without an `Origin` header to get the
    refresh token in the response body; every other request is a web client and gets it in the
    `mtgstats_refresh` cookie (`HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/api/v1/auth`).
    """
    result = await run_registration(session, body)
    return token_response(result.access_token, result.refresh_token, client_mode(request), response)
