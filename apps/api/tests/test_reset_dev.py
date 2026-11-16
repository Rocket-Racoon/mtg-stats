"""Tests for the dev reset command and the seed registry (S-1.3.4).

Unit tests use no database. The integration tests use a throwaway ``reset_dev_e2e_<random>``
database on the test server (never the dev database) and skip, or fail on CI, when Postgres is
unreachable.
"""

import asyncio
import importlib
import os
import re
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

import app as app_package
from app.core import config as core_config
from app.core import db as core_db
from app.core import seeding
from app.scripts import migrate_dev, reset_dev
from tests.db_policy import must_fail_when_db_unreachable, resolve_test_database_url

GOOD_URL = "postgresql+psycopg://u:CHANGE_ME@localhost:5432/mtg"  # secrets-check: allow


def _env(url: str = GOOD_URL, app_env: str = "dev") -> dict[str, str]:
    return {"APP_ENV": app_env, "DATABASE_URL": url}


def _url_with_host(host: str) -> str:
    return f"postgresql+psycopg://u:CHANGE_ME@{host}:5432/mtg"  # secrets-check: allow


class Calls:
    """Records the order in which the injected callables ran."""

    def __init__(self, seeded: int = 0) -> None:
        self.calls: list[str] = []
        self.recreate_urls: list[str] = []
        self._seeded = seeded

    def recreate(self, url: str) -> None:
        self.calls.append("recreate")
        self.recreate_urls.append(url)

    def upgrade(self) -> None:
        self.calls.append("upgrade")

    def seed(self) -> int:
        self.calls.append("seed")
        return self._seeded

    def run(self, environ: Mapping[str, str] | None) -> int:
        return reset_dev.main(environ, self.recreate, self.upgrade, self.seed)


@pytest.fixture(autouse=True)
def isolated_registry() -> Iterator[None]:
    """Start each test with an empty seeder registry; put the real registrations back after."""
    snapshot = seeding.snapshot_seeders()
    seeding.clear_seeders()
    yield
    seeding.restore_seeders(snapshot)


# ---------------------------------------------------------------- guard: APP_ENV


