"""No router reaches an outbound client (S-3.1.2 Scryfall, S-4.2.1 deck sites).

User requests read the local database; imports run in jobs. Every module that is named
``router.py`` or is a router module (``is_router_module``: it imports ``fastapi.APIRouter``) must
not reach ``app.cards.scryfall_client`` / ``ScryfallClient`` or
``app.decks.services.deck_sites`` / ``DeckSiteClient``.

Detected, through ``imported_modules`` of ``test_domain_imports`` (relative imports resolved,
literal ``importlib.import_module`` and ``__import__`` calls included, ``TYPE_CHECKING`` and
in-function imports included): any import that resolves to ``app.cards.scryfall_client`` or below,
e.g. ``from . import scryfall_client`` inside ``cards/router.py``. On top of that, by name:
``from <anything> import scryfall_client`` or ``ScryfallClient`` (this also catches a re-export,
and a ``from app.cards import scryfall_client`` when the file does not exist on disk), and any
attribute access to ``.scryfall_client`` or ``.ScryfallClient``, so ``from app import cards``
followed by ``cards.scryfall_client.ScryfallClient()`` is flagged at the attribute.

Not detectable: ``getattr(cards, "scryfall_client")`` and other non-literal lookups, a dynamic
import whose name is not a string literal, and a router that gets a client from another module
that builds one (for example ``from app.cards.services import some_helper``). Those are left to
review, as architecture §4.2 does for the other import rules.
"""

import ast
from collections.abc import Mapping
from pathlib import Path

from tests.architecture.test_domain_imports import APP_ROOT, imported_modules, is_router_module

# Outbound clients no router may reach (S-3.1.2 Scryfall, S-4.2.1 deck sites): the module that
# holds each one, and the names that identify it (a re-export or an attribute access of them).
CLIENT_MODULES = ("app.cards.scryfall_client", "app.decks.services.deck_sites")
CLIENT_NAMES = frozenset({"ScryfallClient", "scryfall_client", "DeckSiteClient", "deck_sites"})


def _named_uses(tree: ast.AST) -> list[tuple[int, str]]:
    """Name-based hits: imported names and attribute accesses of the client."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found += [
                (node.lineno, f"{node.module or '.'}.{a.name}")
                for a in node.names
                if a.name in CLIENT_NAMES
            ]
        elif isinstance(node, ast.Attribute) and node.attr in CLIENT_NAMES:
            found.append((node.lineno, f"attribute .{node.attr}"))
    return found


def router_violations(root: Path) -> list[str]:
    """Router modules under ``root`` that reach the Scryfall client, as ``file:line: what``."""
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        if path.name != "router.py" and not is_router_module(tree):
            continue
        hits = [
            (line, module)
            for line, module in imported_modules(root, path)
            if any(module == m or module.startswith(f"{m}.") for m in CLIENT_MODULES)
        ]
        hits += _named_uses(tree)
        shown = path.relative_to(root).as_posix()
        problems += [f"{shown}:{line}: {what}" for line, what in sorted(set(hits))]
    return problems


def _write(root: Path, files: Mapping[str, str]) -> Path:
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def test_no_router_in_the_app_imports_the_scryfall_client() -> None:
    assert router_violations(APP_ROOT) == []


def _flagged(root: Path) -> set[str]:
    return {line.split(":")[0] for line in router_violations(root)}


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
            "cards/scryfall_client.py": "",
            "a/router.py": "import app.cards.scryfall_client\n",
            "b/router.py": "from app.cards import scryfall_client\n",
            "c/router.py": "from app.cards.services import ScryfallClient\n",
            "d/router.py": "def f() -> None:\n    from app.cards.scryfall_client import x\n",
        },
    )
    assert _flagged(root) == {"a/router.py", "b/router.py", "c/router.py", "d/router.py"}


def test_relative_import_of_the_client_in_cards_router_is_reported(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "cards/scryfall_client.py": "",
            "cards/router.py": "from . import scryfall_client\n",
            "cards/other/router.py": "from ..scryfall_client import ScryfallClient\n",
        },
    )
    assert _flagged(root) == {"cards/router.py", "cards/other/router.py"}


def test_literal_importlib_and_dunder_import_of_the_client_are_reported(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "a/router.py": (
                "import importlib\nmod = importlib.import_module('app.cards.scryfall_client')\n"
            ),
            "b/router.py": "mod = __import__('app.cards.scryfall_client')\n",
        },
    )
    assert _flagged(root) == {"a/router.py", "b/router.py"}


def test_from_app_import_cards_with_attribute_access_is_reported_at_the_attribute(
    tmp_path: Path,
) -> None:
    root = _write(
        tmp_path,
        {
            "decks/router.py": (
                "from app import cards\nclient = cards.scryfall_client.ScryfallClient()\n"
            )
        },
    )
    assert router_violations(root) == [
        "decks/router.py:2: attribute .ScryfallClient",
        "decks/router.py:2: attribute .scryfall_client",
    ]


def test_getattr_lookup_of_the_client_is_a_known_undetectable_form(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "decks/router.py": (
                "from app import cards\nclient = getattr(cards, 'scryfall_client')\n"
            )
        },
    )
    assert router_violations(root) == []


def test_router_importing_the_deck_site_client_in_any_form_is_reported(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        {
            "decks/services/deck_sites/http.py": "",
            "a/router.py": "from app.decks.services.deck_sites.http import DeckSiteClient\n",
            "b/router.py": "from app.decks.services import DeckSiteClient\n",
            "c/router.py": "from app.decks.services import deck_sites\n",
            "d/router.py": "import app.decks.services.deck_sites\n",
            "e/router.py": "from app.decks.services import parse_decklist\n",
        },
    )
    assert _flagged(root) == {"a/router.py", "b/router.py", "c/router.py", "d/router.py"}


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
