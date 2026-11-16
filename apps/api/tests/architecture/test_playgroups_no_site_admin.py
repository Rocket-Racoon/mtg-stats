"""``playgroups`` never uses the site-admin role for access (S-2.1.11 AC6, decision 1 on #70).

The role grants no playgroup access, and no playgroup role grants it. The real 404 test is deferred
to S-2.4.1 (#85), when the ``/playgroups/{id}/...`` routes exist. Until then these static
rules hold:

1. Route check over the real app: no route under ``/api/v1/playgroups`` has ``require_site_admin``
   anywhere in its dependency tree (vacuous today; a synthetic app proves it can fail).
2. Static scan: no module under ``app/playgroups/`` other than ``router.py`` (services, repository,
   models, schemas) references ``is_site_admin``, ``require_site_admin`` or ``SiteAdminDep``, so
   access decisions cannot consult the role.
3. ``router.py`` may use ``SiteAdminDep`` only on the invites routes (``POST /invites`` is a phase-0
   site-admin action by S-2.1.2 AC5, not playgroup access) and never ``is_site_admin``.

The scan parses with ``ast``, so comments and docstrings never trip it. It flags names in code:
identifiers, attributes, imports (with or without ``as``), keyword arguments, function and argument
names, and a string constant that is exactly a forbidden name. Test modules are skipped: a future
404 test has to build a site admin with ``is_site_admin=True``.
"""

import ast
from pathlib import Path
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts

from app.core.auth import SiteAdminDep, require_site_admin
from app.main import create_app

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
PLAYGROUPS = APP_ROOT / "playgroups"
FORBIDDEN = frozenset({"is_site_admin", "require_site_admin", "SiteAdminDep"})
ROUTER_FORBIDDEN = frozenset({"is_site_admin"})
PLAYGROUP_PREFIX = "/api/v1/playgroups"
INVITES_PREFIX = "/api/v1/invites"


def _names_in(node: ast.AST, forbidden: frozenset[str]) -> list[tuple[int, str]]:
    """Every use of a forbidden name as code, as ``(line, name)``."""
    found: list[tuple[int, str]] = []
    for child in ast.walk(node):
        candidates: list[str] = []
        if isinstance(child, ast.Name):
            candidates.append(child.id)
        elif isinstance(child, ast.Attribute):
            candidates.append(child.attr)
        elif isinstance(child, ast.alias):
            candidates.append(child.name.rsplit(".", 1)[-1])
            if child.asname:
                candidates.append(child.asname)
        elif (isinstance(child, ast.keyword) and child.arg) or isinstance(child, ast.arg):
            candidates.append(child.arg)
        elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            candidates.append(child.name)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            candidates.append(child.value)
        found.extend(
            (getattr(child, "lineno", 0), name) for name in candidates if name in forbidden
        )
    return found


