"""Database and service behavior for the player identity (S-2.2.1 AC1-AC4)."""

import dataclasses
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError

from app.core.db import AppSession
from app.core.ids import uuid7
from app.identity import services
from app.identity.models import Player, User
from app.identity.services import (
    AVATAR_COLORS,
    InvalidAvatarColorError,
    InvalidDisplayNameError,
    PlayerInfo,
    UsernameTakenError,
    create_guest_player,
    create_user_with_player,
    default_avatar_color,
    get_player,
    get_player_for_user,
    get_players,
)
from tests.factories import GuestPlayerFactory, PlayerFactory, UserFactory, create
from tests.statement_counter import StatementCounter

PASSWORD = "correct horse battery"
UNIQUE_VIOLATION = "23505"
CHECK_VIOLATION = "23514"

pytestmark = pytest.mark.usefixtures("app_schema")


def _sqlstate(exc: IntegrityError) -> str | None:
    state: str | None = getattr(exc.orig, "sqlstate", None)
    return state


async def _rejected(session: AppSession, build: Callable[[], Player | User]) -> IntegrityError:
    """Flush ``build()`` inside a savepoint and return the IntegrityError (the session lives on)."""
    with pytest.raises(IntegrityError) as caught:  # noqa: PT012
        async with session.begin_nested():
            session.add(build())
            await session.flush()
    return caught.value


async def _count(session: AppSession, model: type[Player] | type[User]) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _member(session: AppSession, username: str = "ada", name: str = "Ada") -> PlayerInfo:
    return await create_user_with_player(
        session, username=username, password=PASSWORD, display_name=name
    )


# ---------------------------------------------------------------- AC1: one player per user


async def test_registered_user_has_exactly_one_player(db_session: AppSession) -> None:
    info = await _member(db_session)
    rows = (
        (await db_session.execute(select(Player).where(Player.user_id == info.user_id)))
        .scalars()
        .all()
    )
    assert [r.id for r in rows] == [info.id]
    assert info.is_guest is False
    assert info.user_id is not None


async def test_database_rejects_a_second_player_for_the_same_user(db_session: AppSession) -> None:
    user = await create(db_session, UserFactory)
    await create(db_session, PlayerFactory, user_id=user.id)
    error = await _rejected(db_session, lambda: PlayerFactory.build(user_id=user.id))
    assert _sqlstate(error) == UNIQUE_VIOLATION


async def test_get_player_for_user_returns_that_users_player(db_session: AppSession) -> None:
    info = await _member(db_session)
    assert info.user_id is not None
    assert await get_player_for_user(db_session, info.user_id) == info


async def test_get_player_for_user_is_none_for_an_unknown_user(db_session: AppSession) -> None:
    assert await get_player_for_user(db_session, uuid7()) is None


async def test_usernames_differing_only_in_case_are_rejected(db_session: AppSession) -> None:
    await create(db_session, UserFactory, username="Alice")
    error = await _rejected(db_session, lambda: UserFactory.build(username="aLICE"))
    assert _sqlstate(error) == UNIQUE_VIOLATION


async def test_service_rejects_usernames_differing_only_in_case(db_session: AppSession) -> None:
    await _member(db_session, username="Alice")
    with pytest.raises(UsernameTakenError):
        await _member(db_session, username="alice", name="B")
    assert await _count(db_session, User) == 1
    assert await _count(db_session, Player) == 1


async def test_user_defaults_match_the_spec(db_session: AppSession) -> None:
    await _member(db_session)
    user = (await db_session.execute(select(User))).scalar_one()
    assert user.email == ""
    assert user.is_site_admin is False
    assert user.is_active is True
    assert user.password_hash.startswith("$argon2id$")
    assert user.created_at.tzinfo is not None
    assert user.password_changed_at.tzinfo is not None
    assert isinstance(user.id, UUID)
    assert user.id.version == 7


async def test_create_user_with_player_passes_email_and_admin_flag(db_session: AppSession) -> None:
    await create_user_with_player(
        db_session,
        username="root",
        password=PASSWORD,
        display_name="Root",
        email="r@example.com",
        is_site_admin=True,
    )
    user = (await db_session.execute(select(User))).scalar_one()
    assert user.email == "r@example.com"
    assert user.is_site_admin is True


async def test_create_user_with_player_flushes_so_the_rows_are_queryable(
    db_session: AppSession,
) -> None:
    info = await _member(db_session)
    assert len(db_session.new) == 0  # flushed, nothing pending
    assert await _count(db_session, User) == 1
    assert await get_player(db_session, info.id) == info


