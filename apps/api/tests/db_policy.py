"""Pure decision helpers for the database test fixtures (kept free of pytest for testability)."""

from collections.abc import Mapping

DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://mtg_stats:CHANGE_ME@127.0.0.1:5432/mtg_stats_test"
_FALSE_VALUES = {"", "0", "false", "no", "off"}


def resolve_test_database_url(env: Mapping[str, str]) -> str:
    """Return ``TEST_DATABASE_URL`` from ``env`` when set and non-empty, else the default."""
    return env.get("TEST_DATABASE_URL", "").strip() or DEFAULT_TEST_DATABASE_URL


def must_fail_when_db_unreachable(env: Mapping[str, str]) -> bool:
    """True when an unreachable Postgres must fail the test instead of skipping it.

    That is the case on CI (``CI`` set to anything but a false-like value) or when
    ``MTG_STATS_REQUIRE_DB=1``.
    """
    ci = env.get("CI", "").strip().lower() not in _FALSE_VALUES
    return ci or env.get("MTG_STATS_REQUIRE_DB", "").strip() == "1"
