"""Behavior of ``python -m app.cli create-admin`` and ``set-password`` (S-2.1.1 AC1-AC4).

The CLI runs against the rolled-back ``db_session`` through an injected session factory and a fake
hidden prompt. Refresh-token revocation on ``set-password`` is deferred to S-2.1.5 (decision on
#60), so it is not tested here.
"""

import dataclasses
import getpass
import io
import json
import logging
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

import app.cli
from app.cli import run
from app.core.db import AppSession
from app.core.logging import JsonFormatter
from app.identity.models import Player, User
from app.identity.services import (
    InvalidDisplayNameError,
    InvalidPasswordError,
    InvalidUsernameError,
    UsernameTakenError,
    UserNotFoundError,
    create_user_with_player,
    hash_password,
    verify_password,
)
from tests.factories import UserFactory, create

PASSWORD = "correct horse battery staple"
OTHER_PASSWORD = "a completely different one"  # secrets-check: allow
API_ROOT = Path(__file__).resolve().parents[1]
E_ACUTE = chr(0xE9)
CYRILLIC_A = chr(0x430)


class FakePrompt:
    """Stands in for ``getpass.getpass``: records what it was asked, answers from a queue."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def __call__(self, text: str) -> str:
        self.asked.append(text)
        return self.answers.pop(0)


@dataclasses.dataclass
class Outcome:
    code: int
    out: str
    err: str
    prompt: FakePrompt


@pytest.fixture
async def session(app_schema: AppSession) -> AppSession:
    """The rolled-back session with the schema committed to its outer savepoint.

    The CLI rolls back on errors; releasing the savepoint first keeps the freshly created tables
    out of that rollback, so a rollback only ever undoes what the command itself did.
    """
    await app_schema.commit()
    return app_schema


async def invoke(session: AppSession, argv: Sequence[str], *answers: str) -> Outcome:
    """Run the CLI on ``session`` with the given hidden-prompt answers."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[AppSession]:
        yield session

    prompt = FakePrompt(*answers)
    out, err = io.StringIO(), io.StringIO()
    code = await run(argv, session_factory=factory, prompt=prompt, stdout=out, stderr=err)
    return Outcome(code, out.getvalue(), err.getvalue(), prompt)


def create_admin(username: str = "hector", name: str = "Hector", *extra: str) -> list[str]:
    return ["create-admin", "--username", username, "--display-name", name, *extra]


