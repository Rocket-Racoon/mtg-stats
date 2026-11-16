"""Unit tests of ``core.rate_limit`` internals that need no Redis."""

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core import rate_limit


def test_client_ip_falls_back_to_unknown_without_a_client() -> None:
    assert rate_limit.client_ip(SimpleNamespace(client=None)) == "unknown"  # type: ignore[arg-type]
    request: Any = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4"))
    assert rate_limit.client_ip(request) == "1.2.3.4"


async def test_acquire_fails_open_and_logs_one_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> None:
        raise RedisConnectionError("secret-host")

    monkeypatch.setattr(rate_limit, "get_redis", broken)
    with caplog.at_level(logging.WARNING, logger="app.core.rate_limit"):
        assert await rate_limit.acquire([("k", 1)]) == rate_limit.Allowed(counted=False)
    assert len(caplog.records) == 1
    assert "secret-host" not in caplog.text
