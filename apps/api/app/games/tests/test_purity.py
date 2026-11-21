"""Purity guard for the game core (CONTRACT.md section 7), the Python twin of ``purity.test.ts``.

The reducer and the serializer may import only an allowlist of side-effect-free modules and may
not call clock, I/O or dynamic-import builtins.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE_DIR = Path(__file__).resolve().parents[1] / "core"
SOURCES = sorted(CORE_DIR.glob("*.py"))

ALLOWED_MODULES = frozenset(
    {"__future__", "collections", "dataclasses", "json", "pydantic", "re", "typing"}
)
ALLOWED_APP_PREFIX = "app.games.core"
FORBIDDEN_CALLS = frozenset({"open", "input", "print", "exec", "eval", "compile", "__import__"})
FORBIDDEN_ATTRIBUTES = frozenset(
    {"now", "utcnow", "today", "time", "monotonic", "random", "uuid4", "getenv", "environ"}
)


def _violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [("." * node.level) + (node.module or "")]
        for module in modules:
            top = module.split(".")[0]
            if module.startswith(ALLOWED_APP_PREFIX) or top in ALLOWED_MODULES:
                continue
            found.append(f"import {module}")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FORBIDDEN_CALLS
        ):
            found.append(f"call {node.func.id}()")
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            found.append(f"attribute .{node.attr}")
    return found


def test_guard_finds_the_core_modules() -> None:
    names = {path.name for path in SOURCES}
    assert {"reducer.py", "canonical.py", "contract.py", "state.py"} <= names


@pytest.mark.parametrize("path", SOURCES, ids=[p.name for p in SOURCES])
def test_core_module_uses_no_clock_randomness_or_io(path: Path) -> None:
    assert _violations(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "snippet",
    [
        "import random",
        "from datetime import datetime",
        "import uuid",
        "import os",
        "from pathlib import Path",
        "import time",
        "import asyncio",
        "from sqlalchemy import select",
        "x = datetime.now()",
        "x = open('f')",
        "x = os.environ",
        "x = uuid.uuid4()",
    ],
)
def test_guard_flags_a_forbidden_construct(snippet: str) -> None:
    assert _violations(snippet) != []
