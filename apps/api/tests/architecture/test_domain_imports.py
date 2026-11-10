"""Import-boundary and domain-layout checks (S-1.4.6, architecture §4.2).

The checker parses every module under ``app/`` with the standard library ``ast`` module (no
import is executed) and reports *every* violation with file, line and the import. Each rule has a
test on a synthetic module tree proving it fails, plus one run over the real ``app/`` tree.

Rules, with ``A`` the domain a module belongs to:

* A domain may import another domain ``B`` only as ``app.B.services`` (the public interface), and
  only when ``B`` is in the transitive closure of the §4.2 graph from ``A``.
* ``workflows/`` and ``workers/`` may import a domain only as ``app.<B>.services``.
* A domain never imports ``app.workflows``.
* ``app.core`` never imports a domain, ``app.workflows`` or ``app.workers``.
* Nothing under ``app/`` imports ``app.model_registry`` (only ``migrations/env.py`` and tests do).
* A domain's ``router.py`` never imports its own ``repository`` nor anything from ``sqlalchemy``
  (strict: every ``sqlalchemy`` / ``sqlalchemy.*`` import, including typing-only ones). Routers
  call services; a query API in a router means business logic leaked out of ``services/``.
* The declared graph itself has no cycle.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
DOC_PATH = Path(__file__).resolve().parents[4] / "docs" / "domain-template.md"

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
REQUIRED_DOMAIN_FILES = (
    "models.py",
    "schemas.py",
    "router.py",
    "services/__init__.py",
    "repository.py",
)
REQUIRED_DOMAIN_DIRS = ("tests",)


@dataclass(frozen=True)
class Violation:
    """One offending import."""

    path: Path
    line: int
    imported: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: imports {self.imported}: {self.reason}"


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
    """True when ``module.name`` is a module or package on disk (``from module import name``)."""
    parts = [*module.split(".")[1:], name]
    base = app_root.joinpath(*parts)
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def imported_modules(app_root: Path, path: Path) -> list[tuple[int, str]]:
    """Every ``(line, dotted module)`` the file imports, relative imports resolved.

    ``from a import b`` yields ``a.b`` when ``b`` is a submodule on disk, else ``a``. Imports under
    ``if TYPE_CHECKING:`` are included (``ast.walk`` visits every node).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_name(app_root, path)
    if path.name != "__init__.py":
        package = package.rpartition(".")[0]
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, *([node.module] if node.module else [])])
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


def check_imports(
    app_root: Path,
    graph: Mapping[str, frozenset[str]] = DEPENDENCIES,
) -> list[Violation]:
    """Check every module under ``app_root`` against the boundary rules; return all violations."""
    closure = transitive_closure(graph)
    domains = frozenset(graph)
    violations: list[Violation] = []
    for path in sorted(app_root.rglob("*.py")):
        module = _module_name(app_root, path)
        owner = _top(module)
        is_router = owner in domains and path == app_root / str(owner) / "router.py"
        for line, target in imported_modules(app_root, path):
            reasons: list[str] = []
            top = _top(target)
            if target == "app.model_registry" and module != "app.model_registry":
                reasons.append("only migrations/env.py and tests may import app.model_registry")
            if is_router:
                if target == "sqlalchemy" or target.startswith("sqlalchemy."):
                    reasons.append("a router must not use a SQLAlchemy query API; call a service")
                own_repo = f"app.{owner}.repository"
                if target == own_repo or target.startswith(own_repo + "."):
                    reasons.append("a router must not import its own repository; call a service")
            if owner == "core":
                if top in domains or top in {"workflows", "workers"}:
                    reasons.append("core must not import a domain, workflows or workers")
            elif owner in domains:
                if top == "workflows":
                    reasons.append("a domain must not import workflows")
                elif top in domains and top != owner:
                    if top not in closure[str(owner)]:
                        reasons.append(
                            f"{owner} may not depend on {top} (outside the §4.2 closure)"
                        )
                    elif target != f"app.{top}.services":
                        reasons.append(f"only app.{top}.services is public; got a deeper import")
            elif (
                owner in {"workflows", "workers"}
                and top in domains
                and target != f"app.{top}.services"
            ):
                reasons.append(f"{owner} may import only app.{top}.services")
            violations.extend(Violation(path, line, target, r) for r in reasons)
    return violations


def check_layout(app_root: Path, domains: frozenset[str] = DOMAINS) -> dict[str, list[str]]:
    """Missing required files per existing domain package. Absent domains are skipped."""
    missing: dict[str, list[str]] = {}
    for name in sorted(domains):
        pkg = app_root / name
        if not pkg.is_dir():
            continue
        gone = [f for f in REQUIRED_DOMAIN_FILES if not (pkg / f).is_file()]
        gone += [f"{d}/" for d in REQUIRED_DOMAIN_DIRS if not (pkg / d).is_dir()]
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
    for name in (*REQUIRED_DOMAIN_FILES, *REQUIRED_DOMAIN_DIRS):
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
}


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
        "from app.identity.repository import q\n"
        "from .repository import r\n"
        "from . import repository\n"
    )
    found = _violations(tmp_path, {"identity/router.py": body})
    assert [v.line for v in found] == [1, 2, 3]


def test_router_importing_sqlalchemy_fails_but_services_are_fine(tmp_path: Path) -> None:
    body = (
        "from sqlalchemy import select\n"
        "from sqlalchemy.orm import selectinload\n"
        "from app.identity import services\n"
    )
    found = _violations(tmp_path, {"identity/router.py": body})
    assert [(v.line, v.imported) for v in found] == [(1, "sqlalchemy"), (2, "sqlalchemy.orm")]


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
        "cards": ["router.py", "services/__init__.py", "repository.py", "tests/"]
    }


def test_complete_domain_layout_passes_and_non_domain_packages_are_ignored(tmp_path: Path) -> None:
    files = {f"cards/{f}": "" for f in REQUIRED_DOMAIN_FILES}
    app = _tree(tmp_path, {**files, "workflows/w.py": "", "core/c.py": ""})
    (app / "cards" / "tests").mkdir()
    assert check_layout(app) == {}
