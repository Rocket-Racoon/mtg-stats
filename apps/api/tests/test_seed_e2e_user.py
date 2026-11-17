"""Behavior of ``python -m app.scripts.seed_e2e_user`` (S-1.8.5, decision 3).

The seeding runs against the rolled-back ``db_session`` through an injected session factory.
"""

import io
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.db import AppSession
from app.identity.models import Player, User
from app.identity.services import verify_password
from app.scripts.seed_e2e_user import run

API_ROOT = Path(__file__).resolve().parents[1]
FIRST_PASSWORD = "first-e2e-passphrase-1"  # test fixture  # secrets-check: allow
SECOND_PASSWORD = "second-e2e-passphrase-2"  # test fixture  # secrets-check: allow


@pytest.fixture
async def session(app_schema: AppSession) -> AppSession:
    """The session with the schema committed to its outer savepoint (see test_cli_admin)."""
    await app_schema.commit()
    return app_schema


async def seed(session: AppSession, environ: Mapping[str, str]) -> tuple[int, str, str]:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AppSession]:
        yield session

    out, err = io.StringIO(), io.StringIO()
    code = await run(environ, session_factory=factory, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def env(password: str = FIRST_PASSWORD, **extra: str) -> dict[str, str]:
    return {"APP_ENV": "test", "E2E_USERNAME": "e2e-tester", "E2E_PASSWORD": password, **extra}


async def counts(session: AppSession) -> tuple[int, int]:
    users = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    players = (await session.execute(select(func.count()).select_from(Player))).scalar_one()
    return users, players


async def the_user(session: AppSession) -> User:
    result = await session.execute(select(User).execution_options(populate_existing=True))
    return result.scalar_one()


async def test_seed_creates_a_non_admin_user_with_a_player_when_absent(session: AppSession) -> None:
    code, out, err = await seed(session, env())
    assert (code, err) == (0, "")
    assert out == "seeded e2e user e2e-tester\n"
    assert await counts(session) == (1, 1)
    user = await the_user(session)
    assert user.username == "e2e-tester"
    assert user.is_site_admin is False
    assert verify_password(user.password_hash, FIRST_PASSWORD)
    player = (await session.execute(select(Player))).scalar_one()
    assert player.display_name == "E2E Tester"


async def test_seed_uses_the_given_display_name(session: AppSession) -> None:
    code, _, _ = await seed(session, env(E2E_DISPLAY_NAME="Playwright"))
    assert code == 0
    assert (await session.execute(select(Player))).scalar_one().display_name == "Playwright"


async def test_second_run_resets_the_password_without_a_second_user(session: AppSession) -> None:
    assert (await seed(session, env(FIRST_PASSWORD)))[0] == 0
    code, out, _ = await seed(session, env(SECOND_PASSWORD))
    assert code == 0
    assert out == "seeded e2e user e2e-tester\n"
    assert await counts(session) == (1, 1)
    user = await the_user(session)
    assert verify_password(user.password_hash, SECOND_PASSWORD)
    assert not verify_password(user.password_hash, FIRST_PASSWORD)


async def test_output_never_contains_the_password(session: AppSession) -> None:
    for password in (FIRST_PASSWORD, SECOND_PASSWORD):
        _, out, err = await seed(session, env(password))
        assert password not in out + err


async def test_invalid_password_fails_with_a_message_and_no_password_echo(
    session: AppSession,
) -> None:
    code, out, err = await seed(session, env("short"))
    assert code == 1
    assert out == ""
    assert "seed failed" in err
    assert "short" not in err.replace("seed failed", "")
    assert await counts(session) == (0, 0)


@pytest.mark.parametrize("missing", ["E2E_USERNAME", "E2E_PASSWORD"])
async def test_missing_variable_is_refused_and_touches_nothing(
    session: AppSession, missing: str
) -> None:
    environ = env()
    del environ[missing]
    code, out, err = await seed(session, environ)
    assert code == 1
    assert out == ""
    assert missing in err
    assert await counts(session) == (0, 0)


async def test_empty_variable_is_refused(session: AppSession) -> None:
    code, _, err = await seed(session, env("", E2E_USERNAME="x"))
    assert code == 1
    assert "E2E_PASSWORD" in err


async def test_prod_is_refused_and_touches_nothing(session: AppSession) -> None:
    environ = env()
    environ["APP_ENV"] = "prod"
    code, out, err = await seed(session, environ)
    assert code == 1
    assert out == ""
    assert "prod" in err
    assert FIRST_PASSWORD not in err
    assert await counts(session) == (0, 0)


def test_module_exits_non_zero_for_prod_before_any_connection() -> None:
    environ = {**os.environ, "APP_ENV": "prod", "E2E_USERNAME": "u", "E2E_PASSWORD": FIRST_PASSWORD}
    result = subprocess.run(
        [sys.executable, "-m", "app.scripts.seed_e2e_user"],
        cwd=API_ROOT,
        env=environ,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 1
    assert "seed refused" in result.stderr
    assert FIRST_PASSWORD not in result.stdout + result.stderr
