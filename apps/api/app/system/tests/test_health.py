"""Health endpoint tests (S-1.4.4).

Tests that prove "one dependency is down" stub the healthy side, so they never depend on how
fast the shared Docker Postgres or Redis answer. Real-dependency tests are the happy path and
the closed-port tests, which assert only about the dependency under test.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine, get_sessionmaker
from app.core.redis import close_redis, get_redis
from app.system import services

CLOSED_PORT = "127.0.0.1:1"  # nothing listens on port 1: connection refused at once
DOWN_DATABASE = {"DATABASE_URL": f"postgresql+psycopg://u:p@{CLOSED_PORT}/x"}
DOWN_REDIS = {"REDIS_URL": f"redis://{CLOSED_PORT}/0"}
READY = "/api/v1/health/ready"
LIVE = "/api/v1/health/live"

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


async def _hang() -> None:
    await asyncio.sleep(30)


async def _ok() -> None:
    return None


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    database: Callable[[], object] | None = None,
    redis: Callable[[], object] | None = None,
) -> None:
    """Replace the built-in checks, so a test controls what each dependency does."""
    if database is not None:
        monkeypatch.setattr("app.system.services.health._check_database", database)
    if redis is not None:
        monkeypatch.setattr("app.system.services.health._check_redis", redis)


# ------------------------------------------------------------------ AC1: live


async def test_live_returns_ok_without_touching_dependencies(health_client: HealthClient) -> None:
    async with health_client(**DOWN_DATABASE, **DOWN_REDIS) as http:
        response = await http.get(LIVE)
        assert not get_engine.cache_info().currsize
        assert not get_redis.cache_info().currsize
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_live_never_runs_a_dependency_check(
    health_client: HealthClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    boom = AsyncMock(side_effect=AssertionError("live must not check dependencies"))
    _stub(monkeypatch, database=boom, redis=boom)
    async with health_client(**DOWN_DATABASE, **DOWN_REDIS) as http:
        response = await http.get(LIVE)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    boom.assert_not_called()


async def test_live_is_fast_when_dependencies_hang(
    health_client: HealthClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, database=_hang, redis=_hang)
    async with health_client() as http:
        started = time.monotonic()
        response = await http.get(LIVE)
        elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert elapsed < 0.5


# ------------------------------------------------------------------ AC2: ready, healthy


async def test_ready_ok_with_real_postgres_and_redis(health_client: HealthClient) -> None:
    async with health_client() as http:
        await _warm_up_pool()
        await get_redis().ping()  # warm the Redis connection too: the check budget is 2 s
        response = await http.get(READY)
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok"},
        "version": "dev",
    }
    assert response.status_code == 200


async def test_ready_response_has_exactly_the_documented_shape(
    health_client: HealthClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, database=_ok, redis=_ok)
    async with health_client() as http:
        body = (await http.get(READY)).json()
    assert set(body) == {"status", "checks", "version"}
    assert set(body["checks"]) == {"database", "redis"}


async def test_ready_reports_git_sha_as_version(
    health_client: HealthClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, database=_ok, redis=_ok)
    async with health_client(GIT_SHA="abc1234") as http:
        body = (await http.get(READY)).json()
    assert body["version"] == "abc1234"


async def test_ready_version_is_dev_when_git_sha_is_unset(
    health_client: HealthClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    _stub(monkeypatch, database=_ok, redis=_ok)
    async with health_client() as http:
        body = (await http.get(READY)).json()
    assert body["version"] == "dev"


# ------------------------------------------------------------------ AC3: ready, a dependency down


async def test_ready_503_when_redis_is_down(
    health_client: HealthClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, database=_ok)  # healthy side stubbed: only Redis is under test
    async with health_client(**DOWN_REDIS) as http:
        response = await http.get(READY)
    assert response.status_code == 503
    assert response.json()["status"] == "error"
    assert response.json()["checks"] == {"database": "ok", "redis": "error"}


async def test_ready_503_when_postgres_is_down(
    health_client: HealthClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, redis=_ok)  # healthy side stubbed: only Postgres is under test
    async with health_client(**DOWN_DATABASE) as http:
        started = time.monotonic()
        response = await http.get(READY)
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json()["status"] == "error"
    assert response.json()["checks"] == {"database": "error", "redis": "ok"}
    assert elapsed < 3.0


async def test_ready_real_closed_redis_port_reports_redis_error(
    health_client: HealthClient,
) -> None:
    async with health_client(**DOWN_REDIS) as http:
        response = await http.get(READY)
    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "error"


async def test_ready_real_closed_postgres_port_reports_database_error(
    health_client: HealthClient,
) -> None:
    async with health_client(**DOWN_DATABASE) as http:
        response = await http.get(READY)
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "error"


@pytest.mark.parametrize("hanging", ["database", "redis"])
async def test_ready_503_within_3s_when_one_dependency_hangs(
    health_client: HealthClient, monkeypatch: pytest.MonkeyPatch, hanging: str
) -> None:
    _stub(
        monkeypatch,
        database=_hang if hanging == "database" else _ok,
        redis=_hang if hanging == "redis" else _ok,
    )
    async with health_client() as http:
        started = time.monotonic()
        response = await http.get(READY)
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "ok", hanging: "error"}
    assert 1.9 <= elapsed < 3.0


async def test_ready_returns_503_within_3s_when_both_hang_at_default_timeout(
    health_client: HealthClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, database=_hang, redis=_hang)
    async with health_client() as http:
        started = time.monotonic()
        response = await http.get(READY)
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "error", "redis": "error"}
    assert 1.9 <= elapsed < 3.0  # concurrent: one 2 s timeout, not two


async def test_default_per_check_timeout_is_2_seconds() -> None:
    async def finishes_at_1_5s() -> None:
        await asyncio.sleep(1.5)

    async def finishes_at_2_5s() -> None:
        await asyncio.sleep(2.5)

    report = await services.readiness(
        "v", database_check=finishes_at_1_5s, redis_check=finishes_at_2_5s
    )
    assert (report.checks.database, report.checks.redis) == ("ok", "error")


# ------------------------------------------------------------------ service-level checks


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


# ------------------------------------------------------------------ OpenAPI and shutdown


def test_openapi_documents_200_and_503_with_the_ready_schema() -> None:
    from app.main import create_app  # noqa: PLC0415

    operation = create_app().openapi()["paths"]["/api/v1/health/ready"]["get"]
    for code in ("200", "503"):
        ref = operation["responses"][code]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/ReadyResponse")


async def test_redis_client_and_engine_are_closed_on_app_shutdown(
    health_client: HealthClient,
) -> None:
    from app.main import create_app  # noqa: PLC0415

    async with health_client():
        app = create_app()
        async with app.router.lifespan_context(app):
            client = get_redis()
            get_engine()
            aclose = AsyncMock()
            client.aclose = aclose  # type: ignore[method-assign]
            assert get_redis.cache_info().currsize
        aclose.assert_awaited_once()
        assert not get_redis.cache_info().currsize
        assert not get_engine.cache_info().currsize
