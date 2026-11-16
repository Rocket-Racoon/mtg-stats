"""Review fixes for S-2.1.1: narrow race mapping and email normalization (identity services)."""

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import app.identity.services.players as players_module
from app.core.db import AppSession
from app.core.errors import DomainError
from app.identity.models import Player, User
from app.identity.services import (
    InvalidEmailError,
    UsernameTakenError,
    create_user_with_player,
    normalize_email,
)

PASSWORD = "correct horse battery staple"
EMAIL_LIMIT = 320

pytestmark = pytest.mark.usefixtures("app_schema")


async def _count(session: AppSession, model: type[User] | type[Player]) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _create(session: AppSession, username: str = "ada", **kwargs: str) -> None:
    await create_user_with_player(
        session, username=username, password=PASSWORD, display_name="Ada", **kwargs
    )


# ---------------------------------------------------------------- narrow IntegrityError mapping
async def test_a_not_null_violation_is_not_reported_as_username_taken(
    db_session: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(players_module, "hash_password", lambda password: None)
    with pytest.raises(IntegrityError) as caught:
        await _create(db_session)
    assert getattr(caught.value.orig, "sqlstate", None) == "23502"


@pytest.mark.parametrize(
    ("sqlstate", "constraint"),
    [("23505", "uq_something_else"), ("23503", "uq_users_username_lower"), ("23505", None)],
    ids=["other_unique", "other_class_same_name", "unique_without_name"],
)
async def test_other_integrity_errors_are_re_raised_unchanged(
    db_session: AppSession,
    monkeypatch: pytest.MonkeyPatch,
    sqlstate: str,
    constraint: str | None,
) -> None:
    orig = SimpleNamespace(
        sqlstate=sqlstate,
        pgcode=sqlstate,
        diag=SimpleNamespace(constraint_name=constraint),
        constraint_name=constraint,
    )
    original = IntegrityError("INSERT INTO users", {}, orig)  # type: ignore[arg-type]

    async def boom(*args: object, **kwargs: object) -> None:
        raise original

    monkeypatch.setattr(db_session, "flush", boom)
    with pytest.raises(IntegrityError) as caught:
        await _create(db_session)
    assert caught.value is original


async def test_the_username_unique_violation_is_still_reported_as_username_taken(
    db_session: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _create(db_session, "ada")

    async def none_found(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr("app.identity.repository.get_user_by_username", none_found)
    with pytest.raises(UsernameTakenError):
        await _create(db_session, "ADA")
    assert await _count(db_session, User) == 1
    assert await _count(db_session, Player) == 1


# ---------------------------------------------------------------- normalize_email
def test_invalid_email_error_has_the_contracted_code_status_and_message() -> None:
    error = InvalidEmailError()
    assert error.code == "invalid_email"
    assert error.status_code == 422
    assert error.message == "Email must be at most 320 characters."
    assert isinstance(error, DomainError)
    assert isinstance(error, ValueError)


def test_normalize_email_keeps_an_empty_string_empty() -> None:
    assert normalize_email("") == ""


def test_normalize_email_trims_surrounding_whitespace() -> None:
    assert normalize_email("  \t h@example.com \n") == "h@example.com"


def test_normalize_email_of_only_whitespace_is_empty() -> None:
    assert normalize_email("   ") == ""


def test_normalize_email_accepts_exactly_320_characters() -> None:
    email = "a" * (EMAIL_LIMIT - len("@example.com")) + "@example.com"
    assert len(email) == EMAIL_LIMIT
    assert normalize_email(email) == email


def test_normalize_email_rejects_321_characters() -> None:
    with pytest.raises(InvalidEmailError):
        normalize_email("a" * (EMAIL_LIMIT + 1))


def test_normalize_email_counts_code_points_not_bytes() -> None:
    assert normalize_email(chr(0xE9) * EMAIL_LIMIT) == chr(0xE9) * EMAIL_LIMIT
    with pytest.raises(InvalidEmailError):
        normalize_email(chr(0xE9) * (EMAIL_LIMIT + 1))


def test_normalize_email_measures_the_trimmed_value() -> None:
    padded = "  " + "a" * EMAIL_LIMIT + "  "
    assert normalize_email(padded) == "a" * EMAIL_LIMIT


@pytest.mark.parametrize("bad", ["a\x00b@example.com", "a\nb@example.com", "a\x7fb@example.com"])
def test_normalize_email_rejects_control_characters(bad: str) -> None:
    with pytest.raises(InvalidEmailError):
        normalize_email(bad)


def test_normalize_email_rejects_a_lone_surrogate() -> None:
    with pytest.raises(InvalidEmailError):
        normalize_email("a" + chr(0xD800) + "@example.com")


# ---------------------------------------------------------------- through the service
async def test_service_stores_a_320_character_email(db_session: AppSession) -> None:
    email = "a" * (EMAIL_LIMIT - len("@example.com")) + "@example.com"
    await _create(db_session, email=email)
    assert (await db_session.execute(select(User.email))).scalar_one() == email


async def test_service_rejects_a_321_character_email_before_any_write(
    db_session: AppSession,
) -> None:
    with pytest.raises(InvalidEmailError):
        await _create(db_session, email="a" * (EMAIL_LIMIT + 1))
    assert await _count(db_session, User) == 0
    assert await _count(db_session, Player) == 0


async def test_service_rejects_an_email_with_a_control_character(db_session: AppSession) -> None:
    with pytest.raises(InvalidEmailError):
        await _create(db_session, email="a\x00b@example.com")
    assert await _count(db_session, User) == 0


async def test_service_trims_the_email_and_allows_an_empty_one(db_session: AppSession) -> None:
    await _create(db_session, "ada", email="  h@example.com ")
    await _create(db_session, "bob", email="")
    emails = (await db_session.execute(select(User.email).order_by(User.username))).scalars().all()
    assert list(emails) == ["h@example.com", ""]
