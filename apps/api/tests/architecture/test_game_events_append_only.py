"""``game_events`` is append-only in code (S-6.3.1 AC5, architecture 6.1).

The database trigger is the guard of last resort; these static rules keep the code honest:

1. The games repository's public functions are exactly the allowed set: the event operations
   ``append``, ``read_since``, ``get_by_event_id`` and the two audited exceptions
   ``reassign_player`` and ``delete_playgroup_data``, plus the functions of the ``games`` table.
2. Outside ``games/models.py``, ``games/repository.py`` and ``games/services/events.py`` no module
   under ``app/`` (tests excluded) may name ``GameEvent`` or the table ``game_events`` at all:
   not as an import, an alias, an attribute, or in a string literal. Every way of mutating a row
   (``update(Ev)``, ``__table__.delete()``, ``table("game_events")``, ``session.delete(row)``,
   ``merge``) needs such a name first, so none can be spelled.
3. Inside those three modules, only ``append`` may create a ``GameEvent``, and nothing but the two
   audited repository operations may call update/delete/merge-like functions, issue UPDATE,
   DELETE or TRUNCATE SQL against ``game_events``, or assign an event column on a row.

The scan parses with ``ast`` and looks at code; docstrings are ignored.
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

GAMES_MODULES = {
    Path("games/models.py"),
    Path("games/repository.py"),
    Path("games/services/events.py"),
}
MUTATING_CALLS = {
    "update",
    "delete",
    "merge",
    "add_all",
    "bulk_update_mappings",
    "bulk_insert_mappings",
    "bulk_save_objects",
    "truncate",
}
EVENT_COLUMNS = {"seq", "event_id", "type", "payload", "actor", "on_behalf_of_seat", "ts", "v"}
_NAME = re.compile(r"game_events|GameEvent", re.IGNORECASE)
_MUTATING_SQL = re.compile(
    r"\b(?:update|delete\s+from|truncate)\b[^;]{0,60}?game_events", re.I | re.S
)


def _public_functions(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _names_event_table(tree: ast.AST) -> bool:
    docstrings = _docstrings(tree)
    for child in ast.walk(tree):
        if isinstance(child, ast.Name) and child.id == "GameEvent":
            return True
        if isinstance(child, ast.Attribute) and child.attr == "GameEvent":
            return True
        if isinstance(child, ast.alias) and _NAME.search(child.name):
            return True
        if (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and id(child) not in docstrings
            and _NAME.search(child.value)
        ):
            return True
    return False


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def _is_mutating_call(call: ast.Call) -> bool:
    name = _call_name(call)
    if name not in MUTATING_CALLS:
        return False
    if name == "update" and isinstance(call.func, ast.Attribute):
        receiver = ast.dump(call.func.value)  # ``candidate.update(...)`` is a plain dict
        return any(word in receiver for word in ("GameEvent", "__table__", "game_events"))
    return True


def _mutations(node: ast.AST, docstrings: set[int]) -> Iterator[tuple[int, str]]:
    """Constructs under ``node`` that could change or delete event rows."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _is_mutating_call(child):
            yield child.lineno, f"{_call_name(child)}()"
        elif (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and id(child) not in docstrings
            and _MUTATING_SQL.search(child.value)
        ):
            yield child.lineno, "SQL that mutates game_events"
        elif (
            isinstance(child, ast.Attribute)
            and isinstance(child.ctx, ast.Store)
            and child.attr in EVENT_COLUMNS
        ):
            yield child.lineno, f"assigns .{child.attr}"


def _creates_events(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == "GameEvent"
        for child in ast.walk(node)
    )


