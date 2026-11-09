"""Shared pytest configuration for the API test suite."""

import os

pytest_plugins = ["pytester"]

# `app.main` builds `app = create_app()` at import time, which needs valid settings.
# Forced, not defaulted, so a shell with APP_ENV=dev cannot leak into the run.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "postgresql+psycopg://u:CHANGE_ME@localhost:5432/mtg_stats_test"
