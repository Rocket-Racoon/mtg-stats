"""Argument parsing and the ``create-admin`` and ``set-password`` commands (S-2.1.1).

The password only ever comes from a hidden prompt: there is no option or environment variable
for it, so it never reaches the shell history, ``argv`` or the logs.
"""

import argparse
import asyncio
import getpass
import logging
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import TextIO

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.core.errors import DomainError
from app.core.logging import configure_logging
from app.identity.services import (
    create_user_with_player,
    normalize_display_name,
    set_password,
    validate_password,
    validate_username,
)

logger = logging.getLogger("app.cli")

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
Prompt = Callable[[str], str]


class _NoTerminalError(Exception):
    """The default password prompt was used without a terminal."""


class _PasswordMismatchError(Exception):
    """The two typed passwords differ."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="MTG Stats operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin", help="create a site administrator")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--email", default="")
    setpw = sub.add_parser("set-password", help="replace a user's password")
    setpw.add_argument("--username", required=True)
    return parser


def _read_password(prompt: Prompt) -> str:
    password = prompt("Password: ")
    if prompt("Repeat password: ") != password:
        raise _PasswordMismatchError
    return password


def _require_terminal(prompt: Prompt | None) -> bool:
    """The default prompt needs a real terminal; an injected one (tests) does not."""
    return prompt is not None or sys.stdin.isatty()


async def _create_admin(
    args: argparse.Namespace,
    factory: SessionFactory,
    prompt: Prompt,
    out: TextIO,
    *,
    interactive: bool,
) -> None:
    username: str = args.username
    validate_username(username)
    normalize_display_name(args.display_name)
    if not interactive:
        raise _NoTerminalError
    password = _read_password(prompt)
    validate_password(password)
    async with factory() as session:
        try:
            info = await create_user_with_player(
                session,
                username=username,
                password=password,
                display_name=args.display_name,
                email=args.email,
                is_site_admin=True,
            )
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
    logger.info("identity.site_admin_created username=%s user_id=%s", username, info.user_id)
    print(f"Created site admin {username}.", file=out)


async def _set_password(
    args: argparse.Namespace,
    factory: SessionFactory,
    prompt: Prompt,
    out: TextIO,
    *,
    interactive: bool,
) -> None:
    username: str = args.username
    if not interactive:
        raise _NoTerminalError
    password = _read_password(prompt)
    validate_password(password)
    async with factory() as session:
        try:
            await set_password(session, username=username, password=password)
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
    logger.info("identity.password_set username=%s", username)
    print(f"Password updated for {username}.", file=out)


async def run(
    argv: Sequence[str],
    *,
    session_factory: SessionFactory | None = None,
    prompt: Prompt | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one CLI command and return the exit code (0 ok, 1 error, 2 usage)."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    ask: Prompt = prompt if prompt is not None else getpass.getpass
    parser = _build_parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    own_engine = session_factory is None
    factory: SessionFactory = session_factory if session_factory is not None else get_sessionmaker()
    try:
        if args.command == "create-admin":
            await _create_admin(args, factory, ask, out, interactive=_require_terminal(prompt))
        else:
            await _set_password(args, factory, ask, out, interactive=_require_terminal(prompt))
    except _NoTerminalError:
        print("error: A terminal is required to type the password.", file=err)
        return 1
    except _PasswordMismatchError:
        print("error: Passwords do not match.", file=err)
        return 1
    except EOFError:
        print("error: No password entered.", file=err)
        return 1
    except KeyboardInterrupt:
        print("error: Cancelled.", file=err)
        return 130
    except DomainError as exc:
        print(f"error: {exc.message}", file=err)
        return 1
    except Exception as exc:
        # No exc_info and no str(exc): a database error would echo the bound parameters,
        # including the password hash.
        logger.error("cli.unexpected_failure type=%s", type(exc).__name__)
        print(f"error: Unexpected failure ({type(exc).__name__}).", file=err)
        return 1
    finally:
        if own_engine:
            await dispose_engine()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: configure logging, run the command and return its exit code."""
    configure_logging(get_settings().log_level)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(run(sys.argv[1:] if argv is None else argv), loop_factory=loop_factory)
