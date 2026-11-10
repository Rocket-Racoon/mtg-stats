"""Health service tests (S-1.4.4): the readiness and liveness services with stubbed checks.

Whole-app tests (middleware, lifespan, OpenAPI) live in `apps/api/tests/test_health_acceptance.py`.
Checks are stubbed, so these tests never depend on how fast the shared Postgres or Redis answer.
"""

import asyncio
import logging
import time
from collections.abc import Callable

import pytest

from app.system import services
from app.system.services import health

SECRET = "s3cr3t-pw"


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
    """Replace the private checks (the one test seam), so a test controls each dependency."""
    if database is not None:
        monkeypatch.setattr("app.system.services.health._check_database", database)
    if redis is not None:
        monkeypatch.setattr("app.system.services.health._check_redis", redis)


async def test_default_per_check_timeout_is_2_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    async def finishes_at_1_5s() -> None:
        await asyncio.sleep(1.5)

    async def finishes_at_2_5s() -> None:
        await asyncio.sleep(2.5)

    _stub(monkeypatch, database=finishes_at_1_5s, redis=finishes_at_2_5s)
    report = await services.readiness("v")
    assert (report.checks.database, report.checks.redis) == ("ok", "error")


# ------------------------------------------------------------------ service-level checks


async def test_readiness_times_out_each_check_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.3)
    _stub(monkeypatch, database=_hang, redis=_hang)
    started = time.monotonic()
    report = await services.readiness("v")
    elapsed = time.monotonic() - started
    assert report.checks.database == "error"
    assert report.checks.redis == "error"
    assert 0.25 <= elapsed < 0.55  # concurrent: one timeout, not two


async def test_readiness_one_hanging_check_does_not_fail_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.2)
    _stub(monkeypatch, database=_ok, redis=_hang)
    report = await services.readiness("v")
    assert (report.status, report.checks.database, report.checks.redis) == ("error", "ok", "error")


async def test_readiness_check_exception_is_reported_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom() -> None:
        raise RuntimeError("down")

    _stub(monkeypatch, database=boom, redis=_ok)
    report = await services.readiness("v")
    assert (report.checks.database, report.checks.redis) == ("error", "ok")


async def test_failure_log_names_the_check_and_never_contains_credentials(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def leaky() -> None:
        raise RuntimeError(f"cannot connect to redis://:{SECRET}@cache:6379/0")

    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.1)
    _stub(monkeypatch, database=_hang, redis=leaky)
    with caplog.at_level(logging.WARNING, logger=health.logger.name):
        await services.readiness("v")
    assert "health check redis failed" in caplog.text
    assert "cache:6379" in caplog.text
    assert "health check database failed: timed out after" in caplog.text
    assert SECRET not in caplog.text
