"""HTTP layer of the ``identity`` domain. Thin: parse, call a service, respond.

Client modes (S-2.1.5): a request is ``mobile`` only when it has ``X-Client: mobile`` and no
``Origin`` header; everything else is ``web``. Web clients get the refresh token as the
``mtgstats_refresh`` cookie (``HttpOnly``, ``Secure``, ``SameSite=Strict``, path ``/api/v1/auth``)
and never in a body, and refresh and logout read it only from that cookie. Mobile clients get it in
the response body and send it in the request body.
"""

from fastapi import APIRouter, Request, Response

from app.core.auth import (
    REFRESH_COOKIE_NAME,
    ClientMode,
    CurrentUserDep,
    InvalidTokenError,
    TokenResponse,
    clear_refresh_cookie,
    client_mode,
    token_response,
)
from app.core.db import SessionDep
from app.core.errors import AuthenticationError
from app.identity import services
from app.identity.schemas import LoginRequest, MeResponse, RefreshRequest

router = APIRouter()
auth_router = APIRouter(prefix="/auth", tags=["auth"])


def _presented_token(request: Request, mode: ClientMode, body: RefreshRequest | None) -> str | None:
    """The refresh token from the cookie (web) or the body (mobile); never the other."""
    if mode == "web":
        return request.cookies.get(REFRESH_COOKIE_NAME) or None
    return (body.refresh_token if body else None) or None


def _clearing_cookie_header() -> str:
    """The ``Set-Cookie`` value that expires the refresh cookie (via ``clear_refresh_cookie``)."""
    scratch = Response()
    clear_refresh_cookie(scratch)
    return scratch.headers["set-cookie"]


@auth_router.post("/login", summary="Sign in", response_model_exclude_none=True)
async def login(
    body: LoginRequest, request: Request, response: Response, session: SessionDep
) -> TokenResponse:
    """Exchange a username and password for an access token and a refresh token.

    Unknown user, wrong password and inactive account all return the same 401
    (``invalid_credentials``). Each sign-in starts a new refresh-token family.

    Client mode: send `X-Client: mobile` without an `Origin` header to get the refresh token in
    the response body; every other request is a web client and gets it in the `mtgstats_refresh`
    cookie (`HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/api/v1/auth`) instead.
    """
    result = await services.sign_in(session, username=body.username, password=body.password)
    return token_response(result.access_token, result.refresh_token, client_mode(request), response)


@auth_router.post(
    "/refresh",
    summary="Rotate the refresh token",
    response_model=TokenResponse,
    response_model_exclude_none=True,
)
async def refresh(
    request: Request, response: Response, session: SessionDep, body: RefreshRequest | None = None
) -> TokenResponse | Response:
    """Exchange a refresh token for a new access token and a new refresh token.

    The presented token is revoked. Presenting a revoked token (already rotated or signed out)
    returns 401 and revokes every token of its family; so does a token of an inactive user. A
    missing token is a 401 `unauthenticated`. In web mode a 401 also clears the cookie.

    Web clients send the cookie and must use `fetch(..., {credentials: "include"})`; any body token
    is ignored. Mobile clients send `{"refresh_token": ...}`.

    Client mode: send `X-Client: mobile` without an `Origin` header to get the refresh token in
    the response body; every other request is a web client and gets it in the `mtgstats_refresh`
    cookie (`HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/api/v1/auth`) instead.
    """
    mode = client_mode(request)
    token = _presented_token(request, mode, body)
    try:
        if token is None:
            raise AuthenticationError
        result = await services.refresh_session(session, refresh_token=token)
    except AuthenticationError as exc:
        if mode == "web":
            # Re-raise with the clearing cookie so the 401 envelope carries it; the session
            # dependency's persist_changes rule alone decides whether the 401 commits.
            exc.headers = {**(exc.headers or {}), "Set-Cookie": _clearing_cookie_header()}
        raise
    return token_response(result.access_token, result.refresh_token, mode, response)


@auth_router.post("/logout", summary="Sign out", status_code=204)
async def logout(
    request: Request, response: Response, session: SessionDep, body: RefreshRequest | None = None
) -> Response:
    """Revoke the current refresh token and clear the cookie.

    Needs only the refresh token (cookie for web, body for mobile). Idempotent: a missing,
    invalid, unknown or already revoked token still returns 204 and revokes nothing else.
    """
    mode = client_mode(request)
    token = _presented_token(request, mode, body)
    if token is not None:
        await services.revoke_refresh_token(session, refresh_token=token)
    if mode == "web":
        clear_refresh_cookie(response)
    response.status_code = 204
    return response


@auth_router.post("/logout-all", summary="Sign out everywhere", status_code=204)
async def logout_all(
    user: CurrentUserDep, request: Request, response: Response, session: SessionDep
) -> Response:
    """Revoke all of the caller's refresh tokens (every device) and clear the cookie.

    Requires a valid access token. Access tokens already issued stay valid until they expire.
    """
    await services.revoke_all_refresh_tokens(session, user_id=user.id)
    if client_mode(request) == "web":
        clear_refresh_cookie(response)
    response.status_code = 204
    return response


@auth_router.get("/me", summary="Current user")
async def me(user: CurrentUserDep, session: SessionDep) -> MeResponse:
    """Return the signed-in user, read from the database on every request.

    `is_site_admin` is only for showing or hiding the admin entry in the UI. A deleted or
    inactive user gets 401 `invalid_token`.
    """
    info = await services.get_me(session, user.id)
    if info is None:
        raise InvalidTokenError
    return MeResponse(
        id=info.id,
        username=info.username,
        display_name=info.display_name,
        is_site_admin=info.is_site_admin,
    )


router.include_router(auth_router)
