"""Import-boundary and domain-layout checks (S-1.4.6, architecture §4.2).

The checker parses every module under ``app/`` with the standard library ``ast`` module (no
import is executed) and reports *every* violation with file, line and the import. Each rule has a
test on a synthetic module tree proving it fails, plus one run over the real ``app/`` tree.

Rules (architecture §4.2 "Enforcement"), with ``A`` the domain a module belongs to:

* A domain may import another domain ``B`` only as ``app.B.services`` (the public interface), and
  only when ``B`` is in the transitive closure of the §4.2 graph from ``A``.
* ``workflows/`` and ``workers/`` may import a domain only as ``app.<B>.services``.
* A domain never imports ``app.workflows`` or ``app.workers`` (jobs are enqueued by name).
  ``workflows/`` never imports ``app.workers``; ``app.workers`` may import ``app.workflows``.
* ``app.core`` never imports a domain, ``app.workflows`` or ``app.workers``.
* Domains, ``core``, ``workflows`` and ``workers`` allowlist their ``app.*`` targets: a domain
  may import its own package, ``app.core`` and allowed domain ``services``; ``core`` only itself;
  ``workflows`` ``core`` and domain ``services``; ``workers`` also ``workflows``. So ``app.main``,
  ``app.scripts`` and unknown ``app.x`` fail from all four.
* A package or module directly under ``app/`` must be one of the ten domains, ``core``,
  ``workflows``, ``workers``, ``scripts``, ``main.py``, ``model_registry.py`` or ``__init__.py``
  (``__pycache__`` and hidden entries starting with ``.`` are ignored).
* Nothing under ``app/`` imports ``app.model_registry``, ``app/main.py`` included (only
  ``migrations/env.py`` and ``apps/api/tests/`` may). ``main.py`` and ``scripts/`` are exempt
  from every other rule. Domain tests (``app/<domain>/tests/``) obey their domain's rules.
* A *router module* is a module that imports ``fastapi.APIRouter``: ``from fastapi import
  APIRouter`` (also ``as``), ``from fastapi.routing import APIRouter``, or ``fastapi.APIRouter`` /
  ``fastapi.routing.APIRouter`` used through ``import fastapi[.routing] [as x]`` or ``from fastapi
  import routing``; or a module that imports a router module of its own package (own domain for
  a domain module, ``app.workflows`` for a workflow module), iterated to a fixed point, so a split
  file that does ``from app.decks.router import router`` is one too. An ``APIRouter`` import under
  ``if TYPE_CHECKING:`` still makes a module a router module (consistent with "imports").
  In a domain or in ``workflows/`` a router module never imports anything from ``sqlalchemy``
  (strict, typing-only imports included); in a domain it never imports its own ``repository``.
* Literal-string dynamic imports (``importlib.import_module("…")``, positional or ``name=``;
  relative literals with a literal ``package``; ``__import__("…")`` with a single name) are
  checked like static ones.
  Left to review (architecture §4.2): attribute access through an allowed import
  (``app.decks.services.helpers`` after ``import app.decks.services``); non-literal dynamic
  imports, and ``__import__`` with ``fromlist`` or ``level``; ``APIRouter`` reached through a
  re-export or ``getattr``, and ``add_api_route`` from a module that imports no router; a router
  using ``SessionDep`` with its own ``models``.
* The declared graph itself has no cycle, and equals the one documented in architecture §4.2.
"""

from __future__ import annotations

import ast
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
DOCS_ROOT = Path(__file__).resolve().parents[4] / "docs"
DOC_PATH = DOCS_ROOT / "domain-template.md"
ARCHITECTURE_PATH = DOCS_ROOT / "architecture.md"

# Architecture §4.2, one entry per domain: the domains it may call directly.
DEPENDENCIES: Mapping[str, frozenset[str]] = {
    "system": frozenset(),
    "identity": frozenset({"cards"}),
    "cards": frozenset(),
    "playgroups": frozenset({"identity", "cards"}),
    "decks": frozenset({"playgroups", "cards"}),
    "brackets": frozenset({"decks", "cards"}),
    "games": frozenset({"brackets", "decks", "playgroups"}),
    "stats": frozenset({"games", "brackets"}),
    "collection": frozenset({"decks", "cards", "playgroups"}),
    "leagues": frozenset({"stats", "games"}),
}
DOMAINS = frozenset(DEPENDENCIES)
NON_DOMAIN_PACKAGES = frozenset({"core", "workflows", "workers", "scripts"})
TOP_LEVEL_FILES = frozenset({"main.py", "model_registry.py", "__init__.py"})
SCRIPT_LIKE = frozenset({"main", "scripts"})
REGISTRY = "app.model_registry"
REQUIRED_DOMAIN_FILES = (
    "__init__.py",
    "models.py",
    "schemas.py",
    "router.py",
    "services/__init__.py",
    "repository.py",
    "tests/__init__.py",
)


@dataclass(frozen=True)
class Violation:
    """One offending import."""

    path: Path  # relative to the directory holding ``app/``, e.g. ``app/workflows/x.py``
    line: int
    imported: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path.as_posix()}:{self.line}: imports {self.imported}: {self.reason}"


def find_cycle(graph: Mapping[str, frozenset[str]]) -> list[str] | None:
    """Return one dependency cycle as a node path (first == last), or None when acyclic."""
    done: set[str] = set()

    def visit(node: str, stack: list[str]) -> list[str] | None:
        if node in stack:
            return [*stack[stack.index(node) :], node]
        if node in done:
            return None
        for nxt in sorted(graph.get(node, frozenset())):
            found = visit(nxt, [*stack, node])
            if found:
                return found
        done.add(node)
        return None

    for start in sorted(graph):
        found = visit(start, [])
        if found:
            return found
    return None


