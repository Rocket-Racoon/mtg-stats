"""Create or reset the end-to-end test account (S-1.8.5), for dev and CI stacks only.

Run from ``apps/api`` with ``E2E_USERNAME=... E2E_PASSWORD=... uv run python -m
app.scripts.seed_e2e_user``. Inside the Compose ``api`` container, run it through the image
entrypoint, which builds ``DATABASE_URL`` for the ``postgres`` service (``exec`` skips it)::

    docker compose -f infra/docker-compose.yml exec -T -e E2E_USERNAME=... -e E2E_PASSWORD=... \
        api docker-entrypoint.sh python -m app.scripts.seed_e2e_user

It reads ``E2E_USERNAME``, ``E2E_PASSWORD`` and the optional ``E2E_DISPLAY_NAME`` (default
``E2E Tester``). It exits 1 without touching the database when ``APP_ENV`` is ``prod`` or a
required variable is missing. The run is idempotent: the user (not a site admin) and its player
are created when absent, otherwise the password is reset and the user's refresh tokens are
revoked. It prints ``seeded e2e user <username>``, never the password.
"""

import asyncio
import os
import sys
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import TextIO

from pydantic import ValidationError
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.core.errors import DomainError
from app.identity.services import UsernameTakenError, create_user_with_player, set_password

EXIT_REFUSED = 1
DEFAULT_DISPLAY_NAME = "E2E Tester"

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def check_guard(environ: Mapping[str, str]) -> str | None:
    """Return the reason the seeding must be refused, or ``None`` when it may run."""
    if environ.get("APP_ENV", "") == "prod":
        return "APP_ENV=prod; the e2e account is for dev and CI stacks only"
    missing = [name for name in ("E2E_USERNAME", "E2E_PASSWORD") if not environ.get(name)]
    if missing:
        return f"{' and '.join(missing)} must be set"
    return None


async def seed(
    session_factory: SessionFactory, *, username: str, password: str, display_name: str
) -> None:
    """Create the user with a player, or reset the password when it exists; then commit."""
    async with session_factory() as session:
        try:
            try:
                await create_user_with_player(
                    session, username=username, password=password, display_name=display_name
                )
            except UsernameTakenError:
                await set_password(session, username=username, password=password)
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def run(
    environ: Mapping[str, str] | None = None,
    *,
    session_factory: SessionFactory | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Seed the e2e account and return the exit code (0 ok, 1 refused or failed)."""
    env = os.environ if environ is None else environ
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    reason = check_guard(env)
    if reason is not None:
        print(f"seed refused: {reason}. The database was not touched.", file=err)
        return EXIT_REFUSED
    own_engine = session_factory is None
    if own_engine:
        try:
            settings = get_settings()
        except ValidationError:
            print("seed refused: invalid settings. The database was not touched.", file=err)
            return EXIT_REFUSED
        if settings.app_env == "prod":
            print("seed refused: APP_ENV=prod. The database was not touched.", file=err)
            return EXIT_REFUSED
    factory: SessionFactory = session_factory if session_factory is not None else get_sessionmaker()
    username = env["E2E_USERNAME"]
    try:
        await seed(
            factory,
            username=username,
            password=env["E2E_PASSWORD"],
            display_name=env.get("E2E_DISPLAY_NAME") or DEFAULT_DISPLAY_NAME,
        )
    except DomainError as exc:
        print(f"seed failed: {exc.message}", file=err)
        return 1
    except (OperationalError, InterfaceError):
        print(
            "seed failed: could not connect to the database; check DATABASE_URL's host "
            "(inside the api container run it through docker-entrypoint.sh).",
            file=err,
        )
        return 1
    except Exception as exc:
        # No str(exc): a database error would echo bound parameters, including the hash.
        print(f"seed failed: unexpected failure ({type(exc).__name__}).", file=err)
        return 1
    finally:
        if own_engine:
            await dispose_engine()
    print(f"seeded e2e user {username}", file=out)
    return 0


def main() -> int:
    """Entry point: run the seeding on the configured database; return the exit code."""
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(run(), loop_factory=loop_factory)


if __name__ == "__main__":
    sys.exit(main())
