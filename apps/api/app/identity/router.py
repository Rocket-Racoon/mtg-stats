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
    clear_refresh_cookie,
    client_mode,
    set_refresh_cookie,
)
from app.core.db import SessionDep
from app.core.errors import AuthenticationError, error_response
from app.identity import services
from app.identity.schemas import LoginRequest, RefreshRequest, TokenResponse

router = APIRouter()
auth_router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(
    result: services.SignInResult, mode: ClientMode, response: Response
) -> TokenResponse:
    """Build the body, and set the refresh cookie (web) or put the token in the body (mobile)."""
    if mode == "web":
        set_refresh_cookie(response, result.refresh_token)
    return TokenResponse(
        access_token=result.access_token.token,
        refresh_token=result.refresh_token.token if mode == "mobile" else None,
        token_type="bearer",  # noqa: S106  (a scheme name)
        expires_in=result.access_token.expires_in,
    )


def _presented_token(request: Request, mode: ClientMode, body: RefreshRequest | None) -> str | None:
    """The refresh token from the cookie (web) or the body (mobile); never the other."""
    if mode == "web":
        return request.cookies.get(REFRESH_COOKIE_NAME) or None
    return (body.refresh_token if body else None) or None


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
    return _token_response(result, client_mode(request), response)


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
        if mode == "mobile":
            raise
        # Answer here so the 401 can clear the cookie; returning normally lets the session
        # dependency commit, which is how a family revocation survives the 401.
        failure = error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)
        clear_refresh_cookie(failure)
        return failure
    return _token_response(result, mode, response)


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
    clear_refresh_cookie(response)
    response.status_code = 204
    return response


@auth_router.post("/logout-all", summary="Sign out everywhere", status_code=204)
async def logout_all(user: CurrentUserDep, response: Response, session: SessionDep) -> Response:
    """Revoke all of the caller's refresh tokens (every device) and clear the cookie.

    Requires a valid access token. Access tokens already issued stay valid until they expire.
    """
    await services.revoke_all_refresh_tokens(session, user_id=user.id)
    clear_refresh_cookie(response)
    response.status_code = 204
    return response


router.include_router(auth_router)
