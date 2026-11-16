"""``grant-site-admin`` and ``revoke-site-admin`` (S-2.1.11 AC1, AC2 and decisions 2 to 5).

The CLI runs in-process against the rolled-back ``db_session`` through an injected session
factory, as ``tests/test_cli_admin.py`` does. Neither command prompts for a password.
"""

import io
import logging
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.cli import run
from app.core.db import AppSession
from app.identity.models import User
from app.identity.services import (
    LastSiteAdminError,
    SiteAdminChange,
    UserNotFoundError,
    set_site_admin,
)
from tests.factories import UserFactory, create

API_ROOT = Path(__file__).resolve().parents[1]
REFUSAL = "at least one site admin is required"
LOG_RE = re.compile(
    r"^identity\.site_admin_changed username=(?P<username>\S+) user_id=(?P<user_id>\S+) "
    r"old=(?P<old>True|False) new=(?P<new>True|False) at=(?P<at>\S+)$"
)


@dataclass
class Outcome:
    code: int
    out: str
    err: str


@pytest.fixture
async def session(app_schema: AppSession) -> AppSession:
    """The rolled-back session with the schema released from the CLI's rollback scope."""
    await app_schema.commit()
    return app_schema


async def invoke(session: AppSession, argv: Sequence[str]) -> Outcome:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AppSession]:
        yield session

    def no_prompt(text: str) -> str:
        raise AssertionError(f"the command must not prompt, it asked {text!r}")

    out, err = io.StringIO(), io.StringIO()
    code = await run(argv, session_factory=factory, prompt=no_prompt, stdout=out, stderr=err)
    return Outcome(code, out.getvalue(), err.getvalue())


def grant(username: str) -> list[str]:
    return ["grant-site-admin", "--username", username]


def revoke(username: str) -> list[str]:
    return ["revoke-site-admin", "--username", username]


async def make_user(
    session: AppSession, username: str, *, admin: bool = False, active: bool = True
) -> User:
    user = await create(
        session, UserFactory, username=username, is_site_admin=admin, is_active=active
    )
    await session.commit()
    return user


async def state(session: AppSession) -> list[tuple[str, bool, bool]]:
    """Everything the commands may touch, for every user: the "nothing changes" check."""
    result = await session.execute(
        select(User.username, User.is_site_admin, User.is_active)
        .order_by(User.username)
        .execution_options(populate_existing=True)
    )
    return [(r[0], r[1], r[2]) for r in result.all()]


async def flags(session: AppSession, username: str) -> tuple[bool, bool]:
    """``(is_site_admin, is_active)`` read fresh from the database."""
    result = await session.execute(
        select(User.is_site_admin, User.is_active)
        .where(User.username == username)
        .execution_options(populate_existing=True)
    )
    row = result.one()
    return row[0], row[1]


def change_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "identity.site_admin_changed" in r.getMessage()]


def parse(record: logging.LogRecord) -> dict[str, str]:
    match = LOG_RE.match(record.getMessage())
    assert match, record.getMessage()
    return match.groupdict()


# ---------------------------------------------------------------- AC1: grant
async def test_grant_makes_an_existing_user_a_site_admin_and_exits_0(session: AppSession) -> None:
    await make_user(session, "ada")
    result = await invoke(session, grant("ada"))
    assert result.code == 0
    assert result.err == ""
    assert await flags(session, "ada") == (True, True)


async def test_grant_logs_one_record_with_old_new_and_a_utc_time(
    session: AppSession, caplog: pytest.LogCaptureFixture
) -> None:
    user = await make_user(session, "ada")
    caplog.set_level(logging.INFO)
    before = datetime.now(UTC)
    await invoke(session, grant("ada"))
    after = datetime.now(UTC)

    records = change_records(caplog)
    assert len(records) == 1
    assert records[0].name == "app.cli"
    assert records[0].levelno == logging.INFO
    fields = parse(records[0])
    assert fields["username"] == "ada"
    assert fields["user_id"] == str(user.id)
    assert fields["old"] == "False"
    assert fields["new"] == "True"
    at = datetime.fromisoformat(fields["at"])
    assert at.tzinfo is not None
    assert at.utcoffset() == timedelta(0)
    assert before <= at <= after


async def test_grant_leaves_other_users_untouched(session: AppSession) -> None:
    await make_user(session, "ada")
    await make_user(session, "bob")
    await make_user(session, "root", admin=True)
    await invoke(session, grant("ada"))
    assert await state(session) == [("ada", True, True), ("bob", False, True), ("root", True, True)]


async def test_grant_does_not_reactivate_an_inactive_user(session: AppSession) -> None:
    await make_user(session, "ada", active=False)
    result = await invoke(session, grant("ada"))
    assert result.code == 0
    assert await flags(session, "ada") == (True, False)


# ---------------------------------------------------------------- AC1: revoke
async def test_revoke_sets_the_flag_back_to_false_when_another_active_admin_exists(
    session: AppSession,
) -> None:
    await make_user(session, "ada", admin=True)
    await make_user(session, "root", admin=True)
    result = await invoke(session, revoke("ada"))
    assert result.code == 0
    assert result.err == ""
    assert await flags(session, "ada") == (False, True)
    assert await flags(session, "root") == (True, True)


