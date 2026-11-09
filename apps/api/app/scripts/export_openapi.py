"""Export the OpenAPI spec without a server, a database or a ``.env`` (S-1.5.1).

Run from ``apps/api``: ``uv run python -m app.scripts.export_openapi [--output PATH] [--check]``.

Exit codes: 0 success (or ``--check`` and the file matches); 1 ``--check`` and the file is
missing or differs; 2 bad usage (argparse); 3 duplicate operation IDs.
"""

import argparse
import json
import sys
from pathlib import Path
from unittest import mock

from app.core.config import Settings
from app.core.openapi import assert_unique_operation_ids

DEFAULT_OUTPUT = Path(__file__).resolve().parents[4] / "packages" / "api-client" / "openapi.json"
EXIT_DRIFT = 1
EXIT_DUPLICATE = 3


def render_spec() -> str:
    """Build the app with dummy settings and return the stable JSON text of its spec."""
    dummy = {
        "APP_ENV": "test",
        "DATABASE_URL": "postgresql+psycopg://mtg:CHANGE_ME@localhost:5432/mtg_stats",
    }
    # ``app.main`` builds a module-level ``app`` on import, which reads the environment. Give that
    # import the dummy values (restored afterwards) so no ``.env`` is needed.
    with mock.patch.dict("os.environ", dummy):
        from app.main import create_app  # noqa: PLC0415

    app = create_app(Settings.model_validate(dummy))
    assert_unique_operation_ids(app)
    return json.dumps(app.openapi(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description="Export the OpenAPI spec.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="exit 1 if the file differs")
    args = parser.parse_args(argv)
    try:
        text = render_spec()
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return EXIT_DUPLICATE
    output: Path = args.output
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
