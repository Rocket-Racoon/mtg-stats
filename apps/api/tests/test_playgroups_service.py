"""Services and database rules of playgroups and memberships (S-2.4.1)."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError

from app.core.auth import CurrentUser
from app.core.db import AppSession
from app.core.errors import InvalidCursorError, NotFoundError, PermissionDeniedError
from app.core.paging import PageParams
from app.playgroups import repository, services
from app.playgroups.models import Membership, Playgroup
from tests.playgroup_support import join, make_user
from tests.statement_counter import StatementCounter

pytestmark = pytest.mark.usefixtures("app_schema")


async def _create(session: AppSession, user_id: UUID, name: str = "Friday Night") -> UUID:
    return (await services.create_playgroup(session, user_id=user_id, name=name)).id


async def _add_group_in_savepoint(session: AppSession, group: Playgroup) -> None:
    async with session.begin_nested():
        repository.add_playgroup(session, group)
        await session.flush()


async def test_creating_a_playgroup_makes_the_creator_its_owner(app_schema: AppSession) -> None:
    user_id, player_id, _ = await make_user(app_schema)
    info = await services.create_playgroup(
        app_schema, user_id=user_id, name="Friday Night", description="Commander"
    )
    assert (info.name, info.description, info.role) == ("Friday Night", "Commander", "owner")
    rows = (await app_schema.scalars(select(Membership))).all()
    assert [(m.player_id, m.role, m.left_at) for m in rows] == [(player_id, "owner", None)]


async def test_there_is_no_cap_on_playgroups_per_user(app_schema: AppSession) -> None:
    user_id, _, _ = await make_user(app_schema)
    for i in range(12):
        await _create(app_schema, user_id, f"Group {i:02d}")
    page = await services.list_playgroups(
        app_schema, user_id=user_id, params=PageParams(cursor=None, limit=200)
    )
    assert len(page.items) == 12


async def test_a_user_without_a_player_cannot_create_a_playgroup(app_schema: AppSession) -> None:
    with pytest.raises(NotFoundError):
        await services.create_playgroup(app_schema, user_id=uuid4(), name="Nobody")


async def test_a_second_active_owner_is_rejected_by_the_database(app_schema: AppSession) -> None:
    owner_user, _, _ = await make_user(app_schema)
    _, other_player, _ = await make_user(app_schema)
    group_id = await _create(app_schema, owner_user)
    with pytest.raises(IntegrityError, match="uq_memberships_playgroup_id_owner"):
        async with app_schema.begin_nested():
            await join(app_schema, group_id, other_player, "owner")


async def test_a_former_owner_does_not_block_a_new_owner(app_schema: AppSession) -> None:
    _, former, _ = await make_user(app_schema)
    _, current, _ = await make_user(app_schema)
    group = Playgroup(name="Old Guard")
    repository.add_playgroup(app_schema, group)
    await app_schema.flush()
    await join(app_schema, group.id, former, "owner", left=True)
    await join(app_schema, group.id, current, "owner")


async def test_a_player_has_one_active_membership_per_playgroup(app_schema: AppSession) -> None:
    user_id, player_id, _ = await make_user(app_schema)
    group_id = await _create(app_schema, user_id)
    with pytest.raises(IntegrityError, match="uq_memberships_playgroup_id_player_id_active"):
        async with app_schema.begin_nested():
            await join(app_schema, group_id, player_id, "member")


@pytest.mark.parametrize("name", ["ab", "x" * 51], ids=["too-short", "too-long"])
async def test_the_database_rejects_a_name_outside_3_to_50(
    app_schema: AppSession, name: str
) -> None:
    with pytest.raises((IntegrityError, DataError), match=r"ck_playgroups_name_length|too long"):
        await _add_group_in_savepoint(app_schema, Playgroup(name=name))


async def test_the_database_rejects_an_unknown_role(app_schema: AppSession) -> None:
    user_id, _, _ = await make_user(app_schema)
    group_id = await _create(app_schema, user_id)
    _, player, _ = await make_user(app_schema)
    with pytest.raises(IntegrityError, match="ck_memberships_role_valid"):
        async with app_schema.begin_nested():
            await join(app_schema, group_id, player, "boss")


async def test_the_database_rejects_a_description_over_500(app_schema: AppSession) -> None:
    with pytest.raises(
        (IntegrityError, DataError), match=r"ck_playgroups_description_length|too long"
    ):
        await _add_group_in_savepoint(app_schema, Playgroup(name="Valid", description="d" * 501))


async def test_get_membership_ignores_former_members_and_strangers(
    app_schema: AppSession,
) -> None:
    owner_user, _, _ = await make_user(app_schema)
    gone_user, gone_player, _ = await make_user(app_schema)
    stranger_user, _, _ = await make_user(app_schema)
    group_id = await _create(app_schema, owner_user)
    await join(app_schema, group_id, gone_player, "member", left=True)
    for user_id in (gone_user, stranger_user, uuid4()):
        found = await services.get_membership(app_schema, playgroup_id=group_id, user_id=user_id)
        assert found is None
    owner = await services.get_membership(app_schema, playgroup_id=group_id, user_id=owner_user)
    assert owner is not None
    assert owner.is_owner
    assert owner.is_admin


async def _membership(session: AppSession, role: str) -> tuple[UUID, services.MembershipInfo]:
    """A playgroup and the membership of a user holding ``role`` in it."""
    owner_user, _, _ = await make_user(session)
    group_id = await _create(session, owner_user)
    user_id = owner_user
    if role != "owner":
        user_id, player_id, _ = await make_user(session)
        await join(session, group_id, player_id, role)
    found = await services.get_membership(session, playgroup_id=group_id, user_id=user_id)
    assert found is not None
    return group_id, found


async def test_require_member_returns_the_membership_and_hides_everything_else(
    app_schema: AppSession,
) -> None:
    owner_user, _, _ = await make_user(app_schema)
    stranger, _, _ = await make_user(app_schema)
    group_id = await _create(app_schema, owner_user)
    ok = await services.require_member(str(group_id), CurrentUser(owner_user, "t"), app_schema)
    assert ok.role == "owner"
    for bad in (str(group_id), str(uuid4()), "not-a-uuid"):
        with pytest.raises(NotFoundError):
            await services.require_member(bad, CurrentUser(stranger, "t"), app_schema)


@pytest.mark.parametrize(
    ("role", "admin_ok", "owner_ok"),
    [("owner", True, True), ("admin", True, False), ("member", False, False)],
)
async def test_role_dependencies_gate_by_role(
    app_schema: AppSession, role: str, admin_ok: bool, owner_ok: bool
) -> None:
    _, info = await _membership(app_schema, role)
    for check, allowed in (
        (services.require_playgroup_admin, admin_ok),
        (services.require_playgroup_owner, owner_ok),
    ):
        if allowed:
            assert await check(info) is info
        else:
            with pytest.raises(PermissionDeniedError):
                await check(info)


async def test_admins_and_the_owner_can_update_but_members_cannot(
    app_schema: AppSession,
) -> None:
    group_id, owner = await _membership(app_schema, "owner")
    member_user, member_player, _ = await make_user(app_schema)
    await join(app_schema, group_id, member_player, "member")
    member = await services.get_membership(app_schema, playgroup_id=group_id, user_id=member_user)
    assert member is not None
    with pytest.raises(PermissionDeniedError):
        await services.update_playgroup(app_schema, member, name="Hijacked")
    renamed = await services.update_playgroup(app_schema, owner, name="Renamed", description="New")
    assert (renamed.name, renamed.description) == ("Renamed", "New")
    only_name = await services.update_playgroup(app_schema, owner, name="Again")
    assert (only_name.name, only_name.description) == ("Again", "New")
    member = await services.get_membership(app_schema, playgroup_id=group_id, user_id=member_user)
    assert member is not None
    got = await services.get_playgroup(app_schema, member)
    assert (got.name, got.role) == ("Again", "member")


async def test_a_demotion_after_the_dependency_ran_blocks_the_update(
    app_schema: AppSession,
) -> None:
    group_id, _ = await _membership(app_schema, "owner")
    admin_user, admin_player, _ = await make_user(app_schema)
    await join(app_schema, group_id, admin_player, "admin")
    stale = await services.get_membership(app_schema, playgroup_id=group_id, user_id=admin_user)
    assert stale is not None
    assert stale.is_admin
    await app_schema.execute(
        text("update memberships set role = 'member' where player_id = :p"), {"p": admin_player}
    )
    with pytest.raises(PermissionDeniedError):
        await services.update_playgroup(app_schema, stale, name="Stale Edit")


async def test_getting_a_playgroup_costs_no_statement_after_the_dependency(
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    _, owner = await _membership(app_schema, "owner")
    with count_statements() as counter:
        await services.get_playgroup(app_schema, owner)
    assert counter.count == 0


async def test_updating_after_leaving_is_a_404(app_schema: AppSession) -> None:
    _, owner = await _membership(app_schema, "owner")
    await app_schema.execute(text("update memberships set left_at = now()"))
    with pytest.raises(NotFoundError):
        await services.update_playgroup(app_schema, owner, name="Too Late")


async def test_list_pages_by_cursor_with_roles_and_excludes_former_memberships(
    app_schema: AppSession,
) -> None:
    user_id, player_id, _ = await make_user(app_schema)
    other_user, _, _ = await make_user(app_schema)
    mine = [await _create(app_schema, user_id, f"Mine {i}") for i in range(3)]
    theirs = await _create(app_schema, other_user, "Theirs")
    await join(app_schema, theirs, player_id, "admin")
    left = await _create(app_schema, other_user, "Left")
    await join(app_schema, left, player_id, "member", left=True)

    first = await services.list_playgroups(
        app_schema, user_id=user_id, params=PageParams(cursor=None, limit=2)
    )
    assert first.next_cursor is not None
    second = await services.list_playgroups(
        app_schema, user_id=user_id, params=PageParams(cursor=first.next_cursor, limit=2)
    )
    assert second.next_cursor is None
    seen = {i.id: i.role for i in [*first.items, *second.items]}
    assert seen == {**dict.fromkeys(mine, "owner"), theirs: "admin"}


async def test_list_rejects_a_malformed_cursor(app_schema: AppSession) -> None:
    user_id, _, _ = await make_user(app_schema)
    with pytest.raises(InvalidCursorError):
        await services.list_playgroups(
            app_schema, user_id=user_id, params=PageParams(cursor="bogus", limit=5)
        )


async def test_listing_uses_a_constant_number_of_statements(
    app_schema: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    user_id, _, _ = await make_user(app_schema)
    await _create(app_schema, user_id, "One")
    params = PageParams(cursor=None, limit=50)
    with count_statements() as small:
        await services.list_playgroups(app_schema, user_id=user_id, params=params)
    for i in range(5):
        await _create(app_schema, user_id, f"More {i}")
    with count_statements() as large:
        await services.list_playgroups(app_schema, user_id=user_id, params=params)
    assert small.count == large.count


# --- house rules (S-2.5.4): the service-side branches the HTTP tests cannot reach ---------------


async def test_saving_house_rules_after_leaving_is_a_404(app_schema: AppSession) -> None:
    _, owner = await _membership(app_schema, "owner")
    await app_schema.execute(text("update memberships set left_at = now()"))
    with pytest.raises(NotFoundError):
        await services.set_house_rules(app_schema, owner, text="Too late")


async def test_a_demotion_after_the_dependency_ran_blocks_saving_house_rules(
    app_schema: AppSession,
) -> None:
    group_id, _ = await _membership(app_schema, "owner")
    admin_user, admin_player, _ = await make_user(app_schema)
    await join(app_schema, group_id, admin_player, "admin")
    stale = await services.get_membership(app_schema, playgroup_id=group_id, user_id=admin_user)
    assert stale is not None
    await app_schema.execute(
        text("update memberships set role = 'member' where player_id = :p"), {"p": admin_player}
    )
    with pytest.raises(PermissionDeniedError):
        await services.set_house_rules(app_schema, stale, text="Stale edit")


async def test_house_rules_role_is_re_read_even_if_this_session_loaded_it_earlier(
    app_schema: AppSession,
) -> None:
    group_id, _ = await _membership(app_schema, "owner")
    member_user, member_player, _ = await make_user(app_schema)
    await join(app_schema, group_id, member_player)
    access = await services.get_membership(app_schema, playgroup_id=group_id, user_id=member_user)
    assert access is not None
    # A strong reference keeps the stale object in the identity map, so a plain re-read would
    # return it with the old role instead of the committed one.
    held = await repository.get_active_membership(app_schema, group_id, member_player)
    assert held is not None
    assert held.role == "member"
    with pytest.raises(PermissionDeniedError):
        await services.set_house_rules(app_schema, access, text="No")
    await app_schema.execute(
        text("update memberships set role = 'admin' where player_id = :p"), {"p": member_player}
    )
    saved = await services.set_house_rules(app_schema, access, text="Yes")
    assert saved.text == "Yes"
    assert saved.edited_by_player_id == member_player


async def test_house_rules_of_a_never_edited_playgroup_have_no_editor(
    app_schema: AppSession,
) -> None:
    _, owner = await _membership(app_schema, "owner")
    info = await services.get_house_rules(app_schema, owner)
    assert (info.text, info.edited_by_player_id, info.edited_by_display_name, info.edited_at) == (
        "",
        None,
        None,
        None,
    )
