"""Acceptance tests for S-1.4.1: environment settings and the app factory."""

import importlib
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import config
from app.core.config import Settings, get_settings, load_settings
from app.main import create_app

DB_URL = "postgresql+psycopg://user:CHANGE_ME@db.example.com:5432/mtg"
WEB_DEV_ORIGIN = "http://localhost:5173"
ALLOWED = "https://app.example.com"
EVIL = "https://evil.example.org"


_env_path: list[Path] = []


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Start from an empty environment and never read a real ``.env``."""
    for name in ("APP_ENV", "DATABASE_URL", "CORS_ALLOWED_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    env_path = tmp_path / ".env"
    _env_path.append(env_path)
    monkeypatch.setattr(config, "find_env_file", lambda: env_path if env_path.is_file() else None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _make(monkeypatch: pytest.MonkeyPatch, env: str, origins: str = "") -> Settings:
    _env(monkeypatch, APP_ENV=env, DATABASE_URL=DB_URL)
    if origins:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", origins)
    return load_settings()


def _write_dotenv(content: str) -> None:
    _env_path[-1].write_text(content, encoding="utf-8")


# ---------------------------------------------------------------- AC1


def test_settings_values_come_from_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(
        monkeypatch,
        APP_ENV="prod",
        DATABASE_URL=DB_URL,
        CORS_ALLOWED_ORIGINS=f"{ALLOWED}, https://other.example.com",
    )
    s = load_settings()
    assert s.app_env == "prod"
    assert s.database_url == DB_URL
    assert s.cors_allowed_origins == [ALLOWED, "https://other.example.com"]


@pytest.mark.parametrize("env", ["dev", "test", "prod"])
def test_app_env_accepts_each_allowed_value(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    assert _make(monkeypatch, env).app_env == env


@pytest.mark.parametrize("bad", ["staging", "production", "", "DEV"])
def test_invalid_app_env_fails_naming_app_env(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    _env(monkeypatch, APP_ENV=bad, DATABASE_URL=DB_URL)
    with pytest.raises(ValidationError, match="APP_ENV"):
        load_settings()


def test_missing_app_env_fails_naming_app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, DATABASE_URL=DB_URL)
    with pytest.raises(ValidationError, match="APP_ENV"):
        load_settings()


def test_dotenv_is_honoured_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_dotenv(f"DATABASE_URL={DB_URL}\nCORS_ALLOWED_ORIGINS={ALLOWED}\n")
    _env(monkeypatch, APP_ENV="dev")
    s = load_settings()
    assert s.database_url == DB_URL
    assert ALLOWED in s.cors_allowed_origins


def test_dotenv_app_env_dev_is_honoured_when_process_env_has_none() -> None:
    _write_dotenv(f"APP_ENV=dev\nDATABASE_URL={DB_URL}\n")
    s = load_settings()
    assert s.app_env == "dev"
    assert s.database_url == DB_URL


@pytest.mark.parametrize("env", ["test", "prod"])
def test_dotenv_is_ignored_outside_dev(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    _write_dotenv(f"DATABASE_URL={DB_URL}\n")
    _env(monkeypatch, APP_ENV=env)
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        load_settings()


@pytest.mark.parametrize("env", ["test", "prod"])
def test_dotenv_does_not_override_process_env_outside_dev(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    _write_dotenv("DATABASE_URL=postgresql+psycopg://x:CHANGE_ME@fromfile:5432/db\n")
    s = _make(monkeypatch, env)
    assert s.database_url == DB_URL


def test_process_env_wins_over_dotenv_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_dotenv("DATABASE_URL=postgresql+psycopg://x:CHANGE_ME@fromfile:5432/db\n")
    _env(monkeypatch, APP_ENV="dev", DATABASE_URL=DB_URL)
    assert load_settings().database_url == DB_URL


def test_dotenv_app_env_prod_is_not_loaded() -> None:
    _write_dotenv(f"APP_ENV=prod\nDATABASE_URL={DB_URL}\n")
    with pytest.raises(ValidationError, match="APP_ENV"):
        load_settings()


def test_no_secret_field_has_a_default_in_code() -> None:
    secret_name = re.compile(r"(SECRET|KEY|TOKEN|PASSWORD|_URL)$", re.IGNORECASE)
    checked = []
    for name, field in Settings.model_fields.items():
        alias = str(field.validation_alias or name)
        if secret_name.search(alias) or secret_name.search(name):
            checked.append(alias)
            assert field.is_required(), f"{alias} must not have a default"
    assert "DATABASE_URL" in checked


def test_no_default_in_code_looks_like_a_credential_url() -> None:
    for name, field in Settings.model_fields.items():
        if field.is_required():
            continue
        default = field.get_default(call_default_factory=True)
        if isinstance(default, str):
            assert "://" not in default, name
            assert "@" not in default, name


# ---------------------------------------------------------------- AC2

BAD_URLS = [
    "mysql://u:CHANGE_ME@localhost:3306/db",
    "postgresql://u:CHANGE_ME@localhost:5432/db",
    "postgresql+psycopg://",
    "postgresql+psycopg:///dbname",
    "not a url",
    "",
]


def test_missing_database_url_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, APP_ENV="test")
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        load_settings()
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", BAD_URLS)
def test_malformed_database_url_names_the_variable(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    _env(monkeypatch, APP_ENV="test", DATABASE_URL=bad)
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        load_settings()


def test_create_app_fails_naming_missing_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, APP_ENV="test")
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        create_app()


def test_create_app_fails_naming_malformed_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, APP_ENV="test", DATABASE_URL="mysql://u:CHANGE_ME@localhost:3306/db")
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        create_app()


def test_valid_database_url_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _make(monkeypatch, "test").database_url == DB_URL


# ---------------------------------------------------------------- AC3


def test_openapi_is_served_under_api_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(_make(monkeypatch, "test")))
    resp = client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["openapi"].startswith("3.")
    assert "info" in doc


def test_openapi_is_not_served_at_the_root(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(_make(monkeypatch, "test")))
    assert client.get("/openapi.json").status_code == 404


def test_docs_are_not_served_at_the_root(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(_make(monkeypatch, "test")))
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_every_route_is_mounted_under_api_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(_make(monkeypatch, "test"))
    assert app.routes, "the app must expose at least the OpenAPI route"
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None:
            # An included router: the domain routers are mounted through it.
            path = route.original_router.prefix  # type: ignore[attr-defined]
        assert path.startswith("/api/v1"), path


def test_openapi_paths_are_all_under_api_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(_make(monkeypatch, "test")))
    for path in client.get("/api/v1/openapi.json").json().get("paths", {}):
        assert path.startswith("/api/v1"), path


# ---------------------------------------------------------------- AC4


def _client(monkeypatch: pytest.MonkeyPatch, env: str, origins: str = "") -> TestClient:
    return TestClient(create_app(_make(monkeypatch, env, origins)))


def _preflight(client: TestClient, origin: str) -> httpx.Response:
    return client.options(
        "/api/v1/openapi.json",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


def test_prod_disallowed_origin_gets_no_cors_headers_on_simple_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, "prod", ALLOWED)
    resp = client.get("/api/v1/openapi.json", headers={"Origin": EVIL})
    assert "access-control-allow-origin" not in resp.headers


def test_prod_disallowed_origin_preflight_gets_no_allow_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = _preflight(_client(monkeypatch, "prod", ALLOWED), EVIL)
    assert "access-control-allow-origin" not in resp.headers


def test_prod_disallowed_origin_gets_no_cors_headers_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4 says no CORS headers: that includes allow-credentials, not just allow-origin."""
    client = _client(monkeypatch, "prod", ALLOWED)
    simple = client.get("/api/v1/openapi.json", headers={"Origin": EVIL})
    pre = _preflight(client, EVIL)
    for resp in (simple, pre):
        cors = [h for h in resp.headers if h.lower().startswith("access-control-")]
        assert cors == []


