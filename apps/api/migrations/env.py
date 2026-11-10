"""Alembic environment (S-1.4.3).

The URL comes from the app settings (``DATABASE_URL``), never from ``alembic.ini``, and the
engine is the async one over ``postgresql+psycopg://``, the same driver as the app. Every domain's
models are loaded through :mod:`app.model_registry`.

Tests may pass ``config.attributes["target_metadata"]`` to compare against test-only models.
"""

import asyncio
import sys
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import MetaData, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.model_registry import metadata as registry_metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata: MetaData = config.attributes.get("target_metadata", registry_metadata)


def _configure(**kwargs: Any) -> None:
    context.configure(target_metadata=target_metadata, compare_type=True, **kwargs)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    _configure(
        url=get_settings().database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations on an async engine built from the app settings."""
    engine = create_async_engine(get_settings().database_url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
elif sys.platform == "win32":
    # psycopg's async mode cannot run on the default Proactor loop; pick the loop per run
    # instead of changing the process-global event loop policy.
    asyncio.run(run_migrations_online(), loop_factory=asyncio.SelectorEventLoop)
else:
    asyncio.run(run_migrations_online())
