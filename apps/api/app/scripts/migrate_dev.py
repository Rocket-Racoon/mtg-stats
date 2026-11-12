"""Run ``alembic upgrade head`` once, but only when ``APP_ENV=dev`` (S-1.3.2).

This is the one-shot ``migrate`` service of ``infra/docker-compose.yml``: ``python -m
app.scripts.migrate_dev``. For any other ``APP_ENV`` (including unset) it exits with status 1
and a clear message before it reads settings or opens a database connection, so it can never
migrate a non-dev database. The API and worker wait for this step to complete successfully.

It imports :mod:`alembic` and ``migrations/env.py`` (which reads ``DATABASE_URL``), never
``app.main``.
"""

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from alembic import command
from alembic.config import Config

EXIT_REFUSED = 1
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def upgrade_head() -> None:
    """Run ``alembic upgrade head`` against the configured database."""
    command.upgrade(Config(str(ALEMBIC_INI)), "head")


def main(
    environ: Mapping[str, str] | None = None,
    upgrade: Callable[[], None] = upgrade_head,
) -> int:
    """Migrate when ``APP_ENV`` is exactly ``dev``; return the process exit code."""
    env = os.environ if environ is None else environ
    app_env = env.get("APP_ENV", "")
    if app_env != "dev":
        sys.stderr.write(
            f"migrate refused: APP_ENV={app_env!r}, but automatic migrations run only when "
            "APP_ENV=dev. The database was not touched.\n"
        )
        return EXIT_REFUSED
    upgrade()
    sys.stdout.write("migrate: database is at head\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
