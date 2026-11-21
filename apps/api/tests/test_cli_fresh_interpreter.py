"""A lower domain's model must not need a higher domain's table (S-2.4.1 review, B1).

``python -m app.cli`` and the worker load only the modules they import, not the model registry. A
``ForeignKey`` from an ``identity`` table to a ``playgroups`` table made ``create-admin`` fail with
``NoReferencedTableError`` in production while every test passed, because ``conftest.py`` imports
every model. These tests use a fresh interpreter that imports only what the entry point imports.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.migration_support import alembic_config, temporary_database

API_ROOT = Path(__file__).resolve().parents[1]

CREATE_ADMIN = textwrap.dedent(
    """
    import asyncio
    import sys

    import app.cli  # the only app import: no model registry, no other domain

    async def main() -> int:
        answers = iter(["a long enough passphrase", "a long enough passphrase"])
        return await app.cli.run(
            ["create-admin", "--username", "ada", "--display-name", "Ada"],
            prompt=lambda _text: next(answers),
        )

    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    sys.exit(asyncio.run(main(), loop_factory=loop_factory))
    """
)

RESOLVE_FOREIGN_KEYS = textwrap.dedent(
    """
    import importlib
    import sys

    importlib.import_module(sys.argv[1])
    from app.core.db import Base

    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            fk.column  # raises NoReferencedTableError when the target table is not loaded
    """
)


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and scripts, controlled environment
        [sys.executable, "-c", script, *args],
        cwd=API_ROOT,
        env={**os.environ, "PYTHONPATH": str(API_ROOT)},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_create_admin_works_in_a_fresh_interpreter_that_imports_only_the_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic import command  # noqa: PLC0415

    with temporary_database(monkeypatch):
        command.upgrade(alembic_config(), "head")
        result = _run(CREATE_ADMIN)
    assert result.returncode == 0, result.stderr
    assert "NoReferencedTableError" not in result.stderr


@pytest.mark.parametrize("entry_point", ["app.cli", "app.workers.registry"])
def test_every_foreign_key_resolves_with_only_the_entry_point_loaded(entry_point: str) -> None:
    result = _run(RESOLVE_FOREIGN_KEYS, entry_point)
    assert result.returncode == 0, result.stderr
