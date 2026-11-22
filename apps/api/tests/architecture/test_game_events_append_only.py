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
   audited repository operations may issue UPDATE/DELETE/TRUNCATE SQL against ``game_events``,
   call update/delete/merge-like functions on ``GameEvent`` or on a row loaded from it, or assign
   an event column on such a row. Calls on other tables (``delete(GameSeat)``, ``update(Game)``,
   ``session.add_all(seats)``) are not the scan's business: the repository is the domain's only
   one, and the seat queries of the next stories live there.

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


def _event_class_names(tree: ast.AST) -> set[str]:
    """``GameEvent`` and any alias it is imported under in this module."""
    names = {"GameEvent"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            names.update(a.asname for a in node.names if a.name == "GameEvent" and a.asname)
    return names


def _mentions(node: ast.AST, names: set[str]) -> bool:
    """True when ``node`` uses one of ``names`` or names the table ``game_events`` in a string."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in names:
            return True
        if isinstance(child, ast.Attribute) and child.attr == "GameEvent":
            return True
        if (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and _NAME.search(child.value)
        ):
            return True
    return False


def _target_names(target: ast.AST) -> Iterator[str]:
    for child in ast.walk(target):
        if isinstance(child, ast.Name):
            yield child.id


def _event_names(top: ast.AST, class_names: set[str]) -> set[str]:
    """Names in ``top`` that hold event rows or statements on them (simple dataflow).

    A name is tainted when it is assigned (or bound by ``for``/``with``/a comprehension) from an
    expression that mentions ``GameEvent`` or ``game_events``, or another tainted name.
    """
    tainted = set(class_names)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(top):
            pairs: list[tuple[ast.AST, ast.AST | None]] = []
            if isinstance(node, ast.Assign):
                pairs = [(t, node.value) for t in node.targets]
            elif isinstance(node, ast.AnnAssign | ast.NamedExpr | ast.AugAssign):
                pairs = [(node.target, node.value)]
            elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
                pairs = [(node.target, node.iter)]
            elif isinstance(node, ast.With | ast.AsyncWith):
                pairs = [(i.optional_vars, i.context_expr) for i in node.items if i.optional_vars]
            for target, value in pairs:
                if value is not None and _mentions(value, tainted):
                    new = set(_target_names(target)) - tainted
                    if new:
                        tainted |= new
                        changed = True
    return tainted


def _is_mutating_call(call: ast.Call, tainted: set[str]) -> bool:
    """A mutating call that targets event rows: its receiver or an argument is an event name."""
    if _call_name(call) not in MUTATING_CALLS:
        return False
    parts: list[ast.AST] = [*call.args, *(k.value for k in call.keywords)]
    if isinstance(call.func, ast.Attribute):
        parts.append(call.func.value)
    return any(_mentions(part, tainted) for part in parts)


def _mutations(node: ast.AST, docstrings: set[int], tainted: set[str]) -> Iterator[tuple[int, str]]:
    """Constructs under ``node`` that could change or delete event rows."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _is_mutating_call(child, tainted):
            yield child.lineno, f"{_call_name(child)}() on game_events"
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
            and _mentions(child.value, tainted)
        ):
            yield child.lineno, f"assigns .{child.attr} of an event row"


def _creates_events(node: ast.AST, class_names: set[str]) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id in class_names
        for child in ast.walk(node)
    )


def scan(root: Path) -> list[str]:
    """Every violation of the append-only rule in the ``app`` tree under ``root``.

    Outside the three games modules nothing may name the event table at all. Inside them only
    calls and writes that target event rows count, so ``delete(GameSeat)`` or ``update(Game)`` in
    the repository are fine.
    """
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
        class_names = _event_class_names(tree)
        for top in tree.body:
            is_function = isinstance(top, ast.FunctionDef | ast.AsyncFunctionDef)
            in_repository = relative == Path("games/repository.py")
            if in_repository and is_function and top.name in AUDITED_OPERATIONS:
                continue
            tainted = _event_names(top, class_names)
            for line, what in _mutations(top, docstrings, tainted):
                found.append(f"{relative.as_posix()}:{line}: {what}")
            if (
                is_function
                and _creates_events(top, class_names)
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


@pytest.mark.parametrize(
    "body",
    [
        "async def prune_seats(s, game_id):\n    await s.execute(delete(GameSeat))\n",
        "async def touch(s):\n    await s.execute(update(Game).values(last_seq=0))\n",
        "async def seat_all(s, seats):\n    s.add_all(seats)\n",
        "async def drop(s, seat):\n    await s.delete(seat)\n    await s.merge(seat)\n",
        "def forget(s, game):\n    s.expunge(game)\n",
        "def fields(candidate):\n    candidate.update(seq=1)\n    candidate.type = 'x'\n",
    ],
)
def test_the_scan_does_not_flag_calls_on_other_tables_in_the_repository(
    tree: Path, body: str
) -> None:
    repository = tree / "games" / "repository.py"
    repository.write_text(repository.read_text(encoding="utf-8") + "\n\n" + body, "utf-8")
    assert scan(tree) == []


@pytest.mark.parametrize(
    "body",
    [
        "async def prune(s):\n    await s.execute(delete(GameEvent))\n",
        "async def prune(s):\n    await s.execute(GameEvent.__table__.delete())\n",
        "async def prune(s):\n    await s.execute(delete(table('game_events')))\n",
        (
            "async def prune(s):\n"
            "    rows = (await s.execute(select(GameEvent))).scalars().all()\n"
            "    for row in rows:\n"
            "        await s.delete(row)\n"
        ),
        (
            "async def prune(s):\n"
            "    row = (await s.execute(select(GameEvent))).scalar_one()\n"
            "    row.payload = {}\n"
        ),
        "async def prune(s):\n    await s.merge(GameEvent(seq=1))\n",
    ],
)
def test_the_scan_still_flags_event_offenders_next_to_other_table_calls(
    tree: Path, body: str
) -> None:
    repository = tree / "games" / "repository.py"
    other = "async def prune_seats(s):\n    await s.execute(delete(GameSeat))\n"
    repository.write_text(
        repository.read_text(encoding="utf-8") + "\n\n" + other + "\n\n" + body, "utf-8"
    )
    assert scan(tree)
