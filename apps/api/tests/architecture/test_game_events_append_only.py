"""``game_events`` is append-only in code (S-6.3.1 AC5, architecture 6.1).

The database trigger is the guard of last resort; these static rules keep the code honest:

1. The games repository's public functions are exactly the allowed set: the event operations
   ``append``, ``read_since``, ``get_by_event_id`` and the two audited exceptions
   ``reassign_player`` and ``delete_playgroup_data``, plus the functions of the ``games`` table.
2. Inside the repository, only ``append`` creates ``GameEvent`` rows, and only the two audited
   operations may name an UPDATE, DELETE or TRUNCATE of ``game_events`` or ``GameEvent``.
3. No other module under ``app/`` (tests excluded) issues UPDATE, DELETE or TRUNCATE against
   ``game_events``, uses ``update(GameEvent)``/``delete(GameEvent)``, or calls ``.delete(...)`` /
   ``.merge(...)`` / ``.add_all`` on a ``GameEvent``. Mutating an attribute of a loaded row is
   caught by the trigger test, not here.

The scan parses with ``ast`` and looks at code, never at comments or docstrings.
"""

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
REPOSITORY = APP_ROOT / "games" / "repository.py"

EVENT_OPERATIONS = {"append", "read_since", "get_by_event_id"}
AUDITED_OPERATIONS = {"reassign_player", "delete_playgroup_data"}
GAME_TABLE_FUNCTIONS = {"add_game", "get_game", "lock_game", "save_snapshot"}
ALLOWED_PUBLIC = EVENT_OPERATIONS | AUDITED_OPERATIONS | GAME_TABLE_FUNCTIONS

_MUTATING_SQL = re.compile(
    r"\b(?:update\s+(?:only\s+)?\"?game_events|delete\s+from\s+(?:only\s+)?\"?game_events"
    r"|truncate\s+(?:table\s+)?(?:only\s+)?\"?game_events)",
    re.IGNORECASE,
)


def _public_functions(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }


def _mutations(node: ast.AST) -> Iterator[tuple[int, str]]:
    """Every code construct under ``node`` that changes or removes ``game_events`` rows."""
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if _MUTATING_SQL.search(child.value):
                yield child.lineno, "SQL text that mutates game_events"
        elif isinstance(child, ast.Call):
            func = child.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            argument_names = {arg.id for arg in child.args if isinstance(arg, ast.Name)} | {
                arg.attr for arg in child.args if isinstance(arg, ast.Attribute)
            }
            if name in {"update", "delete"} and "GameEvent" in argument_names:
                yield child.lineno, f"{name}(GameEvent)"
            if name in {"delete", "merge", "add_all", "bulk_update_mappings"} and isinstance(
                func, ast.Attribute
            ):
                targets = {a.id for a in child.args if isinstance(a, ast.Name)}
                if "GameEvent" in targets:
                    yield child.lineno, f".{name}(GameEvent)"


def _creates_events(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == "GameEvent"
        for child in ast.walk(node)
    )


def _violations_in_repository(source: str) -> list[str]:
    tree = ast.parse(source)
    found: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for line, what in _mutations(node):
            if node.name not in AUDITED_OPERATIONS:
                found.append(f"repository.py:{line} {node.name}: {what}")
        if _creates_events(node) and node.name != "append":
            found.append(f"repository.py:{node.lineno} {node.name}: creates a GameEvent")
    return found


def _violations_elsewhere(path: Path, source: str) -> list[str]:
    return [f"{path.name}:{line}: {what}" for line, what in _mutations(ast.parse(source))]


def _app_modules() -> Iterator[Path]:
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "tests" in path.relative_to(APP_ROOT).parts:
            continue
        yield path


def test_the_repository_public_functions_are_exactly_the_allowed_set() -> None:
    tree = ast.parse(REPOSITORY.read_text(encoding="utf-8"))
    assert _public_functions(tree) == ALLOWED_PUBLIC


def test_inside_the_repository_only_the_audited_operations_may_mutate_events() -> None:
    assert _violations_in_repository(REPOSITORY.read_text(encoding="utf-8")) == []


def test_no_other_module_mutates_game_events() -> None:
    offenders: list[str] = []
    for path in _app_modules():
        if path == REPOSITORY:
            continue
        offenders.extend(
            f"{path.relative_to(APP_ROOT)}: {v}"
            for v in _violations_elsewhere(path, path.read_text(encoding="utf-8"))
        )
    assert offenders == []


@pytest.mark.parametrize(
    "source",
    [
        'async def f(s):\n    await s.execute(text("UPDATE game_events SET type = 1"))',
        'async def f(s):\n    await s.execute(text("delete from game_events"))',
        'async def f(s):\n    await s.execute(text("TRUNCATE TABLE game_events"))',
        "async def f(s):\n    await s.execute(update(GameEvent).values(type='x'))",
        "async def f(s):\n    await s.execute(delete(GameEvent))",
        "async def f(s, e):\n    await s.delete(GameEvent)",
    ],
)
def test_the_scan_flags_mutating_code(source: str) -> None:
    assert _violations_elsewhere(Path("synthetic.py"), source)
    assert _violations_in_repository(source)


def test_the_scan_allows_the_audited_operations_and_reads() -> None:
    source = (
        'async def reassign_player(s):\n    await s.execute(text("UPDATE game_events SET v = 1"))\n'
        "async def read_since(s):\n    return await s.execute(select(GameEvent))\n"
    )
    assert _violations_in_repository(source) == []


def test_the_scan_flags_a_second_function_that_creates_events() -> None:
    source = "def sneaky(s):\n    s.add(GameEvent(seq=1))\n"
    assert _violations_in_repository(source)
