"""Tests of the User-Agent version fallback (S-3.1.2)."""

import logging
from pathlib import Path

import pytest

from app.core import user_agent


def test_version_falls_back_to_zero_and_warns_when_pyproject_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(user_agent, "PYPROJECT_PATH", tmp_path / "missing.toml")
    user_agent.api_version.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger="app.core.user_agent"):
            assert user_agent.api_version() == "0.0.0"
    finally:
        user_agent.api_version.cache_clear()
    assert "0.0.0" in caplog.text


def test_version_falls_back_when_the_project_table_has_no_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broken = tmp_path / "pyproject.toml"
    broken.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    monkeypatch.setattr(user_agent, "PYPROJECT_PATH", broken)
    user_agent.api_version.cache_clear()
    try:
        assert user_agent.api_version() == "0.0.0"
    finally:
        user_agent.api_version.cache_clear()