async def count(session: AppSession, model: type[User] | type[Player]) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def user_named(session: AppSession, username: str) -> User:
    result = await session.execute(
        select(User)
        .where(func.lower(User.username) == username.lower())
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def seed_member(
    session: AppSession, username: str = "ada", password: str = PASSWORD, name: str = "Ada"
) -> None:
    await create_user_with_player(session, username=username, password=password, display_name=name)
    await session.commit()


async def snapshot(session: AppSession) -> list[tuple[str, str, bool, str]]:
    """Everything observable about users and players, to prove "no rows change"."""
    users = (await session.execute(select(User).order_by(User.username))).scalars().all()
    players = {p.user_id: p for p in (await session.execute(select(Player))).scalars().all()}
    return [
        (u.username, u.password_hash, u.is_site_admin, players[u.id].display_name)
        for u in users
        if u.id in players
    ]


# ---------------------------------------------------------------- AC1: create-admin succeeds
async def test_create_admin_on_an_empty_users_table_creates_user_and_linked_player(
    session: AppSession,
) -> None:
    assert await count(session, User) == 0
    result = await invoke(session, create_admin(), PASSWORD, PASSWORD)
    assert result.code == 0
    assert await count(session, User) == 1
    assert await count(session, Player) == 1
    user = await user_named(session, "hector")
    player = (await session.execute(select(Player))).scalar_one()
    assert user.is_site_admin is True
    assert user.is_active is True
    assert player.user_id == user.id
    assert player.is_guest is False
    assert player.display_name == "Hector"


async def test_create_admin_prints_the_success_line_and_nothing_on_stderr(
    session: AppSession,
) -> None:
    result = await invoke(session, create_admin("hector"), PASSWORD, PASSWORD)
    assert result.out == "Created site admin hector.\n"
    assert result.err == ""


async def test_create_admin_stores_the_email_when_given(session: AppSession) -> None:
    result = await invoke(
        session, create_admin("hector", "Hector", "--email", "h@example.com"), PASSWORD, PASSWORD
    )
    assert result.code == 0
    assert (await user_named(session, "hector")).email == "h@example.com"


async def test_create_admin_leaves_the_email_empty_when_omitted(session: AppSession) -> None:
    await invoke(session, create_admin(), PASSWORD, PASSWORD)
    assert (await user_named(session, "hector")).email == ""


async def test_create_admin_trims_the_display_name(session: AppSession) -> None:
    await invoke(session, create_admin("hector", "  Hector  "), PASSWORD, PASSWORD)
    player = (await session.execute(select(Player))).scalar_one()
    assert player.display_name == "Hector"


async def test_create_admin_stores_an_argon2id_hash_that_verifies_with_the_typed_password(
    session: AppSession,
) -> None:
    await invoke(session, create_admin(), PASSWORD, PASSWORD)
    user = await user_named(session, "hector")
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash
    assert verify_password(user.password_hash, PASSWORD) is True
    assert verify_password(user.password_hash, OTHER_PASSWORD) is False
    assert user.password_changed_at.tzinfo is not None


async def test_create_admin_asks_for_the_password_twice_through_the_injected_prompt(
    session: AppSession,
) -> None:
    result = await invoke(session, create_admin(), PASSWORD, PASSWORD)
    assert result.prompt.asked == ["Password: ", "Repeat password: "]


async def test_create_admin_uses_getpass_as_the_default_hidden_prompt(
    session: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def fake_getpass(text: str = "Password: ") -> str:
        asked.append(text)
        return PASSWORD

    monkeypatch.setattr(getpass, "getpass", fake_getpass)

    @asynccontextmanager
    async def factory() -> AsyncIterator[AppSession]:
        yield session

    code = await run(
        create_admin(), session_factory=factory, stdout=io.StringIO(), stderr=io.StringIO()
    )
    assert code == 0
    assert asked == ["Password: ", "Repeat password: "]
    assert verify_password((await user_named(session, "hector")).password_hash, PASSWORD)


async def test_create_admin_creates_user_and_player_in_one_transaction(
    session: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    commits: list[int] = []
    real_commit = session.commit

    async def counting_commit() -> None:
        commits.append(1)
        await real_commit()

    monkeypatch.setattr(session, "commit", counting_commit)
    result = await invoke(session, create_admin(), PASSWORD, PASSWORD)
    assert result.code == 0
    assert len(commits) == 1


async def test_create_admin_does_not_make_other_users_admins(session: AppSession) -> None:
    await seed_member(session, "ada")
    await invoke(session, create_admin("hector"), PASSWORD, PASSWORD)
    assert (await user_named(session, "ada")).is_site_admin is False
    assert (await user_named(session, "hector")).is_site_admin is True


@pytest.mark.parametrize("username", ["abc", "a" * 24, "A_b-c.d"])
async def test_create_admin_accepts_usernames_at_the_length_and_charset_boundaries(
    session: AppSession, username: str
) -> None:
    result = await invoke(session, create_admin(username), PASSWORD, PASSWORD)
    assert result.code == 0
    assert (await user_named(session, username)).username == username


@pytest.mark.parametrize("length", [12, 128])
async def test_create_admin_accepts_passwords_at_the_length_boundaries(
    session: AppSession, length: int
) -> None:
    password = "p" * length
    result = await invoke(session, create_admin(), password, password)
    assert result.code == 0
    assert verify_password((await user_named(session, "hector")).password_hash, password)


# ---------------------------------------------------------------- AC2: duplicate username
@pytest.mark.parametrize("duplicate", ["ada", "ADA", "Ada", "aDa"])
async def test_create_admin_with_an_existing_username_in_any_case_exits_non_zero(
    session: AppSession, duplicate: str
) -> None:
    await seed_member(session, "ada")
    result = await invoke(session, create_admin(duplicate, "Other"), PASSWORD, PASSWORD)
    assert result.code != 0
    assert "username already taken" in result.err.lower()
    assert result.out == ""


async def test_duplicate_username_error_is_the_api_message(session: AppSession) -> None:
    await seed_member(session, "ada")
    result = await invoke(session, create_admin("ADA"), PASSWORD, PASSWORD)
    assert result.code == 1
    assert result.err == "error: Username already taken.\n"
    assert result.err == f"error: {UsernameTakenError().message}\n"


async def test_duplicate_username_changes_no_rows(session: AppSession) -> None:
    await seed_member(session, "ada")
    before = await snapshot(session)
    await invoke(
        session, create_admin("ADA", "Impostor", "--email", "x@example.com"), PASSWORD, PASSWORD
    )
    assert await snapshot(session) == before
    assert await count(session, User) == 1
    assert await count(session, Player) == 1
    assert (await user_named(session, "ada")).is_site_admin is False


async def test_duplicate_username_commits_nothing(
    session: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_member(session, "ada")
    commits: list[int] = []
    real_commit = session.commit

    async def counting_commit() -> None:
        commits.append(1)
        await real_commit()

    monkeypatch.setattr(session, "commit", counting_commit)
    await invoke(session, create_admin("ada"), PASSWORD, PASSWORD)
    assert commits == []


async def test_service_raises_username_taken_for_a_case_insensitive_duplicate(
    session: AppSession,
) -> None:
    await seed_member(session, "Alice")
    with pytest.raises(UsernameTakenError) as caught:
        await create_user_with_player(
            session, username="aLICE", password=PASSWORD, display_name="Second"
        )
    assert caught.value.code == "username_taken"
    assert caught.value.status_code == 409
    assert await count(session, User) == 1
    assert await count(session, Player) == 1


# ---------------------------------------------------------------- AC3: registration rules
INVALID_USERNAMES = {
    "too_short": "ab",
    "empty": "",
    "too_long": "a" * 25,
    "space": "a b",
    "leading_space": " abc",
    "trailing_space": "abc ",
    "accent": "caf" + E_ACUTE,
    "at_sign": "hector@home",
    "cyrillic_look_alike": CYRILLIC_A + "bc",
}


@pytest.mark.parametrize("username", INVALID_USERNAMES.values(), ids=INVALID_USERNAMES.keys())
async def test_create_admin_rejects_a_bad_username_with_the_api_message_and_writes_nothing(
    session: AppSession, username: str
) -> None:
    result = await invoke(session, create_admin(username), PASSWORD, PASSWORD)
    assert result.code == 1
    assert result.err == f"error: {InvalidUsernameError().message}\n"
    assert result.out == ""
    assert await count(session, User) == 0
    assert await count(session, Player) == 0


async def test_bad_username_message_is_exactly_the_documented_text(session: AppSession) -> None:
    result = await invoke(session, create_admin("ab"), PASSWORD, PASSWORD)
    assert result.err == (
        "error: Username must be 3 to 24 characters using only letters, digits, '_', '-' and '.'.\n"
    )


@pytest.mark.parametrize("username", INVALID_USERNAMES.values(), ids=INVALID_USERNAMES.keys())
async def test_a_bad_username_never_prompts_for_a_password(
    session: AppSession, username: str
) -> None:
    result = await invoke(session, create_admin(username), PASSWORD, PASSWORD)
    assert result.prompt.asked == []


INVALID_PASSWORDS = {"eleven_characters": "p" * 11, "one_hundred_29_characters": "p" * 129}


@pytest.mark.parametrize("password", INVALID_PASSWORDS.values(), ids=INVALID_PASSWORDS.keys())
async def test_create_admin_rejects_a_bad_password_with_the_api_message_and_writes_nothing(
    session: AppSession, password: str
) -> None:
    result = await invoke(session, create_admin(), password, password)
    assert result.code == 1
    assert result.err == f"error: {InvalidPasswordError().message}\n"
    assert result.out == ""
    assert await count(session, User) == 0
    assert await count(session, Player) == 0


async def test_bad_password_message_is_exactly_the_documented_text(session: AppSession) -> None:
    result = await invoke(session, create_admin(), "p" * 11, "p" * 11)
    assert result.err == "error: Password must be 12 to 128 characters.\n"


@pytest.mark.parametrize("name", ["", "   ", "x" * 33, "bad" + chr(7) + "name"])
async def test_create_admin_rejects_a_bad_display_name_with_the_api_message_and_writes_nothing(
    session: AppSession, name: str
) -> None:
    result = await invoke(session, create_admin("hector", name), PASSWORD, PASSWORD)
    assert result.code == 1
    assert result.err == f"error: {InvalidDisplayNameError().message}\n"
    assert await count(session, User) == 0
    assert await count(session, Player) == 0


async def test_a_bad_display_name_never_prompts_for_a_password(session: AppSession) -> None:
    result = await invoke(session, create_admin("hector", "x" * 33), PASSWORD, PASSWORD)
    assert result.prompt.asked == []


async def test_a_bad_username_is_reported_before_a_bad_display_name(session: AppSession) -> None:
    result = await invoke(session, create_admin("ab", ""), PASSWORD, PASSWORD)
    assert result.err == f"error: {InvalidUsernameError().message}\n"


async def test_password_mismatch_exits_1_and_writes_nothing(session: AppSession) -> None:
    result = await invoke(session, create_admin(), PASSWORD, OTHER_PASSWORD)
    assert result.code == 1
    assert result.err == "error: Passwords do not match.\n"
    assert result.out == ""
    assert result.prompt.asked == ["Password: ", "Repeat password: "]
    assert await count(session, User) == 0
    assert await count(session, Player) == 0


async def test_a_failed_create_admin_does_not_block_a_later_valid_one(
    session: AppSession,
) -> None:
    await invoke(session, create_admin(), "short", "short")
    result = await invoke(session, create_admin(), PASSWORD, PASSWORD)
    assert result.code == 0
    assert await count(session, User) == 1


# ---------------------------------------------------------------- AC4: set-password
async def test_set_password_replaces_the_hash_old_password_stops_working(
    session: AppSession,
) -> None:
    await seed_member(session, "ada", PASSWORD)
    old_hash = (await user_named(session, "ada")).password_hash
    result = await invoke(
        session, ["set-password", "--username", "ada"], OTHER_PASSWORD, OTHER_PASSWORD
    )
    assert result.code == 0
    user = await user_named(session, "ada")
    assert user.password_hash != old_hash
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password(user.password_hash, OTHER_PASSWORD) is True
    assert verify_password(user.password_hash, PASSWORD) is False


async def test_set_password_prints_the_success_line(session: AppSession) -> None:
    await seed_member(session, "ada")
    result = await invoke(
        session, ["set-password", "--username", "ada"], OTHER_PASSWORD, OTHER_PASSWORD
    )
    assert result.out == "Password updated for ada.\n"
    assert result.err == ""
    assert result.prompt.asked == ["Password: ", "Repeat password: "]


async def test_set_password_updates_password_changed_at(session: AppSession) -> None:
    past = datetime.now(UTC) - timedelta(days=30)
    await create(
        session,
        UserFactory,
        username="grace",
        password_hash=hash_password(PASSWORD),
        password_changed_at=past,
    )
    await session.commit()
    before = datetime.now(UTC)
    result = await invoke(
        session, ["set-password", "--username", "grace"], OTHER_PASSWORD, OTHER_PASSWORD
    )
    assert result.code == 0
    changed = (await user_named(session, "grace")).password_changed_at
    assert changed.tzinfo is not None
    assert changed >= before


async def test_set_password_finds_the_user_case_insensitively(session: AppSession) -> None:
    await seed_member(session, "Ada", PASSWORD)
    result = await invoke(
        session, ["set-password", "--username", "ADA"], OTHER_PASSWORD, OTHER_PASSWORD
    )
    assert result.code == 0
    assert verify_password((await user_named(session, "ada")).password_hash, OTHER_PASSWORD)


async def test_set_password_leaves_other_users_untouched(session: AppSession) -> None:
    await seed_member(session, "ada", PASSWORD, "Ada")
    await seed_member(session, "bob", PASSWORD, "Bob")
    bob_hash = (await user_named(session, "bob")).password_hash
    await invoke(session, ["set-password", "--username", "ada"], OTHER_PASSWORD, OTHER_PASSWORD)
    assert (await user_named(session, "bob")).password_hash == bob_hash


async def test_set_password_for_an_unknown_user_exits_1_with_user_not_found(
    session: AppSession,
) -> None:
    await seed_member(session, "ada")
    before = await snapshot(session)
    result = await invoke(
        session, ["set-password", "--username", "nobody"], OTHER_PASSWORD, OTHER_PASSWORD
    )
    assert result.code == 1
    assert result.err == "error: User not found.\n"
    assert result.err == f"error: {UserNotFoundError().message}\n"
    assert result.out == ""
    assert await snapshot(session) == before


@pytest.mark.parametrize("password", INVALID_PASSWORDS.values(), ids=INVALID_PASSWORDS.keys())
async def test_set_password_rejects_a_bad_new_password_and_keeps_the_old_one(
    session: AppSession, password: str
) -> None:
    await seed_member(session, "ada", PASSWORD)
    old_hash = (await user_named(session, "ada")).password_hash
    result = await invoke(session, ["set-password", "--username", "ada"], password, password)
    assert result.code == 1
    assert result.err == f"error: {InvalidPasswordError().message}\n"
    assert (await user_named(session, "ada")).password_hash == old_hash


async def test_set_password_mismatch_exits_1_and_keeps_the_old_password(
    session: AppSession,
) -> None:
    await seed_member(session, "ada", PASSWORD)
    old_hash = (await user_named(session, "ada")).password_hash
    result = await invoke(
        session, ["set-password", "--username", "ada"], OTHER_PASSWORD, PASSWORD + "x"
    )
    assert result.code == 1
    assert result.err == "error: Passwords do not match.\n"
    assert (await user_named(session, "ada")).password_hash == old_hash


# ---------------------------------------------------------------- AC4: the password never leaks
def _log_text(caplog: pytest.LogCaptureFixture) -> str:
    """Plain and JSON-formatted log output together, as the real handler would write it."""
    formatter = JsonFormatter()
    parts = [caplog.text]
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.append(formatter.format(record))
    return "\n".join(parts)


async def test_create_admin_never_prints_or_logs_the_password(
    session: AppSession, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    result = await invoke(session, create_admin(), PASSWORD, PASSWORD)
    assert PASSWORD not in result.out
    assert PASSWORD not in result.err
    assert PASSWORD not in _log_text(caplog)
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err


async def test_set_password_never_prints_or_logs_the_password(
    session: AppSession, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    await seed_member(session, "ada", PASSWORD)
    caplog.set_level(logging.DEBUG)
    result = await invoke(
        session, ["set-password", "--username", "ada"], OTHER_PASSWORD, OTHER_PASSWORD
    )
    assert OTHER_PASSWORD not in result.out + result.err
    assert OTHER_PASSWORD not in _log_text(caplog)
    assert PASSWORD not in _log_text(caplog)
    captured = capsys.readouterr()
    assert OTHER_PASSWORD not in captured.out + captured.err


@pytest.mark.parametrize(
    "scenario", ["mismatch", "too_short", "too_long", "duplicate", "bad_username"]
)
async def test_create_admin_failure_paths_never_print_or_log_the_password(
    session: AppSession,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    typed = {
        "mismatch": ("secret-one-secret-one", "secret-two-secret-two"),
        "too_short": ("tiny-secret", "tiny-secret"),
        "too_long": ("L" * 129, "L" * 129),
        "duplicate": ("dup-secret-dup-secret", "dup-secret-dup-secret"),
        "bad_username": ("bad-user-secret-x", "bad-user-secret-x"),
    }[scenario]
    if scenario == "duplicate":
        await seed_member(session, "hector")
    username = "ab" if scenario == "bad_username" else "hector"
    result = await invoke(session, create_admin(username), *typed)
    assert result.code == 1
    everything = result.out + result.err + _log_text(caplog)
    captured = capsys.readouterr()
    everything += captured.out + captured.err
    for secret in set(typed):
        assert secret not in everything


@pytest.mark.parametrize("scenario", ["mismatch", "too_short", "unknown_user"])
async def test_set_password_failure_paths_never_print_or_log_the_password(
    session: AppSession,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
) -> None:
    await seed_member(session, "ada", PASSWORD)
    caplog.set_level(logging.DEBUG)
    typed = {
        "mismatch": ("secret-one-secret-one", "secret-two-secret-two"),
        "too_short": ("tiny-secret", "tiny-secret"),
        "unknown_user": ("unknown-user-secret", "unknown-user-secret"),
    }[scenario]
    username = "nobody" if scenario == "unknown_user" else "ada"
    result = await invoke(session, ["set-password", "--username", username], *typed)
    assert result.code == 1
    everything = result.out + result.err + _log_text(caplog)
    captured = capsys.readouterr()
    everything += captured.out + captured.err
    for secret in set(typed):
        assert secret not in everything


async def test_create_admin_logs_the_event_with_username_and_user_id_only(
    session: AppSession, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    await invoke(
        session, create_admin("hector", "Hector", "--email", "h@example.com"), PASSWORD, PASSWORD
    )
    user = await user_named(session, "hector")
    records = [r for r in caplog.records if "identity.site_admin_created" in r.getMessage()]
    assert len(records) == 1
    line = json.loads(JsonFormatter().format(records[0]))
    assert "hector" in line["message"]
    assert str(user.id) in line["message"]
    assert "h@example.com" not in _log_text(caplog)
    assert user.password_hash not in _log_text(caplog)


async def test_set_password_logs_the_event_with_the_username_only(
    session: AppSession, caplog: pytest.LogCaptureFixture
) -> None:
    await seed_member(session, "ada", PASSWORD)
    caplog.set_level(logging.INFO)
    await invoke(session, ["set-password", "--username", "ada"], OTHER_PASSWORD, OTHER_PASSWORD)
    records = [r for r in caplog.records if "identity.password_set" in r.getMessage()]
    assert len(records) == 1
    assert "ada" in records[0].getMessage()
    new_hash = (await user_named(session, "ada")).password_hash
    assert new_hash not in _log_text(caplog)


async def test_failed_commands_do_not_log_the_success_events(
    session: AppSession, caplog: pytest.LogCaptureFixture
) -> None:
    await seed_member(session, "ada")
    caplog.set_level(logging.DEBUG)
    await invoke(session, create_admin("ADA"), PASSWORD, PASSWORD)
    await invoke(session, ["set-password", "--username", "nobody"], PASSWORD, PASSWORD)
    text = _log_text(caplog)
    assert "identity.site_admin_created" not in text
    assert "identity.password_set" not in text


# ---------------------------------------------------------------- AC4: no password option
@pytest.mark.parametrize(
    "argv",
    [
        [*create_admin(), "--password", "hunter2hunter2"],
        [*create_admin(), "--password=hunter2hunter2"],
        ["set-password", "--username", "ada", "--password", "hunter2hunter2"],
        ["set-password", "--username", "ada", "--password=hunter2hunter2"],
    ],
)
async def test_a_password_option_is_a_usage_error_with_exit_code_2(
    session: AppSession, argv: list[str]
) -> None:
    result = await invoke(session, argv, PASSWORD, PASSWORD)
    assert result.code == 2
    assert result.prompt.asked == []
    assert await count(session, User) == 0


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["bogus-command"],
        ["create-admin", "--username", "hector"],
        ["create-admin", "--display-name", "Hector"],
        ["set-password"],
    ],
)
async def test_usage_errors_exit_2_without_prompting_or_writing(
    session: AppSession, argv: list[str]
) -> None:
    result = await invoke(session, argv, PASSWORD, PASSWORD)
    assert result.code == 2
    assert result.prompt.asked == []
    assert await count(session, User) == 0


@pytest.mark.parametrize(
    "argv", [["--help"], ["create-admin", "--help"], ["set-password", "--help"]]
)
async def test_help_text_has_no_password_option(
    session: AppSession, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    result = await invoke(session, argv)
    help_text = capsys.readouterr().out
    assert result.code == 0
    assert "--username" in help_text or "create-admin" in help_text
    assert "--password" not in help_text
    assert "--passwd" not in help_text


async def test_the_password_environment_variables_are_ignored(
    session: AppSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("PASSWORD", "ADMIN_PASSWORD", "MTG_STATS_PASSWORD", "CLI_PASSWORD"):
        monkeypatch.setenv(name, "from-the-environment-123")
    result = await invoke(session, create_admin(), PASSWORD, PASSWORD)
    assert result.prompt.asked == ["Password: ", "Repeat password: "]
    user = await user_named(session, "hector")
    assert verify_password(user.password_hash, PASSWORD) is True
    assert verify_password(user.password_hash, "from-the-environment-123") is False


def test_cli_package_exports_run_and_main() -> None:
    assert callable(app.cli.run)
    assert callable(app.cli.main)


# ---------------------------------------------------------------- subprocess smoke
def _python_m_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)  # conftest forced APP_ENV, DATABASE_URL, REDIS_URL, PUBLIC_WEB_URL
    return subprocess.run(  # noqa: S603 - fixed arguments, controlled environment
        [sys.executable, "-m", "app.cli", *args],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_python_m_app_cli_help_exits_0_and_lists_both_commands() -> None:
    result = _python_m_cli("--help")
    assert result.returncode == 0, result.stderr
    assert "create-admin" in result.stdout
    assert "set-password" in result.stdout
    assert "--password" not in result.stdout


def test_python_m_app_cli_rejects_a_password_option_with_exit_code_2() -> None:
    result = _python_m_cli("set-password", "--username", "ada", "--password", "hunter2hunter2")
    assert result.returncode == 2
    assert "hunter2hunter2" not in result.stdout
