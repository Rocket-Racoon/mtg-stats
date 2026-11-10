"""Export the OpenAPI spec without a server, a database or a ``.env`` (S-1.5.1).

Run from ``apps/api``: ``uv run python -m app.scripts.export_openapi [--output PATH] [--check]``.

Exit codes: 0 success (or ``--check`` and the file matches); 1 ``--check`` and the file is
missing or differs; 2 bad usage (argparse); 3 duplicate operation IDs. Any other failure (for
example invalid settings) propagates as a traceback with Python's non-zero exit status.

``--output`` is resolved against the current directory. ``pnpm openapi:export`` runs the script
with ``--directory apps/api``, so a relative ``--output`` is relative to ``apps/api`` there.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from unittest import mock

from app.core.config import WORKSPACE_MARKER, Settings, get_settings
from app.core.openapi import DuplicateOperationIdError, assert_unique_operation_ids

EXIT_DRIFT = 1
EXIT_DUPLICATE = 3


def default_output() -> Path:
    """Return ``packages/api-client/openapi.json`` under the monorepo root (found lazily)."""
    for directory in Path(__file__).resolve().parents:
        if (directory / WORKSPACE_MARKER).is_file():
            return directory / "packages" / "api-client" / "openapi.json"
    msg = f"{WORKSPACE_MARKER} not found above {__file__}; pass --output"
    raise FileNotFoundError(msg)


def render_spec() -> str:
    """Build the app with dummy settings and return the stable JSON text of its spec."""
    dummy = {
        "APP_ENV": "test",
        "DATABASE_URL": "postgresql+psycopg://mtg:CHANGE_ME@localhost:5432/mtg_stats",
        "REDIS_URL": "redis://localhost:6379/0",
    }
    # ``app.main`` builds a module-level ``app`` on import, which reads the environment. Run the
    # import and the build with only the dummy values (ambient variables such as
    # CORS_ALLOWED_ORIGINS must not leak in), restoring the environment afterwards.
    with mock.patch.dict(os.environ, dummy, clear=True):
        try:
            from app.main import create_app  # noqa: PLC0415

            app = create_app(Settings.model_validate(dummy))
        finally:
            get_settings.cache_clear()
    assert_unique_operation_ids(app)
    return json.dumps(app.openapi(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description="Export the OpenAPI spec.")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--check", action="store_true", help="exit 1 if the file differs")
    args = parser.parse_args(argv)
    try:
        text = render_spec()
    except DuplicateOperationIdError as exc:
        sys.stderr.write(f"{exc}\n")
        return EXIT_DUPLICATE
    output: Path = args.output or default_output()
    if args.check:
        current = output.read_bytes().decode("utf-8") if output.exists() else None
        if current != text:
            sys.stderr.write(f"{output} is out of date; run `pnpm openapi:export`.\n")
            return EXIT_DRIFT
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(text.encode("utf-8"))  # bytes: no CRLF translation on Windows
    return 0


if __name__ == "__main__":
    sys.exit(main())
