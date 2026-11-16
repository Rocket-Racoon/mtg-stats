"""Unit tests of ``core.rate_limit`` internals that need no Redis."""

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core import rate_limit


def test_remaining_uses_window_when_ttl_is_missing_or_negative() -> None:
    assert rate_limit._remaining(-1, 900) == 900
    assert rate_limit._remaining(-2, 900) == 900
    assert rate_limit._remaining(None, 900) == 900


def test_remaining_is_at_least_one_second() -> None:
    assert rate_limit._remaining(0, 900) == 1
    assert rate_limit._remaining(42, 900) == 42


def test_client_ip_falls_back_to_unknown_without_a_client() -> None:
    assert rate_limit.client_ip(SimpleNamespace(client=None)) == "unknown"  # type: ignore[arg-type]
    request: Any = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4"))
    assert rate_limit.client_ip(request) == "1.2.3.4"


async def test_hit_fails_open_and_logs_one_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> None:
        raise RedisConnectionError("secret-host")

    monkeypatch.setattr(rate_limit, "get_redis", broken)
    with caplog.at_level(logging.WARNING, logger="app.core.rate_limit"):
        assert await rate_limit.hit(["k"]) is None
    assert len(caplog.records) == 1
    assert "secret-host" not in caplog.text