async def test_revoke_logs_one_record_with_old_true_new_false_and_a_utc_time(
    session: AppSession, caplog: pytest.LogCaptureFixture
) -> None:
    user = await make_user(session, "ada", admin=True)
    await make_user(session, "root", admin=True)
    caplog.set_level(logging.INFO)
    before = datetime.now(UTC)
    await invoke(session, revoke("ada"))

    records = change_records(caplog)
    assert len(records) == 1
    assert records[0].name == "app.cli"
    fields = parse(records[0])
    assert fields["user_id"] == str(user.id)
    assert (fields["old"], fields["new"]) == ("True", "False")
    at = datetime.fromisoformat(fields["at"])
    assert at.utcoffset() == timedelta(0)
    assert before <= at <= datetime.now(UTC)


async def test_grant_then_revoke_round_trips_the_flag(session: AppSession) -> None:
    await make_user(session, "root", admin=True)
    await make_user(session, "ada")
    assert (await invoke(session, grant("ada"))).code == 0
    assert await flags(session, "ada") == (True, True)
    assert (await invoke(session, revoke("ada"))).code == 0
    assert await flags(session, "ada") == (False, True)


async def test_the_log_record_carries_no_password_hash_or_email(
    session: AppSession, caplog: pytest.LogCaptureFixture
) -> None:
    user = await make_user(session, "ada")
    caplog.set_level(logging.DEBUG)
    await invoke(session, grant("ada"))
    assert user.password_hash not in caplog.text
    assert user.email not in caplog.text


# ---------------------------------------------------------------- decision 3: no-ops
async def test_granting_to_an_existing_site_admin_exits_0_changes_nothing_and_logs_equal_values(
    session: AppSession, caplog: pytest.LogCaptureFixture
) -> None:
    await make_user(session, "ada", admin=True)
    await make_user(session, "bob")
    before = await state(session)
    caplog.set_level(logging.INFO)
    result = await invoke(session, grant("ada"))
    assert result.code == 0
    assert await state(session) == before
    records = change_records(caplog)
    assert len(records) == 1
    fields = parse(records[0])
    assert (fields["old"], fields["new"]) == ("True", "True")


async def test_revoking_from_a_non_admin_exits_0_changes_nothing_and_logs_equal_values(
    session: AppSession, caplog: pytest.LogCaptureFixture
) -> None:
    await make_user(session, "ada")
    await make_user(session, "root", admin=True)
    before = await state(session)
    caplog.set_level(logging.INFO)
    result = await invoke(session, revoke("ada"))
    assert result.code == 0
    assert await state(session) == before
    records = change_records(caplog)
    assert len(records) == 1
    fields = parse(records[0])
    assert (fields["old"], fields["new"]) == ("False", "False")


async def test_revoking_from_a_non_admin_succeeds_even_when_no_admin_exists(
    session: AppSession,
) -> None:
    await make_user(session, "ada")
    result = await invoke(session, revoke("ada"))
    assert result.code == 0
    assert await flags(session, "ada") == (False, True)


# ---------------------------------------------------------------- AC1: unknown username
@pytest.mark.parametrize("command", [grant, revoke], ids=["grant", "revoke"])
async def test_an_unknown_username_exits_non_zero_with_user_not_found_and_changes_nothing(
    session: AppSession,
    caplog: pytest.LogCaptureFixture,
    command: Callable[[str], list[str]],
) -> None:
    await make_user(session, "ada")
    await make_user(session, "root", admin=True)
    before = await state(session)
    caplog.set_level(logging.INFO)
    result = await invoke(session, command("nobody"))
    assert result.code == 1
    assert result.err == f"error: {UserNotFoundError().message}\n"
    assert result.out == ""
    assert await state(session) == before
    assert change_records(caplog) == []


@pytest.mark.parametrize("name", ["grant-site-admin", "revoke-site-admin"])
async def test_a_command_with_no_username_is_a_usage_error_exit_2(
    session: AppSession, name: str
) -> None:
    result = await invoke(session, [name])
    assert result.code == 2


# ---------------------------------------------------------------- decision 3: case-insensitive
@pytest.mark.parametrize("typed", ["ada", "ADA", "AdA"])
async def test_grant_finds_the_user_in_any_letter_case(session: AppSession, typed: str) -> None:
    await make_user(session, "Ada")
    result = await invoke(session, grant(typed))
    assert result.code == 0
    assert await flags(session, "Ada") == (True, True)


@pytest.mark.parametrize("typed", ["ada", "ADA", "AdA"])
async def test_revoke_finds_the_user_in_any_letter_case(session: AppSession, typed: str) -> None:
    await make_user(session, "Ada", admin=True)
    await make_user(session, "root", admin=True)
    result = await invoke(session, revoke(typed))
    assert result.code == 0
    assert await flags(session, "Ada") == (False, True)