@pytest.mark.parametrize("value", ["prod", "test", "", "Dev", " dev", "development"])
def test_refuses_when_app_env_is_not_exactly_dev(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Calls()
    assert rec.run(_env(app_env=value)) == reset_dev.EXIT_REFUSED
    assert rec.calls == []
    err = capsys.readouterr().err
    assert "reset refused:" in err
    assert "APP_ENV" in err
    assert "The database was not touched." in err


def test_refuses_when_app_env_is_unset() -> None:
    rec = Calls()
    assert rec.run({"DATABASE_URL": GOOD_URL}) == reset_dev.EXIT_REFUSED
    assert rec.calls == []


def test_exit_refused_is_one() -> None:
    assert reset_dev.EXIT_REFUSED == 1


# ---------------------------------------------------------------- guard: host


@pytest.mark.parametrize(
    "host",
    [
        "db.example.com",
        "10.0.0.5",
        "localhost.evil.com",
        "evil-localhost",
        "postgres.example.com",
        "127.0.0.2",
        "127.0.0.1.evil.com",
        "LOCALHOST",
        "[::1]",
        "0.0.0.0",  # noqa: S104
    ],
)
def test_refuses_when_host_not_in_allow_list(host: str, capsys: pytest.CaptureFixture[str]) -> None:
    rec = Calls()
    assert rec.run(_env(_url_with_host(host))) == reset_dev.EXIT_REFUSED
    assert rec.calls == []
    err = capsys.readouterr().err
    assert "reset refused:" in err
    assert "host" in err
    assert "The database was not touched." in err


@pytest.mark.parametrize(
    "host",
    [
        "localhost.",
        "localhost,db.example.com",
        "db.example.com@localhost",
        "%6cocalhost",
        "%2Fvar%2Frun%2Fpostgresql",
    ],
)
def test_refuses_host_bypass_shapes_that_resemble_localhost(host: str) -> None:
    rec = Calls()
    environ = _env(_url_with_host(host))
    reason = reset_dev.check_guard(environ)
    assert reason is not None
    assert "host" in reason
    assert rec.run(environ) == reset_dev.EXIT_REFUSED
    assert rec.calls == []


@pytest.mark.parametrize(
    "scheme", ["mysql", "postgresql", "postgresql+asyncpg", "postgresql+psycopg2", "sqlite"]
)
def test_refuses_a_driver_that_is_not_postgresql_psycopg(
    scheme: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Calls()
    url = f"{scheme}://u:CHANGE_ME@localhost:5432/mtg"  # secrets-check: allow
    reason = reset_dev.check_guard(_env(url))
    assert reason is not None
    assert "postgresql+psycopg://" in reason
    assert rec.run(_env(url)) == reset_dev.EXIT_REFUSED
    assert rec.calls == []
    assert "postgresql+psycopg://" in capsys.readouterr().err


def test_app_env_refusal_reason_names_the_process_environment_and_dev_env() -> None:
    reason = reset_dev.check_guard(_env(app_env="prod"))
    assert reason == "APP_ENV is not set to dev (process environment or the dev .env)"


def test_refuses_when_host_only_appears_in_the_password_or_query() -> None:
    rec = Calls()
    url = (
        "postgresql+psycopg://localhost:localhost@db.example.com:5432/mtg"  # secrets-check: allow
        "?host=localhost"
    )
    assert rec.run(_env(url)) == reset_dev.EXIT_REFUSED
    assert rec.calls == []


def test_refuses_when_url_has_no_host() -> None:
    rec = Calls()
    assert rec.run(_env("postgresql+psycopg:///mtg")) == reset_dev.EXIT_REFUSED
    assert rec.calls == []


def test_refuses_when_database_url_is_missing(capsys: pytest.CaptureFixture[str]) -> None:
    rec = Calls()
    assert rec.run({"APP_ENV": "dev"}) == reset_dev.EXIT_REFUSED
    assert rec.calls == []
    assert "DATABASE_URL" in capsys.readouterr().err


def test_refuses_when_database_url_is_empty() -> None:
    rec = Calls()
    assert rec.run(_env("")) == reset_dev.EXIT_REFUSED
    assert rec.calls == []


@pytest.mark.parametrize("raw", ["not a url", "://", "postgresql+psycopg://[bad", "localhost"])
def test_refuses_when_database_url_cannot_be_parsed(raw: str) -> None:
    rec = Calls()
    assert rec.run(_env(raw)) == reset_dev.EXIT_REFUSED
    assert rec.calls == []


def test_refuses_when_database_name_is_missing() -> None:
    rec = Calls()
    url = "postgresql+psycopg://u:CHANGE_ME@localhost:5432"  # secrets-check: allow
    assert rec.run(_env(url)) == reset_dev.EXIT_REFUSED
    assert rec.calls == []


def test_refusal_for_wrong_env_wins_even_with_an_allowed_host() -> None:
    rec = Calls()
    assert rec.run(_env(GOOD_URL, app_env="prod")) == reset_dev.EXIT_REFUSED
    assert rec.calls == []


@pytest.mark.parametrize(
    "query",
    [
        "?host=db.example.com",
        "?hostaddr=10.0.0.5",
        "?service=prod",
        "?sslmode=disable",
        "?host=localhost",
    ],
)
def test_refuses_any_query_string_even_with_a_localhost_host(
    query: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Calls()
    assert rec.run(_env(GOOD_URL + query)) == reset_dev.EXIT_REFUSED
    assert rec.calls == []
    err = capsys.readouterr().err
    assert "reset refused:" in err
    assert "query" in err
    assert "The database was not touched." in err


@pytest.mark.parametrize("var", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
def test_refuses_when_a_forbidden_libpq_variable_is_set_and_names_it(
    var: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Calls()
    environ = {**_env(), var: "10.0.0.5"}
    assert rec.run(environ) == reset_dev.EXIT_REFUSED
    assert rec.calls == []
    reason = reset_dev.check_guard(environ)
    assert reason is not None
    assert var in reason
    err = capsys.readouterr().err
    assert var in err
    assert "The database was not touched." in err


def test_forbidden_libpq_variables_are_exactly_hostaddr_service_and_servicefile() -> None:
    assert set(reset_dev.FORBIDDEN_LIBPQ_VARS) == {"PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"}


@pytest.mark.parametrize("name", ["postgres", "template0", "template1"])
def test_refuses_system_database_names(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    rec = Calls()
    url = f"postgresql+psycopg://u:CHANGE_ME@localhost:5432/{name}"  # secrets-check: allow
    assert rec.run(_env(url)) == reset_dev.EXIT_REFUSED
    assert rec.calls == []
    err = capsys.readouterr().err
    assert name in err
    assert "The database was not touched." in err


def test_system_databases_are_exactly_postgres_template0_template1() -> None:
    assert set(reset_dev.SYSTEM_DATABASES) == {"postgres", "template0", "template1"}


def test_refusal_does_not_read_settings_or_touch_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched: list[str] = []

    def tripwire(name: str) -> Callable[..., Any]:
        def trip(*_a: Any, **_k: Any) -> None:
            touched.append(name)
            raise AssertionError(f"{name} must not be reached on refusal")

        return trip

    monkeypatch.setattr(psycopg, "connect", tripwire("psycopg.connect"))
    monkeypatch.setattr(reset_dev, "get_sessionmaker", tripwire("get_sessionmaker"))
    monkeypatch.setattr(core_config, "get_settings", tripwire("config.get_settings"))
    monkeypatch.setattr(core_config, "load_settings", tripwire("config.load_settings"))
    monkeypatch.setattr(reset_dev, "get_settings", tripwire("reset_dev.get_settings"))
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", _url_with_host("db.example.com"))
    # Real default callables are used (main() with no injection); none may run.
    assert reset_dev.main() == reset_dev.EXIT_REFUSED
    assert touched == []


def test_refusal_with_default_callables_for_a_bad_host_touches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("must not connect on refusal")

    monkeypatch.setattr(psycopg, "connect", boom)
    assert reset_dev.main(_env(_url_with_host("10.0.0.5"))) == reset_dev.EXIT_REFUSED


def test_check_guard_returns_a_reason_string_when_refusing() -> None:
    reason = reset_dev.check_guard(_env(app_env="prod"))
    assert isinstance(reason, str)
    assert reason


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "postgres"])
def test_check_guard_returns_none_for_allowed_hosts(host: str) -> None:
    assert reset_dev.check_guard(_env(_url_with_host(host))) is None


def test_allow_list_is_exactly_localhost_loopback_and_compose_postgres() -> None:
    assert frozenset(reset_dev.ALLOWED_HOSTS) == {"localhost", "127.0.0.1", "postgres"}


# ---------------------------------------------------------------- allowed path


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "postgres"])
def test_allowed_host_recreates_upgrades_then_seeds_and_exits_zero(
    host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Calls(seeded=3)
    url = _url_with_host(host)
    assert rec.run(_env(url)) == 0
    assert rec.calls == ["recreate", "upgrade", "seed"]
    assert rec.recreate_urls == [url]
    out_lines = capsys.readouterr().out.strip().splitlines()
    assert out_lines[-1] == "seeded: 3 seeders"


def test_zero_seeders_reports_zero_seeders(capsys: pytest.CaptureFixture[str]) -> None:
    rec = Calls(seeded=0)
    assert rec.run(_env()) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "seeded: 0 seeders"


def test_a_failing_recreate_exits_failed_and_later_steps_do_not_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rec = Calls()

    def bad_recreate(_url: str) -> None:
        raise RuntimeError("connection refused")

    assert reset_dev.main(_env(), bad_recreate, rec.upgrade, rec.seed) == reset_dev.EXIT_FAILED
    assert rec.calls == []
    err = capsys.readouterr().err
    assert "reset failed while recreating the database" in err
    assert "connection refused" in err


def test_a_failing_migration_exits_failed_and_seeding_does_not_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rec = Calls()

    def bad_upgrade() -> None:
        raise RuntimeError("migration failed")

    assert reset_dev.main(_env(), rec.recreate, bad_upgrade, rec.seed) == reset_dev.EXIT_FAILED
    assert rec.calls == ["recreate"]
    err = capsys.readouterr().err
    assert "database was recreated but migration failed" in err
    assert "migration failed" in err


def test_a_failing_seed_exits_failed_and_says_seeding_failed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rec = Calls()

    def bad_seed() -> int:
        raise RuntimeError("seeder exploded")

    assert reset_dev.main(_env(), rec.recreate, rec.upgrade, bad_seed) == reset_dev.EXIT_FAILED
    assert rec.calls == ["recreate", "upgrade"]
    err = capsys.readouterr().err
    assert "database was recreated but seeding failed" in err
    assert "seeder exploded" in err


def test_exit_failed_is_three_and_distinct_from_refused() -> None:
    assert reset_dev.EXIT_FAILED == 3
    assert reset_dev.EXIT_FAILED != reset_dev.EXIT_REFUSED


def test_recreate_receives_the_validated_canonical_url() -> None:
    rec = Calls()
    url = "postgresql+psycopg://u:p%40ss@localhost:5432/mtg"  # secrets-check: allow
    assert rec.run(_env(url)) == 0
    assert rec.recreate_urls == [make_url(url).render_as_string(hide_password=False)]


def _valid_settings_env(monkeypatch: pytest.MonkeyPatch, url: str = GOOD_URL) -> None:
    """Make every required setting valid, ignoring any repo ``.env``."""
    monkeypatch.setattr(reset_dev, "_dev_env_file", lambda: None)
    monkeypatch.setattr(core_config, "dev_env_file", lambda: None)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("PUBLIC_WEB_URL", "https://example.com")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    core_config.get_settings.cache_clear()


def test_default_environ_exports_app_env_and_database_url_and_clears_the_settings_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _valid_settings_env(monkeypatch)
    cleared: list[bool] = []
    real_clear = reset_dev.get_settings.cache_clear

    def spy_clear() -> None:
        cleared.append(True)
        real_clear()

    monkeypatch.setattr(reset_dev.get_settings, "cache_clear", spy_clear)
    rec = Calls()
    try:
        assert reset_dev.main(None, rec.recreate, rec.upgrade, rec.seed) == 0
    finally:
        real_clear()
    assert os.environ["APP_ENV"] == "dev"
    assert os.environ["DATABASE_URL"] == GOOD_URL
    assert cleared == [True]


@pytest.mark.parametrize(
    ("var", "value"),
    [("REDIS_URL", ""), ("JWT_SECRET", "SUPERSECRETVALUE-short"), ("PUBLIC_WEB_URL", "")],
)
def test_real_run_refuses_invalid_settings_before_dropping_anything(
    var: str, value: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _valid_settings_env(monkeypatch)
    monkeypatch.setenv(var, value)
    rec = Calls()
    try:
        code = reset_dev.main(None, rec.recreate, rec.upgrade, rec.seed)
    finally:
        core_config.get_settings.cache_clear()
    assert code == reset_dev.EXIT_REFUSED
    assert rec.calls == []
    captured = capsys.readouterr()
    assert "reset refused: invalid settings (" in captured.err
    assert "The database was not touched." in captured.err
    names = captured.err.split("invalid settings (", 1)[1].split(")", 1)[0]
    assert names == var
    if value:
        assert value not in captured.out + captured.err


def test_missing_required_setting_is_named_without_any_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _valid_settings_env(monkeypatch)
    monkeypatch.delenv("JWT_SECRET")
    rec = Calls()
    try:
        code = reset_dev.main(None, rec.recreate, rec.upgrade, rec.seed)
    finally:
        core_config.get_settings.cache_clear()
    assert code == reset_dev.EXIT_REFUSED
    assert rec.calls == []
    err = capsys.readouterr().err
    assert "invalid settings (JWT_SECRET)" in err


def test_injected_environ_is_not_exported_to_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    rec = Calls()
    assert rec.run(_env()) == 0
    assert "DATABASE_URL" not in os.environ


# ---------------------------------------------------------------- seed registry


async def _noop(_session: Any) -> None:
    return None


def test_no_registered_seeders_means_empty_registry() -> None:
    assert seeding.registered_seeders() == []


async def test_run_seeders_with_none_registered_returns_zero_and_is_a_no_op() -> None:
    assert await seeding.run_seeders(object()) == 0  # type: ignore[arg-type]


def test_registered_seeders_lists_names_in_registration_order() -> None:
    for name in ["zeta", "alpha", "mid"]:
        seeding.register_seeder(name, _noop)
    assert seeding.registered_seeders() == ["zeta", "alpha", "mid"]


def test_registering_a_duplicate_name_raises_value_error_and_keeps_the_first() -> None:
    seeding.register_seeder("dup", _noop)

    async def other(_session: Any) -> None:
        return None

    with pytest.raises(ValueError, match="dup"):
        seeding.register_seeder("dup", other)
    assert seeding.registered_seeders() == ["dup"]


def test_clear_seeders_empties_the_registry_and_allows_reregistering() -> None:
    seeding.register_seeder("a", _noop)
    seeding.clear_seeders()
    assert seeding.registered_seeders() == []
    seeding.register_seeder("a", _noop)
    assert seeding.registered_seeders() == ["a"]


def test_registered_seeders_returns_a_copy() -> None:
    seeding.register_seeder("a", _noop)
    names = seeding.registered_seeders()
    names.append("hacked")
    assert seeding.registered_seeders() == ["a"]


async def test_run_seeders_calls_each_in_registration_order_and_returns_count() -> None:
    order: list[str] = []

    def make(name: str) -> Callable[[Any], Any]:
        async def seeder(_session: Any) -> None:
            order.append(name)

        return seeder

    for name in ["second", "first", "third"]:
        seeding.register_seeder(name, make(name))
    assert await seeding.run_seeders(object()) == 3  # type: ignore[arg-type]
    assert order == ["second", "first", "third"]


async def test_run_seeders_passes_the_given_session_to_every_seeder() -> None:
    sentinel = object()
    received: list[object] = []

    async def seeder(session: Any) -> None:
        received.append(session)

    seeding.register_seeder("one", seeder)
    seeding.register_seeder("two", seeder)
    await seeding.run_seeders(sentinel)  # type: ignore[arg-type]
    assert received == [sentinel, sentinel]


async def test_run_seeders_does_not_commit() -> None:
    class Session:
        committed = False

        async def commit(self) -> None:
            self.committed = True

    session = Session()
    seeding.register_seeder("a", _noop)
    await seeding.run_seeders(session)  # type: ignore[arg-type]
    assert session.committed is False


async def test_a_failing_seeder_propagates_its_error() -> None:
    async def boom(_session: Any) -> None:
        raise RuntimeError("seed broke")

    seeding.register_seeder("boom", boom)
    with pytest.raises(RuntimeError, match="seed broke"):
        await seeding.run_seeders(object())  # type: ignore[arg-type]


async def test_run_seeders_returns_the_pre_call_count_when_a_seeder_registers_another() -> None:
    ran: list[str] = []

    async def late(_session: Any) -> None:
        ran.append("late")

    async def first(_session: Any) -> None:
        ran.append("first")
        seeding.register_seeder("late", late)

    seeding.register_seeder("first", first)
    assert await seeding.run_seeders(object()) == 1  # type: ignore[arg-type]
    assert ran == ["first"]
    assert seeding.registered_seeders() == ["first", "late"]


def test_restore_seeders_puts_back_a_snapshot_and_drops_later_registrations() -> None:
    seeding.register_seeder("kept", _noop)
    snapshot = seeding.snapshot_seeders()
    seeding.register_seeder("extra", _noop)
    seeding.restore_seeders(snapshot)
    assert seeding.registered_seeders() == ["kept"]


def test_seeders_do_not_leak_between_tests_first() -> None:
    seeding.register_seeder("leak-check", _noop)


def test_seeders_do_not_leak_between_tests_second() -> None:
    assert "leak-check" not in seeding.registered_seeders()


# ---------------------------------------------------------------- argparse and cli


def test_an_extra_argument_exits_two() -> None:
    with pytest.raises(SystemExit) as exc:
        reset_dev.build_parser().parse_args(["unexpected"])
    assert exc.value.code == 2


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        reset_dev.build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    assert "localhost" in capsys.readouterr().out


def test_cli_with_no_arguments_calls_main_and_returns_its_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_main() -> int:
        calls.append("main")
        return 7

    monkeypatch.setattr(reset_dev, "main", fake_main)
    assert reset_dev.cli([]) == 7
    assert calls == ["main"]


def test_cli_with_an_extra_argument_exits_two_without_running_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_main() -> int:
        raise AssertionError("main must not run")

    monkeypatch.setattr(reset_dev, "main", fake_main)
    with pytest.raises(SystemExit) as exc:
        reset_dev.cli(["unexpected"])
    assert exc.value.code == 2


# ---------------------------------------------------------------- resolve_environ


def _write_env_file(tmp_path: Path, app_env: str, url: str, extra: str = "") -> Path:
    path = tmp_path / ".env"
    path.write_text(f"APP_ENV={app_env}\nDATABASE_URL={url}\n{extra}", encoding="utf-8")
    return path


def test_resolve_environ_process_values_win_over_the_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = _write_env_file(tmp_path, "dev", _url_with_host("db.example.com"))
    monkeypatch.setattr(reset_dev, "_dev_env_file", lambda: env_file)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", GOOD_URL)
    resolved = reset_dev.resolve_environ()
    assert resolved["APP_ENV"] == "dev"
    assert resolved["DATABASE_URL"] == GOOD_URL


def test_resolve_environ_fills_missing_values_from_the_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = _write_env_file(tmp_path, "dev", GOOD_URL)
    monkeypatch.setattr(reset_dev, "_dev_env_file", lambda: env_file)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    resolved = reset_dev.resolve_environ()
    assert resolved["APP_ENV"] == "dev"
    assert resolved["DATABASE_URL"] == GOOD_URL


def test_resolve_environ_ignores_the_env_file_when_no_dev_env_file_applies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_env_file(tmp_path, "dev", GOOD_URL)
    monkeypatch.setattr(reset_dev, "_dev_env_file", lambda: None)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    resolved = reset_dev.resolve_environ()
    assert "APP_ENV" not in resolved
    assert "DATABASE_URL" not in resolved


@pytest.mark.parametrize("var", ["PGSERVICE", "PGHOSTADDR", "PGSERVICEFILE"])
def test_resolve_environ_does_not_pull_other_env_file_keys(
    var: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = _write_env_file(tmp_path, "dev", GOOD_URL, extra=f"{var}=10.0.0.5\n")
    monkeypatch.setattr(reset_dev, "_dev_env_file", lambda: env_file)
    monkeypatch.delenv(var, raising=False)
    assert var not in reset_dev.resolve_environ()


def test_reset_dev_does_not_use_python_dotenv() -> None:
    source = Path(reset_dev.__file__).read_text(encoding="utf-8")
    assert "dotenv_values" not in source
    assert "import dotenv" not in source
    assert "from dotenv" not in source
    assert not hasattr(reset_dev, "dotenv_values")


# ---------------------------------------------------------------- recreate and event loop


def test_recreate_database_connects_with_a_timeout_and_an_empty_hostaddr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_connect(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise psycopg.OperationalError("stop here")

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    with pytest.raises(psycopg.OperationalError):
        reset_dev.recreate_database(GOOD_URL)
    assert reset_dev.CONNECT_TIMEOUT == 5
    assert captured["connect_timeout"] == reset_dev.CONNECT_TIMEOUT
    assert captured["hostaddr"] == ""
    assert captured["dbname"] == "postgres"
    assert captured["host"] == "localhost"
    assert captured["autocommit"] is True


@pytest.mark.parametrize(
    ("platform", "expect_selector"), [("win32", True), ("linux", False), ("darwin", False)]
)
def test_run_seeding_uses_the_selector_event_loop_only_on_windows(
    platform: str, expect_selector: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(coro: Any, **kwargs: Any) -> int:
        coro.close()
        captured.update(kwargs)
        return 4

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(reset_dev.asyncio, "run", fake_run)
    assert reset_dev.run_seeding() == 4
    if expect_selector:
        assert captured["loop_factory"] is asyncio.SelectorEventLoop
    else:
        assert captured["loop_factory"] is None


# ---------------------------------------------------------------- seed domain discovery


def test_discover_seed_domains_matches_the_domain_directories_on_disk() -> None:
    app_dir = Path(app_package.__file__).resolve().parent
    expected = sorted(
        path.name
        for path in app_dir.iterdir()
        if path.is_dir() and (path / "services" / "__init__.py").is_file()
    )
    assert expected  # the real domains exist
    assert reset_dev.discover_seed_domains() == expected


# ---------------------------------------------------------------- integration (throwaway database)


class Server:
    """Connection details for the test Postgres server and one throwaway database name."""

    def __init__(self, base_url: str, name: str) -> None:
        self.parsed = make_url(base_url)
        self.name = name
        self.url = self.parsed.set(database=name).render_as_string(hide_password=False)

    def connect(self, dbname: str) -> "psycopg.Connection[Any]":
        return psycopg.connect(
            host=self.parsed.host,
            port=self.parsed.port,
            user=self.parsed.username,
            password=self.parsed.password,
            dbname=dbname,
            autocommit=True,
            connect_timeout=2,
        )


def _server_reachable(url: str) -> bool:
    parsed = make_url(url)
    try:
        with psycopg.connect(
            host=parsed.host,
            port=parsed.port,
            user=parsed.username,
            password=parsed.password,
            dbname="postgres",
            connect_timeout=2,
        ):
            return True
    except psycopg.Error:
        return False


def _clear_caches() -> None:
    core_config.get_settings.cache_clear()
    core_db.get_sessionmaker.cache_clear()
    core_db.get_engine.cache_clear()


@pytest.fixture
def throwaway_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Server]:
    """A uniquely named database on the test server (not created yet); dropped afterwards.

    The process environment points at it (restored by monkeypatch) and the settings and engine
    caches are cleared before and after, so the dev database is never involved.
    """
    base = resolve_test_database_url(os.environ)
    if not _server_reachable(base):
        if must_fail_when_db_unreachable(os.environ):
            pytest.fail("Postgres is unreachable but this run requires a database")
        pytest.skip("no reachable local Postgres server")
    server = Server(base, f"reset_dev_e2e_{uuid.uuid4().hex[:12]}")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", server.url)
    monkeypatch.setattr(reset_dev, "_dev_env_file", lambda: None)
    _clear_caches()
    try:
        yield server
    finally:
        _clear_caches()
        with server.connect("postgres") as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{server.name}" WITH (FORCE)')


def test_recreate_database_drops_and_recreates_a_throwaway_database(throwaway_db: Server) -> None:
    reset_dev.recreate_database(throwaway_db.url)
    with throwaway_db.connect(throwaway_db.name) as conn:
        conn.execute("CREATE TABLE marker (id int)")
    reset_dev.recreate_database(throwaway_db.url)  # drops (with the marker) and recreates
    with throwaway_db.connect(throwaway_db.name) as conn:
        row = conn.execute("SELECT to_regclass('public.marker')").fetchone()
        assert row is not None
        assert row[0] is None


def test_recreate_database_forces_off_other_open_connections(throwaway_db: Server) -> None:
    reset_dev.recreate_database(throwaway_db.url)
    with throwaway_db.connect(throwaway_db.name):  # a client still connected during the reset
        reset_dev.recreate_database(throwaway_db.url)


def test_reset_drops_recreates_migrates_to_head_and_seeds_within_sixty_seconds(
    throwaway_db: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    head = ScriptDirectory.from_config(Config(str(migrate_dev.ALEMBIC_INI))).get_current_head()
    assert head
    with throwaway_db.connect("postgres") as admin:  # a stale database that must be dropped
        admin.execute(f'CREATE DATABASE "{throwaway_db.name}"')
    with throwaway_db.connect(throwaway_db.name) as conn:
        conn.execute("CREATE TABLE stale_marker (id int)")

    started = time.monotonic()
    code = reset_dev.main(environ={"APP_ENV": "dev", "DATABASE_URL": throwaway_db.url})
    elapsed = time.monotonic() - started

    out = capsys.readouterr().out
    assert code == 0, out
    assert elapsed < 60
    match = re.search(r"^seeded: (\d+) seeders$", out, re.MULTILINE)
    assert match is not None, out
    assert int(match.group(1)) == len(seeding.registered_seeders())
    with throwaway_db.connect(throwaway_db.name) as conn:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        stale = conn.execute("SELECT to_regclass('public.stale_marker')").fetchone()
    assert version is not None
    assert version[0] == head
    assert stale is not None
    assert stale[0] is None


def _write_fake_domain(root: Path, name: str) -> None:
    services = root / name / "services"
    services.mkdir(parents=True)
    (root / name / "__init__.py").write_text("", encoding="utf-8")
    (services / "__init__.py").write_text(
        "from sqlalchemy import text\n"  # noqa: S608
        "from sqlalchemy.ext.asyncio import AsyncSession\n"
        "from app.core.seeding import register_seeder\n\n\n"
        "async def seed_fake(session: AsyncSession) -> None:\n"
        '    await session.execute(text("CREATE TABLE fake_seed_marker (id int)"))\n'
        '    await session.execute(text("INSERT INTO fake_seed_marker VALUES (42)"))\n\n\n'
        f'register_seeder("{name}", seed_fake)\n',
        encoding="utf-8",
    )


def test_seeding_calls_a_registered_domain_seeder_with_a_session_and_commits_its_write(
    throwaway_db: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = f"fakeseed_{uuid.uuid4().hex[:8]}"
    _write_fake_domain(tmp_path, name)
    # run_seeding imports ``app.<domain>.services``, so the fake package joins the app namespace.
    monkeypatch.setattr(app_package, "__path__", [*app_package.__path__, str(tmp_path)])
    monkeypatch.setattr(reset_dev, "discover_seed_domains", lambda: [name])
    importlib.invalidate_caches()
    reset_dev.recreate_database(throwaway_db.url)
    try:
        count = reset_dev.run_seeding()
    finally:
        for module in [m for m in sys.modules if m.startswith(f"app.{name}")]:
            del sys.modules[module]
        if hasattr(app_package, name):
            delattr(app_package, name)
    assert count == 1
    assert seeding.registered_seeders() == [name]
    with throwaway_db.connect(throwaway_db.name) as conn:
        rows = conn.execute("SELECT id FROM fake_seed_marker").fetchall()
    assert rows == [(42,)]


def test_seeding_with_no_registered_seeders_is_a_no_op_that_returns_zero(
    throwaway_db: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reset_dev, "discover_seed_domains", list)
    reset_dev.recreate_database(throwaway_db.url)
    assert reset_dev.run_seeding() == 0
