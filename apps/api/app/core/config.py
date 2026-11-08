"""Environment-driven settings (S-1.4.1).

Every value comes from environment variables. The repo-root ``.env`` is read only when
``APP_ENV=dev``; ``test`` and ``prod`` read the process environment only. No secret has a
default in code.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]
ENV_FILE = REPO_ROOT / ".env"
DEV_WEB_ORIGIN = "http://localhost:5173"
DATABASE_URL_PREFIX = "postgresql+psycopg://"

AppEnv = Literal["dev", "test", "prod"]


class Settings(BaseSettings):
    """Application settings, validated at startup."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    app_env: AppEnv = Field(validation_alias="APP_ENV")
    database_url: str = Field(validation_alias="DATABASE_URL")
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="CORS_ALLOWED_ORIGINS"
    )

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        if not value.startswith(DATABASE_URL_PREFIX) or not urlsplit(value).hostname:
            msg = f"must be a {DATABASE_URL_PREFIX}user:password@host:port/db URL"
            raise ValueError(msg)
        return value

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _check_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            if "*" in origin:
                msg = "wildcard origins are not allowed (credentials are sent)"
                raise ValueError(msg)
            parts = urlsplit(origin)
            if parts.scheme not in {"http", "https"} or not parts.netloc or parts.path:
                msg = f"invalid origin {origin!r}; expected scheme://host[:port]"
                raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _dev_allows_web_origin(self) -> Self:  # noqa: N804
        if self.app_env == "dev" and DEV_WEB_ORIGIN not in self.cors_allowed_origins:
            self.cors_allowed_origins = [DEV_WEB_ORIGIN, *self.cors_allowed_origins]
        return self


def _dev_env_file() -> Path | None:
    """Return the root ``.env`` only when the effective ``APP_ENV`` is ``dev``."""
    app_env = os.environ.get("APP_ENV")
    if app_env is None and ENV_FILE.is_file():
        app_env = dotenv_values(ENV_FILE).get("APP_ENV")
    if app_env == "dev" and ENV_FILE.is_file():
        return ENV_FILE
    return None


def load_settings() -> Settings:
    """Build settings from the environment (and the root ``.env`` in dev only)."""
    return Settings(_env_file=_dev_env_file())


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor."""
    return load_settings()