def _strip_docstrings(tree: ast.Module) -> None:
    """Remove docstrings so prose that mentions a name is never mistaken for a reference."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0] = ast.Pass()


def violations(root: Path) -> list[str]:
    """``path:line name`` for every reference under ``root``.

    Tests folders are skipped. ``router.py`` at the top of ``root`` may use ``SiteAdminDep`` and
    ``require_site_admin`` (checked by route below) but never ``is_site_admin``.
    """
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if "tests" in relative.parts[:-1] or relative.name.startswith("test_"):
            continue
        forbidden = ROUTER_FORBIDDEN if relative.as_posix() == "router.py" else FORBIDDEN
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _strip_docstrings(tree)
        hits = _names_in(tree, forbidden)
        problems.extend(f"{relative.as_posix()}:{line} {name}" for line, name in hits)
    return problems


def _write(root: Path, relative: str, source: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


# ------------------------------------------------------------------ the real tree
def test_the_playgroups_package_exists_and_has_modules_to_scan() -> None:
    modules = [p for p in PLAYGROUPS.rglob("*.py") if "tests" not in p.parts]
    assert len(modules) >= 5


def test_no_logic_module_under_app_playgroups_references_the_site_admin_role() -> None:
    assert violations(PLAYGROUPS) == []


# ------------------------------------------------------------------ the route checks
def _depends_on(dependant: Dependant, target: object) -> bool:
    return any(dep.call is target or _depends_on(dep, target) for dep in dependant.dependencies)


def _api_routes(app: FastAPI) -> list[Any]:
    """Every API route with its effective path and dependency tree (included routers expanded)."""
    return [
        context
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
    ]


def routes_with_site_admin(app: FastAPI, prefix: str = "") -> list[str]:
    """``METHOD path`` of every route under ``prefix`` that needs ``require_site_admin``."""
    return [
        f"{sorted(route.methods)[0]} {route.path}"
        for route in _api_routes(app)
        if route.path.startswith(prefix) and _depends_on(route.dependant, require_site_admin)
    ]


def test_no_playgroup_route_in_the_real_app_depends_on_require_site_admin() -> None:
    assert routes_with_site_admin(create_app(), PLAYGROUP_PREFIX) == []


def test_in_the_playgroups_router_site_admin_is_used_only_on_invites_routes() -> None:
    app = create_app()
    own = [r for r in _api_routes(app) if r.endpoint.__module__ == "app.playgroups.router"]
    assert own, "the playgroups router has routes"
    guarded = [r for r in own if _depends_on(r.dependant, require_site_admin)]
    assert guarded, "POST /invites is the site-admin route this check expects to find"
    assert all(r.path.startswith(INVITES_PREFIX) for r in guarded), [r.path for r in guarded]


def _synthetic(path: str) -> FastAPI:
    app = FastAPI()

    @app.get(path)
    async def route(admin: SiteAdminDep) -> dict[str, str]:
        return {}

    return app


def test_the_route_check_fails_for_a_playgroup_route_guarded_by_the_site_admin_dependency() -> None:
    app = _synthetic("/api/v1/playgroups/abc/x")
    found = routes_with_site_admin(app, PLAYGROUP_PREFIX)
    assert found == ["GET /api/v1/playgroups/abc/x"]


def test_the_route_check_sees_the_dependency_through_a_nested_dependency() -> None:
    app = FastAPI()

    async def outer(admin: SiteAdminDep) -> None:
        return None

    @app.get("/api/v1/playgroups/abc/y")
    async def route(_: Annotated[None, Depends(outer)]) -> dict[str, str]:
        return {}

    found = routes_with_site_admin(app, PLAYGROUP_PREFIX)
    assert found == ["GET /api/v1/playgroups/abc/y"]


def test_the_route_check_ignores_site_admin_routes_outside_playgroups() -> None:
    assert routes_with_site_admin(_synthetic("/api/v1/invites"), PLAYGROUP_PREFIX) == []


# ------------------------------------------------------------------ the scanner itself
@pytest.mark.parametrize(
    "source",
    [
        "from app.core.auth import SiteAdminDep\n",
        "from app.core.auth import SiteAdminDep as Admin\n",
        "from app.core.auth import require_site_admin\n",
        "from app.identity.services import is_site_admin\n",
        "import app.core.auth as auth\nx = auth.require_site_admin\n",
        "def f(user):\n    return user.is_site_admin\n",
        "def f(session):\n    return session.where(User.is_site_admin.is_(True))\n",
        "x = dict(is_site_admin=True)\n",
        "def is_site_admin(): ...\n",
        "def f(is_site_admin): ...\n",
        "x = getattr(user, 'is_site_admin')\n",
        "async def route(admin: SiteAdminDep): ...\n",
        "from fastapi import Depends\nx = Depends(require_site_admin)\n",
    ],
)
def test_the_scanner_flags_a_reference_in_any_code_form(tmp_path: Path, source: str) -> None:
    _write(tmp_path, "mod.py", source)
    assert violations(tmp_path) != []


def test_the_scanner_reports_the_file_line_and_name(tmp_path: Path) -> None:
    _write(tmp_path, "services/sub.py", "x = 1\n\ny = user.is_site_admin\n")
    assert violations(tmp_path) == ["services/sub.py:3 is_site_admin"]


@pytest.mark.parametrize(
    "source",
    [
        "# is_site_admin and require_site_admin are never used here\nx = 1\n",
        '"""Module doc: SiteAdminDep is not used."""\nx = 1\n',
        'def f():\n    """No is_site_admin here."""\n    return 1\n',
        'class A:\n    """About require_site_admin."""\n',
        "x = 'the is_site_admin flag is ignored'\n",
        "x = 'SiteAdminDep, require_site_admin'\n",
        "is_admin_of_playgroup = True\n",
        "site_admin = 1\nsiteadmin = 2\n",
    ],
)
def test_the_scanner_ignores_comments_docstrings_and_longer_strings(
    tmp_path: Path, source: str
) -> None:
    _write(tmp_path, "services.py", source)
    assert violations(tmp_path) == []


def test_the_scanner_skips_the_domains_test_modules(tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_x.py", "x = dict(is_site_admin=True)\n")
    _write(tmp_path, "test_y.py", "x = dict(is_site_admin=True)\n")
    assert violations(tmp_path) == []


def test_the_scanner_finds_a_reference_in_a_nested_package(tmp_path: Path) -> None:
    _write(tmp_path, "services/deep/mod.py", "from app.core.auth import SiteAdminDep\n")
    assert len(violations(tmp_path)) == 1


def test_the_scanner_allows_site_admin_dep_in_router_py_but_never_the_flag(tmp_path: Path) -> None:
    _write(tmp_path, "router.py", "from app.core.auth import SiteAdminDep\n")
    assert violations(tmp_path) == []
    _write(tmp_path, "router.py", "x = user.is_site_admin\n")
    assert violations(tmp_path) == ["router.py:1 is_site_admin"]


@pytest.mark.parametrize("module", ["services/x.py", "repository.py", "models.py", "schemas.py"])
def test_the_scanner_flags_the_dependency_in_any_logic_module(tmp_path: Path, module: str) -> None:
    _write(tmp_path, module, "from app.core.auth import SiteAdminDep\n")
    assert len(violations(tmp_path)) == 1
