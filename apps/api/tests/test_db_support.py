"""Tests for the DB test-support policy and the ``DB_CONNECT_TIMEOUT`` setting (S-1.4.2)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core import config
from app.core.config import get_settings, load_settings
from tests.db_policy import (
    DEFAULT_TEST_DATABASE_URL,
    must_fail_when_db_unreachable,
    resolve_test_database_url,
)

# ---------------------------------------------------------------- unreachable DB: fail or skip


def test_unreachable_db_skips_when_neither_ci_nor_require_db_is_set() -> None:
    assert must_fail_when_db_unreachable({}) is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "True"])
def test_unreachable_db_fails_when_ci_is_set(value: str) -> None:
    assert must_fail_when_db_unreachable({"CI": value}) is True


@pytest.mark.parametrize("value", ["", "0", "false"])
def test_ci_with_a_false_like_value_does_not_force_failure(value: str) -> None:
    assert must_fail_when_db_unreachable({"CI": value}) is False


def test_unreachable_db_fails_when_require_db_is_1() -> None:
    assert must_fail_when_db_unreachable({"MTG_STATS_REQUIRE_DB": "1"}) is True


@pytest.mark.parametrize("value", ["0", "", "true"])
def test_require_db_other_than_1_does_not_force_failure(value: str) -> None:
    assert must_fail_when_db_unreachable({"MTG_STATS_REQUIRE_DB": value}) is False


# ---------------------------------------------------------------- TEST_DATABASE_URL override


def test_default_test_database_url_targets_mtg_stats_test_over_psycopg3() -> None:
    assert resolve_test_database_url({}) == DEFAULT_TEST_DATABASE_URL
    assert DEFAULT_TEST_DATABASE_URL.startswith("postgresql+psycopg://")
    assert DEFAULT_TEST_DATABASE_URL.endswith("/mtg_stats_test")


def test_test_database_url_env_override_is_honoured() -> None:
    url = "postgresql+psycopg://u:p@db.internal:5433/other_test"
    assert resolve_test_database_url({"TEST_DATABASE_URL": url}) == url


def test_empty_test_database_url_falls_back_to_the_default() -> None:
    assert resolve_test_database_url({"TEST_DATABASE_URL": "  "}) == DEFAULT_TEST_DATABASE_URL


# ---------------------------------------------------------------- DB_CONNECT_TIMEOUT setting

DB_URL = "postgresql+psycopg://user:CHANGE_ME@db.example.com:5432/mtg"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Empty settings environment that never reads a real ``.env``."""
    for name in ("APP_ENV", "DATABASE_URL", "DB_CONNECT_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    missing = tmp_path / ".env"
    monkeypatch.setattr(config, "find_env_file", lambda: missing if missing.is_file() else None)
    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("clean_env")
def test_db_connect_timeout_defaults_to_5() -> None:
    assert load_settings().db_connect_timeout == 5


@pytest.mark.usefixtures("clean_env")
def test_db_connect_timeout_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "12")
    assert load_settings().db_connect_timeout == 12


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize("value", ["0", "-3"])
def test_db_connect_timeout_below_1_fails_validation_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", value)
    with pytest.raises(ValidationError, match="DB_CONNECT_TIMEOUT"):
        load_settings()


@pytest.mark.usefixtures("clean_env")
def test_db_connect_timeout_1_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "1")
    assert load_settings().db_connect_timeout == 1
