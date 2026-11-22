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
    MemberOut,
    MemberRoleUpdate,
    PlaygroupCreate,
    PlaygroupOut,
    PlaygroupPageOut,
    PlaygroupUpdate,
    TransferOwnership,
)

invites_router = APIRouter(prefix="/invites", tags=["invites"])
playgroups_router = APIRouter(prefix="/playgroups", tags=["playgroups"])
router = APIRouter()


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


@invites_router.post("", summary="Create an invite link", status_code=201)
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


@invites_router.post("/preview", summary="Preview an invite")
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


@invites_router.get("", summary="List my invites")
async def list_invites(
    user: CurrentUserDep, session: SessionDep, params: PageParamsDep
) -> InvitePageOut:
    """The caller's own invites, newest first, with a derived status. Never the token."""
    page = await services.list_invites(session, user_id=user.id, params=params)
    return InvitePageOut(items=[_out(i) for i in page.items], next_cursor=page.next_cursor)


@invites_router.post("/{invite_id}/revoke", summary="Revoke an invite")
async def revoke_invite(invite_id: str, user: CurrentUserDep, session: SessionDep) -> InviteOut:
    """Revoke one of the caller's invites. Idempotent. Anyone else's, or unknown, is a 404."""
    try:
        parsed = UUID(invite_id)
    except ValueError:
        raise NotFoundError from None
    return _out(await services.revoke_invite(session, invite_id=parsed, user_id=user.id))


def _group_out(info: services.PlaygroupInfo) -> PlaygroupOut:
    return PlaygroupOut(
        id=info.id,
        name=info.name,
        description=info.description,
        role=info.role,
        created_at=info.created_at,
        updated_at=info.updated_at,
        joined_at=info.joined_at,
    )


@playgroups_router.post("", summary="Create a playgroup", status_code=201)
async def create_playgroup(
    body: PlaygroupCreate, user: CurrentUserDep, session: SessionDep
) -> PlaygroupOut:
    """Create a playgroup; the caller becomes its owner. Any signed-in user, no cap (C3).

    `name` is 3-50 characters (trimmed) and `description` up to 500; otherwise 422.
    """
    info = await services.create_playgroup(
        session, user_id=user.id, name=body.name, description=body.description
    )
    return _group_out(info)


@playgroups_router.get("", summary="List my playgroups")
async def list_playgroups(
    user: CurrentUserDep, session: SessionDep, params: PageParamsDep
) -> PlaygroupPageOut:
    """Every playgroup the caller belongs to, with their role in each, newest membership first."""
    page = await services.list_playgroups(session, user_id=user.id, params=params)
    return PlaygroupPageOut(items=[_group_out(i) for i in page.items], next_cursor=page.next_cursor)


@playgroups_router.get("/{playgroup_id}", summary="Get a playgroup")
async def get_playgroup(
    playgroup_id: str, access: services.MemberDep, session: SessionDep
) -> PlaygroupOut:
    """One playgroup with the caller's role. A non-member, or an unknown id, gets a 404."""
    return _group_out(await services.get_playgroup(session, access))


@playgroups_router.patch("/{playgroup_id}", summary="Rename or re-describe a playgroup")
async def update_playgroup(
    playgroup_id: str,
    body: PlaygroupUpdate,
    access: services.PlaygroupAdminDep,
    session: SessionDep,
) -> PlaygroupOut:
    """Change the name and/or description. Owner or admin only: a plain member gets 403, a
    non-member 404."""
    info = await services.update_playgroup(
        session, access, name=body.name, description=body.description
    )
    return _group_out(info)


@playgroups_router.patch("/{playgroup_id}/members/{player_id}", summary="Change a member's role")
async def change_member_role(
    playgroup_id: str,
    player_id: str,
    body: MemberRoleUpdate,
    access: services.PlaygroupOwnerDep,
    session: SessionDep,
) -> MemberOut:
    """Promote a member to `admin` or demote an admin to `member`. Owner only.

    A non-owner member gets 403 and a non-member 404. A target who is not an active member is a
    404. Setting the current role again succeeds. The owner changing their own role is 409
    `transfer_ownership_first`. `role` is `admin` or `member`; anything else, `owner` included,
    is 422 `validation_error` (for a non-owner the 403 or 404 comes first).
    """
    try:
        target = UUID(player_id)
    except ValueError:
        raise NotFoundError from None
    info = await services.change_member_role(session, access, player_id=target, role=body.role)
    return MemberOut(player_id=info.player_id, role=info.role, joined_at=info.joined_at)


@playgroups_router.post(
    "/{playgroup_id}/transfer-ownership", summary="Transfer ownership of a playgroup"
)
async def transfer_ownership(
    playgroup_id: str,
    body: TransferOwnership,
    access: services.PlaygroupOwnerDep,
    session: SessionDep,
) -> PlaygroupOut:
    """Make another active member the owner; the caller becomes an admin. Owner only.

    Returns the playgroup with the caller's new role. A target who is not an active member is a
    404; the owner naming themselves is 409 `already_owner`.
    """
    return _group_out(await services.transfer_ownership(session, access, player_id=body.player_id))


router.include_router(invites_router)
router.include_router(playgroups_router)
