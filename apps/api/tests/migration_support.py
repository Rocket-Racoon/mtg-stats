"""Helpers that run Alembic against a throwaway database, never against a shared one."""

import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import URL, make_url

from app.core.config import get_settings
from tests.db_policy import must_fail_when_db_unreachable, resolve_test_database_url

API_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = API_ROOT / "alembic.ini"
TEMP_DB_PREFIX = "mtg_stats_mig_"
MIGRATION_FILE_RE = re.compile(r"^\d{8}_\d{4}_[0-9a-f]+_[a-z0-9_]+\.py$")


def _server_url() -> URL:
    return make_url(resolve_test_database_url(os.environ))


def _admin_conninfo() -> str:
    url = _server_url().set(database="postgres", drivername="postgresql")
    return url.render_as_string(hide_password=False)


@contextmanager
def temporary_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Create ``mtg_stats_mig_<uuid>``, point the app settings at it, drop it afterwards.

    Only a database created here (unique name) is ever dropped. Skips when Postgres is
    unreachable unless ``CI``/``MTG_STATS_REQUIRE_DB=1`` demands a failure.
    """
    name = f"{TEMP_DB_PREFIX}{uuid.uuid4().hex}"
    try:
        admin = psycopg.connect(_admin_conninfo(), autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as exc:
        message = f"Postgres is not reachable ({type(exc).__name__})"
        if must_fail_when_db_unreachable(os.environ):
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    with admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        url = _server_url().set(database=name).render_as_string(hide_password=False)
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        try:
            yield url
        finally:
            get_settings.cache_clear()
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def alembic_config(versions_dir: Path | None = None, **attributes: object) -> Config:
    """Alembic config for the real ``alembic.ini``, optionally with a different versions dir."""
    config = Config(str(ALEMBIC_INI))
    if versions_dir is not None:
        config.set_main_option("version_locations", str(versions_dir))
    config.attributes.update(attributes)
    return config
