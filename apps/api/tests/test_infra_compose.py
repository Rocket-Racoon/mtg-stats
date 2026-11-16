"""Static checks of the Docker Compose dev stack (S-1.3.1).

The runtime halves of the acceptance criteria (health within 30 s, persistence across
down/up, database creation at runtime) are verified manually; see the PR. These tests
read the files as text (no yaml dependency) and check the configuration that makes
those behaviors possible. Each check takes text so negative tests can feed broken variants.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.repo_paths import repo_root

REPO_ROOT = repo_root(__file__)
COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
INIT_SQL = REPO_ROOT / "infra" / "postgres" / "init" / "01-create-test-db.sql"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Inside the API container only apps/api exists (no infra/, no .env.example): skip the whole file.
pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "pnpm-workspace.yaml").is_file() or not COMPOSE.is_file(),
    reason="needs the full monorepo checkout (infra/ and the repo root files)",
)

PG_PORT = '"127.0.0.1:${POSTGRES_PORT:-5432}:5432"'
REDIS_PORT = '"127.0.0.1:${REDIS_PORT:-6379}:6379"'


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compose_text() -> str:
    return read_text(COMPOSE)


def strip_comments(text: str) -> str:
    return "\n".join(re.sub(r"(^|\s)#.*$", "", line).rstrip() for line in text.splitlines())


def top_level_block(text: str, key: str) -> str:
    """Return the body (indented lines) of a top-level key."""
    body: list[str] = []
    inside = False
    for line in strip_comments(text).splitlines():
        if re.match(r"^\S", line):
            inside = line.startswith(f"{key}:")
            continue
        if inside:
            body.append(line)
    return "\n".join(body)


def service_block(text: str, name: str) -> str:
    body: list[str] = []
    inside = False
    for line in top_level_block(text, "services").splitlines():
        if re.match(r"^ {2}\S", line):
            inside = line.strip() == f"{name}:"
            continue
        if inside:
            body.append(line)
    return "\n".join(body)


def image_of(text: str, service: str) -> str:
    match = re.search(r"^\s+image:\s*(\S+)\s*$", service_block(text, service), re.M)
    assert match, f"service {service} has no image"
    return match.group(1)


def list_items(block: str, key: str) -> list[str]:
    """Items of a block-style `key:` YAML list inside a service block."""
    items: list[str] = []
    inside = False
    key_indent = 0
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if not inside:
            if stripped == f"{key}:":
                inside, key_indent = True, indent
            continue
        if indent <= key_indent and not stripped.startswith("-"):
            break
        if stripped.startswith("-"):
            items.append(stripped[1:].strip().strip("\"'"))
    return items


# ---------------------------------------------------------------- checks (AC1)


def check_pinned(text: str, service: str, major: str) -> None:
    name, _, tag = image_of(text, service).partition(":")
    assert name == service, f"{service} image is {name}"
    # Postgres 10+ versions are major.minor (16.10); Redis is major.minor.patch (7.4.6).
    parts = r"\.\d+" if service == "postgres" else r"\.\d+\.\d+"
    assert re.match(rf"^{major}{parts}(-[a-z0-9.]+)?$", tag), (
        f"{service} tag {tag!r} is not an exact pinned {major}.x tag"
    )


def check_healthchecks(text: str) -> None:
    for service in ("postgres", "redis"):
        block = service_block(text, service)
        assert re.search(r"^\s+healthcheck:\s*$", block, re.M), f"{service} has no healthcheck"
        assert re.search(r"^\s+test:\s*\[", block, re.M), f"{service} healthcheck has no test"


def check_postgres_tcp_probe(text: str) -> None:
    block = service_block(text, "postgres")
    match = re.search(r"^\s+test:\s*(\[.*\])\s*$", block, re.M)
    assert match, "postgres healthcheck test not found"
    probe = match.group(1)
    assert "pg_isready" in probe
    assert re.search(r"pg_isready\b[^\]]*\s-h\s+(127\.0\.0\.1|localhost)\b", probe), (
        "pg_isready must probe a TCP host with -h"
    )


# ---------------------------------------------------------------- checks (AC2)


def check_named_volumes(text: str) -> None:
    declared = {
        line.strip().rstrip(":").strip()
        for line in top_level_block(text, "volumes").splitlines()
        if line.strip()
    }
    expectations = {"postgres": "/var/lib/postgresql/data", "redis": "/data"}
    for service, target in expectations.items():
        mounts = list_items(service_block(text, service), "volumes")
        match = [m for m in mounts if m.split(":")[1:2] == [target]]
        assert match, f"{service} does not mount {target}"
        source = match[0].split(":")[0]
        assert not re.match(r"^(\.|/|~|[A-Za-z]:)", source), f"{service} data mount is a bind mount"
        assert source in declared, f"volume {source} not declared at top level"


# ---------------------------------------------------------------- checks (AC3)


def check_init_dir_mounted(text: str) -> None:
    mounts = list_items(service_block(text, "postgres"), "volumes")
    assert any(
        m.startswith("./postgres/init:") and m.split(":")[1] == "/docker-entrypoint-initdb.d"
        for m in mounts
    ), "init directory not mounted at /docker-entrypoint-initdb.d"


def check_init_sql(sql: str) -> None:
    assert re.search(r"^\s*CREATE\s+DATABASE\s+mtg_stats_test\s*;", sql, re.I | re.M)


# ---------------------------------------------------------------- checks (AC4)


def check_ports_loopback(text: str) -> None:
    for service in ("postgres", "redis"):
        ports = list_items(service_block(text, service), "ports")
        assert ports, f"{service} publishes no ports"
        for port in ports:
            assert port.startswith("127.0.0.1:"), f"{service} port {port!r} is not loopback-only"


def check_no_hardcoded_password(text: str) -> None:
    # `${POSTGRES_PASSWORD:-CHANGE_ME}` is an interpolation with the allowed default, not a literal.
    assert not re.search(r"(?<!\{)POSTGRES_PASSWORD\s*[:=]", strip_comments(text))


def check_env_file(text: str) -> None:
    block = service_block(text, "postgres")
    assert re.search(r"^\s+env_file:\s*$", block, re.M), "postgres has no env_file"
    assert re.search(r"^\s*-\s*(path:\s*)?\.\./\.env\s*$", block, re.M), (
        "postgres env_file must point at the root .env"
    )
    assert re.search(r"^\s*required:\s*true\s*$", block, re.M), "env_file must be required"


def check_postgres_bookworm(text: str) -> None:
    assert image_of(text, "postgres").endswith("-bookworm")


def check_unsafe_durability_absent(text: str) -> None:
    stripped = strip_comments(text)
    assert not re.search(r"fsync\s*=\s*off", stripped)
    assert not re.search(r"full_page_writes\s*=\s*off", stripped)


# ------------------------------------------------------------------- positive


def test_files_exist() -> None:
    assert COMPOSE.is_file()
    assert INIT_SQL.is_file()
    assert ENV_EXAMPLE.is_file()


def test_postgres_image_pinned_to_exact_16_tag() -> None:
    check_pinned(compose_text(), "postgres", "16")


def test_redis_image_pinned_to_exact_7_tag() -> None:
    check_pinned(compose_text(), "redis", "7")


def test_both_services_define_healthchecks() -> None:
    check_healthchecks(compose_text())


def test_postgres_healthcheck_probes_over_tcp() -> None:
    check_postgres_tcp_probe(compose_text())


def test_redis_healthcheck_pings() -> None:
    block = service_block(compose_text(), "redis")
    assert "redis-cli" in block
    assert "ping" in block


def test_data_directories_use_declared_named_volumes() -> None:
    check_named_volumes(compose_text())


def test_init_directory_mounted_at_entrypoint_initdb() -> None:
    check_init_dir_mounted(compose_text())


def test_init_sql_creates_test_database() -> None:
    check_init_sql(read_text(INIT_SQL))


def test_published_ports_bind_loopback_only() -> None:
    check_ports_loopback(compose_text())


def test_compose_has_no_hardcoded_postgres_password() -> None:
    check_no_hardcoded_password(compose_text())


def test_postgres_credentials_come_from_root_env_file() -> None:
    check_env_file(compose_text())


def test_env_example_declares_postgres_settings() -> None:
    text = read_text(ENV_EXAMPLE)
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        assert re.search(rf"^{key}=\S+", text, re.M), f"{key} missing from .env.example"
    assert re.search(r"^POSTGRES_PASSWORD=CHANGE_ME\s*$", text, re.M)


def test_env_example_ships_the_jwt_secret_placeholder_not_a_usable_secret() -> None:
    assert re.search(r"^JWT_SECRET=CHANGE_ME\s*$", read_text(ENV_EXAMPLE), re.M)


SMOKE_SCRIPT = REPO_ROOT / "scripts" / "compose_smoke.sh"


def test_compose_smoke_generates_a_jwt_secret_after_copying_env_example() -> None:
    text = read_text(SMOKE_SCRIPT)
    copy_at = text.index("cp .env.example .env")
    generate_at = text.index("secrets.token_urlsafe(48)")
    assert generate_at > copy_at
    assert "JWT_SECRET" in text[copy_at:]
    assert "CHANGE_ME" in text[copy_at:generate_at]


def test_compose_smoke_never_prints_the_generated_jwt_secret() -> None:
    lines = read_text(SMOKE_SCRIPT).splitlines()
    for line in lines:
        code = line.split("#", 1)[0]
        if "JWT_SECRET" not in code:
            continue
        assert not re.search(r"\b(echo|printf)\b[^>|]*\$\{?JWT_SECRET\w*", code) or (">" in code), (
            f"script may print the secret: {line.strip()}"
        )
    assert not re.search(r"^\s*(set -x|set -o xtrace)", "\n".join(lines), re.M)


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
@pytest.mark.parametrize("path", [".env", "infra/.env"])
def test_real_env_files_are_gitignored(path: str) -> None:
    git = shutil.which("git")
    assert git is not None
    result = subprocess.run(  # noqa: S603 - fixed arguments, no untrusted input
        [git, "check-ignore", "-q", path],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        pytest.skip("not a usable git work tree")
    assert result.returncode == 0, f"{path} is not gitignored"


# ------------------------------------------------------------------- negative


def quote_agnostic(literal: str) -> re.Pattern[str]:
    """Regex matching `literal`, where every ' or " also matches the other quote style.

    A formatter (Prettier) may switch the compose file between single and double quotes; the
    mutation targets must keep matching either way.
    """
    pattern = "".join("['\"]" if ch in "'\"" else re.escape(ch) for ch in literal)
    return re.compile(pattern)


def broken(old: str, new: str) -> str:
    text = compose_text()
    pattern = quote_agnostic(old)
    assert pattern.search(text), f"mutation target {old!r} not found"
    return pattern.sub(lambda _match: new, text)


@pytest.mark.parametrize("tag", ["16", "16-bookworm", "latest", "15.4"])
def test_check_rejects_unpinned_postgres(tag: str) -> None:
    with pytest.raises(AssertionError):
        check_pinned(broken("postgres:16.10-bookworm", f"postgres:{tag}"), "postgres", "16")


@pytest.mark.parametrize("tag", ["7", "7.4", "latest", "6.2.1"])
def test_check_rejects_unpinned_redis(tag: str) -> None:
    with pytest.raises(AssertionError):
        check_pinned(broken("redis:7.4.6-alpine", f"redis:{tag}"), "redis", "7")


def test_check_rejects_socket_only_postgres_probe() -> None:
    text = broken("pg_isready -h 127.0.0.1 -p 5432", "pg_isready -p 5432")
    with pytest.raises(AssertionError):
        check_postgres_tcp_probe(text)


def test_check_rejects_missing_healthcheck() -> None:
    text = broken(
        '    healthcheck:\n      test: ["CMD", "redis-cli"',
        '    xcheck:\n      test: ["CMD", "redis-cli"',
    )
    with pytest.raises(AssertionError):
        check_healthchecks(text)


@pytest.mark.parametrize("port", [PG_PORT, REDIS_PORT])
def test_check_rejects_non_loopback_ports(port: str) -> None:
    for variant in (port.replace("127.0.0.1:", "0.0.0.0:"), port.replace("127.0.0.1:", "")):
        with pytest.raises(AssertionError):
            check_ports_loopback(broken(port, variant))


@pytest.mark.parametrize("literal", ["POSTGRES_PASSWORD: secret", "POSTGRES_PASSWORD=secret"])
def test_check_rejects_hardcoded_password(literal: str) -> None:
    with pytest.raises(AssertionError):
        check_no_hardcoded_password(broken('POSTGRES_INITDB_ARGS: "--no-sync"', literal))


def test_check_rejects_bind_mounted_data_dir() -> None:
    text = broken("postgres_data:/var/lib/postgresql/data", "./pgdata:/var/lib/postgresql/data")
    with pytest.raises(AssertionError):
        check_named_volumes(text)


def test_check_rejects_undeclared_volume() -> None:
    with pytest.raises(AssertionError):
        check_named_volumes(broken("  redis_data:\n", ""))


def test_check_rejects_missing_init_mount() -> None:
    with pytest.raises(AssertionError):
        check_init_dir_mounted(broken("/docker-entrypoint-initdb.d", "/elsewhere"))


def test_check_rejects_init_sql_without_test_db() -> None:
    with pytest.raises(AssertionError):
        check_init_sql("CREATE DATABASE other;")


def test_check_rejects_env_file_pointing_elsewhere() -> None:
    with pytest.raises(AssertionError):
        check_env_file(broken("path: ../.env", "path: ./.env"))


def test_postgres_image_is_bookworm_variant() -> None:
    check_postgres_bookworm(compose_text())


def test_compose_does_not_disable_fsync_or_full_page_writes() -> None:
    check_unsafe_durability_absent(compose_text())


def test_check_rejects_unix_socket_postgres_probe() -> None:
    text = broken("pg_isready -h 127.0.0.1", "pg_isready -h /var/run/postgresql")
    with pytest.raises(AssertionError):
        check_postgres_tcp_probe(text)


def test_check_rejects_env_example_as_env_file() -> None:
    with pytest.raises(AssertionError):
        check_env_file(broken("path: ../.env", "path: ../.env.example"))


def test_check_rejects_optional_env_file() -> None:
    with pytest.raises(AssertionError):
        check_env_file(broken("required: true", "required: false"))


def test_check_rejects_alpine_postgres() -> None:
    with pytest.raises(AssertionError):
        check_postgres_bookworm(broken("16.10-bookworm", "16.10-alpine"))


@pytest.mark.parametrize("flag", ["fsync=off", "full_page_writes=off"])
def test_check_rejects_unsafe_durability_flags(flag: str) -> None:
    text = broken('"synchronous_commit=off"]', f'"synchronous_commit=off", "-c", "{flag}"]')
    with pytest.raises(AssertionError):
        check_unsafe_durability_absent(text)


# ------------------------------------------------------- API and worker (S-1.3.2)

API_DIR = REPO_ROOT / "apps" / "api"


def depends_on_conditions(text: str, service: str) -> dict[str, str]:
    """Map each `depends_on` entry of a service to its `condition` (long syntax only)."""
    result: dict[str, str] = {}
    current = ""
    inside = False
    for line in service_block(text, service).splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped == "depends_on:":
            inside = True
            continue
        if inside and indent <= 4 and stripped:
            break
        if inside and indent == 6 and stripped.endswith(":"):
            current = stripped[:-1]
        elif inside and stripped.startswith("condition:"):
            result[current] = stripped.split(":", 1)[1].strip()
    return result


def test_dev_stack_services_exist() -> None:
    for service in ("postgres", "redis", "migrate", "api", "worker"):
        assert service_block(compose_text(), service), service


def test_api_worker_and_migrate_share_one_image_built_from_the_dev_target() -> None:
    for service in ("migrate", "api", "worker"):
        block = service_block(compose_text(), service)
        assert re.search(r"image:\s*mtg-stats-api:dev", block), service
        assert re.search(r"target:\s*dev", block), service


def test_container_services_read_the_root_env_file() -> None:
    for service in ("migrate", "api", "worker"):
        assert "../.env" in service_block(compose_text(), service)


def test_dockerfile_has_a_pinned_dev_target_running_as_a_non_root_user() -> None:
    text = read_text(API_DIR / "Dockerfile")
    assert re.search(r"^FROM python:3\.12\.\d+-slim-bookworm AS dev$", text, re.M)
    assert "UV_PROJECT_ENVIRONMENT=/opt/venv" in text
    assert re.search(r"^USER app$", text, re.M)
