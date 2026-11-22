"""Argument parsing and the operator commands (S-2.1.1, S-2.1.11, S-6.3.8).

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
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.core.errors import DomainError
from app.core.logging import configure_logging
from app.games.services import RebuildError, rebuild_state
from app.identity.services import (
    create_user_with_player,
    normalize_display_name,
    set_password,
    set_site_admin,
    validate_password,
    validate_username,
)

logger = logging.getLogger("app.cli")

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
Prompt = Callable[[str], str]

EXIT_MISMATCH = 3
"""``rebuild-state --dry-run`` found a snapshot that differs from its log (nothing was written)."""


class _NoTerminalError(Exception):
    """The default password prompt was used without a terminal."""


class _PasswordMismatchError(Exception):
    """The two typed passwords differ."""


def _game_id(text: str) -> UUID:
    try:
        return UUID(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a game id (a UUID)") from None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="MTG Stats operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin", help="create a site administrator")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--email", default="")
    setpw = sub.add_parser("set-password", help="replace a user's password")
    setpw.add_argument("--username", required=True)
    for name, text in (
        ("grant-site-admin", "make a user a site administrator"),
        ("revoke-site-admin", "remove a user's site administrator role"),
    ):
        role = sub.add_parser(name, help=text)
        role.add_argument("--username", required=True)
    rebuild = sub.add_parser(
        "rebuild-state", help="replace a game's state snapshot with the replay of its event log"
    )
    rebuild.add_argument("--game-id", required=True, type=_game_id)
    rebuild.add_argument(
        "--dry-run",
        action="store_true",
        help="report a mismatch (exit 3) without writing anything",
    )
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


async def _site_admin(
    args: argparse.Namespace, factory: SessionFactory, out: TextIO, *, value: bool
) -> None:
    async with factory() as session:
        try:
            change = await set_site_admin(session, username=args.username, value=value)
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
    logger.info(
        "identity.site_admin_changed username=%s user_id=%s old=%s new=%s at=%s",
        change.username,
        change.user_id,
        change.old,
        change.new,
        change.at.isoformat(),
    )
    verb = "is now" if value else "is no longer"
    print(f"{change.username} {verb} a site admin.", file=out)


async def _rebuild_state(args: argparse.Namespace, factory: SessionFactory, out: TextIO) -> int:
    dry_run: bool = args.dry_run
    async with factory() as session:
        try:
            result = await rebuild_state(session, args.game_id, dry_run=dry_run)
            if dry_run:
                await session.rollback()  # releases the game lock; nothing was written
            else:
                await session.commit()
        except BaseException:
            await session.rollback()
            raise
    if not result.changed:
        print(f"Game {result.game_id}: snapshot matches its {result.event_count} events.", file=out)
        return 0
    keys = ", ".join(result.differing_keys) or "(only last_seq)"
    if dry_run:
        print(
            f"Game {result.game_id}: snapshot differs from its {result.event_count} events "
            f"(keys: {keys}); dry run, nothing written.",
            file=out,
        )
        return EXIT_MISMATCH
    print(
        f"Game {result.game_id}: snapshot rebuilt from {result.event_count} events (keys: {keys}).",
        file=out,
    )
    return 0


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
    code = 0
    try:
        if args.command == "rebuild-state":
            code = await _rebuild_state(args, factory, out)
        elif args.command == "create-admin":
            await _create_admin(args, factory, ask, out, interactive=_require_terminal(prompt))
        elif args.command in ("grant-site-admin", "revoke-site-admin"):
            await _site_admin(args, factory, out, value=args.command == "grant-site-admin")
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
    except RebuildError as exc:
        print(f"error: {exc}", file=err)
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
    return code


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: configure logging, run the command and return its exit code."""
    configure_logging(get_settings().log_level)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(run(sys.argv[1:] if argv is None else argv), loop_factory=loop_factory)