async def test_user_factory_builds_a_persistable_row(db_session: AppSession) -> None:
    user = await create(db_session, UserFactory)
    assert await _count(db_session, User) == 1
    assert user.username


# ---------------------------------------------------------------- AC2: guests


async def test_guest_is_stored_with_null_user_and_guest_flag(db_session: AppSession) -> None:
    info = await create_guest_player(db_session, display_name="Visitor")
    assert info.user_id is None
    assert info.is_guest is True
    row = (await db_session.execute(select(Player).where(Player.id == info.id))).scalar_one()
    assert row.user_id is None
    assert row.is_guest is True


async def test_database_rejects_guest_with_user_id(db_session: AppSession) -> None:
    user = await create(db_session, UserFactory)
    error = await _rejected(
        db_session, lambda: GuestPlayerFactory.build(user_id=user.id, is_guest=True)
    )
    assert _sqlstate(error) == CHECK_VIOLATION


async def test_non_guest_player_without_a_user_is_accepted(db_session: AppSession) -> None:
    """Pinned: the check only forbids guest+user; a non-guest with NULL user_id is allowed."""
    player = await create(db_session, PlayerFactory, user_id=None, is_guest=False)
    assert player.user_id is None
    assert await _count(db_session, Player) == 1


async def test_multiple_guests_may_coexist_with_null_user_id(db_session: AppSession) -> None:
    await create(db_session, GuestPlayerFactory)
    await create(db_session, GuestPlayerFactory)
    assert await _count(db_session, Player) == 2


async def test_guest_player_factory_builds_a_guest() -> None:
    guest = GuestPlayerFactory.build()
    assert guest.user_id is None
    assert guest.is_guest is True


async def test_player_factory_links_to_a_persisted_user(db_session: AppSession) -> None:
    user = await create(db_session, UserFactory)
    player = await create(db_session, PlayerFactory, user_id=user.id)
    assert player.user_id == user.id
    assert player.is_guest is False


# ---------------------------------------------------------------- AC3: display name, avatar


async def test_service_stores_the_trimmed_display_name(db_session: AppSession) -> None:
    guest = await create_guest_player(db_session, display_name="\t  Ada  \n")
    assert guest.display_name == "Ada"
    member = await _member(db_session, name="  Bo ")
    assert member.display_name == "Bo"


@pytest.mark.parametrize("name", ["a", "x" * 32, "é" * 32, "😀" * 32])
async def test_service_accepts_display_name_boundaries(db_session: AppSession, name: str) -> None:
    info = await create_guest_player(db_session, display_name=name)
    assert info.display_name == name


@pytest.mark.parametrize("name", ["", "   ", "\t", "x" * 33, "é" * 33])
async def test_service_rejects_invalid_display_names(db_session: AppSession, name: str) -> None:
    with pytest.raises(InvalidDisplayNameError):
        await create_guest_player(db_session, display_name=name)
    with pytest.raises(InvalidDisplayNameError):
        await _member(db_session, username="nobody", name=name)
    assert await _count(db_session, Player) == 0
    assert await _count(db_session, User) == 0


@pytest.mark.parametrize("name", [" x", "x ", "\tx", "x\n", "   ", ""])
async def test_database_rejects_untrimmed_or_empty_display_names(
    db_session: AppSession, name: str
) -> None:
    error = await _rejected(db_session, lambda: GuestPlayerFactory.build(display_name=name))
    assert _sqlstate(error) == CHECK_VIOLATION


async def test_database_rejects_a_33_character_display_name(db_session: AppSession) -> None:
    """The column is varchar(32), so Postgres refuses it (string-data-right-truncation)."""
    with pytest.raises(DataError):  # noqa: PT012
        async with db_session.begin_nested():
            db_session.add(GuestPlayerFactory.build(display_name="x" * 33))
            await db_session.flush()


@pytest.mark.parametrize("name", ["x", "x" * 32, "é" * 32])
async def test_database_accepts_display_name_boundaries(db_session: AppSession, name: str) -> None:
    await create(db_session, GuestPlayerFactory, display_name=name)


async def test_omitted_avatar_color_is_derived_from_the_player_id(db_session: AppSession) -> None:
    guest = await create_guest_player(db_session, display_name="G")
    assert guest.avatar_color == default_avatar_color(guest.id)
    assert guest.avatar_color in AVATAR_COLORS
    member = await _member(db_session)
    assert member.avatar_color == default_avatar_color(member.id)