def scan(root: Path) -> list[str]:
    """Every violation of the append-only rule in the ``app`` tree under ``root``."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if "tests" in relative.parts or "__pycache__" in relative.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if relative not in GAMES_MODULES:
            if _names_event_table(tree):
                found.append(f"{relative.as_posix()}: names GameEvent or game_events")
            continue
        docstrings = _docstrings(tree)
        for top in tree.body:
            is_function = isinstance(top, ast.FunctionDef | ast.AsyncFunctionDef)
            in_repository = relative == Path("games/repository.py")
            if in_repository and is_function and top.name in AUDITED_OPERATIONS:
                continue
            for line, what in _mutations(top, docstrings):
                found.append(f"{relative.as_posix()}:{line}: {what}")
            if (
                is_function
                and _creates_events(top)
                and not (in_repository and top.name == "append")
            ):
                found.append(f"{relative.as_posix()}:{top.lineno}: {top.name} creates a GameEvent")
    return found


def test_the_repository_public_functions_are_exactly_the_allowed_set() -> None:
    tree = ast.parse(REPOSITORY.read_text(encoding="utf-8"))
    assert _public_functions(tree) == ALLOWED_PUBLIC


def test_no_code_in_app_mutates_game_events() -> None:
    assert scan(APP_ROOT) == []


OFFENDERS = {
    "raw_update": 'async def f(s):\n    await s.execute(text("UPDATE game_events SET v = 1"))\n',
    "raw_delete_lowercase": (
        'async def f(s):\n    await s.execute(text("delete from game_events"))\n'
    ),
    "raw_truncate": 'async def f(s):\n    await s.execute(text("TRUNCATE TABLE game_events"))\n',
    "raw_schema_qualified": (
        'async def f(s):\n    await s.execute(text("DELETE FROM public.game_events"))\n'
    ),
    "orm_bulk_update": (
        "from app.games.models import GameEvent\n"
        "async def f(s):\n    await s.execute(update(GameEvent).values(v=2))\n"
    ),
    "orm_bulk_delete_alias": (
        "from app.games.models import GameEvent as Ev\n"
        "async def f(s):\n    await s.execute(delete(Ev))\n"
    ),
    "orm_table_delete": (
        "from app.games.models import GameEvent\n"
        "async def f(s):\n    await s.execute(GameEvent.__table__.delete())\n"
    ),
    "core_table_delete": 'async def f(s):\n    await s.execute(delete(table("game_events")))\n',
    "session_delete_loaded_row": (
        "from app.games.models import GameEvent\n"
        "async def f(s):\n"
        "    e = (await s.execute(select(GameEvent))).scalars().first()\n"
        "    await s.delete(e)\n"
    ),
    "merge": (
        "from app.games.models import GameEvent\n"
        "async def f(s):\n    await s.merge(GameEvent(seq=1))\n"
    ),
    "attribute_write_on_loaded_row": (
        "from app.games.models import GameEvent\n"
        "async def f(s):\n"
        "    e = (await s.execute(select(GameEvent))).scalars().first()\n"
        "    e.payload = {}\n"
    ),
}


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A copy of the three games modules plus an empty ``stats`` package."""
    root = tmp_path / "app"
    for relative in GAMES_MODULES:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text((APP_ROOT / relative).read_text(encoding="utf-8"), "utf-8")
    (root / "stats").mkdir()
    return root


def test_the_scan_is_clean_on_a_copy_of_the_games_modules(tree: Path) -> None:
    assert scan(tree) == []


@pytest.mark.parametrize("name", sorted(OFFENDERS))
def test_the_scan_flags_a_module_outside_games_that_mutates_events(tree: Path, name: str) -> None:
    (tree / "stats" / "rogue.py").write_text(OFFENDERS[name], encoding="utf-8")
    assert scan(tree), name


@pytest.mark.parametrize("name", ["raw_update", "orm_bulk_delete_alias", "merge"])
def test_the_scan_flags_the_same_code_in_a_non_audited_repository_function(
    tree: Path, name: str
) -> None:
    repository = tree / "games" / "repository.py"
    body = OFFENDERS[name].replace("async def f(s)", "async def prune(s)")
    repository.write_text(repository.read_text(encoding="utf-8") + "\n\n" + body, encoding="utf-8")
    assert scan(tree), name


def test_the_scan_allows_the_audited_operations_to_mutate(tree: Path) -> None:
    repository = tree / "games" / "repository.py"
    body = (
        "async def delete_playgroup_data(s):\n"
        '    await s.execute(text("DELETE FROM game_events"))\n'
    )
    text = repository.read_text(encoding="utf-8")
    start = text.index("async def delete_playgroup_data")
    repository.write_text(text[:start] + body, encoding="utf-8")
    assert scan(tree) == []


def test_the_scan_flags_a_second_function_that_creates_events(tree: Path) -> None:
    repository = tree / "games" / "repository.py"
    repository.write_text(
        repository.read_text(encoding="utf-8")
        + "\n\ndef sneaky(s):\n    s.add(GameEvent(seq=1))\n",
        encoding="utf-8",
    )
    assert scan(tree)


def test_a_docstring_that_mentions_the_table_is_not_a_violation(tree: Path) -> None:
    (tree / "stats" / "doc.py").write_text('"""Reads game_events? No."""\n', encoding="utf-8")
    assert scan(tree) == []