# ---------------------------------------------------------------- AC2: last site admin
async def test_revoking_the_only_site_admin_is_refused_with_the_exact_message(
    session: AppSession, caplog: pytest.LogCaptureFixture
) -> None:
    await make_user(session, "root", admin=True)
    await make_user(session, "ada")
    before = await state(session)
    caplog.set_level(logging.INFO)
    result = await invoke(session, revoke("root"))
    assert result.code == 1
    assert result.err == f"error: {REFUSAL}\n"
    assert result.out == ""
    assert await state(session) == before
    assert await flags(session, "root") == (True, True)
    assert change_records(caplog) == []


async def test_the_refusal_does_not_depend_on_the_case_of_the_typed_username(
    session: AppSession,
) -> None:
    await make_user(session, "Root", admin=True)
    result = await invoke(session, revoke("ROOT"))
    assert result.code == 1
    assert REFUSAL in result.err
    assert await flags(session, "Root") == (True, True)


async def test_revoking_the_only_admin_commits_nothing(
    session: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_user(session, "root", admin=True)
    commits: list[int] = []
    real_commit = session.commit

    async def spy() -> None:
        commits.append(1)
        await real_commit()

    monkeypatch.setattr(session, "commit", spy)
    await invoke(session, revoke("root"))
    assert commits == []


# ---------------------------------------------------------------- decision 2: active admins count
async def test_with_an_inactive_and_an_active_admin_revoking_the_active_one_is_refused(
    session: AppSession,
) -> None:
    await make_user(session, "sleeper", admin=True, active=False)
    await make_user(session, "root", admin=True)
    before = await state(session)
    result = await invoke(session, revoke("root"))
    assert result.code == 1
    assert result.err == f"error: {REFUSAL}\n"
    assert await state(session) == before


async def test_with_an_inactive_and_an_active_admin_revoking_the_inactive_one_is_allowed(
    session: AppSession,
) -> None:
    await make_user(session, "sleeper", admin=True, active=False)
    await make_user(session, "root", admin=True)
    result = await invoke(session, revoke("sleeper"))
    assert result.code == 0
    assert await flags(session, "sleeper") == (False, False)
    assert await flags(session, "root") == (True, True)


async def test_revoking_the_only_active_admin_is_refused_even_with_inactive_admins_around(
    session: AppSession,
) -> None:
    await make_user(session, "sleeper1", admin=True, active=False)
    await make_user(session, "sleeper2", admin=True, active=False)
    await make_user(session, "root", admin=True)
    result = await invoke(session, revoke("root"))
    assert result.code == 1
    assert await flags(session, "root") == (True, True)


async def test_with_two_active_admins_the_first_revoke_works_and_the_second_is_refused(
    session: AppSession,
) -> None:
    await make_user(session, "one", admin=True)
    await make_user(session, "two", admin=True)
    assert (await invoke(session, revoke("one"))).code == 0
    second = await invoke(session, revoke("two"))
    assert second.code == 1
    assert second.err == f"error: {REFUSAL}\n"
    assert await flags(session, "two") == (True, True)


# ---------------------------------------------------------------- the service (decision 5)
async def test_set_site_admin_returns_old_new_user_id_username_and_time(
    session: AppSession,
) -> None:
    user = await make_user(session, "ada")
    before = datetime.now(UTC)
    change = await set_site_admin(session, username="ADA", value=True)
    assert isinstance(change, SiteAdminChange)
    assert change.old is False
    assert change.new is True
    assert change.user_id == user.id
    assert change.username.lower() == "ada"
    assert change.at.tzinfo is not None
    assert before <= change.at <= datetime.now(UTC)


async def test_set_site_admin_raises_user_not_found_for_an_unknown_name(
    session: AppSession,
) -> None:
    with pytest.raises(UserNotFoundError):
        await set_site_admin(session, username="nobody", value=True)


async def test_set_site_admin_raises_last_site_admin_error_with_its_code_status_and_message(
    session: AppSession,
) -> None:
    await make_user(session, "root", admin=True)
    with pytest.raises(LastSiteAdminError) as caught:
        await set_site_admin(session, username="root", value=False)
    assert caught.value.code == "last_site_admin"
    assert caught.value.status_code == 409
    assert caught.value.message == REFUSAL
    assert await flags(session, "root") == (True, True)


# ---------------------------------------------------------------- help
async def test_the_cli_help_lists_both_new_commands_and_the_old_ones(
    session: AppSession, capsys: pytest.CaptureFixture[str]
) -> None:
    result = await invoke(session, ["--help"])
    help_text = capsys.readouterr().out
    assert result.code == 0
    for name in ("grant-site-admin", "revoke-site-admin", "create-admin", "set-password"):
        assert name in help_text


@pytest.mark.parametrize("name", ["grant-site-admin", "revoke-site-admin"])
async def test_each_command_takes_a_username_and_no_password_option(
    session: AppSession, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    result = await invoke(session, [name, "--help"])
    help_text = capsys.readouterr().out
    assert result.code == 0
    assert "--username" in help_text
    assert "--password" not in help_text


def test_python_m_app_cli_help_lists_both_new_commands() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "app.cli", "--help"],
        cwd=API_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "grant-site-admin" in completed.stdout
    assert "revoke-site-admin" in completed.stdout