@pytest.mark.parametrize("color", AVATAR_COLORS)
async def test_explicit_valid_avatar_color_is_kept(db_session: AppSession, color: str) -> None:
    info = await create_guest_player(db_session, display_name="G", avatar_color=color)
    assert info.avatar_color == color
    stored = await get_player(db_session, info.id)
    assert stored is not None
    assert stored.avatar_color == color


@pytest.mark.parametrize("color", ["magenta", "RED", "", "#ff0000"])
async def test_service_rejects_unknown_avatar_colors(db_session: AppSession, color: str) -> None:
    with pytest.raises(InvalidAvatarColorError):
        await create_guest_player(db_session, display_name="G", avatar_color=color)
    with pytest.raises(InvalidAvatarColorError):
        await create_user_with_player(
            db_session, username="uuu", password=PASSWORD, display_name="G", avatar_color=color
        )
    assert await _count(db_session, Player) == 0


async def test_database_rejects_an_unknown_avatar_color(db_session: AppSession) -> None:
    error = await _rejected(db_session, lambda: GuestPlayerFactory.build(avatar_color="magenta"))
    assert _sqlstate(error) == CHECK_VIOLATION


async def test_stored_player_has_a_timezone_aware_created_at(db_session: AppSession) -> None:
    info = await create_guest_player(db_session, display_name="G")
    assert info.created_at.tzinfo is not None
    assert abs((datetime.now(UTC) - info.created_at).total_seconds()) < 60


# ---------------------------------------------------------------- AC4: public interface


def test_services_public_interface_exports_the_three_readers() -> None:
    for name in ("get_player", "get_players", "get_player_for_user"):
        assert name in services.__all__
        assert callable(getattr(services, name))


async def test_get_player_returns_an_immutable_player_info_not_an_orm_object(
    db_session: AppSession,
) -> None:
    created = await create_guest_player(db_session, display_name="G")
    info = await get_player(db_session, created.id)
    assert isinstance(info, PlayerInfo)
    assert not isinstance(info, Player)
    assert not hasattr(info, "_sa_instance_state")
    assert info == created
    with pytest.raises((AttributeError, TypeError, ValueError)):
        info.display_name = "other"  # type: ignore[misc]


def test_player_info_has_the_contracted_fields() -> None:
    assert dataclasses.is_dataclass(PlayerInfo)
    assert {f.name for f in dataclasses.fields(PlayerInfo)} == {
        "id",
        "user_id",
        "is_guest",
        "display_name",
        "avatar_color",
        "home_playgroup_id",
        "merged_into_id",
        "created_at",
        "created_by_id",
    }


async def test_get_player_is_none_for_an_unknown_id(db_session: AppSession) -> None:
    assert await get_player(db_session, uuid7()) is None


async def test_get_players_is_keyed_by_id_and_omits_missing_ids(db_session: AppSession) -> None:
    a = await create_guest_player(db_session, display_name="A")
    b = await create_guest_player(db_session, display_name="B")
    missing = uuid7()
    result = await get_players(db_session, [a.id, b.id, missing])
    assert result == {a.id: a, b.id: b}
    assert missing not in result


async def test_get_players_runs_one_statement(
    db_session: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    infos = [await create_guest_player(db_session, display_name=f"P{i}") for i in range(5)]
    with count_statements() as counter:
        result = await get_players(db_session, [i.id for i in infos] + [uuid7()])
    assert len(result) == 5
    assert counter.count == 1


async def test_get_players_with_no_ids_runs_no_statement(
    db_session: AppSession,
    count_statements: Callable[[], AbstractContextManager[StatementCounter]],
) -> None:
    await create_guest_player(db_session, display_name="warm")
    with count_statements() as counter:
        assert await get_players(db_session, []) == {}
    assert counter.count == 0


async def test_merged_into_id_round_trips_and_home_playgroup_has_no_foreign_key(
    db_session: AppSession,
) -> None:
    keep = await create_guest_player(db_session, display_name="Keep")
    merged = await create(db_session, GuestPlayerFactory, merged_into_id=keep.id)
    home = uuid7()  # no FK yet: any uuid is accepted
    other = await create(db_session, GuestPlayerFactory, home_playgroup_id=home)
    merged_info = await get_player(db_session, merged.id)
    other_info = await get_player(db_session, other.id)
    assert merged_info is not None
    assert merged_info.merged_into_id == keep.id
    assert other_info is not None
    assert other_info.home_playgroup_id == home
