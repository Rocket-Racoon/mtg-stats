"""Project configuration tests for S-1.1.2 (uv-managed Python project)."""

import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

API_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = API_ROOT / "pyproject.toml"

CORE_RUNTIME = {
    "fastapi",
    "sqlalchemy",
    "alembic",
    "pydantic-settings",
    "httpx",
    "pyjwt",
    "arq",
    "uvicorn",
    "psycopg",
}
LATER_APPROVED_RUNTIME = {"argon2-cffi", "ijson", "qrcode", "redis"}
ALLOWED_DEV = {
    "ruff",
    "mypy",
    "pytest",
    "pytest-cov",
    "pytest-asyncio",
    "factory-boy",
    "pre-commit",
}

_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?")


def _parse(requirement: str) -> tuple[str, set[str]]:
    match = _REQ.match(requirement)
    assert match, f"unparseable requirement: {requirement}"
    name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
    extras = {e.strip().lower() for e in (match.group(2) or "").split(",") if e.strip()}
    return name, extras


def _load() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _runtime() -> dict[str, set[str]]:
    return dict(_parse(r) for r in _load()["project"]["dependencies"])


def test_running_interpreter_is_python_3_12_or_newer() -> None:
    assert sys.version_info >= (3, 12)


def test_requires_python_is_3_12_or_newer() -> None:
    assert _load()["project"]["requires-python"].replace(" ", "") == ">=3.12"


def test_runtime_dependencies_contain_the_required_core_set() -> None:
    assert set(_runtime()) >= CORE_RUNTIME


def test_runtime_dependencies_are_only_approved_packages() -> None:
    unexpected = set(_runtime()) - CORE_RUNTIME - LATER_APPROVED_RUNTIME
    assert unexpected == set()


def test_sqlalchemy_is_declared_with_the_asyncio_extra() -> None:
    assert "asyncio" in _runtime()["sqlalchemy"]


def test_dev_group_contains_exactly_the_approved_tools() -> None:
    dev = {_parse(r)[0] for r in _load()["dependency-groups"]["dev"]}
    assert dev == ALLOWED_DEV


def test_pytest_config_enforces_strict_markers_and_discovers_tests_dir() -> None:
    options = _load()["tool"]["pytest"]["ini_options"]
    assert "--strict-markers" in options["addopts"].split()
    assert "tests" in options["testpaths"]


def test_unregistered_marker_fails_the_run_under_project_config(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyprojecttoml(PYPROJECT.read_text(encoding="utf-8"))
    pytester.makepyfile(
        test_unknown_marker="""
        import pytest

        @pytest.mark.not_a_registered_marker
        def test_x():
            pass
        """
    )
    result = pytester.runpytest_inprocess()
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*'not_a_registered_marker' not found in `markers`*"])


def test_python_version_file_pins_3_12() -> None:
    assert (API_ROOT / ".python-version").read_text(encoding="utf-8").strip().startswith("3.12")


def test_mypy_strict_mode_is_enabled() -> None:
    assert _load()["tool"]["mypy"]["strict"] is True


def test_coverage_measures_the_app_package() -> None:
    assert "app" in _load()["tool"]["coverage"]["run"]["source"]


def test_pytest_asyncio_mode_is_auto() -> None:
    assert _load()["tool"]["pytest"]["ini_options"]["asyncio_mode"] == "auto"


def test_uv_project_is_not_packaged() -> None:
    assert _load()["tool"]["uv"]["package"] is False


def test_lockfile_exists() -> None:
    assert (API_ROOT / "uv.lock").is_file()


def _find_uv() -> str:
    uv = shutil.which("uv") or os.environ.get("UV")
    if uv:
        return uv
    if os.environ.get("CI"):
        pytest.fail("uv not found on PATH or in $UV, but CI is set")
    pytest.skip("uv is not available (not on PATH, $UV unset)")


def test_lockfile_is_up_to_date_with_pyproject() -> None:
    result = subprocess.run(  # noqa: S603
        [_find_uv(), "lock", "--check"],
        capture_output=True,
        text=True,
        cwd=API_ROOT,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
