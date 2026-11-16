"""HTTP layer of the ``identity`` domain. Thin: parse, call a service, respond."""

from fastapi import APIRouter

from app.core.db import SessionDep
from app.identity import services
from app.identity.schemas import LoginRequest, TokenResponse

router = APIRouter()
auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.post("/login", summary="Sign in")
async def login(body: LoginRequest, session: SessionDep) -> TokenResponse:
    """Exchange a username and password for an access token and a refresh token.

    Unknown user, wrong password and inactive account all return the same 401
    (``invalid_credentials``).
    """
    result = await services.sign_in(session, username=body.username, password=body.password)
    return TokenResponse(
        access_token=result.access_token.token,
        refresh_token=result.refresh_token.token,
        token_type="bearer",  # noqa: S106  (a scheme name)
        expires_in=result.access_token.expires_in,
    )


router.include_router(auth_router)
