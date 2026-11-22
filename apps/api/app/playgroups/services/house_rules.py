"""Read and save a playgroup's house rules note (S-2.5.4)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.identity import services as identity_services
from app.playgroups import repository
from app.playgroups.models import ROLE_ADMIN, ROLE_OWNER
from app.playgroups.services.access import MembershipInfo


@dataclass(frozen=True)
class HouseRulesInfo:
    """The house rules text and who last edited it (the editor and time are None until a save)."""

    text: str
    edited_by_player_id: UUID | None
    edited_by_display_name: str | None
    edited_at: datetime | None


async def _info(
    session: AsyncSession, text: str, editor_id: UUID | None, edited_at: datetime | None
) -> HouseRulesInfo:
    """Resolve the editor's current id and display name (at most two queries, no N+1).

    A player merged into another is shown as the player they were merged into. The editor is
    shown whether or not they are still a member: the attribution is history.
    """
    player_id: UUID | None = None
    name: str | None = None
    if editor_id is not None:
        player = await identity_services.get_player(session, editor_id)
        if player is not None and player.merged_into_id is not None:
            player = await identity_services.get_player(session, player.merged_into_id) or player
        if player is not None:
            player_id, name = player.id, player.display_name
    return HouseRulesInfo(
        text=text, edited_by_player_id=player_id, edited_by_display_name=name, edited_at=edited_at
    )


async def get_house_rules(session: AsyncSession, access: MembershipInfo) -> HouseRulesInfo:
    """The house rules of a membership obtained from ``require_member``.

    The dependency's query already read the playgroup; only the editor's name is fetched.
    """
    group = access.playgroup
    return await _info(
        session, group.house_rules_text, group.house_rules_edited_by, group.house_rules_edited_at
    )


async def set_house_rules(
    session: AsyncSession, access: MembershipInfo, *, text: str
) -> HouseRulesInfo:
    """Save the house rules as the caller. Flushes; the caller commits.

    The text is stored exactly as sent (no trimming, escaping or newline conversion); the schema
    has already checked its length and characters. A whitespace-only text is stored as ``""``,
    which clears the rules. The editor and time are still recorded for a clear. The role is
    checked on the membership row re-read here with ``fresh=True`` (``populate_existing``), so the
    role is the committed one even if this session loaded the row earlier: a demotion between the
    dependency and this call cannot authorise the edit. Concurrent saves: the last one wins.
    """
    membership = await repository.get_active_membership(
        session, access.playgroup_id, access.player_id, fresh=True
    )
    if membership is None:
        raise NotFoundError
    if membership.role not in (ROLE_OWNER, ROLE_ADMIN):
        raise PermissionDeniedError
    group = membership.playgroup
    group.house_rules_text = text if text.strip() else ""
    group.house_rules_edited_by = membership.player_id
    group.house_rules_edited_at = datetime.now(UTC)
    await session.flush()
    return await _info(
        session, group.house_rules_text, group.house_rules_edited_by, group.house_rules_edited_at
    )
