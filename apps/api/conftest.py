"""Shared pytest configuration for the API test suite (``tests/`` and every ``app/**/tests/``)."""

import asyncio
import os
import sys
import uuid
import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.db import AppSession, get_session
from tests.db_policy import must_fail_when_db_unreachable, resolve_test_database_url
from tests.jobs import JobQueue, delete_queue_keys
from tests.sample_models import SampleBase
from tests.statement_counter import StatementCounter

pytest_plugins = ["pytester"]

DEFAULT_TEST_REDIS_URL = "redis://127.0.0.1:6379/15"

# `create_app()` reads the settings, which need a valid environment.
# Forced, not defaulted, so a shell with APP_ENV=dev cannot leak into the run.
TEST_DATABASE_URL = resolve_test_database_url(os.environ)
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "").strip() or DEFAULT_TEST_REDIS_URL

if sys.platform == "win32":
    # psycopg's async mode cannot run on the default Proactor loop.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """Engine for ``mtg_stats_test`` (no pooling, so no connection outlives its event loop).

    When Postgres is unreachable the test is skipped, unless ``CI`` or ``MTG_STATS_REQUIRE_DB=1``
    is set, in which case it fails.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect():
            pass
    except OperationalError as exc:
        await engine.dispose()
        message = (
            "Postgres is not reachable; start it with "
            f"`docker compose -f infra/docker-compose.yml up -d postgres` ({type(exc).__name__})"
        )
        if must_fail_when_db_unreachable(os.environ):
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AppSession]:
    """Session bound to a connection-level transaction that is rolled back after the test.

    ``create_savepoint`` makes the code under test's ``commit()`` release a savepoint only, so
    nothing is ever committed to ``mtg_stats_test`` and tests cannot see each other's rows.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        session = async_sessionmaker(
            connection,
            class_=AppSession,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )()
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
async def sample_schema(db_session: AppSession) -> AppSession:
    """``db_session`` with the sample tables created inside its (rolled-back) transaction."""
    connection = await db_session.connection()
    await connection.run_sync(SampleBase.metadata.create_all)
    return db_session


@pytest.fixture
async def api_app(db_session: AppSession) -> AsyncIterator[FastAPI]:
    """An app whose ``get_session`` yields the rolled-back ``db_session`` (nothing really commits).

    Mirrors the real dependency: commit on success (a savepoint release here), rollback on error,
    and never closes the shared session. Add the routes under test to the returned app.
    """
    from app.main import create_app  # noqa: PLC0415

    app = create_app()

    async def override() -> AsyncIterator[AppSession]:
        try:
            yield db_session
            await db_session.commit()
        except BaseException:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_session] = override
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def client(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """httpx client for ``api_app``; unhandled errors come back as 500 responses.

    Write isolation covers only sessions obtained through ``get_session``/``SessionDep``, which
    are overridden to use the rolled-back ``db_session``. Code that calls ``get_sessionmaker()``
    directly gets a real session and commits for real to ``mtg_stats_test``.
    """
    transport = httpx.ASGITransport(app=api_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http_client:
        yield http_client


@pytest.fixture
def count_statements(
    db_engine: AsyncEngine,
) -> Callable[[], AbstractContextManager[StatementCounter]]:
    """Context manager factory counting SQL statements run through ``db_engine`` (S-1.4.7).

    Use ``with count_statements() as counter: ...`` then read ``counter.count``. It counts every
    cursor execution, including SAVEPOINT bookkeeping of the test session, which is constant per
    request, so equal counts for different data sizes prove there is no N+1.

    Only statements on ``db_engine`` are counted: code that opens sessions through
    ``get_sessionmaker()`` directly uses another engine and is not counted.

    The ``before_cursor_execute`` listener is test instrumentation only; the "no ORM event
    listeners for business logic" rule does not apply to it.
    """

    @contextmanager
    def counting() -> Iterator[StatementCounter]:
        counter = StatementCounter()

        def on_execute(
            conn: object, cursor: object, statement: str, *args: object, **kwargs: object
        ) -> None:
            counter.count += 1
            counter.statements.append(statement)

        target = db_engine.sync_engine
        event.listen(target, "before_cursor_execute", on_execute)
        try:
            yield counter
        finally:
            event.remove(target, "before_cursor_execute", on_execute)

    return counting


@pytest.fixture
async def job_queue(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[JobQueue]:
    """Point ``core.jobs`` at a unique queue on the test Redis, and delete its keys afterwards.

    Skips when Redis is unreachable (fails on CI). Only keys of this queue are removed; the
    database is never flushed.
    """
    from app.core import jobs as core_jobs  # noqa: PLC0415
    from app.core.config import get_settings  # noqa: PLC0415

    token = uuid.uuid4().hex[:12]
    queue = JobQueue(name=f"arq:queue:test-{token}", token=token)
    monkeypatch.setenv("WORKER_QUEUE_NAME", queue.name)
    get_settings.cache_clear()
    await core_jobs.close_jobs_pool()
    try:
        pool = await core_jobs.get_jobs_pool()
        await pool.ping()
    except Exception as exc:
        await core_jobs.close_jobs_pool()
        get_settings.cache_clear()
        message = f"Redis is not reachable ({type(exc).__name__})"
        if must_fail_when_db_unreachable(os.environ):
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    try:
        yield queue
    finally:
        pool = await core_jobs.get_jobs_pool()
        await delete_queue_keys(pool, queue)
        await core_jobs.close_jobs_pool()
        get_settings.cache_clear()