def test_prod_with_empty_origin_list_returns_no_cors_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, "prod")
    resp = client.get("/api/v1/openapi.json", headers={"Origin": ALLOWED})
    assert "access-control-allow-origin" not in resp.headers


def test_prod_does_not_allow_the_dev_web_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "prod", ALLOWED)
    resp = client.get("/api/v1/openapi.json", headers={"Origin": WEB_DEV_ORIGIN})
    assert "access-control-allow-origin" not in resp.headers


def test_prod_allowed_origin_is_echoed_with_credentials_on_simple_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, "prod", ALLOWED)
    resp = client.get("/api/v1/openapi.json", headers={"Origin": ALLOWED})
    assert resp.headers["access-control-allow-origin"] == ALLOWED
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_prod_allowed_origin_preflight_is_echoed_with_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = _preflight(_client(monkeypatch, "prod", ALLOWED), ALLOWED)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_each_listed_origin_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    second = "https://second.example.com"
    client = _client(monkeypatch, "prod", f"{ALLOWED},{second}")
    for origin in (ALLOWED, second):
        resp = client.get("/api/v1/openapi.json", headers={"Origin": origin})
        assert resp.headers["access-control-allow-origin"] == origin


def test_dev_allows_web_dev_server_origin_with_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, "dev")
    resp = client.get("/api/v1/openapi.json", headers={"Origin": WEB_DEV_ORIGIN})
    assert resp.headers["access-control-allow-origin"] == WEB_DEV_ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"
    pre = _preflight(client, WEB_DEV_ORIGIN)
    assert pre.headers["access-control-allow-origin"] == WEB_DEV_ORIGIN


