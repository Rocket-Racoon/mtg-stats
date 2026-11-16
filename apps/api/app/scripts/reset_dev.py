"""Drop, recreate, migrate and seed the dev database (S-1.3.4).

Run from ``apps/api`` with ``uv run python -m app.scripts.reset_dev``. The guard runs first,
before settings are read or any connection is opened: the effective ``APP_ENV`` must be exactly
``dev`` and the effective ``DATABASE_URL`` host must be ``localhost``, ``127.0.0.1`` or the
Compose ``postgres`` service. Otherwise it exits 1 with the reason and touches nothing.

The effective values follow :mod:`app.core.config`: the process environment wins, and the
repo-root ``.env`` counts only when the effective ``APP_ENV`` is ``dev``. One validated parse of
``DATABASE_URL`` feeds the recreate, the migration and the seeding.
"""

import argparse
import asyncio
import importlib
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import psycopg
from psycopg import sql
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.core.config import DATABASE_URL_PREFIX, get_settings
from app.core.config import dev_env_file as _dev_env_file
from app.core.db import dispose_engine, get_sessionmaker
from app.core.seeding import run_seeders
from app.scripts.migrate_dev import EXIT_REFUSED, upgrade_head

__all__ = [
    "ALLOWED_HOSTS",
    "EXIT_FAILED",
    "EXIT_REFUSED",
    "FORBIDDEN_LIBPQ_VARS",
    "MAINTENANCE_DB",
    "SYSTEM_DATABASES",
    "build_parser",
    "check_guard",
    "cli",
    "discover_seed_domains",
    "main",
    "recreate_database",
    "resolve_environ",
    "run_seeding",
]

EXIT_FAILED = 3
CONNECT_TIMEOUT = 5
ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "postgres"})
MAINTENANCE_DB = "postgres"
SYSTEM_DATABASES = frozenset({"postgres", "template0", "template1"})
FORBIDDEN_LIBPQ_VARS = ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")
APP_DIR = Path(__file__).resolve().parents[1]


def check_guard(environ: Mapping[str, str]) -> str | None:
    """Return the reason the reset must be refused, or ``None`` when it may run."""
    app_env = environ.get("APP_ENV", "")
    if app_env != "dev":
        return "APP_ENV is not set to dev (process environment or the dev .env)"
    for var in FORBIDDEN_LIBPQ_VARS:
        if environ.get(var):
            return f"{var} is set; libpq would let it override the database host"
    raw = environ.get("DATABASE_URL", "")
    if not raw:
        return "DATABASE_URL is not set"
    try:
        url = make_url(raw)
    except Exception:
        return "DATABASE_URL could not be parsed"
    if f"{url.drivername}://" != DATABASE_URL_PREFIX:
        return f"DATABASE_URL must start with {DATABASE_URL_PREFIX}"
    if url.query:
        return "DATABASE_URL has a query string, which could redirect the connection"
    if not url.database:
        return "DATABASE_URL has no database name"
    if url.database in SYSTEM_DATABASES:
        return f"database {url.database!r} is a system database"
    if url.host not in ALLOWED_HOSTS:
        return (
            f"database host {url.host!r} is not allowed; "
            f"allowed hosts: {', '.join(sorted(ALLOWED_HOSTS))}"
        )
    return None


class _DevEnvProbe(BaseSettings):
    """Reads ``APP_ENV`` and ``DATABASE_URL`` (process environment wins over the ``.env``)."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    APP_ENV: str | None = None
    DATABASE_URL: str | None = None


def resolve_environ() -> dict[str, str]:
    """Return the process environment plus ``APP_ENV``/``DATABASE_URL`` from a dev ``.env``.

    Mirrors :mod:`app.core.config`: the root ``.env`` is read only when the effective
    ``APP_ENV`` is ``dev``, and process variables win over its values.
    """
    resolved = dict(os.environ)
    env_file = _dev_env_file()
    if env_file is not None:
        probe = _DevEnvProbe(_env_file=env_file)
        for key in ("APP_ENV", "DATABASE_URL"):
            value = getattr(probe, key)
            if key not in resolved and value is not None:
                resolved[key] = value
    return resolved


def discover_seed_domains() -> list[str]:
    """Return every ``app/<domain>/services/__init__.py`` package name found on disk."""
    return sorted(
        path.parent.parent.name for path in APP_DIR.glob("*/services/__init__.py") if path.is_file()
    )


def recreate_database(database_url: str) -> None:
    """Drop (forcing other connections off) and create the database named in the URL."""
    url = make_url(database_url)
    name = str(url.database)
    with psycopg.connect(
        host=url.host,
        hostaddr="",
        port=url.port,
        user=url.username,
        password=url.password,
        dbname=MAINTENANCE_DB,
        connect_timeout=CONNECT_TIMEOUT,
        autocommit=True,
    ) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


async def _seed_async() -> int:
    for domain in discover_seed_domains():
        importlib.import_module(f"app.{domain}.services")
    try:
        async with get_sessionmaker()() as session:
            count = await run_seeders(session)
            await session.commit()
            return count
    finally:
        await dispose_engine()


def run_seeding() -> int:
    """Import every domain's services, run the registered seeders and commit; return the count."""
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(_seed_async(), loop_factory=loop_factory)


def _fail(step: str, exc: Exception) -> int:
    sys.stderr.write(f"reset: database was recreated but {step} failed: {exc!r}\n")
    return EXIT_FAILED


def main(
    environ: Mapping[str, str] | None = None,
    recreate: Callable[[str], None] = recreate_database,
    upgrade: Callable[[], None] = upgrade_head,
    seed: Callable[[], int] = run_seeding,
) -> int:
    """Reset the dev database when the guard allows it; return the process exit code.

    With ``environ=None`` the effective environment is resolved from the process and the dev
    ``.env``, then exported (validated) so migration and seeding read the same URL. An injected
    ``environ`` is for tests only.
    """
    real = environ is None
    env = resolve_environ() if environ is None else environ
    reason = check_guard(env)
    if reason is not None:
        sys.stderr.write(f"reset refused: {reason}. The database was not touched.\n")
        return EXIT_REFUSED
    url = make_url(env["DATABASE_URL"]).render_as_string(hide_password=False)
    if real:
        os.environ["APP_ENV"] = "dev"
        os.environ["DATABASE_URL"] = url
        get_settings.cache_clear()
        try:
            get_settings()
        except ValidationError as exc:
            names = ", ".join(sorted({".".join(map(str, e["loc"])) for e in exc.errors()}))
            sys.stderr.write(
                f"reset refused: invalid settings ({names}). The database was not touched.\n"
            )
            return EXIT_REFUSED
    try:
        recreate(url)
    except Exception as exc:
        sys.stderr.write(f"reset failed while recreating the database: {exc!r}\n")
        return EXIT_FAILED
    sys.stdout.write("reset: database dropped and recreated\n")
    try:
        upgrade()
    except Exception as exc:
        return _fail("migration", exc)
    sys.stdout.write("reset: migrated to head\n")
    try:
        count = seed()
    except Exception as exc:
        return _fail("seeding", exc)
    sys.stdout.write(f"seeded: {count} seeders\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Return the (argument-less) command-line parser."""
    return argparse.ArgumentParser(
        prog="python -m app.scripts.reset_dev",
        description=(
            "Drop, recreate, migrate to head and seed the dev database. Refuses unless "
            "APP_ENV=dev and the database host is localhost, 127.0.0.1 or postgres."
        ),
    )


def cli(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` (extra arguments exit 2) and run :func:`main`."""
    build_parser().parse_args(argv)
    return main()


if __name__ == "__main__":
    sys.exit(cli())
