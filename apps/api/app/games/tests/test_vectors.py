"""Run every shared vector in ``packages/game-core/vectors/`` (CONTRACT.md section 8.5).

One pytest case per file, id ``test_vector[<file>.json]``, so a failure names the vector file. A
missing or empty vectors directory fails loudly; event-type coverage (8.4) is one case per type.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from app.games.core import EVENT_TYPES
from app.games.tests.repo_files import VECTORS_DIR
from app.games.tests.vector_runner import (
    VectorError,
    list_vector_files,
    run_vector_file,
    valid_reducer_vectors,
)
from app.games.tests.vector_validator import uncovered_event_type_messages

try:
    FILES = list_vector_files(VECTORS_DIR)
    DIRECTORY_PROBLEM = ""
except (
    Exception
) as _problem:  # VectorError, or an unexpected OS error: it fails a test, not collection
    FILES = []
    DIRECTORY_PROBLEM = f"{type(_problem).__name__}: {_problem}"


@functools.cache
def _uncovered() -> tuple[frozenset[str], str]:
    """The uncovered-type messages, or the reason they could not be computed (never raises)."""
    try:
        messages = uncovered_event_type_messages(valid_reducer_vectors(FILES), EVENT_TYPES)
    except Exception as error:
        return frozenset(), f"coverage could not be computed: {type(error).__name__}: {error}"
    return frozenset(messages), ""


def test_vectors_directory_is_available() -> None:
    if DIRECTORY_PROBLEM:
        pytest.fail(DIRECTORY_PROBLEM, pytrace=False)


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_vector(path: Path) -> None:
    try:
        run_vector_file(path)
    except VectorError as failure:
        pytest.fail(str(failure), pytrace=False)


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_event_type_is_exercised_by_a_vector(event_type: str) -> None:
    uncovered, problem = _uncovered()
    if problem:
        pytest.fail(problem, pytrace=False)
    message = f"uncovered event type: {event_type}"
    if message in uncovered:
        pytest.fail(message, pytrace=False)
