"""No router reaches the Commander Spellbook client (S-5.2.1): the check of the Scryfall rule.

Same detection as ``test_routers_never_call_scryfall`` (it reuses ``imported_modules`` and
``is_router_module`` of ``test_domain_imports``): a router module (named ``router.py`` or
importing ``fastapi.APIRouter``) must not import ``app.brackets.spellbook_client``, import the
names ``spellbook_client`` or ``SpellbookClient``, or access them as attributes. Calling the
service ``find_deck_combos`` is not the client, so it is not flagged here; whether a request path
may wait on Spellbook is a review question. Non-literal lookups (``getattr``) are not detectable.
"""

import ast
from pathlib import Path

from tests.architecture.test_domain_imports import APP_ROOT, imported_modules, is_router_module

CLIENT_MODULE = "app.brackets.spellbook_client"
CLIENT_NAMES = {"spellbook_client", "SpellbookClient"}


def spellbook_router_violations(root: Path) -> list[str]:
    """Router modules under ``root`` that reach the Spellbook client, as ``file:line: what``."""
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        if path.name != "router.py" and not is_router_module(tree):
            continue
        hits = {
            (line, module)
            for line, module in imported_modules(root, path)
            if module == CLIENT_MODULE or module.startswith(f"{CLIENT_MODULE}.")
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                hits |= {
                    (node.lineno, f"{node.module or '.'}.{a.name}")
                    for a in node.names
                    if a.name in CLIENT_NAMES
                }
            elif isinstance(node, ast.Attribute) and node.attr in CLIENT_NAMES:
                hits.add((node.lineno, f"attribute .{node.attr}"))
        shown = path.relative_to(root).as_posix()
        problems += [f"{shown}:{line}: {what}" for line, what in sorted(hits)]
    return problems


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def test_no_router_in_the_app_imports_the_spellbook_client() -> None:
    assert spellbook_router_violations(APP_ROOT) == []


def test_router_reaching_the_client_in_any_form_is_reported(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "brackets/spellbook_client.py": "",
            "a/router.py": "import app.brackets.spellbook_client\n",
            "b/router.py": "from app.brackets import spellbook_client\n",
            "c/router.py": "from app.brackets.services import SpellbookClient\n",
            "d/router.py": "from app import brackets\nx = brackets.spellbook_client\n",
            "e/endpoints.py": (
                "from fastapi import APIRouter\nfrom app.brackets.spellbook_client import x\n"
            ),
        },
    )
    flagged = {line.split(":")[0] for line in spellbook_router_violations(root)}
    assert flagged == {"a/router.py", "b/router.py", "c/router.py", "d/router.py", "e/endpoints.py"}


def test_non_router_module_may_use_the_client_and_a_router_may_call_the_service(
    tmp_path: Path,
) -> None:
    root = _tree(
        tmp_path,
        {
            "brackets/sync.py": "from app.brackets.spellbook_client import SpellbookClient\n",
            "decks/router.py": "from app.brackets.services import find_deck_combos\n",
        },
    )
    assert spellbook_router_violations(root) == []
