"""HTTP layer of the ``playgroups`` domain. Thin: parse, call a service, respond."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body

from app.core.auth import CurrentUserDep, SiteAdminDep
from app.core.db import SessionDep
from app.core.errors import NotFoundError
from app.core.paging import PageParamsDep
from app.core.rate_limit import PublicRateLimitDep
from app.playgroups import services
from app.playgroups.schemas import (
    InviteCreate,
    InviteCreated,
    InviteOut,
    InvitePageOut,
    InvitePreviewOut,
    InvitePreviewRequest,
)

router = APIRouter(prefix="/invites", tags=["invites"])


def _out(info: services.InviteInfo) -> InviteOut:
    return InviteOut(
        id=info.id,
        status=info.status,
        expires_at=info.expires_at,
        max_uses=info.max_uses,
        use_count=info.use_count,
        revoked_at=info.revoked_at,
        created_at=info.created_at,
    )


@router.post("", summary="Create an invite link", status_code=201)
async def create_invite(
    admin: SiteAdminDep,
    session: SessionDep,
    body: Annotated[InviteCreate | None, Body()] = None,
) -> InviteCreated:
    """Create an invite and return its `https` URL, which is shown only once.

    Site admins only (403 otherwise). The body is optional: `expires_in_days` 1-30 (default 7)
    and `max_uses` 1-20 (default 1).
    """
    body = body or InviteCreate()
    created = await services.create_invite(
        session,
        created_by=admin.id,
        expires_in_days=body.expires_in_days,
        max_uses=body.max_uses,
    )
    return InviteCreated(**_out(created.invite).model_dump(), url=created.url)


@router.post("/preview", summary="Preview an invite")
async def preview_invite(
    body: InvitePreviewRequest, session: SessionDep, _rate_limit: PublicRateLimitDep
) -> InvitePreviewOut:
    """Show the inviter's display name and the expiry of an invite. No authentication.

    Limited to 30 requests per IP address per 15 minutes by default, shared with registration (429
    `rate_limited`, with `Retry-After`). The token is in the body so it never reaches a URL or an
    access log. A revoked, expired, used-up or unknown token is a 410 `invite_no_longer_valid`.
    """
    preview = await services.preview_invite(session, body.token)
    return InvitePreviewOut(
        inviter_display_name=preview.inviter_display_name, expires_at=preview.expires_at
    )


@router.get("", summary="List my invites")
async def list_invites(
    user: CurrentUserDep, session: SessionDep, params: PageParamsDep
) -> InvitePageOut:
    """The caller's own invites, newest first, with a derived status. Never the token."""
    page = await services.list_invites(session, user_id=user.id, params=params)
    return InvitePageOut(items=[_out(i) for i in page.items], next_cursor=page.next_cursor)


@router.post("/{invite_id}/revoke", summary="Revoke an invite")
async def revoke_invite(invite_id: str, user: CurrentUserDep, session: SessionDep) -> InviteOut:
    """Revoke one of the caller's invites. Idempotent. Anyone else's, or unknown, is a 404."""
    try:
        parsed = UUID(invite_id)
    except ValueError:
        raise NotFoundError from None
    return _out(await services.revoke_invite(session, invite_id=parsed, user_id=user.id))
