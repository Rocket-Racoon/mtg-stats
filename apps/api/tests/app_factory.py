"""Builds the full application for tests that live inside a domain package.

Domain tests obey their domain's import rules (architecture §4.2), so they may not import
``app.main`` themselves. Tests under ``apps/api/tests/`` may, and this seam gives domain tests
one place to get a fresh app from.
"""

from fastapi import FastAPI


def new_app() -> FastAPI:
    """A fresh ``create_app()`` (the import builds the module-level app; env is set by conftest)."""
    from app.main import create_app  # noqa: PLC0415

    return create_app()
