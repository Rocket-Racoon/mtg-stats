"""Shared pytest configuration for the API test suite."""

import os

pytest_plugins = ["pytester"]

# `app.main` builds `app = create_app()` at import time, which needs valid settings.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/mtg_stats_test")