def transitive_closure(graph: Mapping[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    """Everything reachable from each node by following the arrows (the node itself excluded)."""
    closure: dict[str, frozenset[str]] = {}
    for start in graph:
        seen: set[str] = set()
        todo = list(graph[start])
        while todo:
            node = todo.pop()
            if node not in seen:
                seen.add(node)
                todo.extend(graph.get(node, frozenset()))
        closure[start] = frozenset(seen)
    return closure


def _module_name(app_root: Path, path: Path) -> str:
    parts = [app_root.name, *path.relative_to(app_root).with_suffix("").parts]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_submodule(app_root: Path, module: str, name: str) -> bool:
    """True when ``module.name`` is a module or package on disk (``from module import name``).

    A directory counts even without ``__init__.py`` (a namespace package still imports).
    """
    parts = [*module.split(".")[1:], name]
    base = app_root.joinpath(*parts)
    return base.with_suffix(".py").is_file() or base.is_dir()


def _resolve_relative(package: str, level: int, module: str, where: str) -> str:
    """Absolute dotted name of ``level`` dots + ``module`` seen from ``package``."""
    parts = package.split(".")
    keep = len(parts) - (level - 1)
    if keep <= 0:
        raise ValueError(f"{where}: relative import goes beyond the top-level package")
    return ".".join([*parts[:keep], *([module] if module else [])])


def _literal(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dynamic_import_target(call: ast.Call, where: str) -> str | None:
    """The literal module name of ``__import__("x")`` or ``[pkg.]import_module("x"[, pkg])``."""
    func = call.func
    name = (
        func.id
        if isinstance(func, ast.Name)
        else func.attr
        if isinstance(func, ast.Attribute)
        else ""
    )
    if name not in {"__import__", "import_module"}:
        return None
    arg = (
        call.args[0]
        if call.args
        else next((k.value for k in call.keywords if k.arg == "name"), None)
    )
    literal = _literal(arg)
    if literal is None:
        return None
    if not literal.startswith("."):
        return literal
    if name != "import_module":
        return None
    pkg_arg = (
        call.args[1]
        if len(call.args) > 1
        else next((k.value for k in call.keywords if k.arg == "package"), None)
    )
    package = _literal(pkg_arg)
    if package is None:
        return None
    level = len(literal) - len(literal.lstrip("."))
    return _resolve_relative(package, level, literal[level:], where)


_APIROUTER_PATHS = frozenset({"fastapi.APIRouter", "fastapi.routing.APIRouter"})


def is_router_module(tree: ast.AST) -> bool:
    """True when the module imports ``fastapi.APIRouter`` (see the module docstring for forms)."""
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not node.level:
            if node.module in {"fastapi", "fastapi.routing"}:
                for alias in node.names:
                    if alias.name == "APIRouter":
                        return True
                    bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bound[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    bound[root] = root
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "APIRouter":
            chain: list[str] = []
            cur = node.value
            while isinstance(cur, ast.Attribute):
                chain.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name) and cur.id in bound:
                full = ".".join([bound[cur.id], *reversed(chain), "APIRouter"])
                if full in _APIROUTER_PATHS:
                    return True
    return False


def _parse(path: Path) -> ast.Module:
    """Parse from bytes so a BOM or an encoding cookie is honoured."""
    return ast.parse(path.read_bytes(), filename=str(path))


def imported_modules(app_root: Path, path: Path) -> list[tuple[int, str]]:
    """Every ``(line, dotted module)`` the file imports, relative imports resolved.

    ``from a import b`` yields ``a.b`` when ``b`` is a submodule on disk, else ``a``. Imports under
    ``if TYPE_CHECKING:`` are included (``ast.walk`` visits every node). A relative import that
    climbs above ``app`` raises ``ValueError`` naming the file and line.
    """
    tree = _parse(path)
    package = _module_name(app_root, path)
    if path.name != "__init__.py":
        package = package.rpartition(".")[0]
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        where = f"{path}:{getattr(node, 'lineno', 0)}"
        if isinstance(node, ast.Call):
            literal = _dynamic_import_target(node, where)
            if literal:
                found.append((node.lineno, literal))
        elif isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(package, node.level, node.module or "", where)
            else:
                base = node.module or ""
            for alias in node.names:
                if alias.name != "*" and _is_submodule(app_root, base, alias.name):
                    found.append((node.lineno, f"{base}.{alias.name}"))
                else:
                    found.append((node.lineno, base))
    return sorted(set(found))


def _top(module: str) -> str | None:
    """The ``app`` sub-package a dotted name points into (``app.games.x`` -> ``games``)."""
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "app" else None


def _unknown_packages(app_root: Path, domains: frozenset[str]) -> list[Violation]:
    """Packages and modules directly under ``app/`` that are not on the §4.2 list."""
    known = domains | NON_DOMAIN_PACKAGES
    found: list[Violation] = []
    for child in sorted(app_root.iterdir()):
        if child.name.startswith(".") or child.name == "__pycache__":
            continue
        if child.is_dir() and child.name not in known:
            kind, name = "package", child.name
        elif child.is_file() and child.suffix == ".py" and child.name not in TOP_LEVEL_FILES:
            kind, name = "module", child.stem
        else:
            continue
        found.append(
            Violation(
                child.relative_to(app_root.parent),
                0,
                f"app.{name}",
                f"a {kind} under app/ must be a domain, core, workflows, workers, scripts, "
                "main.py or model_registry.py",
            )
        )
    return found


def router_modules(app_root: Path, domains: frozenset[str]) -> set[str]:
    """Dotted names of the router modules in domains and ``workflows/`` (fixed point).

    Seeds are modules importing ``fastapi.APIRouter``; a module joins when it imports a router
    module of its own package (own domain, or ``app.workflows``), until nothing changes.
    """
    modules: dict[str, tuple[ast.Module, list[str]]] = {}
    for path in sorted(app_root.rglob("*.py")):
        name = _module_name(app_root, path)
        owner = _top(name)
        if owner is not None and (owner in domains or owner == "workflows"):
            modules[name] = (_parse(path), [t for _, t in imported_modules(app_root, path)])
    routers = {name for name, (tree, _) in modules.items() if is_router_module(tree)}
    changed = True
    while changed:
        changed = False
        for name, (_, targets) in modules.items():
            if name in routers:
                continue
            own = f"app.{_top(name)}"
            if any(t in routers and (t == own or t.startswith(own + ".")) for t in targets):
                routers.add(name)
                changed = True
    return routers


def _reasons(
    owner: str | None,
    top: str | None,
    target: str,
    domains: frozenset[str],
    closure: Mapping[str, frozenset[str]],
) -> list[str]:
    """Why ``owner`` may not import ``target`` (``top``: its ``app`` sub-package), if it may not."""
    if top is None or top == "model_registry":  # the registry has its own rule
        return []
    if owner == "core":
        if top != "core":
            return [f"core must not import app.{top} (only core itself)"]
    elif owner in domains:
        if top in {"workflows", "workers"}:
            return [f"a domain must not import {top}"]
        if top in domains and top != owner:
            if top not in closure[str(owner)]:
                return [f"{owner} may not depend on {top} (outside the §4.2 closure)"]
            if target != f"app.{top}.services":
                return [f"only app.{top}.services is public; got a deeper import"]
        elif top not in {owner, "core"}:
            return [f"a domain may import only itself, core and domain services, not app.{top}"]
    elif owner in {"workflows", "workers"}:
        if top in domains:
            if target != f"app.{top}.services":
                return [f"{owner} may import only app.{top}.services"]
        elif owner == "workflows" and top == "workers":
            return ["workflows must not import workers (enqueue jobs by name)"]
        elif top not in {"core", owner, "workflows"}:
            return [f"{owner} may not import app.{top}"]
    return []


def check_imports(
    app_root: Path,
    graph: Mapping[str, frozenset[str]] = DEPENDENCIES,
) -> list[Violation]:
    """Check every module under ``app_root`` against the boundary rules; return all violations."""
    closure = transitive_closure(graph)
    domains = frozenset(graph)
    violations: list[Violation] = _unknown_packages(app_root, domains)
    routers = router_modules(app_root, domains)
    for path in sorted(app_root.rglob("*.py")):
        module = _module_name(app_root, path)
        owner = _top(module)
        shown = path.relative_to(app_root.parent)
        router = module in routers
        for line, target in imported_modules(app_root, path):
            reasons: list[str] = []
            if target == REGISTRY and module != REGISTRY:
                reasons.append(
                    "only migrations/env.py and tests under apps/api/tests may import it"
                )
            if owner not in SCRIPT_LIKE:
                reasons.extend(_reasons(owner, _top(target), target, domains, closure))
            if router:
                if target == "sqlalchemy" or target.startswith("sqlalchemy."):
                    reasons.append("a router must not use a SQLAlchemy query API; call a service")
                own_repo = f"app.{owner}.repository"
                if owner in domains and (target == own_repo or target.startswith(own_repo + ".")):
                    reasons.append("a router must not import its own repository; call a service")
            violations.extend(Violation(shown, line, target, r) for r in reasons)
    return violations


def check_layout(app_root: Path, domains: frozenset[str] = DOMAINS) -> dict[str, list[str]]:
    """Missing required files per existing domain package. Absent domains are skipped."""
    missing: dict[str, list[str]] = {}
    for name in sorted(domains):
        pkg = app_root / name
        if not pkg.is_dir():
            continue
        gone = [f for f in REQUIRED_DOMAIN_FILES if not (pkg / f).is_file()]
        if gone:
            missing[name] = gone
    return missing


# --------------------------------------------------------------------------- real tree


def test_declared_graph_is_acyclic_and_names_only_known_domains() -> None:
    assert find_cycle(DEPENDENCIES) is None
    assert all(deps <= DOMAINS for deps in DEPENDENCIES.values())


def test_real_app_tree_respects_import_boundaries() -> None:
    violations = check_imports(APP_ROOT)
    assert not violations, "\n" + "\n".join(str(v) for v in violations)


def test_real_domain_packages_have_the_required_layout() -> None:
    assert check_layout(APP_ROOT) == {}
    assert (APP_ROOT / "system").is_dir(), "the system domain is the worked example"


def test_non_domain_packages_are_not_domains() -> None:
    assert not NON_DOMAIN_PACKAGES & DOMAINS


def test_domain_template_doc_lists_every_required_file() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    for name in REQUIRED_DOMAIN_FILES:
        assert name.removesuffix("/") in text, f"docs/domain-template.md does not mention {name}"
    assert "app/model_registry.py" in text


# --------------------------------------------------------------------------- synthetic


def _tree(root: Path, files: Mapping[str, str]) -> Path:
    """Write ``files`` (paths relative to ``app/``) under ``root/app`` and return that root."""
    app = root / "app"
    for rel, body in files.items():
        target = app / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    (app / "__init__.py").touch()
    for pkg in {p for rel in files for p in Path(rel).parents if str(p) != "."}:
        (app / pkg / "__init__.py").touch()
    return app


_SUBS = {
    "identity/models.py": "",
    "identity/repository.py": "",
    "identity/services/__init__.py": "",
    "identity/services/users.py": "",
    "cards/services/__init__.py": "",
    "cards/models.py": "",
    "games/services/__init__.py": "",
    "games/models.py": "",
    "decks/models.py": "",
    "decks/repository.py": "",
    "decks/services/__init__.py": "",
    "decks/services/helpers.py": "",
    "workflows/__init__.py": "",
    "workflows/w0.py": "",
}


ROUTER = "from fastapi import APIRouter\n"  # makes a module a router module


def _violations(tmp_path: Path, extra: Mapping[str, str]) -> list[Violation]:
    return check_imports(_tree(tmp_path, {**_SUBS, **extra}))


def test_workflow_importing_domain_models_fails_naming_file_and_import(tmp_path: Path) -> None:
    body = "x = 1\nfrom app.identity.models import User\n"
    found = _violations(tmp_path, {"workflows/reg.py": body})
    assert [(v.path.name, v.line, v.imported) for v in found] == [
        ("reg.py", 2, "app.identity.models")
    ]
    assert "reg.py:2" in str(found[0])
    assert "app.identity.models" in str(found[0])


def test_worker_importing_repository_or_service_submodule_fails(tmp_path: Path) -> None:
    body = (
        "import app.identity.repository\n"
        "import app.identity.services.users as u\n"
        "from app.identity.services import users\n"
        "from app.identity.services import create_user\n"
    )
    found = _violations(tmp_path, {"workers/job.py": body})
    assert sorted((v.line, v.imported) for v in found) == [
        (1, "app.identity.repository"),
        (2, "app.identity.services.users"),
        (3, "app.identity.services.users"),
    ]


def test_workflow_may_import_any_domain_services_interface(tmp_path: Path) -> None:
    body = "from app.games import services\nimport app.cards.services\n"
    assert _violations(tmp_path, {"workflows/w.py": body}) == []


def test_domain_importing_other_domain_models_fails_inside_type_checking(tmp_path: Path) -> None:
    body = (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.cards.models import C\n"
    )
    found = _violations(tmp_path, {"identity/schemas.py": body})
    assert [(v.line, v.imported) for v in found] == [(3, "app.cards.models")]


def test_domain_import_outside_transitive_closure_fails(tmp_path: Path) -> None:
    found = _violations(tmp_path, {"cards/services/x.py": "from app.games.services import f\n"})
    assert [v.imported for v in found] == ["app.games.services"]
    assert "closure" in found[0].reason


def test_domain_import_through_transitive_closure_is_allowed(tmp_path: Path) -> None:
    files = {"games/services/x.py": "from app.identity.services import f\n"}
    assert _violations(tmp_path, files) == []


def test_relative_imports_are_resolved(tmp_path: Path) -> None:
    files = {
        "cards/services/rel.py": "from ...games.services import f\nfrom ..models import Card\n",
        "workflows/rel.py": "from ..identity import models\n",
    }
    found = _violations(tmp_path, files)
    assert sorted((v.path.parent.name, v.imported) for v in found) == [
        ("services", "app.games.services"),
        ("workflows", "app.identity.models"),
    ]


def test_domain_importing_workflows_fails(tmp_path: Path) -> None:
    files = {"workflows/w.py": "", "cards/services/y.py": "import app.workflows.w\n"}
    assert [v.imported for v in _violations(tmp_path, files)] == ["app.workflows.w"]


def test_core_importing_a_domain_workflows_or_workers_fails(tmp_path: Path) -> None:
    body = "import app.cards.services\nfrom app import workflows\nimport app.workers.settings\n"
    found = _violations(tmp_path, {"core/auth.py": body, "workflows/__init__.py": ""})
    assert sorted(v.imported for v in found) == [
        "app.cards.services",
        "app.workers.settings",
        "app.workflows",
    ]


def test_importing_the_model_registry_from_app_code_fails(tmp_path: Path) -> None:
    files = {"model_registry.py": "", "cards/services/z.py": "from app import model_registry\n"}
    assert [v.imported for v in _violations(tmp_path, files)] == ["app.model_registry"]


def test_router_importing_own_repository_fails(tmp_path: Path) -> None:
    body = (
        ROUTER + "from app.identity.repository import q\n"
        "from .repository import r\n"
        "from . import repository\n"
    )
    found = _violations(tmp_path, {"identity/router.py": body})
    assert [v.line for v in found] == [2, 3, 4]


def test_router_importing_sqlalchemy_fails_but_services_are_fine(tmp_path: Path) -> None:
    body = (
        ROUTER + "from sqlalchemy import select\n"
        "from sqlalchemy.orm import selectinload\n"
        "from app.identity import services\n"
    )
    found = _violations(tmp_path, {"identity/router.py": body})
    assert [(v.line, v.imported) for v in found] == [(2, "sqlalchemy"), (3, "sqlalchemy.orm")]


def test_sqlalchemy_in_a_non_router_module_is_fine(tmp_path: Path) -> None:
    files = {"identity/repository.py": "from sqlalchemy import select\n"}
    assert _violations(tmp_path, files) == []


def test_every_violation_is_reported_not_just_the_first(tmp_path: Path) -> None:
    body = "import app.cards.models\nimport app.games.models\n"
    assert len(_violations(tmp_path, {"workflows/many.py": body})) == 2


def test_a_cycle_in_the_declared_graph_is_detected() -> None:
    cyclic = {"a": frozenset({"b"}), "b": frozenset({"c"}), "c": frozenset({"a"})}
    cycle = find_cycle(cyclic)
    assert cycle is not None
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {"a", "b", "c"}


def test_transitive_closure_follows_arrows() -> None:
    closure = transitive_closure(DEPENDENCIES)
    assert {"playgroups", "identity", "cards"} <= closure["games"]
    assert closure["system"] == frozenset()
    assert "games" not in closure["cards"]


def test_new_domain_missing_files_reported_and_absent_domains_skipped(tmp_path: Path) -> None:
    app = _tree(tmp_path, {"cards/models.py": "", "cards/schemas.py": "", "core/x.py": ""})
    assert check_layout(app) == {
        "cards": ["router.py", "services/__init__.py", "repository.py", "tests/__init__.py"]
    }


def test_complete_domain_layout_passes_and_non_domain_packages_are_ignored(tmp_path: Path) -> None:
    files = {f"cards/{f}": "" for f in REQUIRED_DOMAIN_FILES}
    app = _tree(tmp_path, {**files, "workflows/w.py": "", "core/c.py": ""})
    assert check_layout(app) == {}


# --------------------------------------------------------------------------- new rules


def test_domain_importing_workers_fails_in_every_form(tmp_path: Path) -> None:
    files = {
        "workers/__init__.py": "",
        "workers/settings.py": "",
        "cards/services/a.py": (
            "import app.workers\n"
            "import app.workers.settings\n"
            "from app.workers import settings\n"
            "from app import workers\n"
            "from ...workers import settings as s2\n"
        ),
    }
    found = _violations(tmp_path, files)
    assert sorted((v.line, v.imported) for v in found) == [
        (1, "app.workers"),
        (2, "app.workers.settings"),
        (3, "app.workers.settings"),
        (4, "app.workers"),
        (5, "app.workers.settings"),
    ]
    assert all("workers" in v.reason for v in found)


def test_core_importing_workflows_or_workers_fails_individually(tmp_path: Path) -> None:
    files = {
        "workers/__init__.py": "",
        "core/a.py": "import app.workflows.w0\n",
        "core/b.py": "import app.workers\n",
    }
    found = _violations(tmp_path, files)
    assert sorted((v.path.name, v.imported) for v in found) == [
        ("a.py", "app.workflows.w0"),
        ("b.py", "app.workers"),
    ]


def test_workers_importing_workflows_is_allowed(tmp_path: Path) -> None:
    files = {"workers/registry.py": "from app.workflows import w0\nimport app.workflows\n"}
    assert _violations(tmp_path, files) == []


def test_main_and_scripts_are_outside_the_rules(tmp_path: Path) -> None:
    body = "import app.decks.models\nimport app.workers\nimport app.workflows.w0\n"
    files = {"main.py": body, "scripts/seed.py": body}
    assert _violations(tmp_path, files) == []


# --------------------------------------------------------------------------- doc and test agree


def _documented_graph() -> dict[str, frozenset[str]]:
    """Parse the mermaid block of architecture.md section 4.2 into an adjacency mapping."""
    text = ARCHITECTURE_PATH.read_text(encoding="utf-8")
    section = text.split("### 4.2", 1)[1].split("### 4.3", 1)[0]
    block = section.split("```mermaid", 1)[1].split("```", 1)[0]
    graph: dict[str, set[str]] = {}
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("flowchart"):
            continue
        parts = [p.strip() for p in line.split("-->")]
        assert len(parts) <= 2, f"unparsed: {raw!r}"
        assert all(p.isidentifier() for p in parts), f"unparsed: {raw!r}"
        graph.setdefault(parts[0], set())
        if len(parts) == 2:
            graph.setdefault(parts[1], set())
            graph[parts[0]].add(parts[1])
    return {k: frozenset(v) for k, v in graph.items()}


def test_dependencies_dict_equals_the_graph_documented_in_architecture_4_2() -> None:
    documented = _documented_graph()
    assert len(documented) == 10
    assert dict(DEPENDENCIES) == documented


def test_documented_graph_parser_reads_edges_and_leaf_nodes() -> None:
    documented = _documented_graph()
    assert documented["leagues"] == {"stats", "games"}
    assert documented["system"] == frozenset()
    assert documented["cards"] == frozenset()


# --------------------------------------------------------------------------- import forms


def _only(tmp_path: Path, owner_file: str, body: str) -> list[tuple[int, str]]:
    root = tmp_path / f"case{len(list(tmp_path.iterdir()))}"
    root.mkdir()
    return [(v.line, v.imported) for v in _violations(root, {owner_file: body})]


def test_import_dotted_module_with_alias_fails(tmp_path: Path) -> None:
    assert _only(tmp_path, "workflows/a.py", "import app.decks.models as m\n") == [
        (1, "app.decks.models")
    ]


def test_from_package_import_models_submodule_fails(tmp_path: Path) -> None:
    assert _only(tmp_path, "workflows/a.py", "from app.decks import models\n") == [
        (1, "app.decks.models")
    ]


def test_from_package_import_services_is_the_public_interface(tmp_path: Path) -> None:
    assert _only(tmp_path, "workflows/a.py", "from app.decks import services\n") == []
    assert _only(tmp_path, "games/services/a.py", "from app.decks import services\n") == []


def test_from_services_import_a_function_is_allowed(tmp_path: Path) -> None:
    assert _only(tmp_path, "workflows/a.py", "from app.decks.services import get_deck\n") == []


def test_from_services_import_a_submodule_fails(tmp_path: Path) -> None:
    body = "from app.decks.services import helpers\n"
    assert _only(tmp_path, "workflows/a.py", body) == [(1, "app.decks.services.helpers")]
    assert _only(tmp_path, "games/services/a.py", body) == [(1, "app.decks.services.helpers")]


def test_from_app_import_domain_and_import_domain_package_fail(tmp_path: Path) -> None:
    body = "from app import decks\nimport app.decks\n"
    assert _only(tmp_path, "workflows/a.py", body) == [(1, "app.decks"), (2, "app.decks")]
    assert _only(tmp_path, "games/services/a.py", body) == [(1, "app.decks"), (2, "app.decks")]


def test_relative_import_from_nested_module_reaching_another_domain_fails(
    tmp_path: Path,
) -> None:
    body = "from ...decks import models\nfrom ..models import Own\n"
    assert _only(tmp_path, "games/services/a.py", body) == [(1, "app.decks.models")]


def test_relative_import_from_a_top_level_domain_module_fails(tmp_path: Path) -> None:
    assert _only(tmp_path, "games/schemas.py", "from ..decks import models\n") == [
        (1, "app.decks.models")
    ]


def test_literal_dynamic_imports_are_checked_like_static_ones(tmp_path: Path) -> None:
    body = (
        "import importlib\n"
        "from importlib import import_module\n"
        'importlib.import_module("app.decks.models")\n'
        'import_module("app.decks.repository")\n'
        '__import__("app.identity.models")\n'
        'importlib.import_module("app.decks.services")\n'
        'importlib.import_module("os.path")\n'
    )
    assert _only(tmp_path, "workflows/a.py", body) == [
        (3, "app.decks.models"),
        (4, "app.decks.repository"),
        (5, "app.identity.models"),
    ]


def test_non_literal_dynamic_imports_are_out_of_scope(tmp_path: Path) -> None:
    body = 'import importlib\nname = "app.decks.models"\nimportlib.import_module(name)\n'
    assert _only(tmp_path, "workflows/a.py", body) == []


def test_imports_inside_functions_and_try_except_are_checked(tmp_path: Path) -> None:
    body = (
        "def f():\n"
        "    from app.decks.models import D\n"
        "try:\n"
        "    import app.identity.repository\n"
        "except ImportError:\n"
        "    app = None\n"
    )
    assert _only(tmp_path, "workflows/a.py", body) == [
        (2, "app.decks.models"),
        (4, "app.identity.repository"),
    ]


def test_type_checking_import_in_a_worker_fails(tmp_path: Path) -> None:
    body = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import app.decks.models\n"
    assert _only(tmp_path, "workers/a.py", body) == [(3, "app.decks.models")]


def test_importing_core_and_own_domain_internals_is_allowed(tmp_path: Path) -> None:
    body = "from app.core.db import SessionDep\nfrom app.decks.models import D\n"
    assert _only(tmp_path, "decks/services/a.py", body) == []


# --------------------------------------------------------------------------- router rule


def test_router_sqlalchemy_in_any_form_fails(tmp_path: Path) -> None:
    body = (
        ROUTER + "import sqlalchemy as sa\n"
        "from sqlalchemy import select\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n"
        "import importlib\n"
        'importlib.import_module("sqlalchemy.orm")\n'
    )
    assert _only(tmp_path, "decks/router.py", body) == [
        (2, "sqlalchemy"),
        (3, "sqlalchemy"),
        (4, "sqlalchemy.ext.asyncio"),
        (6, "sqlalchemy.orm"),
    ]


def test_router_may_import_core_db_session_dep(tmp_path: Path) -> None:
    files = {"core/db.py": "", "decks/router.py": ROUTER + "from app.core.db import SessionDep\n"}
    assert _violations(tmp_path, files) == []


def test_router_importing_own_repository_in_every_form_fails(tmp_path: Path) -> None:
    body = ROUTER + "from app.decks import repository\nimport app.decks.repository as r\n"
    assert _only(tmp_path, "decks/router.py", body) == [
        (2, "app.decks.repository"),
        (3, "app.decks.repository"),
    ]


def test_router_importing_another_domains_repository_fails_as_non_public(
    tmp_path: Path,
) -> None:
    found = _violations(tmp_path, {"games/router.py": "from app.decks.repository import q\n"})
    assert [v.imported for v in found] == ["app.decks.repository"]


def test_workflow_router_may_not_use_sqlalchemy(tmp_path: Path) -> None:
    body = ROUTER + "from sqlalchemy import select\nfrom app.decks import services\n"
    assert _only(tmp_path, "workflows/router.py", body) == [(2, "sqlalchemy")]


def test_sqlalchemy_in_a_non_router_workflow_is_fine(tmp_path: Path) -> None:
    assert _only(tmp_path, "workflows/registration.py", "from sqlalchemy import select\n") == []


def test_router_module_in_workflows_with_a_non_router_name_may_not_use_sqlalchemy(
    tmp_path: Path,
) -> None:
    body = ROUTER + "from sqlalchemy import select\n"
    assert _only(tmp_path, "workflows/registration.py", body) == [(2, "sqlalchemy")]


def test_router_module_in_a_routers_package_fails_for_repository_and_sqlalchemy(
    tmp_path: Path,
) -> None:
    body = ROUTER + "from app.decks.repository import q\nfrom sqlalchemy import select\n"
    assert _only(tmp_path, "decks/routers/drafts.py", body) == [
        (2, "app.decks.repository"),
        (3, "sqlalchemy"),
    ]


def test_split_router_file_beside_router_py_fails_for_repository_and_sqlalchemy(
    tmp_path: Path,
) -> None:
    body = ROUTER + "from .repository import q\nimport sqlalchemy\n"
    assert _only(tmp_path, "decks/router_drafts.py", body) == [
        (2, "app.decks.repository"),
        (3, "sqlalchemy"),
    ]


def test_deep_module_without_apirouter_is_not_a_router_even_when_named_router(
    tmp_path: Path,
) -> None:
    body = "from sqlalchemy import select\nfrom app.decks import repository\n"
    assert _only(tmp_path, "decks/services/router.py", body) == []
    assert _only(tmp_path, "decks/router.py", body) == []


def test_deep_module_that_imports_apirouter_is_a_router(tmp_path: Path) -> None:
    body = ROUTER + "from sqlalchemy import select\n"
    assert _only(tmp_path, "decks/services/anything.py", body) == [(2, "sqlalchemy")]


def test_router_detection_covers_aliases_and_attribute_forms(tmp_path: Path) -> None:
    forms = [
        "from fastapi import APIRouter as R\n",
        "from fastapi.routing import APIRouter\n",
        "import fastapi\nr = fastapi.APIRouter()\n",
        "import fastapi as fa\nr = fa.APIRouter()\n",
        "import fastapi.routing\nr = fastapi.routing.APIRouter()\n",
        "import fastapi.routing as fr\nr = fr.APIRouter()\n",
        "from fastapi import routing\nr = routing.APIRouter()\n",
    ]
    for body in forms:
        found = _only(tmp_path, "decks/api.py", body + "import sqlalchemy\n")
        assert [imp for _, imp in found] == ["sqlalchemy"], body


def test_apirouter_import_under_type_checking_still_makes_a_router_module(
    tmp_path: Path,
) -> None:
    body = (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from fastapi import APIRouter\n"
        "import sqlalchemy\n"
    )
    assert _only(tmp_path, "decks/api.py", body) == [(4, "sqlalchemy")]


def test_split_router_importing_the_router_py_router_is_a_router_module(
    tmp_path: Path,
) -> None:
    body = (
        "from app.decks.router import router\n"
        "from sqlalchemy import select\n"
        "from app.decks.repository import q\n"
        '@router.get("/x")\n'
        "def f(): ...\n"
    )
    files = {"decks/router.py": ROUTER, "decks/router_drafts.py": body}
    assert [(v.line, v.imported) for v in _violations(tmp_path, files)] == [
        (2, "sqlalchemy"),
        (3, "app.decks.repository"),
    ]


def test_chain_of_two_split_router_files_are_both_router_modules(tmp_path: Path) -> None:
    tail = "from sqlalchemy import select\n"
    files = {
        "decks/router.py": ROUTER,
        "decks/router_a.py": "from app.decks.router import router\n" + tail,
        "decks/router_b.py": "from app.decks import router_a\n" + tail,
    }
    found = _violations(tmp_path, files)
    assert sorted((v.path.name, v.line, v.imported) for v in found) == [
        ("router_a.py", 2, "sqlalchemy"),
        ("router_b.py", 2, "sqlalchemy"),
    ]


def test_chain_is_followed_regardless_of_file_order(tmp_path: Path) -> None:
    tail = "import sqlalchemy\n"
    files = {
        "decks/a.py": "from .b import r\n" + tail,
        "decks/b.py": "from .c import r\n" + tail,
        "decks/c.py": ROUTER + tail,
    }
    assert sorted(v.path.name for v in _violations(tmp_path, files)) == ["a.py", "b.py", "c.py"]


def test_workflow_module_importing_the_workflows_router_is_a_router_module(
    tmp_path: Path,
) -> None:
    files = {
        "workflows/router.py": ROUTER,
        "workflows/registration_routes.py": (
            "from app.workflows.router import router\nfrom sqlalchemy import select\n"
        ),
    }
    found = _violations(tmp_path, files)
    assert [(v.path.name, v.imported) for v in found] == [("registration_routes.py", "sqlalchemy")]


def test_service_module_importing_nothing_router_like_stays_a_non_router(
    tmp_path: Path,
) -> None:
    files = {
        "decks/router.py": ROUTER,
        "decks/services/q.py": "from sqlalchemy import select\nfrom app.decks import models\n",
    }
    assert _violations(tmp_path, files) == []


def test_importing_another_packages_router_does_not_make_a_router_module(
    tmp_path: Path,
) -> None:
    files = {
        "identity/router.py": ROUTER,
        "decks/x.py": "import app.identity.router\nimport sqlalchemy\n",
    }
    found = _violations(tmp_path, files)
    assert [v.imported for v in found] == ["app.identity.router"]


def test_router_detection_ignores_unrelated_apirouter_names(tmp_path: Path) -> None:
    for body in (
        "from other import APIRouter\n",
        "import other\nr = other.APIRouter()\n",
        "import fastapi\n",
        "from fastapi import FastAPI\n",
    ):
        assert _only(tmp_path, "decks/api.py", body + "import sqlalchemy\n") == [], body


def test_router_in_core_or_workers_may_use_sqlalchemy(tmp_path: Path) -> None:
    body = ROUTER + "import sqlalchemy\n"
    assert _only(tmp_path, "core/api.py", body) == []
    assert _only(tmp_path, "workers/api.py", body) == []


def test_non_router_workflow_module_may_use_sqlalchemy(tmp_path: Path) -> None:
    assert _only(tmp_path, "workflows/registration.py", "import sqlalchemy\n") == []


# --------------------------------------------------------------------------- allowlists


def test_unknown_package_under_app_is_flagged(tmp_path: Path) -> None:
    found = _violations(tmp_path, {"players/x.py": ""})
    assert [(v.path.as_posix(), v.imported) for v in found] == [("app/players", "app.players")]
    assert "domain" in found[0].reason


def test_stray_top_level_module_is_flagged(tmp_path: Path) -> None:
    found = _violations(tmp_path, {"helpers.py": ""})
    assert [(v.path.as_posix(), v.imported) for v in found] == [("app/helpers.py", "app.helpers")]
    assert "module" in found[0].reason


def test_hidden_top_level_entries_are_ignored(tmp_path: Path) -> None:
    app = _tree(tmp_path, dict(_SUBS))
    (app / ".hidden").mkdir()
    (app / ".hidden.py").write_text("", encoding="utf-8")
    assert check_imports(app) == []


def test_domain_importing_a_stray_module_or_package_fails(tmp_path: Path) -> None:
    files = {
        "helpers.py": "",
        "x/__init__.py": "",
        "decks/services/a.py": "import app.helpers\nfrom app import x\nimport app.x\n",
    }
    found = [v for v in _violations(tmp_path, files) if v.path.name == "a.py"]
    assert [(v.line, v.imported) for v in found] == [
        (1, "app.helpers"),
        (2, "app.x"),
        (3, "app.x"),
    ]


def test_known_packages_and_top_level_files_are_not_unknown(tmp_path: Path) -> None:
    files = {
        "main.py": "",
        "model_registry.py": "",
        "scripts/s.py": "",
        "workers/w.py": "",
        "core/c.py": "",
        "system/models.py": "",
    }
    assert _violations(tmp_path, files) == []


def test_pycache_directory_is_not_an_unknown_package(tmp_path: Path) -> None:
    app = _tree(tmp_path, dict(_SUBS))
    (app / "__pycache__").mkdir()
    assert check_imports(app) == []


def test_core_importing_main_or_scripts_or_unknown_fails(tmp_path: Path) -> None:
    files = {
        "main.py": "",
        "scripts/s.py": "",
        "core/a.py": "import app.main\nimport app.scripts.s\nimport app.nope\n",
    }
    found = [v for v in _violations(tmp_path, files) if v.path.name == "a.py"]
    assert [v.imported for v in found] == ["app.main", "app.scripts.s", "app.nope"]


def test_core_importing_itself_is_allowed(tmp_path: Path) -> None:
    files = {"core/a.py": "from app.core import b\nimport app.core.b\n", "core/b.py": ""}
    assert _violations(tmp_path, files) == []


def test_domain_importing_main_scripts_or_unknown_app_package_fails(tmp_path: Path) -> None:
    body = "import app.main\nimport app.scripts.s\nfrom app.nope import x\n"
    found = [
        v
        for v in _violations(tmp_path, {"decks/services/a.py": body, "scripts/s.py": ""})
        if v.path.name == "a.py"
    ]
    assert [v.imported for v in found] == ["app.main", "app.scripts.s", "app.nope"]


def test_workflows_and_workers_importing_main_scripts_or_unknown_fail(tmp_path: Path) -> None:
    body = "import app.main\nimport app.scripts.s\nimport app.nope\n"
    for owner_file in ("workflows/a.py", "workers/a.py"):
        found = _only(tmp_path, owner_file, body)
        assert [imp for _, imp in found] == ["app.main", "app.scripts.s", "app.nope"]


def test_workflows_and_workers_may_import_core_and_domain_services(tmp_path: Path) -> None:
    body = "import app.core.db\nfrom app.decks import services\n"
    assert _only(tmp_path, "workflows/a.py", body) == []
    assert _only(tmp_path, "workers/a.py", body) == []


def test_workflows_importing_workers_fails_in_every_form(tmp_path: Path) -> None:
    files = {
        "workers/__init__.py": "",
        "workers/settings.py": "",
        "workflows/a.py": "import app.workers\nfrom app.workers import settings\n",
    }
    found = _violations(tmp_path, files)
    assert sorted((v.line, v.imported) for v in found) == [
        (1, "app.workers"),
        (2, "app.workers.settings"),
    ]


def test_workers_importing_workflows_is_allowed_while_workflows_importing_workers_is_not(
    tmp_path: Path,
) -> None:
    assert _only(tmp_path, "workers/a.py", "import app.workflows.w0\n") == []
    assert _only(tmp_path, "workflows/a.py", "import app.workers\n") == [(1, "app.workers")]


def test_main_importing_the_model_registry_fails_but_nothing_else_in_main_does(
    tmp_path: Path,
) -> None:
    body = "import app.model_registry\nimport app.decks.models\nimport app.workers\n"
    files = {"model_registry.py": "", "main.py": body}
    assert [(v.line, v.imported) for v in _violations(tmp_path, files)] == [
        (1, "app.model_registry")
    ]
    other = tmp_path / "other"
    other.mkdir()
    files = {"model_registry.py": "", "main.py": "from app import model_registry\n"}
    assert [(v.line, v.imported) for v in _violations(other, files)] == [(1, "app.model_registry")]


def test_scripts_importing_the_model_registry_fails(tmp_path: Path) -> None:
    assert _only(tmp_path, "scripts/s.py", "import app.model_registry\n") == [
        (1, "app.model_registry")
    ]


def test_domain_tests_obey_their_domains_rules_including_the_registry_rule(
    tmp_path: Path,
) -> None:
    body = "import app.model_registry\nimport app.cards.models\nimport app.games.services\n"
    found = _only(tmp_path, "decks/tests/test_x.py", body)
    assert found == [(1, "app.model_registry"), (2, "app.cards.models"), (3, "app.games.services")]


def test_domain_tests_may_import_own_internals_and_allowed_services(tmp_path: Path) -> None:
    body = "from app.decks.models import D\nfrom app.cards import services\n"
    assert _only(tmp_path, "decks/tests/test_x.py", body) == []


# --------------------------------------------------------------------------- namespace packages


def _drop(app: Path, *rels: str) -> None:
    for rel in rels:
        (app / rel).unlink()


def test_import_from_a_directory_without_init_is_treated_as_a_submodule(tmp_path: Path) -> None:
    files = {
        **_SUBS,
        "decks/services/helpers2/x.py": "",
        "workflows/a.py": "from app.decks.services import helpers2\n",
    }
    app = _tree(tmp_path, files)
    _drop(app, "decks/services/helpers2/__init__.py")
    assert [(v.line, v.imported) for v in check_imports(app)] == [
        (1, "app.decks.services.helpers2")
    ]


def test_from_app_import_package_without_init_resolves_to_the_package(tmp_path: Path) -> None:
    files = {**_SUBS, "workflows/a.py": "from app import decks\nfrom app import workflows\n"}
    app = _tree(tmp_path, files)
    _drop(app, "decks/__init__.py", "workflows/__init__.py")
    found = [v for v in check_imports(app) if v.path.name == "a.py"]
    assert [v.imported for v in found] == ["app.decks"]
    assert imported_modules(app, app / "workflows" / "a.py") == [
        (1, "app.decks"),
        (2, "app.workflows"),
    ]


def test_domain_missing_init_files_fail_the_layout_check(tmp_path: Path) -> None:
    for i, missing in enumerate(("__init__.py", "tests/__init__.py")):
        root = tmp_path / f"case{i}"
        root.mkdir()
        app = _scaffold(root, "decks")
        (app / "decks" / missing).unlink()
        assert check_layout(app) == {"decks": [missing]}


# --------------------------------------------------------------------------- dynamic imports 2


def test_dynamic_import_keyword_and_literal_relative_forms_are_checked(tmp_path: Path) -> None:
    body = (
        "import importlib\n"
        "from importlib import import_module\n"
        'importlib.import_module(name="app.decks.models")\n'
        'import_module(".models", "app.decks")\n'
        'import_module(".models", package="app.decks")\n'
        'import_module(name=".repository", package="app.decks")\n'
        'import_module("..decks.models", "app.workflows")\n'
        'import_module(".models", pkg)\n'
        'import_module(".models")\n'
        'import_module(".services", "app.decks")\n'
        '__import__(name="app.identity.models")\n'
    )
    assert _only(tmp_path, "workflows/a.py", body) == [
        (3, "app.decks.models"),
        (4, "app.decks.models"),
        (5, "app.decks.models"),
        (6, "app.decks.repository"),
        (7, "app.decks.models"),
        (11, "app.identity.models"),
    ]


def test_dynamic_relative_import_beyond_the_top_package_raises(tmp_path: Path) -> None:
    body = 'import importlib\nimportlib.import_module("..x", "app")\n'
    with pytest.raises(ValueError, match="beyond the top-level package"):
        _only(tmp_path, "workflows/a.py", body)


# --------------------------------------------------------------------------- parsing


def test_module_with_a_bom_and_an_encoding_cookie_is_parsed(tmp_path: Path) -> None:
    app = _tree(tmp_path, dict(_SUBS))
    (app / "workflows" / "bom.py").write_bytes(b"\xef\xbb\xbfimport app.decks.models\n")
    (app / "workflows" / "latin.py").write_bytes(
        b"# -*- coding: latin-1 -*-\n# caf\xe9\nimport app.identity.models\n"
    )
    found = sorted((v.path.name, v.imported) for v in check_imports(app))
    assert found == [("bom.py", "app.decks.models"), ("latin.py", "app.identity.models")]


def test_relative_import_beyond_the_top_package_raises_a_clear_error(tmp_path: Path) -> None:
    app = _tree(tmp_path, {**_SUBS, "workflows/a.py": "from ... import x\n"})
    with pytest.raises(ValueError, match=r"a\.py:1: relative import goes beyond"):
        check_imports(app)


def test_relative_import_reaching_exactly_the_top_package_is_fine(tmp_path: Path) -> None:
    files = {
        **_SUBS,
        "model_registry.py": "",
        "workflows/a.py": "from .. import model_registry\nfrom .. import core\n",
    }
    found = check_imports(_tree(tmp_path, files))
    assert [v.imported for v in found] == ["app.model_registry"]


# --------------------------------------------------------------------------- reporting


def test_several_violations_across_files_are_all_reported_with_relative_paths(
    tmp_path: Path,
) -> None:
    files = {
        "workflows/one.py": "x = 1\nimport app.decks.models\n",
        "workers/two.py": "from app.identity.repository import q\n",
        "cards/services/three.py": "\n\nimport app.games.services\n",
        "core/four.py": "import app.cards.services\n",
        "decks/router.py": ROUTER + "from sqlalchemy import select\n",
        "cards/services/five.py": "import app.workers\n",
    }
    found = _violations(tmp_path, files)
    assert sorted(f"{v.path.as_posix()}:{v.line}|{v.imported}" for v in found) == [
        "app/cards/services/five.py:1|app.workers",
        "app/cards/services/three.py:3|app.games.services",
        "app/core/four.py:1|app.cards.services",
        "app/decks/router.py:2|sqlalchemy",
        "app/workers/two.py:1|app.identity.repository",
        "app/workflows/one.py:2|app.decks.models",
    ]
    for v in found:
        assert v.reason
        assert str(v) == f"{v.path.as_posix()}:{v.line}: imports {v.imported}: {v.reason}"
        assert not v.path.is_absolute()


# --------------------------------------------------------------------------- AC1 via the template


def _template_layout_paths(domain: str) -> list[str]:
    """Paths (files and directories) of the first fenced layout block in domain-template.md."""
    text = DOC_PATH.read_text(encoding="utf-8")
    block = text.split("## Files", 1)[1].split("```", 2)[1]
    stack: list[str] = []
    paths: list[str] = []
    for raw in block.splitlines()[1:]:
        if not raw.strip():
            continue
        depth = (len(raw) - len(raw.lstrip())) // 2
        name = raw.strip().split("  ")[0].replace("<topic>", "example").replace("<name>", domain)
        del stack[depth:]
        stack.append(name.rstrip("/"))
        if depth > 0:
            paths.append("/".join(stack[1:]) + ("/" if name.endswith("/") else ""))
    return paths


def _scaffold(root: Path, domain: str) -> Path:
    """Create the domain exactly as docs/domain-template.md's layout block lists it."""
    app = _tree(root, {"core/c.py": ""})
    for rel in _template_layout_paths(domain):
        target = app / domain / rel.rstrip("/")
        if rel.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('"""Template stub."""\n', encoding="utf-8")
    return app


def test_template_layout_block_lists_every_required_file() -> None:
    paths = _template_layout_paths("decks")
    for required in REQUIRED_DOMAIN_FILES:
        assert required in paths, f"{required} missing from the docs/domain-template.md layout"


def test_domain_created_from_the_template_passes_layout_and_import_checks(
    tmp_path: Path,
) -> None:
    app = _scaffold(tmp_path, "decks")
    assert check_layout(app) == {}
    assert check_imports(app) == []


def test_removing_any_one_required_file_fails_the_layout_check_naming_it(
    tmp_path: Path,
) -> None:
    required_all = REQUIRED_DOMAIN_FILES
    for i, required in enumerate(required_all):
        root = tmp_path / f"case{i}"
        root.mkdir()
        app = _scaffold(root, "decks")
        target = app / "decks" / required.rstrip("/")
        if required.endswith("/"):
            shutil.rmtree(target)
        else:
            target.unlink()
        assert check_layout(app) == {"decks": [required]}, required


def test_another_domain_importing_identity_models_fails_but_identity_services_is_allowed(
    tmp_path: Path,
) -> None:
    """S-2.2.1 AC4: only ``app.identity.services`` is reachable from another domain."""
    bad = "from app.identity.models import Player" + chr(10)
    found = _violations(tmp_path, {"playgroups/bad.py": bad})
    assert [(v.path.name, v.imported) for v in found] == [("bad.py", "app.identity.models")]
    for ok in (
        "from app.identity.services import get_player",
        "from app.identity.services import get_players, get_player_for_user",
    ):
        assert _violations(tmp_path / "ok", {"playgroups/ok.py": ok}) == []
    repo = _violations(tmp_path / "repo", {"playgroups/r.py": "import app.identity.repository"})
    assert [v.imported for v in repo] == ["app.identity.repository"]
