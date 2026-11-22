"""Environment-driven settings (S-1.4.1).

Every value comes from environment variables. The repo-root ``.env`` is read only when
``APP_ENV=dev``; ``test`` and ``prod`` read the process environment only. No secret has a
default in code.
"""

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

WORKSPACE_MARKER = "pnpm-workspace.yaml"
DEV_WEB_ORIGIN = "http://localhost:5173"
DEFAULT_REPO_SLUG = "Rocket-Racoon/mtg-stats"
REPO_SLUG_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
DATABASE_URL_PREFIX = "postgresql+psycopg://"
REDIS_URL_SCHEMES = ("redis://", "rediss://")
JWT_SECRET_MIN_LENGTH = 32
JWT_SECRET_PLACEHOLDER = "CHANGE_ME"  # noqa: S105  (the rejected placeholder, not a secret)
JWT_SECRET_MESSAGE = (
    f"JWT_SECRET must be at least {JWT_SECRET_MIN_LENGTH} characters and not the "
    f"{JWT_SECRET_PLACEHOLDER} placeholder; generate one with: "
    'python -c "import secrets; print(secrets.token_urlsafe(48))"'
)

AppEnv = Literal["dev", "test", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Application settings, validated at startup."""

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=True,
        hide_input_in_errors=True,
    )

    app_env: AppEnv = Field(validation_alias="APP_ENV")
    database_url: str = Field(validation_alias="DATABASE_URL")
    log_level: LogLevel = Field(default="INFO", validation_alias="LOG_LEVEL")
    db_connect_timeout: int = Field(default=5, ge=1, validation_alias="DB_CONNECT_TIMEOUT")
    redis_url: str = Field(validation_alias="REDIS_URL")
    git_sha: str = Field(default="dev", validation_alias="GIT_SHA")
    worker_queue_name: str = Field(
        default="arq:queue", min_length=1, validation_alias="WORKER_QUEUE_NAME"
    )
    worker_shutdown_grace_seconds: int = Field(
        default=30, ge=0, validation_alias="WORKER_SHUTDOWN_GRACE_SECONDS"
    )
    scheduler_enabled: bool = Field(default=True, validation_alias="SCHEDULER_ENABLED")
    public_web_url: str = Field(validation_alias="PUBLIC_WEB_URL")
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CORS_ALLOWED_ORIGINS"
    )
    argon2_time_cost: int = Field(default=3, ge=1, validation_alias="ARGON2_TIME_COST")
    argon2_memory_cost: int = Field(default=65536, ge=1, validation_alias="ARGON2_MEMORY_COST")
    argon2_parallelism: int = Field(default=4, ge=1, validation_alias="ARGON2_PARALLELISM")
    jwt_secret: SecretStr = Field(validation_alias="JWT_SECRET")
    access_token_ttl_minutes: int = Field(
        default=15, ge=1, le=1440, validation_alias="ACCESS_TOKEN_TTL_MINUTES"
    )
    refresh_token_ttl_days: int = Field(
        default=30, ge=1, le=365, validation_alias="REFRESH_TOKEN_TTL_DAYS"
    )

    signin_max_failures_per_username: int = Field(
        default=5, ge=1, validation_alias="SIGNIN_MAX_FAILURES_PER_USERNAME"
    )
    signin_max_failures_per_ip: int = Field(
        default=20, ge=1, validation_alias="SIGNIN_MAX_FAILURES_PER_IP"
    )
    public_max_requests_per_ip: int = Field(
        default=30, ge=1, validation_alias="PUBLIC_MAX_REQUESTS_PER_IP"
    )
    rate_limit_window_seconds: int = Field(
        default=900, ge=1, validation_alias="RATE_LIMIT_WINDOW_SECONDS"
    )
    redis_call_timeout_seconds: float = Field(
        default=1.0, gt=0, validation_alias="REDIS_CALL_TIMEOUT_SECONDS"
    )
    rate_limit_key_prefix: str = Field(
        default="mtgstats:rl", min_length=1, validation_alias="RATE_LIMIT_KEY_PREFIX"
    )
    repo_slug: str = Field(default=DEFAULT_REPO_SLUG, validation_alias="REPO_SLUG")
    scryfall_min_interval_ms: int = Field(
        default=100, ge=100, validation_alias="SCRYFALL_MIN_INTERVAL_MS"
    )
    # Commander Spellbook client (S-5.2.1); the host is a constant in the client, not a setting.
    spellbook_timeout_seconds: float = Field(
        default=10.0, gt=0, validation_alias="SPELLBOOK_TIMEOUT_SECONDS"
    )
    spellbook_cache_ttl_seconds: int = Field(
        default=604800, ge=1, validation_alias="SPELLBOOK_CACHE_TTL_SECONDS"
    )
    spellbook_min_interval_ms: int = Field(
        default=1000, ge=750, validation_alias="SPELLBOOK_MIN_INTERVAL_MS"
    )
    spellbook_max_retry_after_seconds: float = Field(
        default=30.0, ge=0, validation_alias="SPELLBOOK_MAX_RETRY_AFTER_SECONDS"
    )
    spellbook_max_pages: int = Field(default=5, ge=1, validation_alias="SPELLBOOK_MAX_PAGES")
    spellbook_lookup_deadline_seconds: float = Field(
        default=60.0, ge=1, le=600, validation_alias="SPELLBOOK_LOOKUP_DEADLINE_SECONDS"
    )

    # Deck-site client (S-4.2.1). Archidekt's developer asks for well under 40 requests a minute:
    # the floor of 2000 ms is 30 a minute and the default of 3000 ms is 20 a minute.
    deck_site_timeout_seconds: float = Field(
        default=10.0, gt=0, le=120, validation_alias="DECK_SITE_TIMEOUT_SECONDS"
    )
    deck_site_total_deadline_seconds: float = Field(
        default=45.0, gt=0, le=300, validation_alias="DECK_SITE_TOTAL_DEADLINE_SECONDS"
    )
    deck_site_cache_ttl_seconds: int = Field(
        default=900, ge=1, le=86400, validation_alias="DECK_SITE_CACHE_TTL_SECONDS"
    )
    deck_site_max_retries: int = Field(
        default=2, ge=0, le=2, validation_alias="DECK_SITE_MAX_RETRIES"
    )
    deck_site_retry_after_cap_seconds: float = Field(
        default=30.0, ge=0, le=300, validation_alias="DECK_SITE_RETRY_AFTER_CAP_SECONDS"
    )
    deck_site_max_response_bytes: int = Field(
        default=5_000_000, ge=1024, le=50_000_000, validation_alias="DECK_SITE_MAX_RESPONSE_BYTES"
    )
    deck_site_max_concurrency: int = Field(
        default=2, ge=1, le=10, validation_alias="DECK_SITE_MAX_CONCURRENCY"
    )
    deck_site_archidekt_min_interval_ms: int = Field(
        default=3000, ge=2000, validation_alias="DECK_SITE_ARCHIDEKT_MIN_INTERVAL_MS"
    )

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        return _check_url(value, (DATABASE_URL_PREFIX,), f"must start with {DATABASE_URL_PREFIX}")

    @field_validator("redis_url")
    @classmethod
    def _check_redis_url(cls, value: str) -> str:
        return _check_url(value, REDIS_URL_SCHEMES, "must start with redis:// or rediss://")

    @field_validator("repo_slug")
    @classmethod
    def _check_repo_slug(cls, value: str) -> str:
        value = value.strip()
        if not REPO_SLUG_PATTERN.fullmatch(value):
            raise ValueError("REPO_SLUG must look like owner/repo")
        return value

    @property
    def repo_url(self) -> str:
        """The GitHub repository URL sent in the User-Agent of external API calls."""
        return f"https://github.com/{self.repo_slug}"

    @field_validator("public_web_url")
    @classmethod
    def _check_public_web_url(cls, value: str) -> str:
        return _normalise_public_web_url(value)

    @field_validator("jwt_secret")
    @classmethod
    def _check_jwt_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < JWT_SECRET_MIN_LENGTH or secret == JWT_SECRET_PLACEHOLDER:
            raise ValueError(JWT_SECRET_MESSAGE)
        return value

    @field_validator("git_sha")
    @classmethod
    def _empty_git_sha_is_dev(cls, value: str) -> str:
        return value.strip() or "dev"

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _check_origins(cls, value: list[str]) -> list[str]:
        return [_normalise_origin(origin) for origin in value]

    @model_validator(mode="after")
    def _argon2_memory_minimum(self) -> Self:
        if self.argon2_memory_cost < 8 * self.argon2_parallelism:
            raise ValueError("ARGON2_MEMORY_COST must be at least 8 x ARGON2_PARALLELISM (KiB)")
        return self

    @model_validator(mode="after")
    def _prod_requires_https_web_url(self) -> Self:
        if self.app_env == "prod" and not self.public_web_url.startswith("https://"):
            raise ValueError("PUBLIC_WEB_URL must use https:// when APP_ENV=prod")
        return self

    @model_validator(mode="after")
    def _dev_allows_web_origin(self) -> Self:
        if self.app_env == "dev" and DEV_WEB_ORIGIN not in self.cors_allowed_origins:
            self.cors_allowed_origins = [DEV_WEB_ORIGIN, *self.cors_allowed_origins]
        return self


def _check_url(value: str, prefixes: tuple[str, ...], start_msg: str) -> str:
    """Check a connection URL's scheme prefix, host and port; return it unchanged."""
    msg = f"{start_msg} and have a host and a valid port"
    if not value.startswith(prefixes):
        raise ValueError(msg)
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018  (raises ValueError on an invalid port)
    except ValueError:
        raise ValueError(msg) from None
    if not parts.hostname:
        raise ValueError(msg)
    return value


def _normalise_origin(origin: str) -> str:
    """Validate a CORS origin and return it as ``scheme://host[:port]`` in lowercase."""
    msg = f"invalid origin {origin!r}; expected http(s)://host[:port] with no path"
    if "*" in origin or origin == "null":
        raise ValueError("wildcard and null origins are not allowed (credentials are sent)")
    if any(ch in origin for ch in "?#@") or any(ch.isspace() for ch in origin):
        raise ValueError(msg)
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        raise ValueError(msg) from None
    host = parts.hostname
    if parts.scheme.lower() not in {"http", "https"} or not host or parts.path:
        raise ValueError(msg)
    if ":" in host:
        host = f"[{host}]"
    scheme = parts.scheme.lower()
    if port == {"http": 80, "https": 443}[scheme]:
        port = None
    return f"{scheme}://{host}" + (f":{port}" if port is not None else "")


def _normalise_public_web_url(value: str) -> str:
    """Validate ``PUBLIC_WEB_URL`` and return it without trailing slashes.

    Accepts ``http(s)://host[:port][/path-prefix]``; rejects userinfo, query, fragment, whitespace
    and backslashes (browsers read a backslash as a slash, so it would hide a path inside the
    host). Scheme and host are lowercased; the path keeps its case. The message never echoes
    the value.
    """
    msg = (
        "must be an http(s) URL with a host, an optional port and path, "
        "and no credentials, query or fragment"
    )
    if any(ch in value for ch in "?#@\\") or any(ch.isspace() for ch in value):
        raise ValueError(msg)
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018  (raises ValueError on an invalid port)
    except ValueError:
        raise ValueError(msg) from None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(msg)
    # Canonical form: lowercase scheme and host (no userinfo is possible here), path kept as given.
    return f"{parts.scheme}://{parts.netloc.lower()}{parts.path}".rstrip("/")


def find_env_file() -> Path | None:
    """Locate the repo-root ``.env`` by walking up to ``pnpm-workspace.yaml``, or ``None``."""
    for directory in Path(__file__).resolve().parents:
        if (directory / WORKSPACE_MARKER).is_file():
            env = directory / ".env"
            return env if env.is_file() else None
    return None


class _AppEnvProbe(BaseSettings):
    """Reads only ``APP_ENV`` from a ``.env`` file, to decide whether the file may be used."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=True)

    app_env: str | None = Field(default=None, validation_alias="APP_ENV")


def dev_env_file() -> Path | None:
    """Return the root ``.env`` only when the effective ``APP_ENV`` is ``dev``."""
    env_file = find_env_file()
    if env_file is None:
        return None
    app_env = os.environ.get("APP_ENV")
    if app_env is None:
        app_env = _AppEnvProbe(_env_file=env_file).app_env
    return env_file if app_env == "dev" else None


_dev_env_file = dev_env_file  # private alias kept for existing callers


def load_settings() -> Settings:
    """Build settings from the environment (and the root ``.env`` in dev only)."""
    return Settings(_env_file=dev_env_file())


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor."""
    return load_settings()
