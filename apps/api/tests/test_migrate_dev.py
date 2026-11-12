"""Unit tests for the dev-only migrate entry point (S-1.3.2); no database is used."""

from collections.abc import Callable

import pytest

from app.scripts import migrate_dev


def _recorder() -> tuple[list[str], Callable[[], None]]:
    calls: list[str] = []
    return calls, lambda: calls.append("upgrade")


def test_dev_runs_the_upgrade_once_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    calls, upgrade = _recorder()
    assert migrate_dev.main({"APP_ENV": "dev"}, upgrade) == 0
    assert calls == ["upgrade"]
    assert "at head" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["prod", "test", "", "Dev", " dev", "development"])
def test_any_other_app_env_refuses_without_upgrading(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    calls, upgrade = _recorder()
    assert migrate_dev.main({"APP_ENV": value}, upgrade) == migrate_dev.EXIT_REFUSED
    assert calls == []
    err = capsys.readouterr().err
    assert "migrate refused" in err
    assert "APP_ENV=dev" in err


def test_unset_app_env_refuses_without_upgrading() -> None:
    calls, upgrade = _recorder()
    assert migrate_dev.main({}, upgrade) == migrate_dev.EXIT_REFUSED
    assert calls == []


def test_refusal_does_not_read_database_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")
    assert migrate_dev.main() == migrate_dev.EXIT_REFUSED


def test_a_failing_upgrade_propagates_so_the_step_fails() -> None:
    def boom() -> None:
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        migrate_dev.main({"APP_ENV": "dev"}, boom)