def test_dev_still_rejects_unlisted_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, "dev")
    resp = client.get("/api/v1/openapi.json", headers={"Origin": EVIL})
    assert "access-control-allow-origin" not in resp.headers


@pytest.mark.parametrize("env", ["dev", "test", "prod"])
@pytest.mark.parametrize("origin", ["*", "https://*.example.com", f"{ALLOWED},*"])
def test_wildcard_origin_is_rejected_at_startup(
    monkeypatch: pytest.MonkeyPatch, env: str, origin: str
) -> None:
    _env(monkeypatch, APP_ENV=env, DATABASE_URL=DB_URL, CORS_ALLOWED_ORIGINS=origin)
    with pytest.raises(ValidationError, match="CORS_ALLOWED_ORIGINS"):
        create_app()
    with pytest.raises(ValidationError, match="CORS_ALLOWED_ORIGINS"):
        load_settings()


@pytest.mark.parametrize("origin", ["app.example.com", "ftp://app.example.com", "https://a.com/x"])
def test_malformed_origin_is_rejected_at_startup(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    _env(monkeypatch, APP_ENV="prod", DATABASE_URL=DB_URL, CORS_ALLOWED_ORIGINS=origin)
    with pytest.raises(ValidationError, match="CORS_ALLOWED_ORIGINS"):
        load_settings()


# ------------------------------------------------- review fixes (S-1.4.1)

SECRET = "s3cr" + "etvalue"


def test_malformed_database_url_error_does_not_echo_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = "mysql://admin:" + SECRET + "@db.example.com/x"  # secrets-check: allow
    _env(monkeypatch, APP_ENV="test", DATABASE_URL=bad)
    with pytest.raises(ValidationError) as info:
        load_settings()
    assert "DATABASE_URL" in str(info.value)
    assert SECRET not in str(info.value)


def test_missing_variable_error_does_not_echo_other_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = "postgresql+psycopg://admin:" + SECRET + "@db.example.com/x"  # secrets-check: allow
    _env(monkeypatch, DATABASE_URL=other)
    with pytest.raises(ValidationError) as info:
        load_settings()
    assert "APP_ENV" in str(info.value)
    assert SECRET not in str(info.value)
    assert "db.example.com" not in str(info.value)


def test_find_env_file_returns_none_without_a_workspace_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    start = tmp_path / "a" / "b"
    start.mkdir(parents=True)
    monkeypatch.undo()  # drop the autouse find_env_file patch to test the real helper
    monkeypatch.setattr(config, "__file__", str(start / "config.py"))
    assert config.find_env_file() is None


def test_find_env_file_finds_env_next_to_the_workspace_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    start = tmp_path / "apps" / "api"
    start.mkdir(parents=True)
    (tmp_path / "pnpm-workspace.yaml").write_text("packages: []\n")
    monkeypatch.undo()
    monkeypatch.setattr(config, "__file__", str(start / "config.py"))
    assert config.find_env_file() is None  # marker but no .env
    (tmp_path / ".env").write_text("APP_ENV=dev\n")
    assert config.find_env_file() == (tmp_path / ".env").resolve()


def test_importing_config_never_raises() -> None:
    importlib.reload(config)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows env var names are case-insensitive")
def test_lowercase_app_env_variable_is_not_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("app_env", "prod")
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    with pytest.raises(ValidationError, match="APP_ENV"):
        load_settings()


def test_dotenv_saying_dev_is_not_loaded_when_process_sets_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(f"APP_ENV=dev\nDATABASE_URL={DB_URL}\n")
    _env(monkeypatch, APP_ENV="prod")
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        load_settings()


def test_origins_are_normalised_to_lowercase(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _make(monkeypatch, "prod", "HTTPS://A.COM, http://B.com:8080")
    assert s.cors_allowed_origins == ["https://a.com", "http://b.com:8080"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://a.com?x=1",
        "https://a.com#frag",
        "https://user@a.com",
        "https://a.com/path",
        "https://a.com/",
        "https://a.com:notaport",
        "https://a.com:99999",
        "null",
    ],
)
def test_invalid_origin_forms_are_rejected_at_startup(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    _env(monkeypatch, APP_ENV="prod", DATABASE_URL=DB_URL, CORS_ALLOWED_ORIGINS=origin)
    with pytest.raises(ValidationError, match="CORS_ALLOWED_ORIGINS"):
        create_app()


@pytest.mark.parametrize(
    "url",
    ["postgresql+psycopg://u@h:notaport/db", "postgresql+psycopg://u@h:99999/db"],
)
def test_bad_port_in_database_url_is_rejected_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    _env(monkeypatch, APP_ENV="test", DATABASE_URL=url)
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        load_settings()


@pytest.mark.parametrize("origin", [None, EVIL, ALLOWED])
def test_vary_origin_is_present_exactly_once_when_cors_is_configured(
    monkeypatch: pytest.MonkeyPatch, origin: str | None
) -> None:
    client = _client(monkeypatch, "prod", ALLOWED)
    headers = {"Origin": origin} if origin else {}
    resp = client.get("/api/v1/openapi.json", headers=headers)
    vary = [t.strip().lower() for v in resp.headers.get_list("vary") for t in v.split(",")]
    assert vary.count("origin") == 1


@pytest.mark.parametrize("origin", ["https://A.com", "https://a.com/", "null"])
def test_request_origin_must_match_exactly(monkeypatch: pytest.MonkeyPatch, origin: str) -> None:
    client = _client(monkeypatch, "prod", "https://a.com")
    resp = client.get("/api/v1/openapi.json", headers={"Origin": origin})
    assert "access-control-allow-origin" not in resp.headers


def test_prod_with_no_origins_gives_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _make(monkeypatch, "prod").cors_allowed_origins == []


def test_dev_with_no_origins_gives_only_the_web_dev_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _make(monkeypatch, "dev").cors_allowed_origins == [WEB_DEV_ORIGIN]


def test_dev_does_not_duplicate_the_web_dev_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _make(monkeypatch, "dev", f"{WEB_DEV_ORIGIN},{ALLOWED}")
    assert s.cors_allowed_origins == [WEB_DEV_ORIGIN, ALLOWED]
