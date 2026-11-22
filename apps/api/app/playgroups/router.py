"""HTTP layer of the ``playgroups`` domain. Thin: parse, call a service, respond."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Request, Response

from app.core.auth import CurrentUserDep, SiteAdminDep
from app.core.db import SessionDep
from app.core.errors import NotFoundError
from app.core.paging import PageParamsDep
from app.core.rate_limit import PublicRateLimitDep
from app.playgroups import services
from app.playgroups.schemas import (
    CommonPlaygroupOut,
    InviteCreate,
    InviteCreated,
    InviteJoinOut,
    InviteJoinRequest,
    InviteOut,
    InvitePageOut,
    InvitePreviewOut,
    InvitePreviewRequest,
    MemberOut,
    MemberRoleUpdate,
    PlayerMergedOut,
    PlayerProfileOut,
    PlaygroupCreate,
    PlaygroupOut,
    PlaygroupPageOut,
    PlaygroupUpdate,
    TransferOwnership,
)

invites_router = APIRouter(prefix="/invites", tags=["invites"])
playgroups_router = APIRouter(prefix="/playgroups", tags=["playgroups"])
players_router = APIRouter(prefix="/players", tags=["players"])
router = APIRouter()


def _out(info: services.InviteInfo) -> InviteOut:
    return InviteOut(
        id=info.id,
        playgroup_id=info.playgroup_id,
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
    """Create a site-level invite and return its `https` URL, which is shown only once.

    Site admins only (403 otherwise). It lets someone register but joins no playgroup; playgroup
    invites are created under `/playgroups/{id}/invites`. The body is optional:
    `expires_in_days` 1-30 (default 7) and `max_uses` 1-20 (default 1).
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
    """Show the inviter's display name, the expiry and, for a playgroup invite, the playgroup's
    name (`playgroup_name`, null for a site-level invite). No authentication.

    Limited to 30 requests per IP address per 15 minutes by default, shared with registration (429
    `rate_limited`, with `Retry-After`). The token is in the body so it never reaches a URL or an
    access log. A revoked, expired, used-up or unknown token is a 410 `invite_no_longer_valid`.
    """
    preview = await services.preview_invite(session, body.token)
    return InvitePreviewOut(
        inviter_display_name=preview.inviter_display_name,
        expires_at=preview.expires_at,
        playgroup_name=preview.playgroup_name,
    )


@invites_router.post("/join", summary="Join a playgroup with an invite")
async def join_with_invite(
    body: InviteJoinRequest, user: CurrentUserDep, session: SessionDep
) -> InviteJoinOut:
    """Confirm "Join <playgroup>" as the signed-in user: they become a `member`.

    The invite's use count goes up in the same transaction. A user who is already an active
    member of the invite's playgroup (the owner and admins included) gets 200 with
    `already_member: true` whatever the invite's state, so re-opening a used-up link says
    "You're already in <name>"; nothing changes and no use is counted. Everyone else needs an
    active invite: a revoked, expired or used-up token is a 410 `invite_no_longer_valid`, the
    same answer as for an unknown token or a site-level invite (a former member joins again with
    a new membership). The token is in the body so it never reaches a URL or a log.
    """
    result = await services.join_with_invite(session, user_id=user.id, token=body.token)
    return InviteJoinOut(
        already_member=result.already_member, playgroup=_group_out(result.playgroup)
    )


@invites_router.get("", summary="List my invites")
async def list_invites(
    user: CurrentUserDep, session: SessionDep, params: PageParamsDep
) -> InvitePageOut:
    """The caller's own site-level invites, newest first, with a derived status.

    Playgroup invites are not listed here (see `/playgroups/{id}/invites`). Never the token.
    """
    page = await services.list_invites(session, user_id=user.id, params=params)
    return InvitePageOut(items=[_out(i) for i in page.items], next_cursor=page.next_cursor)


@invites_router.post("/{invite_id}/revoke", summary="Revoke an invite")
async def revoke_invite(invite_id: str, user: CurrentUserDep, session: SessionDep) -> InviteOut:
    """Revoke one of the caller's site-level invites. Idempotent.

    Anyone else's, a playgroup invite, or an unknown id is a 404.
    """
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


