"""Static and unit checks for the API, worker and migrate services (S-1.3.2).

Runtime behaviour (start order, migrate refusal, reload timing, /health/ready) is covered by the
CI compose smoke job. These tests pin the configuration that makes it possible, and the guard
logic of ``app.scripts.migrate_dev``. Compose files are read as text (no yaml dependency).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock
from urllib.parse import quote, unquote, urlsplit

import pytest

from app.core import config as core_config
from app.scripts import migrate_dev
from tests.repo_paths import repo_root
from tests.test_infra_compose import (
    API_DIR,
    COMPOSE,
    REPO_ROOT,
    compose_text,
    depends_on_conditions,
    list_items,
    read_text,
    service_block,
    strip_comments,
)

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "pnpm-workspace.yaml").is_file() or not COMPOSE.is_file(),
    reason="needs the full monorepo checkout (infra/ and the repo root files)",
)

DOCKERFILE = API_DIR / "Dockerfile"


def command_items(service: str) -> list[str]:
    block = service_block(compose_text(), service)
    match = re.search(r"command:\s*\[(.*?)\]", block, re.S)
    assert match, f"{service} has no list-form command"
    return re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))


# ------------------------------------------------------------------ AC1: start order


def test_postgres_and_redis_depend_on_nothing_so_infra_only_start_stays_minimal() -> None:
    for service in ("postgres", "redis"):
        assert "depends_on" not in service_block(compose_text(), service), service


def test_postgres_and_redis_keep_their_healthchecks() -> None:
    for service in ("postgres", "redis"):
        assert "healthcheck:" in service_block(compose_text(), service), service


@pytest.mark.parametrize("service", ["api", "worker"])
def test_service_waits_for_healthy_postgres_and_redis_and_completed_migrate(service: str) -> None:
    assert depends_on_conditions(compose_text(), service) == {
        "postgres": "service_healthy",
        "redis": "service_healthy",
        "migrate": "service_completed_successfully",
    }


def test_migrate_waits_only_for_healthy_postgres() -> None:
    assert depends_on_conditions(compose_text(), "migrate") == {"postgres": "service_healthy"}


# ------------------------------------------------------------- AC2: migrate guard (unit)


def _run_main(app_env: str | None) -> tuple[int, mock.Mock, mock.Mock]:
    environ = {} if app_env is None else {"APP_ENV": app_env}
    with (
        mock.patch.object(migrate_dev.command, "upgrade") as upgrade,
        mock.patch.object(migrate_dev, "Config") as config,
    ):
        code = migrate_dev.main(environ)
    return code, upgrade, config


def test_migrate_runs_alembic_upgrade_head_exactly_once_for_dev() -> None:
    code, upgrade, config = _run_main("dev")
    assert code == 0
    upgrade.assert_called_once_with(config.return_value, "head")


@pytest.mark.parametrize("value", ["test", "prod", None, "", "Dev", " dev", "dev ", "DEV"])
def test_migrate_refuses_without_calling_alembic_for_any_other_app_env(
    value: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    code, upgrade, config = _run_main(value)
    assert code != 0
    upgrade.assert_not_called()
    config.assert_not_called()
    captured = capsys.readouterr()
    assert captured.err.strip() != ""
    assert captured.out == ""


def test_migrate_refusal_does_not_load_settings_or_touch_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core_config.get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://invalid.invalid:1/none")
    with mock.patch.object(migrate_dev.command, "upgrade") as upgrade:
        assert migrate_dev.main() != 0
    upgrade.assert_not_called()
    assert core_config.get_settings.cache_info().currsize == 0


def test_migrate_default_upgrade_uses_the_apps_alembic_ini() -> None:
    assert API_DIR / "alembic.ini" == migrate_dev.ALEMBIC_INI
    assert migrate_dev.ALEMBIC_INI.is_file()


# ------------------------------------------------------- AC2: migrate service (static)


def test_migrate_command_is_the_guard_not_raw_alembic() -> None:
    assert command_items("migrate") == ["python", "-m", "app.scripts.migrate_dev"]


def test_migrate_is_one_shot_and_never_restarts() -> None:
    block = service_block(compose_text(), "migrate")
    assert re.search(r"^\s+restart:\s*['\"]no['\"]\s*$", block, re.M)


def test_migrate_gets_app_env_from_the_environment_defaulting_to_dev() -> None:
    assert "APP_ENV: ${APP_ENV:-dev}" in service_block(compose_text(), "migrate")


# ------------------------------------------------------------------ AC3: reload / worker


def test_api_command_has_factory_reload_and_reload_dir_limited_to_app_source() -> None:
    items = command_items("api")
    assert items[0] == "uvicorn"
    assert items[items.index("--factory") + 1] == "app.main:create_app"
    assert "--reload" in items
    assert items.count("--reload-dir") == 1
    assert items[items.index("--reload-dir") + 1] == "/app/app"


@pytest.mark.parametrize("service", ["api", "worker"])
def test_source_is_bind_mounted_so_no_rebuild_is_needed(service: str) -> None:
    mounts = list_items(service_block(compose_text(), service), "volumes")
    assert "../apps/api:/app:ro" in mounts


def test_api_polls_for_file_changes() -> None:
    block = service_block(compose_text(), "api").replace('"', "'")
    assert "WATCHFILES_FORCE_POLLING: 'true'" in block


def test_worker_has_no_reload_and_runs_the_arq_worker_command_on_the_shared_image() -> None:
    block = service_block(compose_text(), "worker")
    assert "reload" not in block.lower()
    assert command_items("worker") == ["python", "-m", "app.workers"]
    assert re.search(r"image:\s*mtg-stats-api:dev", block)


def test_worker_stop_grace_period_exceeds_the_default_shutdown_grace() -> None:
    config_src = read_text(API_DIR / "app" / "core" / "config.py")
    default = re.search(
        r"worker_shutdown_grace_seconds:\s*int\s*=\s*Field\(\s*default=(\d+)", config_src
    )
    assert default
    stop = re.search(r"stop_grace_period:\s*(\d+)s", service_block(compose_text(), "worker"))
    assert stop
    assert int(stop.group(1)) > int(default.group(1))


# ------------------------------------------------------------------ AC4: readiness


def test_api_port_is_published_on_loopback_8000_by_default() -> None:
    ports = list_items(service_block(compose_text(), "api"), "ports")
    assert ports == ["127.0.0.1:${API_PORT:-8000}:8000"]


def test_api_healthcheck_targets_the_ready_endpoint_on_container_port_8000() -> None:
    block = service_block(compose_text(), "api")
    assert "http://127.0.0.1:8000/api/v1/health/ready" in block
    assert "/health/live" not in block


# ------------------------------------------------------------------ image (T-1.3.2.1)


def test_dockerfile_base_image_is_an_exact_version_not_latest() -> None:
    froms = re.findall(r"^FROM\s+(\S+)", read_text(DOCKERFILE), re.M)
    assert froms
    for image in froms:
        assert re.search(r":\d+\.\d+\.\d+", image), image


def test_dockerfile_copies_lockfile_and_pyproject_before_the_source() -> None:
    text = read_text(DOCKERFILE)
    deps_copy = text.index("COPY --chown=app:app pyproject.toml uv.lock")
    sync = text.index("uv sync --locked")
    source_copy = text.index("COPY --chown=app:app . .")
    assert deps_copy < sync < source_copy


def test_dockerfile_venv_lives_outside_the_bind_mounted_app_dir() -> None:
    text = read_text(DOCKERFILE)
    match = re.search(r"UV_PROJECT_ENVIRONMENT=(\S+)", text)
    assert match
    venv = match.group(1)
    assert venv != "/app"
    assert not venv.startswith("/app/")
    assert f"PATH={venv}/bin" in text
    assert re.search(r"^WORKDIR /app$", text, re.M)


def test_dockerfile_uv_version_matches_ci_setup_uv_version() -> None:
    ci = read_text(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    versions = set(re.findall(r"^\s+version:\s*(\d+\.\d+\.\d+)\s*$", ci, re.M))
    docker = set(re.findall(r"ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)", read_text(DOCKERFILE)))
    assert len(docker) == 1
    assert versions == docker


@pytest.mark.parametrize(
    "entry", [".env", ".env.*", ".venv", ".mypy_cache", ".pytest_cache", ".coverage"]
)
def test_dockerignore_excludes_env_files_and_venv(entry: str) -> None:
    assert entry in read_text(API_DIR / ".dockerignore").splitlines()


def test_dockerignore_re_includes_only_the_env_example() -> None:
    negations = [
        line for line in read_text(API_DIR / ".dockerignore").splitlines() if line.startswith("!")
    ]
    assert negations == ["!.env.example"]


# ------------------------------------------------------------------ factory refactor


def test_importing_app_main_builds_no_app_and_leaves_the_settings_cache_untouched() -> None:
    script = (
        "import app.main as m\n"
        "from app.core.config import get_settings\n"
        "assert not hasattr(m, 'app'), 'module-level app exists'\n"
        "assert callable(m.create_app)\n"
        "assert get_settings.cache_info().currsize == 0, 'settings were loaded at import'\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed arguments
        [sys.executable, "-c", script],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_root_dev_api_script_runs_uvicorn_as_a_factory() -> None:
    package = read_text(REPO_ROOT / "package.json")
    match = re.search(r'"dev:api":\s*"([^"]*)"', package)
    assert match
    assert "--factory app.main:create_app" in match.group(1)
    assert "app.main:app" not in match.group(1)


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_no_tracked_file_still_references_the_removed_module_level_app() -> None:
    git = shutil.which("git")
    assert git is not None
    result = subprocess.run(  # noqa: S603 - fixed arguments
        [git, "grep", "-l", "-I", r"app\.main:app\b", "--", ".", ":!apps/api/tests"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        pytest.skip("not a usable git work tree")
    assert result.stdout.strip() == ""


# ------------------------------------------------------------------ secrets


def test_compose_has_no_postgres_password_or_embedded_url_credentials() -> None:
    text = strip_comments(compose_text())
    assert "POSTGRES_PASSWORD" not in text
    assert not re.search(r"postgresql\+psycopg://[^\s'\"]*@", text)
    assert not re.search(r"(?i)[a-z_]*(password|secret|token|api_key)[a-z_]*\s*[:=]", text)


def test_postgres_service_gets_credentials_only_through_env_file() -> None:
    block = service_block(compose_text(), "postgres")
    assert re.search(r"^\s*-\s*path:\s*\.\./\.env\s*$", block, re.M)
    assert "POSTGRES_USER" not in block.replace("$$POSTGRES_USER", "")


# ------------------------------------------------------------ single credential source


ENTRYPOINT = API_DIR / "docker-entrypoint.sh"
ENTRYPOINT_PATH = "/usr/local/bin/docker-entrypoint.sh"


@pytest.mark.parametrize("service", ["migrate", "api", "worker"])
def test_service_uses_the_entrypoint_and_reaches_redis_without_localhost(service: str) -> None:
    block = service_block(compose_text(), service)
    assert re.search(
        rf"^\s+entrypoint:\s*\[\s*['\"]{re.escape(ENTRYPOINT_PATH)}['\"]\s*\]\s*$", block, re.M
    )
    assert "redis://redis:6379/" in block
    assert "DATABASE_URL" not in block
    assert not re.search(r"(localhost|127\.0\.0\.1):(5432|6379)", block)


def test_entrypoint_builds_the_url_with_host_postgres_port_5432() -> None:
    assert "@postgres:5432/" in read_text(ENTRYPOINT)


def test_dockerfile_installs_the_entrypoint_executable_at_the_compose_path() -> None:
    text = read_text(DOCKERFILE)
    assert f"COPY --chmod=755 docker-entrypoint.sh {ENTRYPOINT_PATH}" in text


def _sh_and_python_shim(tmp_path: Path) -> tuple[str, dict[str, str]]:
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("sh unavailable")
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "python"
    body = "#!/bin/sh" + chr(10) + f'exec "{Path(sys.executable).as_posix()}" "$@"' + chr(10)
    shim.write_text(body, newline=chr(10))
    shim.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("POSTGRES_", "DATABASE_"))}
    env["PATH"] = f"{shim_dir.as_posix()}{os.pathsep}{env.get('PATH', '')}"
    probe = subprocess.run(  # noqa: S603
        [sh, "-c", "python -c 'print(1)'"], env=env, capture_output=True, text=True, check=False
    )
    if probe.stdout.strip() != "1":
        pytest.skip("python shim is not runnable from sh")
    return sh, env


def _entrypoint(
    tmp_path: Path, extra: dict[str, str], *command: str
) -> subprocess.CompletedProcess[str]:
    sh, env = _sh_and_python_shim(tmp_path)
    env.update(extra)
    return subprocess.run(  # noqa: S603 - fixed script, controlled environment
        [sh, ENTRYPOINT.as_posix(), *command],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


GOOD = {"POSTGRES_USER": "u", "POSTGRES_PASSWORD": "p", "POSTGRES_DB": "db"}  # secrets-check: allow
EXPECTED_URL = "postgresql+psycopg://u:p@postgres:5432/db"  # secrets-check: allow
LOCAL_URL = "postgresql+psycopg://a:b@127.0.0.1:5432/other"  # secrets-check: allow


def test_entrypoint_builds_database_url_from_the_postgres_settings(tmp_path: Path) -> None:
    result = _entrypoint(tmp_path, GOOD, "printenv", "DATABASE_URL")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == EXPECTED_URL


def test_entrypoint_percent_encodes_special_characters_so_the_url_round_trips(
    tmp_path: Path,
) -> None:
    password = "p@ss:w/o#rd%1 x"
    result = _entrypoint(
        tmp_path, {**GOOD, "POSTGRES_PASSWORD": password}, "printenv", "DATABASE_URL"
    )
    assert result.returncode == 0, result.stderr
    url = result.stdout.strip()
    assert url == f"postgresql+psycopg://u:{quote(password, safe='')}@postgres:5432/db"
    parts = urlsplit(url)
    assert parts.hostname == "postgres"
    assert parts.port == 5432
    assert unquote(parts.password or "") == password
    assert parts.path == "/db"


def test_entrypoint_overrides_an_inherited_localhost_database_url(tmp_path: Path) -> None:
    inherited = {**GOOD, "DATABASE_URL": LOCAL_URL}
    result = _entrypoint(tmp_path, inherited, "printenv", "DATABASE_URL")
    assert result.stdout.strip() == EXPECTED_URL


@pytest.mark.parametrize("name", ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"])
@pytest.mark.parametrize("empty", [False, True])
def test_entrypoint_refuses_when_a_postgres_setting_is_missing_or_empty(
    tmp_path: Path, name: str, empty: bool
) -> None:
    extra = {k: v for k, v in GOOD.items() if k != name or empty}
    if empty:
        extra[name] = ""
    result = _entrypoint(tmp_path, extra, "echo", "SHOULD_NOT_RUN")
    assert result.returncode != 0
    assert name in result.stderr
    assert "SHOULD_NOT_RUN" not in result.stdout


def test_entrypoint_execs_the_command_with_its_arguments(tmp_path: Path) -> None:
    result = _entrypoint(tmp_path, GOOD, "echo", "a b", "c")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "a b c" + chr(10)


def test_entrypoint_propagates_the_command_exit_status(tmp_path: Path) -> None:
    assert _entrypoint(tmp_path, GOOD, "sh", "-c", "exit 7").returncode == 7


# ------------------------------------------------- import-safe repo root (API-only container)


def test_repo_root_of_a_test_file_in_a_three_level_container_path_does_not_raise() -> None:
    # /app/tests/x.py has parents /app/tests, /app and / only; resolve() keeps it verbatim on POSIX.
    with mock.patch.object(Path, "resolve", lambda self: self):
        root = repo_root(Path("/app/tests/test_x.py"))
    assert root == Path("/app/tests/test_x.py").parents[-1]
    assert not (root / "pnpm-workspace.yaml").is_file() or root == REPO_ROOT


def test_repo_root_of_a_test_file_in_the_monorepo_is_the_repo_root() -> None:
    assert repo_root(Path(__file__)) == REPO_ROOT
    assert (REPO_ROOT / "pnpm-workspace.yaml").is_file()
