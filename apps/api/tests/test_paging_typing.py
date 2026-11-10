"""Type-level regression guard for the public paging helpers (S-1.4.7).

The helpers once failed ``mypy --strict`` for a real domain caller (SQLAlchemy's generic
``Select[Deck]`` and ``Mapped`` columns), which only a typed usage shows. This runs mypy on
``tests/typing_samples/paging_usage.py`` with the project configuration.
"""

from pathlib import Path

from mypy import api

API_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = API_ROOT / "tests" / "typing_samples" / "paging_usage.py"


def test_paging_helpers_type_check_for_a_domain_caller_under_mypy_strict() -> None:
    stdout, stderr, status = api.run(
        [
            "--strict",
            "--config-file",
            str(API_ROOT / "pyproject.toml"),
            "--cache-dir",
            str(API_ROOT / ".mypy_cache"),
            "--show-error-codes",
            str(SAMPLE),
        ]
    )
    assert status == 0, stdout + stderr
