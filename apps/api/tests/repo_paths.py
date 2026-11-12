"""Locate the monorepo root from a test file, tolerating the API-only container layout."""

from pathlib import Path


def repo_root(test_file: str | Path) -> Path:
    """Return the monorepo root for a file directly under ``apps/api/tests``.

    In the API container only ``/app`` exists, so the file has fewer than four parents and the
    plain ``parents[3]`` would raise ``IndexError`` at import time. Fall back to the topmost
    directory; callers then skip on a missing repo-root marker file.
    """
    parents = Path(test_file).resolve().parents
    return parents[3] if len(parents) > 3 else parents[-1]
