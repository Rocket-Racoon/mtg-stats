"""Health endpoint tests (S-1.4.4). Dependencies are "down" via closed ports or hanging checks."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest

from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine, get_sessionmaker
from app.core.redis import close_redis, get_redis
from app.system import services

CLOSED_PORT = "127.0.0.1:1"  # nothing listens on port 1: connection refused at once
DOWN_DATABASE = {"DATABASE_URL": f"postgresql+psycopg://u:p@{CLOSED_PORT}/x"}
DOWN_REDIS = {"REDIS_URL": f"redis://{CLOSED_PORT}/0"}

HealthClient = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


async def _reset_cached_dependencies() -> None:
    """Drop the cached settings, engine and Redis client (they are cached per process)."""
    await dispose_engine()
    await close_redis()
    get_settings.cache_clear()
    get_sessionmaker.cache_clear()


@pytest.fixture
def health_client(monkeypatch: pytest.MonkeyPatch) -> HealthClient:
    """Factory for a client on a fresh app, with the given env overrides applied first."""

    @asynccontextmanager
    async def make(**env: str) -> AsyncIterator[httpx.AsyncClient]:
        from app.main import create_app  # noqa: PLC0415  (import builds the app; env set by conftest)

        for name, value in env.items():
            monkeypatch.setenv(name, value)
        await _reset_cached_dependencies()
        transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                yield client
        finally:
            await _reset_cached_dependencies()

    return make


async def _warm_up_pool() -> None:
    """Open the first Postgres connection outside the 2 s check budget.

    A cold first connection to the Docker container can take over 2 s on Windows, which would
    make the happy-path assertions flaky. The pooled connection is reused by the health check.
    """
    try:
        async with get_engine().connect():
            pass
    except Exception:  # noqa: S110  (unreachable DB: the assertions below report it)
        pass


async def test_live_returns_ok_without_touching_dependencies(health_client: HealthClient) -> None:
    async with health_client(**DOWN_DATABASE, **DOWN_REDIS) as http:
        response = await http.get("/api/v1/health/live")
        assert not get_engine.cache_info().currsize
        assert not get_redis.cache_info().currsize
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_ok_with_real_postgres_and_redis(health_client: HealthClient) -> None:
    async with health_client() as http:
        await _warm_up_pool()
        response = await http.get("/api/v1/health/ready")
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok"},
        "version": "dev",
    }
    assert response.status_code == 200


async def test_ready_reports_git_sha_as_version(health_client: HealthClient) -> None:
    async with health_client(GIT_SHA="abc1234") as http:
        body = (await http.get("/api/v1/health/ready")).json()
    assert body["version"] == "abc1234"


async def test_ready_503_when_redis_is_down(health_client: HealthClient) -> None:
    async with health_client(**DOWN_REDIS) as http:
        await _warm_up_pool()
        response = await http.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "error"
    assert response.json()["checks"] == {"database": "ok", "redis": "error"}


async def test_ready_503_when_postgres_is_down(health_client: HealthClient) -> None:
    async with health_client(**DOWN_DATABASE) as http:
        started = time.monotonic()
        response = await http.get("/api/v1/health/ready")
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "error", "redis": "ok"}
    assert elapsed < 3.0


async def _hang() -> None:
    await asyncio.sleep(30)


async def _ok() -> None:
    return None


async def test_readiness_times_out_each_check_concurrently() -> None:
    started = time.monotonic()
    report = await services.readiness(
        "v", timeout_seconds=0.3, database_check=_hang, redis_check=_hang
    )
    elapsed = time.monotonic() - started
    assert report.checks.database == "error"
    assert report.checks.redis == "error"
    assert 0.25 <= elapsed < 0.55  # concurrent: one timeout, not two


async def test_readiness_one_hanging_check_does_not_fail_the_other() -> None:
    report = await services.readiness(
        "v", timeout_seconds=0.2, database_check=_ok, redis_check=_hang
    )
    assert (report.status, report.checks.database, report.checks.redis) == ("error", "ok", "error")


async def test_readiness_check_exception_is_reported_as_error() -> None:
    async def boom() -> None:
        raise RuntimeError("down")

    report = await services.readiness("v", database_check=boom, redis_check=_ok)
    assert (report.checks.database, report.checks.redis) == ("error", "ok")


async def test_ready_returns_503_within_3s_when_both_hang_at_default_timeout(
    health_client: HealthClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.system.services.health._check_database", _hang)
    monkeypatch.setattr("app.system.services.health._check_redis", _hang)
    async with health_client() as http:
        started = time.monotonic()
        response = await http.get("/api/v1/health/ready")
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "error", "redis": "error"}
    assert 1.9 <= elapsed < 3.0