@playgroups_router.post(
    "/{playgroup_id}/invites", summary="Create an invite to a playgroup", status_code=201
)
async def create_playgroup_invite(
    playgroup_id: str,
    access: services.PlaygroupAdminDep,
    session: SessionDep,
    body: Annotated[InviteCreate | None, Body()] = None,
) -> InviteCreated:
    """Create an invite bound to this playgroup; its `https` URL is shown only once.

    Owner or admin only: a plain member gets 403, a non-member 404. The body is optional:
    `expires_in_days` 1-30 (default 7) and `max_uses` 1-20 (default 1).
    """
    body = body or InviteCreate()
    created = await services.create_playgroup_invite(
        session, access, expires_in_days=body.expires_in_days, max_uses=body.max_uses
    )
    return InviteCreated(**_out(created.invite).model_dump(), url=created.url)


@playgroups_router.get("/{playgroup_id}/invites", summary="List a playgroup's invites")
async def list_playgroup_invites(
    playgroup_id: str,
    access: services.PlaygroupAdminDep,
    session: SessionDep,
    params: PageParamsDep,
) -> InvitePageOut:
    """Every invite of the playgroup, whoever created it, newest first. Never the token.

    Owner or admin only: a plain member gets 403, a non-member 404.
    """
    page = await services.list_playgroup_invites(session, access, params=params)
    return InvitePageOut(items=[_out(i) for i in page.items], next_cursor=page.next_cursor)


@playgroups_router.post(
    "/{playgroup_id}/invites/{invite_id}/revoke", summary="Revoke a playgroup invite"
)
async def revoke_playgroup_invite(
    playgroup_id: str,
    invite_id: str,
    access: services.PlaygroupAdminDep,
    session: SessionDep,
) -> InviteOut:
    """Revoke any invite of this playgroup. Idempotent. Owner or admin only.

    An invite that is unknown or belongs to another playgroup is a 404.
    """
    try:
        parsed = UUID(invite_id)
    except ValueError:
        raise NotFoundError from None
    return _out(await services.revoke_playgroup_invite(session, access, invite_id=parsed))


# Ordering rule: this route takes a ``str`` and matches every single-segment GET under
# ``/players``. Any future literal ``/players/<word>`` GET route must be registered BEFORE it.
@players_router.get(
    "/{player_id}",
    summary="View a player's profile",
    response_model=PlayerProfileOut,
    responses={308: {"model": PlayerMergedOut, "description": "A merged guest; see `Location`."}},
)
async def get_player_profile(
    player_id: str,
    request: Request,
    response: Response,
    user: CurrentUserDep,
    session: SessionDep,
) -> Response | PlayerProfileOut:
    """A player's display name, avatar color, guest badge, "member since" and the playgroups
    you have in common with them. No email, username or stats.

    You can open your own profile, and another player's when you are both active members of a
    playgroup, or they are a guest whose home playgroup is one you belong to. Anyone else, an
    unknown id and an unparseable id are all the same 404 `not_found`. A guest that was merged
    into another player answers 308 with `Location` set to the survivor's profile (and
    `merged_into_id` in the body), but only when you could open both the old guest (you belong to
    its home playgroup or share one with it, or you are the survivor) and the survivor; otherwise
    404. `member_since` is when the player record was created.

    Browsers' `fetch` follows the 308 by itself, so a client detects a merge because the returned
    profile's `id` differs from the id it asked for; the 308 body is visible only to clients that
    do not follow redirects. Responses are `Cache-Control: no-store` (the answer depends on the
    viewer, and a cached 308 would be replayed for another user of the same browser).
    """
    try:
        parsed = UUID(player_id)
    except ValueError:
        raise NotFoundError from None
    profile = await services.get_player_profile(session, viewer_user_id=user.id, player_id=parsed)
    response.headers["Cache-Control"] = "no-store"
    if profile.redirected_from is not None:
        location = request.app.url_path_for("get_player_profile", player_id=str(profile.id))
        return Response(
            content=PlayerMergedOut(merged_into_id=profile.id).model_dump_json(),
            status_code=308,
            media_type="application/json",
            headers={"Location": str(location), "Cache-Control": "no-store"},
        )
    return PlayerProfileOut(
        id=profile.id,
        display_name=profile.display_name,
        avatar_color=profile.avatar_color,
        is_guest=profile.is_guest,
        member_since=profile.member_since,
        common_playgroups=[
            CommonPlaygroupOut(id=g.id, name=g.name) for g in profile.common_playgroups
        ],
    )


router.include_router(invites_router)
router.include_router(playgroups_router)
router.include_router(players_router)
