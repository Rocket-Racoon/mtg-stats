"""Shared pytest configuration for the API test suite."""

import asyncio
import os
import sys
import warnings
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests.sample_models import SampleBase

pytest_plugins = ["pytester"]

# `app.main` builds `app = create_app()` at import time, which needs valid settings.
# Forced, not defaulted, so a shell with APP_ENV=dev cannot leak into the run.
TEST_DATABASE_URL = "postgresql+psycopg://mtg_stats:CHANGE_ME@127.0.0.1:5432/mtg_stats_test"
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

if sys.platform == "win32":
    # psycopg's async mode cannot run on the default Proactor loop.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """Engine for ``mtg_stats_test`` (no pooling, so no connection outlives its event loop).

    Skips the test with a clear reason when Postgres is unreachable.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect():
            pass
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(
            "Postgres is not reachable; start it with "
            f"`docker compose -f infra/docker-compose.yml up -d postgres` ({type(exc).__name__})"
        )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Session bound to a connection-level transaction that is rolled back after the test.

    ``create_savepoint`` makes the code under test's ``commit()`` release a savepoint only, so
    nothing is ever committed to ``mtg_stats_test`` and tests cannot see each other's rows.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
async def sample_schema(db_session: AsyncSession) -> AsyncSession:
    """``db_session`` with the sample tables created inside its (rolled-back) transaction."""
    connection = await db_session.connection()
    await connection.run_sync(SampleBase.metadata.create_all)
    return db_session
