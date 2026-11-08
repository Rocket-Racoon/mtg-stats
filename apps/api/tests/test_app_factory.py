"""Unit tests for settings and the app factory (S-1.4.1)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import config
from app.core.config import DEV_WEB_ORIGIN, Settings, load_settings
from app.main import create_app

DB = "postgresql+psycopg://u:p@localhost:5432/db"


def make(**kw: str) -> Settings:
    base = {"APP_ENV": "test", "DATABASE_URL": DB}
    return Settings(**{**base, **kw})  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_ENV", "DATABASE_URL", "CORS_ALLOWED_ORIGINS"):
        monkeypatch.delenv(name, raising=False)


def test_missing_database_url_error_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        load_settings()


@pytest.mark.parametrize("url", ["postgresql://u:p@h/db", "sqlite:///x", "postgresql+psycopg://"])
def test_malformed_database_url_is_rejected(url: str) -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        make(DATABASE_URL=url)


def test_unknown_app_env_is_rejected() -> None:
    with pytest.raises(ValidationError, match="APP_ENV"):
        make(APP_ENV="staging")


@pytest.mark.parametrize("origins", ["*", "https://*.example.com", "not-a-url", "https://a.com/x"])
def test_bad_origins_are_rejected(origins: str) -> None:
    with pytest.raises(ValidationError, match="CORS_ALLOWED_ORIGINS"):
        make(CORS_ALLOWED_ORIGINS=origins)


def test_origins_parse_comma_separated_and_dev_adds_web_origin() -> None:
    assert make(CORS_ALLOWED_ORIGINS="https://a.com, https://b.com").cors_allowed_origins == [
        "https://a.com",
        "https://b.com",
    ]
    assert make(APP_ENV="dev").cors_allowed_origins == [DEV_WEB_ORIGIN]
    assert make(APP_ENV="prod").cors_allowed_origins == []


def test_dotenv_loaded_in_dev_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(f"APP_ENV=dev\nDATABASE_URL={DB}\n")
    monkeypatch.setattr(config, "ENV_FILE", env)
    assert load_settings().app_env == "dev"
    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        load_settings()


def test_dotenv_ignored_when_file_says_prod(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = tmp_path / ".env"
    env.write_text(f"APP_ENV=prod\nDATABASE_URL={DB}\n")
    monkeypatch.setattr(config, "ENV_FILE", env)
    with pytest.raises(ValidationError, match="APP_ENV"):
        load_settings()


def test_openapi_served_under_api_v1() -> None:
    client = TestClient(create_app(make()))
    assert client.get("/api/v1/openapi.json").status_code == 200
    assert client.get("/openapi.json").status_code == 404


def test_cors_prod_unknown_origin_gets_no_headers_and_allowed_gets_credentials() -> None:
    client = TestClient(create_app(make(APP_ENV="prod", CORS_ALLOWED_ORIGINS="https://a.com")))
    bad = client.get("/api/v1/openapi.json", headers={"Origin": "https://evil.com"})
    assert "access-control-allow-origin" not in bad.headers
    ok = client.get("/api/v1/openapi.json", headers={"Origin": "https://a.com"})
    assert ok.headers["access-control-allow-origin"] == "https://a.com"
    assert ok.headers["access-control-allow-credentials"] == "true"


def test_cors_dev_allows_vite_origin() -> None:
    client = TestClient(create_app(make(APP_ENV="dev")))
    resp = client.get("/api/v1/openapi.json", headers={"Origin": DEV_WEB_ORIGIN})
    assert resp.headers["access-control-allow-origin"] == DEV_WEB_ORIGIN


def test_prod_with_no_origins_has_no_cors() -> None:
    client = TestClient(create_app(make(APP_ENV="prod")))
    resp = client.get("/api/v1/openapi.json", headers={"Origin": DEV_WEB_ORIGIN})
    assert "access-control-allow-origin" not in resp.headers
