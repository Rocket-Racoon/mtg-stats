"""No router reaches Scryfall (S-3.1.2): user requests read the local card database.

Every module that is named ``router.py`` or imports ``fastapi.APIRouter`` (a router module, as
``test_domain_imports`` defines it) must not import ``ScryfallClient`` or the
``app.cards.scryfall_client`` module. Imports are read with ``ast`` (nothing is executed), so an
import under ``TYPE_CHECKING`` or inside a function counts too.
"""

import ast
from collections.abc import Mapping
from pathlib import Path

from tests.architecture.test_domain_imports import APP_ROOT, is_router_module

CLIENT_MODULE = "app.cards.scryfall_client"
CLIENT_NAME = "ScryfallClient"


def scryfall_imports(tree: ast.AST) -> list[tuple[int, str]]:
    """``(line, what)`` of every import of the Scryfall client module or class in ``tree``."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names if a.name.startswith(CLIENT_MODULE)]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(CLIENT_MODULE):
                found.append((node.lineno, module))
            found += [
                (node.lineno, f"{module}.{a.name}") for a in node.names if a.name == CLIENT_NAME
            ]
            if module == "app.cards":
                found += [
                    (node.lineno, f"{module}.{a.name}")
                    for a in node.names
                    if a.name == "scryfall_client"
                ]
    return found


def router_violations(root: Path) -> list[str]:
    """Router modules under ``root`` that import the Scryfall client, as ``file:line: import``."""
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if path.name != "router.py" and not is_router_module(tree):
            continue
        shown = path.relative_to(root).as_posix()
        problems += [f"{shown}:{line}: {what}" for line, what in scryfall_imports(tree)]
    return problems


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def test_no_router_in_the_app_imports_the_scryfall_client() -> None:
    assert router_violations(APP_ROOT) == []


def test_router_importing_the_client_class_is_reported(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {"decks/router.py": "from app.cards.scryfall_client import ScryfallClient\n"},
    )
    assert router_violations(root) == [
        "decks/router.py:1: app.cards.scryfall_client",
        "decks/router.py:1: app.cards.scryfall_client.ScryfallClient",
    ]


def test_router_importing_the_client_module_in_any_form_is_reported(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "a/router.py": "import app.cards.scryfall_client\n",
            "b/router.py": "from app.cards import scryfall_client\n",
            "c/router.py": "from app.cards.services import ScryfallClient\n",
            "d/router.py": "def f() -> None:\n    from app.cards.scryfall_client import x\n",
        },
    )
    flagged = {line.split("/")[0] for line in router_violations(root)}
    assert flagged == {"a", "b", "c", "d"}


def test_non_router_module_may_import_the_client(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {"cards/sync.py": "from app.cards.scryfall_client import ScryfallClient\n"},
    )
    assert router_violations(root) == []


def test_module_that_defines_an_api_router_is_checked_even_if_not_named_router(
    tmp_path: Path,
) -> None:
    root = _write(
        tmp_path,
        {
            "cards/endpoints.py": (
                "from fastapi import APIRouter\n"
                "from app.cards.scryfall_client import ScryfallClient\n"
            )
        },
    )
    assert len(router_violations(root)) == 2
