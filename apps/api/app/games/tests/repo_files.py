"""Locate shared repository files (the game-core contract and vectors) from the API tests.

The ``require`` helpers are for tests that compare the Python package with the contract or with
one vector (S-6.1.1 parity and reference tests). The API container holds only ``apps/api``, so
outside CI a missing file skips such a test; on CI (the whole repository is checked out) it fails.

This differs on purpose from the vector runner (``test_vectors.py``, ``test_vector_cases.py``,
``test_vector_runner_behavior.py``): CONTRACT.md 8.5 says a missing or empty vectors directory
fails loudly, so the runner fails everywhere and never skips. A skipped parity test hides nothing
while the runner is red, and the runner is what the gate relies on for the vectors. Making these
helpers fail everywhere would only break running the API tests inside the container layout.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

# apps/api/app/games/tests/repo_files.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[5]
GAME_CORE = REPO_ROOT / "packages" / "game-core"
VECTORS_DIR = GAME_CORE / "vectors"
CONTRACT_PATH = GAME_CORE / "CONTRACT.md"


def require(path: Path) -> Path:
    """Return ``path``; skip when absent outside CI, fail when absent on CI."""
    if path.exists():
        return path
    if os.environ.get("CI", "").strip().lower() not in {"", "0", "false", "no", "off"}:
        pytest.fail(f"missing on CI: {path}", pytrace=False)
    pytest.skip(f"{path} is not available in this layout")


def contract_text() -> str:
    return require(CONTRACT_PATH).read_text(encoding="utf-8")


def load_vector(name: str) -> dict[str, Any]:
    """Load one vector file with a plain JSON parser (CONTRACT.md section 8)."""
    loaded = json.loads(require(VECTORS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded
